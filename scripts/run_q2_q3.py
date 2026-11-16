"""Step 9: Q2 (spread/premium), Q3 (events) and the Feb–Apr 2020 window check.

This script reads the frozen panel and the Step 7–8 result files; it never refits a model.  It
first reproduces `results/tests.parquet` exactly from `forecasts.parquet` and `losses.parquet`, so
any later number is traceable to the same inputs.  It then runs the same tests on the 1,632
ex-window forecasts, summarises Q2, and fits the Q3 event regressions.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from volab import events, spread, window  # noqa: E402
from volab.data import REPO_ROOT, load_panel_metadata  # noqa: E402
from volab.models.baselines import implied_variance  # noqa: E402
from volab.stats_tests import (  # noqa: E402
    clark_west,
    diebold_mariano,
    hac_lag,
    holm,
    mincer_zarnowitz,
)

MODELS = ("M0", "M1", "M2", "M3")
PANEL = REPO_ROOT / "data" / "panel" / "panel.parquet"
EVENTS = REPO_ROOT / "data" / "events"
RESULTS = REPO_ROOT / "results"

EXPECTED_FORECASTS = 1703
EXPECTED_EX_WINDOW = 1632
EXPECTED_REMOVED = 71
EXPECTED_Q3_OBS = 4219
EXPECTED_Q3_COUNTS = {"cpi": 199, "fomc": 133, "dual": 10, "emg": 5}


def git_commit() -> str:
    """The commit the results were produced from, or "unknown" outside a work tree."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as saved:
        for chunk in iter(lambda: saved.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recompute_tests(realised, raw, qlike_loss, lag: int) -> pd.DataFrame:
    """The same eight §5.5 rows run_eval writes, in the same order."""
    rows: list[dict] = []

    cw_stat, cw_p = clark_west(realised, raw["M1"], raw["M2"], lag)
    rows.append({"test": "clark_west", "pair": "M2_vs_M1", "statistic": cw_stat,
                 "pvalue": cw_p, "family": "headline"})

    for pair, (a, b) in {
        "M2_vs_M1": ("M2", "M1"),
        "M1_vs_M0": ("M1", "M0"),
        "M1_vs_M3": ("M1", "M3"),
    }.items():
        statistic, pvalue = diebold_mariano(qlike_loss[a], qlike_loss[b], lag)
        rows.append({"test": "diebold_mariano", "pair": pair, "statistic": statistic,
                     "pvalue": pvalue, "family": "Q1b/Q1d (raw; Holm deferred)"})

    for model in MODELS:
        mz = mincer_zarnowitz(realised, raw[model], lag)
        rows.append({"test": "mincer_zarnowitz", "pair": model, "statistic": mz["wald"],
                     "pvalue": mz["pvalue"], "family": "calibration"})

    return pd.DataFrame(rows)


def _decision(test: str, statistic: float, pvalue: float) -> str:
    if float(pvalue) >= 0.05:
        return "not rejected"
    if test == "diebold_mariano":
        return "reject; first model better" if statistic < 0 else "reject; second model better"
    return "reject"


def _signed_decision(statistic: float, pvalue: float, noun: str) -> str:
    """Reject/not-reject with the sign of the estimated statistic."""
    direction = "positive" if statistic > 0 else "negative" if statistic < 0 else "zero"
    prefix = "reject" if float(pvalue) < 0.05 else "not rejected"
    return f"{prefix}; {noun} {direction}"


def main() -> int:
    metadata = load_panel_metadata()
    digest = sha256_of(PANEL)
    if digest != metadata["sha256"]:
        raise SystemExit("panel hash does not match the manifest; rebuild before evaluating")

    panel = pd.read_parquet(PANEL)
    forecasts = pd.read_parquet(RESULTS / "forecasts.parquet")
    losses = pd.read_parquet(RESULTS / "losses.parquet")
    existing_tests = pd.read_parquet(RESULTS / "tests.parquet")

    forecast_wide = forecasts.pivot(index="date", columns="model", values="raw_30d").sort_index()
    loss_wide = losses.pivot(index="date", columns="model", values="qlike").sort_index()
    if set(MODELS) - set(forecast_wide.columns) or set(MODELS) - set(loss_wide.columns):
        raise SystemExit("result files do not contain every model")

    dates = pd.DatetimeIndex(forecast_wide.index)
    loss_wide = loss_wide.reindex(dates)
    realised_series = (
        forecasts.loc[forecasts["model"] == "M0"]
        .set_index("date")["realised_30d"]
        .sort_index()
    )
    if not realised_series.index.equals(dates):
        raise SystemExit("forecast dates are not identical across models")

    raw = {model: forecast_wide[model].to_numpy(dtype=float) for model in MODELS}
    qlike_loss = {model: loss_wide[model].to_numpy(dtype=float) for model in MODELS}
    realised = realised_series.to_numpy(dtype=float)

    for model in MODELS:
        model_realised = (
            forecasts.loc[forecasts["model"] == model]
            .set_index("date")["realised_30d"]
            .sort_index()
        )
        if not np.array_equal(model_realised.to_numpy(dtype=float), realised):
            raise SystemExit(f"realised_30d differs across models at {model}")

    if len(dates) != EXPECTED_FORECASTS:
        raise SystemExit(f"expected {EXPECTED_FORECASTS} forecasts, got {len(dates)}")

    # --- sanity: reproduce results/tests.parquet from the result files ----------
    lag = hac_lag(len(dates))
    recomputed = recompute_tests(realised, raw, qlike_loss, lag)
    full = recomputed.reset_index(drop=True)
    saved = existing_tests.reset_index(drop=True)
    if not full[["test", "pair"]].equals(saved[["test", "pair"]]):
        raise SystemExit("recomputed test/pair rows do not match results/tests.parquet")

    largest_difference = 0.0
    for column in ("statistic", "pvalue"):
        left = full[column].to_numpy(dtype=float)
        right = saved[column].to_numpy(dtype=float)
        difference = float(np.max(np.abs(left - right)))
        largest_difference = max(largest_difference, difference)
        if not np.allclose(left, right, rtol=1e-12, atol=0):
            raise SystemExit(
                f"recomputed {column} does not match results/tests.parquet; "
                f"largest |difference| {difference:.3e}"
            )
    print(f"sanity check: largest statistic/pvalue difference {largest_difference:.3e}")

    # --- §8 window check -------------------------------------------------------
    ex_mask = ~window.target_touches_window(dates, panel.index)
    removed = int((~ex_mask).sum())
    if int(ex_mask.sum()) != EXPECTED_EX_WINDOW or removed != EXPECTED_REMOVED:
        raise SystemExit(
            f"window rule gave {int(ex_mask.sum())} ex-window and {removed} removed forecasts"
        )

    ex_tests = recompute_tests(
        realised[ex_mask],
        {model: raw[model][ex_mask] for model in MODELS},
        {model: qlike_loss[model][ex_mask] for model in MODELS},
        hac_lag(int(ex_mask.sum())),
    )
    ex_tests.to_parquet(RESULTS / "tests_ex_window.parquet", index=False)

    merged = existing_tests.merge(ex_tests, on=["test", "pair"], suffixes=("_full", "_ex"))
    rule_rows = []
    for row in merged.itertuples(index=False):
        full_decision = _decision(row.test, row.statistic_full, row.pvalue_full)
        ex_decision = _decision(row.test, row.statistic_ex, row.pvalue_ex)
        same = full_decision == ex_decision
        provisional = row.test == "diebold_mariano" and row.pair in {
            "M2_vs_M1", "M1_vs_M0", "M1_vs_M3"
        }
        rule_rows.append({
            "test": row.test,
            "pair": row.pair,
            "statistic_full": row.statistic_full,
            "pvalue_full": row.pvalue_full,
            "decision_full": full_decision,
            "statistic_ex_window": row.statistic_ex,
            "pvalue_ex_window": row.pvalue_ex,
            "decision_ex_window": ex_decision,
            "same": bool(same),
            "headline_sample": "full" if same else "ex_window",
            "provisional": bool(provisional),
        })

    # --- Q2 -------------------------------------------------------------------
    iv = implied_variance(panel["vix"]).reindex(dates)
    rv30 = panel["rv_30d"].reindex(dates)
    if not np.array_equal(realised, rv30.to_numpy(dtype=float)):
        raise SystemExit("forecasts.parquet realised_30d does not equal panel rv_30d")

    low = float(metadata["training"]["tercile_low"])
    high = float(metadata["training"]["tercile_high"])

    def labels_for(subset_dates: pd.DatetimeIndex, sample: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "year": subset_dates.year,
                "regime": spread.regime(
                    panel.loc[subset_dates, "vix"], low, high
                ).to_numpy(),
                "sample": sample,
            },
            index=subset_dates,
        )

    spread_values = spread.ex_ante_spread(iv, raw["M1"])
    premium_values = spread.ex_post_premium(iv, rv30)

    q2_parts = []
    for variable, values in (("spread", spread_values), ("premium", premium_values)):
        full_summary = spread.summarise(values, labels_for(dates, "full"))
        ex_summary = spread.summarise(
            values[ex_mask], labels_for(dates[ex_mask], "ex_window")
        )
        combined = pd.concat([full_summary, ex_summary], ignore_index=True)
        combined.insert(0, "variable", variable)
        q2_parts.append(combined)
    q2_summary = pd.concat(q2_parts, ignore_index=True)
    q2_summary.to_parquet(RESULTS / "q2_summary.parquet", index=False)

    q2_counts = q2_summary.loc[q2_summary["group"] == "all", ["variable", "sample", "n"]]
    if q2_counts.loc[q2_counts["sample"] == "full", "n"].tolist() != [1703, 1703]:
        raise SystemExit("Q2 full-sample counts are wrong")
    if q2_counts.loc[q2_counts["sample"] == "ex_window", "n"].tolist() != [1632, 1632]:
        raise SystemExit("Q2 ex-window counts are wrong")

    q2_all = q2_summary[q2_summary["group"] == "all"].set_index(["variable", "sample"])
    for variable in ("spread", "premium"):
        full = q2_all.loc[(variable, "full")]
        ex = q2_all.loc[(variable, "ex_window")]
        full_decision = _signed_decision(
            float(full["mean"]), float(full["p_two_sided"]), "mean"
        )
        ex_decision = _signed_decision(
            float(ex["mean"]), float(ex["p_two_sided"]), "mean"
        )
        same = full_decision == ex_decision
        rule_rows.append({
            "test": "q2",
            "pair": f"{variable}_all",
            "statistic_full": float(full["mean"]),
            "pvalue_full": float(full["p_two_sided"]),
            "decision_full": full_decision,
            "statistic_ex_window": float(ex["mean"]),
            "pvalue_ex_window": float(ex["p_two_sided"]),
            "decision_ex_window": ex_decision,
            "same": bool(same),
            "headline_sample": "full" if same else "ex_window",
            "provisional": False,
        })

    # --- Q3 -------------------------------------------------------------------
    fomc_dates, cpi_dates = events.load_event_dates(EVENTS)
    indicator = events.indicators(
        panel.index, fomc_dates, cpi_dates, events.EMERGENCY_CONTROL
    )
    log_rv = events.lagged_rv(panel["rv"])
    rows_primary = log_rv.notna().all(axis=1).to_numpy()
    if int(rows_primary.sum()) != EXPECTED_Q3_OBS:
        raise SystemExit(f"expected {EXPECTED_Q3_OBS} Q3 rows, got {int(rows_primary.sum())}")

    primary_indicator = indicator.loc[rows_primary]
    q3_counts = {
        "cpi": int(primary_indicator["cpi"].sum()),
        "fomc": int(primary_indicator["fomc"].sum()),
        "dual": int(
            ((primary_indicator["cpi"] == 1) & (primary_indicator["fomc"] == 1)).sum()
        ),
        "emg": int(primary_indicator["emg"].sum()),
    }
    if q3_counts != EXPECTED_Q3_COUNTS:
        raise SystemExit(f"Q3 event counts {q3_counts} != {EXPECTED_Q3_COUNTS}")

    in_window = window.session_in_window(panel.index)
    rows_ex_window = rows_primary & ~in_window
    rows_2020 = rows_primary & (panel.index.year >= 2020)
    dual = ((indicator["cpi"] == 1) & (indicator["fomc"] == 1)).to_numpy()
    rows_no_dual = rows_primary & ~dual

    def c_for(component: str) -> float:
        values = events.component_values(panel, component).to_numpy(dtype=float)
        return 0.01 * float(np.median(values[rows_primary]))

    c_k = {"on": c_for("on"), "oc": c_for("oc"), "gk": c_for("gk")}

    q3_frames = []
    for component in ("on", "oc"):
        q3_frames.append(
            events.run(panel, indicator, component, c_k[component],
                       rows=rows_primary, label="primary")
        )
        q3_frames.append(
            events.run(panel, indicator, component, c_k[component],
                       rows=rows_ex_window, label="ex_window")
        )
        q3_frames.append(
            events.run(panel, indicator, component, c_k[component],
                       rows=rows_2020, label="2020+")
        )
        q3_frames.append(
            events.run(panel, indicator, component, c_k[component],
                       rows=rows_primary, weekday=False, label="no_weekday")
        )
        q3_frames.append(
            events.run(panel, indicator, component, c_k[component],
                       rows=rows_no_dual, label="no_dual")
        )
    q3_frames.append(
        events.run(panel, indicator, "gk", c_k["gk"], rows=rows_primary, label="gk_session")
    )
    q3_events = pd.concat(q3_frames, ignore_index=True)

    q3_events["p_holm"] = np.nan
    q3_events["holm_family"] = ""
    for label in ("primary", "ex_window"):
        def row_index(component: str, term: str) -> int:
            selected = q3_events[
                (q3_events["run"] == label)
                & (q3_events["component"] == component)
                & (q3_events["term"] == term)
            ]
            if len(selected) != 1:
                raise SystemExit(f"missing Holm row {label}/{component}/{term}")
            return int(selected.index[0])

        cpi_on = row_index("on", "cpi")
        fomc_on = row_index("on", "fomc")
        fomc_oc = row_index("oc", "fomc")
        cpi_oc = row_index("oc", "cpi")

        pvalues = np.array([
            q3_events.loc[cpi_on, "p_one_sided"],
            q3_events.loc[fomc_oc, "p_one_sided"],
            q3_events.loc[fomc_on, "p_two_sided"],
            q3_events.loc[cpi_oc, "p_two_sided"],
        ])
        adjusted = holm(pvalues)
        for index, adjusted_p in zip((cpi_on, fomc_oc, fomc_on, cpi_oc), adjusted):
            q3_events.loc[index, "p_holm"] = float(adjusted_p)
            q3_events.loc[index, "holm_family"] = label

    q3_events.to_parquet(RESULTS / "q3_events.parquet", index=False)

    vix_frames = []
    for label, mask in (("primary", rows_primary), ("ex_window", rows_ex_window)):
        frame = events.vix_change(panel, indicator, mask)
        frame["run"] = label
        vix_frames.append(frame)
    q3_vix = pd.concat(vix_frames, ignore_index=True)
    q3_vix.to_parquet(RESULTS / "q3_vix.parquet", index=False)

    q3_specs = (
        ("H-CPI_overnight", "on", "cpi"),
        ("H-FOMC_session", "oc", "fomc"),
        ("FOMC_overnight", "on", "fomc"),
        ("CPI_session", "oc", "cpi"),
    )

    def q3_row(component: str, term: str, label: str):
        selected = q3_events[
            (q3_events["run"] == label)
            & (q3_events["component"] == component)
            & (q3_events["term"] == term)
        ]
        if len(selected) != 1:
            raise SystemExit(f"missing Q3 window-rule row {label}/{component}/{term}")
        return selected.iloc[0]

    for pair, component, term in q3_specs:
        full = q3_row(component, term, "primary")
        ex = q3_row(component, term, "ex_window")
        full_decision = _signed_decision(
            float(full["beta"]), float(full["p_holm"]), "effect"
        )
        ex_decision = _signed_decision(
            float(ex["beta"]), float(ex["p_holm"]), "effect"
        )
        same = full_decision == ex_decision
        rule_rows.append({
            "test": "q3_holm",
            "pair": pair,
            "statistic_full": float(full["beta"]),
            "pvalue_full": float(full["p_holm"]),
            "decision_full": full_decision,
            "statistic_ex_window": float(ex["beta"]),
            "pvalue_ex_window": float(ex["p_holm"]),
            "decision_ex_window": ex_decision,
            "same": bool(same),
            "headline_sample": "full" if same else "ex_window",
            "provisional": False,
        })

    window_rule = pd.DataFrame(rule_rows)
    window_rule.to_parquet(RESULTS / "window_rule.parquet", index=False)

    # --- manifest -------------------------------------------------------------
    manifest = {
        "panel_sha256": digest,
        "code_commit": git_commit(),
        "window_rule_note": (
            "DEVIATIONS A (commit 92f9631): 30-day results drop t in the window or with a "
            "target session in it; Q3 and §7.4 drop window sessions; Q3 Holm is applied "
            "within the primary and ex-window runs."
        ),
        "window": {
            "start": window.WINDOW_START.date().isoformat(),
            "end": window.WINDOW_END.date().isoformat(),
            "removed_forecasts": removed,
            "ex_window_forecasts": int(ex_mask.sum()),
        },
        "c_k": {key: float(value) for key, value in c_k.items()},
        "counts": {
            "forecasts": len(dates),
            "ex_window_forecasts": int(ex_mask.sum()),
            "q3_n_obs": int(rows_primary.sum()),
            **q3_counts,
        },
        "hac_lags": {
            "q2": int(hac_lag(len(dates))),
            "q2_ex_window": int(hac_lag(int(ex_mask.sum()))),
            "q3": events.EVENT_HAC_LAG,
            "q3_vix": events.EVENT_HAC_LAG,
        },
    }
    (RESULTS / "q2q3_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"  # Codex fix (P8e)
    )

    print(f"forecasts {len(dates)}, ex-window {int(ex_mask.sum())}, removed {removed}")
    print(f"Q3 rows {int(rows_primary.sum())}, counts {q3_counts}, c_k {c_k}")
    print(f"window rule:\n{window_rule.to_string(index=False)}")
    print(f"written: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
