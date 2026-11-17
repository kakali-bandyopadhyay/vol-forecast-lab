"""Aggregate-only §9.5 primary-proxy check on the initial training window."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TRAINING_END = pd.Timestamp("2015-12-31")


def build_proxy_check(panel: pd.DataFrame, training_end=TRAINING_END) -> dict:
    required = {"rv", "rv_close_to_close", "cross_term"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel is missing columns: {sorted(missing)}")
    training = panel.loc[pd.to_datetime(panel.index) <= pd.Timestamp(training_end),
                         ["rv", "rv_close_to_close", "cross_term"]].dropna()
    if training.empty:
        raise ValueError("training window has no complete proxy rows")
    mean_r2 = float(training["rv_close_to_close"].mean())
    if not np.isfinite(mean_r2) or mean_r2 == 0.0:
        raise ValueError("training-window mean r-squared must be finite and non-zero")
    return {
        "section": "PREREGISTRATION §9.5",
        "training_end": pd.Timestamp(training_end).date().isoformat(),
        "n": int(len(training)),
        "mean_rv_primary": float(training["rv"].mean()),
        "mean_r_squared": mean_r2,
        "mean_rv_primary_over_mean_r_squared": float(training["rv"].mean()) / mean_r2,
        "mean_cross_term": float(training["cross_term"].mean()),
        "cross_term_share_of_mean_r_squared": float(training["cross_term"].mean()) / mean_r2,
        "definitions": {"rv_primary": "r_on^2 + Garman-Klass",
                        "r_squared": "(r_on + r_oc)^2",
                        "cross_term": "2*r_on*r_oc"},
    }


def main() -> int:
    panel = pd.read_parquet(ROOT / "data/panel/panel.parquet")
    result = build_proxy_check(panel)
    out = ROOT / "results/proxy_check.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"written: {out}; proxy ratio {result['mean_rv_primary_over_mean_r_squared']:.6f}; "
          f"cross-term share {result['cross_term_share_of_mean_r_squared']:.6%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
