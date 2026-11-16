"""Tests for the losses — BUILD_GUIDE Step 7 hand-computed cases."""

from __future__ import annotations

import numpy as np
import pytest

from volab.losses import qlike, squared_error


def test_qlike_is_zero_when_the_forecast_is_exact():
    assert qlike(np.array([0.004]), np.array([0.004])).item() == pytest.approx(0.0, abs=1e-15)


def test_qlike_doubled_realised_variance():
    assert np.asarray(qlike(np.array([2.0]), np.array([1.0]))).item() == pytest.approx(0.306853, rel=1e-6)


def test_qlike_halved_realised_variance():
    assert np.asarray(qlike(np.array([0.5]), np.array([1.0]))).item() == pytest.approx(0.193147, rel=1e-6)


def test_losses_return_one_value_per_date():
    rv = np.array([0.001, 0.002, 0.003])
    f = np.array([0.0011, 0.0019, 0.0032])

    assert qlike(rv, f).shape == (3,)
    assert squared_error(rv, f).shape == (3,)


def test_qlike_rejects_a_non_positive_forecast():
    for bad in (0.0, -1e-6):
        with pytest.raises(ValueError, match="strictly positive"):
            qlike(np.array([0.002]), np.array([bad]))


def test_qlike_rejects_a_non_finite_forecast():
    with pytest.raises(ValueError, match="finite"):
        qlike(np.array([0.002]), np.array([np.nan]))


def test_qlike_rejects_a_nan_target():
    with pytest.raises(ValueError, match="finite"):
        qlike(np.array([np.nan]), np.array([0.002]))


def test_qlike_rejects_a_non_positive_target():
    with pytest.raises(ValueError, match="strictly positive"):
        qlike(np.array([0.0]), np.array([0.002]))


def test_squared_error_matches_the_definition():
    rv = np.array([0.004, 0.009])
    f = np.array([0.001, 0.010])

    assert squared_error(rv, f) == pytest.approx([9e-6, 1e-6])


def test_losses_reject_mismatched_shapes():
    with pytest.raises(ValueError, match="shape"):
        qlike(np.array([0.1, 0.2]), np.array([0.1]))
