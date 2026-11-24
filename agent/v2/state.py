"""LangGraph state schemas for v2 supervisor and sub-agent graphs."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from agent.schema import Answer, RouteDecision, Usage
from agent.v2.contract import AgentName, Handoff, Plan

RESET = "__v2_state_reset__"


def append_or_reset(left, right):
    """Append aggregate updates, or clear a persisted per-turn aggregate."""
    if right == RESET:
        return 0.0 if isinstance(left, (int, float)) else []
    if isinstance(left, (int, float)):
        return float(left or 0.0) + float(right or 0.0)
    return list(left or ()) + list(right or ())


class SupervisorState(TypedDict, total=False):
    thread_id: str
    question: str
    plan: Plan | None
    pending: list[AgentName]
    called: Annotated[list[AgentName], append_or_reset]
    handoffs: Annotated[list[Handoff], append_or_reset]
    hops: int
    replans: int
    cost_usd: Annotated[float, append_or_reset]
    max_call_cost_usd: float
    answer: Answer | None
    rejection: str | None
    attempts: int
    stop_reason: str | None
    refusal: bool
    user_id: str
    turns: Annotated[list[dict], operator.add]
    thread_prefs: dict[str, str]
    user_prefs: dict[str, str]
    resolved_question: str | None
    memory_reads: list[str]
    memory_writes: list[dict]
    turn: int | None


class SubagentState(TypedDict, total=False):
    agent: AgentName
    task: str
    inputs: dict[str, Any]
    hop: int
    decision: RouteDecision | None
    route_usage: Usage
    handoff: Handoff | None
