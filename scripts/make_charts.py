"""Build the four Step 14 summary charts from committed result files.

Licence boundary (ADR 0003): every chart shows aggregates only - mean losses, monthly loss
ratios, yearly means with HAC intervals and regression effects. No price, return,
realised-variance or forecast series is plotted.

Run: python scripts/make_charts.py   (writes figures/results/*.svg and *.png)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUT = ROOT / "figures" / "results"
# DEVIATIONS rule A: forecast dates whose 30-day target touches Feb-Apr 2020.
WINDOW = (pd.Timestamp("2020-01-21"), pd.Timestamp("2020-04-30"))
MODELS = ["M0", "M1", "M2", "M3", "M4"]
LABELS = {"M0": "M0 persistence", "M1": "M1 HAR", "M2": "M2 HAR-X", "M3": "M3 GARCH", "M4": "M4 MLP"}
BLUE, ORANGE, GREY = "#2a6f97", "#e07a1f", "#8a8a8a"


def _qlike(realised: pd.Series, forecast: pd.Series) -> pd.Series:
    ratio = realised / forecast
    return ratio - np.log(ratio) - 1.0


def _in_window(dates: pd.Series) -> pd.Series:
    return dates.between(*WINDOW)


def mean_losses(results: Path = RESULTS) -> pd.DataFrame:
    """Mean QLIKE per model on the full and ex-window samples.

    Computed from the per-date loss files when they are present (author's machine) and written to
    results/loss_summary.json; a public clone without per-date files reads that summary instead.
    """
    summary = results / "loss_summary.json"
    if not (results / "losses.parquet").exists():
        data = json.loads(summary.read_text(encoding="utf-8"))
        out = pd.DataFrame(data["mean_qlike"]).reindex(MODELS)
        out.attrs["n_ex"] = data["n_ex_window"]
        return out
    losses = pd.read_parquet(results / "losses.parquet")[["model", "date", "qlike"]]
    mlp = pd.read_parquet(results / "mlp_forecasts.parquet")
    m4 = pd.DataFrame(
        {"model": "M4", "date": mlp["date"], "qlike": _qlike(mlp["realised_30d"], mlp["floored_30d"])}
    )
    frame = pd.concat([losses, m4], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    full = frame.groupby("model")["qlike"].mean()
    ex = frame[~_in_window(frame["date"])].groupby("model")["qlike"].mean()
    n_ex = int((~_in_window(frame.loc[frame.model == "M1", "date"])).sum())
    out = pd.DataFrame({"full": full, "ex_window": ex}).reindex(MODELS)
    out.attrs["n_ex"] = n_ex
    summary.write_text(
        json.dumps({"n_full": int((frame.model == "M1").sum()), "n_ex_window": n_ex,
                    "mean_qlike": out.round(6).to_dict()}, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return out


def _save(fig: plt.Figure, name: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.svg", bbox_inches="tight")
    fig.savefig(out / f"{name}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_forecast_comparison(results: Path, out: Path) -> pd.DataFrame:
    table = mean_losses(results)
    fig, ax = plt.subplots(figsize=(7.5, 4))
    x = np.arange(len(MODELS))
    ax.bar(x - 0.2, table["full"], 0.4, label="Full sample (n = 1,703)", color=BLUE)
    ax.bar(x + 0.2, table["ex_window"], 0.4, label=f"Ex Feb–Apr 2020 (n = {table.attrs['n_ex']:,})", color=ORANGE)
    for i, m in enumerate(MODELS):
        ax.text(i - 0.2, table.loc[m, "full"] + 0.005, f"{table.loc[m, 'full']:.3f}", ha="center", fontsize=8)
        ax.text(i + 0.2, table.loc[m, "ex_window"] + 0.005, f"{table.loc[m, 'ex_window']:.3f}", ha="center", fontsize=8)
    ax.set_xticks(x, [LABELS[m] for m in MODELS])
    ax.set_ylabel("Mean QLIKE (lower is better)")
    ax.set_title("Forecast comparison: 30-day S&P 500 variance, test period 2020-01-02 to 2026-10-12", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.01, -0.04, "No QLIKE difference is detected after the Holm adjustment (FINDINGS, Step 10).", fontsize=8, color=GREY)
    _save(fig, "fig1_forecast_comparison", out)
    return table


def chart_errors_over_time(results: Path, out: Path) -> pd.DataFrame:
    drift = pd.read_parquet(results / "drift_report.parquet")
    q = drift[drift["section"] == "qlike"].dropna(subset=["qlike_ratio"]).copy()
    q["month"] = pd.PeriodIndex(q["month"].astype(str), freq="M").to_timestamp()
    q["qlike_ratio"] = q["qlike_ratio"].astype(float)
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    ax.plot(q["month"], q["qlike_ratio"], color=BLUE, lw=1.4, label="QLIKE ratio M2/M1, trailing 63 sessions")
    deg = q[q["degraded"].astype(bool)]
    ax.scatter(deg["month"], deg["qlike_ratio"], color=ORANGE, zorder=3, s=18, label="Degraded month-end (3 in a row > 1.10)")
    ax.axhline(1.10, color=GREY, ls="--", lw=1)
    ax.axhline(1.0, color=GREY, lw=0.6)
    ax.set_ylabel("Ratio (> 1 = HAR-X worse)")
    ax.set_title("Errors over time: HAR-X relative to HAR at each month-end", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, "fig2_errors_over_time", out)
    return q


def chart_spread_premium(results: Path, out: Path) -> pd.DataFrame:
    q2 = pd.read_parquet(results / "q2_summary.parquet")
    rows = q2[(q2["sample"] == "ex_window") & (q2["group"] == "year")].copy()
    rows["se"] = rows["mean"] / rows["hac_t"]
    years = sorted(rows["label"].unique())
    x = np.arange(len(years))
    fig, ax = plt.subplots(figsize=(7.5, 4))
    for offset, var, colour, name in [(-0.2, "spread", BLUE, "Ex-ante spread (VIX-implied − HAR forecast)"),
                                      (0.2, "premium", ORANGE, "Ex-post premium (VIX-implied − realised)")]:
        r = rows[rows["variable"] == var].set_index("label").reindex(years)
        ax.bar(x + offset, r["mean"] * 1e3, 0.4, yerr=1.96 * r["se"].abs() * 1e3, capsize=3, color=colour, label=name)
    ax.axhline(0, color=GREY, lw=0.8)
    ax.set_xticks(x, years)
    ax.set_ylabel("Mean, 30-day total variance × 10⁻³")
    ax.set_title("Implied vs realised: yearly means with 95% HAC intervals (ex Feb–Apr 2020)", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.01, -0.04, "Descriptive only: no signal, threshold or trade rule is drawn from either series (PREREGISTRATION §6).", fontsize=8, color=GREY)
    _save(fig, "fig3_spread_premium_by_year", out)
    return rows


def chart_event_effects(results: Path, out: Path) -> pd.DataFrame:
    ev = pd.read_parquet(results / "q3_events.parquet")
    keep = [("cpi", "on", "CPI, overnight (H-CPI)"), ("fomc", "oc", "FOMC, session (H-FOMC)"),
            ("fomc", "on", "FOMC, overnight"), ("cpi", "oc", "CPI, session")]
    rows = []
    for term, comp, label in keep:
        r = ev[(ev.term == term) & (ev.component == comp) & (ev.run == "primary")].iloc[0]
        rows.append({"label": label, "effect": r.effect, "lo": r.effect_ci_low, "hi": r.effect_ci_high, "p_holm": r.p_holm})
    table = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    y = np.arange(len(table))[::-1]
    colours = [ORANGE if p < 0.05 else GREY for p in table["p_holm"]]
    for yi, colour, (_, r) in zip(y, colours, table.iterrows()):
        ax.errorbar(r.effect * 100, yi, xerr=[[(r.effect - r.lo) * 100], [(r.hi - r.effect) * 100]],
                    fmt="none", ecolor=colour, capsize=3)
    ax.scatter(table["effect"] * 100, y, color=colours, zorder=3)
    for yi, (_, r) in zip(y, table.iterrows()):
        ax.text(r.hi * 100 + 4, yi, f"{r.effect:+.0%}  (Holm p = {r.p_holm:.3f})", va="center", fontsize=8)
    ax.axvline(0, color=GREY, lw=0.8)
    ax.set_yticks(y, table["label"])
    ax.set_xlabel("Effect on daily variance component, % (95% CI)")
    ax.set_xlim(-70, 230)
    ax.set_title("Event variance split: CPI and FOMC days, 2010-02-04 to 2026-11-11 (n = 4,219)", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, "fig4_event_variance_split", out)
    return table


def main() -> int:
    t1 = chart_forecast_comparison(RESULTS, OUT)
    chart_errors_over_time(RESULTS, OUT)
    chart_spread_premium(RESULTS, OUT)
    t4 = chart_event_effects(RESULTS, OUT)
    print(t1.round(6).to_string())
    print(t4.round(4).to_string())
    print(f"wrote 4 charts to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
