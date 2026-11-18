"""Publish document registration events to SNS or directly to the registry queue."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import uuid


DEFAULT_DOCUMENTS = Path("data/text/documents.parquet")
MANIFEST_DIR = Path("results/eventdriven")


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _utc_timestamp(value: str | datetime | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_event(
    row: dict[str, Any], *, schema_version: str = "1", event_id: str | None = None,
    published_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Build one v1 registration event from a document row."""
    return {
        "schema_version": str(schema_version),
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "document.registered",
        "doc_id": str(row["doc_id"]),
        "sha256": str(row["sha256"]),
        "doc_type": str(row["doc_type"]),
        "event_date": _iso_date(row["event_date"]),
        "n_chars": int(row["n_chars"]),
        "extractor_version": str(row["extractor_version"]),
        "published_at": _utc_timestamp(published_at),
    }


def build_poison_event(row: dict[str, Any]) -> dict[str, Any]:
    """Build the deterministic unsupported-v2 test event for a document."""
    changed_hash = hashlib.sha256(f"{row['sha256']}:v2-test".encode("utf-8")).hexdigest()
    poison_row = dict(row, sha256=changed_hash, extractor_version=f"{row['extractor_version']}+v2-test")
    return build_event(poison_row, schema_version="2")


def _load_rows(path: Path, limit: int | None) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    rows = table.to_pylist()
    rows.sort(key=lambda row: str(row["doc_id"]))
    return rows[:limit] if limit is not None else rows


def _manifest_row(event: dict[str, Any], status: str, channel: str) -> dict[str, Any]:
    return {key: event[key] for key in ("doc_id", "event_id", "sha256", "schema_version", "published_at")} | {
        "status": status,
        "channel": channel,
    }


def _write_manifest(phase: str, records: list[dict[str, Any]]) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / f"published_{phase}.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
    return path


def _publish_sns(client: Any, topic_arn: str, events: list[dict[str, Any]]) -> tuple[set[int], int]:
    successful: set[int] = set()
    failed = 0
    for offset in range(0, len(events), 10):
        chunk = events[offset:offset + 10]
        response = client.publish_batch(
            TopicArn=topic_arn,
            PublishBatchRequestEntries=[
                {"Id": str(index), "Message": json.dumps(event, separators=(",", ":"))}
                for index, event in enumerate(chunk)
            ],
        )
        successful.update(offset + int(entry["Id"]) for entry in response.get("Successful", []))
        failed += len(response.get("Failed", []))
    return successful, failed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--topic-arn")
    parser.add_argument("--profile", default="vfl-build")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--republish", action="store_true")
    parser.add_argument("--poison", action="store_true")
    parser.add_argument("--raw-duplicate", action="store_true")
    parser.add_argument("--queue-url")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    return parser


def main(argv: list[str] | None = None, *, session_factory: Any = None) -> int:
    args = _parser().parse_args(argv)
    rows = _load_rows(args.documents, args.limit)
    rows.sort(key=lambda row: str(row["doc_id"]))

    if args.poison:
        events = [build_poison_event(rows[0])] if rows else []
    else:
        events = [build_event(row) for row in rows]
    raw_event = build_event(rows[0]) if args.raw_duplicate and rows else None

    if args.dry_run:
        print(f"built={len(events) + int(raw_event is not None)} failed=0 manifest=none")
        return 0
    if events and not args.topic_arn:
        raise SystemExit("--topic-arn is required when publishing SNS events")
    if raw_event and not args.queue_url:
        raise SystemExit("--queue-url is required with --raw-duplicate")

    if session_factory is None:
        import boto3

        session_factory = boto3.Session
    session = session_factory(profile_name=args.profile, region_name=args.region)
    sns = session.client("sns") if events else None
    sqs = session.client("sqs") if raw_event else None

    successful_ids, failed = _publish_sns(sns, args.topic_arn, events) if events else ([], 0)
    records = [
        _manifest_row(event, "published" if index in successful_ids else "failed", "sns")
        for index, event in enumerate(events)
    ]
    if raw_event:
        sqs.send_message(QueueUrl=args.queue_url, MessageBody=json.dumps(raw_event, separators=(",", ":")))
        records.append(_manifest_row(raw_event, "published", "sqs"))
    path = _write_manifest(args.phase, records)
    print(f"published={len(records) - failed} failed={failed} manifest={path.as_posix()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
