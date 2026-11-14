"""Synthetic tests for the Q2 spread/premium and the Feb–Apr 2020 window rule.

The only non-synthetic input here is the XNYS calendar, used offline to pin the 71 forecast dates
whose 30-day targets touch the window.  The panel itself is not read.
"""

from __future__ import annotations

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from volab import window
from volab.models.baselines import implied_variance
from volab.spread import ex_ante_spread, ex_post_premium, regime, summarise


def test_spread_and_premium_match_the_hand_values():
    iv = implied_variance(pd.Series([20.0]))
    f_m1 = pd.Series([0.003], index=iv.index)
    rv30 = pd.Series([0.004], index=iv.index)

    spread = ex_ante_spread(iv, f_m1)
    premium = ex_post_premium(iv, rv30)

    assert spread.iloc[0] == pytest.approx(2.8767e-4, rel=1e-5)
    assert premium.iloc[0] == pytest.approx(-7.1233e-4, rel=1e-5)


def test_spread_rejects_shape_mismatch_and_non_finite_input():
    with pytest.raises(ValueError, match="shapes"):
        ex_ante_spread(np.array([1.0, 2.0]), np.array([1.0]))

    with pytest.raises(ValueError, match="non-finite"):
        ex_ante_spread(np.array([1.0, np.nan]), np.array([1.0, 2.0]))


def test_regime_boundaries_are_low_below_and_high_at_the_upper_threshold():
    vix = pd.Series([14.88, 14.89, 18.57])

    labels = regime(vix, 14.89, 18.57)

    assert labels.tolist() == ["low", "mid", "high"]
    assert regime(18.57, 14.89, 18.57) == "high"


def _forecast_sessions() -> pd.DatetimeIndex:
    calendar = xcals.get_calendar("XNYS")
    return calendar.sessions_in_range("2020-01-02", "2026-10-12")


def test_window_edge_dates_and_the_seventy_one_forecast_count():
    sessions = xcals.get_calendar("XNYS").sessions_in_range("2019-12-01", "2026-12-31")
    dates = pd.DatetimeIndex([
        "2020-01-17",
        "2020-01-21",
        "2020-04-30",
        "2020-05-01",
    ])

    touched = window.target_touches_window(dates, sessions)

    assert touched.tolist() == [False, True, True, False]
    forecasts = _forecast_sessions()
    assert len(forecasts) == 1703
    assert int(window.target_touches_window(forecasts, forecasts).sum()) == 71
    assert int(window.session_in_window(forecasts).sum()) == 52


def test_summarise_hand_checks_n_median_and_share_positive():
    values = np.array([1.0, -2.0, 3.0, 4.0, -5.0, 6.0])
    labels = pd.Series(["a", "a", "a", "b", "b", "b"])

    summary = summarise(values, labels)

    all_row = summary[summary["group"] == "all"].iloc[0]
    assert all_row["n"] == 6
    assert all_row["median"] == pytest.approx(2.0)
    assert all_row["share_positive"] == pytest.approx(4.0 / 6.0)
    assert np.isfinite(all_row["hac_t"])

    group_a = summary[(summary["group"] == "label") & (summary["label"] == "a")].iloc[0]
    assert group_a["n"] == 3
    assert group_a["median"] == pytest.approx(1.0)
    assert group_a["share_positive"] == pytest.approx(2.0 / 3.0)


def test_summarise_keeps_full_and_ex_window_samples_apart():
    values = np.array([1.0, -2.0, 3.0, 4.0, -5.0, 6.0])
    labels = pd.DataFrame({
        "year": [2020, 2020, 2020, 2021, 2021, 2021],
        "regime": ["low", "low", "low", "high", "high", "high"],
        "sample": ["full"] * 6,
    })

    summary = summarise(values, labels)

    assert set(summary["sample"]) == {"full"}
    assert set(summary["group"]) == {"all", "year", "regime"}
    assert set(summary.loc[summary["group"] == "year", "label"]) == {"2020", "2021"}
