"""Synthetic-only tests for M3 — GARCH(1,1) with `arch` 8.0.0.

The fixture simulates percent returns from a known GARCH(1,1) process.  Nothing here reads the
frozen panel, so the test period is untouched.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from volab.models import garch

OMEGA = 0.02
ALPHA = 0.08
BETA = 0.90
N_DAYS = 1500
SEED = 20261114
REFIT_POSITION = 1000


@pytest.fixture(scope="module")
def sim_returns() -> pd.Series:
    """Simulated GARCH(1,1) percent returns, fixed seed, ~1,500 business days."""
    rng = np.random.default_rng(SEED)
    index = pd.bdate_range("2015-01-01", periods=N_DAYS, name="date")
    variance = np.empty(N_DAYS)
    values = np.empty(N_DAYS)

    variance[0] = OMEGA / (1.0 - ALPHA - BETA)
    values[0] = np.sqrt(variance[0]) * rng.standard_normal()
    for t in range(1, N_DAYS):
        variance[t] = OMEGA + ALPHA * values[t - 1] ** 2 + BETA * variance[t - 1]
        values[t] = np.sqrt(variance[t]) * rng.standard_normal()

    return pd.Series(values, index=index, name="r_pct")


def _fit(returns: pd.Series):
    return garch.fit_fold(garch.build_model(returns), returns.index[REFIT_POSITION])


# --- unit conversion and the ragged sum -------------------------------------


def test_sum_steps_sums_the_first_n_columns_and_converts_from_percent_squared():
    variance = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])

    totals = garch.sum_steps(variance, [2, 3])

    np.testing.assert_allclose(totals, [3e-4, 1.5e-3])


@pytest.mark.parametrize("counts", [[0, 2], [2, 4], [1.5, 2]])
def test_sum_steps_rejects_counts_outside_one_to_the_column_count(counts):
    with pytest.raises(ValueError):
        garch.sum_steps(np.ones((2, 3)), counts)


# --- percent returns --------------------------------------------------------


def test_pct_returns_scales_both_legs_by_one_hundred():
    r_on = pd.Series([0.01, -0.02])
    r_oc = pd.Series([0.005, -0.005])

    result = garch.pct_returns(r_on, r_oc)

    np.testing.assert_allclose(result.to_numpy(), [1.5, -2.5])


def test_pct_returns_drops_only_the_leading_nan():
    index = pd.date_range("2020-01-01", periods=3, name="date")
    r_on = pd.Series([np.nan, 0.01, 0.02], index=index)
    r_oc = pd.Series([np.nan, 0.00, 0.01], index=index)

    result = garch.pct_returns(r_on, r_oc)

    assert result.index.equals(index[1:])
    np.testing.assert_allclose(result.to_numpy(), [1.0, 3.0])


def test_pct_returns_rejects_a_nan_after_the_first_row():
    r_on = pd.Series([0.01, np.nan, 0.02])
    r_oc = pd.Series([0.0, 0.0, 0.0])

    with pytest.raises(ValueError, match="non-finite"):
        garch.pct_returns(r_on, r_oc)


# --- timing: the fit cutoff -------------------------------------------------


def test_fit_cutoff_excludes_the_refit_date(sim_returns):
    refit_date = sim_returns.index[REFIT_POSITION]
    _, base = _fit(sim_returns)
    base_params = np.array([base["mu"], base["omega"], base["alpha"], base["beta"]])

    at_cutoff = sim_returns.copy()
    at_cutoff.loc[refit_date] += 10.0
    _, cutoff = garch.fit_fold(garch.build_model(at_cutoff), refit_date)
    cutoff_params = np.array(
        [cutoff["mu"], cutoff["omega"], cutoff["alpha"], cutoff["beta"]]
    )
    np.testing.assert_allclose(cutoff_params, base_params, rtol=1e-10)

    day_before = sim_returns.index[REFIT_POSITION - 1]
    before_cutoff = sim_returns.copy()
    before_cutoff.loc[day_before] += 10.0
    _, shifted = garch.fit_fold(garch.build_model(before_cutoff), refit_date)
    shifted_params = np.array(
        [shifted["mu"], shifted["omega"], shifted["alpha"], shifted["beta"]]
    )
    assert not np.allclose(shifted_params, base_params, rtol=1e-10)


# --- timing: the forecast origin -------------------------------------------


def test_forecast_at_t_uses_returns_through_t_only(sim_returns):
    refit_date = sim_returns.index[REFIT_POSITION]
    origin = sim_returns.index[REFIT_POSITION + 100]
    horizon = 5

    def forecast_at(series: pd.Series) -> np.ndarray:
        res, _ = garch.fit_fold(garch.build_model(series), refit_date)
        return (
            res.forecast(horizon=horizon, start=origin, reindex=False, method="analytic")
            .variance.loc[origin]
            .to_numpy()
        )

    base = forecast_at(sim_returns)

    same_day = sim_returns.copy()
    same_day.loc[origin] += 10.0
    assert not np.allclose(forecast_at(same_day), base)

    next_day = sim_returns.copy()
    next_day.loc[sim_returns.index[REFIT_POSITION + 101]] += 10.0
    np.testing.assert_array_equal(forecast_at(next_day), base)


# --- the analytic recursion -------------------------------------------------


def test_forecast_matches_the_garch_recursion(sim_returns):
    res, record = _fit(sim_returns)
    origin = sim_returns.index[REFIT_POSITION + 10]
    n_steps = 30

    first_step = float(
        res.forecast(horizon=n_steps, start=origin, reindex=False, method="analytic")
        .variance.loc[origin, "h.01"]
    )

    sigma2 = first_step
    total = 0.0
    persistence = record["alpha"] + record["beta"]
    for _ in range(n_steps):
        total += sigma2
        sigma2 = record["omega"] + persistence * sigma2

    expected = total / garch.PCT2_TO_DECIMAL
    actual = garch.forecast_fold(res, [origin], [n_steps])[0]

    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=0.0)


# --- positivity -------------------------------------------------------------


def test_forecasts_are_finite_and_positive_for_mixed_session_counts(sim_returns):
    res, _ = _fit(sim_returns)
    origins = sim_returns.index[REFIT_POSITION : REFIT_POSITION + 6]
    counts = np.array([20, 21, 22, 20, 21, 22])

    totals = garch.forecast_fold(res, origins, counts)

    assert np.isfinite(totals).all()
    assert (totals > 0).all()


# --- the fit record ---------------------------------------------------------


def test_fit_record_reports_persistence_and_the_bound(sim_returns):
    _, record = _fit(sim_returns)

    expected_keys = {
        "refit_date",
        "nobs",
        "first_estimation_date",
        "last_estimation_date",
        "mu",
        "omega",
        "alpha",
        "beta",
        "persistence",
        "at_bound",
        "loglik",
        "convergence_flag",
        "other_warnings",
    }
    assert expected_keys <= set(record)
    assert record["persistence"] == record["alpha"] + record["beta"]
    assert isinstance(record["at_bound"], bool)
    assert record["at_bound"] == (record["persistence"] >= 1.0 - 1e-6)
    assert record["first_estimation_date"] == sim_returns.index[0]
    assert record["last_estimation_date"] < record["refit_date"]
    assert record["nobs"] == REFIT_POSITION
    assert isinstance(record["other_warnings"], str)


# --- failures ---------------------------------------------------------------


def test_non_convergence_raises(sim_returns):
    with pytest.raises(RuntimeError, match="converge"):
        garch.fit_fold(
            garch.build_model(sim_returns),
            sim_returns.index[REFIT_POSITION],
            options={"maxiter": 1},
        )


def test_forecast_fold_rejects_an_origin_missing_from_the_data(sim_returns):
    res, _ = _fit(sim_returns)

    with pytest.raises(ValueError, match="missing"):
        garch.forecast_fold(res, [pd.Timestamp("1900-01-01")], [21])


def test_forecast_fold_rejects_a_zero_count_before_asking_arch(sim_returns):
    res, _ = _fit(sim_returns)
    origins = sim_returns.index[REFIT_POSITION : REFIT_POSITION + 2]

    with pytest.raises(ValueError, match="at least 1"):
        garch.forecast_fold(res, origins, [21, 0])
