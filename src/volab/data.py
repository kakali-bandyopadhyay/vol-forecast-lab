"""Load verified source files from immutable data snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    """Compute a saved file's SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as saved_file:
        for chunk in iter(lambda: saved_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_file(snapshot_id: str, source_name: str, file_name: str) -> Path:
    """Return a source path only after verifying its manifest digest."""
    directory = REPO_ROOT / "data" / "snapshots" / snapshot_id
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("snapshot_id") != snapshot_id:
        raise ValueError(f"Snapshot id mismatch for {snapshot_id}")
    source: dict[str, Any] = manifest["sources"][source_name]
    if source.get("status") != "available":
        raise ValueError(f"Source {source_name} is unavailable in snapshot {snapshot_id}")
    matching = [entry for entry in source.get("files", []) if entry.get("file_name") == file_name]
    if len(matching) != 1:
        raise ValueError(f"Manifest does not identify {file_name} for {source_name}")
    path = directory / file_name
    if not path.is_file() or _sha256(path) != matching[0].get("sha256"):
        raise ValueError(f"SHA-256 mismatch for {path.name}")
    return path


def _date_index(frame: pd.DataFrame, date_column: str) -> pd.DataFrame:
    """Set a sorted, timezone-naive date index without aligning or filling rows."""
    if date_column not in frame.columns:
        raise ValueError(f"Missing date column: {date_column}")
    dates = pd.to_datetime(frame.pop(date_column), utc=True, errors="raise").dt.tz_localize(None)
    frame.index = pd.DatetimeIndex(dates, name="date")
    return frame.sort_index()


def load_spy(snapshot_id: str) -> pd.DataFrame:
    """Load the SPY CSV with raw and adjusted OHLC fields after hash verification."""
    path = _verified_file(snapshot_id, "spy", "spy.csv")
    frame = pd.read_csv(path)
    return _date_index(frame, "date")


def load_vix(snapshot_id: str) -> pd.DataFrame:
    """Load Cboe's saved VIX CSV after hash verification."""
    path = _verified_file(snapshot_id, "vix_cboe", "vix_cboe.csv")
    frame = pd.read_csv(path)
    date_column = next((column for column in frame.columns if column.strip().lower() == "date"), frame.columns[0])
    return _date_index(frame, date_column)


def load_vix_fred(snapshot_id: str) -> pd.DataFrame:
    """Load the saved FRED VIXCLS CSV after hash verification."""
    path = _verified_file(snapshot_id, "vix_fred", "vix_fred.csv")
    frame = pd.read_csv(path)
    date_column = next((column for column in frame.columns if column.strip().lower() in {"observation_date", "date"}), frame.columns[0])
    return _date_index(frame, date_column)


def load_panel_metadata(panel_dir: str | Path | None = None) -> dict[str, Any]:
    """Read the frozen panel manifest — the single source for §3.4 F_min and the §3.5 terciles.

    The panel itself is derived and git-ignored, and its bytes move with the pyarrow version, so
    model code must take these constants from here rather than from a second hard-coded copy.
    """
    directory = Path(panel_dir) if panel_dir is not None else REPO_ROOT / "data" / "panel"
    return json.loads((directory / "panel_manifest.json").read_text(encoding="utf-8"))
