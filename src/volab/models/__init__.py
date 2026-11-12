"""Model layer: M0 persistence, HAR/HAR-X, the R1 implied reference and the §3.4 positivity rule.

Every function here is pure: no file I/O and no hard-coded pre-registration constants. F_min and
the VIX terciles are produced by `scripts/build_panel.py` into `data/panel/panel_manifest.json`
and read from there by the caller, so there is exactly one source for them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FloorResult:
    """A floored forecast series, with the raw values kept for reporting."""

    total: np.ndarray     # after max(F, F_min)
    raw: np.ndarray       # before flooring
    floored: np.ndarray   # boolean, one per forecast

    @property
    def n_floored(self) -> int:
        return int(self.floored.sum())


def apply_floor(total, f_min: float) -> FloorResult:
    """§3.4: every forecast passed to QLIKE is max(F, F_min).

    Order matters. F_min is a percentile of the 30-day *total*, so the per-session forecast is
    multiplied by N_t first and the floor is applied to that product — flooring per-session would
    compare a per-day quantity against a 30-day percentile.

    The raw value and a boolean flag are both returned so the floored count §3.4 asks for comes
    from the flag rather than from a second comparison.
    """
    raw = np.asarray(total, dtype=float)
    if not np.isfinite(raw).all():
        # A NaN forecast would survive max() as NaN, be counted as not-floored, and reach QLIKE.
        raise ValueError("forecast contains a non-finite value; refusing to floor it into QLIKE")
    if not np.isfinite(f_min) or f_min <= 0:
        raise ValueError("f_min must be a positive finite number")
    return FloorResult(total=np.maximum(raw, f_min), raw=raw, floored=raw < f_min)


def to_total(per_session, n_sessions) -> np.ndarray:
    """§3.2: the 30-day total is the per-session forecast times N_t; the floor applies after.

    Kept beside `apply_floor` because the two always run as a pair and the order is the thing that
    goes wrong: F_min is a percentile of the total, so flooring a per-session value would compare
    a one-day quantity against a 30-day threshold.
    """
    per_session = np.asarray(per_session, dtype=float)
    n_sessions = np.asarray(n_sessions, dtype=float)

    if per_session.shape != n_sessions.shape:
        raise ValueError(
            f"per_session has shape {per_session.shape}, n_sessions has {n_sessions.shape}"
        )
    if not np.all(n_sessions > 0):
        raise ValueError("n_sessions must be positive for every forecast date")
    return per_session * n_sessions
