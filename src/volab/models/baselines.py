"""M0 persistence baseline and the R1 implied-variance reference — §4 and §6.

M0 is the naive benchmark the HAR models must beat. R1 is a reference series used in §6 and is
never ranked against the models (§4 table).
"""

from __future__ import annotations

import pandas as pd

M0_WINDOW = 21          # §4: "per-session mean RV over the previous 21 sessions"
IV_DAYS = 30.0          # §3.3: the VIX horizon
IV_YEAR_DAYS = 365.0


def rolling_mean_rv(rv: pd.Series, window: int = M0_WINDOW) -> pd.Series:
    """M0: mean RV over the `window` sessions ending at t, i.e. t-20 … t inclusive.

    The window ends *at* t because RV_t is known at the close of the forecast date — "the previous
    21 sessions" is read as the 21 sessions ending on t, not the 21 sessions before it. The first
    window-1 rows are NaN: fewer than a full window is not a forecast.
    """
    return rv.rolling(window=window, min_periods=window).mean().rename("m0")


def implied_variance(vix) -> pd.Series:
    """R1: IV_t = (VIX_t / 100)² · 30/365 — total variance over the next 30 calendar days.

    On the total scale, directly comparable with RV_t^30 and with F_min. Deliberately *not*
    divided by N_t here: M2 uses IV_t / N_t as a per-session regressor, and that division belongs
    where the design matrix is built.
    """
    return ((vix / 100.0) ** 2 * (IV_DAYS / IV_YEAR_DAYS)).rename("iv")
