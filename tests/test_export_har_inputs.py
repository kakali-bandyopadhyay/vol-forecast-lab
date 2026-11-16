from __future__ import annotations

import importlib.util
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
