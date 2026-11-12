"""Tests for the model layer — BUILD_GUIDE Step 6 hand-computed cases.

Numeric expectations are the guide's, derived by hand; none is regenerated from the code under
test. The HAR recovery case draws independent features on purpose: on collinear columns the check
would pass for the wrong reason.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from volab.models import apply_floor, har, to_total
from volab.models.baselines import implied_variance, rolling_mean_rv

F_MIN = 3.722081e-04          # §3.4, from the frozen panel metadata


# --- HAR recovery -----------------------------------------------------------


def test_har_recovers_known_coefficients_from_noise_free_data():
    rng = np.random.default_rng(20261112)
    features = rng.normal(size=(400, 3))

    # Independent columns: with collinear regressors lstsq returns a minimum-norm solution and
    # the recovery below would pass without the coefficients being identified.
    correlation = np.corrcoef(features, rowvar=False)
    assert np.abs(correlation - np.eye(3)).max() < 0.2

    target = 0.1 + 0.4 * features[:, 0] + 0.3 * features[:, 1] + 0.2 * features[:, 2]

    beta = har.fit(features, target)

    assert beta == pytest.approx([0.1, 0.4, 0.3, 0.2], abs=1e-8)


# --- implied variance -------------------------------------------------------


def test_implied_variance_matches_the_hand_value():
    implied = implied_variance(pd.Series([20.0]))

    assert implied.iloc[0] == pytest.approx(0.00328767, rel=1e-6)
    assert implied.iloc[0] == pytest.approx(0.04 * 30.0 / 365.0)


# --- M1 versus M2 -----------------------------------------------------------


def test_har_x_with_a_zero_iv_coefficient_reproduces_har_exactly():
    """Exact equality, not approx: the extra term must contribute exactly zero."""
    features = np.array([
        [0.0010, 0.0020, 0.0030],
        [0.0040, 0.0050, 0.0060],
        [0.0070, 0.0080, 0.0090],
        [0.0022, 0.0011, 0.0033],
    ])
    per_session_iv = np.array([0.00331, 0.00417, 0.00275, 0.00360])
    har_x = np.column_stack([features, per_session_iv])

    beta_har = np.array([0.1, 0.4, 0.3, 0.2])
    beta_har_x = np.append(beta_har, 0.0)

    assert np.array_equal(har.predict(beta_har_x, har_x), har.predict(beta_har, features))


def test_design_appends_the_implied_column_last():
    rv = pd.Series(np.linspace(0.0005, 0.002, 40))
    features = har.har_features(rv)
    per_session_iv = np.linspace(0.001, 0.002, 40)

    assert har.design(features).shape == (40, 3)
    assert har.design(features, per_session_iv).shape == (40, 4)


# --- M0 baseline ------------------------------------------------------------


def test_m0_matches_the_hand_computation():
    """RV = 1 … 25: 11.0 at position 20, 15.0 at position 24, NaN before position 20."""
    rv = pd.Series(np.arange(1, 26, dtype=float))

    m0 = rolling_mean_rv(rv)

    assert m0.iloc[:20].isna().all()
    assert m0.iloc[20] == pytest.approx(11.0)
    assert m0.iloc[24] == pytest.approx(15.0)


# --- positivity rule --------------------------------------------------------


def test_floor_applies_to_the_total_and_counts_only_strict_violations():
    forecasts = np.array([F_MIN / 2, F_MIN, 2 * F_MIN])

    result = apply_floor(forecasts, F_MIN)

    assert result.total == pytest.approx([F_MIN, F_MIN, 2 * F_MIN])
    assert result.n_floored == 1
    assert result.raw[0] == pytest.approx(F_MIN / 2)


def test_floor_rejects_a_non_positive_f_min():
    with pytest.raises(ValueError, match="positive"):
        apply_floor(np.array([1.0]), 0.0)


# --- leakage and warm-up ----------------------------------------------------


def test_features_at_t_are_unchanged_when_later_rv_is_rewritten():
    rv = pd.Series(np.linspace(0.0005, 0.0025, 80))
    horizon = 50

    before = har.har_features(rv)
    rewritten = rv.copy()
    rewritten.iloc[horizon + 1:] *= 100.0
    after = har.har_features(rewritten)

    assert after.iloc[: horizon + 1].equals(before.iloc[: horizon + 1])


def test_the_first_twenty_one_rows_are_incomplete():
    rv = pd.Series(np.full(60, 0.001))

    usable = har.complete_cases(har.har_features(rv))

    assert not usable[:21].any()
    assert usable[21:].all()


def test_forecasting_on_an_incomplete_row_raises():
    rv = pd.Series(np.full(60, 0.001))
    features = har.har_features(rv)

    with pytest.raises(ValueError, match="NaN feature"):
        har.forecast_fold(np.ones(4), features, [0])


def test_fit_fold_drops_the_warm_up_rows_instead_of_absorbing_them():
    rng = np.random.default_rng(7)
    rv = pd.Series(rng.uniform(0.0005, 0.003, 60))
    features = har.har_features(rv)

    beta = har.fit_fold(features, rv.to_numpy(), np.arange(60))

    assert beta.shape == (4,)
    assert np.isfinite(beta).all()


# --- the implied column is part of the design, not an afterthought ----------


def test_nan_iv_in_a_training_row_is_dropped_not_fitted():
    rng = np.random.default_rng(11)
    rv = pd.Series(rng.uniform(0.0005, 0.003, 60))
    features = har.har_features(rv)
    per_session_iv = np.linspace(0.001, 0.002, 60)
    per_session_iv[30] = np.nan
    target = rv.to_numpy()

    beta = har.fit_fold(features, target, np.arange(60), per_session_iv)

    keep = np.ones(60, dtype=bool)
    keep[:21] = False          # warm-up: RV_m undefined
    keep[30] = False           # the row whose implied variance is missing
    expected = har.fit(har.design(features, per_session_iv)[keep], target[keep])

    assert np.array_equal(beta, expected)


def test_nan_iv_in_a_forecast_row_raises():
    rng = np.random.default_rng(12)
    rv = pd.Series(rng.uniform(0.0005, 0.003, 60))
    features = har.har_features(rv)
    per_session_iv = np.linspace(0.001, 0.002, 60)
    per_session_iv[45] = np.nan

    with pytest.raises(ValueError, match="NaN feature"):
        har.forecast_fold(np.ones(5), features, [45], per_session_iv)


def test_a_duplicated_column_makes_fit_raise():
    rng = np.random.default_rng(13)
    column = rng.normal(size=50)
    duplicated = np.column_stack([column, column])

    with pytest.raises(ValueError, match="rank deficient"):
        har.fit(duplicated, rng.normal(size=50))


# --- per-session to total, then floor ---------------------------------------


def test_to_total_multiplies_by_the_calendar_count():
    total = to_total(np.array([0.0001, 0.0002]), np.array([21, 20]))

    assert total == pytest.approx([0.0021, 0.0040])


def test_to_total_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="shape"):
        to_total(np.array([0.0001, 0.0002]), np.array([21]))


def test_to_total_rejects_a_non_positive_count():
    with pytest.raises(ValueError, match="positive"):
        to_total(np.array([0.0001]), np.array([0]))


def test_apply_floor_rejects_a_nan_forecast():
    with pytest.raises(ValueError, match="non-finite"):
        apply_floor(np.array([F_MIN, np.nan]), F_MIN)


# --- strengthened design and fold tests -------------------------------------


def test_design_columns_are_the_har_features_then_the_implied_column():
    rv = pd.Series(np.linspace(0.0005, 0.002, 40))
    features = har.har_features(rv)
    per_session_iv = np.linspace(0.001, 0.002, 40)

    three = har.design(features)
    four = har.design(features, per_session_iv)

    assert four.shape == (40, 4)
    np.testing.assert_array_equal(four[:, :3], three)
    np.testing.assert_array_equal(four[:, 3], per_session_iv)
    np.testing.assert_array_equal(three, features[["rv_d", "rv_w", "rv_m"]].to_numpy())


def test_fit_fold_drops_the_warm_up_rows_exactly():
    rng = np.random.default_rng(21)
    rv = pd.Series(rng.uniform(0.0005, 0.003, 60))
    features = har.har_features(rv)
    target = rv.to_numpy()

    beta = har.fit_fold(features, target, np.arange(60))
    expected = har.fit(har.design(features)[21:], target[21:])

    assert np.array_equal(beta, expected)


def test_fit_fold_ignores_the_target_outside_train_idx():
    rng = np.random.default_rng(22)
    rv = pd.Series(rng.uniform(0.0005, 0.003, 60))
    features = har.har_features(rv)
    train_idx = np.arange(21, 40)
    target = rv.to_numpy()
    perturbed = target.copy()
    perturbed[40:] *= 100.0

    assert np.array_equal(
        har.fit_fold(features, target, train_idx),
        har.fit_fold(features, perturbed, train_idx),
    )
