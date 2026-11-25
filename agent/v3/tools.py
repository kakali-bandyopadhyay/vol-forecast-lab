"""In-process Claude Agent SDK wrappers for the existing read-only tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent import tools as source_tools

SERVER_NAME = "vfl"
TOOL_NAMES = ("get_forecast", "compare_models", "event_effect", "search_fomc")

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_forecast": {"type": "object", "properties": {
        "as_of": {"type": "string", "description": "Forecast date in YYYY-MM-DD form."},
        "model": {"type": "string", "enum": ["M0", "M1", "M2", "M3", "M4"]},
    }, "required": ["as_of", "model"], "additionalProperties": False},
    "compare_models": {"type": "object", "properties": {
        "start": {"type": "string"}, "end": {"type": "string"},
        "metric": {"type": "string", "enum": ["qlike", "squared_error"]},
    }, "required": ["start", "end", "metric"], "additionalProperties": False},
    "event_effect": {"type": "object", "properties": {
        "event": {"type": "string", "enum": ["cpi", "fomc"]},
        "component": {"type": "string", "enum": ["on", "oc", "gk"]},
    }, "required": ["event", "component"], "additionalProperties": False},
    "search_fomc": {"type": "object", "properties": {
        "query": {"type": "string"},
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "doc_type": {"type": ["string", "null"], "enum": ["statement", "minutes", None]},
    }, "required": ["query"], "additionalProperties": False},
}


def sdk_tool_name(name: str) -> str:
    return f"mcp__{SERVER_NAME}__{name}"


def _error_status(exc: Exception) -> str:
    if isinstance(exc, source_tools.OutOfRangeError):
        return "out_of_range"
    if isinstance(exc, source_tools.NoDataError):
        return "no_data"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "invalid_args"
    return "no_data"


def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    results_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run one source tool, returning structured data even for known failures."""
    if name not in TOOL_NAMES:
        return {"status": "invalid_args", "detail": f"unknown tool {name!r}", "tool": name, "args": args}
    function = getattr(source_tools, name)
    kwargs = dict(args)
    data_root = Path(results_dir) if results_dir is not None else Path(source_tools.RESULTS_DIR)
    if not data_root.is_absolute():
        data_root = Path(source_tools.REPO_ROOT) / data_root
    kwargs["results_dir"] = data_root.resolve()
    try:
        data = function(**kwargs)
        return {
            "status": "ok", "tool": name, "args": dict(args),
            "rows": data.get("rows", []), "source": data.get("source", ""),
            "row_keys": data.get("row_keys", []), "detail": None,
        }
    except Exception as exc:  # source tools raise ValueError subclasses for domain errors
        return {
            "status": _error_status(exc), "tool": name, "args": dict(args),
            "rows": [], "source": "", "row_keys": [], "detail": str(exc),
        }


def _mcp_result(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload,
            "is_error": payload["status"] != "ok"}


def build_sdk_tools(
    *, results_dir: Path | str | None = None, recorder: dict[str, list] | None = None
):
    """Create decorated handlers and their in-process SDK MCP server."""
    recorder = recorder if recorder is not None else {}
    recorder.setdefault("tool_calls", [])
    data_root = Path(results_dir) if results_dir is not None else Path(source_tools.RESULTS_DIR)
    if not data_root.is_absolute():
        data_root = Path(source_tools.REPO_ROOT) / data_root
    data_root = data_root.resolve()
    handlers = []
    descriptions = {
        "get_forecast": "Read one model's 30-session variance forecast for an exact date.",
        "compare_models": "Compare model losses over an inclusive date range.",
        "event_effect": "Read one event-study estimate for an event and component.",
        "search_fomc": "Search Federal Reserve statements or minutes and return cited chunks.",
    }
    for name in TOOL_NAMES:
        schema = TOOL_SCHEMAS[name]

        async def handler(args, _name=name, _schema=schema):
            result = execute_tool(_name, args, results_dir=data_root)
            pending = next((
                call for call in recorder["tool_calls"]
                if call.get("tool") == _name and call.get("args") == args and call.get("status") == "pending"
            ), None)
            if pending is None:
                recorder["tool_calls"].append({
                    "tool": _name, "args": dict(args), "status": result["status"],
                    "rows": result["rows"], "source": result["source"], "detail": result["detail"],
                })
            else:
                pending.update({
                    "status": result["status"], "rows": result["rows"],
                    "source": result["source"], "detail": result["detail"],
                })
            return _mcp_result(result)

        handlers.append(tool(name, descriptions[name], schema)(handler))
    return handlers, create_sdk_mcp_server(name=SERVER_NAME, version="3.0.0", tools=handlers)
