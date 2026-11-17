"""Author-owned consumers for the Step 10c Kafka document pipeline."""

from __future__ import annotations

import io
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from volab.data import REPO_ROOT
from volab.text import chunk, index, opensearch_index
from volab.text import kafka_io
from volab.text.kafka_io import DocumentMessage, publish_document

LAKE_ROOT = REPO_ROOT / "data" / "lake"
PUBLISHER_STATE = LAKE_ROOT / "publisher_state.json"
INDEXER_STATE = LAKE_ROOT / "indexer_state.json"
COUNTERS_PATH = REPO_ROOT / "results" / "text" / "10c_kafka.json"
BUCKET_ENV = "VFL_LAKE_BUCKET"
DOCUMENT_TYPES = frozenset({"statement", "minutes"})
CHUNK_COLUMNS = [
    "chunk_id", "doc_id", "doc_type", "event_date", "ord",
    "page", "text", "n_tokens", "content_hash",
]


def _bucket(value: str | None) -> str:
    bucket = value or os.environ.get(BUCKET_ENV)
    if not bucket:
        raise ValueError(f"S3 bucket is required; pass bucket= or set {BUCKET_ENV}")
    return bucket


def _s3_client(value=None):
    if value is not None:
        return value
    import boto3

    return boto3.client("s3")


def _compact_date(value) -> str:
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y%m%d")
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise ValueError(f"invalid event date {value!r}")


def _iso_date(value) -> str:
    return datetime.strptime(_compact_date(value), "%Y%m%d").date().isoformat()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"state file must contain a JSON object: {path}")
    return data


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def write_counters(name: str, counts: dict[str, int], *, path: Path = COUNTERS_PATH) -> None:
    """Append one run of counters for a consumer role.  # Codex fix (10c review, 16 Nov)"""
    all_counts = _load_state(path)
    role_runs = all_counts.get(name, {})
    if role_runs and not all(isinstance(value, dict) for value in role_runs.values()):
        role_runs = {"legacy": role_runs}
    role_runs[datetime.now(timezone.utc).isoformat()] = {key: int(value) for key, value in counts.items()}  # Codex fix (10c review, 16 Nov)
    all_counts[name] = role_runs
    _save_state(path, all_counts)


def _head_metadata(s3, bucket: str, key: str) -> dict[str, str]:
    try:
        response = s3.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return {}
        raise
    metadata = response.get("Metadata") or {}
    return {str(key).lower(): str(value) for key, value in metadata.items()}


def uploader(
    record: DocumentMessage,
    *,
    s3=None,
    bucket: str | None = None,
) -> str:
    """Upload a docs.raw event to raw/{doc_type}/{YYYYMMDD}.{ext}, deduplicating by sha256."""
    if record.event != kafka_io.RAW_EVENT:
        raise ValueError("uploader requires a raw event")
    if not record.path:
        raise ValueError("raw event has no path")
    s3 = _s3_client(s3)
    bucket = _bucket(bucket)
    body = Path(record.path).read_bytes()
    if hashlib.sha256(body).hexdigest() != record.sha256:
        raise ValueError(f"sha256 mismatch for {record.doc_id}")
    extension = "html" if record.doc_type == "statement" else "pdf"
    key = f"raw/{record.doc_type}/{_compact_date(record.event_date)}.{extension}"
    existing = _head_metadata(s3, bucket, key)
    if existing.get("sha256") == record.sha256:
        return "duplicate"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        Metadata={"sha256": record.sha256, "url": record.url or ""},
    )
    return "processed"


def _list_clean_objects(s3, bucket: str):
    kwargs = {"Bucket": bucket, "Prefix": "clean/"}
    while True:
        response = s3.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            yield item
        if not response.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = response["NextContinuationToken"]


