from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "stamp_snapshot.py"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    results = tmp_path / "results"
    results.mkdir()
    pd.DataFrame({"date": ["2020-01-01"], "value": [1]}).to_parquet(
        results / "forecasts.parquet", index=False
    )
    manifest = tmp_path / "snapshot_manifest.json"
    manifest.write_text(json.dumps({"snapshots": {"S1": {"snapshot_id": "S1"}}}), encoding="utf-8")
    return results, manifest


def _run(results: Path, manifest: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--results-dir", str(results), "--manifest", str(manifest)],
        capture_output=True,
        text=True,
    )


def test_stamp_preserves_original_columns_and_appends_snapshot_id(tmp_path: Path) -> None:
    results, manifest = _fixture(tmp_path)
    original = pd.read_parquet(results / "forecasts.parquet")
    run = _run(results, manifest)
    assert run.returncode == 0, run.stderr
    stamped = pd.read_parquet(results / "forecasts.parquet")
    pd.testing.assert_frame_equal(stamped[original.columns], original)
    assert list(stamped.columns) == [*original.columns, "snapshot_id"]
    assert stamped["snapshot_id"].tolist() == ["S1"]


def test_stamp_is_idempotent(tmp_path: Path) -> None:
    results, manifest = _fixture(tmp_path)
    assert _run(results, manifest).returncode == 0
    before = (results / "forecasts.parquet").read_bytes()
    second = _run(results, manifest)
    assert second.returncode == 0
    assert (results / "forecasts.parquet").read_bytes() == before


def test_stamp_rejects_mismatched_snapshot_id(tmp_path: Path) -> None:
    results, manifest = _fixture(tmp_path)
    pd.DataFrame({"value": [1], "snapshot_id": ["OLD"]}).to_parquet(
        results / "forecasts.parquet", index=False
    )
    run = _run(results, manifest)
    assert run.returncode != 0
    assert "forecasts.parquet" in run.stderr
