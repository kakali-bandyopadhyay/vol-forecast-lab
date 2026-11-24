"""LangGraph state schemas for v2 supervisor and sub-agent graphs."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from agent.schema import Answer, RouteDecision, Usage
from agent.v2.contract import AgentName, Handoff, Plan


class SupervisorState(TypedDict, total=False):
    thread_id: str
    question: str
    plan: Plan | None
    pending: list[AgentName]
    called: Annotated[list[AgentName], operator.add]
    handoffs: Annotated[list[Handoff], operator.add]
    hops: int
    replans: int
    cost_usd: Annotated[float, operator.add]
    max_call_cost_usd: float
    answer: Answer | None
    rejection: str | None
    attempts: int
    stop_reason: str | None
    refusal: bool


class SubagentState(TypedDict, total=False):
    agent: AgentName
    task: str
    inputs: dict[str, Any]
    hop: int
    decision: RouteDecision | None
    route_usage: Usage
    handoff: Handoff | None
