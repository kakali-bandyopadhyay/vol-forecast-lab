"""Walk-forward splits — PREREGISTRATION §5.1–5.2, and the §9.3 no-overlap invariant.

Expanding window, refitted every 21 forecast dates. The parameters fitted at a refit date r are
used for r and the next 20 forecast dates, so there are exactly `refit_every` forecast dates per
fold except possibly the last.

Training rows for a refit at r are sessions s whose target window closes before r:
    s + gap_days < r
Because r is always a session and the target window is (s, s + gap_days], this is equivalent to
"the last session in s's target window is strictly before r" — the no-overlap rule of §9.3.
Checking it once, at r, is enough: every later forecast in the same block is further away.

The training start never moves (§5.2). Training rows must carry a target; a session whose target
is NaN is dropped, which is why the mask is passed in rather than inferred.

Refit cadence counts forecast dates, not sessions. A session with no target is skipped by
`_forecast_positions` and therefore does not count toward `refit_every`. §5.2 says "every 21
sessions"; on the frozen snapshot no session is excluded, so the two readings coincide, but the
code counts forecast dates and that is what the tests pin.

Both grids — every 21 forecast dates, and once per calendar year — are built by the same
`_forecast_positions` and `_make_fold`, so they can differ only in how dates are grouped, never in
which dates are eligible or which rows are trained on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

REFIT_EVERY = 21
GAP_DAYS = 30


@dataclass(frozen=True)
class Fold:
    """One refit: where the parameters are fitted, and which forecasts they produce.

    `train_idx` and `forecast_idx` are integer positions into the `sessions` index passed to the
    splitter, so they can be used with `.iloc` on any frame sharing that session index.
    """

    refit_date: pd.Timestamp
    train_idx: np.ndarray
    forecast_idx: np.ndarray


def _target_mask(sessions: pd.DatetimeIndex, target_ok) -> np.ndarray:
    """Boolean array aligned to `sessions`; a date absent from a Series counts as unavailable."""
    if isinstance(target_ok, pd.Series):
        return target_ok.reindex(sessions).eq(True).to_numpy(dtype=bool)
    values = np.asarray(target_ok)
    if values.shape != (len(sessions),):
        raise ValueError(
            f"target_ok has shape {values.shape}, expected ({len(sessions)},) to match sessions"
        )
    return values.astype(bool)


def _validate_inputs(
    sessions: pd.DatetimeIndex,
    *,
    gap_days: int,
    refit_every: int | None = None,
) -> pd.DatetimeIndex:
    """Shared argument checks. Every public grid calls this before touching the data.

    Validating up front, rather than incidentally inside a loop, is what stops a malformed
    argument from producing an empty fold list that looks like a legitimate empty window.
    """
    sessions = pd.DatetimeIndex(sessions)
    if not sessions.is_monotonic_increasing:
        raise ValueError("sessions must be sorted ascending")
    if gap_days < 0:
        raise ValueError("gap_days must be non-negative")
    if refit_every is not None and refit_every < 1:
        raise ValueError("refit_every must be at least 1")
    return sessions


def _forecast_positions(
    sessions: pd.DatetimeIndex,
    target_ok,
    start,
    end,
) -> tuple[np.ndarray, np.ndarray]:
    """The target mask and the positions of the forecast dates in [start, end]."""
    mask = _target_mask(sessions, target_ok)
    if not mask.any():
        return mask, np.zeros(0, dtype=np.int64)

    end_ts = pd.Timestamp(sessions[np.flatnonzero(mask)[-1]] if end is None else end)
    in_window = (sessions >= pd.Timestamp(start)) & (sessions <= end_ts) & mask
    return mask, np.flatnonzero(in_window).astype(np.int64)


def _make_fold(
    sessions: pd.DatetimeIndex,
    mask: np.ndarray,
    block: np.ndarray,
    gap_days: int,
) -> Fold:
    """Train on every target-bearing session before `block[0] - gap_days`, forecast `block`."""
    refit_date = sessions[block[0]]
    cutoff = refit_date - pd.Timedelta(days=gap_days)      # s + gap_days < r  <=>  s < r - gap
    train_positions = np.flatnonzero(mask & (sessions < cutoff)).astype(np.int64)
    return Fold(
        refit_date=refit_date,
        train_idx=train_positions,
        forecast_idx=block.astype(np.int64),
    )


def walk_forward(
    sessions: pd.DatetimeIndex,
    target_ok,
    start,
    end=None,
    *,
    refit_every: int = REFIT_EVERY,
    gap_days: int = GAP_DAYS,
) -> list[Fold]:
    """Expanding-window folds, refitted every `refit_every` forecast dates.

    `target_ok` is a boolean Series indexed by date (a date missing from it is treated as
    unavailable) or a boolean array aligned with `sessions`. Forecast dates are the sessions in
    [start, end] that carry a target; with `end=None` the last such session is used.

    Forecast dates are partitioned into consecutive blocks of `refit_every`; each block is one
    fold, refitted on its first date. Every forecast date lands in exactly one fold. The count is
    of forecast dates, not sessions: a date with no target is skipped and does not consume a slot.
    """
    sessions = _validate_inputs(sessions, gap_days=gap_days, refit_every=refit_every)
    mask, forecast_positions = _forecast_positions(sessions, target_ok, start, end)
    return [
        _make_fold(sessions, mask, forecast_positions[offset: offset + refit_every], gap_days)
        for offset in range(0, forecast_positions.size, refit_every)
    ]


def annual_walk_forward(
    sessions: pd.DatetimeIndex,
    target_ok,
    start,
    end=None,
    *,
    gap_days: int = GAP_DAYS,
) -> list[Fold]:
    """The same expanding window, refitted once per calendar year (§5.2, for M4).

    Forecast dates are identical to `walk_forward`'s — this grid changes when the parameters are
    refreshed, never which dates are forecast — so the two partitions agree by construction:
    both come from the same `_forecast_positions` and the same `_make_fold`. Each fold is refitted
    on the first forecast date of its calendar year.
    """
    sessions = _validate_inputs(sessions, gap_days=gap_days)
    mask, forecast_positions = _forecast_positions(sessions, target_ok, start, end)
    if forecast_positions.size == 0:
        return []

    years = sessions[forecast_positions].year
    return [
        _make_fold(sessions, mask, forecast_positions[years == year], gap_days)
        for year in np.unique(years)
    ]


def validation_bounds() -> tuple[pd.Timestamp, pd.Timestamp]:
    """§5.1 validation window, for M4 selection. Same splitter, different bounds."""
    return pd.Timestamp("2016-01-01"), pd.Timestamp("2019-12-31")


def early_stop_split(train_idx, fraction: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    """Chronological (fit, stop) split: the last ceil(fraction·n) rows form the stopping block.

    Deliberately a literal tail slice, matching §4.3's "last 20% of each training window". No
    purge is applied, so the boundary rows' 30-day targets extend into the stopping block. That
    overlap lies wholly inside the training window — it never reaches the train/forecast boundary
    that §9.3 protects — and purging it would be a deviation from frozen §4.3 for an effect
    confined to the challenger model M4. Ceiling rather than flooring keeps the holdout at no less
    than 20% and guarantees it is never empty.
    """
    train_idx = np.asarray(train_idx, dtype=np.int64)
    if train_idx.size and np.any(np.diff(train_idx) <= 0):
        raise ValueError("train_idx must be sorted ascending and unique")
    if not 0.0 < fraction < 1.0:
        raise ValueError("fraction must be in (0, 1)")

    n_stop = max(1, math.ceil(fraction * train_idx.size))
    if n_stop >= train_idx.size:
        raise ValueError(
            f"training block of {train_idx.size} rows is too small for a {fraction:.0%} holdout"
        )
    return train_idx[:-n_stop], train_idx[-n_stop:]
