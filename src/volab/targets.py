"""Forward realised-variance targets — PREREGISTRATION §3.2.

RV_t^{30} sums daily RV over the sessions in the window (t, t + 30 calendar days]: t excluded,
t + 30 included. The window is counted on the exchange calendar, never on the rows that happen
to be present in the data, so a missing observation cannot quietly shrink it — it drops the
whole target instead. A target containing an excluded session (§2.3) is dropped and logged
rather than computed on a partial window.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

HORIZON_DAYS = 30

logger = logging.getLogger(__name__)


def window_sessions(sessions: pd.DatetimeIndex, t, horizon_days: int = HORIZON_DAYS) -> pd.DatetimeIndex:
    """Calendar sessions s with t < date(s) <= t + horizon_days.

    `sessions` must be sorted and unique, as an exchange calendar is.
    """
    if len(sessions) == 0:
        raise ValueError("sessions is empty; pass an exchange-calendar session index")

    start = pd.Timestamp(t)
    end = start + pd.Timedelta(days=horizon_days)
    left = sessions.searchsorted(start, side="right")     # first session strictly after t
    right = sessions.searchsorted(end, side="right")      # first session strictly after t + 30d
    if right >= len(sessions):
        # Fail loudly rather than silently summing a short window: a truncated calendar
        # understates both the target and N_t. Testing `sessions[-1] >= end` would not do,
        # because the window end can fall on a weekend or a holiday with no session on it.
        # The calendar must extend past the window end, which exchange_calendars does.
        raise ValueError(
            f"calendar ends {sessions[-1].date()}, with no session after the window end "
            f"{end.date()}; pass sessions that extend past t + horizon"
        )
    return sessions[left:right]


def n_sessions(sessions: pd.DatetimeIndex, t, horizon_days: int = HORIZON_DAYS) -> int:
    """N_t: the number of sessions in the window, taken from the calendar."""
    return int(len(window_sessions(sessions, t, horizon_days)))


def target_30d(
    rv: pd.Series,
    sessions: pd.DatetimeIndex,
    t,
    *,
    horizon_days: int = HORIZON_DAYS,
    excluded=(),
) -> float:
    """Sum RV over the window at t, or NaN when the target cannot be formed.

    Drops the target (returning NaN and logging) if the window is empty, contains an excluded
    session, or lacks an RV value for any session in it.
    """
    t = pd.Timestamp(t)
    window = window_sessions(sessions, t, horizon_days)

    if len(window) == 0:
        logger.warning("target at %s: no sessions in (%s, +%dd]",
                       t.date(), t.date(), horizon_days)
        return np.nan

    excluded_set = {pd.Timestamp(value) for value in excluded}
    blocked = [session for session in window if session in excluded_set]
    if blocked:
        logger.warning("target at %s: dropped, %d excluded session(s) in window (%s)",
                       t.date(), len(blocked),
                       ", ".join(str(session.date()) for session in blocked[:5]))
        return np.nan

    values = rv.reindex(window)
    missing = int(values.isna().sum())
    if missing:
        logger.warning("target at %s: dropped, RV unavailable for %d of %d sessions in window",
                       t.date(), missing, len(window))
        return np.nan

    return float(values.sum())


def forward_targets(
    rv: pd.Series,
    sessions: pd.DatetimeIndex,
    *,
    horizon_days: int = HORIZON_DAYS,
    excluded=(),
) -> pd.DataFrame:
    """Target and N_t for every date in `rv.index`, indexed like `rv`.

    Columns: `rv_30d` (NaN where the target is dropped) and `n_sessions` (always from the
    calendar, so it is populated even where the target is NaN).
    """
    totals = [
        target_30d(rv, sessions, t, horizon_days=horizon_days, excluded=excluded)
        for t in rv.index
    ]
    counts = [n_sessions(sessions, t, horizon_days) for t in rv.index]
    return pd.DataFrame({"rv_30d": totals, "n_sessions": counts}, index=rv.index)


def per_session_mean(targets: pd.DataFrame) -> pd.Series:
    """§3.2: models forecast the per-session mean, so the total is the mean × N_t."""
    return (targets["rv_30d"] / targets["n_sessions"]).rename("rv_30d_per_session")
