"""Real OpenAI adapter for the Step 11 forecasting agent."""

from __future__ import annotations

import json
import os
from typing import Any

from agent.schema import Answer, DraftAnswer, RouteDecision, Usage
from agent.tracing import get_tracing_config

ROUTE_INSTRUCTIONS = """You are a routing model for a volatility-forecasting research assistant.
Choose exactly one tool for the user's question. Do not answer the question yourself.
Use get_forecast for one date/model variance forecast, compare_models for mean QLIKE or squared error
over a date window, event_effect for CPI or FOMC effects, and search_fomc for Federal Reserve text.
Use ISO dates (YYYY-MM-DD)."""

DRAFT_INSTRUCTIONS = """Answer only from the supplied tool output. Never give investment advice.
Use dates only in YYYY-MM-DD form.
Write p_two_sided and p_holm values to 3 decimal places.
Write every other number exactly as Python f"{value:.6g}" (no trailing zeros) and copy it from the tool output.
Use horizon "30 sessions"; put caveats in limitations.
Only include citations when the tool output comes from search_fomc.
If a rejection is supplied, include it and fix only that reason."""


def _usage(response) -> Usage:
    usage = response.usage
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_forecast",
        "description": "Return one 30-day variance forecast for a model and ISO date.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "as_of": {"type": "string"},
                "model": {"type": "string", "enum": ["M0", "M1", "M2", "M3", "M4"]},
            },
            "required": ["as_of", "model"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "compare_models",
        "description": "Compare mean loss per model over an inclusive ISO date window.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"},
                "metric": {"type": "string", "enum": ["qlike", "squared_error"]},
            },
            "required": ["start", "end", "metric"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "event_effect",
        "description": "Return the primary-run event effect for a CPI/FOMC and component pair.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "event": {"type": "string", "enum": ["cpi", "fomc"]},
                "component": {"type": "string", "enum": ["on", "oc", "gk"]},
            },
            "required": ["event", "component"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_fomc",
        "description": "Search Federal Reserve statements and minutes with optional date filters.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


class OpenAIAgentLLM:
    """Thin Responses-API adapter; tests inject a fake instead."""

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        tracing = get_tracing_config()
        if tracing["tracing"]:
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            if tracing["project"]:
                os.environ.setdefault("LANGSMITH_PROJECT", tracing["project"])
        self.model = model or os.environ["AGENT_MODEL"]
        self.client = OpenAI()

    def route(self, question: str) -> RouteDecision:
        response = self.client.responses.create(
            model=self.model,
            instructions=ROUTE_INSTRUCTIONS,
            input=question,
            tools=TOOL_SCHEMAS,
            tool_choice="required",
        )
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if len(calls) != 1:
            raise ValueError(f"expected exactly one tool call, got {len(calls)}")
        call = calls[0]
        args = json.loads(call.arguments or "{}")
        return RouteDecision(tool=call.name, args=args, usage=_usage(response))

    def draft(
        self,
        question: str,
        tool_output: dict[str, Any],
        rejection: str | None = None,
    ) -> tuple[Answer, Usage]:
        content = {
            "question": question,
            "tool_output": tool_output,
            "rejection": rejection,
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=DRAFT_INSTRUCTIONS,
            input=json.dumps(content, default=str),
            text_format=DraftAnswer,
        )
        if response.output_parsed is None:
            raise ValueError("model returned no structured answer")
        return Answer(**response.output_parsed.model_dump()), _usage(response)
