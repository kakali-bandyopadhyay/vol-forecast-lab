"""Tests-first coverage for the author-owned S3 Lambda handler contract."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO = Path(__file__).resolve().parents[1]
HANDLER_PATH = REPO / "lambda" / "extract" / "handler.py"
FIXTURE_PATH = REPO / "tests" / "fixtures" / "s3_raw_event.json"


def _load_handler():
    spec = importlib.util.spec_from_file_location("vfl_lambda_handler_under_test", HANDLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeS3:
    def __init__(self, objects: dict[tuple[str, str], dict] | None = None):
        self.objects = objects or {}
        self.get_calls: list[dict] = []
        self.put_calls: list[dict] = []

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return self.objects[(kwargs["Bucket"], kwargs["Key"])]

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)


def _record(**overrides):
    result = {
        "doc_id": "statement-20200129",
        "event_date": date(2020, 1, 29),
        "doc_type": "statement",
        "url": "https://example.test/statement",
        "sha256": "abc123",
        "text": "Federal Reserve statement",
        "n_chars": 26,
        "page_count": None,
        "extractor_version": "fixture-version",
        "extracted_at": datetime(2020, 1, 29, tzinfo=timezone.utc),
        "votes_against": [],
        "vote_tally": None,
        "blocks_dropped": 0,
        "paragraphs": [{"text": "Federal Reserve statement", "page": None}],
    }
    result.update(overrides)
    return result


def _event(*keys):
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "fixture"},
                    "object": {"key": key},
                }
            }
            for key in keys
        ]
    }


def _glue_columns() -> list[tuple[str, str]]:
    """Read the Glue columns using a regex over the Terraform columns blocks."""
    main_tf = (REPO / "infra" / "terraform" / "main.tf").read_text(encoding="utf-8")
    table_start = main_tf.index('resource "aws_glue_catalog_table" "vfl_documents_clean" {')
    table_end = main_tf.index('\nresource "', table_start + 1)
    table = main_tf[table_start:table_end]
    return re.findall(
        r"columns\s*\{\s*name\s*=\s*\"([a-z0-9_]+)\"\s+type\s*=\s*\"([^\"]+)\"\s*\}",
        table,
    )


def _assert_glue_arrow_type(actual, glue_type: str):
    if glue_type == "string":
        assert pa.types.is_string(actual)
    elif glue_type == "date":
        assert pa.types.is_date32(actual)
    elif glue_type == "int":
        assert pa.types.is_int32(actual)
    elif glue_type == "timestamp":
        assert pa.types.is_timestamp(actual)
    elif glue_type == "array<string>":
        assert pa.types.is_list(actual) and pa.types.is_string(actual.value_type)
    elif glue_type == "array<int>":
        assert pa.types.is_list(actual) and pa.types.is_int32(actual.value_type)
    else:
        pytest.fail(f"Unmapped Glue type in Terraform: {glue_type}")


def _install_fakes(monkeypatch, *, objects=None, records=None):
    handler = _load_handler()
    s3 = FakeS3(objects or {("fixture", "raw/statement/20200129.html"): {
        "Body": b"<html>fixture</html>",
        "Metadata": {"sha256": "abc123", "url": "https://example.test/statement"},
    }})
    monkeypatch.setattr(handler, "_s3_client", lambda: s3, raising=False)
    chosen_records = records or [_record()]
    calls = []

    def fake_extract_document(path, **kwargs):
        calls.append((Path(path), kwargs))
        if isinstance(chosen_records, Exception):
            raise chosen_records
        result = chosen_records.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(handler, "extract_document", fake_extract_document, raising=False)
    return handler, s3, calls


def test_fixture_has_raw_record_and_ignored_clean_record():
    event = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert [row["s3"]["object"]["key"] for row in event["Records"]] == [
        "raw/statement/20200129.html",
        "clean/year=2020/doc_type=statement/statement-20200129.parquet",
    ]


def test_writes_contract_key_metadata_and_glue_schema(monkeypatch):
    handler, s3, calls = _install_fakes(monkeypatch)
    result = handler.handler(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")), None)

    assert result == {"processed": 1, "errors": []}
    assert calls[0][1] == {
        "event_date": "20200129",
        "doc_type": "statement",
        "url": "https://example.test/statement",
        "sha256": "abc123",
    }
    assert len(s3.put_calls) == 1
    written = s3.put_calls[0]
    assert written["Bucket"] == "fixture"
    assert written["Key"] == "clean/year=2020/doc_type=statement/statement-20200129.parquet"
    assert set(written["Metadata"]) == {"doc-id", "sha256", "doc-type", "event-date", "extractor-version"}
    assert written["Metadata"] == {
        "doc-id": "statement-20200129",
        "sha256": "abc123",
        "doc-type": "statement",
        "event-date": "2020-01-29",
        "extractor-version": "fixture-version",
    }

    schema = pq.read_schema(pa.BufferReader(written["Body"]))
    glue_columns = _glue_columns()
    assert [field.name for field in schema] == [name for name, _ in glue_columns]
    for field, (_name, glue_type) in zip(schema, glue_columns, strict=True):
        _assert_glue_arrow_type(field.type, glue_type)
    assert "year" not in schema.names and "doc_type" not in schema.names
    table = pq.read_table(pa.BufferReader(written["Body"]))
    assert len(table.column("paragraph_texts")[0].as_py()) == len(table.column("paragraph_pages")[0].as_py())


def test_non_raw_key_is_ignored(monkeypatch):
    handler, s3, calls = _install_fakes(monkeypatch)
    result = handler.handler(_event("clean/year=2020/doc_type=statement/statement-20200129.parquet"), None)
    assert result == {"processed": 0, "errors": []}
    assert s3.get_calls == [] and s3.put_calls == [] and calls == []


def test_bad_record_is_reported_and_good_record_is_still_written(monkeypatch):
    objects = {
        ("fixture", "raw/statement/20200129.html"): {
            "Body": b"good", "Metadata": {"sha256": "abc123", "url": "https://example.test/statement"}
        },
        ("fixture", "raw/minutes/20200130.pdf"): {
            "Body": b"bad", "Metadata": {"sha256": "def456", "url": "https://example.test/minutes"}
        },
    }
    handler, s3, _calls = _install_fakes(monkeypatch, objects=objects, records=[_record(), RuntimeError("bad document")])
    result = handler.handler(_event("raw/statement/20200129.html", "raw/minutes/20200130.pdf"), None)
    assert result["processed"] == 1
    assert result["errors"] == [{"key": "raw/minutes/20200130.pdf", "error": "bad document"}]
    assert len(s3.put_calls) == 1


def test_unequal_paragraph_arrays_become_record_error(monkeypatch):
    malformed = _record(paragraphs=[{"text": "one", "page": 1}, {"text": "two", "page": None}])
    # A malformed extractor payload must be rejected before it can be written.
    malformed["paragraph_pages"] = [1]
    handler, s3, _calls = _install_fakes(monkeypatch, records=[malformed])
    result = handler.handler(_event("raw/statement/20200129.html"), None)
    assert result["processed"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["key"] == "raw/statement/20200129.html"
    assert s3.put_calls == []


def test_url_encoded_key_is_decoded_before_s3_read(monkeypatch):
    encoded = "raw%2Fstatement%2F20200129.html"
    handler, s3, calls = _install_fakes(monkeypatch)
    result = handler.handler(_event(encoded), None)
    assert result == {"processed": 1, "errors": []}
    assert s3.get_calls[0]["Key"] == "raw/statement/20200129.html"
    assert calls[0][1]["event_date"] == "20200129"
