"""Author-owned retrieval and indexing interfaces."""

from typing import Any


def search(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    doc_type: str | None = None,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Search indexed document chunks with optional metadata filters."""
    raise NotImplementedError("author")


def get_statement(date: str) -> dict[str, Any]:
    """Return a statement and its citation metadata for an event date."""
    raise NotImplementedError("author")
