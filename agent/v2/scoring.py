"""Scoring helpers for the frozen v2 evaluation (rule X.2).

Pure functions only: no LLM, no pandas, no I/O. The runner calls these; the
frozen expected values come from prereg/questions_v2.json.
"""

from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_CASE_INSENSITIVE_ARGS = {"model", "metric", "event", "component"}


def _clean_token(token) -> str:
    """Normalise a written number without changing its precision."""
    return (
        str(token)
        .strip()
        .replace(",", "")
        .replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )


def _split_scientific(text: str) -> tuple[str, int]:
    mantissa, _, exponent = text.lower().partition("e")
    return mantissa, int(exponent) if exponent else 0


def _significant_figures(text: str) -> int:
    """Count significant figures from the token as written (not the float)."""
    mantissa, _ = _split_scientific(text.lstrip("+-"))
    digits = mantissa.replace(".", "").lstrip("0")
    if not digits:
        return 0
    if "." not in mantissa:
        # Trailing zeros in an integer are placeholders, not significant.
        digits = digits.rstrip("0")
    return len(digits)


def _decimal_places(text: str) -> int:
    """Decimal places of the written value, accounting for the exponent."""
    mantissa, exponent = _split_scientific(text.lstrip("+-"))
    decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
    return decimals - exponent


def numeric_match(
    tokens: list[str],
    expected: float,
    min_sf: int = 3,
    max_rel: float = 0.005,
) -> bool:
    """True when one written token is a valid match for the frozen value.

    A token passes only if it has at least ``min_sf`` significant figures,
    equals ``expected`` rounded to the token's own stated decimal places,
    keeps the same sign, and is within ``max_rel`` relative error. Plain and
    scientific notation are both accepted ("0.00255", "2.55e-3", "-0.446").
    """
    for token in tokens:
        text = _clean_token(token)
        if not _NUMBER_RE.match(text):
            continue
        value = float(text)
        if _significant_figures(text) < min_sf:
            continue
        if round(expected, _decimal_places(text)) != value:
            continue
        if expected > 0 and value <= 0:
            continue
        if expected < 0 and value >= 0:
            continue
        if expected == 0 and value != 0:
            continue
        if expected != 0 and abs(value - expected) / abs(expected) > max_rel:
            continue
        return True
    return False


_POSITIVE_RE = re.compile(r"\bpositive\b", re.IGNORECASE)
_NEGATIVE_RE = re.compile(r"\bnegative\b", re.IGNORECASE)


def sign_word(text: str) -> str | None:
    """Return "positive", "negative", "conflict" (both words), or None."""
    text = text or ""
    positive = bool(_POSITIVE_RE.search(text))
    negative = bool(_NEGATIVE_RE.search(text))
    if positive and negative:
        return "conflict"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return None


def routing_ok(agents_called: list[str], expected: list[str]) -> bool:
    """True when the called agents equal the expected set (both empty is valid)."""
    return set(agents_called or ()) == set(expected or ())


def _evidence_value(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _arg_equal(key: str, actual, expected) -> bool:
    if key.lower() in _CASE_INSENSITIVE_ARGS:
        return str(actual).strip().lower() == str(expected).strip().lower()
    return actual == expected


def args_match(evidence: list[dict], tool: str, expected_args: dict) -> bool:
    """True if any evidence item for ``tool`` matches every expected argument.

    Extra arguments in the evidence are ignored. Model, metric, event and
    component values compare case-insensitively; everything else (notably ISO
    dates and counts) must match exactly.
    """
    for item in evidence or ():
        if _evidence_value(item, "tool") != tool:
            continue
        args = _evidence_value(item, "args") or {}
        if all(
            key in args and _arg_equal(key, args[key], want)
            for key, want in (expected_args or {}).items()
        ):
            return True
    return False
