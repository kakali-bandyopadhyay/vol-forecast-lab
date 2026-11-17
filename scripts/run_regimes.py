"""Descriptive Q1 Clark–West breakdowns by the pre-registered regimes (§8)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from volab.stats_tests import clark_west, hac_lag  # noqa: E402
from volab.window import target_touches_window  # noqa: E402


def build_regime_report(forecasts: pd.DataFrame, panel: pd.DataFrame,
                        low: float, high: float) -> dict:
    """Return aggregate Clark–West cells for VIX terciles and calendar sub-periods."""
    if not np.isfinite([low, high]).all() or low >= high:
        raise ValueError("regime thresholds must be finite and low < high")
    required = {"model", "date", "realised_30d", "raw_30d"}
    missing = required - set(forecasts.columns)
    if missing:
        raise ValueError(f"forecast table is missing columns: {sorted(missing)}")
    f = forecasts.loc[forecasts["model"].isin(["M1", "M2"]),
                      ["model", "date", "realised_30d", "raw_30d"]].copy()
    f["date"] = pd.to_datetime(f["date"])
    if f.duplicated(["date", "model"]).any():
        raise ValueError("forecast table has duplicate date/model rows")
    wide = f.pivot(index="date", columns="model", values=["realised_30d", "raw_30d"]).sort_index()
    if not {"M1", "M2"}.issubset(wide["raw_30d"].columns):
        raise ValueError("forecast table must contain M1 and M2")
    wide = wide.dropna(subset=[("realised_30d", "M1"), ("raw_30d", "M1"),
                               ("raw_30d", "M2")])
    dates = pd.DatetimeIndex(wide.index)
    realised = wide[("realised_30d", "M1")].to_numpy(dtype=float)
    m1 = wide[("raw_30d", "M1")].to_numpy(dtype=float)
    m2 = wide[("raw_30d", "M2")].to_numpy(dtype=float)
    vix = pd.to_numeric(panel["vix"], errors="coerce").reindex(dates)
    valid = vix.notna().to_numpy()
    dates, realised, m1, m2, vix = (dates[valid], realised[valid], m1[valid], m2[valid],
                                    vix.to_numpy(dtype=float)[valid])
    if not len(dates):
        raise ValueError("no M1/M2 forecast dates have an observed VIX")

    full = np.ones(len(dates), dtype=bool)
    ex_window = ~target_touches_window(dates, panel.index)
    vix_labels = np.where(vix < low, "low", np.where(vix < high, "mid", "high"))
    years = dates.year
    calendar_labels = np.where(years == 2020, "2020",
                               np.where((years >= 2021) & (years <= 2023), "2021-2023",
                                        np.where(years >= 2024, "2024-end", "before-2020")))
    dims = (("vix_tercile", vix_labels, ["low", "mid", "high"]),
            ("calendar_subperiod", calendar_labels, ["2020", "2021-2023", "2024-end"]))
    cells = []
    for sample, sample_mask in (("full", full), ("ex_window", ex_window)):
        for dimension, labels, order in dims:
            for label in order:
                mask = sample_mask & (labels == label)
                n = int(mask.sum())
                ft = ((realised[mask] - m1[mask]) ** 2
                      - ((realised[mask] - m2[mask]) ** 2 - (m1[mask] - m2[mask]) ** 2))
                if n >= 2:
                    statistic, pvalue = clark_west(realised[mask], m1[mask], m2[mask], hac_lag(n))
                    mean_ft = float(ft.mean())
                else:
                    statistic, pvalue = None, None
                    mean_ft = float(ft.mean()) if n else None
                cells.append({"sample": sample, "dimension": dimension, "regime": label,
                              "n": n, "mean_f_t": mean_ft, "t": statistic,
                              "p_one_sided": pvalue, "small_n": n < 100})
    return {
        "description": "Descriptive Clark–West breakdown; no Holm adjustment and not a headline test.",
        "pair": "M2_vs_M1", "hac_lag_rule": "max(21, floor(4*(T/100)^(2/9)))",
        "vix_thresholds": {"low_mid": float(low), "mid_high": float(high),
                           "vix_date": "forecast date"},
        "calendar_subperiods": ["2020", "2021-2023", "2024-end"],
        "any_cell_n_lt_100": any(cell["small_n"] for cell in cells),
        "cells": cells,
    }


def main() -> int:
    manifest = json.loads((ROOT / "data/panel/panel_manifest.json").read_text(encoding="utf-8"))
    panel = pd.read_parquet(ROOT / "data/panel/panel.parquet")
    forecasts = pd.read_parquet(ROOT / "results/forecasts.parquet")
    report = build_regime_report(forecasts, panel, manifest["training"]["tercile_low"],
                                 manifest["training"]["tercile_high"])
    out = ROOT / "results/q1_regimes.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"written: {out} ({len(report['cells'])} aggregate cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
