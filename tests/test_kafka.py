"""Offline tests for the Step 10c Kafka producer, consumers and checkpoints."""

from __future__ import annotations

import io
import hashlib
import json
import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from qdrant_client import QdrantClient

from volab.text import consumers, index, kafka_io
from volab.text.kafka_io import DocumentMessage


class FakeProducer:
    def __init__(self):
        self.messages = []
        self.flushes = 0

    def produce(self, topic, key=None, value=None):
        self.messages.append((topic, key, value))

    def flush(self):
        self.flushes += 1
        return 0


class FakeMessage:
    def __init__(self, payload: str):
        self.payload = payload

    def value(self):
        return self.payload.encode("utf-8")

    def error(self):
        return None


class EmptyMessage(FakeMessage):
    def value(self):
        return None


class FakeConsumer:
    def __init__(self, messages):
        self.messages = list(messages)
        self.subscribed = []
        self.commits = []
        self.position = 0
        self.closed = False

    def subscribe(self, topics):
        self.subscribed = list(topics)

    def poll(self, timeout):
        if self.position >= len(self.messages):
            return None
        message = self.messages[self.position]
        self.position += 1
        return message

    def commit(self, message=None, asynchronous=False):
        self.commits.append(message)

    def close(self):
        self.closed = True


class _NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self, objects=None, clean_metadata=None, etag='"etag-1"', version="v1"):
        self.objects = objects or {}
        self.clean_metadata = clean_metadata or {}
        self.etag = etag
        self.version = version
        self.puts = []

    def head_object(self, *, Bucket, Key):
        if Key.startswith("clean/"):
            return {"Metadata": self.clean_metadata, "VersionId": self.version}
        if Key in self.objects:
            return {"Metadata": self.objects[Key].get("Metadata", {}), "VersionId": self.version}
        raise _NotFound()

    def put_object(self, *, Bucket, Key, Body, Metadata):
        self.objects[Key] = {"Body": Body, "Metadata": Metadata}
        self.puts.append(Key)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}

    def list_objects_v2(self, *, Bucket, Prefix, **kwargs):
        contents = [
            {"Key": key, "ETag": self.etag}
            for key in sorted(self.objects)
            if key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}


class CountingQdrant:
    def __init__(self):
        self.inner = QdrantClient(":memory:")
        self.upserted = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def upsert(self, *, collection_name, points, wait):
        self.upserted += len(points)
        return self.inner.upsert(
            collection_name=collection_name, points=points, wait=wait
        )


def _fake_embedder(texts):
    return [[0.0] * 8 for _ in texts]


def _message(event="raw") -> DocumentMessage:
    return DocumentMessage(
        doc_id="statement-20200129",
        doc_type="statement",
        event_date="2020-01-29",
        sha256="abc123",
        path="C:/tmp/statement.html" if event == "raw" else None,
        s3_key=None if event == "raw" else "clean/year=2020/doc_type=statement/doc.parquet",
        event=event,
    )


def _clean_parquet(
    doc_id: str = "statement-20200129",
    text: str = "The Committee discussed the economic outlook and monetary policy.",
    paragraphs: list[str] | None = None,
) -> bytes:
    paragraphs = paragraphs if paragraphs is not None else [text]
    frame = pd.DataFrame([{
        "doc_id": doc_id,
        "doc_type": "statement" if doc_id.startswith("statement") else "minutes",
        "event_date": date(2020, 1, 29),
        "url": "https://example.test/document",
        "sha256": "abc123",
        "text": text,
        "paragraph_texts": paragraphs,
        "paragraph_pages": [None] * len(paragraphs),
    }])
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def test_message_schema_round_trips_and_rejects_bad_event():
    message = _message()
    assert DocumentMessage.from_json(message.to_json()) == message
    with pytest.raises(ValueError):
        DocumentMessage(
            doc_id="x", doc_type="statement", event_date="2020-01-29",
            sha256="h", path="p", s3_key=None, event="other",
        )


def test_message_url_round_trips_with_and_without_url():
    with_url = _message()
    object.__setattr__(with_url, "url", "https://example.test/source")
    assert DocumentMessage.from_json(with_url.to_json()) == with_url
    without_url = _message()
    assert without_url.url is None
    assert DocumentMessage.from_json(without_url.to_json()) == without_url


def test_uploader_skips_a_matching_sha(tmp_path: Path):
    path = tmp_path / "statement.html"
    path.write_text("<html/>", encoding="utf-8")
    record = _message("raw")
    object.__setattr__(record, "path", str(path))
    # The uploader now verifies the file bytes, so the message must carry the file's real hash.
    object.__setattr__(record, "sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    s3 = FakeS3(objects={
        "raw/statement/20200129.html": {"Metadata": {"sha256": record.sha256}}
    })

    assert consumers.uploader(record, s3=s3, bucket="bucket") == "duplicate"
    assert s3.puts == []


