"""Stamp result parquet files with the source snapshot after the Step 12 hash check.

Run only AFTER the MLflow rerun hash check (Step 12), then commit alone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


FILES = ("forecasts", "losses", "mlp_forecasts", "q3_events", "q2_summary", "tests")


def _snapshot_id(manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, dict) or len(snapshots) != 1:
        raise ValueError("snapshot manifest must contain exactly one snapshot")
    snapshot_id = next(iter(snapshots.values())).get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("snapshot manifest has no snapshot_id")
    return snapshot_id


def stamp(results_dir: Path, manifest_path: Path, *, dry_run: bool = False) -> list[Path]:
    snapshot_id = _snapshot_id(manifest_path)
    paths = [results_dir / f"{name}.parquet" for name in FILES]
    frames: list[tuple[Path, pd.DataFrame]] = []

    # Validate all files before writing any, so a late conflict cannot partially stamp results.
    for path in paths:
        if not path.is_file():
            continue
        frame = pd.read_parquet(path)
        if "snapshot_id" in frame.columns:
            values = frame["snapshot_id"].dropna().unique()
            if len(values) > 1 or (len(values) == 1 and values[0] != snapshot_id):
                raise ValueError(f"{path.name} already has a different snapshot_id")
            if len(values) == 0 and not frame.empty:
                raise ValueError(f"{path.name} has a null snapshot_id")
        frames.append((path, frame))

    changed: list[Path] = []
    for path, frame in frames:
        if "snapshot_id" in frame.columns:
            continue
        changed.append(path)
        if not dry_run:
            frame["snapshot_id"] = snapshot_id
            frame.to_parquet(path, index=False)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--manifest", type=Path, default=Path("data/snapshot_manifest.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        changed = stamp(args.results_dir, args.manifest, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")
    if args.dry_run:
        print("would change: " + (", ".join(path.name for path in changed) or "none"))
    else:
        print("changed: " + (", ".join(path.name for path in changed) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
