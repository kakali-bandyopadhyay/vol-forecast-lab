"""Read-only API routes for document search and cached operational metrics."""

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from volab.text import cache, index

VERSION = "0.1.0"
app = FastAPI(title="vol-forecast-lab document API", version=VERSION)


class SearchResponse(BaseModel):
    """Response containing query text and matching cited chunks."""

    query: str
    results: list[dict[str, Any]] = Field(default_factory=list)


class StatementResponse(BaseModel):
    """Response containing a statement lookup result."""

    date: str
    document: dict[str, Any]


class HealthResponse(BaseModel):
    """API process health and version."""

    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report that the API process is serving requests."""
    return HealthResponse(status="ok", version=VERSION)


@app.get("/metrics", response_model=dict[str, Any])
def metrics() -> dict[str, Any]:
    """Return cache counters from the cache backend implementation."""
    try:
        return cache.get_metrics()
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail="Metrics are unavailable until the cache stub is implemented.") from exc


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=1),
    date_from: str | None = None,
    date_to: str | None = None,
    doc_type: str | None = None,
    k: int = Query(default=5, ge=1, le=100),
) -> SearchResponse:
    """Search document index with optional date and type filters."""
    try:
        results = index.search(q, date_from=date_from, date_to=date_to, doc_type=doc_type, k=k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SearchResponse(query=q, results=results)


@app.get("/statement/{date}", response_model=StatementResponse)
def statement(date: str) -> StatementResponse:
    """Look up a statement by its event date."""
    try:
        document = index.get_statement(date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No statement for {date}") from exc
    return StatementResponse(date=date, document=document)
