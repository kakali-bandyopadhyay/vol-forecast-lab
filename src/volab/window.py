"""The Feb–Apr 2020 window rule — PREREGISTRATION §3.5 and the §8 robustness reading.

A result built on a 30-day target is exposed to the window if the forecast date itself lies in
the window or if any session in its target window (t, t + 30 days] does.  The check is
session-based on purpose: a date such as 2020-01-17 is 30 calendar days before 2020-02-16, but
that endpoint is a weekend and no window session falls in the target, so it is not exposed.

Q3 and the daily VIX regression have no target; for those, only `session_in_window` is used.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW_START = pd.Timestamp("2020-02-15")
WINDOW_END = pd.Timestamp("2020-04-30")
TARGET_DAYS = 30


def _sessions(sessions) -> pd.DatetimeIndex:
    sessions = pd.DatetimeIndex(pd.to_datetime(sessions))
    if sessions.empty:
        raise ValueError("sessions is empty")
    if not sessions.is_monotonic_increasing or not sessions.is_unique:
        raise ValueError("sessions must be sorted ascending and unique")
    return sessions


def session_in_window(dates) -> np.ndarray:
    """Boolean mask: the date itself lies in [2020-02-15, 2020-04-30]."""
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    return np.asarray((index >= WINDOW_START) & (index <= WINDOW_END), dtype=bool)


def target_touches_window(dates, sessions) -> np.ndarray:
    """Boolean mask: t is in the window, or a session in (t, t + 30 days] is in the window.

    A prefix count over the session calendar keeps this session-based without a Python loop:
    `right - left` is the number of sessions in the target window, and its prefix-sum difference
    is how many of them lie in the window.
    """
    calendar = _sessions(sessions)
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    if index.empty:
        return np.zeros(0, dtype=bool)

    in_window = session_in_window(calendar).astype(np.int64)
    prefix = np.concatenate([[0], np.cumsum(in_window)])
    left = calendar.searchsorted(index, side="right")
    right = calendar.searchsorted(index + pd.Timedelta(days=TARGET_DAYS), side="right")
    touched = (prefix[right] - prefix[left]) > 0
    return np.asarray(touched | session_in_window(index), dtype=bool)
