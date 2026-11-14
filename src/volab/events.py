"""Q3 — CPI and FOMC event study — PREREGISTRATION §7.1–7.4.

The response is the log of a daily variance component plus a small constant: `log(x + c)`, with
x one of `r_on²`, `r_oc²` or GK.  `c` is 1% of the median of x over the estimation rows, fixed
once per component and reused in every robustness run.  Regressors are the event indicators, the
previous session's HAR components in logs, and Tuesday–Friday dummies with Monday as the
reference.  HAC standard errors use lag 5, as §7.3 and §7.4 specify.

The five emergency-control dates are fixed by §7.1 and are separate from the scheduled FOMC
calendar; 2020-03-16 is the first session after the Sunday 2020-03-15 action, not a scheduled
FOMC meeting, so it is not in `fomc.csv`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from volab.models import har
from volab.stats_tests import ols_hac

EMERGENCY_CONTROL = (
    pd.Timestamp("2010-05-10"),
    pd.Timestamp("2019-10-11"),
    pd.Timestamp("2020-03-03"),
    pd.Timestamp("2020-03-16"),
    pd.Timestamp("2020-03-23"),
)

EVENT_HAC_LAG = 5
WEEKDAY_NAMES = ("tue", "wed", "thu", "fri")
_COMPONENTS = {
    "on": "r_on",
    "overnight": "r_on",
    "oc": "r_oc",
    "session": "r_oc",
    "gk": "gk",
    "garman_klass": "gk",
}


def load_event_dates(events_dir) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Return `(fomc_dates, cpi_dates)` for rows with `in_event_sample == true`.

    The CPI calendar's `effective_session` column is deliberately not used: the event date is the
    release date, and the two holiday releases that are first priced at the next open already
    carry `in_event_sample == false`.
    """
    directory = Path(events_dir)

    def _dates(filename: str) -> pd.DatetimeIndex:
        frame = pd.read_csv(directory / filename)
        for column in ("event_date", "in_event_sample"):
            if column not in frame.columns:
                raise ValueError(f"{filename} is missing the {column} column")
        keep = (
            frame["in_event_sample"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
        )
        dates = pd.to_datetime(frame.loc[keep, "event_date"], errors="raise")
        return pd.DatetimeIndex(dates).sort_values().unique()

    return _dates("fomc.csv"), _dates("cpi.csv")


def indicators(index, fomc, cpi, emg) -> pd.DataFrame:
    """0/1 indicators for CPI, FOMC and emergency-control dates, aligned to `index`.

    An event date that is not a session in `index` raises rather than being silently dropped: a
    calendar mistake must not turn into an unrecorded missing observation.
    """
    index = pd.DatetimeIndex(pd.to_datetime(index))
    if not index.is_unique:
        raise ValueError("index must be unique")

    frame = pd.DataFrame(0, index=index, columns=["cpi", "fomc", "emg"], dtype="int64")
    for column, dates in (("cpi", cpi), ("fomc", fomc), ("emg", emg)):
        event_dates = pd.DatetimeIndex(pd.to_datetime(dates))
        if event_dates.empty:
            continue
        missing = event_dates[~event_dates.isin(index)]
        if len(missing):
            raise ValueError(
                f"{column} event date(s) not in the index: {list(missing)}"
            )
        frame.loc[event_dates, column] = 1
    return frame


def lagged_rv(rv) -> pd.DataFrame:
    """HAR components at t−1, logged; the first 22 rows are NaN."""
    series = pd.Series(rv, dtype=float)
    defined = series.dropna()
    if not np.isfinite(defined).all() or (defined <= 0).any():
        raise ValueError("rv must be positive and finite where defined")

    features = har.har_features(series).shift(1)
    logged = np.log(features)
    return logged.rename(
        columns={"rv_d": "log_rv_d", "rv_w": "log_rv_w", "rv_m": "log_rv_m"}
    )


def component_values(panel, component: str) -> pd.Series:
    """The variance component x: r_on², r_oc² or GK."""
    if component not in _COMPONENTS:
        raise ValueError(f"unknown component {component!r}")
    column = _COMPONENTS[component]
    raw = pd.Series(panel[column], index=panel.index, dtype=float)
    if not np.isfinite(raw).all():
        raise ValueError(f"{column} contains a non-finite value")
    values = raw ** 2 if column in ("r_on", "r_oc") else raw
    if (values < 0).any():
        raise ValueError(f"{column} contains a negative variance")
    return values.rename(component)


def design(panel, ind, component, *, weekday=True, c=None) -> tuple[pd.Series, pd.DataFrame]:
    """Return `(y, X)` for one component, aligned to `panel.index`.

    `y = log(x + c)`.  `run` always passes the fixed estimation-row `c`; the default here is a
    convenience for inspection and is not used by any reported run.
    """
    x = component_values(panel, component)
    if c is None:
        c = 0.01 * float(np.median(x.to_numpy(dtype=float)))
    c = float(c)
    if not np.isfinite(c) or c <= 0:
        raise ValueError("c must be positive and finite")

    y = np.log(x + c)
    log_rv = lagged_rv(panel["rv"])

    aligned = ind.reindex(panel.index)
    if aligned[["cpi", "fomc", "emg"]].isna().any().any():
        raise ValueError("ind does not cover every panel date")

    columns: dict[str, np.ndarray] = {
        "const": np.ones(len(panel), dtype=float),
        "cpi": aligned["cpi"].to_numpy(dtype=float),
        "fomc": aligned["fomc"].to_numpy(dtype=float),
        "emg": aligned["emg"].to_numpy(dtype=float),
        "log_rv_d": log_rv["log_rv_d"].to_numpy(dtype=float),
        "log_rv_w": log_rv["log_rv_w"].to_numpy(dtype=float),
        "log_rv_m": log_rv["log_rv_m"].to_numpy(dtype=float),
    }
    if weekday:
        day_of_week = panel.index.day_of_week
        for number, name in enumerate(WEEKDAY_NAMES, start=1):
            dummy = (day_of_week == number).astype(float)
            if dummy.sum() == 0:
                raise ValueError(f"weekday dummy {name} is all zero")
            columns[name] = dummy

    return y, pd.DataFrame(columns, index=panel.index)


def _row_mask(rows, n_rows: int) -> np.ndarray:
    array = np.asarray(rows)
    if array.dtype == bool:
        if array.shape != (n_rows,):
            raise ValueError(f"rows has shape {array.shape}, expected ({n_rows},)")
        return array.copy()
    positions = array.astype(int)
    if positions.ndim != 1:
        raise ValueError("rows must be a boolean mask or a one-dimensional position array")
    if ((positions < 0) | (positions >= n_rows)).any():
        raise ValueError("rows contains a position outside the panel")
    mask = np.zeros(n_rows, dtype=bool)
    mask[positions] = True
    if mask.sum() != positions.size:
        raise ValueError("rows contains a duplicate position")
    return mask


def _tidy_ols(
    y,
    X: pd.DataFrame,
    ind: pd.DataFrame,
    mask: np.ndarray,
    *,
    lag: int,
    component: str,
    run_label: str,
    c: float,
) -> pd.DataFrame:
    if mask.sum() == 0:
        raise ValueError("the selected rows are empty")

    response = np.asarray(y, dtype=float)[mask]
    design_matrix = X.loc[mask]
    if not np.isfinite(response).all() or not np.isfinite(design_matrix.to_numpy()).all():
        raise ValueError("the selected rows contain a non-finite value")

    result = ols_hac(response, design_matrix.to_numpy(dtype=float), lag)
    beta = result["beta"]
    se = result["se"]
    t = result["t"]

    ci_low = beta - 1.96 * se
    ci_high = beta + 1.96 * se
    effect = np.exp(beta) - 1.0
    effect_low = np.exp(ci_low) - 1.0
    effect_high = np.exp(ci_high) - 1.0
    p_one_sided = stats.norm.sf(t)
    p_two_sided = 2.0 * stats.norm.sf(np.abs(t))

    selected = ind.loc[mask]
    n_obs = int(mask.sum())
    n_cpi = int(selected["cpi"].sum())
    n_fomc = int(selected["fomc"].sum())
    n_emg = int(selected["emg"].sum())
    n_dual = int(((selected["cpi"] == 1) & (selected["fomc"] == 1)).sum())

    return pd.DataFrame(
        {
            "term": list(X.columns),
            "beta": beta,
            "se": se,
            "t": t,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "effect": effect,
            "effect_ci_low": effect_low,
            "effect_ci_high": effect_high,
            "p_one_sided": p_one_sided,
            "p_two_sided": p_two_sided,
            "n_obs": n_obs,
            "n_cpi": n_cpi,
            "n_fomc": n_fomc,
            "n_emg": n_emg,
            "n_dual": n_dual,
            "c": float(c),
            "component": component,
            "run": run_label,
        }
    )


def run(panel, ind, component, c, *, rows, weekday=True, label) -> pd.DataFrame:
    """Run one §7.3 regression and return a tidy row per term."""
    if c is None or not np.isfinite(c) or c <= 0:
        raise ValueError("c must be positive and finite")
    y, X = design(panel, ind, component, weekday=weekday, c=c)
    mask = _row_mask(rows, len(panel))
    if weekday:
        dummies = [name for name in WEEKDAY_NAMES if name in X.columns]
        if (X.loc[mask, dummies].sum(axis=0) == 0).any():
            raise ValueError("a weekday dummy is all zero in the selected rows")
    return _tidy_ols(
        y, X, ind, mask, lag=EVENT_HAC_LAG, component=component, run_label=label, c=c
    )


def vix_change(panel, ind, rows) -> pd.DataFrame:
    """§7.4: ΔVIX on events, lagged VIX, lagged ΔVIX and weekday dummies, HAC lag 5."""
    vix = pd.Series(panel["vix"], index=panel.index, dtype=float)
    if not np.isfinite(vix).all():
        raise ValueError("vix contains a non-finite value")
    delta = vix.diff()

    aligned = ind.reindex(panel.index)
    if aligned[["cpi", "fomc", "emg"]].isna().any().any():
        raise ValueError("ind does not cover every panel date")

    columns: dict[str, np.ndarray] = {
        "const": np.ones(len(panel), dtype=float),
        "cpi": aligned["cpi"].to_numpy(dtype=float),
        "fomc": aligned["fomc"].to_numpy(dtype=float),
        "emg": aligned["emg"].to_numpy(dtype=float),
        "vix_lag1": vix.shift(1).to_numpy(dtype=float),
        "dvix_lag1": delta.shift(1).to_numpy(dtype=float),
    }
    day_of_week = panel.index.day_of_week
    for number, name in enumerate(WEEKDAY_NAMES, start=1):
        columns[name] = (day_of_week == number).astype(float)
    X = pd.DataFrame(columns, index=panel.index)

    mask = _row_mask(rows, len(panel))
    return _tidy_ols(
        delta,
        X,
        ind,
        mask,
        lag=EVENT_HAC_LAG,
        component="vix_change",
        run_label="vix_change",
        c=np.nan,
    )
