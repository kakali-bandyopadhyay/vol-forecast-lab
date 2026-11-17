"""Contract for publishing from an existing snapshot manifest."""  # Codex fix (10c review, 16 Nov)

import json

import pytest

from volab.text import pipeline


def test_publish_uses_manifest_without_fetching_or_writing_text_manifest(tmp_path, monkeypatch):  # Codex fix (10c review, 16 Nov)
    snapshot = "snap"
    docs = tmp_path / snapshot / "docs"
    docs.mkdir(parents=True)
    (docs / "manifest.json").write_text(json.dumps({"files": {
        "statement/20200129.html": {"status": "ok", "sha256": "abc", "url": "https://example.test/a"},
        "minutes/20200315.pdf": {"status": "error", "sha256": "skip"},
    }}), encoding="utf-8")
    manifest_path = tmp_path / "text_manifest.json"
    manifest_path.write_text("leave unchanged", encoding="utf-8")
    monkeypatch.setattr(pipeline, "SNAPSHOT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(pipeline, "fetch_documents", lambda _snapshot: pytest.fail("fetch_documents called"))
    published = []
    monkeypatch.setattr(pipeline.kafka_io, "make_producer", lambda: object())
    monkeypatch.setattr(pipeline.kafka_io, "publish_document", lambda message, **kwargs: published.append(message))

    result = pipeline.fetch_stage(snapshot, publish=True)

    assert result == {"files": 2, "published": 1}
    assert len(published) == 1
    assert published[0].doc_id == "statement-20200129"
    assert manifest_path.read_text(encoding="utf-8") == "leave unchanged"


def test_publish_exits_when_snapshot_manifest_is_missing(tmp_path, monkeypatch):  # Codex fix (10c review, 16 Nov)
    monkeypatch.setattr(pipeline, "SNAPSHOT_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="missing docs manifest"):
        pipeline.fetch_stage("absent", publish=True)
