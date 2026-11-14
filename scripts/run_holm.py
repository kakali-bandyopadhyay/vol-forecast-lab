"""Step 10: Q1c tests, final Holm decisions and the M4 regime check.

The script reads the Step 7–10 result files and never refits a model.  The three existing
Diebold–Mariano p-values are carried from `tests.parquet` / `tests_ex_window.parquet` exactly; only
the M4-versus-M2 DM and the M4 Mincer–Zarnowitz tests are computed here.
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

from volab import spread, window  # noqa: E402
from volab.data import REPO_ROOT, load_panel_metadata  # noqa: E402
from volab.losses import qlike  # noqa: E402
from volab.stats_tests import (  # noqa: E402
    diebold_mariano,
    hac_lag,
    holm,
    mincer_zarnowitz,
)

PANEL = REPO_ROOT / "data" / "panel" / "panel.parquet"
RESULTS = REPO_ROOT / "results"
PAIRS = ("M2_vs_M1", "M4_vs_M2", "M1_vs_M3", "M1_vs_M0")
EXISTING_PAIRS = ("M2_vs_M1", "M1_vs_M3", "M1_vs_M0")


def git_commit() -> str:
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


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def _require_committed(relative: str) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise SystemExit(f"{relative} is not committed; commit it first")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(f"{relative} has uncommitted changes; commit it first")


def _decision(adjusted_p: float, statistic: float) -> tuple[str, str]:
    sign = "negative" if statistic < 0 else "positive" if statistic > 0 else "zero"
    return ("reject" if adjusted_p < 0.05 else "not rejected"), sign


def _existing_row(table: pd.DataFrame, pair: str) -> pd.Series:
    selected = table[(table["test"] == "diebold_mariano") & (table["pair"] == pair)]
    if len(selected) != 1:
        raise SystemExit(f"expected one {pair} DM row, found {len(selected)}")
    return selected.iloc[0]


def main() -> int:
    _require_committed("scripts/run_holm.py")
    metadata = load_panel_metadata()
    digest = sha256_of(PANEL)
    if digest != metadata["sha256"]:
        raise SystemExit("panel hash does not match the manifest; rebuild before evaluating")

    panel = pd.read_parquet(PANEL)
    forecasts = pd.read_parquet(RESULTS / "forecasts.parquet")
    losses = pd.read_parquet(RESULTS / "losses.parquet")
    tests = pd.read_parquet(RESULTS / "tests.parquet")
    tests_ex_window = pd.read_parquet(RESULTS / "tests_ex_window.parquet")
    mlp_forecasts = pd.read_parquet(RESULTS / "mlp_forecasts.parquet").sort_values("date")

    m1 = forecasts.loc[forecasts["model"] == "M1"].set_index("date").sort_index()
    dates = pd.DatetimeIndex(m1.index)
    realised = m1["realised_30d"].to_numpy(dtype=float)
    if len(dates) != 1703:
        raise SystemExit(f"expected 1,703 forecast dates, got {len(dates)}")
    if not pd.DatetimeIndex(mlp_forecasts["date"]).equals(dates):
        raise SystemExit("M4 dates do not equal the M1 dates")
    if not np.array_equal(
        mlp_forecasts["realised_30d"].to_numpy(dtype=float), realised
    ):
        raise SystemExit("M4 realised_30d does not equal the M1 realised_30d")

    loss_wide = (
        losses.pivot(index="date", columns="model", values="qlike")
        .sort_index()
        .reindex(dates)
    )
    m2_qlike = loss_wide["M2"].to_numpy(dtype=float)
    m4_qlike = qlike(realised, mlp_forecasts["floored_30d"].to_numpy(dtype=float))
    m4_raw = mlp_forecasts["raw_30d"].to_numpy(dtype=float)

    ex_mask = ~window.target_touches_window(dates, panel.index)
    if int(ex_mask.sum()) != 1632:
        raise SystemExit(f"expected 1,632 ex-window dates, got {int(ex_mask.sum())}")

    low = float(metadata["training"]["tercile_low"])
    high = float(metadata["training"]["tercile_high"])
    vix = panel["vix"].reindex(dates)

    mlp_test_rows = []
    holm_rows = []
    regime_rows = []

    for sample, mask in (("full", np.ones(len(dates), dtype=bool)), ("ex_window", ex_mask)):
        n = int(mask.sum())
        sample_dates = dates[mask]
        sample_realised = realised[mask]
        sample_m2 = m2_qlike[mask]
        sample_m4 = m4_qlike[mask]
        sample_m4_raw = m4_raw[mask]
        lag = hac_lag(n)

        dm_stat, dm_p = diebold_mariano(sample_m4, sample_m2, lag)
        mz = mincer_zarnowitz(sample_realised, sample_m4_raw, lag)
        mlp_test_rows.append({
            "test": "diebold_mariano",
            "pair": "M4_vs_M2",
            "sample": sample,
            "statistic": dm_stat,
            "pvalue": dm_p,
            "family": "Q1c",
        })
        mlp_test_rows.append({
            "test": "mincer_zarnowitz",
            "pair": "M4",
            "sample": sample,
            "statistic": mz["wald"],
            "pvalue": mz["pvalue"],
            "family": "calibration",
        })

        existing_source = tests if sample == "full" else tests_ex_window
        raw_p = {}
        statistic = {}
        for pair in EXISTING_PAIRS:
            row = _existing_row(existing_source, pair)
            raw_p[pair] = float(row["pvalue"])
            statistic[pair] = float(row["statistic"])
        raw_p["M4_vs_M2"] = float(dm_p)
        statistic["M4_vs_M2"] = float(dm_stat)
        if set(raw_p) != set(PAIRS):
            raise SystemExit(f"{sample}: Holm family does not have four tests")

        adjusted = holm(np.array([raw_p[pair] for pair in PAIRS]))
        for pair, adjusted_p in zip(PAIRS, adjusted):
            decision, sign = _decision(float(adjusted_p), statistic[pair])
            holm_rows.append({
                "pair": pair,
                "sample": sample,
                "statistic": statistic[pair],
                "raw_p": raw_p[pair],
                "holm_p": float(adjusted_p),
                "decision": decision,
                "sign": sign,
            })

        labels = spread.regime(vix[mask], low, high).to_numpy()
        for regime in ("low", "mid", "high"):
            selected = labels == regime
            regime_rows.append({
                "sample": sample,
                "regime": regime,
                "n": int(selected.sum()),
                "mean_qlike_m2": float(sample_m2[selected].mean()),
                "mean_qlike_m4": float(sample_m4[selected].mean()),
                "difference_m4_minus_m2": float(
                    sample_m4[selected].mean() - sample_m2[selected].mean()
                ),
            })

    holm_final = pd.DataFrame(holm_rows)
    holm_final["headline_sample"] = ""
    for pair in PAIRS:
        full = holm_final[(holm_final["pair"] == pair) & (holm_final["sample"] == "full")]
        ex = holm_final[
            (holm_final["pair"] == pair) & (holm_final["sample"] == "ex_window")
        ]
        if len(full) != 1 or len(ex) != 1:
            raise SystemExit(f"expected one full and one ex-window row for {pair}")
        f, e = full.iloc[0], ex.iloc[0]
        same = f["decision"] == e["decision"] and (
            f["decision"] == "not rejected" or f["sign"] == e["sign"]
        )
        headline = "full" if same else "ex_window"
        holm_final.loc[full.index, "headline_sample"] = headline
        holm_final.loc[ex.index, "headline_sample"] = headline

    if not holm_final["headline_sample"].isin(["full", "ex_window"]).all():
        raise SystemExit("headline_sample was not assigned for every Holm row")

    if len(mlp_test_rows) != 4:
        raise SystemExit(f"expected four M4 tests, got {len(mlp_test_rows)}")
    if len(holm_final) != 8:
        raise SystemExit(f"expected eight Holm rows, got {len(holm_final)}")
    for sample in ("full", "ex_window"):
        rows = holm_final[holm_final["sample"] == sample]
        if set(rows["pair"]) != set(PAIRS):
            raise SystemExit(f"{sample}: Holm file does not contain four pairs")

    # The existing raw p-values must be carried through exactly.
    for sample, source in (("full", tests), ("ex_window", tests_ex_window)):
        current = holm_final[holm_final["sample"] == sample].set_index("pair")
        for pair in EXISTING_PAIRS:
            if current.loc[pair, "raw_p"] != float(
                _existing_row(source, pair)["pvalue"]
            ):
                raise SystemExit(f"{sample}: {pair} raw p changed")

    mlp_tests = pd.DataFrame(mlp_test_rows)
    mlp_tests.to_parquet(RESULTS / "mlp_tests.parquet", index=False)
    holm_final.to_parquet(RESULTS / "holm_final.parquet", index=False)
    pd.DataFrame(regime_rows).to_parquet(RESULTS / "mlp_regimes.parquet", index=False)

    _write_json(
        RESULTS / "holm_manifest.json",
        {
            "panel_sha256": digest,
            "code_commit": git_commit(),
            "n_full": int(len(dates)),
            "n_ex_window": int(ex_mask.sum()),
            "pairs": list(PAIRS),
            "samples": ["full", "ex_window"],
            "holm_rule": "Holm-adjusted p < 0.05; sign is the sign of d = loss(first) - loss(second)",
            "headline_rule": "full if full and ex-window decisions and signs agree, else ex_window",
            "note": "Q1b-Q1d final Holm replaces the three provisional Step 9 classifications.",
        },
    )

    print(f"n full {len(dates)}, ex-window {int(ex_mask.sum())}")
    print(holm_final.to_string(index=False))
    print(f"written: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
