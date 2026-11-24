"""Tool-scoped sub-agent graphs that return typed evidence handoffs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from agent.graph import PRICES_USD_PER_1M
from agent.tools import RESULTS_DIR, TOOLS, NoDataError, OutOfRangeError, data_range
from agent.v2 import policy
from agent.v2.contract import AgentName, Evidence, Handoff
from agent.v2.llm import tool_schemas_for_agent
from agent.v2.state import SubagentState


class SubagentOutput(TypedDict):
    handoff: Handoff


def _slug(agent: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-")


def _cost(usage, model_name: str) -> float:
    input_price, output_price = PRICES_USD_PER_1M.get(model_name, (0.0, 0.0))
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000


def _schemas_for(agent: str) -> list[dict]:
    return tool_schemas_for_agent(agent)


def build_subagent(
    agent: AgentName,
    llm,
    *,
    results_dir: Path | str = RESULTS_DIR,
    search_fn=None,
):
    """Build a select-tool → run-tool graph; its public result is one Handoff."""
    model_name = (
        getattr(llm, "model_name", None)
        or getattr(llm, "model", None)
        or "gpt-5.4-mini"
    )

    def make_handoff(state, *, status, detail=None, evidence=None, cost=None, tool_attempt=None):
        usage_cost = cost
        if usage_cost is None:
            usage_cost = _cost(state.get("route_usage"), model_name) if state.get("route_usage") else 0.0
        task = state.get("task", "")
        inputs = dict(state.get("inputs", {"task": task}))
        if tool_attempt is not None:
            inputs["tool_attempts"] = [tool_attempt]
        return Handoff(
            task=task,
            agent=agent,
            inputs=inputs,
            evidence=evidence or [],
            status=status,
            detail=detail,
            hop=state["hop"],
            cost_usd_hop=usage_cost,
            cost_usd_so_far=usage_cost,
        )

    def select_tool(state: SubagentState):
        try:
            decision, usage = llm.subagent_route(
                agent, state.get("task", ""), _schemas_for(agent)
            )
        except (TypeError, ValueError) as exc:
            handoff = make_handoff(
                state, status="invalid_args", detail=str(exc), cost=0.0
            )
            return {"handoff": handoff}
        if decision is None:
            return {
                "handoff": make_handoff(
                    state, status="out_of_scope", detail="no tool selected",
                    cost=_cost(usage, model_name),
                )
            }
        return {"decision": decision, "route_usage": usage}

    def after_select(state: SubagentState) -> str:
        return END if state.get("handoff") is not None else "run_tool"

    def run_tool(state: SubagentState):
        decision = state["decision"]
        route_cost = _cost(state.get("route_usage"), model_name)
        if not policy.tool_allowed(agent, decision.tool):
            return {
                "handoff": make_handoff(
                    state, status="invalid_args", detail="tool outside agent scope", cost=route_cost,
                    tool_attempt={"tool": decision.tool, "args": dict(decision.args)},
                )
            }
        fn = TOOLS.get(decision.tool)
        if fn is None:
            return {
                "handoff": make_handoff(
                    state, status="invalid_args", detail="unknown tool", cost=route_cost,
                    tool_attempt={"tool": decision.tool, "args": dict(decision.args)},
                )
            }
        kwargs = dict(decision.args)
        kwargs["results_dir"] = results_dir
        if decision.tool == "search_fomc" and search_fn is not None:
            kwargs["search_fn"] = search_fn
        try:
            output = fn(**kwargs)
            bounds = data_range(decision.tool, results_dir, model=decision.args.get("model"))
        except OutOfRangeError as exc:
            status, detail = "out_of_range", str(exc)
        except NoDataError as exc:
            status, detail = "no_data", str(exc)
        except (ValueError, TypeError) as exc:
            status, detail = "invalid_args", str(exc)
        else:
            status, detail = "ok", None
            evidence = Evidence(
                tool_call_id=f"{_slug(agent)}-h{state['hop']}-t1",
                tool=decision.tool,
                args=dict(decision.args),
                rows=output["rows"],
                source=output["source"],
                data_range=bounds,
            )
            return {
                "handoff": make_handoff(
                    state, status=status, detail=detail, evidence=[evidence], cost=route_cost,
                    tool_attempt={"tool": decision.tool, "args": dict(decision.args)},
                )
            }
        return {"handoff": make_handoff(
            state, status=status, detail=detail, cost=route_cost,
            tool_attempt={"tool": decision.tool, "args": dict(decision.args)},
        )}

    graph = StateGraph(SubagentState, output_schema=SubagentOutput)
    graph.add_node("select_tool", select_tool)
    graph.add_node("run_tool", run_tool)
    graph.set_entry_point("select_tool")
    graph.add_conditional_edges("select_tool", after_select, {"run_tool": "run_tool", END: END})
    graph.add_edge("run_tool", END)
    return graph.compile(name=f"vfl-agent-v2-{_slug(agent)}")
