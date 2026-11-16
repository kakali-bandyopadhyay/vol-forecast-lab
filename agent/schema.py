"""Pydantic and dataclass records for the Step 11 forecasting agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


@dataclass
class Usage:
    """Token usage for one model call."""

    input_tokens: int
    output_tokens: int


class Citation(BaseModel):
    """A source citation attached to an answer."""

    chunk_id: str
    url: str


class RouteDecision(BaseModel):
    """The model-selected tool and its arguments."""

    tool: str
    args: dict[str, Any]
    usage: Usage


class Answer(BaseModel):
    """The final answer returned by the agent."""

    as_of: str | None = None
    horizon: str = "30 sessions"
    forecast: str | None = None
    implied_variance: str | None = None
    spread: str | None = None
    text: str = ""
    limitations: str = ""
    source_rows: list[Any] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
