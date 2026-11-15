"""Offline checks for the deterministic synthetic snapshot generator."""

from __future__ import annotations

import hashlib
import ast
import json
import subprocess
import sys
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "make_synthetic_fixture.py"
SNAPSHOT_ID = "20240101T000000Z"


def _script_date(script_name: str, constant_name: str) -> pd.Timestamp:
    """Read a date constant directly from a repository script."""
    script_path = REPO_ROOT / "scripts" / script_name
    tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == constant_name for target in node.targets
        ):
            assert isinstance(node.value, ast.Call)
            return pd.Timestamp(*[ast.literal_eval(argument) for argument in node.value.args])
    raise AssertionError(f"{constant_name} was not found in {script_name}")


def _run_generator(output_dir: Path) -> None:
    """Generate fixture data using the active test interpreter."""
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output-dir", str(output_dir)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _file_map(root: Path) -> dict[str, bytes]:
    """Map relative fixture file names to their exact bytes."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_generator_is_byte_identical_and_manifest_hashes_match(tmp_path: Path) -> None:
    """A second run in a temp directory yields the same files and valid source digests."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _run_generator(first)
    _run_generator(second)
    assert _file_map(first) == _file_map(second)

    manifest_path = first / "snapshots" / SNAPSHOT_ID / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"].values():
        for entry in source["files"]:
            path = manifest_path.parent / entry["file_name"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_fixture_covers_window_start_through_2020_without_missing_values() -> None:
    """Generated dates cover every XNYS session from WINDOW_START through 2020."""
    fixture_dir = REPO_ROOT / "fixtures" / "synthetic" / "data"
    if not fixture_dir.exists():
        pytest.skip("synthetic fixture has not been generated")
    snapshot = fixture_dir / "snapshots" / SNAPSHOT_ID
    spy = pd.read_csv(snapshot / "spy.csv")
    cboe = pd.read_csv(snapshot / "vix_cboe.csv")
    fred = pd.read_csv(snapshot / "vix_fred.csv")
    raw_rows = json.loads((snapshot / "spy_raw.json").read_text(encoding="utf-8"))
    assert len(spy) == len(cboe) == len(fred) == len(raw_rows)
    dates = pd.to_datetime(spy["date"], utc=True).dt.tz_convert(None)
    calendar = xcals.get_calendar("XNYS")
    window_start = _script_date("audit_snapshot.py", "WINDOW_START")
    first_session = calendar.date_to_session(window_start, direction="next")
    sessions = calendar.sessions_in_range(first_session, pd.Timestamp("2020-12-31"))
    last_2020_session = calendar.sessions_in_range("2020-01-01", "2020-12-31")[-1]
    assert list(dates.dt.strftime("%Y-%m-%d")) == [pd.Timestamp(day).date().isoformat() for day in sessions]
    assert pd.Timestamp(dates.iloc[0]).date() == pd.Timestamp(sessions[0]).date()
    assert pd.Timestamp(dates.iloc[-1]).date() == pd.Timestamp(last_2020_session).date()
    assert len(spy) == len(sessions)
    assert not spy.isna().any().any()
    assert not cboe.isna().any().any()
    assert not fred.isna().any().any()
    assert all(value is not None for row in raw_rows for value in row.values())
    assert np.isfinite(spy.select_dtypes(include="number").to_numpy()).all()


def test_generated_headers_match_real_snapshot_when_available() -> None:
    """CSV headers mirror the real snapshot when the repository snapshot is present."""
    real_dir = REPO_ROOT / "data" / "snapshots" / "20261112T071104Z"
    if not real_dir.is_dir():
        pytest.skip("real data/snapshots directory is absent")
    synthetic_dir = REPO_ROOT / "fixtures" / "synthetic" / "data" / "snapshots" / SNAPSHOT_ID
    if not synthetic_dir.is_dir():
        pytest.skip("synthetic fixture has not been generated")
    for name in ("spy.csv", "vix_cboe.csv", "vix_fred.csv"):
        real_header = (real_dir / name).open("r", encoding="utf-8-sig").readline().rstrip("\r\n")
        synthetic_header = (synthetic_dir / name).open("r", encoding="utf-8-sig").readline().rstrip("\r\n")
        assert synthetic_header == real_header
