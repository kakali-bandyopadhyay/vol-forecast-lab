"""Author-owned OpenSearch adapter for the Step 10c chunk index."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from volab.text import index as qdrant_index

INDEX = "fomc_chunks_v1"

MAPPING = {
    "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},  # Codex fix (10c review, 16 Nov)
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "event_date": {"type": "date"},
            "page": {"type": "integer"},
            "header": {"type": "text", "analyzer": "english"},
            "text": {"type": "text", "analyzer": "english"},
            "content_hash": {"type": "keyword"},
        }
    }
}


def ensure_index(client) -> None:
    """Create the chunk index with its explicit mapping if it does not exist."""
    if not client.indices.exists(index=INDEX):
        client.indices.create(index=INDEX, body=MAPPING)


def _iso_date(value) -> str:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).date().isoformat()
    return str(value)[:10]


def _document(row) -> dict[str, Any]:
    page = row.page
    return {
        "chunk_id": str(row.chunk_id),
        "doc_id": str(row.doc_id),
        "doc_type": str(row.doc_type),
        "event_date": _iso_date(row.event_date),
        "page": None if page is None or pd.isna(page) else int(page),
        "header": qdrant_index.context_header(str(row.doc_type), row.event_date),
        "text": str(row.text),
        "content_hash": str(row.content_hash),
    }


BULK_BATCH = 500


def index_chunks_os(client, chunks: pd.DataFrame, batch_size: int = BULK_BATCH) -> dict[str, int]:
    """Bulk-index chunks with _id = chunk_id in batches; raise on any failed item."""
    ensure_index(client)
    frame = chunks if isinstance(chunks, pd.DataFrame) else pd.DataFrame(chunks)
    rows = list(frame.itertuples(index=False))
    counts = {"created": 0, "updated": 0}
    for start in range(0, len(rows), batch_size):
        body: list[dict[str, Any]] = []
        for row in rows[start:start + batch_size]:
            body.append({"index": {"_index": INDEX, "_id": str(row.chunk_id)}})
            body.append(_document(row))
        last = start + batch_size >= len(rows)
        response = client.bulk(body=body, refresh="wait_for" if last else False)
        actions = [next(iter(item.values())) for item in response.get("items", [])]
        failed = [a for a in actions if a.get("error")]
        if response.get("errors") or failed:
            first = failed[0]["error"] if failed else "unknown"
            raise RuntimeError(f"bulk batch at row {start}: {len(failed)} failed; first error: {first}")
        for action in actions:
            result = action.get("result")
            if result not in counts:
                raise RuntimeError(f"unexpected bulk result {result!r} for {action.get('_id')}")
            counts[result] += 1
    return counts


def bm25_query(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    doc_type: str | None = None,
    size: int = 50,
) -> dict[str, Any]:
    """Build the BM25 query with header^2 and only the requested filters."""
    filters: list[dict[str, Any]] = []
    if date_from is not None or date_to is not None:
        bounds: dict[str, str] = {}
        if date_from is not None:
            bounds["gte"] = date_from
        if date_to is not None:
            bounds["lte"] = date_to
        filters.append({"range": {"event_date": bounds}})
    if doc_type is not None:
        filters.append({"term": {"doc_type": doc_type}})

    bool_query: dict[str, Any] = {
        "must": [{"multi_match": {"query": query, "fields": ["header^2", "text"]}}]
    }
    if filters:
        bool_query["filter"] = filters
    return {"size": size, "query": {"bool": bool_query}}


def search_bm25(client, query: str, **filters) -> list[str]:
    """Return chunk_ids in BM25 rank order."""
    response = client.search(index=INDEX, body=bm25_query(query, **filters))
    chunk_ids = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source") or {}
        chunk_ids.append(str(source.get("chunk_id", hit.get("_id", ""))))
    return chunk_ids


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion, rank starting at 1; ties by first appearance."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            if chunk_id not in first_seen:
                first_seen[chunk_id] = order
                order += 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))

