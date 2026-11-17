"""Drift metrics for the monthly drift report (Step 12)."""

from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-6


def psi(expected, actual, eps: float = EPS) -> float:
    """Population stability index; exact zeros become eps and are not renormalised."""
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)
    expected = np.where(expected == 0, eps, expected)
    actual = np.where(actual == 0, eps, actual)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def decile_edges(values) -> np.ndarray:
    """Return the nine inner decile edges, computed after dropping NaN."""
    array = np.asarray(values, dtype=float)
    array = array[~np.isnan(array)]
    return np.quantile(array, np.arange(0.1, 1.0, 0.1))


def bin_props(values, edges) -> np.ndarray:
    """Return ten bin proportions; a value equal to an edge goes to the upper bin."""
    array = np.asarray(values, dtype=float)
    array = array[~np.isnan(array)]
    if len(array) == 0:
        return np.zeros(len(edges) + 1, dtype=float)
    bins = np.searchsorted(np.asarray(edges, dtype=float), array, side="right")
    return np.bincount(bins, minlength=len(edges) + 1) / len(array)


def monthly_psi(frame: pd.DataFrame, train_end, window: int = 126) -> pd.DataFrame:
    """Compute monthly PSI per input against deciles frozen at train_end."""
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"])
    train_end = pd.Timestamp(train_end)
    inputs = [column for column in data.columns if column != "date"]
    train = data[data["date"] <= train_end]

    edges = {}
    train_props = {}
    for name in inputs:
        values = train[name].to_numpy(dtype=float)
        values = values[~np.isnan(values)]
        edges[name] = decile_edges(values)
        train_props[name] = bin_props(values, edges[name])

    rows: list[dict] = []
    after = data[data["date"] > train_end]
    for month, group in after.groupby(after["date"].dt.to_period("M"), sort=True):
        month_end = group["date"].max()
        window_rows = data[data["date"] <= month_end].tail(window)
        if window_rows.empty or window_rows["date"].iloc[0] <= train_end:
            continue
        for name in inputs:
            window_values = window_rows[name].to_numpy(dtype=float)
            value = psi(train_props[name], bin_props(window_values, edges[name]))
            rows.append({"month": str(month), "input": name, "psi": value})
    return pd.DataFrame(rows, columns=["month", "input", "psi"])


def qlike_ratio(losses: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    """Return month-end M2/M1 rolling-mean QLIKE ratios."""
    data = losses[["model", "date", "qlike"]].copy()
    data["date"] = pd.to_datetime(data["date"])
    wide = data.pivot(index="date", columns="model", values="qlike").sort_index()
    m2 = wide["M2"].rolling(window, min_periods=window).mean()
    m1 = wide["M1"].rolling(window, min_periods=window).mean()
    ratio = m2 / m1
    if ratio.empty:
        return pd.DataFrame(columns=["month", "qlike_ratio"])
    months = ratio.index.to_period("M").astype(str)
    result = pd.DataFrame({"month": months, "qlike_ratio": ratio.to_numpy()})
    return result.groupby("month", sort=True).tail(1).reset_index(drop=True)


def degradation(ratios, threshold: float = 1.10, months: int = 3) -> pd.Series:
    """True where the last `months` values are all above threshold; NaN breaks a run."""
    series = pd.Series(ratios, dtype=float)
    flags = series.rolling(months, min_periods=months).apply(
        lambda values: bool(np.all(values > threshold)), raw=True
    )
    return flags.fillna(0).astype(bool)
