"""Author-owned Kafka producer and consumer interfaces."""

from typing import Any


def publish_document(record: dict[str, Any], *, bootstrap_servers: str = "localhost:9092") -> None:
    """Publish a new raw-document event to the configured Kafka topic."""
    raise NotImplementedError("author")


def consume_document_events(*, bootstrap_servers: str = "localhost:9092") -> list[dict[str, Any]]:
    """Consume document events from the configured Kafka topic."""
    raise NotImplementedError("author")
