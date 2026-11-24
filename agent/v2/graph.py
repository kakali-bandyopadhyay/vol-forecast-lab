"""Supervisor graph and public runner for the v2 tool-scoped agents."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from agent.graph import CostCapExceeded, PRICES_USD_PER_1M, is_advice
from agent.schema import Answer
from agent.tools import RESULTS_DIR
from agent.v2 import policy
from agent.v2.contract import Handoff
from agent.v2.memory import (
    load_user_prefs,
    memory_context,
    merge_thread_prefs,
    parse_preference,
    save_user_pref,
    validate_pref,
)
from agent.v2.state import RESET, SupervisorState
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
    rejections: list[dict[str, Any]]
    handoffs: list[dict[str, Any]]
    resolved_question: str
    memory_reads: list[str]
    memory_writes: list[dict[str, Any]]
    turn: int


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
        return "finalize" if state.get("answer") is not None else "memory"

    def memory_node(state: SupervisorState, config: RunnableConfig, runtime: Runtime):
        store_obj = runtime.store
        configurable = (config or {}).get("configurable", {})
        user_id = configurable.get("user_id", state.get("user_id", "default"))
        turns = list(state.get("turns", []))
        user_prefs = dict(state.get("user_prefs", {}))
        thread_prefs = dict(state.get("thread_prefs", {}))
        if not turns and store_obj is not None:
            user_prefs = load_user_prefs(store_obj, user_id)

        memory_writes: list[dict[str, Any]] = []
        write = parse_preference(state["question"])
        if write is not None and validate_pref(write.key, write.value) is None:
            if write.scope == "user":
                if store_obj is not None:
                    memory_writes.append(save_user_pref(store_obj, user_id, write))
                    user_prefs = load_user_prefs(store_obj, user_id)
                else:
                    user_prefs[write.key] = write.value
            else:
                thread_prefs = merge_thread_prefs(thread_prefs, write)
                memory_writes.append({"scope": write.scope, "key": write.key, "value": write.value})

        context = memory_context(state["question"], turns, thread_prefs, user_prefs)
        resolved_question = state["question"]
        memory_reads: list[str] = []
        delta = 0.0
        max_call_cost = state.get("max_call_cost_usd", 0.0)
        metrics["question"] = state["question"]
        metrics["prior_turns"] = turns
        metrics["resolved_question"] = resolved_question
        metrics["memory_reads"] = memory_reads
        metrics["memory_writes"] = memory_writes
        # Explicit values already present in a first-turn question need no
        # rewrite; only remembered turns or reusable stored defaults do.
        if turns or thread_prefs or user_prefs:
            projected_cost(state)
            resolved, usage = llm.contextualize(state["question"], context)
            resolved_question = resolved.standalone_question
            delta = _cost(usage, model_name)
            max_call_cost = max(max_call_cost, delta)
            turn_ids = {item.get("turn") for item in context["turns"]}
            memory_reads.extend(
                f"turn:{turn_id}" for turn_id in resolved.used_turns if turn_id in turn_ids
            )
            memory_reads.extend(
                key for key in resolved.used_defaults if key in context["defaults"]
            )

        metrics["question"] = state["question"]
        metrics["prior_turns"] = turns
        metrics["resolved_question"] = resolved_question
        metrics["memory_reads"] = memory_reads
        metrics["memory_writes"] = memory_writes
        return {
            "user_id": user_id,
            "user_prefs": user_prefs,
            "thread_prefs": thread_prefs,
            "resolved_question": resolved_question,
            "memory_reads": memory_reads,
            "memory_writes": memory_writes,
            "cost_usd": delta,
            "max_call_cost_usd": max_call_cost,
        }

    def plan_node(state: SupervisorState):
        projected_cost(state)
        plan, usage = llm.plan(state["resolved_question"])
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
                "inputs": {"question": state["resolved_question"], "task": subtask},
                "hop": hop,
            }
        )
        handoff = subagent_result["handoff"]
        total_cost = state.get("cost_usd", 0.0) + handoff.cost_usd_hop
        handoff = handoff.model_copy(update={"cost_usd_so_far": total_cost})
        metrics["handoffs"].append(handoff)
        metrics["hops"] = hop
        metrics["cost_usd"] = total_cost
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
        plan, usage = llm.replan(state["resolved_question"], list(state.get("handoffs", [])))
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
            state["resolved_question"], list(state.get("handoffs", [])), state.get("rejection")
        )
        delta = _cost(usage, model_name)
        metrics["cost_usd"] = state.get("cost_usd", 0.0) + delta
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
        metrics["last_rejection"] = reason
        answer = state.get("answer")
        draft_text = " ".join(
            str(value)
            for value in (
                getattr(answer, "as_of", None),
                getattr(answer, "horizon", None),
                getattr(answer, "forecast", None),
                getattr(answer, "implied_variance", None),
                getattr(answer, "spread", None),
                getattr(answer, "text", None),
                getattr(answer, "limitations", None),
                getattr(answer, "refusal_reason", None),
            )
            if value
        )
        metrics["rejections"].append(
            {
                "attempt": int(state.get("attempts", 0)),
                "reason": reason,
                "draft_text": draft_text,
            }
        )
        if state.get("attempts", 0) < policy.MAX_DRAFTS:
            return {"answer": None, "rejection": reason}
        return {
            "answer": Answer(refused=True, refusal_reason="could not verify"),
            "stop_reason": "could_not_verify",
            "rejection": reason,
        }

    def after_verify(state: SupervisorState) -> str:
        return "draft" if state.get("answer") is None else "finalize"

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

    def finalize(state: SupervisorState):
        turns = list(state.get("turns", []))
        turn_number = len(turns) + 1
        resolved_question = state.get("resolved_question") or state["question"]
        tool_args = [
            {"tool": item.tool, "args": item.args}
            for handoff in state.get("handoffs", [])
            for item in handoff.evidence
        ]
        turn_record = {
            "turn": turn_number,
            "question": state["question"],
            "resolved": resolved_question,
            "tool_args": tool_args,
        }
        metrics["prior_turns"] = turns
        metrics["resolved_question"] = resolved_question
        metrics["memory_reads"] = state.get("memory_reads", [])
        metrics["memory_writes"] = state.get("memory_writes", [])
        metrics["turn"] = turn_number
        return {"turns": [turn_record], "turn": turn_number, "resolved_question": resolved_question}

    metrics: dict[str, Any] = {
        "handoffs": [], "hops": 0, "cost_usd": 0.0, "last_rejection": None,
        "rejections": [],
        "question": "", "prior_turns": [], "resolved_question": "",
        "memory_reads": [], "memory_writes": [], "turn": 0,
    }
    graph = StateGraph(SupervisorState)
    graph.add_node("guard", guard)
    graph.add_node("memory", memory_node)
    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", dispatch)
    graph.add_node("replan", replan_node)
    graph.add_node("draft", draft)
    graph.add_node("verify", verify_node)
    graph.add_node("refuse", refuse)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("guard")
    graph.add_conditional_edges("guard", after_guard, {"memory": "memory", "finalize": "finalize"})
    graph.add_edge("memory", "plan")
    graph.add_conditional_edges("plan", lambda s: "refuse" if s.get("answer") else "dispatch", {"dispatch": "dispatch", "refuse": "refuse"})
    graph.add_conditional_edges(
        "dispatch", after_handoff,
        {"dispatch": "dispatch", "replan": "replan", "draft": "draft", "refuse": "refuse"},
    )
    graph.add_conditional_edges("replan", after_replan, {"dispatch": "dispatch", "refuse": "refuse"})
    graph.add_edge("refuse", "finalize")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", after_verify, {"draft": "draft", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    compiled = graph.compile(checkpointer=checkpointer, store=store, name="vfl-agent-v2")

    class SupervisorGraph:
        def invoke(self, inputs, config=None):
            try:
                return compiled.invoke(inputs, config=config)
            except CostCapExceeded as exc:
                saved = {}
                if checkpointer is not None and config is not None:
                    snapshot = compiled.get_state(config)
                    saved = dict(snapshot.values or {})
                question = inputs["question"]
                turns = list(saved.get("turns", metrics.get("prior_turns", [])))
                turn_number = len(turns) + 1
                resolved_question = metrics.get("resolved_question") or question
                turn_record = {
                    "turn": turn_number,
                    "question": question,
                    "resolved": resolved_question,
                    "tool_args": [
                        {"tool": evidence.tool, "args": evidence.args}
                        for handoff in metrics.get("handoffs", [])
                        for evidence in handoff.evidence
                    ],
                }
                values = {
                    "answer": Answer(refused=True, refusal_reason="cost cap reached"),
                    "stop_reason": "cost_cap",
                    "resolved_question": resolved_question,
                    "memory_reads": metrics.get("memory_reads", []),
                    "memory_writes": metrics.get("memory_writes", []),
                    "turn": turn_number,
                }
                if checkpointer is not None and config is not None:
                    compiled.update_state(config, {**values, "turns": [turn_record]})
                    return dict(compiled.get_state(config).values or {})
                return {
                    **inputs,
                    **saved,
                    **values,
                    "cost_usd": exc.cost_usd,
                    "handoffs": metrics.get("handoffs", []),
                    "hops": metrics.get("hops", 0),
                    "replans": saved.get("replans", 0),
                    "attempts": saved.get("attempts", 0),
                    "turns": turns + [turn_record],
                }

        def get_state(self, config):
            return compiled.get_state(config)

        def _runtime(self):
            return metrics

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
    user_id="default",
    trace_meta: dict | None = None,
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
    runtime["question"] = question
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    if trace_meta is not None:
        config.update(
            {
                "run_name": trace_meta["run_name"],
                "tags": list(trace_meta["tags"]),
                "metadata": dict(trace_meta["metadata"]),
            }
        )
    try:
        final = graph.invoke(
            {
                "question": question,
                "thread_id": thread_id,
                "answer": None,
                "rejection": None,
                "stop_reason": None,
                "attempts": 0,
                "refusal": False,
                "plan": None,
                "pending": [],
                "hops": 0,
                "replans": 0,
                "max_call_cost_usd": 0.0,
                "called": RESET,
                "handoffs": RESET,
                "cost_usd": RESET,
                "user_id": user_id,
                "resolved_question": None,
                "memory_reads": [],
                "memory_writes": [],
                "turn": None,
            },
            config=config,
        )
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
            "resolved_question": runtime.get("resolved_question") or question,
            "memory_reads": runtime.get("memory_reads", []),
            "memory_writes": runtime.get("memory_writes", []),
            "rejections": runtime.get("rejections", []),
            "turn": runtime.get("turn", len(runtime.get("prior_turns", [])) + 1),
        }
    handoffs = list(final.get("handoffs", runtime["handoffs"]))
    evidence = [item for handoff in handoffs for item in handoff.evidence]
    tools_called = []
    for handoff in handoffs:
        if handoff.evidence:
            tools_called.extend(item.tool for item in handoff.evidence)
        else:
            tools_called.extend(
                attempted.get("tool")
                for attempted in handoff.inputs.get("tool_attempts", [])
                if attempted.get("tool") is not None
            )
    return V2Result(
        answer=final["answer"],
        agents_called=[handoff.agent for handoff in handoffs],
        tools_called=tools_called,
        tool_call_ids=[item.tool_call_id for item in evidence],
        hops=int(final.get("hops", runtime["hops"])),
        replans=int(final.get("replans", 0)),
        attempts=int(final.get("attempts", 0)),
        cost_usd=float(final.get("cost_usd", runtime["cost_usd"])),
        latency_s=time.perf_counter() - started,
        stop_reason=final.get("stop_reason"),
        last_rejection=final.get("rejection", runtime["last_rejection"]),
        rejections=list(final.get("rejections", runtime.get("rejections", []))),
        handoffs=_handoff_dump(handoffs),
        resolved_question=final.get("resolved_question") or question,
        memory_reads=list(final.get("memory_reads", [])),
        memory_writes=list(final.get("memory_writes", [])),
        turn=int(final.get("turn") or runtime.get("turn") or 1),
    )
