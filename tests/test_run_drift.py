"""Plumbing tests for the drift report command with author functions stubbed."""

from __future__ import annotations

import json
import sys
import types

import numpy as np
import pandas as pd
import volab

from scripts import run_drift


def _install_fake_drift(monkeypatch, *, captured=None):
    fake = types.ModuleType("volab.drift")

    def monthly_psi(frame, train_end, *, window):
        if captured is not None:
            captured["train_end"] = train_end
            captured["psi_window"] = window
        return pd.DataFrame({
            "month": ["2020-01", "2020-02", "2020-01", "2020-02"],
            "input": ["rv_d", "rv_d", "rv_w", "rv_w"],
            "psi": [0.20, 0.30, 0.10, 0.40],
        })

    def qlike_ratio(losses, *, window):
        if captured is not None:
            captured["qlike_window"] = window
        return pd.DataFrame({"month": ["2020-01", "2020-02"], "qlike_ratio": [1.05, 1.25]})

    def degradation(ratios, *, threshold, months):
        if captured is not None:
            captured["degradation_threshold"] = threshold
            captured["degradation_months"] = months
        return pd.Series(ratios > threshold, index=ratios.index)

    fake.monthly_psi = monthly_psi
    fake.qlike_ratio = qlike_ratio
    fake.degradation = degradation
    monkeypatch.setitem(sys.modules, "volab.drift", fake)
    monkeypatch.setattr(volab, "drift", fake, raising=False)
    return fake


def _write_inputs(directory):
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": pd.date_range("2020-01-01", periods=4), "rv_d": np.arange(4)}).to_parquet(
        directory / "har_inputs.parquet", index=False
    )
    (directory / "har_inputs_manifest.json").write_text(
        json.dumps({"train_end": "2019-12-31", "snapshot_id": "snapshot-1"}), encoding="utf-8"
    )
    pd.DataFrame({"model": ["M1", "M2"], "date": pd.to_datetime(["2020-01-01", "2020-01-01"]), "qlike": [1.0, 1.1]}).to_parquet(
        directory / "losses.parquet", index=False
    )


def test_writes_report_summary_and_uses_environment_thresholds(monkeypatch, tmp_path, capsys):
    results = tmp_path / "inputs"
    output = tmp_path / "emptydir"
    _write_inputs(results)
    captured = {}
    _install_fake_drift(monkeypatch, captured=captured)
    monkeypatch.setenv("RESULTS_DIR", str(results))
    monkeypatch.setenv("PSI_THRESHOLD", "0.25")
    monkeypatch.setenv("QLIKE_RATIO_THRESHOLD", "1.15")
    monkeypatch.setenv("QLIKE_RATIO_MONTHS", "2")
    monkeypatch.setenv("PSI_WINDOW", "126")
    monkeypatch.setenv("QLIKE_WINDOW", "63")

    assert run_drift.main(["--out-dir", str(output)]) == 0
    report = pd.read_parquet(output / "drift_report.parquet")
    summary = json.loads((output / "drift_summary.json").read_text(encoding="utf-8"))
    assert set(report["section"]) == {"psi", "qlike"}
    assert {"section", "month", "input", "psi", "flag", "qlike_ratio", "degraded"} == set(report.columns)
    assert report.loc[(report.section == "psi") & (report.input == "rv_d"), "flag"].tolist() == [False, True]
    assert report.loc[report.section == "qlike", "degraded"].tolist() == [False, True]
    assert report.loc[report.section == "psi", "qlike_ratio"].isna().all()
    assert summary["train_end"] == "2019-12-31"
    assert summary["snapshot_id"] == "snapshot-1"
    assert summary["psi_window"] == 126 and summary["qlike_window"] == 63
    assert summary["thresholds"] == {"psi": 0.25, "qlike_ratio": 1.15, "qlike_ratio_months": 2}
    assert summary["n_months_flagged_per_input"] == {"rv_d": 1, "rv_w": 1}
    assert summary["months_degraded"] == ["2020-02"]
    assert summary["latest_month_status"] == {"psi_month": "2020-02", "qlike_month": "2020-02", "inputs": {"rv_d": True, "rv_w": True}, "degraded": True}
    assert "code_commit" in summary
    assert captured == {
        "train_end": "2019-12-31", "psi_window": 126, "qlike_window": 63,
        "degradation_threshold": 1.15, "degradation_months": 2,
    }
    assert json.loads(capsys.readouterr().out) == summary

    monkeypatch.setenv("PSI_THRESHOLD", "0.45")
    second = tmp_path / "higher-threshold"
    assert run_drift.main(["--out-dir", str(second)]) == 0
    second_report = pd.read_parquet(second / "drift_report.parquet")
    assert not second_report.loc[second_report.section == "psi", "flag"].any()


def test_missing_input_exits_one_with_clear_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "missing"))
    assert run_drift.main(["--out-dir", str(tmp_path / "out")]) == 1
    assert "missing input file:" in capsys.readouterr().err
