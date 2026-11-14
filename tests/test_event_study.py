"""Synthetic tests for the Q3 event study and the HAC OLS helper.

The planted-effect panel is generated from known log-variance equations, so the test checks
recovery of the planted coefficients rather than re-fitting the same arithmetic.  No panel file
is read.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from volab import events
from volab.stats_tests import ols_hac


def _ar1(rng: np.random.Generator, n: int, phi: float, scale: float) -> np.ndarray:
    values = np.zeros(n)
    for t in range(1, n):
        values[t] = phi * values[t - 1] + rng.normal(scale=scale)
    return values


def _panel(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2010-01-04", periods=n)
    r_on = 0.01 * rng.standard_normal(n)
    r_oc = 0.01 * rng.standard_normal(n)
    gk = np.full(n, 1e-4)
    rv = np.exp(-9.0 + _ar1(rng, n, 0.5, 0.3))
    return pd.DataFrame({"r_on": r_on, "r_oc": r_oc, "gk": gk, "rv": rv}, index=index)


# --- ols_hac ---------------------------------------------------------------


def test_ols_hac_recovers_noise_free_coefficients():
    rng = np.random.default_rng(20261114)
    n = 200
    design = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    truth = np.array([0.5, -0.2, 0.7])
    response = design @ truth

    result = ols_hac(response, design, lag=5)

    np.testing.assert_allclose(result["beta"], truth, atol=1e-10)


def test_ols_hac_standard_errors_match_statsmodels():
    rng = np.random.default_rng(314)
    n = 300
    design = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    response = design @ np.array([0.2, 0.5, -0.3]) + _ar1(rng, n, 0.5, 0.1)

    result = ols_hac(response, design, lag=5)

    fitted = sm.OLS(response, design).fit(
        cov_type="HAC", cov_kwds={"maxlags": 5}
    )
    np.testing.assert_allclose(result["se"], fitted.bse, rtol=1e-8)


def test_ols_hac_rejects_a_rank_deficient_design():
    rng = np.random.default_rng(7)
    column = rng.normal(size=50)
    design = np.column_stack([np.ones(50), column, column])

    with pytest.raises(ValueError, match="rank deficient"):
        ols_hac(rng.normal(size=50), design, lag=5)


# --- planted event effects --------------------------------------------------


def _planted_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(20261121)
    n = 2500
    index = pd.bdate_range("2010-01-04", periods=n)

    cpi_positions = np.arange(30, n - 10, 40)[:60]
    fomc_positions = np.arange(50, n - 10, 40)[:60]
    fomc_positions = np.array(
        [position for position in fomc_positions if position not in set(cpi_positions)]
    )[:60]

    cpi = np.zeros(n)
    fomc = np.zeros(n)
    cpi[cpi_positions] = 1.0
    fomc[fomc_positions] = 1.0
    emg_positions = np.array([100, 500, 1000, 1500])

    x_on = np.exp(-6.0 + 0.7 * cpi + _ar1(rng, n, 0.3, 0.5))
    x_oc = np.exp(-6.0 + 0.4 * fomc + _ar1(rng, n, 0.3, 0.5))
    panel = pd.DataFrame(
        {
            "r_on": np.sqrt(x_on) * rng.choice([-1.0, 1.0], size=n),
            "r_oc": np.sqrt(x_oc) * rng.choice([-1.0, 1.0], size=n),
            "gk": 0.5 * x_oc,
            "rv": np.exp(-7.0 + _ar1(rng, n, 0.8, 0.3)),
        },
        index=index,
    )
    indicator = events.indicators(
        index, index[fomc_positions], index[cpi_positions], index[emg_positions]
    )
    return panel, indicator


def _term(frame: pd.DataFrame, name: str) -> pd.Series:
    return frame.set_index("term").loc[name]


def test_planted_cpi_and_fomc_effects_are_recovered():
    panel, indicator = _planted_panel()
    rows = np.arange(len(panel)) >= 22
    c_on = 0.01 * float(np.median(panel["r_on"].to_numpy()[rows] ** 2))
    c_oc = 0.01 * float(np.median(panel["r_oc"].to_numpy()[rows] ** 2))

    overnight = events.run(panel, indicator, "on", c_on, rows=rows, label="planted")
    session = events.run(panel, indicator, "oc", c_oc, rows=rows, label="planted")

    cpi_overnight = _term(overnight, "cpi")
    assert abs(cpi_overnight["beta"] - 0.7) < 3.0 * cpi_overnight["se"]
    assert cpi_overnight["ci_low"] < 0.7 < cpi_overnight["ci_high"]

    fomc_session = _term(session, "fomc")
    assert abs(fomc_session["beta"] - 0.4) < 3.0 * fomc_session["se"]
    assert fomc_session["ci_low"] < 0.4 < fomc_session["ci_high"]

    assert _term(overnight, "fomc")["ci_low"] < 0 < _term(overnight, "fomc")["ci_high"]
    assert _term(session, "cpi")["ci_low"] < 0 < _term(session, "cpi")["ci_high"]


# --- calendars and indicators ----------------------------------------------


def test_an_event_date_off_the_session_calendar_raises():
    index = pd.DatetimeIndex(["2017-04-13", "2017-04-17"])

    with pytest.raises(ValueError, match="not in the index"):
        events.indicators(index, [], [pd.Timestamp("2017-04-14")], [])


def test_a_same_day_cpi_and_fomc_sets_both_indicators():
    date = pd.Timestamp("2013-10-30")

    indicator = events.indicators(pd.DatetimeIndex([date]), [date], [date], [])

    assert indicator.loc[date, "cpi"] == 1
    assert indicator.loc[date, "fomc"] == 1
    assert indicator.loc[date, "emg"] == 0


def test_warm_up_starts_at_index_twenty_two_and_drops_an_early_event():
    panel = _panel(100, seed=9)
    dates = [panel.index[9], panel.index[22]]
    indicator = events.indicators(
        panel.index, [panel.index[24]], dates, [panel.index[30]]
    )
    rows = events.lagged_rv(panel["rv"]).notna().all(axis=1).to_numpy()

    assert np.flatnonzero(rows)[0] == 22
    c_on = 0.01 * float(np.median(panel["r_on"].to_numpy()[rows] ** 2))
    result = events.run(panel, indicator, "on", c_on, rows=rows, label="warm-up")

    assert result.iloc[0]["n_obs"] == 78
    assert result.iloc[0]["n_cpi"] == 1


def test_monday_is_the_reference_and_emergency_control_has_five_dates():
    panel = _panel(60, seed=11)
    indicator = events.indicators(panel.index, [], [], [])

    _, design = events.design(panel, indicator, "on", c=1e-6)

    assert "mon" not in design.columns
    assert list(design.columns[-4:]) == ["tue", "wed", "thu", "fri"]
    assert tuple(events.EMERGENCY_CONTROL) == (
        pd.Timestamp("2010-05-10"),
        pd.Timestamp("2019-10-11"),
        pd.Timestamp("2020-03-03"),
        pd.Timestamp("2020-03-16"),
        pd.Timestamp("2020-03-23"),
    )
