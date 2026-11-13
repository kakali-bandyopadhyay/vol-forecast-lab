"""Tests for the statistical tests — BUILD_GUIDE Step 7.

All synthetic: data/panel/ is git-ignored and CI has no panel, so nothing here may touch it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from volab.losses import qlike
from volab.models import har
from volab.stats_tests import (
    clark_west,
    diebold_mariano,
    hac_lag,
    holm,
    mincer_zarnowitz,
    nw_mean_test,
)

# The leak test is a discrimination check, not a bare threshold: the leak must cut QLIKE by at
# least 5% while a shuffled placebo moves it by less than 2%. Both bounds come from measuring the
# synthetic behaviour — an initial 10% bound was too high (measured 8.8% with the overlap bug
# fixed, 7.5-10.9% across seeds) and 5% on its own was a number chosen to pass.
PLANTED_LEAK_MIN_DROP = 0.05
PLACEBO_MAX_DROP = 0.02


# --- lag rule ---------------------------------------------------------------


def test_hac_lag_is_twenty_one_at_the_real_sample_size():
    """§5.5: max(21, floor(4 (T/100)^(2/9))). At T = 1,703 the formula gives 7."""
    assert hac_lag(1703) == 21
    assert hac_lag(50) == 21
    assert int(np.floor(4.0 * (1703 / 100.0) ** (2.0 / 9.0))) == 7


def test_hac_lag_switches_to_the_formula_for_very_large_samples():
    """Exercises the branch that beats the 21-session floor."""
    assert hac_lag(10 ** 7) == 51


# --- guards on degenerate input --------------------------------------------


def test_nw_rejects_a_constant_non_zero_d():
    """Constant d leaves only rounding noise; the variance is positive but meaningless."""
    with pytest.raises(ValueError, match="negligible|non-positive"):
        nw_mean_test(np.full(200, 0.3), lag=21)


def test_holm_rejects_a_nan_p_value():
    with pytest.raises(ValueError, match="finite"):
        holm([0.01, np.nan, 0.03])


def test_qlike_rejects_a_nan_target():
    with pytest.raises(ValueError, match="finite"):
        qlike(np.array([np.nan]), np.array([0.002]))


def test_mz_rejects_non_finite_input():
    with pytest.raises(ValueError, match="finite"):
        mincer_zarnowitz(np.array([0.1, np.nan, 0.3]), np.array([0.1, 0.2, 0.3]), lag=1)


# --- Newey–West cross-check -------------------------------------------------


def test_dm_matches_statsmodels_hac_to_six_places():
    rng = np.random.default_rng(2026)
    n = 400
    differential = np.zeros(n)
    for t in range(1, n):                      # autocorrelated, so the HAC correction bites
        differential[t] = 0.6 * differential[t - 1] + rng.normal(scale=0.01)

    statistic, pvalue = diebold_mariano(differential, np.zeros(n), lag=21)

    fitted = sm.OLS(differential, np.ones((n, 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": 21}
    )
    assert statistic == pytest.approx(float(fitted.tvalues[0]), rel=1e-6, abs=1e-9)
    assert pvalue == pytest.approx(float(fitted.pvalues[0]), rel=1e-6, abs=1e-9)


def test_mz_wald_matches_statsmodels_hac():
    rng = np.random.default_rng(314)
    n = 500
    forecast = rng.uniform(0.002, 0.006, n)
    error = np.zeros(n)
    for t in range(1, n):
        error[t] = 0.5 * error[t - 1] + rng.normal(scale=0.0004)
    realised = 0.8 + 0.5 * forecast + error        # a ≠ 0 and b ≠ 1: the restriction is wrong

    result = mincer_zarnowitz(realised, forecast, lag=21)

    fitted = sm.OLS(realised, sm.add_constant(forecast)).fit(
        cov_type="HAC", cov_kwds={"maxlags": 21}
    )
    reference = fitted.wald_test((np.eye(2), np.array([0.0, 1.0])), scalar=True)

    assert result["wald"] == pytest.approx(float(reference.statistic), rel=1e-6)


# --- degenerate forecasts ---------------------------------------------------


def test_identical_forecasts_give_a_zero_statistic():
    """d_t ≡ 0, so the HAC variance is zero. Must return 0, not NaN."""
    rv = np.array([0.001, 0.002, 0.0015, 0.0022, 0.0011] * 10)
    forecast = np.array([0.0011] * 50)

    dm_statistic, dm_pvalue = diebold_mariano(np.zeros(50), np.zeros(50), lag=21)
    cw_statistic, cw_pvalue = clark_west(rv, forecast, forecast, lag=21)

    assert dm_statistic == 0.0
    assert dm_pvalue == pytest.approx(1.0)      # two-sided
    assert cw_statistic == 0.0
    assert cw_pvalue == pytest.approx(0.5)      # one-sided


# --- Clark–West direction and power ----------------------------------------


def test_clark_west_detects_a_nested_improvement_and_its_reverse():
    """Nothing else tests that the sign runs the right way, or that the test has power.

    Built as a genuine nesting, f2 = f1 + z with z informative about f1's error, because that is
    the only case where the statistic is signed. With independent noise the Clark–West series
    reduces to 2·e₁·(e₁ − e₂), which stays positive in both orderings and would make the swap
    assertion vacuous rather than a check.
    """
    rng = np.random.default_rng(77)
    n = 300
    realised = rng.uniform(0.002, 0.006, n)
    error = rng.normal(scale=0.001, size=n)                     # the restricted model's error
    restricted = realised - error
    increment = 0.5 * error + rng.normal(scale=0.0002, size=n)  # informative expansion
    unrestricted = restricted + increment

    statistic, pvalue = clark_west(realised, restricted, unrestricted, lag=5)
    swapped_statistic, swapped_pvalue = clark_west(realised, unrestricted, restricted, lag=5)

    assert statistic > 3.0 and pvalue < 0.01
    assert swapped_statistic < 0.0 and swapped_pvalue > 0.5


# --- Diebold–Mariano sign convention ----------------------------------------


def test_dm_sign_says_which_model_is_better():
    rng = np.random.default_rng(5)
    better = rng.uniform(0.001, 0.002, 200)
    worse = better * 1.5                       # strictly larger loss, and varying: a constant
                                               # differential has zero HAC variance and is degenerate

    statistic, _ = diebold_mariano(worse, better, lag=21)

    assert statistic > 0, "positive statistic must mean loss_a > loss_b"


# --- Mincer–Zarnowitz -------------------------------------------------------


def test_mz_does_not_reject_a_calibrated_forecast():
    rng = np.random.default_rng(7)
    forecast = rng.uniform(0.002, 0.006, 500)
    realised = forecast + rng.normal(scale=0.0002, size=500)

    result = mincer_zarnowitz(realised, forecast, lag=5)

    assert result["pvalue"] > 0.01


def test_mz_rejects_a_forecast_with_a_systematic_bias():
    rng = np.random.default_rng(8)
    forecast = rng.uniform(0.002, 0.006, 500)
    # Noisy on purpose: an exact 3× fit leaves only rounding noise in the residuals and the Wald
    # statistic explodes for arithmetic reasons rather than because of the bias.
    realised = 3.0 * forecast + rng.normal(scale=0.0005, size=500)

    result = mincer_zarnowitz(realised, forecast, lag=5)

    assert result["b"] == pytest.approx(3.0, rel=1e-2)
    assert result["pvalue"] < 1e-6


# --- Holm -------------------------------------------------------------------


def test_holm_matches_the_hand_computed_example():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_holm_caps_at_one():
    assert holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])


# --- §9.1 planted leak ------------------------------------------------------


def _persistent_rv(n: int = 900, seed: int = 99) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_rv = np.zeros(n)
    for t in range(1, n):
        log_rv[t] = 0.95 * log_rv[t - 1] + rng.normal(scale=0.25)
    return pd.Series(np.exp(log_rv) * 1e-4)


def test_planted_leak_cuts_qlike_and_a_placebo_does_not():
    """§9.1: add RV_{t+1} to the M1 inputs and QLIKE must fall sharply.

    The leak is one of the 21 sessions summed into the target, so the fall is moderate by
    construction. Asserting that a shuffled placebo column does not reproduce it turns this into a
    discrimination check rather than a threshold that could be tuned to pass.
    """
    rv = _persistent_rv()
    design = har.design(har.har_features(rv))

    # Target is t+1 … t+21. shift(-21) is required: shift(-20) stops at t+20 and includes RV_t,
    # which is already the rv_d feature, so part of the "leak" would be a legitimate regressor.
    target = rv.rolling(21).sum().shift(-21).to_numpy()
    leaked = rv.shift(-1).to_numpy()
    placebo = rv.sample(frac=1.0, random_state=4242).to_numpy()

    train = np.arange(22, 579)      # t + 21 < 600 keeps every training target out of the test rows
    test = np.arange(600, 860)
    realised = target[test]

    def mean_qlike(extra) -> float:
        matrix = np.column_stack([design, extra]) if extra is not None else design
        beta = har.fit(matrix[train], target[train])
        forecast = har.predict(beta, matrix[test])
        assert (forecast > 0).all(), "forecast must be positive before QLIKE"
        return float(qlike(realised, forecast).mean())

    clean = mean_qlike(None)
    leak_drop = (clean - mean_qlike(leaked)) / clean
    placebo_drop = (clean - mean_qlike(placebo)) / clean

    assert leak_drop > PLANTED_LEAK_MIN_DROP, (
        f"planted leak only cut QLIKE by {leak_drop:.1%} (clean {clean:.4f})"
    )
    assert placebo_drop < PLACEBO_MAX_DROP, (
        f"placebo moved QLIKE by {placebo_drop:.1%}; the fall is not attributable to the leak"
    )
