"""M3 — GARCH(1,1), normal QMLE, estimated with `arch` 8.0.0.

PREREGISTRATION §4 (M3) and BUILD_GUIDE Step 8 fix the model: daily close-to-close returns in
percent, a GARCH(1,1) conditional-variance recursion, and the sum of the 1- to N_t-step-ahead
conditional variance forecasts as the 30-day forecast.  The mean is the `arch` constant mean, the
package default.  The frozen spec does not name a mean model, so that choice is recorded in
`prereg/DEVIATIONS.md`, After freeze, 2026-11-14.

All returns here are percentages: 100 * (r_on + r_oc).  `arch` is built with `rescale=False`, so
the conditional variance is in percent squared.  A fold's total is
sum_{h=1..N_t} sigma2_{t+h} / PCT2_TO_DECIMAL, with PCT2_TO_DECIMAL = 10,000, which converts
percent squared to decimal variance.  The result is already a 30-day total on the same scale as
RV_30d, so it goes directly to `apply_floor`; there is no `to_total` multiplication.

Estimation cutoff.  `fit(last_obs=refit_date)` is exclusive: the return on `refit_date` and
everything after it is excluded from the fit, so the last estimation date is the session before
the refit.  Forecasting at t then uses returns through t, so a forecast made at t cannot see
r_{t+1}.

Information set.  HAR's newest training targets run through the session before the refit date —
the same data date as GARCH's newest return.  The real difference is the number of training rows:
HAR has about 21 fewer, because its most recent sessions do not yet have a complete 30-day target
and so cannot enter the fit (§5.2).  This is an information-set difference, not a leak: both
models are fitted only on data available at the refit, and the GARCH forecast at t uses no return
after t.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from arch import arch_model
from arch.utility.exceptions import ConvergenceWarning

PCT2_TO_DECIMAL = 10_000
AT_BOUND_TOLERANCE = 1e-6


def pct_returns(r_on: pd.Series, r_oc: pd.Series) -> pd.Series:
    """100 * (r_on + r_oc), dropping only the leading NaN.

    The first row is the only tolerated missing value.  A NaN or infinity anywhere after it
    raises: an interior gap would silently shorten the estimation sample, and an infinity would
    poison the GARCH recursion.
    """
    combined = 100.0 * (r_on + r_oc)
    values = combined.to_numpy(dtype=float)
    if values.size == 0:
        return combined

    start = 1 if np.isnan(values[0]) else 0
    if not np.isfinite(values[start:]).all():
        raise ValueError("returns contain a non-finite value after the leading NaN")
    return combined.iloc[start:]


def build_model(returns: pd.Series):
    """Build the GARCH(1,1) model once on the full return series.

    `rescale=False` is essential: `arch` would otherwise rescale the series and we would have to
    undo that scale before interpreting the variances in percent squared.
    """
    return arch_model(
        returns,
        mean="Constant",
        vol="GARCH",
        p=1,
        q=1,
        dist="normal",
        rescale=False,
    )


def fit_fold(model, refit_date, *, options=None) -> tuple[object, dict]:
    """Fit one fold's GARCH model with an exclusive cutoff, returning the result and a record.

    `warnings.simplefilter("error", ConvergenceWarning)` does not work with `arch` 8.0.0: inside
    `fit()` arch resets its own filter to "always" for that warning, overriding the caller's.
    Recording and raising explicitly is therefore the only reliable check.

    `options` is keyword-only and is for the synthetic tests; `run_eval` never passes it.
    """
    refit_date = pd.Timestamp(refit_date)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = model.fit(last_obs=refit_date, disp="off", options=options)

    convergence_warnings = [
        warning for warning in caught if issubclass(warning.category, ConvergenceWarning)
    ]
    if res.convergence_flag != 0 or convergence_warnings:
        detail = "; ".join(str(warning.message) for warning in convergence_warnings)
        raise RuntimeError(
            f"GARCH fit at {refit_date.date()} did not converge "
            f"(flag {res.convergence_flag}); {detail or 'no ConvergenceWarning'}"
        )

    other_warnings = "; ".join(
        sorted(
            {
                warning.category.__name__
                for warning in caught
                if not issubclass(warning.category, ConvergenceWarning)
            }
        )
    )

    first_estimation_date = model.y.index[res.fit_start]
    last_estimation_date = model.y.index[res.fit_stop - 1]
    assert last_estimation_date < refit_date, (
        f"last estimation date {last_estimation_date.date()} is not before "
        f"refit date {refit_date.date()}"
    )

    mu = float(res.params["mu"])
    omega = float(res.params["omega"])
    alpha = float(res.params["alpha[1]"])
    beta = float(res.params["beta[1]"])
    persistence = alpha + beta

    record = {
        "refit_date": refit_date,
        "nobs": int(res.nobs),
        "first_estimation_date": first_estimation_date,
        "last_estimation_date": last_estimation_date,
        "mu": mu,
        "omega": omega,
        "alpha": alpha,
        "beta": beta,
        "persistence": persistence,
        "at_bound": bool(persistence >= 1.0 - AT_BOUND_TOLERANCE),
        "loglik": float(res.loglikelihood),
        "convergence_flag": int(res.convergence_flag),
        "other_warnings": other_warnings,
    }
    return res, record


def sum_steps(variance_pct, n_sessions) -> np.ndarray:
    """Sum the first n_i forecast columns of each row, then convert percent squared to decimal.

    Kept pure so the unit conversion and the ragged sum can be checked without fitting anything.
    """
    variance = np.asarray(variance_pct, dtype=float)
    if variance.ndim != 2:
        raise ValueError(f"variance_pct must be two-dimensional, got shape {variance.shape}")

    counts = np.asarray(n_sessions)
    if counts.ndim != 1:
        raise ValueError(f"n_sessions must be one-dimensional, got shape {counts.shape}")
    if counts.size != variance.shape[0]:
        raise ValueError(
            f"n_sessions has length {counts.size}, variance_pct has {variance.shape[0]} rows"
        )
    if not np.issubdtype(counts.dtype, np.number):
        raise ValueError("n_sessions must be numeric")
    if not np.isfinite(counts).all() or not np.equal(counts, np.floor(counts)).all():
        raise ValueError("n_sessions must be whole numbers")

    counts = counts.astype(int)
    if (counts < 1).any():
        raise ValueError("every n_sessions must be at least 1")
    if (counts > variance.shape[1]).any():
        raise ValueError(
            f"n_sessions exceeds the {variance.shape[1]} forecast columns available"
        )

    columns = np.arange(variance.shape[1])
    selected = columns[None, :] < counts[:, None]
    return (variance * selected).sum(axis=1) / PCT2_TO_DECIMAL


def forecast_fold(res, origins, n_sessions) -> np.ndarray:
    """Forecast each origin through its own N_t-step horizon and return decimal 30-day totals."""
    origin_index = pd.Index(origins)
    counts = np.asarray(n_sessions)
    if origin_index.size == 0:
        raise ValueError("at least one forecast origin is required")
    if origin_index.size != counts.size:
        raise ValueError(
            f"{origin_index.size} origins but {counts.size} n_sessions values"
        )
    if not np.issubdtype(counts.dtype, np.number):
        raise ValueError("n_sessions must be numeric")
    if not np.isfinite(counts).all() or not np.equal(counts, np.floor(counts)).all():
        raise ValueError("n_sessions must be whole numbers")
    if (counts < 1).any():
        raise ValueError("every n_sessions must be at least 1")

    horizon = int(np.max(counts))
    variance = res.forecast(
        horizon=horizon,
        start=origin_index[0],
        reindex=False,
        method="analytic",
    ).variance

    missing = origin_index.difference(variance.index)
    if len(missing):
        raise ValueError(f"forecast origin(s) missing from the data: {list(missing)}")

    totals = sum_steps(variance.loc[origin_index].to_numpy(dtype=float), counts)
    if not np.isfinite(totals).all():
        raise ValueError("GARCH forecast contains a non-finite total")
    if np.any(totals <= 0):
        raise ValueError("GARCH forecast contains a non-positive total")
    return totals
