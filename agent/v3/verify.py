"""Adapter from v3 tool results to the unchanged v2 verifier contract."""

from __future__ import annotations

from typing import Any

from agent.schema import Answer
from agent.v2.contract import Evidence, Handoff
from agent.v2.verify_multi import verify_multi


def _policy_agent(agent: str | None) -> str:
    if agent in {"forecast", "event-effect", "FOMC retrieval"}:
        return agent
    return "forecast"


def build_handoffs(tool_calls: list[dict[str, Any]], tool_contexts: list[dict[str, Any]]) -> list[Handoff]:
    """Build typed evidence handoffs from recorded wrapper results."""
    handoffs: list[Handoff] = []
    used_contexts: set[int] = set()
    for call in tool_calls:
        context_index = next((
            index for index, item in enumerate(tool_contexts)
            if index not in used_contexts and item.get("tool") == call.get("tool")
            and item.get("args") == call.get("args")
        ), None)
        context = tool_contexts[context_index] if context_index is not None else {}
        if context_index is not None:
            used_contexts.add(context_index)
        tool_name = call.get("tool")
        if tool_name not in {"get_forecast", "compare_models", "event_effect", "search_fomc"}:
            continue
        status = call.get("status", "no_data")
        evidence = []
        if status == "ok":
            source = str(call.get("source") or "")
            evidence.append(Evidence(
                tool=tool_name,
                tool_call_id=f"v3-{len(handoffs)}-{tool_name}",
                args=dict(call.get("args") or {}),
                rows=list(call.get("rows") or []),
                source=source,
                data_range=None,
            ))
        handoffs.append(Handoff(
            task="v3 tool call", agent=_policy_agent(context.get("agent")),
            inputs={"tool_attempts": [{"tool": tool_name, "args": call.get("args", {})}]},
            evidence=evidence,
            status=status if status in {"ok", "no_data", "out_of_range", "invalid_args", "out_of_scope"} else "no_data",
            detail=call.get("detail"), hop=len(handoffs) + 1, cost_usd_hop=0, cost_usd_so_far=0,
        ))
    return handoffs


def verify_answer(answer: Answer, tool_calls: list[dict[str, Any]], tool_contexts: list[dict[str, Any]]):
    handoffs = build_handoffs(tool_calls, tool_contexts)
    ok, reason = verify_multi(answer, handoffs)
    if not ok:
        return Answer(
            refused=True, refusal_reason="could not verify", text="I could not verify this answer from the tool evidence.",
            horizon=answer.horizon,
        ), False, reason, handoffs
    return answer, True, "", handoffs
