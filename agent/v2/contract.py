"""Typed handoffs between the v2 supervisor and tool-scoped sub-agents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentName = Literal["forecast", "event-effect", "FOMC retrieval"]
ToolName = Literal["get_forecast", "compare_models", "event_effect", "search_fomc"]
HandoffStatus = Literal["ok", "no_data", "out_of_range", "invalid_args", "out_of_scope"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    tool: ToolName
    args: dict[str, Any]
    rows: list[dict[str, Any]]
    source: str
    data_range: tuple[str, str] | None


class Handoff(BaseModel):
    """The only sub-agent result; it carries tool evidence rather than prose."""

    model_config = ConfigDict(extra="forbid")

    task: str
    agent: AgentName
    inputs: dict[str, Any]
    evidence: list[Evidence] = Field(default_factory=list)
    status: HandoffStatus
    detail: str | None = None
    hop: int
    cost_usd_hop: float
    cost_usd_so_far: float


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[AgentName]
    subtasks: dict[str, str]
