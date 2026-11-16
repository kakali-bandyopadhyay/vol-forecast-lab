from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from volab.models import har
from volab.models.baselines import implied_variance


SCRIPT = Path(__file__).parents[1] / "scripts" / "export_har_inputs.py"
SPEC = importlib.util.spec_from_file_location("export_har_inputs", SCRIPT)
export_har_inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_har_inputs)


def test_export_columns_equal_m2_design_matrix_on_synthetic_panel() -> None:
    dates = pd.date_range("2020-01-01", periods=30, freq="B", name="date")
    panel = pd.DataFrame(
        {
            "rv": np.linspace(0.001, 0.03, len(dates)),
            "vix": np.linspace(10.0, 30.0, len(dates)),
            "n_sessions": np.tile([20, 21, 22], 10),
        },
        index=dates,
    )
    actual = export_har_inputs.build_har_inputs(panel)
    expected = har.design(
        har.har_features(panel["rv"]),
        implied_variance(panel["vix"]) / panel["n_sessions"],
    )
    assert actual.columns.tolist() == ["date", "rv_d", "rv_w", "rv_m", "iv_ps"]
    pd.testing.assert_frame_equal(actual[actual.columns[1:]], pd.DataFrame(
        expected, columns=["rv_d", "rv_w", "rv_m", "iv_ps"]
    ))
    pd.testing.assert_series_equal(actual["date"], pd.Series(dates, name="date"))


def _gap_panel() -> pd.DataFrame:
    dates = pd.bdate_range("2019-09-02", "2020-03-31", name="date")
    rv = np.linspace(0.001, 0.03, len(dates))
    rv_30d = pd.Series(rv, index=dates)
    rv_30d.iloc[-25:] = np.nan  # target window not yet closed at the end of the sample
    return pd.DataFrame(
        {"rv": rv, "rv_30d": rv_30d, "vix": np.linspace(10.0, 30.0, len(dates)), "n_sessions": 21},
        index=dates,
    )


def test_train_end_is_last_session_before_first_refit_minus_gap() -> None:
    panel = _gap_panel()
    first_refit = panel.index[panel.index >= export_har_inputs.TEST_START][0]
    eligible = panel.index[panel["rv_30d"].notna() & (panel.index < first_refit - pd.Timedelta(days=30))]
    assert export_har_inputs.first_fold_train_end(panel) == eligible[-1]
    assert eligible[-1] < export_har_inputs.TEST_START - pd.Timedelta(days=30)


def test_export_writes_manifest_with_train_end_and_snapshot_id(tmp_path: Path) -> None:
    panel_path, out, snap, man = (tmp_path / n for n in ("p.parquet", "h.parquet", "s.json", "m.json"))
    panel = _gap_panel()
    panel.to_parquet(panel_path)
    snap.write_text(json.dumps({"snapshots": {"x": {"snapshot_id": "snap-1"}}}), encoding="utf-8")
    export_har_inputs.export(panel_path, out, snap, man)
    manifest = json.loads(man.read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == "snap-1"
    assert manifest["train_end"] == export_har_inputs.first_fold_train_end(panel).date().isoformat()
    assert manifest["rows"] == len(pd.read_parquet(out))


def test_test_start_matches_run_eval() -> None:
    text = (Path(__file__).parents[1] / "scripts" / "run_eval.py").read_text(encoding="utf-8")
    found = re.search(r'^TEST_START = pd\.Timestamp\("([0-9-]+)"\)', text, re.M)
    assert found and pd.Timestamp(found.group(1)) == export_har_inputs.TEST_START
