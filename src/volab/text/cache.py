"""Author-owned Redis cache interfaces."""

from typing import Any


def get_metrics() -> dict[str, Any]:
    """Return cache hit, miss, and rate-limit metrics for the API."""
    raise NotImplementedError("author")
