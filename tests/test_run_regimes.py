import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).parents[1] / "scripts/run_regimes.py"
SPEC = importlib.util.spec_from_file_location("run_regimes", SCRIPT)
regimes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regimes)


def test_regime_report_is_aggregate_and_flags_small_cells():
    dates = pd.bdate_range("2019-12-01", "2024-02-01")
    panel = pd.DataFrame({"vix": np.resize([12.0, 16.0, 21.0], len(dates))}, index=dates)
    forecasts = pd.DataFrame([
        {"date": d, "model": m, "realised_30d": 0.02 + i * 1e-5,
         "raw_30d": (0.018 if m == "M1" else 0.019) + i * 2e-6}
        for i, d in enumerate(dates[dates >= "2020-01-01"])
        for m in ("M1", "M2")
    ])
    result = regimes.build_regime_report(forecasts, panel, 14.89, 18.57)
    assert len(result["cells"]) == 12
    assert {row["dimension"] for row in result["cells"]} == {"vix_tercile", "calendar_subperiod"}
    assert all("small_n" in row and row["small_n"] == (row["n"] < 100)
               for row in result["cells"])
    assert result["any_cell_n_lt_100"] is any(row["small_n"] for row in result["cells"])
    assert result["any_cell_n_lt_100"] is True
    assert all("mean_f_t" in row and "t" in row and "p_one_sided" in row
               for row in result["cells"])
    assert all(not (set(row) & {"date", "f_t_series", "forecasts"}) for row in result["cells"])
