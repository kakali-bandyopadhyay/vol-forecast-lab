"""Q2 — the ex-ante spread and ex-post premium — PREREGISTRATION §6.

Both series are 30-day total variances in decimal units:

    S_t = IV_t − F_t        IV_t from VIX (§6), F_t the raw M1 total
    P_t = IV_t − RV30_t

`S_t` and `P_t` use the same forecast dates so the two are directly comparable.  The regime
thresholds are not repeated here: the caller reads §3.5's terciles from the panel manifest and
passes them in, which keeps one source for 14.89 and 18.57.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from volab.stats_tests import hac_lag, nw_mean_test


def _difference(left, right, name: str):
    """Element-wise difference, broadcasting a scalar, preserving a Series index."""
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if left_array.ndim == 0 and right_array.ndim > 0:
        left_array = np.full(right_array.shape, float(left_array))
    elif right_array.ndim == 0 and left_array.ndim > 0:
        right_array = np.full(left_array.shape, float(right_array))
    if left_array.shape != right_array.shape:
        raise ValueError(
            f"{name}: inputs have shapes {left_array.shape} and {right_array.shape}"
        )
    if not np.isfinite(left_array).all() or not np.isfinite(right_array).all():
        raise ValueError(f"{name}: inputs contain a non-finite value")

    result = left_array - right_array
    index = None
    if isinstance(left, pd.Series):
        index = left.index
    elif isinstance(right, pd.Series):
        index = right.index
    if index is not None and len(index) == result.size:
        return pd.Series(result, index=index)
    return result


def ex_ante_spread(iv, f_m1):
    """S_t = IV_t − F_t, both 30-day total variances in decimal units."""
    return _difference(iv, f_m1, "ex_ante_spread")


def ex_post_premium(iv, rv30):
    """P_t = IV_t − RV30_t, both 30-day total variances in decimal units."""
    return _difference(iv, rv30, "ex_post_premium")


def regime(vix, low, high):
    """§3.5 terciles: low if VIX < low, mid if low ≤ VIX < high, high otherwise.

    `low` and `high` come from the panel manifest; no threshold is hard-coded here.
    """
    low = float(low)
    high = float(high)
    if not np.isfinite(low) or not np.isfinite(high) or low >= high:
        raise ValueError("regime thresholds must be finite and low < high")

    values = np.asarray(vix, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("vix contains a non-finite value")
    labels = np.where(values < low, "low", np.where(values < high, "mid", "high"))
    if isinstance(vix, pd.Series):
        return pd.Series(labels, index=vix.index, name="regime")
    if labels.ndim == 0:
        return str(labels)
    return labels


def _summary_row(values, group: str, label: str, **extra) -> dict:
    values = np.asarray(values, dtype=float)
    n = values.size
    if n == 0:
        raise ValueError(f"summary group {group}={label} is empty")
    if n < 2:
        hac_t = np.nan
        p_two_sided = np.nan
    else:
        hac_t, p_two_sided = nw_mean_test(values, hac_lag(n))
    return {
        **extra,
        "group": group,
        "label": label,
        "n": int(n),
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "share_positive": float(np.mean(values > 0)),
        "hac_t": float(hac_t),
        "p_two_sided": float(p_two_sided),
    }


def _ordered_labels(column: str, labels) -> list:
    unique = list(pd.unique(labels))
    if column == "regime":
        order = {"low": 0, "mid": 1, "high": 2}
        return sorted(unique, key=lambda value: order.get(str(value), 99))
    if column == "year":
        return sorted(unique, key=lambda value: (pd.isna(value), value))
    return sorted(unique, key=lambda value: str(value))


def summarise(values, labels) -> pd.DataFrame:
    """Per-group n, mean, median, share_positive, HAC t and two-sided p.

    `labels` is either a one-dimensional Series/array of group labels, or a DataFrame whose
    columns are grouping dimensions.  A DataFrame may carry a `sample` column (for example
    `full` / `ex_window`); when it does, every group is computed separately within each sample.
    The output includes an `all` row for each sample, followed by one row per grouping column.
    """
    series = pd.Series(values, dtype=float)
    if not np.isfinite(series).all():
        raise ValueError("values contain a non-finite value")

    if isinstance(labels, pd.DataFrame):
        frame = labels.copy()
    elif isinstance(labels, pd.Series):
        frame = labels.rename("label").to_frame()
    else:
        array = np.asarray(labels)
        if array.ndim != 1 or array.size != series.size:
            raise ValueError("labels must be one-dimensional and the same length as values")
        frame = pd.DataFrame({"label": array})

    if len(frame) != series.size:
        raise ValueError(
            f"labels have {len(frame)} rows but values have {series.size} observations"
        )
    frame.index = series.index

    sample_values = [None]
    if "sample" in frame.columns:
        sample_values = list(pd.unique(frame["sample"].dropna()))

    rows: list[dict] = []
    for sample in sample_values:
        if sample is None:
            mask = np.ones(series.size, dtype=bool)
            extra: dict = {}
        else:
            mask = np.asarray(frame["sample"] == sample, dtype=bool)
            extra = {"sample": sample}
        subset = series.to_numpy(dtype=float)[mask]
        rows.append(_summary_row(subset, "all", "all", **extra))

        for column in frame.columns:
            if column == "sample":
                continue
            column_labels = frame.loc[mask, column].to_numpy()
            for label in _ordered_labels(column, column_labels):
                selected = subset[np.asarray(column_labels == label, dtype=bool)]
                rows.append(_summary_row(selected, column, str(label), **extra))

    return pd.DataFrame(rows)
