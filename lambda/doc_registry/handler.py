"""Author-owned AWS Lambda handler for the DynamoDB document registry (Step 10d).

SQS delivers ``document.registered`` events from the ``vfl-doc-registry`` queue. Every record is
validated against the accepted schema versions and then written with a conditional put, so an
identical (doc_id, sha256) item is reported as a duplicate while a new sha256 for a known doc is
stored as a second version. Rejected and retryable records are returned as SQS
``ReportBatchItemFailures`` identifiers; the handler never retries or sleeps itself, because SQS
redelivery is the retry.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

REQUIRED: dict[str, type] = {
    "schema_version": str,
    "event_id": str,
    "event_type": str,
    "doc_id": str,
    "sha256": str,
    "doc_type": str,
    "event_date": str,
    "n_chars": int,
    "extractor_version": str,
    "published_at": str,
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DOC_TYPES = {"statement", "minutes"}

EVENT_TYPE = "document.registered"
CONDITION = "attribute_not_exists(pk)"
WRITTEN = "written"
DUPLICATE = "duplicate"
REJECTED = "rejected"
RETRY = "retry"


def _client() -> Any:
    """Return the low-level DynamoDB client.

    Created lazily, not at import time, so the tests can pass their own client in.
    """
    return boto3.client("dynamodb")


def _utc_now() -> str:
    """Return the current UTC time as ISO-8601 with a trailing ``Z``."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate(evt: Any, accepted: set[str]) -> None:
    """Raise ``ValueError`` on the first problem with one candidate event."""
    if not isinstance(evt, dict):
        raise ValueError("event is not a JSON object")

    for field, expected_type in REQUIRED.items():
        if field not in evt:
            raise ValueError(f"missing field: {field}")
        value = evt[field]
        if expected_type is int:
            # isinstance(True, int) is True, so booleans need rejecting explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"field {field} must be an integer")
        elif not isinstance(value, expected_type):
            raise ValueError(f"field {field} must be a {expected_type.__name__}")

    if evt["n_chars"] < 0:
        raise ValueError("field n_chars must be >= 0")
    if evt["schema_version"] not in accepted:
        raise ValueError(f"unsupported schema_version: {evt['schema_version']}")
    if evt["event_type"] != EVENT_TYPE:
        raise ValueError(f"unsupported event_type: {evt['event_type']}")
    if not SHA_RE.match(evt["sha256"]):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if evt["doc_type"] not in DOC_TYPES:
        raise ValueError(f"unsupported doc_type: {evt['doc_type']}")

    try:
        date.fromisoformat(evt["event_date"])
    except ValueError as exc:
        raise ValueError(f"event_date is not an ISO date: {evt['event_date']}") from exc

    try:
        datetime.fromisoformat(evt["published_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"published_at is not an ISO timestamp: {evt['published_at']}") from exc


def build_item(evt: dict[str, Any], receive_count: int) -> dict[str, Any]:
    """Convert one validated event into DynamoDB attribute-value format."""
    item: dict[str, Any] = {"pk": {"S": evt["doc_id"]}, "sk": {"S": evt["sha256"]}}
    for field, expected_type in REQUIRED.items():
        value = evt[field]
        item[field] = {"N": str(value)} if expected_type is int else {"S": value}
    item["first_event_id"] = {"S": evt["event_id"]}
    item["written_at"] = {"S": _utc_now()}
    item["receive_count"] = {"N": str(receive_count)}
    return item


def _log(
    outcome: str,
    *,
    doc_id: Any,
    event_id: Any,
    message_id: Any,
    receive_count: Any,
    reason: str | None = None,
) -> None:
    """Emit exactly one JSON log line for one record."""
    line = {
        "outcome": outcome,
        "doc_id": doc_id,
        "event_id": event_id,
        "message_id": message_id,
        "receive_count": receive_count,
    }
    if reason is not None:
        line["reason"] = reason
    print(json.dumps(line))


def process_record(record: dict[str, Any], client: Any, table: str, accepted: set[str]) -> str:
    """Handle one SQS record; return ``written``, ``duplicate``, ``rejected`` or ``retry``."""
    message_id = record["messageId"]
    receive_count = int(record["attributes"]["ApproximateReceiveCount"])

    try:
        evt = json.loads(record["body"])
    except (TypeError, ValueError):
        _log(
            REJECTED,
            doc_id=None,
            event_id=None,
            message_id=message_id,
            receive_count=receive_count,
            reason="body is not valid JSON",
        )
        return REJECTED

    doc_id = evt.get("doc_id") if isinstance(evt, dict) else None
    event_id = evt.get("event_id") if isinstance(evt, dict) else None
    try:
        validate(evt, accepted)
    except ValueError as exc:
        _log(
            REJECTED,
            doc_id=doc_id,
            event_id=event_id,
            message_id=message_id,
            receive_count=receive_count,
            reason=str(exc),
        )
        return REJECTED

    try:
        client.put_item(
            TableName=table,
            Item=build_item(evt, receive_count),
            ConditionExpression=CONDITION,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            _log(
                DUPLICATE,
                doc_id=doc_id,
                event_id=event_id,
                message_id=message_id,
                receive_count=receive_count,
            )
            return DUPLICATE
        _log(
            RETRY,
            doc_id=doc_id,
            event_id=event_id,
            message_id=message_id,
            receive_count=receive_count,
            reason=code or str(exc),
        )
        return RETRY

    _log(
        WRITTEN,
        doc_id=doc_id,
        event_id=event_id,
        message_id=message_id,
        receive_count=receive_count,
    )
    return WRITTEN


def handle_batch(event: dict[str, Any], client: Any, table: str, accepted: set[str]) -> dict[str, Any]:
    """Process a whole SQS batch; report only rejected and retryable message ids."""
    failures: list[dict[str, Any]] = []
    for record in event.get("Records") or []:
        message_id = record.get("messageId") if isinstance(record, dict) else None
        try:
            outcome = process_record(record, client, table, accepted)
        except Exception as exc:  # noqa: BLE001 - one bad record must never fail the batch
            _log(
                RETRY,
                doc_id=None,
                event_id=None,
                message_id=message_id,
                receive_count=None,
                reason=f"unhandled error: {exc!r}",
            )
            outcome = RETRY
        if outcome in (REJECTED, RETRY):
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Read configuration on every invocation, then hand the batch over."""
    table = os.environ["TABLE_NAME"]
    accepted = {
        value.strip()
        for value in os.environ.get("ACCEPTED_SCHEMA_VERSIONS", "1").split(",")
        if value.strip()
    }
    return handle_batch(event, _client(), table, accepted)
