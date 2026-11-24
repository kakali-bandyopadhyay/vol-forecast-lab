"""Supervisor graph and public runner for the v2 tool-scoped agents."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from agent.graph import CostCapExceeded, PRICES_USD_PER_1M, is_advice
from agent.schema import Answer
from agent.tools import RESULTS_DIR
from agent.v2 import policy
from agent.v2.contract import Handoff
from agent.v2.state import SupervisorState
from agent.v2.subagents import build_subagent
from agent.v2.verify_multi import verify_multi


@dataclass
class V2Result:
    answer: Answer
    agents_called: list[str]
    tools_called: list[str]
    tool_call_ids: list[str]
    hops: int
    replans: int
    attempts: int
    cost_usd: float
    latency_s: float
    stop_reason: str | None
    last_rejection: str | None
    handoffs: list[dict[str, Any]]


def _cost(usage, model_name: str) -> float:
    input_price, output_price = PRICES_USD_PER_1M.get(model_name, (0.0, 0.0))
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000


def _handoff_dump(handoffs: list[Handoff]) -> list[dict[str, Any]]:
    return [handoff.model_dump(mode="json") for handoff in handoffs]


def build_supervisor_graph(
    llm,
    *,
    model_name,
    results_dir=RESULTS_DIR,
    search_fn=None,
    max_cost_usd=0.25,
    checkpointer=None,
    store=None,
):
    """Compile the guard → plan → dispatch → draft → multi-evidence verify graph."""
    subagents = {
        agent: build_subagent(agent, llm, results_dir=results_dir, search_fn=search_fn)
        for agent in policy.SUBAGENT_TOOLS
    }
    # The sub-agent graph obtains its model name from the adapter for pricing.
    if not hasattr(llm, "model_name"):
        try:
            llm.model_name = model_name
        except (AttributeError, TypeError):
            pass

    def projected_cost(state):
        if state.get("cost_usd", 0.0) + max(state.get("max_call_cost_usd", 0.0), 0.002) > max_cost_usd:
            raise CostCapExceeded(
                f"projected next call exceeds USD {max_cost_usd:.2f}",
                cost_usd=state.get("cost_usd", 0.0),
            )

    def guard(state: SupervisorState):
        if is_advice(state["question"]):
            return {
                "answer": Answer(refused=True, refusal_reason="investment advice is out of scope"),
                "stop_reason": "refused",
            }
        return {"stop_reason": None}

    def after_guard(state: SupervisorState) -> str:
        return END if state.get("answer") is not None else "plan"

    def plan_node(state: SupervisorState):
        projected_cost(state)
        plan, usage = llm.plan(state["question"])
        delta = _cost(usage, model_name)
        reason = policy.validate_plan(plan)
        if reason is not None:
            return {
                "answer": Answer(refused=True, refusal_reason=reason),
                "stop_reason": "refused",
                "cost_usd": delta,
                "max_call_cost_usd": delta,
            }
        return {
            "plan": plan,
            "pending": list(plan.agents),
            "called": [],
            "handoffs": [],
            "hops": 0,
            "replans": 0,
            "cost_usd": delta,
            "max_call_cost_usd": delta,
        }

    def dispatch(state: SupervisorState):
        projected_cost(state)
        agent = state["pending"][0]
        pending = state["pending"][1:]
        hop = state.get("hops", 0) + 1
        subtask = state["plan"].subtasks.get(agent, "")
        subagent_result = subagents[agent].invoke(
            {
                "agent": agent,
                "task": subtask,
                "inputs": {"question": state["question"], "task": subtask},
                "hop": hop,
            }
        )
        handoff = subagent_result["handoff"]
        total_cost = state.get("cost_usd", 0.0) + handoff.cost_usd_hop
        handoff = handoff.model_copy(update={"cost_usd_so_far": total_cost})
        runtime["handoffs"].append(handoff)
        runtime["hops"] = hop
        runtime["cost_usd"] = total_cost
        return {
            "pending": pending,
            "called": [agent],
            "handoffs": [handoff],
            "hops": hop,
            "cost_usd": handoff.cost_usd_hop,
            "max_call_cost_usd": max(state.get("max_call_cost_usd", 0.0), handoff.cost_usd_hop),
        }

    def after_handoff(state: SupervisorState) -> str:
        return policy.next_step(state)

    def replan_node(state: SupervisorState):
        projected_cost(state)
        plan, usage = llm.replan(state["question"], list(state.get("handoffs", [])))
        delta = _cost(usage, model_name)
        called = list(state.get("called", []))
        replan_reason = policy.validate_replan(plan, called)
        if replan_reason is not None:
            return {
                "answer": Answer(refused=True, refusal_reason="no sub-agent can answer this"),
                "stop_reason": "refused",
                "cost_usd": delta,
                "max_call_cost_usd": max(state.get("max_call_cost_usd", 0.0), delta),
                "replans": state.get("replans", 0) + 1,
            }
        reason = policy.validate_plan(plan)
        if reason is not None:
            return {
                "answer": Answer(refused=True, refusal_reason=reason),
                "stop_reason": "refused",
                "cost_usd": delta,
                "max_call_cost_usd": max(state.get("max_call_cost_usd", 0.0), delta),
                "replans": state.get("replans", 0) + 1,
            }
        return {
            "plan": plan,
            "pending": list(plan.agents),
            "replans": state.get("replans", 0) + 1,
            "cost_usd": delta,
            "max_call_cost_usd": max(state.get("max_call_cost_usd", 0.0), delta),
        }

    def after_replan(state: SupervisorState) -> str:
        if state.get("answer") is not None:
            return "refuse"
        return "dispatch"

    def draft(state: SupervisorState):
        projected_cost(state)
        answer, usage = llm.draft_v2(
            state["question"], list(state.get("handoffs", [])), state.get("rejection")
        )
        delta = _cost(usage, model_name)
        runtime["cost_usd"] = state.get("cost_usd", 0.0) + delta
        return {
            "answer": answer,
            "attempts": state.get("attempts", 0) + 1,
            "rejection": state.get("rejection"),
            "cost_usd": delta,
            "max_call_cost_usd": max(state.get("max_call_cost_usd", 0.0), delta),
        }

    def verify_node(state: SupervisorState):
        ok, reason = verify_multi(state["answer"], list(state.get("handoffs", [])))
        if ok:
            rows = [row for h in state.get("handoffs", []) for item in h.evidence for row in item.rows]
            answer = state["answer"].model_copy(update={"source_rows": rows})
            return {
                "answer": answer,
                "stop_reason": "refused" if answer.refused else "answered",
                "rejection": None,
            }
        runtime["last_rejection"] = reason
        if state.get("attempts", 0) < policy.MAX_DRAFTS:
            return {"answer": None, "rejection": reason}
        return {
            "answer": Answer(refused=True, refusal_reason="could not verify"),
            "stop_reason": "could_not_verify",
            "rejection": reason,
        }

    def after_verify(state: SupervisorState) -> str:
        return "draft" if state.get("answer") is None else END

    def refuse(state: SupervisorState):
        existing_answer = state.get("answer")
        keep_refusal = existing_answer is not None and existing_answer.refused
        reason = existing_answer.refusal_reason if keep_refusal else policy.refusal_reason(state)
        stop_reason = state.get("stop_reason") or "refused"
        if reason == "max hops reached":
            stop_reason = "max_hops"
        return {
            "answer": existing_answer if keep_refusal else Answer(refused=True, refusal_reason=reason),
            "stop_reason": stop_reason,
        }

    runtime: dict[str, Any] = {"handoffs": [], "hops": 0, "cost_usd": 0.0, "last_rejection": None}
    graph = StateGraph(SupervisorState)
    graph.add_node("guard", guard)
    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", dispatch)
    graph.add_node("replan", replan_node)
    graph.add_node("draft", draft)
    graph.add_node("verify", verify_node)
    graph.add_node("refuse", refuse)
    graph.set_entry_point("guard")
    graph.add_conditional_edges("guard", after_guard, {"plan": "plan", END: END})
    graph.add_conditional_edges("plan", lambda s: "refuse" if s.get("answer") else "dispatch", {"dispatch": "dispatch", "refuse": "refuse"})
    graph.add_conditional_edges(
        "dispatch", after_handoff,
        {"dispatch": "dispatch", "replan": "replan", "draft": "draft", "refuse": "refuse"},
    )
    graph.add_conditional_edges("replan", after_replan, {"dispatch": "dispatch", "refuse": "refuse"})
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", after_verify, {"draft": "draft", END: END})
    compiled = graph.compile(checkpointer=checkpointer, store=store, name="vfl-agent-v2")

    class SupervisorGraph:
        def invoke(self, inputs, config=None):
            result = compiled.invoke(inputs, config=config)
            return result

        def _runtime(self):
            return runtime

    return SupervisorGraph()


def answer_question_v2(
    question: str,
    llm,
    *,
    model_name,
    thread_id=None,
    results_dir=RESULTS_DIR,
    search_fn=None,
    max_cost_usd=0.25,
    checkpointer=None,
    store=None,
) -> V2Result:
    """Run one question and return routing, evidence, cost, and verification metadata."""
    started = time.perf_counter()
    graph = build_supervisor_graph(
        llm,
        model_name=model_name,
        results_dir=results_dir,
        search_fn=search_fn,
        max_cost_usd=max_cost_usd,
        checkpointer=checkpointer,
        store=store,
    )
    runtime = graph._runtime()
    config = {"configurable": {"thread_id": thread_id}} if thread_id is not None else None
    try:
        final = graph.invoke({"question": question, "thread_id": thread_id, "cost_usd": 0.0}, config=config)
    except CostCapExceeded as exc:
        final = {
            "answer": Answer(refused=True, refusal_reason="cost cap reached"),
            "stop_reason": "cost_cap",
            "cost_usd": exc.cost_usd,
            "called": [],
            "handoffs": runtime["handoffs"],
            "hops": runtime["hops"],
            "replans": 0,
            "attempts": 0,
        }
    handoffs = list(final.get("handoffs", runtime["handoffs"]))
    evidence = [item for handoff in handoffs for item in handoff.evidence]
    return V2Result(
        answer=final["answer"],
        agents_called=[handoff.agent for handoff in handoffs],
        tools_called=[item.tool for item in evidence],
        tool_call_ids=[item.tool_call_id for item in evidence],
        hops=int(final.get("hops", runtime["hops"])),
        replans=int(final.get("replans", 0)),
        attempts=int(final.get("attempts", 0)),
        cost_usd=float(final.get("cost_usd", runtime["cost_usd"])),
        latency_s=time.perf_counter() - started,
        stop_reason=final.get("stop_reason"),
        last_rejection=final.get("rejection", runtime["last_rejection"]),
        handoffs=_handoff_dump(handoffs),
    )
