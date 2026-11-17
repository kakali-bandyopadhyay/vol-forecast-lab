"""Contract tests for drift metrics; implementation is pending in src/volab/drift.py."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from volab import drift

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_psi_matches_hand_computed_two_bin_value() -> None:
    assert drift.psi(np.array([0.5, 0.5]), np.array([0.8, 0.2])) == pytest.approx(0.415888, abs=1e-6)


def test_psi_identical_distributions_is_zero() -> None:
    assert drift.psi(np.array([0.3, 0.7]), np.array([0.3, 0.7])) == 0.0


def test_psi_empty_bin_uses_epsilon() -> None:
    assert drift.psi(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(
        2 * (1 - 1e-6) * np.log(1e6)
    )


def test_decile_edges_use_training_values_only() -> None:
    train = np.arange(1.0, 101.0)
    baseline = drift.decile_edges(train)
    assert baseline.shape == (9,)
    np.testing.assert_allclose(baseline, np.quantile(train, np.arange(0.1, 1.0, 0.1)))


def test_bin_props_equal_edge_goes_to_upper_bin() -> None:
    props = drift.bin_props(np.array([0.0, 1.0, 2.0]), np.array([1.0]))
    np.testing.assert_allclose(props, [1 / 3, 2 / 3])


def test_monthly_psi_edges_are_frozen_at_train_end(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[np.ndarray] = []
    original = drift.decile_edges

    def record_edges(values):
        array = np.asarray(values)
        seen.append(array.copy())
        return original(array)

    monkeypatch.setattr(drift, "decile_edges", record_edges)
    dates = pd.date_range("2020-01-01", periods=40, freq="D")
    values = np.arange(40.0)
    frame = pd.DataFrame({"date": dates, "x": values})
    drift.monthly_psi(frame, train_end="2020-01-20")
    first_edges_input = seen[-1]
    changed = frame.copy()
    changed.loc[changed["date"] > "2020-01-20", "x"] = 1e9
    drift.monthly_psi(changed, train_end="2020-01-20")
    np.testing.assert_array_equal(seen[-1], first_edges_input)
    np.testing.assert_array_equal(first_edges_input, np.arange(20.0))


def test_qlike_degradation_requires_consecutive_month_ends() -> None:
    ratios = pd.Series([1.2, 1.2, 1.2, 1.0, 1.2, 1.2, 1.2])
    actual = drift.degradation(ratios, threshold=1.10, months=3)
    assert actual.tolist() == [False, False, True, False, False, False, True]

    assert drift.degradation(pd.Series([1.2] * 4), threshold=1.10, months=3).tolist() == [
        False, False, True, True,
    ]
    assert drift.degradation(
        pd.Series([1.2, np.nan, 1.2, 1.2, 1.2]), threshold=1.10, months=3
    ).tolist() == [False, False, False, False, True]


def test_qlike_ratio_matches_hand_computed_month_value() -> None:
    dates = pd.date_range("2020-01-01", periods=4, freq="D")
    losses = pd.DataFrame(
        [(model, date, value) for model, values in {"M1": [1, 3, 5, 7], "M2": [2, 4, 8, 10]}.items()
         for date, value in zip(dates, values)],
        columns=["model", "date", "qlike"],
    )
    actual = drift.qlike_ratio(losses, window=2)
    assert actual.loc[actual["month"] == "2020-01", "qlike_ratio"].iloc[0] == pytest.approx(9 / 6)


def test_qlike_ratio_first_three_months_are_nan_for_a_63_session_window() -> None:
    losses = pd.read_parquet(REPO / "results" / "losses.parquet")
    actual = drift.qlike_ratio(losses, window=63)
    early = actual[actual["month"].isin(["2020-01", "2020-02", "2020-03"])]
    assert len(early) == 3
    assert early["qlike_ratio"].isna().all()


def test_monthly_psi_skips_a_window_starting_on_or_before_train_end() -> None:
    dates = pd.date_range("2020-01-01", "2020-03-31", freq="D")
    frame = pd.DataFrame({"date": dates, "x": np.arange(len(dates), dtype=float)})
    actual = drift.monthly_psi(frame, train_end="2020-01-20", window=20)
    assert actual["month"].tolist() == ["2020-02", "2020-03"]

