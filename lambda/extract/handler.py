"""Author-owned AWS Lambda handler for raw FOMC documents."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import unquote_plus

import pyarrow as pa
import pyarrow.parquet as pq

from volab.text.extract import EXTRACTOR_VERSION, extract_document

RAW_KEY = re.compile(r"^raw/(statement|minutes)/(\d{8})\.(html|pdf)$")

SCHEMA = pa.schema([
    ("doc_id", pa.string()),
    ("event_date", pa.date32()),
    ("url", pa.string()),
    ("sha256", pa.string()),
    ("text", pa.string()),
    ("n_chars", pa.int32()),
    ("page_count", pa.int32()),
    ("extractor_version", pa.string()),
    ("extracted_at", pa.timestamp("ms")),
    ("votes_against", pa.list_(pa.string())),
    ("vote_tally", pa.string()),
    ("blocks_dropped", pa.int32()),
    ("paragraph_texts", pa.list_(pa.string())),
    ("paragraph_pages", pa.list_(pa.int32())),
])


def _s3_client():
    """Return the boto3 S3 client (monkeypatched by tests)."""
    import boto3

    return boto3.client("s3")


def _event_time(record: dict[str, Any], fallback) -> datetime:
    value = record.get("eventTime") or fallback
    if value is None:
        return datetime(1970, 1, 1)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _paragraph_arrays(record: dict[str, Any]) -> tuple[list[str], list[int | None]]:
    paragraphs = record.get("paragraphs") or []
    texts = [str(paragraph["text"]) for paragraph in paragraphs]
    supplied_pages = record.get("paragraph_pages")
    if supplied_pages is not None:
        pages = list(supplied_pages)
    else:
        pages = [paragraph.get("page") for paragraph in paragraphs]
    if len(texts) != len(pages):
        raise ValueError("paragraph_texts and paragraph_pages must have equal length")
    return texts, [None if page is None else int(page) for page in pages]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process raw/ statement and minutes objects into one clean parquet each."""
    s3 = _s3_client()
    processed = 0
    errors: list[dict[str, str]] = []

    for record in event.get("Records", []):
        key = unquote_plus(record["s3"]["object"]["key"])
        match = RAW_KEY.fullmatch(key)
        if match is None:
            continue

        try:
            started = time.perf_counter()
            bucket = record["s3"]["bucket"]["name"]
            doc_type, compact, extension = match.groups()
            obj = s3.get_object(Bucket=bucket, Key=key)
            body_obj = obj["Body"]
            body = body_obj.read() if hasattr(body_obj, "read") else body_obj
            meta = obj.get("Metadata", {})

            fd, path = tempfile.mkstemp(suffix=f".{extension}")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(body)
                extracted = extract_document(
                    path,
                    event_date=compact,
                    doc_type=doc_type,
                    url=meta.get("url", ""),
                    sha256=meta["sha256"],
                )
            finally:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

            paragraph_texts, paragraph_pages = _paragraph_arrays(extracted)
            event_date = _as_date(extracted["event_date"])
            event_date_iso = event_date.isoformat()
            doc_id = f"{doc_type}-{compact}"
            row = {
                "doc_id": doc_id,
                "event_date": event_date,
                "url": str(extracted.get("url", "") or ""),
                "sha256": str(extracted.get("sha256", meta["sha256"])),
                "text": str(extracted["text"]),
                "n_chars": int(extracted["n_chars"]),
                "page_count": None if extracted.get("page_count") is None else int(extracted["page_count"]),
                "extractor_version": str(extracted.get("extractor_version", EXTRACTOR_VERSION)),
                "extracted_at": _event_time(record, extracted.get("extracted_at")),
                "votes_against": list(extracted.get("votes_against") or []),
                "vote_tally": None if extracted.get("vote_tally") is None else str(extracted["vote_tally"]),
                "blocks_dropped": int(extracted.get("blocks_dropped", 0)),
                "paragraph_texts": paragraph_texts,
                "paragraph_pages": paragraph_pages,
            }
            table = pa.Table.from_pylist([row], schema=SCHEMA)
            buffer = io.BytesIO()
            pq.write_table(table, buffer)
            clean_key = f"clean/year={event_date.year}/doc_type={doc_type}/{doc_id}.parquet"
            s3.put_object(
                Bucket=bucket,
                Key=clean_key,
                Body=buffer.getvalue(),
                Metadata={
                    "doc-id": doc_id,
                    "sha256": str(row["sha256"]),
                    "doc-type": doc_type,
                    "event-date": event_date_iso,
                    "extractor-version": row["extractor_version"],
                },
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            print(json.dumps({
                "doc_id": doc_id,
                "n_chars": row["n_chars"],
                "ms": elapsed_ms,
            }))
            processed += 1
        except Exception as exc:
            print(json.dumps({
                "level": "ERROR",
                "key": key,
                "error": str(exc),
            }))
            errors.append({"key": key, "error": str(exc)})

    return {"processed": processed, "errors": errors}
