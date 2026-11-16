"""Export the four M2 HAR-X regressors for downstream drift monitoring."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from volab.models import har  # noqa: E402
from volab.models.baselines import implied_variance  # noqa: E402


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


def export(panel_path: Path, output_path: Path) -> None:
    panel = pd.read_parquet(panel_path)
    output = build_har_inputs(panel)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(output_path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=REPO_ROOT / "data/panel/panel.parquet")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "results/har_inputs.parquet")
    args = parser.parse_args()
    export(args.panel, args.output)
    print(f"written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
