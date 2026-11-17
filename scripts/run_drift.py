"""Write a drift report from exported HAR inputs and rolling model losses."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from volab import tracking  # noqa: E402


def _settings() -> dict[str, Any]:
    return {
        "psi_threshold": float(os.environ.get("PSI_THRESHOLD", "0.25")),
        "qlike_ratio_threshold": float(os.environ.get("QLIKE_RATIO_THRESHOLD", "1.10")),
        "qlike_ratio_months": int(os.environ.get("QLIKE_RATIO_MONTHS", "3")),
        # BUILD_GUIDE Issue 27: use trailing 126-session windows to stabilize monthly PSI.
        "psi_window": int(os.environ.get("PSI_WINDOW", "126")),
        "qlike_window": int(os.environ.get("QLIKE_WINDOW", "63")),
    }


def _month_text(values: pd.Series) -> pd.Series:
    """Normalize month labels to sortable YYYY-MM text for reports and JSON."""
    return values.map(lambda value: value.strftime("%Y-%m") if hasattr(value, "strftime") else str(value))


def build_report(results_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Run the author drift functions and write the parquet report and JSON summary."""
    inputs = {
        name: results_dir / name
        for name in ("har_inputs.parquet", "har_inputs_manifest.json", "losses.parquet")
    }
    for path in inputs.values():
        if not path.is_file():
            raise FileNotFoundError(f"missing input file: {path}")

    manifest = json.loads(inputs["har_inputs_manifest.json"].read_text(encoding="utf-8"))
    train_end = manifest["train_end"]
    snapshot_id = manifest["snapshot_id"]
    har_inputs = pd.read_parquet(inputs["har_inputs.parquet"])
    losses = pd.read_parquet(inputs["losses.parquet"])
    settings = _settings()

    # Imported here so this plumbing remains importable while drift.py is still author-owned.
    from volab import drift

    psi = drift.monthly_psi(har_inputs, train_end, window=settings["psi_window"]).copy()
    psi["month"] = _month_text(psi["month"])
    psi["flag"] = psi["psi"] > settings["psi_threshold"]

    ratios = drift.qlike_ratio(losses, window=settings["qlike_window"]).copy()
    ratios["month"] = _month_text(ratios["month"])
    degraded = drift.degradation(
        ratios["qlike_ratio"],
        threshold=settings["qlike_ratio_threshold"],
        months=settings["qlike_ratio_months"],
    )
    ratios["degraded"] = pd.Series(degraded, index=ratios.index).fillna(False).astype(bool)

    psi_report = pd.DataFrame({
        "section": "psi",
        "month": psi["month"],
        "input": psi["input"],
        "psi": psi["psi"],
        "flag": psi["flag"],
        "qlike_ratio": pd.NA,
        "degraded": pd.NA,
    })
    qlike_report = pd.DataFrame({
        "section": "qlike",
        "month": ratios["month"],
        "input": pd.NA,
        "psi": pd.NA,
        "flag": pd.NA,
        "qlike_ratio": ratios["qlike_ratio"],
        "degraded": ratios["degraded"],
    })
    report = pd.concat([psi_report, qlike_report], ignore_index=True)
    report = report[["section", "month", "input", "psi", "flag", "qlike_ratio", "degraded"]].astype({
        "section": "string", "month": "string", "input": "string", "psi": "Float64",
        "flag": "boolean", "qlike_ratio": "Float64", "degraded": "boolean",
    })

    all_months = sorted(set(psi["month"]) | set(ratios["month"]))
    first_month = all_months[0] if all_months else None
    last_month = all_months[-1] if all_months else None
    # Sections can end in different months (har_inputs runs past the last loss date): report each.
    last_psi_month = psi["month"].max() if not psi.empty else None
    last_ratio_month = ratios["month"].max() if not ratios.empty else None
    flagged_counts = psi.loc[psi["flag"]].groupby("input")["month"].nunique().to_dict()
    latest_psi = psi.loc[psi["month"] == last_psi_month] if last_psi_month is not None else psi.iloc[0:0]
    latest_ratios = ratios.loc[ratios["month"] == last_ratio_month] if last_ratio_month is not None else ratios.iloc[0:0]
    latest_status = {
        "psi_month": last_psi_month,
        "qlike_month": last_ratio_month,
        "inputs": {str(row.input): bool(row.flag) for row in latest_psi.itertuples(index=False)},
        "degraded": bool(latest_ratios["degraded"].iloc[-1]) if not latest_ratios.empty else None,
    }
    summary = {
        "train_end": train_end,
        "snapshot_id": snapshot_id,
        "psi_window": settings["psi_window"],
        "qlike_window": settings["qlike_window"],
        "thresholds": {
            "psi": settings["psi_threshold"],
            "qlike_ratio": settings["qlike_ratio_threshold"],
            "qlike_ratio_months": settings["qlike_ratio_months"],
        },
        "first_month_covered": first_month,
        "last_month_covered": last_month,
        "n_months_flagged_per_input": {
            str(name): int(flagged_counts.get(name, 0))
            for name in psi["input"].dropna().unique()
        },
        "months_degraded": ratios.loc[ratios["degraded"], "month"].tolist(),
        "latest_month_status": latest_status,
        "code_commit": tracking.code_commit(),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    report.to_parquet(out_dir / "drift_report.parquet", index=False)
    (out_dir / "drift_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    results_dir = Path(os.environ.get("RESULTS_DIR", str(REPO_ROOT / "results")))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=results_dir)
    args = parser.parse_args(argv)
    try:
        summary = build_report(results_dir, args.out_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
