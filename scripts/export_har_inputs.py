"""Export the four M2 HAR-X regressors for downstream drift monitoring.

Also writes results/har_inputs_manifest.json with `train_end` (the last training session of the
first walk-forward fold) and the snapshot_id, so the drift job and the k8s CronJob can freeze the
PSI bin edges without the git-ignored panel. Rows after train_end and before TEST_START are the
30-day gap: neither training nor test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from volab.models import har  # noqa: E402
from volab.models.baselines import implied_variance  # noqa: E402
from volab.splits import walk_forward  # noqa: E402

TEST_START = pd.Timestamp("2020-01-01")  # PREREGISTRATION §5.1; must equal run_eval.TEST_START (tested)


def build_har_inputs(panel: pd.DataFrame) -> pd.DataFrame:
    """Build the M2 feature table with the same HAR and implied variance builders as run_eval."""
    features = har.har_features(panel["rv"])
    per_session_iv = implied_variance(panel["vix"]) / panel["n_sessions"]
    matrix = har.design(features, per_session_iv)
    return pd.DataFrame(
        {
            "date": panel.index,
            "rv_d": matrix[:, 0],
            "rv_w": matrix[:, 1],
            "rv_m": matrix[:, 2],
            "iv_ps": matrix[:, 3],
        }
    )


def first_fold_train_end(panel: pd.DataFrame, test_start: pd.Timestamp = TEST_START) -> pd.Timestamp:
    """Last training session of the first fold, using the same splitter and target mask as run_eval."""
    folds = walk_forward(panel.index, panel["rv_30d"].notna(), test_start)
    if not folds or folds[0].train_idx.size == 0:
        raise ValueError("first fold has no training rows")
    return pd.Timestamp(panel.index[folds[0].train_idx[-1]])


def snapshot_id(manifest_path: Path) -> str:
    """The single snapshot_id in data/snapshot_manifest.json (same rule as stamp_snapshot.py)."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, dict) or len(snapshots) != 1:
        raise ValueError("snapshot manifest must contain exactly one snapshot")
    value = next(iter(snapshots.values())).get("snapshot_id")
    if not isinstance(value, str) or not value:
        raise ValueError("snapshot manifest has no snapshot_id")
    return value


def export(panel_path: Path, output_path: Path, snapshot_manifest: Path, manifest_path: Path) -> None:
    panel = pd.read_parquet(panel_path)
    output = build_har_inputs(panel)
    manifest = {
        "train_end": first_fold_train_end(panel).date().isoformat(),
        "test_start": TEST_START.date().isoformat(),
        "snapshot_id": snapshot_id(snapshot_manifest),
        "rows": int(len(output)),
        "source": "scripts/export_har_inputs.py; train_end = last train row of walk_forward fold 0",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(output_path, index=False)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=REPO_ROOT / "data/panel/panel.parquet")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "results/har_inputs.parquet")
    parser.add_argument("--snapshot-manifest", type=Path, default=REPO_ROOT / "data/snapshot_manifest.json")
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "results/har_inputs_manifest.json")
    args = parser.parse_args()
    export(args.panel, args.output, args.snapshot_manifest, args.manifest)
    print(f"written: {args.output}, {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