def clean_publisher(
    *,
    s3=None,
    bucket: str | None = None,
    producer=None,
    state_path: Path = PUBLISHER_STATE,
    topic: str = "docs.clean",
) -> dict[str, int]:
    """Publish clean/ objects and checkpoint their VersionId/ETag after each produce."""
    s3 = _s3_client(s3)
    bucket = _bucket(bucket)
    producer = producer or kafka_io.make_producer()
    state = _load_state(state_path)
    counts = {"processed": 0, "duplicates_suppressed": 0, "dlq": 0}
    for item in _list_clean_objects(s3, bucket):
        key = str(item["Key"])
        if not key or key.endswith("/"):
            continue
        etag = item.get("ETag")
        previous = state.get(key)
        if isinstance(previous, dict) and previous.get("etag") == etag:
            counts["duplicates_suppressed"] += 1
            continue
        head = s3.head_object(Bucket=bucket, Key=key)
        metadata = {
            str(name).lower(): str(value)
            for name, value in (head.get("Metadata") or {}).items()
        }
        missing = {"doc-id", "sha256", "doc-type", "event-date"} - set(metadata)
        if missing:
            raise ValueError(f"{key} is missing metadata: {sorted(missing)}")
        record = DocumentMessage(
            doc_id=metadata["doc-id"],
            doc_type=metadata["doc-type"],
            event_date=metadata["event-date"],
            sha256=metadata["sha256"],
            path=None,
            s3_key=key,
            url=metadata.get("url"),
            event=kafka_io.CLEAN_EVENT,
        )
        publish_document(record, producer=producer, topic=topic)
        state[key] = {"etag": etag, "version_id": head.get("VersionId")}
        _save_state(state_path, state)
        counts["processed"] += 1
    return counts


def _parquet_bytes(s3, bucket: str, key: str) -> bytes:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    data = body.read() if hasattr(body, "read") else body
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError(f"object body for {key} is not bytes")
    return bytes(data)


def _as_list(value) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, float) and pd.isna(value):
        return []
    return list(value)


def _paragraphs(row: pd.Series) -> list[dict[str, Any]]:
    texts = _as_list(row.get("paragraph_texts"))
    pages = _as_list(row.get("paragraph_pages"))
    if len(texts) != len(pages):
        raise ValueError("paragraph_texts and paragraph_pages must have equal length")
    paragraphs = []
    for text, page in zip(texts, pages):
        paragraphs.append({
            "text": str(text),
            "page": None if page is None or pd.isna(page) else int(page),
        })
    return paragraphs


def indexer(
    record: DocumentMessage,
    *,
    s3=None,
    bucket: str | None = None,
    qdrant=None,
    opensearch_client=None,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    state_path: Path = INDEXER_STATE,
    cache_path: Path = index.EMBEDDINGS_PATH,
    collection: str = index.COLLECTION,
    dense_dim: int = index.EMBEDDING_DIM,
) -> str:
    """Rebuild chunks from a clean parquet object and upsert them idempotently."""
    if record.event != kafka_io.CLEAN_EVENT:
        raise ValueError("indexer requires a clean event")
    state = _load_state(state_path)
    if state.get(record.doc_id) == record.sha256:
        return "duplicate"

    s3 = _s3_client(s3)
    bucket = _bucket(bucket)
    frame = pd.read_parquet(io.BytesIO(_parquet_bytes(s3, bucket, str(record.s3_key))))
    if frame.empty:
        raise ValueError(f"clean object {record.s3_key} has no row")
    row = frame.iloc[0]
    document = {
        "doc_id": record.doc_id,
        "doc_type": record.doc_type,
        "event_date": pd.to_datetime(row.get("event_date", record.event_date)).date(),
        "paragraphs": _paragraphs(row),
    }
    chunks = pd.DataFrame(chunk.chunk_document(document), columns=CHUNK_COLUMNS)
    documents = pd.DataFrame([{
        "doc_id": record.doc_id,
        "url": str(row.get("url", "") or ""),
    }])
    index.index_chunks(
        chunks,
        documents,
        client=qdrant,
        embedder=embedder,
        cache_path=cache_path,
        collection=collection,
        dense_dim=dense_dim,
        delete_stale="doc",
    )
    if opensearch_client is not None:
        opensearch_index.index_chunks_os(opensearch_client, chunks)
    state[record.doc_id] = record.sha256
    _save_state(state_path, state)
    return "processed"


def run_consumer(
    topic: str,
    group_id: str,
    handler,
    *,
    consumer=None,
    producer=None,
    max_messages: int | None = None,
    counters_name: str | None = None,
) -> dict[str, int]:
    """Run kafka_io.consume and persist its counters."""
    counts = kafka_io.consume(
        topic,
        group_id,
        handler,
        consumer=consumer,
        producer=producer,
        max_messages=max_messages,
    )
    write_counters(counters_name or topic, counts)
    return counts

