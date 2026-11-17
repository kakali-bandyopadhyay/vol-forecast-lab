"""Plumbing tests for scripts/make_charts.py (Step 14, Claude-drafted, reviewed)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import make_charts

ROOT = Path(__file__).resolve().parents[1]
NEEDED = ["loss_summary.json", "drift_report.parquet", "q2_summary.parquet", "q3_events.parquet"]


@pytest.fixture()
def aggregate_results(tmp_path: Path) -> Path:
    """A results folder with aggregates only, as in a public clone."""
    src = ROOT / "results"
    if not all((src / name).exists() for name in NEEDED):
        pytest.skip("committed aggregate results not present")
    out = tmp_path / "results"
    out.mkdir()
    for name in NEEDED:
        shutil.copy(src / name, out / name)
    return out


def test_charts_build_from_aggregates_only(aggregate_results: Path, tmp_path: Path) -> None:
    figures = tmp_path / "figures"
    table = make_charts.chart_forecast_comparison(aggregate_results, figures)
    make_charts.chart_errors_over_time(aggregate_results, figures)
    make_charts.chart_spread_premium(aggregate_results, figures)
    make_charts.chart_event_effects(aggregate_results, figures)
    assert len(list(figures.glob("*.svg"))) == 4
    assert len(list(figures.glob("*.png"))) == 4
    summary = json.loads((aggregate_results / "loss_summary.json").read_text(encoding="utf-8"))
    assert table.loc["M1", "full"] == pytest.approx(summary["mean_qlike"]["full"]["M1"])


def test_summary_holds_no_dates(aggregate_results: Path) -> None:
    text = (aggregate_results / "loss_summary.json").read_text(encoding="utf-8")
    assert "20" + "20-" not in text and "date" not in text