def test_uploader_rejects_file_with_mismatched_sha(tmp_path: Path):
    path = tmp_path / "statement.html"
    path.write_bytes(b"not the expected content")
    record = _message("raw")
    object.__setattr__(record, "path", str(path))
    s3 = FakeS3()

    with pytest.raises(ValueError, match=record.doc_id):
        consumers.uploader(record, s3=s3, bucket="bucket")
    assert s3.puts == []


def test_mismatched_file_through_consume_writes_one_dlq(tmp_path: Path):
    path = tmp_path / "statement.html"
    path.write_bytes(b"not the expected content")
    record = _message("raw")
    object.__setattr__(record, "path", str(path))
    producer = FakeProducer()
    consumer = FakeConsumer([FakeMessage(record.to_json())])
    counts = kafka_io.consume(
        "docs.raw", "vfl-uploader",
        lambda message: consumers.uploader(message, s3=FakeS3(), bucket="bucket"),
        consumer=consumer, producer=producer, max_messages=1, sleep_fn=lambda s: None,
    )

    assert counts["dlq"] == 1
    assert len(producer.messages) == 1
    assert record.doc_id in json.loads(producer.messages[0][2])["error"]


def test_replaying_the_same_message_indexes_once(tmp_path: Path):
    clean_key = "clean/year=2020/doc_type=statement/doc.parquet"
    s3 = FakeS3(objects={clean_key: {"Body": _clean_parquet()}})
    qdrant = CountingQdrant()
    state = tmp_path / "indexer_state.json"
    message = str(_message("clean").to_json())

    def handler(record):
        return consumers.indexer(
            record,
            s3=s3,
            bucket="bucket",
            qdrant=qdrant,
            embedder=_fake_embedder,
            state_path=state,
            cache_path=tmp_path / "embeddings.parquet",
            dense_dim=8,
        )

    first = kafka_io.consume(
        "docs.clean", "vfl-indexer", handler,
        consumer=FakeConsumer([FakeMessage(message)]), max_messages=1,
    )
    after_first = qdrant.upserted
    second = kafka_io.consume(
        "docs.clean", "vfl-indexer", handler,
        consumer=FakeConsumer([FakeMessage(message)]), max_messages=1,
    )

    assert first["processed"] == 1
    assert second["duplicates_suppressed"] == 1
    assert after_first > 0
    assert qdrant.upserted == after_first


def test_indexing_second_document_keeps_the_first(tmp_path: Path):
    first_key = "clean/year=2020/doc_type=statement/statement.parquet"
    second_key = "clean/year=2020/doc_type=minutes/minutes.parquet"
    s3 = FakeS3(objects={
        first_key: {"Body": _clean_parquet()},
        second_key: {"Body": _clean_parquet("minutes-20200129")},
    })
    qdrant = CountingQdrant()
    state = tmp_path / "indexer_state.json"

    for doc_id, key in (
        ("statement-20200129", first_key),
        ("minutes-20200129", second_key),
    ):
        record = DocumentMessage(
            doc_id=doc_id,
            doc_type=doc_id.split("-")[0],
            event_date="2020-01-29",
            sha256=f"sha-{doc_id}",
            path=None,
            s3_key=key,
            event="clean",
        )
        consumers.indexer(
            record,
            s3=s3,
            bucket="bucket",
            qdrant=qdrant,
            embedder=_fake_embedder,
            state_path=state,
            cache_path=tmp_path / "embeddings.parquet",
            dense_dim=8,
        )

    assert qdrant.count(collection_name=index.COLLECTION, exact=True).count == 2


def test_reindex_deletes_stale_chunks_only_for_that_document(tmp_path: Path):
    doc_a = "statement-20200129"
    doc_b = "minutes-20200129"
    key_a = "clean/year=2020/doc_type=statement/statement.parquet"
    key_b = "clean/year=2020/doc_type=minutes/minutes.parquet"
    original_text = " ".join(["policy"] * 600)
    shorter_text = " ".join(["policy"] * 300)
    s3 = FakeS3(objects={
        key_a: {"Body": _clean_parquet(doc_a, text=original_text)},
        key_b: {"Body": _clean_parquet(doc_b)},
    })
    qdrant = CountingQdrant()
    state = tmp_path / "indexer_state.json"

    def index_doc(doc_id: str, doc_type: str, key: str, sha256: str):
        return consumers.indexer(
            DocumentMessage(
                doc_id=doc_id,
                doc_type=doc_type,
                event_date="2020-01-29",
                sha256=sha256,
                path=None,
                s3_key=key,
                event="clean",
            ),
            s3=s3,
            bucket="bucket",
            qdrant=qdrant,
            embedder=_fake_embedder,
            state_path=state,
            cache_path=tmp_path / "embeddings.parquet",
            dense_dim=8,
        )

    assert index_doc(doc_a, "statement", key_a, "a-v1") == "processed"
    assert index_doc(doc_b, "minutes", key_b, "b-v1") == "processed"
    before, _ = qdrant.scroll(
        collection_name=index.COLLECTION,
        with_payload=True,
        with_vectors=False,
    )
    before_a = {point.payload["chunk_id"] for point in before if point.payload["doc_id"] == doc_a}
    before_b = {point.payload["chunk_id"] for point in before if point.payload["doc_id"] == doc_b}
    # Chunk counts depend on the tokenizer, so assert relations rather than fixed numbers.
    assert len(before_a) >= 2
    assert len(before_b) >= 1

    s3.objects[key_a]["Body"] = _clean_parquet(doc_a, text=shorter_text)
    assert index_doc(doc_a, "statement", key_a, "a-v2") == "processed"
    after, _ = qdrant.scroll(
        collection_name=index.COLLECTION,
        with_payload=True,
        with_vectors=False,
    )
    after_a = {point.payload["chunk_id"] for point in after if point.payload["doc_id"] == doc_a}
    after_b = {point.payload["chunk_id"] for point in after if point.payload["doc_id"] == doc_b}
    assert after_a < before_a            # strict subset: only stale chunks of doc A removed
    assert len(after_a) >= 1
    assert after_b == before_b           # doc B untouched


