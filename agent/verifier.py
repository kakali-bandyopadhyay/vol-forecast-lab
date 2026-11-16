"""Numeric, date and citation verifier for the Step 11 agent."""

from __future__ import annotations

import math
import numbers
import re
from datetime import date
from typing import Any

NUMBER_TOKEN = re.compile(r"(?<![A-Za-z\d.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
ALLOWED_CONSTANTS = {"30"}
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_NUMBER_FIELDS = ("forecast", "implied_variance", "spread", "text", "limitations")
_DATE_FIELDS = (
    "as_of", "horizon", "forecast", "implied_variance",
    "spread", "text", "limitations", "refusal_reason",
)


def _string_fields(answer) -> list[str]:
    return [
        str(getattr(answer, name))
        for name in _DATE_FIELDS
        if getattr(answer, name, None) is not None
    ]


def _candidate_strings(tool_output: list[dict[str, Any]]) -> set[str]:
    candidates = set(ALLOWED_CONSTANTS)
    for row in tool_output:
        for value in row.values():
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            candidates.add(f"{value:.6g}")
            candidates.add(f"{value:.3f}")
            if isinstance(value, numbers.Integral):
                candidates.add(str(value))
    return candidates


def verify(answer, tool_output: list[dict[str, Any]], data_range, tool_name: str) -> tuple[bool, str]:
    """Return (ok, reason); reason names the first rejected date, token or citation."""
    if answer.refused:
        if answer.refusal_reason:
            return True, ""
        return False, "refused answer has no refusal_reason"

    fields = _string_fields(answer)
    if data_range is not None:
        first, last = data_range
        for text in fields:
            for match in ISO_DATE.findall(text):
                try:
                    value = date.fromisoformat(match)
                except ValueError:
                    return False, f"invalid date {match}"
                if not (date.fromisoformat(first) <= value <= date.fromisoformat(last)):
                    return False, f"date {match} is outside data range {first}..{last}"

    candidates = _candidate_strings(tool_output)
    for name in _NUMBER_FIELDS:
        value = getattr(answer, name, None)
        if value is None:
            continue
        text = ISO_DATE.sub(" ", str(value))
        for token in NUMBER_TOKEN.findall(text):
            if token not in candidates:
                return False, f"unsupported number {token}"

    for citation in answer.citations:
        if tool_name != "search_fomc":
            return False, f"citation {citation.chunk_id} is not allowed for {tool_name}"
        known = any(str(row.get("chunk_id")) == citation.chunk_id for row in tool_output)
        if not known:
            return False, f"unknown citation chunk_id {citation.chunk_id}"

    return True, ""
