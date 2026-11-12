"""Hand-computed tests for the walk-forward splitter — BUILD_GUIDE Step 5.

Fixture: pd.bdate_range("2019-10-01", "2020-04-30"), every target present, test start 2020-01-01.
Business-day arithmetic is done by hand in the assertions, not regenerated from the splitter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from volab.splits import (
    Fold,
    annual_walk_forward,
    early_stop_split,
    validation_bounds,
    walk_forward,
)

SESSIONS = pd.bdate_range("2019-10-01", "2020-04-30")
ALL_OK = pd.Series(True, index=SESSIONS)
TEST_START = pd.Timestamp("2020-01-01")
EXPECTED_REFITS = ["2020-01-01", "2020-01-30", "2020-02-28", "2020-03-30", "2020-04-28"]


def _folds(**kwargs) -> list[Fold]:
    return walk_forward(SESSIONS, ALL_OK, TEST_START, **kwargs)


def _dates(positions: np.ndarray) -> list[pd.Timestamp]:
    return [SESSIONS[i] for i in positions]


# --- block structure --------------------------------------------------------


def test_first_forecast_is_the_first_session_at_or_after_the_test_start():
    folds = _folds()

    assert folds[0].refit_date == TEST_START
    assert SESSIONS[folds[0].forecast_idx[0]] == TEST_START


def test_five_refits_covering_eighty_seven_test_dates():
    folds = _folds()

    assert len(folds) == 5
    assert sum(len(fold.forecast_idx) for fold in folds) == 87


def test_refit_dates():
    assert [fold.refit_date.strftime("%Y-%m-%d") for fold in _folds()] == EXPECTED_REFITS


def test_forecast_blocks_partition_the_test_dates():
    folds = _folds()
    joined = np.concatenate([fold.forecast_idx for fold in folds])
    expected = np.flatnonzero(SESSIONS >= TEST_START)

    assert np.array_equal(joined, expected)          # every date once, in order, no gaps
    assert len(np.unique(joined)) == len(joined)


# --- training sets ----------------------------------------------------------


@pytest.mark.parametrize(
    "fold_index, n_train, last_train",
    [(0, 44, "2019-11-29"), (1, 65, "2019-12-30"), (2, 86, "2020-01-28")],
)
def test_training_set_size_and_last_row(fold_index, n_train, last_train):
    fold = _folds()[fold_index]

    assert len(fold.train_idx) == n_train
    assert SESSIONS[fold.train_idx[-1]].strftime("%Y-%m-%d") == last_train


def test_no_training_target_overlaps_a_forecast_date():
    """§9.3: max(train) + gap_days < min(forecast), checked on every fold."""
    for fold in _folds():
        last_train = SESSIONS[fold.train_idx[-1]]
        first_forecast = SESSIONS[fold.forecast_idx[0]]
        assert last_train + pd.Timedelta(days=30) < first_forecast


def test_windows_only_grow_and_the_start_never_moves():
    folds = _folds()
    counts = [len(fold.train_idx) for fold in folds]

    assert counts == sorted(counts)
    assert len(set(counts)) > 1, "expanding window should add rows over time"
    starts = {SESSIONS[fold.train_idx[0]] for fold in folds}
    assert len(starts) == 1


def test_training_row_with_a_nan_target_is_dropped():
    mask = ALL_OK.copy()
    mask.loc[pd.Timestamp("2019-11-29")] = False

    fold = walk_forward(SESSIONS, mask, TEST_START)[0]

    assert len(fold.train_idx) == 43
    dropped = np.flatnonzero(SESSIONS == pd.Timestamp("2019-11-29"))[0]
    assert dropped not in fold.train_idx


def test_mask_may_be_a_plain_boolean_array():
    as_array = np.ones(len(SESSIONS), dtype=bool)

    by_series = _folds()
    by_array = walk_forward(SESSIONS, as_array, TEST_START)

    assert len(by_series) == len(by_array)
    for left, right in zip(by_series, by_array):
        assert left.refit_date == right.refit_date
        assert np.array_equal(left.train_idx, right.train_idx)


# --- bounds, modes, edge cases ---------------------------------------------


def test_validation_mode_stays_inside_its_window():
    start, end = validation_bounds()
    folds = walk_forward(SESSIONS, ALL_OK, start, end)

    assert folds, "fixture overlaps the validation window"
    for fold in folds:
        assert all(start <= day <= end for day in _dates(fold.forecast_idx))
    assert max(_dates(folds[-1].forecast_idx)) <= end


def test_real_data_block_arithmetic_gives_eighty_two_folds():
    """1,703 test dates → 81 full blocks of 21 plus a final block of 2."""
    sessions = pd.bdate_range("2010-01-04", periods=1703)
    folds = walk_forward(sessions, pd.Series(True, index=sessions), sessions[0])

    assert len(folds) == 82
    assert len(folds[-1].forecast_idx) == 2
    assert sum(len(fold.forecast_idx) for fold in folds) == 1703


def test_gap_days_zero_lets_training_run_up_to_the_prior_session():
    folds = _folds(gap_days=0)
    fold = folds[0]
    prior = SESSIONS[np.flatnonzero(SESSIONS < fold.refit_date)[-1]]

    assert SESSIONS[fold.train_idx[-1]] == prior


def test_smaller_refit_every_gives_more_folds():
    assert len(_folds(refit_every=5)) == 18       # ceil(87 / 5)


def test_empty_window_returns_no_folds():
    assert walk_forward(SESSIONS, ALL_OK, pd.Timestamp("2030-01-01")) == []


def test_unsorted_sessions_are_rejected():
    with pytest.raises(ValueError, match="sorted"):
        walk_forward(SESSIONS[::-1], ALL_OK, TEST_START)


def test_array_mask_of_the_wrong_length_is_rejected():
    with pytest.raises(ValueError, match="shape"):
        walk_forward(SESSIONS, np.ones(3, dtype=bool), TEST_START)


# --- annual grid (M4) -------------------------------------------------------

MULTI = pd.bdate_range("2019-10-01", "2021-06-30")
MULTI_OK = pd.Series(True, index=MULTI)


def test_annual_blocks_partition_exactly_the_same_dates_as_the_session_grid():
    """The annual grid changes when parameters refresh, never which dates are forecast."""
    session_grid = walk_forward(MULTI, MULTI_OK, TEST_START)
    annual_grid = annual_walk_forward(MULTI, MULTI_OK, TEST_START)

    from_session = np.sort(np.concatenate([fold.forecast_idx for fold in session_grid]))
    from_annual = np.concatenate([fold.forecast_idx for fold in annual_grid])

    assert np.array_equal(from_annual, np.flatnonzero(MULTI >= TEST_START))
    assert np.array_equal(np.sort(from_annual), from_session)


def test_annual_refits_on_the_first_forecast_date_of_each_year():
    annual_grid = annual_walk_forward(MULTI, MULTI_OK, TEST_START)

    assert [fold.refit_date.strftime("%Y-%m-%d") for fold in annual_grid] == [
        "2020-01-01", "2021-01-01",
    ]


def test_annual_folds_respect_the_no_overlap_rule():
    for fold in annual_walk_forward(MULTI, MULTI_OK, TEST_START):
        last_train = MULTI[fold.train_idx[-1]]
        assert last_train + pd.Timedelta(days=30) < MULTI[fold.forecast_idx[0]]


def test_annual_grid_has_one_fold_per_calendar_year_in_the_test_period():
    annual_grid = annual_walk_forward(MULTI, MULTI_OK, TEST_START)
    years = {MULTI[i].year for fold in annual_grid for i in fold.forecast_idx}

    assert len(annual_grid) == len(years) == 2


# --- early-stopping split ---------------------------------------------------


def test_early_stop_split_takes_the_last_fifth():
    train = np.arange(100)
    fit, stop = early_stop_split(train)

    assert len(stop) == 20
    assert np.array_equal(stop, train[-20:])
    assert np.array_equal(fit, train[:-20])


def test_early_stop_split_is_disjoint_and_complete():
    train = np.arange(37)
    fit, stop = early_stop_split(train)

    assert not set(fit) & set(stop)
    assert np.array_equal(np.concatenate([fit, stop]), train)


def test_early_stop_split_rounds_up_so_the_holdout_is_never_under_a_fifth():
    fit, stop = early_stop_split(np.arange(11))       # 20% of 11 = 2.2 -> 3 rows, not 2

    assert len(stop) == 3
    assert len(fit) == 8


def test_early_stop_split_keeps_one_stop_row_on_a_tiny_block():
    fit, stop = early_stop_split(np.arange(3))

    assert len(stop) == 1
    assert len(fit) == 2


def test_early_stop_split_rejects_a_block_too_small_to_split():
    with pytest.raises(ValueError, match="too small"):
        early_stop_split(np.arange(1))


def test_early_stop_split_rejects_unsorted_indices():
    with pytest.raises(ValueError, match="sorted"):
        early_stop_split(np.array([3, 1, 2]))


def test_early_stop_holdout_still_ends_before_the_first_forecast_date():
    fold = walk_forward(SESSIONS, ALL_OK, TEST_START)[0]
    _fit, stop = early_stop_split(fold.train_idx)

    assert SESSIONS[stop[-1]] + pd.Timedelta(days=30) < SESSIONS[fold.forecast_idx[0]]


# --- shared input validation and cadence ------------------------------------


def test_annual_walk_forward_rejects_unsorted_sessions():
    with pytest.raises(ValueError, match="sorted"):
        annual_walk_forward(MULTI[::-1], MULTI_OK, TEST_START)


def test_annual_walk_forward_rejects_negative_gap_days():
    with pytest.raises(ValueError, match="non-negative"):
        annual_walk_forward(MULTI, MULTI_OK, TEST_START, gap_days=-1)


def test_walk_forward_rejects_negative_gap_days():
    with pytest.raises(ValueError, match="non-negative"):
        walk_forward(SESSIONS, ALL_OK, TEST_START, gap_days=-1)


def test_refit_cadence_counts_forecast_dates_not_sessions():
    """§5.2 says "every 21 sessions"; the splitter counts forecast dates. Masking a single date
    moves the block boundary, which is the documented reading — invisible on the frozen snapshot,
    which has no excluded dates, so the two counts coincide there."""
    mask = ALL_OK.copy()
    mask.loc[pd.Timestamp("2020-01-15")] = False

    folds = walk_forward(SESSIONS, mask, TEST_START)

    assert folds[0].refit_date == TEST_START
    assert folds[1].refit_date.strftime("%Y-%m-%d") == "2020-01-31"
