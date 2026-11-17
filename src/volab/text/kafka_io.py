"""Author-owned Kafka message schema, producer and committing consumer."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

RAW_EVENT = "raw"
CLEAN_EVENT = "clean"
DOC_TYPES = frozenset({"statement", "minutes"})
EVENTS = frozenset({RAW_EVENT, CLEAN_EVENT})
DEFAULT_BOOTSTRAP = "127.0.0.1:9092"  # Codex fix (10c review, 16 Nov)
DEFAULT_DLQ_TOPIC = "docs.dlq"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_event_date(value: str) -> str:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"event_date must be YYYY-MM-DD or YYYYMMDD, got {value!r}")


@dataclass(frozen=True)
class DocumentMessage:
    """One raw or clean document event."""

    doc_id: str
    doc_type: str
    event_date: str
    sha256: str
    path: str | None
    s3_key: str | None
    url: str | None = field(default=None, kw_only=True)
    event: str
    produced_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.doc_id:
            raise ValueError("doc_id must be non-empty")
        if self.doc_type not in DOC_TYPES:
            raise ValueError(f"doc_type must be one of {sorted(DOC_TYPES)}")
        if self.event not in EVENTS:
            raise ValueError(f"event must be one of {sorted(EVENTS)}")
        if not self.sha256:
            raise ValueError("sha256 must be non-empty")
        if self.event == RAW_EVENT and not self.path:
            raise ValueError("raw events require path")
        if self.event == CLEAN_EVENT and not self.s3_key:
            raise ValueError("clean events require s3_key")
        object.__setattr__(self, "event_date", _iso_event_date(self.event_date))
        if isinstance(self.produced_at, datetime):
            object.__setattr__(self, "produced_at", self.produced_at.astimezone(timezone.utc).isoformat())
        else:
            object.__setattr__(self, "produced_at", str(self.produced_at))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str | bytes | dict[str, Any]) -> "DocumentMessage":
        if isinstance(payload, (str, bytes)):
            data = json.loads(payload)
        else:
            data = payload
        if not isinstance(data, dict):
            raise ValueError("message must be a JSON object")
        required = {"doc_id", "doc_type", "event_date", "sha256", "event"}
        missing = required - set(data)
        if missing:
            raise ValueError(f"message is missing fields: {sorted(missing)}")
        return cls(
            doc_id=str(data["doc_id"]),
            doc_type=str(data["doc_type"]),
            event_date=str(data["event_date"]),
            sha256=str(data["sha256"]),
            path=None if data.get("path") is None else str(data["path"]),
            s3_key=None if data.get("s3_key") is None else str(data["s3_key"]),
            url=None if data.get("url") is None else str(data["url"]),
            event=str(data["event"]),
            produced_at=str(data.get("produced_at") or _utc_now()),
        )


def make_producer(bootstrap_servers: str = DEFAULT_BOOTSTRAP):
    """Create a confluent-kafka producer with idempotent, all-acks settings."""
    from confluent_kafka import Producer

    return Producer({
        "bootstrap.servers": bootstrap_servers,
        "enable.idempotence": True,
        "acks": "all",
    })


def _message(record: DocumentMessage | dict[str, Any]) -> DocumentMessage:
    return record if isinstance(record, DocumentMessage) else DocumentMessage.from_json(record)


def _produce(producer, topic: str, key: str, value: str) -> None:
    producer.produce(topic, key=key.encode("utf-8"), value=value.encode("utf-8"))
    remaining = producer.flush()
    if isinstance(remaining, int) and remaining:
        raise RuntimeError(f"{remaining} Kafka message(s) still pending after flush")


def publish_document(
    record: DocumentMessage | dict[str, Any],
    *,
    producer=None,
    topic: str = "docs.raw",
    bootstrap_servers: str = DEFAULT_BOOTSTRAP,
) -> None:
    """Publish a document event keyed by doc_id."""
    message = _message(record)
    _produce(
        producer or make_producer(bootstrap_servers),
        topic,
        message.doc_id,
        message.to_json(),
    )


def consume(
    topic: str,
    group_id: str,
    handler: Callable[[DocumentMessage], Any],
    *,
    consumer=None,
    max_messages: int | None = None,
    producer=None,
    bootstrap_servers: str = DEFAULT_BOOTSTRAP,
    dlq_topic: str = DEFAULT_DLQ_TOPIC,
    poll_timeout: float = 1.0,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> dict[str, int]:
    """Consume with manual commits: handler first, commit after success or DLQ."""
    if max_messages is not None and max_messages < 0:
        raise ValueError("max_messages must be non-negative")
    created = consumer is None
    if created:
        from confluent_kafka import Consumer

        consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        })
    consumer.subscribe([topic])

    counts = {"processed": 0, "duplicates_suppressed": 0, "dlq": 0}
    seen = 0
    try:
        while max_messages is None or seen < max_messages:
            message = consumer.poll(poll_timeout)
            if message is None:
                continue
            if message.error():
                raise RuntimeError(f"Kafka consumer error: {message.error()}")
            seen += 1
            payload = message.value()

            if payload is None:
                _produce(
                    producer or make_producer(bootstrap_servers),
                    dlq_topic,
                    "empty",
                    json.dumps({"error": "message value is empty", "raw": None}),
                )
                consumer.commit(message=message, asynchronous=False)
                counts["dlq"] += 1
                continue

            try:
                record = DocumentMessage.from_json(payload)
            except Exception as exc:
                raw = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
                _produce(
                    producer or make_producer(bootstrap_servers),
                    dlq_topic,
                    "unparseable",
                    json.dumps({"error": str(exc), "raw": raw}),
                )
                consumer.commit(message=message, asynchronous=False)
                counts["dlq"] += 1
                continue

            result: Any = None
            error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    result = handler(record)
                    error = None
                    break
                except Exception as exc:  # handler failures are retried
                    error = exc
                    if attempt < 3:
                        sleep_fn(0.5 * attempt)

            if error is not None:
                _produce(
                    producer or make_producer(bootstrap_servers),
                    dlq_topic,
                    record.doc_id,
                    json.dumps({
                        **record.to_dict(),
                        "error": str(error),
                        "attempts": 3,
                    }, sort_keys=True, ensure_ascii=False),
                )
                counts["dlq"] += 1
            elif result == "duplicate":
                counts["duplicates_suppressed"] += 1
            else:
                counts["processed"] += 1
            consumer.commit(message=message, asynchronous=False)

        return counts
    finally:
        if created:
            consumer.close()

