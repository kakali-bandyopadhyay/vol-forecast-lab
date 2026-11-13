"""Forecast losses — PREREGISTRATION §5.3.

Both functions return per-date arrays rather than a mean: the tests compare loss series, and the
Diebold–Mariano and Clark–West statistics need the series, not its average.
"""

from __future__ import annotations

import numpy as np


def _aligned_pair(rv, f, name: str) -> tuple[np.ndarray, np.ndarray]:
    realised = np.asarray(rv, dtype=float)
    forecast = np.asarray(f, dtype=float)
    if realised.shape != forecast.shape:
        raise ValueError(f"{name}: shape mismatch {realised.shape} vs {forecast.shape}")
    return realised, forecast


def qlike(rv, f) -> np.ndarray:
    """QLIKE(RV, F) = RV/F − ln(RV/F) − 1, per date. The primary ranking loss of §5.3.

    Raises rather than returning inf or NaN. A non-positive forecast makes the ratio explode and a
    non-positive target has no log; either would propagate silently into the ranking, and §3.4's
    floor exists precisely so that a forecast reaching here is positive.
    """
    realised, forecast = _aligned_pair(rv, f, "qlike")

    if not np.isfinite(forecast).all() or np.any(forecast <= 0.0):
        raise ValueError("qlike: forecast must be finite and strictly positive")
    if not np.isfinite(realised).all() or np.any(realised <= 0.0):
        raise ValueError("qlike: realised variance must be finite and strictly positive")

    ratio = realised / forecast
    return ratio - np.log(ratio) - 1.0


def squared_error(rv, f) -> np.ndarray:
    """(RV − F)², per date — secondary loss, reported on total variance (§5.3)."""
    realised, forecast = _aligned_pair(rv, f, "squared_error")
    return (realised - forecast) ** 2
