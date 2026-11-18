"""AI-written plumbing test – reviewed line by line by the author."""

from datetime import date
import re

import scripts.publish_doc_events as publisher


def row(index=0):
    return {
        "doc_id": f"doc-{index:03d}",
        "event_date": date(2025, 1, 2),
        "doc_type": "minutes",
        "sha256": f"{index:064x}",
        "n_chars": 100 + index,
        "extractor_version": "extract-v1",
    }


def test_build_event_has_contract_fields_and_types():
    event = publisher.build_event(row(), event_id="fixed", published_at="2025-01-02T03:04:05Z")
    assert set(event) == {
        "schema_version", "event_id", "event_type", "doc_id", "sha256", "doc_type",
        "event_date", "n_chars", "extractor_version", "published_at",
    }
    assert event["schema_version"] == "1"
    assert isinstance(event["n_chars"], int)
    assert event["event_date"] == "2025-01-02"
    assert event["published_at"].endswith("Z")


def test_event_ids_change_but_document_hash_does_not():
    first = publisher.build_event(row())
    second = publisher.build_event(row())
    assert first["event_id"] != second["event_id"]
    assert first["sha256"] == second["sha256"]


def test_poison_event_is_version_two_with_changed_digest():
    event = publisher.build_poison_event(row())
    assert event["schema_version"] == "2"
    assert re.fullmatch(r"[0-9a-f]{64}", event["sha256"])
    assert event["sha256"] != row()["sha256"]
    assert event["extractor_version"] == "extract-v1+v2-test"


class FakeSNS:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def publish_batch(self, **kwargs):
        entries = kwargs["PublishBatchRequestEntries"]
        self.calls.append(entries)
        if self.fail:
            return {"Successful": entries[:-1], "Failed": [entries[-1]]}
        return {"Successful": entries, "Failed": []}


class FakeSQS:
    def send_message(self, **kwargs):
        return {"MessageId": "msg-1"}


class FakeSession:
    def __init__(self, fail=False):
        self.sns = FakeSNS(fail)
        self.sqs = FakeSQS()

    def client(self, name):
        return {"sns": self.sns, "sqs": self.sqs}[name]


def test_266_rows_publish_as_27_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "MANIFEST_DIR", tmp_path)
    monkeypatch.setattr(publisher, "_load_rows", lambda _path, _limit: [row(i) for i in range(266)])
    session = FakeSession()
    code = publisher.main(["--phase", "R2", "--topic-arn", "arn:topic"], session_factory=lambda **_: session)
    assert code == 0
    assert len(session.sns.calls) == 27
    assert all(len(batch) <= 10 for batch in session.sns.calls)
    assert sum(map(len, session.sns.calls)) == 266


def test_failed_batch_entry_returns_exit_one(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "MANIFEST_DIR", tmp_path)
    monkeypatch.setattr(publisher, "_load_rows", lambda _path, _limit: [row(i) for i in range(11)])
    session = FakeSession(fail=True)
    assert publisher.main(["--phase", "R2", "--topic-arn", "arn:topic"], session_factory=lambda **_: session) == 1


def test_dry_run_never_creates_a_client(monkeypatch, capsys):
    monkeypatch.setattr(publisher, "_load_rows", lambda _path, _limit: [row(i) for i in range(266)])

    def forbidden_factory(**_kwargs):
        raise AssertionError("dry-run must not create a client")

    assert publisher.main(["--phase", "DRY", "--dry-run"], session_factory=forbidden_factory) == 0
    assert capsys.readouterr().out.strip() == "built=266 failed=0 manifest=none"
