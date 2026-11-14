"""Statistical tests for §5.5.

The Newey–West mean test here is deliberately the same estimator statsmodels uses for
`OLS(d ~ 1, cov_type="HAC", maxlags=L, use_t=False)`: Bartlett weights 1 − j/(L+1),
autocovariances divided by T rather than T − j, no small-sample correction, normal p-values. Any
of those four choices changed would break the 1e-6 cross-check in the tests.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

HAC_MIN_LAG = 21


def hac_lag(n_obs: int, minimum: int = HAC_MIN_LAG) -> int:
    """§5.5: max(21, ⌊4·(T/100)^(2/9)⌋). At T = 1,703 the formula term is 7, so the lag is 21."""
    if n_obs < 1:
        raise ValueError("n_obs must be positive")
    formula = int(np.floor(4.0 * (n_obs / 100.0) ** (2.0 / 9.0)))
    return max(minimum, formula)


def _autocovariance(centred: np.ndarray, lag: int) -> float:
    """γ_j = T⁻¹ Σ_{t>j} x_t x_{t−j} — divided by T, matching statsmodels."""
    n = centred.size
    if lag == 0:
        return float(centred @ centred) / n
    return float(centred[lag:] @ centred[:-lag]) / n


def nw_mean_test(d, lag: int) -> tuple[float, float]:
    """Test E[d] = 0 with a Newey–West HAC variance and normal (two-sided) p-values.

    Degenerate case, handled explicitly: when every d is exactly zero — identical forecasts — the
    HAC variance is zero and the ratio is 0/0. That returns statistic 0 with p = 1 rather than NaN.
    """
    d = np.asarray(d, dtype=float)
    n = d.size
    if n < 2:
        raise ValueError("need at least two observations")
    if not np.isfinite(d).all():
        raise ValueError("d contains a non-finite value")

    if np.all(d == 0.0):
        return 0.0, 1.0

    mean = float(d.mean())
    centred = d - mean
    effective_lag = int(min(lag, n - 1))

    long_run = _autocovariance(centred, 0)
    for j in range(1, effective_lag + 1):
        weight = 1.0 - j / (effective_lag + 1.0)
        long_run += 2.0 * weight * _autocovariance(centred, j)

    variance_of_mean = long_run / n
    if not variance_of_mean > 0.0:
        raise ValueError("HAC variance of the mean is non-positive; d is degenerate")
    # A constant series leaves only rounding noise behind, which is positive and produces a
    # statistic around 1e16 rather than raising. Judging the variance against the mean squared
    # catches that: a relative standard error below 1e-6 is not a measurement.
    if mean != 0.0 and variance_of_mean < (mean * mean) * 1e-12:
        raise ValueError(
            "HAC variance of the mean is negligible relative to the mean; d is constant to "
            "rounding"
        )

    statistic = mean / np.sqrt(variance_of_mean)
    pvalue = 2.0 * float(stats.norm.sf(abs(statistic)))
    return float(statistic), pvalue


def ols_hac(y, X, lag: int) -> dict[str, np.ndarray]:
    """OLS with a Newey–West HAC sandwich, for any number of regressors.

    This is the k-column form of the Mincer–Zarnowitz estimator below, with the same four
    conventions: Bartlett weights 1 − j/(L+1), autocovariances divided by T, no small-sample
    correction, and normal p-values from the t ratio. `X` must include its own constant column;
    the caller owns the design, as in the event study.

    Returns `beta`, `se` and `t` as arrays aligned with `X.columns`. A rank-deficient design
    raises rather than returning a minimum-norm solution that looks like a fit.
    """
    response = np.asarray(y, dtype=float)
    design = np.asarray(X, dtype=float)
    if response.ndim != 1:
        raise ValueError(f"y must be one-dimensional, got shape {response.shape}")
    if design.ndim != 2:
        raise ValueError(f"X must be two-dimensional, got shape {design.shape}")
    if design.shape[0] != response.size:
        raise ValueError(
            f"X has {design.shape[0]} rows but y has {response.size} observations"
        )
    if response.size < 2:
        raise ValueError("need at least two observations")
    if not np.isfinite(response).all() or not np.isfinite(design).all():
        raise ValueError("y and X must be finite")

    beta, _residuals, rank, _singular = np.linalg.lstsq(design, response, rcond=None)
    if rank < design.shape[1]:
        raise ValueError(
            f"design matrix is rank deficient: rank {rank} < {design.shape[1]} columns"
        )
    residuals = response - design @ beta

    gram_inverse = np.linalg.inv(design.T @ design)
    effective_lag = int(min(lag, response.size - 1))
    if effective_lag < 0:
        raise ValueError("lag must be non-negative")

    scaled = design * residuals[:, None]
    meat = np.zeros((design.shape[1], design.shape[1]))
    for j in range(effective_lag + 1):
        weight = 1.0 - j / (effective_lag + 1.0)
        if j == 0:
            meat += weight * (scaled.T @ scaled)
        else:
            meat += weight * (scaled[j:].T @ scaled[:-j] + scaled[:-j].T @ scaled[j:])

    covariance = gram_inverse @ meat @ gram_inverse
    se = np.sqrt(np.diag(covariance))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = beta / se
    return {"beta": beta, "se": se, "t": t}


def clark_west(rv, f1, f2, lag: int) -> tuple[float, float]:
    """Clark–West (2007) for nested models. Model 2 must nest model 1.

    f_t = e₁² − [e₂² − (F₁ − F₂)²], with e = RV − F. A positive mean favours model 2, so the
    one-sided p-value is 1 − Φ(t), as §5.5 specifies.

    Identical forecasts give t = 0 and p = 0.5. That is the correct one-sided reading of a
    zero statistic, not a fallback value: Diebold–Mariano on the same input returns p = 1 because
    its test is two-sided. The asymmetry is deliberate.
    """
    realised = np.asarray(rv, dtype=float)
    first = np.asarray(f1, dtype=float)
    second = np.asarray(f2, dtype=float)
    if not (realised.shape == first.shape == second.shape):
        raise ValueError("clark_west: rv, f1 and f2 must have the same shape")

    error_first = realised - first
    error_second = realised - second
    adjustment = error_first ** 2 - (error_second ** 2 - (first - second) ** 2)

    statistic, _ = nw_mean_test(adjustment, lag)
    return statistic, float(stats.norm.sf(statistic))


def diebold_mariano(loss_a, loss_b, lag: int) -> tuple[float, float]:
    """Two-sided Diebold–Mariano on d = loss_a − loss_b.

    Sign convention: a positive statistic means loss_a exceeds loss_b, so model B has the lower
    loss and is the better forecaster. No Harvey–Leybourne–Newbold correction — §5.5 specifies none
    — so a small sample gives a mildly oversized test and that is accepted.

    Identical forecasts give t = 0 and p = 1 (two-sided); Clark–West returns p = 0.5 for the same
    input because its test is one-sided.
    """
    first = np.asarray(loss_a, dtype=float)
    second = np.asarray(loss_b, dtype=float)
    if first.shape != second.shape:
        raise ValueError("diebold_mariano: the two loss series must have the same shape")
    return nw_mean_test(first - second, lag)


def mincer_zarnowitz(rv, f, lag: int) -> dict[str, float | int]:
    """Mincer–Zarnowitz regression RV = a + b·F + e, HAC sandwich, Wald χ²(2) for (a, b) = (0, 1).

    A fitted line indistinguishable from the 45° through the origin is the calibration criterion;
    the joint restriction is tested rather than the two coefficients separately, because a
    systematic bias often shows up only jointly.
    """
    realised = np.asarray(rv, dtype=float)
    forecast = np.asarray(f, dtype=float)
    if realised.shape != forecast.shape:
        raise ValueError("mincer_zarnowitz: rv and f must have the same shape")
    if not np.isfinite(realised).all() or not np.isfinite(forecast).all():
        raise ValueError("mincer_zarnowitz: rv and f must be finite")

    design = np.column_stack([np.ones(realised.size), forecast])
    beta, *_ = np.linalg.lstsq(design, realised, rcond=None)
    residuals = realised - design @ beta

    gram = design.T @ design
    if np.linalg.matrix_rank(gram) < 2:
        raise ValueError("mincer_zarnowitz: forecast is constant; the slope is not identified")
    gram_inverse = np.linalg.inv(gram)

    effective_lag = int(min(lag, realised.size - 1))
    scaled = design * residuals[:, None]
    meat = np.zeros((2, 2))
    for j in range(0, effective_lag + 1):
        weight = 1.0 - j / (effective_lag + 1.0)
        if j == 0:
            meat += weight * (scaled.T @ scaled)
        else:
            meat += weight * (scaled[j:].T @ scaled[:-j] + scaled[:-j].T @ scaled[j:])

    covariance = gram_inverse @ meat @ gram_inverse
    restriction = beta - np.array([0.0, 1.0])
    wald = float(restriction @ np.linalg.solve(covariance, restriction))

    return {
        "a": float(beta[0]),
        "b": float(beta[1]),
        "wald": wald,
        "pvalue": float(stats.chi2.sf(wald, df=2)),
        "lag": effective_lag,
    }


def holm(pvalues) -> np.ndarray:
    """Holm step-down adjusted p-values, returned in the input order and capped at 1.

    Adjusted value for the k-th smallest is max over j ≤ k of (m − j + 1)·p_(j); the running max
    enforces monotonicity, which is what distinguishes Holm from a plain Bonferroni rescale.
    """
    raw = np.asarray(pvalues, dtype=float)
    if raw.size == 0:
        return raw
    # NaN survives every comparison below and would be silently ordered to the end of the sort.
    if not np.isfinite(raw).all():
        raise ValueError("p-values must be finite")
    if np.any((raw < 0.0) | (raw > 1.0)):
        raise ValueError("p-values must lie in [0, 1]")

    m = raw.size
    order = np.argsort(raw, kind="mergesort")
    adjusted = np.empty(m, dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (m - rank) * float(raw[index]))
        adjusted[index] = min(running, 1.0)
    return adjusted
