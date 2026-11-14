"""Offline tests for Federal Reserve document fetching."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from volab.text import fetch_docs

SNAPSHOT = "20260102T030405Z"
EVENT_DATE = "20200129"


def _events_file(path: Path, dates: tuple[str, ...] = ("2020-01-29",)) -> Path:
    """Write an FOMC calendar fixture (one row per date)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parents[1] / "data" / "snapshots" / SNAPSHOT).mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as events:
        writer = csv.DictWriter(events, fieldnames=["event_date", "time_et", "type", "source_url"])
        writer.writeheader()
        for event_date in dates:
            writer.writerow({"event_date": event_date, "time_et": "14:00", "type": "FOMC_scheduled", "source_url": "https://example.test"})
    return path


def _time_args():
    """Provide a no-delay clock and stable UTC timestamp for tests."""
    tick = iter(float(i) for i in range(100))
    return {
        "sleep": lambda _seconds: None,
        "monotonic": lambda: next(tick),
        "now": lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    }


def test_url_building_for_known_event_date():
    """Date-specific statement and minutes endpoints follow Fed URL patterns."""
    assert fetch_docs.build_document_url(EVENT_DATE, "statement").endswith("monetary20200129a.htm")
    assert fetch_docs.build_document_url(EVENT_DATE, "minutes").endswith("fomcminutes20200129.pdf")


def test_404_is_recorded_as_not_found(tmp_path: Path):
    """A missing Federal Reserve PDF is recorded without a substituted source."""
    events = _events_file(tmp_path / "events" / "fomc.csv")
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=404, content=b"not found")

    manifest = fetch_docs.fetch_documents(
        SNAPSHOT, doc_type="minutes", root=tmp_path, events_file=events, get=get, **_time_args()
    )
    record = manifest["files"][f"minutes/{EVENT_DATE}.pdf"]
    assert record["status"] == "not_found"
    assert record["http_status"] == 404
    assert record["url"].endswith("fomcminutes20200129.pdf")
    assert record["sha256"] is None
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 30
    assert calls[0][1]["headers"]["User-Agent"] == fetch_docs.USER_AGENT


def test_fetch_manifest_contains_hashes_and_incremental_run_skips(tmp_path: Path):
    """Downloaded payloads are hashed and subsequent runs do not re-request them."""
    events = _events_file(tmp_path / "events" / "fomc.csv")
    payloads = {
        fetch_docs.build_document_url(EVENT_DATE, "statement"): b"<html>statement</html>",
        fetch_docs.build_document_url(EVENT_DATE, "minutes"): b"%PDF-test",
    }
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, content=payloads[url])

    manifest = fetch_docs.fetch_documents(
        SNAPSHOT, root=tmp_path, events_file=events, get=get, **_time_args()
    )
    assert len(calls) == 2
    for relative_name, record in manifest["files"].items():
        data = (tmp_path / "data" / "snapshots" / SNAPSHOT / "docs" / relative_name).read_bytes()
        assert record["bytes"] == len(data)
        assert record["sha256"] == hashlib.sha256(data).hexdigest()
        assert record["status"] == "ok"

    second = fetch_docs.fetch_documents(
        SNAPSHOT, root=tmp_path, events_file=events, get=get, **_time_args()
    )
    assert len(calls) == 2
    assert second == manifest


def test_existing_file_is_never_overwritten(tmp_path: Path):
    """An unregistered destination file is kept intact and not fetched over."""
    events = _events_file(tmp_path / "events" / "fomc.csv")
    destination = tmp_path / "data" / "snapshots" / SNAPSHOT / "docs" / "statement" / f"{EVENT_DATE}.html"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"precious existing bytes")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("existing file must not be downloaded over")

    manifest = fetch_docs.fetch_documents(
        SNAPSHOT,
        doc_type="statement",
        root=tmp_path,
        events_file=events,
        get=fail_if_called,
        **_time_args(),
    )
    assert destination.read_bytes() == b"precious existing bytes"
    assert manifest["files"][f"statement/{EVENT_DATE}.html"]["status"] == "error"


def test_limit_and_doc_type_only_fetch_requested_document(tmp_path: Path):
    """CLI options can restrict a pull to statement documents for one event (of two)."""
    events = _events_file(tmp_path / "events" / "fomc.csv", dates=("2020-01-29", "2020-03-18"))
    seen = []

    def get(url, **_kwargs):
        seen.append(url)
        return SimpleNamespace(status_code=200, content=b"file")

    fetch_docs.fetch_documents(
        SNAPSHOT,
        limit=1,
        doc_type="statement",
        root=tmp_path,
        events_file=events,
        get=get,
        **_time_args(),
    )
    assert seen == [fetch_docs.build_document_url(EVENT_DATE, "statement")]


def test_error_entry_is_retried_and_429_is_retryable(tmp_path: Path):
    """A failed fetch is retried on the next run; a 429 is retried within a run, then succeeds."""
    events = _events_file(tmp_path / "events" / "fomc.csv")
    url = fetch_docs.build_document_url(EVENT_DATE, "statement")

    def always_503(_url, **_kwargs):
        return SimpleNamespace(status_code=503, content=b"")

    first = fetch_docs.fetch_documents(
        SNAPSHOT, doc_type="statement", root=tmp_path, events_file=events, get=always_503, **_time_args()
    )
    assert first["files"][f"statement/{EVENT_DATE}.html"]["status"] == "error"

    responses = iter([SimpleNamespace(status_code=429, content=b""), SimpleNamespace(status_code=200, content=b"ok")])
    calls = []

    def rate_limited_then_ok(request_url, **_kwargs):
        calls.append(request_url)
        return next(responses)

    second = fetch_docs.fetch_documents(
        SNAPSHOT, doc_type="statement", root=tmp_path, events_file=events, get=rate_limited_then_ok, **_time_args()
    )
    assert calls == [url, url]
    assert second["files"][f"statement/{EVENT_DATE}.html"]["status"] == "ok"
