"""Multi-evidence verifier built around the unchanged v1 verifier."""

from __future__ import annotations

import re
from datetime import date, datetime
from collections.abc import Mapping

from agent.verifier import verify
from agent.v2.contract import Handoff

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _answer_strings(answer) -> list[str]:
    return [
        str(getattr(answer, name))
        for name in (
            "as_of", "horizon", "forecast", "implied_variance", "spread",
            "text", "limitations", "refusal_reason",
        )
        if getattr(answer, name, None) is not None
    ]


def _contains_date(value, token: str) -> bool:
    """Find an exact ISO date in nested tool arguments or returned row values."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10] == token
    if isinstance(value, str):
        return re.search(rf"(?<!\d){re.escape(token)}(?!\d)", value) is not None
    if isinstance(value, Mapping):
        return any(_contains_date(item, token) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_date(item, token) for item in value)
    return False


def verify_multi(answer, handoffs: list[Handoff]) -> tuple[bool, str]:
    """Verify pooled numbers plus citation provenance and evidence date coverage."""
    if answer.refused:
        return verify(answer, [], None, "get_forecast")

    evidence = [item for handoff in handoffs for item in handoff.evidence]
    rows = [row for item in evidence for row in item.rows]
    if not evidence:
        return False, "no tool evidence"
    fomc_chunk_ids = {
        str(row.get("chunk_id"))
        for item in evidence if item.tool == "search_fomc"
        for row in item.rows
    }
    for citation in answer.citations:
        if citation.chunk_id not in fomc_chunk_ids:
            return False, f"citation {citation.chunk_id} is not from search_fomc evidence"
    tool_name = "search_fomc" if any(item.tool == "search_fomc" for item in evidence) else evidence[0].tool
    ok, reason = verify(answer, rows, None, tool_name)
    if not ok:
        return ok, reason

    ranges = [item.data_range for item in evidence if item.data_range is not None]
    supported_values = [item.args for item in evidence]
    supported_values.extend(row for item in evidence for row in item.rows)
    for text in _answer_strings(answer):
        for token in _ISO_DATE.findall(text):
            try:
                value = date.fromisoformat(token)
            except ValueError:
                return False, f"invalid date {token}"
            in_range = any(
                date.fromisoformat(first) <= value <= date.fromisoformat(last)
                for first, last in ranges
            )
            if not in_range and not any(_contains_date(item, token) for item in supported_values):
                return False, f"date {token} is not supported by evidence"
    return True, ""
