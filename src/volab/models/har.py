"""HAR (M1) and HAR-X (M2) — §4.2, ordinary least squares by `numpy.linalg.lstsq`.

Design, at forecast date t, from realised variance known at t:

    RV_d = RV_t                     the day just closed
    RV_w = mean of RV over the 5 sessions ending at t
    RV_m = mean of RV over the 22 sessions ending at t
    M2 adds IV_t / N_t              per-session implied variance

The 22 here is not the 21 of M0: HAR's monthly term is a 22-session mean, M0's persistence window is
21 sessions. They are separate constants and must not be merged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RV_W_WINDOW = 5
RV_M_WINDOW = 22
FEATURE_ORDER = ("rv_d", "rv_w", "rv_m")


def har_features(rv: pd.Series) -> pd.DataFrame:
    """The three HAR regressors, each ending at t.

    The first 21 rows are incomplete because RV_m needs 22 sessions; they are left as NaN rather
    than back-filled, and `complete_cases` is how the caller drops them.
    """
    frame = pd.DataFrame(index=rv.index)
    frame["rv_d"] = rv
    frame["rv_w"] = rv.rolling(RV_W_WINDOW, min_periods=RV_W_WINDOW).mean()
    frame["rv_m"] = rv.rolling(RV_M_WINDOW, min_periods=RV_M_WINDOW).mean()
    return frame


def complete_cases(features: pd.DataFrame) -> np.ndarray:
    """Boolean mask: rows whose every feature is present."""
    return features[list(FEATURE_ORDER)].notna().all(axis=1).to_numpy(dtype=bool)


def design(features: pd.DataFrame, per_session_iv=None) -> np.ndarray:
    """Model matrix *without* the intercept; M2 appends per-session implied variance."""
    matrix = features[list(FEATURE_ORDER)].to_numpy(dtype=float)
    if per_session_iv is not None:
        matrix = np.column_stack([matrix, np.asarray(per_session_iv, dtype=float)])
    return matrix


def _as_2d(values) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array.reshape(-1, 1) if array.ndim == 1 else array


def _with_intercept(matrix) -> np.ndarray:
    matrix = _as_2d(matrix)
    return np.column_stack([np.ones(matrix.shape[0], dtype=float), matrix])


def fit(matrix, target) -> np.ndarray:
    """OLS with an intercept prepended, solved by `lstsq` (no normal equations, no inversion).

    The rank is checked rather than discarded. On collinear columns `lstsq` returns a minimum-norm
    solution that looks like a perfectly good fit; without this check a duplicated regressor would
    produce coefficients nobody could interpret and nothing would raise.
    """
    design_matrix = _with_intercept(matrix)
    solution, _residuals, rank, _singular = np.linalg.lstsq(
        design_matrix, np.asarray(target, dtype=float), rcond=None
    )
    if rank < design_matrix.shape[1]:
        raise ValueError(
            f"design matrix is rank deficient: rank {rank} < {design_matrix.shape[1]} columns"
        )
    return solution


def predict(beta, matrix) -> np.ndarray:
    """One predictor shared by M1 and M2.

    Accumulated term by term in a fixed order rather than as a single BLAS dot product, so that a
    zero coefficient is bit-exact: HAR-X with β_IV = 0 must reproduce HAR under `==`, and a 4- vs
    5-element dot product may reassociate its summation and differ in the last bit.
    """
    beta = np.asarray(beta, dtype=float)
    matrix = _as_2d(matrix)
    if beta.size != matrix.shape[1] + 1:
        raise ValueError(
            f"beta has {beta.size} elements, expected {matrix.shape[1] + 1} "
            f"(intercept + {matrix.shape[1]} columns)"
        )

    fitted = np.full(matrix.shape[0], beta[0], dtype=float)
    for column in range(matrix.shape[1]):
        fitted = fitted + matrix[:, column] * beta[column + 1]
    return fitted


def fit_fold(features: pd.DataFrame, target, train_idx, per_session_iv=None) -> np.ndarray:
    """Fit on one fold's training rows, dropping rows with an incomplete feature vector.

    The warm-up rows are dropped here rather than silently absorbed: a training row missing RV_m
    cannot inform a coefficient, and dropping it explicitly keeps the row count auditable.
    """
    matrix = design(features, per_session_iv)
    target = np.asarray(target, dtype=float)
    selected = np.zeros(len(features), dtype=bool)
    selected[np.asarray(train_idx, dtype=int)] = True
    # Usability comes from the whole design matrix, not from the three HAR columns: a NaN in the
    # M2 implied column would otherwise pass `complete_cases` and poison the fit.
    usable = selected & np.isfinite(matrix).all(axis=1) & np.isfinite(target)
    rows = np.flatnonzero(usable)

    if rows.size < matrix.shape[1] + 1:
        raise ValueError(
            f"only {rows.size} usable training rows for {matrix.shape[1] + 1} parameters"
        )
    return fit(matrix[rows], target[rows])


def forecast_fold(beta, features: pd.DataFrame, forecast_idx, per_session_iv=None) -> np.ndarray:
    """Predict one fold's forecast rows, refusing to forecast from an incomplete row."""
    matrix = design(features, per_session_iv)
    rows = np.asarray(forecast_idx, dtype=int)
    if not np.isfinite(matrix[rows]).all():
        raise ValueError("forecast row has a NaN feature; check the warm-up is excluded")
    return predict(beta, matrix[rows])
