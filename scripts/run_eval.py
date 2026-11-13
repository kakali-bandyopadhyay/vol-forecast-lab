"""Step 7: run M0/M1/M2 end to end on the test period and write results/.

This is the first script that looks at 2020 onwards. Per the build guide it is run once, after the
synthetic tests are green; any later change to the models or to this evaluation is a deviation.

Conventions fixed before the run:
  * QLIKE is computed on the floored totals (§3.4 floors only what goes to QLIKE).
  * MSE, Clark–West and Mincer–Zarnowitz use the raw totals; §3.4 does not reach them.
  * The Holm family at this step holds two tests (M2 vs M1, M1 vs M0). §5.5's full family has
    four; the output is labelled provisional until Steps 8 and 10 exist.
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

from volab.data import REPO_ROOT, load_panel_metadata  # noqa: E402
from volab.losses import qlike, squared_error  # noqa: E402
from volab.models import apply_floor, har, to_total  # noqa: E402
from volab.models.baselines import implied_variance, rolling_mean_rv  # noqa: E402
from volab.splits import walk_forward  # noqa: E402
from volab.stats_tests import (  # noqa: E402
    clark_west,
    diebold_mariano,
    hac_lag,
    mincer_zarnowitz,
)

TEST_START = pd.Timestamp("2020-01-01")
EXPECTED_FOLDS = 82
EXPECTED_FORECASTS = 1703
MODELS = ("M0", "M1", "M2")
PANEL = REPO_ROOT / "data" / "panel" / "panel.parquet"
RESULTS = REPO_ROOT / "results"


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


def planted_leak_drop(panel, features, folds, f_min, clean_qlike_mean: float) -> float:
    """§9.1: refit M1 with tomorrow's realised variance added and measure how far QLIKE falls.

    The extra column goes through the same slot M2 uses for its implied variance, so the fit and
    the forecast apply the identical completeness checks. These forecasts are never written to
    forecasts.parquet, losses.parquet or tests.parquet: the leak exists only to prove the
    evaluation can detect one on the real panel, and the real specification is untouched.
    """
    leaked = panel["rv"].shift(-1).to_numpy()
    target = panel["rv_30d_per_session"].to_numpy()
    n_sessions = panel["n_sessions"].to_numpy()
    realised = panel["rv_30d"].to_numpy()

    pieces = []
    for fold in folds:
        beta = har.fit_fold(features, target, fold.train_idx, leaked)
        per_session = har.forecast_fold(beta, features, fold.forecast_idx, leaked)
        total = to_total(per_session, n_sessions[fold.forecast_idx])
        pieces.append(qlike(realised[fold.forecast_idx], apply_floor(total, f_min).total))

    leaky_mean = float(np.concatenate(pieces).mean())
    return (clean_qlike_mean - leaky_mean) / clean_qlike_mean


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as saved:
        for chunk in iter(lambda: saved.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    metadata = load_panel_metadata()
    digest = sha256_of(PANEL)
    if digest != metadata["sha256"]:
        raise SystemExit("panel hash does not match the manifest; rebuild before evaluating")
    f_min = float(metadata["training"]["f_min"])

    panel = pd.read_parquet(PANEL)
    folds = walk_forward(panel.index, panel["rv_30d"].notna(), TEST_START)

    if len(folds) != EXPECTED_FOLDS:
        raise SystemExit(f"expected {EXPECTED_FOLDS} folds, got {len(folds)}")
    total_forecasts = sum(len(fold.forecast_idx) for fold in folds)
    if total_forecasts != EXPECTED_FORECASTS:
        raise SystemExit(f"expected {EXPECTED_FORECASTS} forecasts, got {total_forecasts}")

    features = har.har_features(panel["rv"])
    per_session_iv = (implied_variance(panel["vix"]) / panel["n_sessions"]).to_numpy()
    m0_per_session = rolling_mean_rv(panel["rv"]).to_numpy()
    target = panel["rv_30d_per_session"].to_numpy()
    n_sessions = panel["n_sessions"].to_numpy()

    raw: dict[str, np.ndarray] = {}
    floored: dict[str, np.ndarray] = {}
    flags: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    dates: list[np.ndarray] = []

    for name in MODELS:
        pieces_raw, pieces_floored, floored_flags = [], [], []
        for fold in folds:
            if name == "M0":
                per_session = m0_per_session[fold.forecast_idx]
                if not np.isfinite(per_session).all():
                    raise SystemExit("M0 produced a NaN per-session forecast")
            else:
                extra = per_session_iv if name == "M2" else None
                beta = har.fit_fold(features, target, fold.train_idx, extra)
                per_session = har.forecast_fold(beta, features, fold.forecast_idx, extra)

            total = to_total(per_session, n_sessions[fold.forecast_idx])
            result = apply_floor(total, f_min)
            pieces_raw.append(result.raw)
            pieces_floored.append(result.total)
            floored_flags.append(result.floored)

        raw[name] = np.concatenate(pieces_raw)
        floored[name] = np.concatenate(pieces_floored)
        flags[name] = np.concatenate(floored_flags).astype(bool)
        counts[name] = int(flags[name].sum())
        dates.append(np.concatenate([fold.forecast_idx for fold in folds]))

    positions = dates[0]
    if len(positions) != EXPECTED_FORECASTS:
        raise SystemExit("forecast blocks do not cover the expected number of dates")

    index = panel.index[positions]
    realised = panel["rv_30d"].to_numpy()[positions]
    lag = hac_lag(len(positions))

    # R1 is already a 30-day total, so it goes straight to the floor without to_total.
    r1_total = implied_variance(panel["vix"]).to_numpy()[positions]
    counts["R1"] = apply_floor(r1_total, f_min).n_floored

    # --- losses -------------------------------------------------------------
    qlike_loss = {name: qlike(realised, floored[name]) for name in MODELS}
    mse_loss = {name: squared_error(realised, raw[name]) for name in MODELS}

    planted_leak = planted_leak_drop(
        panel, features, folds, f_min, float(qlike_loss["M1"].mean())
    )

    losses = pd.concat(
        {
            model: pd.DataFrame(
                {"qlike": qlike_loss[model], "squared_error": mse_loss[model]}, index=index
            )
            for model in MODELS
        },
        names=["model", "date"],
    ).reset_index()

    forecasts = pd.DataFrame(
        {
            "date": np.tile(index, len(MODELS)),
            "model": np.repeat(MODELS, len(index)),
            "realised_30d": np.tile(realised, len(MODELS)),
            "raw_30d": np.concatenate([raw[m] for m in MODELS]),
            "floored_30d": np.concatenate([floored[m] for m in MODELS]),
            "floored": np.concatenate([flags[m] for m in MODELS]),
        }
    )

    # --- tests --------------------------------------------------------------
    rows: list[dict] = []

    cw_stat, cw_p = clark_west(realised, raw["M1"], raw["M2"], lag)
    rows.append({"test": "clark_west", "pair": "M2_vs_M1", "statistic": cw_stat,
                 "pvalue": cw_p, "family": "headline"})

    # §8 requires an interval on the mean loss difference when the headline is a non-rejection.
    # The f_t identity is repeated here only to obtain the mean; the standard error is recovered by
    # inverting the statistic from volab.stats_tests.clark_west, so the HAC estimator itself is not
    # reimplemented and cannot drift from the tested one.
    cw_series = ((realised - raw["M1"]) ** 2
                 - ((realised - raw["M2"]) ** 2 - (raw["M1"] - raw["M2"]) ** 2))
    cw_mean = float(cw_series.mean())
    if cw_stat == 0.0:
        raise SystemExit("clark_west statistic is zero; the interval is undefined")
    cw_se = cw_mean / cw_stat
    cw_interval = [cw_mean - 1.96 * cw_se, cw_mean + 1.96 * cw_se]

    # Holm is applied once, when all four §5.5 tests exist — after Steps 8 and 10. Adjusting a
    # two-test subset now would produce a number that is not the pre-registered statistic and is
    # certain to change, so these rows carry raw p-values and nothing else.
    for pair, (a, b) in {"M2_vs_M1": ("M2", "M1"), "M1_vs_M0": ("M1", "M0")}.items():
        statistic, pvalue = diebold_mariano(qlike_loss[a], qlike_loss[b], lag)
        rows.append({"test": "diebold_mariano", "pair": pair, "statistic": statistic,
                     "pvalue": pvalue, "family": "Q1b/Q1d (raw; Holm deferred)"})

    for model in MODELS:
        mz = mincer_zarnowitz(realised, raw[model], lag)
        rows.append({"test": "mincer_zarnowitz", "pair": model, "statistic": mz["wald"],
                     "pvalue": mz["pvalue"], "family": "calibration"})

    tests = pd.DataFrame(rows)

    # --- write --------------------------------------------------------------
    RESULTS.mkdir(parents=True, exist_ok=True)
    forecasts.to_parquet(RESULTS / "forecasts.parquet", index=False)
    losses.to_parquet(RESULTS / "losses.parquet", index=False)
    tests.to_parquet(RESULTS / "tests.parquet", index=False)
    (RESULTS / "eval_manifest.json").write_text(
        json.dumps(
            {
                "panel_sha256": digest,
                "f_min": f_min,
                "n_folds": len(folds),
                "n_forecasts": int(len(index)),
                "first_forecast": index[0].date().isoformat(),
                "last_forecast": index[-1].date().isoformat(),
                "hac_lag": lag,
                "floored_counts": counts,
                "planted_leak_qlike_drop": planted_leak,
                "clark_west_mean_difference": cw_mean,
                "clark_west_interval_95": cw_interval,
                "clark_west_interval_note": (
                    "mean f_t and mean +/- 1.96 HAC standard errors; f_t > 0 favours M2"
                ),
                "code_commit": git_commit(),
                "dm_convention": "d = loss(first) - loss(second); negative favours the first model",
                "holm_family": "deferred until all four §5.5 tests exist; DM p-values are raw",
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"folds {len(folds)}, forecasts {len(index)}, HAC lag {lag}, f_min {f_min:.6e}")
    print(f"floored: {counts}")
    print(f"planted leak: QLIKE falls {planted_leak:.1%} when tomorrow's RV is added to M1")
    print(f"clark-west mean f_t {cw_mean:+.6e}, 95% interval "
          f"[{cw_interval[0]:+.6e}, {cw_interval[1]:+.6e}]")
    print(f"mean QLIKE: " + ", ".join(f"{m} {qlike_loss[m].mean():.6f}" for m in MODELS))
    print(f"mean MSE  : " + ", ".join(f"{m} {mse_loss[m].mean():.3e}" for m in MODELS))
    print(tests.to_string(index=False))
    print(f"written: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
