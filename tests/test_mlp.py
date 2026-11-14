"""Synthetic tests for the M4 MLP — BUILD_GUIDE Step 10.

No panel file is read.  The annual-fold test uses the real XNYS calendar only to pin the 7-fold /
1,703-date grid.
"""

from __future__ import annotations

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest
import torch
from torch import nn

from volab.losses import qlike
from volab.models import har, mlp
from volab.models.baselines import rolling_mean_rv
from volab.splits import annual_walk_forward, walk_forward


def _training_data(n: int = 100, seed: int = 20261114):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    y = 0.3 * X[:, 0] - 0.2 * X[:, 1] + 0.1 * X[:, 2] + 0.05 * X[:, 3]
    y = y + rng.normal(scale=0.1, size=n)
    return X, y


def _predict(model, X) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        return model(torch.as_tensor(X, dtype=torch.float64)).numpy().reshape(-1)


# --- scaler ----------------------------------------------------------------


def test_scaler_fits_only_the_fit_block():
    X = np.arange(40, dtype=float).reshape(10, 4)
    rows = np.arange(0, 6)

    scaler = mlp.Scaler().fit(X, rows, cutoff_pos=6)

    np.testing.assert_allclose(scaler.mean_, X[:6].mean(axis=0))
    np.testing.assert_allclose(scaler.std_, X[:6].std(axis=0, ddof=0))

    with pytest.raises(ValueError, match="stopping block|forecast row"):
        mlp.Scaler().fit(X, np.array([0, 1, 2, 3, 4, 6]), cutoff_pos=6)
    with pytest.raises(ValueError, match="stopping block|forecast row"):
        mlp.Scaler().fit(X, np.array([0, 1, 2, 3, 4, 9]), cutoff_pos=6)


# --- determinism -----------------------------------------------------------


def test_fixed_seed_gives_identical_forecasts_and_different_seeds_differ():
    X, y = _training_data()
    config = {"hidden": 8, "layers": 1, "weight_decay": 0.0}

    first, _, _ = mlp.train(X[:70], y[:70], X[70:], y[70:], config, seed=0)
    second, _, _ = mlp.train(X[:70], y[:70], X[70:], y[70:], config, seed=0)
    third, _, _ = mlp.train(X[:70], y[:70], X[70:], y[70:], config, seed=1)

    np.testing.assert_array_equal(_predict(first, X), _predict(second, X))
    assert not np.allclose(_predict(third, X), _predict(first, X))


# --- shuffled target -------------------------------------------------------


def _persistent_rv(n: int = 700, seed: int = 2026) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_rv = np.zeros(n)
    for t in range(1, n):
        log_rv[t] = 0.95 * log_rv[t - 1] + rng.normal(scale=0.2)
    return pd.Series(np.exp(log_rv - 8.0))


def test_shuffled_target_mlp_loses_to_m0():
    rv = _persistent_rv()
    features = har.har_features(rv)
    per_session_iv = rv.rolling(5, min_periods=5).mean()
    X = mlp.log_inputs(features, per_session_iv)
    y_log = np.log(rv.shift(-1))
    usable = mlp.usable_rows(X, y_log, np.arange(len(rv)))
    train_idx = usable[:400]
    test_idx = usable[400:500]

    folded = mlp.fit_fold(
        X,
        mlp.shuffled_targets(y_log, mlp.usable_rows(X, y_log, train_idx), year=2020),
        train_idx,
        {"hidden": 8, "layers": 1, "weight_decay": 0.0},
    )
    forecast, _ = mlp.forecast_fold(folded, X, test_idx)

    realised = rv.to_numpy()[test_idx + 1]
    m0 = rolling_mean_rv(rv).to_numpy()[test_idx]
    assert np.isfinite(forecast).all() and np.all(forecast > 0)
    assert qlike(realised, forecast).mean() > qlike(realised, m0).mean()


# --- back-transform --------------------------------------------------------


class _Constant(nn.Module):
    def __init__(self, value: float):
        super().__init__()
        self.value = value

    def forward(self, X):
        return torch.full((X.shape[0], 1), self.value, dtype=X.dtype, device=X.device)


