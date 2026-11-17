import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/run_proxy_check.py"
SPEC = importlib.util.spec_from_file_location("run_proxy_check", SCRIPT)
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


def test_proxy_check_uses_training_window_means_only():
    panel = pd.DataFrame({"rv": [6.0, 8.0, 100.0], "rv_close_to_close": [4.0, 4.0, 50.0],
                          "cross_term": [0.4, 0.4, 8.0]},
                         index=pd.to_datetime(["2015-12-30", "2015-12-31", "2016-01-04"]))
    result = proxy.build_proxy_check(panel)
    assert result["n"] == 2
    assert result["mean_rv_primary_over_mean_r_squared"] == pytest.approx(1.75)
    assert result["cross_term_share_of_mean_r_squared"] == pytest.approx(0.1)
    assert set(result) == {"section", "training_end", "n", "mean_rv_primary", "mean_r_squared",
                           "mean_rv_primary_over_mean_r_squared", "mean_cross_term",
                           "cross_term_share_of_mean_r_squared", "definitions"}
