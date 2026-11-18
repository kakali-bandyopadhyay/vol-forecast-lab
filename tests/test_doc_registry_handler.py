"""Tests for the author-owned DynamoDB document registry Lambda handler (Step 10d).

``lambda`` is a Python keyword, so ``import lambda.doc_registry`` cannot work: the handler is
loaded by path instead. Every fake client is either a botocore ``Stubber`` around a real client
with fake credentials or a recording stand-in, so no test can reach AWS.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import datetime

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

REPO = pathlib.Path(__file__).resolve().parents[1]
HANDLER_PATH = REPO / "lambda" / "doc_registry" / "handler.py"
TABLE = "vfl-doc-registry"


def _load_handler():
    spec = importlib.util.spec_from_file_location("doc_registry_handler", HANDLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


handler = _load_handler()


def valid_event(**overrides):
    """Return a complete, valid v1 registration event."""
    event = {
        "schema_version": "1",
        "event_id": "evt-1",
        "event_type": "document.registered",
        "doc_id": "statement-20200129",
        "sha256": "a" * 64,
        "doc_type": "statement",
        "event_date": "2020-01-29",
        "n_chars": 1234,
        "extractor_version": "extract-v1",
        "published_at": "2026-11-18T09:00:00Z",
    }
    event.update(overrides)
    return event


def record(body, mid="m1", rc=1):
    """Wrap an event body in the SQS record shape the handler reads."""
    return {
        "messageId": mid,
        "body": body if isinstance(body, str) else json.dumps(body),
        "attributes": {"ApproximateReceiveCount": str(rc)},
    }


class FakeRegistry:
    """Recording stand-in for the low-level DynamoDB client."""

    def __init__(self, error: str | None = None):
        self.error = error
        self.put_calls: list[dict] = []

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.error is not None:
            raise client_error(self.error)
        return {}


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "PutItem")


def stub_client():
    """Return a real client plus its Stubber, with fake credentials so nothing leaves the process."""
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )
    return client, Stubber(client)


@pytest.fixture
def stubbed():
    client, stub = stub_client()
    with stub:
        yield client, stub


def logs(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_new_document_is_written_once(capsys):
    client = FakeRegistry()

    out = handler.handle_batch({"Records": [record(valid_event())]}, client, TABLE, {"1"})

    assert out == {"batchItemFailures": []}
    assert logs(capsys) == [
        {
            "outcome": "written",
            "doc_id": "statement-20200129",
            "event_id": "evt-1",
            "message_id": "m1",
            "receive_count": 1,
        }
    ]
    call = client.put_calls[0]
    assert call["TableName"] == TABLE
    assert call["ConditionExpression"] == "attribute_not_exists(pk)"
    item = call["Item"]
    assert item["pk"] == {"S": "statement-20200129"}
    assert item["sk"] == {"S": "a" * 64}
    assert item["event_id"] == {"S": "evt-1"}
    assert item["first_event_id"] == {"S": "evt-1"}
    assert item["schema_version"] == {"S": "1"}
    assert item["event_type"] == {"S": "document.registered"}
    assert item["doc_type"] == {"S": "statement"}
    assert item["event_date"] == {"S": "2020-01-29"}
    assert item["extractor_version"] == {"S": "extract-v1"}
    assert item["published_at"] == {"S": "2026-11-18T09:00:00Z"}
    assert item["n_chars"] == {"N": "1234"}
    assert item["receive_count"] == {"N": "1"}
    written_at = item["written_at"]["S"]
    assert written_at.endswith("Z")
    datetime.fromisoformat(written_at.replace("Z", "+00:00"))


def test_same_event_twice_in_one_batch_is_written_then_duplicate(stubbed, capsys):
    client, stub = stubbed
    stub.add_response("put_item", {})
    stub.add_client_error("put_item", service_error_code="ConditionalCheckFailedException")

    body = valid_event()
    out = handler.handle_batch(
        {"Records": [record(body, mid="m1"), record(body, mid="m2")]}, client, TABLE, {"1"}
    )

    assert out == {"batchItemFailures": []}
    assert [line["outcome"] for line in logs(capsys)] == ["written", "duplicate"]
    stub.assert_no_pending_responses()


def test_new_event_id_with_same_content_is_duplicate(stubbed, capsys):
    client, stub = stubbed
    stub.add_client_error("put_item", service_error_code="ConditionalCheckFailedException")

    out = handler.handle_batch(
        {"Records": [record(valid_event(event_id="evt-2"), mid="m2")]}, client, TABLE, {"1"}
    )

    assert out == {"batchItemFailures": []}
    assert [line["outcome"] for line in logs(capsys)] == ["duplicate"]
    stub.assert_no_pending_responses()


def test_same_doc_with_new_sha256_is_a_second_version(capsys):
    client = FakeRegistry()

    out = handler.handle_batch(
        {
            "Records": [
                record(valid_event(), mid="m1"),
                record(valid_event(event_id="evt-2", sha256="b" * 64), mid="m2"),
            ]
        },
        client,
        TABLE,
        {"1"},
    )

    assert out == {"batchItemFailures": []}
    assert [line["outcome"] for line in logs(capsys)] == ["written", "written"]
    assert [call["Item"]["sk"]["S"] for call in client.put_calls] == ["a" * 64, "b" * 64]
    assert {call["Item"]["pk"]["S"] for call in client.put_calls} == {"statement-20200129"}


def test_bad_json_is_rejected_without_a_put(stubbed, capsys):
    client, stub = stubbed  # no expectation stubbed: any put_item call fails loudly

    out = handler.handle_batch({"Records": [record("{not json", mid="poison")]}, client, TABLE, {"1"})

    assert out == {"batchItemFailures": [{"itemIdentifier": "poison"}]}
    line = logs(capsys)[0]
    assert line["outcome"] == "rejected"
    assert line["doc_id"] is None
    assert line["event_id"] is None
    assert line["message_id"] == "poison"
    assert line["reason"]
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("missing", ["event_id", "n_chars"])
def test_missing_field_is_rejected(stubbed, capsys, missing):
    client, stub = stubbed
    event = valid_event()
    del event[missing]

    out = handler.handle_batch({"Records": [record(event, mid="m9")]}, client, TABLE, {"1"})

    assert out == {"batchItemFailures": [{"itemIdentifier": "m9"}]}
    line = logs(capsys)[0]
    assert line["outcome"] == "rejected"
    assert missing in line["reason"]
    stub.assert_no_pending_responses()


def test_boolean_n_chars_is_rejected(stubbed, capsys):
    client, stub = stubbed

    out = handler.handle_batch(
        {"Records": [record(valid_event(n_chars=True), mid="m9")]}, client, TABLE, {"1"}
    )

    assert out == {"batchItemFailures": [{"itemIdentifier": "m9"}]}
    assert logs(capsys)[0]["outcome"] == "rejected"
    stub.assert_no_pending_responses()


def test_bad_hash_is_rejected(stubbed, capsys):
    client, stub = stubbed

    out = handler.handle_batch(
        {
            "Records": [
                record(valid_event(sha256="A" * 64), mid="upper"),
                record(valid_event(sha256="a" * 63), mid="short"),
            ]
        },
        client,
        TABLE,
        {"1"},
    )

    assert out == {
        "batchItemFailures": [{"itemIdentifier": "upper"}, {"itemIdentifier": "short"}]
    }
    outcomes = logs(capsys)
    assert [line["outcome"] for line in outcomes] == ["rejected", "rejected"]
    assert "sha256" in outcomes[0]["reason"]
    stub.assert_no_pending_responses()


def test_unsupported_schema_version_is_rejected_then_accepted_after_rollout(stubbed, capsys):
    client, stub = stubbed
    poison = valid_event(schema_version="2", event_id="evt-2", sha256="b" * 64)

    first = handler.handle_batch({"Records": [record(poison, mid="poison")]}, client, TABLE, {"1"})
    stub.add_response("put_item", {})
    second = handler.handle_batch(
        {"Records": [record(poison, mid="poison")]}, client, TABLE, {"1", "2"}
    )

    assert first == {"batchItemFailures": [{"itemIdentifier": "poison"}]}
    assert second == {"batchItemFailures": []}
    outcomes = logs(capsys)
    assert [line["outcome"] for line in outcomes] == ["rejected", "written"]
    assert "schema_version" in outcomes[0]["reason"]
    stub.assert_no_pending_responses()


def test_throttling_is_reported_as_retry(stubbed, capsys):
    client, stub = stubbed
    stub.add_client_error("put_item", service_error_code="ProvisionedThroughputExceededException")

    out = handler.handle_batch({"Records": [record(valid_event())]}, client, TABLE, {"1"})

    assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    line = logs(capsys)[0]
    assert line["outcome"] == "retry"
    assert line["reason"] == "ProvisionedThroughputExceededException"
    stub.assert_no_pending_responses()


def test_ten_record_batch_with_one_poison_returns_one_failure(capsys):
    client = FakeRegistry()
    records = [
        record(valid_event(event_id=f"evt-{i}", doc_id=f"statement-{i}", sha256=f"{i:064x}"), mid=f"m{i}")
        for i in range(9)
    ]
    records.insert(4, record(valid_event(sha256="not-a-hash", event_id="evt-poison"), mid="poison"))

    out = handler.handle_batch({"Records": records}, client, TABLE, {"1"})

    assert out == {"batchItemFailures": [{"itemIdentifier": "poison"}]}
    assert len(client.put_calls) == 9
    outcomes = [line["outcome"] for line in logs(capsys)]
    assert len(outcomes) == 10
    assert outcomes.count("written") == 9
    assert outcomes.count("rejected") == 1


def test_lambda_handler_reads_environment_on_every_call(monkeypatch, capsys):
    client = FakeRegistry()
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("ACCEPTED_SCHEMA_VERSIONS", "1, 2")
    monkeypatch.setattr(handler.boto3, "client", lambda *args, **kwargs: client)

    out = handler.lambda_handler(
        {"Records": [record(valid_event(schema_version="2"), mid="v2")]}, context=None
    )

    assert out == {"batchItemFailures": []}
    assert [line["outcome"] for line in logs(capsys)] == ["written"]
    assert client.put_calls[0]["TableName"] == TABLE
    assert client.put_calls[0]["Item"]["schema_version"] == {"S": "2"}
