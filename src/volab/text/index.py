"""Author-owned Qdrant hybrid index and statement lookup for Step 10b."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
import zlib
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
from qdrant_client import QdrantClient, models

from volab.data import REPO_ROOT

COLLECTION = "fomc_chunks"
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
DENSE_LIMIT = 50
SPARSE_LIMIT = 50
BATCH_SIZE = 256
UPSERT_BATCH = 256
DOCUMENTS_PATH = REPO_ROOT / "data" / "text" / "documents.parquet"
EMBEDDINGS_PATH = REPO_ROOT / "data" / "text" / "embeddings.parquet"
POINT_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
HEADER_VERSION = "1"

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are aren't as at be because been before
    being below between both but by can't cannot could couldn't did didn't do does doesn't doing
    don't down during each few for from further had hadn't has hasn't have haven't having he he'd
    he'll he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in
    into is isn't it it's its itself let's me more most mustn't my myself no nor not of off on once
    only or other ought our ours ourselves out over own same shan't she she'd she'll she's should
    shouldn't so some such than that that's the their theirs them themselves then there there's
    these they they'd they'll they're they've this those through to too under until up very was
    wasn't we we'd we'll we're we've were weren't what what's when when's where where's which while
    who who's whom why why's with won't would wouldn't you you'd you'll you're you've your yours
    yourself yourselves
    """.split()
)


class EmbeddingError(RuntimeError):
    """The embedding backend did not return usable vectors."""


def _client(client: QdrantClient | None) -> QdrantClient:
    if client is not None:
        return client
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))


def _yyyymmdd(value) -> int:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return int(value.strftime("%Y%m%d"))
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return int(datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).strftime("%Y%m%d"))
        except ValueError:
            continue
    raise ValueError(f"invalid event date {value!r}")


def _iso_date(value) -> str:
    return datetime.strptime(str(_yyyymmdd(value)), "%Y%m%d").date().isoformat()


def context_header(doc_type: str, event_date) -> str:
    """Return the contextual heading embedded with each chunk."""
    parsed = date.fromisoformat(_iso_date(event_date))
    formatted = f"{parsed.day} {parsed:%B %Y}"
    if doc_type == "statement":
        return f"FOMC statement, {formatted}"
    if doc_type == "minutes":
        return f"FOMC minutes, meeting of {formatted}"
    raise ValueError(f"unsupported doc_type for context header: {doc_type!r}")


def index_text(row) -> str:
    """Text sent to the embedder, with its contextual header."""
    return f"{context_header(row.doc_type, row.event_date)}\n\n{row.text}"


