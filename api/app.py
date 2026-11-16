"""Read-only API routes for document search and cached operational metrics."""

import json
import logging
import os
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from volab.text import cache, index

VERSION = "0.1.0"
app = FastAPI(title="vol-forecast-lab document API", version=VERSION)

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
FORECASTS_PATH = RESULTS_DIR / "forecasts.parquet"
MLP_FORECASTS_PATH = RESULTS_DIR / "mlp_forecasts.parquet"
LOSSES_PATH = RESULTS_DIR / "losses.parquet"
EMPTY_CACHE_METRICS = {"hits": 0, "misses": 0, "hit_rate": 0.0, "rate_limited": 0}
logger = logging.getLogger(__name__)


def get_clock():
    """Return the clock used by the rate limiter (overridden in tests for determinism)."""
    return time.time


def get_redis_client():
    """Return the configured Redis client, or None when caching is disabled."""
    return _redis_client_for_url(os.getenv("REDIS_URL"))


@lru_cache(maxsize=4)
def _redis_client_for_url(redis_url: str | None):
    if not redis_url:
        return None
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    # Short timeouts and one immediate retry so a Redis outage costs milliseconds, not seconds (fail open).
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
        retry=Retry(NoBackoff(), 1),
    )


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
def metrics(redis_client=Depends(get_redis_client)) -> dict[str, Any]:
    """Return cache counters from the cache backend implementation."""
    if redis_client is None:
        return dict(EMPTY_CACHE_METRICS)
    try:
        return cache.get_metrics(redis_client)
    except RedisError as exc:
        logger.warning("Redis unavailable for /metrics: %s", exc)
        return {**EMPTY_CACHE_METRICS, "redis_error": True}


@app.get("/search", response_model=SearchResponse)
def search(
    request: Request,
    q: str = Query(min_length=1),
    date_from: str | None = None,
    date_to: str | None = None,
    doc_type: str | None = None,
    k: int = Query(default=5, ge=1, le=100),
    redis_client=Depends(get_redis_client),
    clock=Depends(get_clock),
) -> SearchResponse:
    """Search document index with optional date and type filters."""
    key = cache.make_key(q, date_from, date_to, doc_type, k, "hybrid")
    if redis_client is not None:
        # Fail open: a Redis outage disables limiting and caching, never search itself.
        try:
            allowed = cache.RateLimiter(redis_client).allow(request.client.host, now=clock())
            cached = cache.SearchCache(redis_client).get(key) if allowed else None
        except RedisError as exc:
            logger.warning("Redis unavailable, serving /search uncached: %s", exc)
            redis_client, allowed, cached = None, True, None
        if not allowed:
            raise HTTPException(status_code=429, detail="rate limited")
        if cached is not None:
            return SearchResponse.model_validate(cached)
    try:
        results = index.search(q, date_from=date_from, date_to=date_to, doc_type=doc_type, k=k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = SearchResponse(query=q, results=results)
    if redis_client is not None:
        try:
            cache.SearchCache(redis_client).set(key, response.model_dump())
        except RedisError as exc:
            logger.warning("Redis unavailable, response not cached: %s", exc)
    return response


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


@app.get("/forecast", response_model=dict[str, Any])
def forecast(
    as_of: date,
    model: Literal["M0", "M1", "M2", "M3", "M4"],
) -> dict[str, Any]:
    """Return stored forecast rows and their source citation."""
    import pandas as pd

    source_path = MLP_FORECASTS_PATH if model == "M4" else FORECASTS_PATH
    frame = pd.read_parquet(source_path)
    row_date = pd.to_datetime(frame["date"]).dt.date
    mask = row_date == as_of
    if "model" in frame.columns:
        mask &= frame["model"] == model
    selected = frame.loc[mask]
    if selected.empty:
        raise HTTPException(status_code=404, detail=f"No forecast for {model} on {as_of.isoformat()}")
    records = json.loads(selected.to_json(orient="records", date_format="iso"))
    return {"source_file": source_path.name, "date": as_of.isoformat(), "model": model, "rows": records}


@app.get("/compare", response_model=dict[str, Any])
def compare(
    start: date,
    end: date,
    metric: Literal["qlike", "squared_error"],
) -> dict[str, Any]:
    """Return stored per-model mean losses over an inclusive date window."""
    import pandas as pd

    if start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    frame = pd.read_parquet(LOSSES_PATH)
    row_dates = pd.to_datetime(frame["date"]).dt.date
    selected = frame.loc[(row_dates >= start) & (row_dates <= end), ["model", metric]]
    if selected.empty:
        raise HTTPException(status_code=404, detail="No loss rows in the requested window")
    grouped = selected.groupby("model")[metric].agg(["mean", "count"]).reset_index()
    results = [
        {"model": str(row.model), "mean": float(row["mean"]), "n": int(row["count"])}
        for _, row in grouped.iterrows()
    ]
    return {"metric": metric, "start": start.isoformat(), "end": end.isoformat(), "results": results}