def test_back_transform_and_level_scale_seed_mean():
    np.testing.assert_allclose(
        mlp.back_transform(np.array([0.0, 1.0]), 0.5),
        np.exp(np.array([0.0, 1.0]) + 0.25),
        rtol=0,
        atol=1e-12,
    )

    X = np.ones((5, 4))
    scaler = mlp.Scaler().fit(X, np.arange(5), cutoff_pos=5)
    folded = mlp.FoldFit(
        scaler=scaler,
        models=(_Constant(0.0), _Constant(1.0), _Constant(1.0)),
        s2=np.zeros(3),
        epochs=np.zeros(3, dtype=int),
        cap_flags=np.zeros(3, dtype=bool),
        usable=np.arange(5),
        y_mean=0.0,
        y_std=1.0,
    )
    mean, per_seed = mlp.forecast_fold(folded, X, np.arange(5))

    np.testing.assert_allclose(per_seed[0], 1.0)
    np.testing.assert_allclose(per_seed[1], np.e)
    np.testing.assert_allclose(mean, (1.0 + 2.0 * np.e) / 3.0)
    assert not np.allclose(mean, np.exp((0.0 + 1.0 + 1.0) / 3.0))


# --- early stopping --------------------------------------------------------


def test_early_stopping_restores_the_best_epoch():
    X, y = _training_data(n=100)
    config = {"hidden": 8, "layers": 1, "weight_decay": 0.0}

    model, best_epoch, hit_cap = mlp.train(
        X[:70], y[:70], X[70:], y[70:], config, seed=0
    )

    history = model.stop_mse_history_
    assert isinstance(hit_cap, bool)
    assert best_epoch == int(np.argmin(history)) + 1
    with torch.no_grad():
        stop_mse = float(
            torch.mean(
                (model(torch.as_tensor(X[70:], dtype=torch.float64))
                 - torch.as_tensor(y[70:], dtype=torch.float64).reshape(-1, 1)) ** 2
            ).item()
        )
    assert stop_mse == pytest.approx(min(history), rel=1e-12, abs=1e-15)


def test_fit_fold_recovers_a_far_from_zero_target_level():
    rng = np.random.default_rng(7)
    X = rng.normal(size=(600, 4))
    y = -9.5 + 0.3 * X[:, 0] + rng.normal(scale=0.1, size=600)
    fitted = mlp.fit_fold(X, y, np.arange(500), {"hidden": 8, "layers": 1, "weight_decay": 0.0})
    mean, _ = mlp.forecast_fold(fitted, X, np.arange(500, 600))
    assert fitted.y_mean == pytest.approx(y[:400].mean())
    assert 0.9 < np.median(mean / np.exp(y[500:600])) < 1.1
    assert np.all(fitted.s2 < 0.05)


# --- grid and inputs -------------------------------------------------------


def test_grid_has_eighteen_unique_configs_and_log_inputs_rejects_zero():
    configs = {tuple(sorted(config.items())) for config in mlp.GRID}
    assert len(mlp.GRID) == 18
    assert len(configs) == 18

    features = pd.DataFrame({"rv_d": [1.0, 0.0], "rv_w": [1.0, 1.0], "rv_m": [1.0, 1.0]})
    with pytest.raises(ValueError, match="positive"):
        mlp.log_inputs(features, np.ones(2))


# --- annual grid -----------------------------------------------------------


def test_annual_test_grid_has_seven_folds_and_the_same_dates_as_walk_forward():
    sessions = xcals.get_calendar("XNYS").sessions_in_range("2010-01-04", "2026-10-12")
    target_ok = pd.Series(True, index=sessions)
    start = pd.Timestamp("2020-01-01")

    annual = annual_walk_forward(sessions, target_ok, start)
    regular = walk_forward(sessions, target_ok, start)

    annual_dates = np.concatenate([fold.forecast_idx for fold in annual])
    regular_dates = np.concatenate([fold.forecast_idx for fold in regular])
    assert len(annual) == 7
    assert annual_dates.size == 1703
    assert np.array_equal(annual_dates, regular_dates)
