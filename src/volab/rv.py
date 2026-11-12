"""Daily realised-variance proxies — PREREGISTRATION §3.1.

Every function takes a frame indexed by session date carrying the four adjusted OHLC columns
(§2.3: adjusted fields only) and returns a float Series on the same index.

Nothing is filled, forward-filled or dropped. The first row of `overnight` is NaN because there
is no previous adjusted close, and it stays NaN — a sentinel for "cannot be computed", not a
missing value to repair.

With consistent OHLC (H >= max(O, C) and L <= min(O, C)) both range estimators are non-negative,
because H/L >= max(O, C)/min(O, C) implies ln(H/L) >= |ln(C/O)| and 0.5 > 2·ln(2) − 1. A negative
value therefore flags violated input rather than a bug in this module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ADJ_OPEN = "adjOpen"
ADJ_HIGH = "adjHigh"
ADJ_LOW = "adjLow"
ADJ_CLOSE = "adjClose"
_REQUIRED_COLUMNS = (ADJ_OPEN, ADJ_HIGH, ADJ_LOW, ADJ_CLOSE)

_TWO_LOG_TWO = 2.0 * np.log(2.0)
_GK_WEIGHT = _TWO_LOG_TWO - 1.0          # 2·ln(2) − 1


def _check(frame: pd.DataFrame) -> None:
    """Reject raw OHLC: §2.3 is explicit that only adjusted fields feed any estimate."""
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise KeyError(f"frame is missing adjusted column(s): {missing}")


def _log_range(frame: pd.DataFrame) -> pd.Series:
    return np.log(frame[ADJ_HIGH] / frame[ADJ_LOW])


def overnight(frame: pd.DataFrame) -> pd.Series:
    """r_on,t = ln(adjOpen_t / adjClose_{t−1}); NaN on the first row by construction."""
    _check(frame)
    return np.log(frame[ADJ_OPEN] / frame[ADJ_CLOSE].shift(1)).rename("r_on")


def session_return(frame: pd.DataFrame) -> pd.Series:
    """r_oc,t = ln(adjClose_t / adjOpen_t)."""
    _check(frame)
    return np.log(frame[ADJ_CLOSE] / frame[ADJ_OPEN]).rename("r_oc")


def garman_klass(frame: pd.DataFrame) -> pd.Series:
    """GK_t = ½·[ln(H/L)]² − (2·ln 2 − 1)·r_oc,t².

    A zero-range session returns exactly 0.0 rather than NaN: ln(1)² = 0, and O = H = L = C
    forces r_oc = 0, so both terms vanish.
    """
    _check(frame)
    log_range_squared = _log_range(frame) ** 2
    return (0.5 * log_range_squared - _GK_WEIGHT * session_return(frame) ** 2).rename("gk")


def parkinson(frame: pd.DataFrame) -> pd.Series:
    """Parkinson_t = [ln(H/L)]² / (4·ln 2)."""
    _check(frame)
    return (_log_range(frame) ** 2 / (2.0 * _TWO_LOG_TWO)).rename("parkinson")


def cross_term(frame: pd.DataFrame) -> pd.Series:
    """2·r_on·r_oc — reported by §3.1 and never dropped silently."""
    return (2.0 * overnight(frame) * session_return(frame)).rename("cross_term")


def rv_primary(frame: pd.DataFrame) -> pd.Series:
    """RV_t = r_on,t² + GK_t, the primary proxy: it covers the overnight gap and the session range."""
    return (overnight(frame) ** 2 + garman_klass(frame)).rename("rv")


def rv_close_to_close(frame: pd.DataFrame) -> pd.Series:
    """Robustness proxy: r_t², with r_t = r_on,t + r_oc,t."""
    r_t = overnight(frame) + session_return(frame)
    return (r_t ** 2).rename("rv_close_to_close")


def rv_overnight_parkinson(frame: pd.DataFrame) -> pd.Series:
    """Robustness proxy: r_on,t² + Parkinson_t."""
    return (overnight(frame) ** 2 + parkinson(frame)).rename("rv_overnight_parkinson")
