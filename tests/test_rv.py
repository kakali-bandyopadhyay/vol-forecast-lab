"""Hand-computed tests for the RV proxies and 30-day targets — BUILD_GUIDE Step 3.

Numeric expectations are the guide's, computed by hand from §3.1–3.2; they are not
regenerated from the implementation.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from volab import rv as rv_module
from volab import targets

# The guide's expectations are quoted to 6 significant figures. rel=1e-6 alone would demand a
# seventh digit (it fails on r_on^2 and on the Day 2 RV), so an absolute floor of half a unit in
# the last quoted decimal is allowed: on the ~1e-4 quantities that is 5e-10.
TOL = {"rel": 1e-6, "abs": 5e-10}

# --- helpers ----------------------------------------------------------------


def _frame(rows: list[tuple]) -> pd.DataFrame:
    """Adjusted OHLC frame from (date, open, high, low, close) tuples."""
    index = pd.DatetimeIndex([pd.Timestamp(row[0]) for row in rows], name="date")
    return pd.DataFrame(
        {
            "adjOpen": [row[1] for row in rows],
            "adjHigh": [row[2] for row in rows],
            "adjLow": [row[3] for row in rows],
            "adjClose": [row[4] for row in rows],
        },
        index=index,
    )


# Day 1 case: previous close 100, then O 100, H 102, L 99, C 101 (no gap).
def _day_one() -> pd.DataFrame:
    return _frame([
        ("2024-01-02", 100.0, 100.0, 100.0, 100.0),
        ("2024-01-03", 100.0, 102.0, 99.0, 101.0),
    ])


# Day 2 case: previous close 101, then O 101.5, H 102, L 100.5, C 100.8 (gap up).
def _day_two() -> pd.DataFrame:
    return _frame([
        ("2024-01-02", 101.0, 101.0, 101.0, 101.0),
        ("2024-01-03", 101.5, 102.0, 100.5, 100.8),
    ])


# --- hand-computed proxy cases ---------------------------------------------


def test_day_one_no_gap():
    frame = _day_one()
    day = frame.index[1]

    assert rv_module.overnight(frame).loc[day] == pytest.approx(0.0, abs=1e-12)
    assert rv_module.garman_klass(frame).loc[day] == pytest.approx(0.000407353, **TOL)
    assert rv_module.rv_primary(frame).loc[day] == pytest.approx(0.000407353, **TOL)


def test_day_two_gap_up():
    frame = _day_two()
    day = frame.index[1]

    r_on = rv_module.overnight(frame).loc[day]
    assert r_on ** 2 == pytest.approx(0.0000243866, **TOL)
    assert rv_module.garman_klass(frame).loc[day] == pytest.approx(0.0000912428, **TOL)
    assert rv_module.rv_primary(frame).loc[day] == pytest.approx(0.000115629, **TOL)


def test_parkinson_day_one():
    frame = _day_one()
    assert rv_module.parkinson(frame).loc[frame.index[1]] == pytest.approx(0.000321432, **TOL)


# --- traps from the brief ---------------------------------------------------


def test_first_row_overnight_is_nan_and_not_filled():
    frame = _day_one()
    r_on = rv_module.overnight(frame)

    assert np.isnan(r_on.iloc[0])
    assert np.isnan(rv_module.rv_primary(frame).iloc[0])
    # the value must not be repaired anywhere else in the series
    assert r_on.isna().sum() == 1


def test_zero_range_day_gives_exactly_zero_not_nan():
    frame = _frame([
        ("2024-01-02", 100.0, 100.0, 100.0, 100.0),
        ("2024-01-03", 100.0, 100.0, 100.0, 100.0),
    ])
    day = frame.index[1]

    gk = rv_module.garman_klass(frame).loc[day]
    assert gk == 0.0
    assert not np.isnan(gk)
    assert rv_module.rv_primary(frame).loc[day] == 0.0


def test_cross_term_reconstructs_close_to_close_squared():
    frame = _day_two()
    day = frame.index[1]

    r_on = rv_module.overnight(frame).loc[day]
    r_oc = rv_module.session_return(frame).loc[day]
    cross = rv_module.cross_term(frame).loc[day]

    assert (r_on + r_oc) ** 2 == pytest.approx(r_on ** 2 + r_oc ** 2 + cross, rel=1e-9)
    assert cross != 0.0


def test_raw_ohlc_columns_are_rejected():
    """§2.3: adjusted fields only. Raw columns must fail loudly, not silently produce proxies."""
    raw = _day_one().rename(columns={
        "adjOpen": "open", "adjHigh": "high", "adjLow": "low", "adjClose": "close",
    })
    for function in (rv_module.overnight, rv_module.session_return,
                     rv_module.garman_klass, rv_module.parkinson, rv_module.rv_primary):
        with pytest.raises(KeyError):
            function(raw)


def test_range_estimators_are_non_negative():
    frame = _day_one()
    assert (rv_module.garman_klass(frame) >= 0).all()
    assert (rv_module.parkinson(frame) >= 0).all()


# --- 30-day window ----------------------------------------------------------

SESSION_DATES = ("2024-01-02", "2024-01-30", "2024-02-01", "2024-02-02")
SESSIONS = pd.DatetimeIndex([pd.Timestamp(value) for value in SESSION_DATES])
T = pd.Timestamp("2024-01-02")          # t + 30 calendar days = 2024-02-01

# A real caller builds sessions well past the last data date (the calendar is known in advance).
# This sentinel stands in for that, so forward-target tests do not trip the coverage guard.
EXTENDED = SESSIONS.append(pd.DatetimeIndex([pd.Timestamp("2024-04-05")]))


def test_window_is_calendar_days_not_sessions():
    window = targets.window_sessions(SESSIONS, T)

    assert list(window) == [pd.Timestamp("2024-01-30"), pd.Timestamp("2024-02-01")]
    assert pd.Timestamp("2024-02-02") not in window     # one day past t + 30
    assert pd.Timestamp("2024-01-02") not in window     # t itself is exclusive
    assert targets.n_sessions(SESSIONS, T) == 2


def test_target_sums_only_the_window_and_excludes_t():
    values = pd.Series([0.50, 0.01, 0.02, 0.04], index=SESSIONS)

    # t's own value (0.50) and 2 Feb (0.04) are both outside the window
    assert targets.target_30d(values, SESSIONS, T) == pytest.approx(0.03)


def test_excluded_session_drops_the_whole_target(caplog):
    values = pd.Series([0.50, 0.01, 0.02, 0.04], index=SESSIONS)

    with caplog.at_level(logging.WARNING, logger="volab.targets"):
        result = targets.target_30d(values, SESSIONS, T, excluded=[pd.Timestamp("2024-02-01")])

    assert np.isnan(result)
    assert "excluded" in caplog.text
    assert "2024-02-01" in caplog.text


def test_missing_rv_inside_window_does_not_shrink_it(caplog):
    values = pd.Series([0.50, 0.01, np.nan, 0.04], index=SESSIONS)

    with caplog.at_level(logging.WARNING, logger="volab.targets"):
        result = targets.target_30d(values, SESSIONS, T)

    assert np.isnan(result)
    assert "unavailable" in caplog.text
    # N_t still comes from the calendar, so the window is still two sessions wide
    assert targets.n_sessions(SESSIONS, T) == 2


def test_rv_row_absent_from_index_also_drops_the_target():
    partial = pd.Series([0.50, 0.01], index=SESSIONS[:2])      # 1 Feb is absent entirely

    assert np.isnan(targets.target_30d(partial, SESSIONS, T))


def test_empty_window_is_nan(caplog):
    """No session falls inside the window, but one does fall after its end, so the guard is
    satisfied and the empty window is reported rather than raised."""
    gapped = pd.DatetimeIndex([pd.Timestamp("2024-01-02"), pd.Timestamp("2024-02-02")])

    with caplog.at_level(logging.WARNING, logger="volab.targets"):
        result = targets.target_30d(pd.Series([0.50, 0.04], index=gapped), gapped, gapped[0])

    assert np.isnan(result)
    assert "no sessions" in caplog.text


def test_forward_targets_reports_target_and_calendar_count():
    values = pd.Series([0.50, 0.01, 0.02, 0.04], index=SESSIONS)

    out = targets.forward_targets(values, EXTENDED)

    assert list(out.columns) == ["rv_30d", "n_sessions"]
    assert len(out) == len(SESSIONS)
    assert out.loc[T, "n_sessions"] == 2
    assert out.loc[T, "rv_30d"] == pytest.approx(0.03)
    # a dropped target still carries its calendar count
    assert np.isnan(out.loc[SESSIONS[-1], "rv_30d"])
    assert out.loc[SESSIONS[-1], "n_sessions"] == 0


def test_per_session_mean_divides_by_calendar_count():
    values = pd.Series([0.50, 0.01, 0.02, 0.04], index=SESSIONS)
    out = targets.forward_targets(values, EXTENDED)

    mean = targets.per_session_mean(out)

    assert mean.loc[T] == pytest.approx(0.03 / 2)


# --- coverage guard ---------------------------------------------------------


def test_truncated_calendar_raises_instead_of_summing_a_partial_window():
    """A calendar ending inside the window must fail loudly: a partial sum understates both the
    target and N_t, and would do it silently on the last ~21 forecast dates of the sample."""
    truncated = pd.DatetimeIndex([
        pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04"),
    ])
    values = pd.Series([0.50, 1.00, 1.00], index=truncated)     # a truncated sum would be 2.0

    with pytest.raises(ValueError, match="calendar ends"):
        targets.target_30d(values, truncated, T)

    with pytest.raises(ValueError, match="calendar ends"):
        targets.n_sessions(truncated, T)


def test_calendar_ending_exactly_at_the_window_end_also_raises():
    """Deliberately conservative: with no session after `end` the guard cannot distinguish a
    complete calendar from a truncated one, so it refuses instead of guessing. Build the index
    well past the last data date and this never fires."""
    boundary = pd.DatetimeIndex([pd.Timestamp("2024-01-02"), pd.Timestamp("2024-02-01")])

    with pytest.raises(ValueError, match="calendar ends"):
        targets.target_30d(pd.Series([0.50, 0.02], index=boundary), boundary, T)
