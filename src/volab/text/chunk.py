"""Author-owned paragraph chunking interfaces."""

from typing import Any


def chunk_document(document: dict[str, Any], *, max_tokens: int = 300) -> list[dict[str, Any]]:
    """Split an extracted document into bounded paragraph chunks."""
    raise NotImplementedError("author")
