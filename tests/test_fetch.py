"""Offline tests for snapshot creation, verification, and source loaders."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import fetch_data
from volab import data as data_module

SNAPSHOT_ID = "20260102T030405Z"
SPY_JSON = json.dumps([
    {
        "date": "2020-01-02T00:00:00.000Z", "open": 10, "high": 12, "low": 9, "close": 11,
        "adjOpen": 10, "adjHigh": 12, "adjLow": 9, "adjClose": 11, "volume": 100,
        "adjVolume": 100, "divCash": 0, "splitFactor": 1,
    },
    {
        "date": "2020-01-03T00:00:00.000Z", "open": 11, "high": 13, "low": 10, "close": 12,
        "adjOpen": 11, "adjHigh": 13, "adjLow": 10, "adjClose": 12, "volume": 200,
        "adjVolume": 200, "divCash": 0, "splitFactor": 1,
    },
]).encode()
CBOE_CSV = b"DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2020,12,13,11,12.5\n01/03/2020,12.5,14,12,13\n"
FRED_CSV = b"observation_date,VIXCLS\n2020-01-02,12.5\n2020-01-03,13.0\n"


def _response(content: bytes, status: int = 200):
    """Build a tiny response object without performing network I/O."""
    return SimpleNamespace(content=content, status_code=status)


def _fake_get(content_by_url: dict[str, bytes], status_by_url: dict[str, int] | None = None):
    """Return a requests.get substitute that records calls and serves inline payloads."""
    calls: list[tuple[str, dict]] = []
    statuses = status_by_url or {}

    def fake_get(url: str, **kwargs):
        calls.append((url, kwargs))
        return _response(content_by_url[url], statuses.get(url, 200))

    return fake_get, calls


def _successful_snapshot(tmp_path: Path, *, key: str = "test-secret") -> Path:
    """Write a complete synthetic snapshot using mocked HTTP responses."""
    content_by_url = {
        fetch_data.SPY_URL: SPY_JSON,
        fetch_data.CBOE_URL: CBOE_CSV,
        fetch_data.FRED_URL: FRED_CSV,
    }
    fake_get, _ = _fake_get(content_by_url)
    (tmp_path / ".env").write_text(f"TIINGO_API_KEY={key}\n", encoding="utf-8")
    snapshot_id = fetch_data.pull_snapshot(
        root=tmp_path,
        get=fake_get,
        now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )
    assert snapshot_id == SNAPSHOT_ID
    return tmp_path / "data" / "snapshots" / snapshot_id


def test_existing_snapshot_directory_aborts_without_overwrite(tmp_path: Path):
    """An existing snapshot directory prevents any pull attempt."""
    directory = tmp_path / "data" / "snapshots" / SNAPSHOT_ID
    directory.mkdir(parents=True)
    marker = directory / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(FileExistsError):
        fetch_data.pull_snapshot(root=tmp_path, get=lambda *_args, **_kwargs: pytest.fail("network attempted"), now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    assert marker.read_text(encoding="utf-8") == "untouched"


def test_hashes_are_written_and_verify_succeeds(tmp_path: Path):
    """Each successful source file is hashed and the CLI verifier accepts it."""
    directory = _successful_snapshot(tmp_path)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sources"]["spy"]["files"]
    for source in manifest["sources"].values():
        for record in source["files"]:
            assert fetch_data.sha256_file(directory / record["file_name"]) == record["sha256"]
    assert fetch_data.main(["--verify", SNAPSHOT_ID], root=tmp_path) == 0


def test_loaders_return_sorted_date_indexed_source_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Loaders retain source columns and expose sorted timezone-naive dates."""
    _successful_snapshot(tmp_path)
    monkeypatch.setattr(data_module, "REPO_ROOT", tmp_path)
    spy = data_module.load_spy(SNAPSHOT_ID)
    cboe = data_module.load_vix(SNAPSHOT_ID)
    fred = data_module.load_vix_fred(SNAPSHOT_ID)
    assert spy.index.name == "date"
    assert spy.index.tz is None
    assert spy.index.is_monotonic_increasing
    assert {"open", "close", "adjOpen", "adjClose"}.issubset(spy.columns)
    assert cboe.index.is_monotonic_increasing and {"OPEN", "CLOSE"}.issubset(cboe.columns)
    assert fred.index.is_monotonic_increasing and "VIXCLS" in fred.columns


def test_tampered_file_fails_verify_and_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Changed source bytes fail both manifest verification and loading."""
    directory = _successful_snapshot(tmp_path)
    monkeypatch.setattr(data_module, "REPO_ROOT", tmp_path)
    with (directory / "spy.csv").open("ab") as saved_file:
        saved_file.write(b"tampered\n")
    assert fetch_data.main(["--verify", SNAPSHOT_ID], root=tmp_path) == 1
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        data_module.load_spy(SNAPSHOT_ID)


def test_key_never_appears_in_manifest_or_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Authentication uses the header, while serialized manifests and logs omit it."""
    secret = "never-log-this-key"
    fake_get, calls = _fake_get({fetch_data.SPY_URL: SPY_JSON, fetch_data.CBOE_URL: CBOE_CSV, fetch_data.FRED_URL: FRED_CSV})
    monkeypatch.setenv("TIINGO_API_KEY", secret)
    fetch_data.pull_snapshot(root=tmp_path, get=fake_get, now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    assert calls[0][1]["headers"]["Authorization"] == f"Token {secret}"
    persisted = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in tmp_path.rglob("*") if path.is_file() and path.suffix in {".json", ".csv"})
    assert secret not in persisted
    assert secret not in caplog.text


def test_unavailable_source_is_recorded_without_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """A failed Cboe request is recorded as unavailable while FRED stays its own source."""
    monkeypatch.setenv("TIINGO_API_KEY", "test-secret")
    calls: list[str] = []

    def fake_get(url: str, **_kwargs):
        calls.append(url)
        if url == fetch_data.CBOE_URL:
            return _response(b"Service unavailable", 503)
        return _response({fetch_data.SPY_URL: SPY_JSON, fetch_data.FRED_URL: FRED_CSV}[url])

    fetch_data.pull_snapshot(root=tmp_path, get=fake_get, now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    manifest = json.loads((tmp_path / "data" / "snapshots" / SNAPSHOT_ID / "manifest.json").read_text(encoding="utf-8"))
    cboe = manifest["sources"]["vix_cboe"]
    assert cboe["status"] == "unavailable"
    assert cboe["http_status"] == 503
    assert cboe["files"] == []
    assert manifest["sources"]["vix_fred"]["status"] == "available"
    assert manifest["sources"]["vix_fred"]["url"] == fetch_data.FRED_URL
    assert calls.count(fetch_data.CBOE_URL) == fetch_data.MAX_ATTEMPTS == 3
    assert fetch_data.CBOE_URL not in calls[calls.index(fetch_data.FRED_URL):]
    assert "test-secret" not in caplog.text
