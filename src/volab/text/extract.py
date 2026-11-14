"""Author-owned document text extraction interfaces."""

from pathlib import Path
from typing import Any


def extract_document(source_path: Path, *, event_date: str, doc_type: str) -> dict[str, Any]:
    """Extract a source document into the fixed Step 10b record schema."""
    raise NotImplementedError("author")