def test_handler_failure_writes_one_dlq_and_commits():
    producer = FakeProducer()
    consumer = FakeConsumer([FakeMessage(str(_message("clean").to_json()))])
    attempts = []

    def handler(record):
        attempts.append(record.doc_id)
        raise RuntimeError("boom")

    counts = kafka_io.consume(
        "docs.clean", "vfl-indexer", handler,
        consumer=consumer, producer=producer, max_messages=1,
        sleep_fn=lambda s: attempts.append(f"sleep:{s}"),
    )

    assert attempts == ["statement-20200129", "sleep:0.5", "statement-20200129", "sleep:1.0", "statement-20200129"]
    assert counts["dlq"] == 1
    assert len(producer.messages) == 1
    topic, key, value = producer.messages[0]
    assert topic == "docs.dlq"
    assert key == b"statement-20200129"
    assert "boom" in json.loads(value)["error"]
    assert len(consumer.commits) == 1


def test_none_value_goes_to_dlq_and_commits():
    producer = FakeProducer()
    consumer = FakeConsumer([EmptyMessage("")])
    counts = kafka_io.consume(
        "docs.clean", "vfl-indexer", lambda record: None,
        consumer=consumer, producer=producer, max_messages=1,
        sleep_fn=lambda s: None,
    )
    assert counts["dlq"] == 1
    assert len(producer.messages) == 1
    assert producer.messages[0][1] == b"empty"
    assert len(consumer.commits) == 1


def test_created_consumer_is_closed(monkeypatch):
    consumer = FakeConsumer([])
    monkeypatch.setitem(sys.modules, "confluent_kafka", types.SimpleNamespace(Consumer=lambda config: consumer))
    counts = kafka_io.consume(
        "docs.clean", "vfl-indexer", lambda record: None,
        max_messages=0, sleep_fn=lambda s: None,
    )
    assert counts["processed"] == 0
    assert consumer.closed


def test_clean_publisher_checkpoint_skips_unchanged_and_republishes_changed(
    tmp_path: Path,
):
    key = "clean/year=2020/doc_type=statement/doc.parquet"
    s3 = FakeS3(objects={key: {"Body": b""}}, clean_metadata={
        "doc-id": "statement-20200129",
        "doc-type": "statement",
        "event-date": "2020-01-29",
        "sha256": "abc123",
    })
    state_path = tmp_path / "publisher_state.json"
    producer = FakeProducer()

    first = consumers.clean_publisher(
        s3=s3, bucket="bucket", producer=producer, state_path=state_path
    )
    assert first == {"processed": 1, "duplicates_suppressed": 0, "dlq": 0}
    assert len(producer.messages) == 1
    checkpoint = json.loads(state_path.read_text(encoding="utf-8"))[key]
    assert checkpoint == {"etag": '"etag-1"', "version_id": "v1"}

    second = consumers.clean_publisher(
        s3=s3, bucket="bucket", producer=producer, state_path=state_path
    )
    assert second["duplicates_suppressed"] == 1
    assert len(producer.messages) == 1

    s3.etag = '"etag-2"'
    third = consumers.clean_publisher(
        s3=s3, bucket="bucket", producer=producer, state_path=state_path
    )
    assert third["processed"] == 1
    assert len(producer.messages) == 2

    restart_producer = FakeProducer()
    fourth = consumers.clean_publisher(
        s3=s3, bucket="bucket", producer=restart_producer, state_path=state_path
    )
    assert fourth["duplicates_suppressed"] == 1
    assert restart_producer.messages == []


def test_uploader_put_metadata_includes_url(tmp_path: Path):
    path = tmp_path / "statement.html"
    body = b"<html>source</html>"
    path.write_bytes(body)
    record = _message()
    object.__setattr__(record, "path", str(path))
    object.__setattr__(record, "sha256", hashlib.sha256(body).hexdigest())
    object.__setattr__(record, "url", "https://example.test/source")
    s3 = FakeS3()

    assert consumers.uploader(record, s3=s3, bucket="bucket") == "processed"
    assert s3.objects["raw/statement/20200129.html"]["Metadata"] == {
        "sha256": record.sha256,
        "url": record.url,
    }
