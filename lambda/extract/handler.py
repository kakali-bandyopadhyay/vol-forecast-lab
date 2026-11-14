"""Author-owned AWS Lambda extraction interface."""

from typing import Any


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle an S3 ObjectCreated event and write extracted document data."""
    raise NotImplementedError("author")
