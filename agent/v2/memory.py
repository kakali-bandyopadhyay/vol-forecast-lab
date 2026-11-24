"""Preference memory for the v2 supervisor: parse, validate, load, merge, context.

Pure helpers with no LLM calls. The only side effect is the explicit preference
read/write against the LangGraph store, and only for keys/values in ``ALLOWED``.
Kakali hand-codes this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langgraph.store.base import BaseStore

ALLOWED: dict[str, set[str]] = {
    "default_model": {"M0", "M1", "M2", "M3", "M4"},
    "default_metric": {"qlike", "squared_error"},
}

Scope = Literal["thread", "user"]


@dataclass(frozen=True)
class PrefWrite:
    """One explicit preference statement, already normalised."""

    scope: Scope
    key: str
    value: str


_MODEL_RE = re.compile(r"\bM([0-4])\b", re.IGNORECASE)
_METRIC_RE = re.compile(r"\bqlike\b|\bsquared[\s_-]*error\b|\bmse\b", re.IGNORECASE)
_DEFAULT_KEY_RE = re.compile(
    r"\bdefault\s+(model|metric)\b|\b(model|metric)\s+default\b", re.IGNORECASE
)
_THREAD_SCOPE_RE = re.compile(r"\b(?:for|in)\s+this\s+thread\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
_NEGATION_RE = re.compile(r"\bnot\b|\bcannot\b|n['’]t\b|\bnever\b", re.IGNORECASE)
_WRITE_CUE_RE = re.compile(
    r"\bremember\b|\bset\b|\bmake\b|\bsave\b"
    r"|\buse\b[^.!?]*?\bas\b"
    r"|\bchange\b[^.!?]*?\bto\b"
    r"|\bswitch\b[^.!?]*?\bto\b"
    r"|\bdefault\s+(?:model|metric)\s+is\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    """Split on . ? ! while keeping the end mark, so a question stays a question."""
    return [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()]


def _model_values(text: str) -> list[tuple[str, int]]:
    return [(f"M{match.group(1)}", match.start()) for match in _MODEL_RE.finditer(text)]


def _metric_values(text: str) -> list[tuple[str, int]]:
    values: list[tuple[str, int]] = []
    for match in _METRIC_RE.finditer(text):
        token = match.group(0).lower()
        value = "qlike" if token == "qlike" else "squared_error"
        values.append((value, match.start()))
    return values


def parse_preference(question: str) -> PrefWrite | None:
    """Parse an explicit default-model/metric statement; otherwise return None.

    Only the sentence carrying the "default model/metric" phrase is considered,
    and it must be a statement with a write cue. A sentence that ends in "?",
    contains not / n't / never, or names more than one model/metric is not a
    write. Bare mentions ("What about M0?") and context requests ("remember the
    CPI ON estimate") return None. Thread scope is read from the whole question,
    so a later "use it for this thread" still keeps the write thread-scoped.
    """
    for sentence in _sentences(question):
        key_match = _DEFAULT_KEY_RE.search(sentence)
        if key_match is None:
            continue
        if sentence.endswith("?") or _NEGATION_RE.search(sentence):
            return None
        if _WRITE_CUE_RE.search(sentence) is None:
            return None
        key = "default_model" if "model" in key_match.group(0).lower() else "default_metric"
        candidates = _model_values(sentence) if key == "default_model" else _metric_values(sentence)
        values = {candidate for candidate, _ in candidates}
        if len(values) != 1:
            return None
        scope: Scope = "thread" if _THREAD_SCOPE_RE.search(question) else "user"
        return PrefWrite(scope=scope, key=key, value=values.pop())
    return None


def validate_pref(key, value) -> str | None:
    """Return None when key/value are storable, else a short rejection reason."""
    if key not in ALLOWED:
        return f"unknown preference key: {key!r}"
    if not isinstance(value, str) or value not in ALLOWED[key]:
        return f"invalid value for {key}: {value!r}"
    return None


def load_user_prefs(store: BaseStore, user_id: str) -> dict[str, str]:
    """Read each allowed key exactly, dropping any item that fails validation."""
    prefs: dict[str, str] = {}
    namespace = ("prefs", user_id)
    for key in ALLOWED:
        item = store.get(namespace, key)
        if item is None:
            continue
        payload = item.value if isinstance(item.value, dict) else None
        if payload is None:
            continue
        value = payload.get("value")
        if validate_pref(key, value) is None:
            prefs[key] = value
    return prefs


def save_user_pref(store: BaseStore, user_id: str, w: PrefWrite) -> dict:
    """Persist one explicit user-scope preference and return its audit record."""
    reason = validate_pref(w.key, w.value)
    if reason is not None:
        raise ValueError(reason)
    store.put(
        ("prefs", user_id),
        w.key,
        {
            "value": w.value,
            "set_at": datetime.now(timezone.utc).isoformat(),
            "source": "explicit",
        },
    )
    return {"scope": w.scope, "key": w.key, "value": w.value}


def merge_thread_prefs(thread_prefs, w: PrefWrite) -> dict[str, str]:
    """Return a new thread-prefs dict with the thread-scope write applied."""
    merged = {
        key: value
        for key, value in (thread_prefs or {}).items()
        if validate_pref(key, value) is None
    }
    if w.scope == "thread":
        merged[w.key] = w.value
    return merged


def _explicit_defaults(question: str) -> dict[str, str | None]:
    """Values stated outright in the question; None means 'stated but ambiguous'."""
    explicit: dict[str, str | None] = {}
    models = _model_values(question)
    if models:
        explicit["default_model"] = models[0][0] if len(models) == 1 else None
    metrics = _metric_values(question)
    if metrics:
        explicit["default_metric"] = metrics[0][0] if len(metrics) == 1 else None
    return explicit


def memory_context(question, turns, thread_prefs, user_prefs, window: int = 3) -> dict:
    """What the question rewriter may see: recent turns plus resolved defaults.

    Default precedence: an explicit mention in the current question beats a
    thread pref ("for this thread"), which beats the long-term user pref. If the
    question mentions several values for one key, that key is ambiguous and is
    omitted, so a stored default cannot leak into a comparison. Old turns carry
    only question/resolved/tool_args, never answer text or rows, so old numbers
    can never be reused as evidence.
    """
    recent = []
    if window > 0:
        for item in list(turns or [])[-window:]:
            if not isinstance(item, dict):
                continue
            recent.append(
                {
                    "turn": item.get("turn"),
                    "question": item.get("question"),
                    "resolved": item.get("resolved"),
                    "tool_args": item.get("tool_args", []),
                }
            )

    explicit = _explicit_defaults(question)
    defaults: dict[str, str] = {}
    for key in ALLOWED:
        if key in explicit:
            if explicit[key] is not None:
                defaults[key] = explicit[key]
            continue
        for source in (thread_prefs, user_prefs):
            value = (source or {}).get(key)
            if validate_pref(key, value) is None:
                defaults[key] = value
                break

    return {"turns": recent, "defaults": defaults}