def index_hash(row) -> str:
    """Cache and change-detection key covering header, text, version and chunk hash."""
    payload = f"{index_text(row)}|{HEADER_VERSION}|{row.content_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_dates(date_from: str | None, date_to: str | None) -> tuple[int | None, int | None]:
    def _parse(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d"))
        except (TypeError, ValueError) as exc:
            raise ValueError("dates must use YYYY-MM-DD") from exc

    start = _parse(date_from)
    end = _parse(date_to)
    if start is not None and end is not None and start > end:
        raise ValueError("date_from must not be after date_to")
    return start, end


def sparse_vector(text: str) -> models.SparseVector:
    counts: dict[int, int] = {}
    for token in re.findall(r"\w+", text.lower()):
        if token in STOPWORDS:
            continue
        index = zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF
        counts[index] = counts.get(index, 0) + 1
    indices = sorted(counts)
    return models.SparseVector(
        indices=indices,
        values=[float(counts[index]) for index in indices],
    )


def _load_cache(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=["content_hash", "model", "embedding"])
    frame = pd.read_parquet(path)
    missing = {"content_hash", "model", "embedding"} - set(frame.columns)
    if missing:
        raise ValueError(f"embeddings cache is missing columns: {sorted(missing)}")
    return frame


def _save_cache(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def openai_embed(texts: Iterable[str]) -> list[list[float]]:
    """Embed texts with text-embedding-3-small; injectable for tests/offline runs."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=list(texts))
    vectors = [list(item.embedding) for item in response.data]
    if any(len(vector) != EMBEDDING_DIM for vector in vectors):
        raise EmbeddingError("embedding dimension is not 1536")
    openai_embed.last_total_tokens = int(getattr(response.usage, "total_tokens", 0))
    return vectors


def _embed_texts(
    items: list[tuple[str, str]],
    *,
    embedder: Callable[[list[str]], list[list[float]]],
    cache_path: Path,
) -> tuple[dict[str, list[float]], int]:
    cache = _load_cache(cache_path)
    known = {
        row.content_hash: list(row.embedding)
        for row in cache[cache["model"] == EMBEDDING_MODEL].itertuples(index=False)
    }
    unique: dict[str, str] = {}
    for content_hash, text in items:
        unique.setdefault(str(content_hash), text)
    missing = [
        (content_hash, text)
        for content_hash, text in unique.items()
        if content_hash not in known
    ]
    total_tokens = 0
    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start:start + BATCH_SIZE]
        vectors = embedder([text for _, text in batch])
        if len(vectors) != len(batch):
            raise EmbeddingError("embedder returned the wrong number of vectors")
        total_tokens += int(getattr(embedder, "last_total_tokens", 0))
        additions = [
            {
                "content_hash": content_hash,
                "model": EMBEDDING_MODEL,
                "embedding": vector,
            }
            for (content_hash, _), vector in zip(batch, vectors)
        ]
        cache = pd.concat([cache, pd.DataFrame(additions)], ignore_index=True)
        _save_cache(cache, cache_path)
        for row in additions:
            known[row["content_hash"]] = row["embedding"]
    return known, total_tokens


def ensure_collection(
    client: QdrantClient,
    collection: str = COLLECTION,
    *,
    dense_dim: int = EMBEDDING_DIM,
) -> None:
    if client.collection_exists(collection):
        return
    client.create_collection(
        collection_name=collection,
        vectors_config={
            DENSE_VECTOR: models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={
            SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    client.create_payload_index(
        collection_name=collection,
        field_name="event_date",
        field_schema=models.PayloadSchemaType.INTEGER,
    )
    client.create_payload_index(
        collection_name=collection,
        field_name="doc_type",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )


def _existing(client: QdrantClient, collection: str) -> dict[str, tuple[str, str]]:
    points: dict[str, tuple[str, str]] = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            points[str(record.id)] = (
                str(payload.get("chunk_id", "")),
                str(payload.get("content_hash", "")),
            )
        if offset is None:
            break
    return points


def _document_urls(documents) -> dict[str, str]:
    if isinstance(documents, pd.DataFrame):
        return {
            str(row.doc_id): str(row.url)
            for row in documents[["doc_id", "url"]].itertuples(index=False)
        }
    return {str(doc_id): str(url) for doc_id, url in dict(documents).items()}


def index_chunks(
    chunks,
    documents,
    *,
    client: QdrantClient | None = None,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    collection: str = COLLECTION,
    cache_path: Path = EMBEDDINGS_PATH,
    dense_dim: int = EMBEDDING_DIM,
) -> dict[str, int]:
    """Idempotently upsert chunks and delete points no longer present."""
    chunks_frame = chunks.copy() if isinstance(chunks, pd.DataFrame) else pd.DataFrame(chunks)
    required = {"chunk_id", "doc_id", "doc_type", "event_date", "page", "text", "content_hash"}
    missing = required - set(chunks_frame.columns)
    if missing:
        raise ValueError(f"chunks is missing columns: {sorted(missing)}")
    urls = _document_urls(documents)

    qdrant = _client(client)
    ensure_collection(qdrant, collection, dense_dim=dense_dim)
    existing = _existing(qdrant, collection)

    current_chunk_ids = set(chunks_frame["chunk_id"])
    to_upsert = []
    unchanged = 0
    for row in chunks_frame.itertuples(index=False):
        point_id = str(uuid.uuid5(POINT_NAMESPACE, str(row.chunk_id)))
        previous = existing.get(point_id)
        if previous is not None and previous[0] == str(row.chunk_id) and previous[1] == index_hash(row):
            unchanged += 1
            continue
        to_upsert.append((point_id, row))

    if to_upsert:
        vectors, total_tokens = _embed_texts(
            [(index_hash(row), index_text(row)) for _, row in to_upsert],
            embedder=embedder or openai_embed,
            cache_path=cache_path,
        )
    else:
        vectors, total_tokens = {}, 0

    points = []
    for point_id, row in to_upsert:
        key = index_hash(row)
        points.append(
            models.PointStruct(
                id=point_id,
                vector={
                    DENSE_VECTOR: vectors[key],
                    SPARSE_VECTOR: sparse_vector(index_text(row)),
                },
                payload={
                    "chunk_id": str(row.chunk_id),
                    "doc_id": str(row.doc_id),
                    "doc_type": str(row.doc_type),
                    "event_date": _yyyymmdd(row.event_date),
                    "url": urls.get(str(row.doc_id), ""),
                    "page": None if pd.isna(row.page) else int(row.page),
                    "content_hash": key,
                    "chunk_content_hash": str(row.content_hash),
                    "text": str(row.text),
                },
            )
        )
    if points:
        for start in range(0, len(points), UPSERT_BATCH):
            qdrant.upsert(
                collection_name=collection,
                points=points[start:start + UPSERT_BATCH],
                wait=True,
            )

    stale = [
        point_id
        for point_id, (chunk_id, _) in existing.items()
        if chunk_id not in current_chunk_ids
    ]
    if stale:
        qdrant.delete(collection_name=collection, points_selector=stale, wait=True)

    return {
        "upserted": len(points),
        "unchanged": unchanged,
        "deleted": len(stale),
        "total": len(chunks_frame),
        "tokens": int(total_tokens),
    }


def _filter(date_from: int | None, date_to: int | None, doc_type: str | None) -> models.Filter | None:
    conditions = []
    if date_from is not None or date_to is not None:
        conditions.append(
            models.FieldCondition(
                key="event_date",
                range=models.Range(gte=date_from, lte=date_to),
            )
        )
    if doc_type is not None:
        if doc_type not in {"statement", "minutes"}:
            raise ValueError("doc_type must be statement or minutes")
        conditions.append(
            models.FieldCondition(key="doc_type", match=models.MatchValue(value=doc_type))
        )
    return models.Filter(must=conditions) if conditions else None


def _result(point) -> dict[str, Any]:
    payload = point.payload or {}
    return {
        "chunk_id": payload.get("chunk_id"),
        "doc_id": payload.get("doc_id"),
        "doc_type": payload.get("doc_type"),
        "event_date": _iso_date(payload.get("event_date")),
        "url": payload.get("url"),
        "page": payload.get("page"),
        "text": payload.get("text"),
        "score": float(point.score),
    }


def search(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    doc_type: str | None = None,
    k: int = 5,
    mode: str = "hybrid",
    client: QdrantClient | None = None,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    collection: str = COLLECTION,
) -> list[dict[str, Any]]:
    """Hybrid (RRF), dense or sparse search with optional metadata filters."""
    if not query or not query.strip():
        raise ValueError("query must be non-empty")
    if mode not in {"hybrid", "dense", "sparse"}:
        raise ValueError("mode must be hybrid, dense or sparse")
    if k < 1:
        raise ValueError("k must be positive")
    start, end = validate_dates(date_from, date_to)
    qdrant = _client(client)
    query_filter = _filter(start, end, doc_type)

    sparse_query = sparse_vector(query)
    if mode == "sparse":
        if not sparse_query.indices:
            return []
        response = qdrant.query_points(
            collection_name=collection,
            query=sparse_query,
            using=SPARSE_VECTOR,
            query_filter=query_filter,
            limit=k,
            with_payload=True,
        )
        return [_result(point) for point in response.points]

    vector = (embedder or openai_embed)([query])[0]
    if mode == "dense" or not sparse_query.indices:
        response = qdrant.query_points(
            collection_name=collection,
            query=vector,
            using=DENSE_VECTOR,
            query_filter=query_filter,
            limit=k,
            with_payload=True,
        )
        return [_result(point) for point in response.points]

    response = qdrant.query_points(
        collection_name=collection,
        prefetch=[
            models.Prefetch(
                query=vector,
                using=DENSE_VECTOR,
                filter=query_filter,
                limit=DENSE_LIMIT,
            ),
            models.Prefetch(
                query=sparse_query,
                using=SPARSE_VECTOR,
                filter=query_filter,
                limit=SPARSE_LIMIT,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=k,
        with_payload=True,
    )
    return [_result(point) for point in response.points]


def get_statement(date: str, *, documents_path: Path = DOCUMENTS_PATH) -> dict[str, Any]:
    """Read a statement from documents.parquet; missing dates raise KeyError."""
    try:
        compact = int(datetime.strptime(date, "%Y-%m-%d").strftime("%Y%m%d"))
    except (TypeError, ValueError) as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc
    documents = pd.read_parquet(documents_path)
    selected = documents[
        (documents["doc_type"] == "statement")
        & (pd.to_datetime(documents["event_date"]).dt.strftime("%Y%m%d").astype(int) == compact)
    ]
    if selected.empty:
        raise KeyError(date)
    row = selected.iloc[0]
    return {
        "doc_id": str(row["doc_id"]),
        "url": str(row["url"]),
        "sha256": str(row["sha256"]),
        "text": str(row["text"]),
        "votes_for": list(row["votes_for"]),
        "votes_against": list(row["votes_against"]),
        "dissent_text": row["dissent_text"],
        "vote_tally": row["vote_tally"] if "vote_tally" in selected.columns else None,
    }
