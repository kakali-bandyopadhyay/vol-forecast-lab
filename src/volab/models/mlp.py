"""M4 — the MLP challenger — PREREGISTRATION §4.3 and DEVIATIONS I–R.

The model is a small fully connected network on the log of M2's four inputs:

    log RV_d, log RV_w, log RV_m, log(IV_t / N_t)

It predicts the log per-session 30-day target.  The scaler is fitted on the fit block only; the
last 20% of each training block is the early-stopping block.  Three seeds are trained per fold and
their back-transformed forecasts are averaged on the level scale:

    F_seed = exp(y_hat + s² / 2)

The architecture grid, epoch cap, patience and learning rate are frozen here.  Nothing in this
module widens them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from volab.splits import early_stop_split

GRID = tuple(
    {"hidden": hidden, "layers": layers, "weight_decay": weight_decay}
    for hidden in (8, 16, 32)
    for layers in (1, 2)
    for weight_decay in (0.0, 1e-4, 1e-3)
)
SEEDS = (0, 1, 2)
MAX_EPOCHS = 3000
PATIENCE = 20
LR = 1e-3

torch.use_deterministic_algorithms(True)
torch.set_num_threads(1)


def config_id(cfg: dict) -> str:
    """Stable short label for one grid configuration."""
    return f"h{cfg['hidden']}_l{cfg['layers']}_wd{cfg['weight_decay']:g}"


def log_inputs(features, per_session_iv) -> np.ndarray:
    """Log of M2's four inputs, shape (n, 4).

    The HAR warm-up rows are NaN and stay NaN; callers drop them with the fold's usable-row mask.
    A zero or negative value raises: `log` would otherwise produce -inf or NaN and the scaler
    would carry it into every fit.
    """
    if isinstance(features, pd.DataFrame):
        required = ("rv_d", "rv_w", "rv_m")
        missing = [column for column in required if column not in features.columns]
        if missing:
            raise ValueError(f"features is missing column(s): {missing}")
        base = features[list(required)].to_numpy(dtype=float)
    else:
        base = np.asarray(features, dtype=float)
        if base.ndim != 2 or base.shape[1] != 3:
            raise ValueError("features must be a frame or array with three columns")

    iv = np.asarray(per_session_iv, dtype=float)
    if iv.ndim != 1 or iv.size != base.shape[0]:
        raise ValueError("per_session_iv must be one-dimensional and match features")

    values = np.column_stack([base, iv])
    if np.isinf(values).any():
        raise ValueError("MLP inputs contain an infinity")
    finite = np.isfinite(values)
    if (values[finite] <= 0.0).any():
        raise ValueError("MLP inputs must be strictly positive where defined")
    return np.log(values)


class Scaler:
    """Column mean/std scaler fitted on the fit block only.

    `fit(X, rows, cutoff_pos)` refuses any row at or after the stopping cutoff.  The rows must be
    the contiguous fit block ending immediately before `cutoff_pos`, which is what
    `early_stop_split` produces.
    """

    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None

    def fit(self, X, rows, cutoff_pos: int) -> "Scaler":
        values = np.asarray(X, dtype=float)
        if values.ndim != 2:
            raise ValueError("X must be two-dimensional")

        positions = _positions(rows, values.shape[0])
        if positions.size == 0:
            raise ValueError("rows is empty")
        if np.any(np.diff(positions) <= 0):
            raise ValueError("rows must be sorted ascending and unique")

        cutoff = int(cutoff_pos)
        if cutoff <= 0 or cutoff > values.shape[0]:
            raise ValueError("cutoff_pos is outside X")
        if np.any(positions >= cutoff):
            raise ValueError("scaler rows include the stopping block or a forecast row")
        if not np.array_equal(positions, np.arange(positions[0], cutoff)):
            raise ValueError("rows are not the fit block immediately before cutoff_pos")

        block = values[positions]
        self.mean_ = block.mean(axis=0)
        self.std_ = block.std(axis=0, ddof=0)
        # A zero-variance column would divide by zero.  Keep the transform finite; the real
        # log inputs are never constant.
        self.std_ = np.where(self.std_ == 0.0, 1.0, self.std_)
        return self

    def transform(self, X) -> np.ndarray:
        if self.mean_ is None or self.std_ is None:
            raise RuntimeError("Scaler is not fitted")
        values = np.asarray(X, dtype=float)
        return (values - self.mean_) / self.std_


def _positions(index, n_rows: int) -> np.ndarray:
    array = np.asarray(index)
    if array.dtype == bool:
        if array.shape != (n_rows,):
            raise ValueError(f"boolean index has shape {array.shape}, expected ({n_rows},)")
        return np.flatnonzero(array)
    positions = array.astype(int)
    if positions.ndim != 1:
        raise ValueError("row index must be one-dimensional")
    if positions.size and ((positions < 0) | (positions >= n_rows)).any():
        raise ValueError("row index is outside the array")
    return positions


def _tensor(values) -> torch.Tensor:
    return torch.as_tensor(np.asarray(values, dtype=np.float64), dtype=torch.float64)


def build(hidden: int, layers: int) -> nn.Sequential:
    """ReLU hidden layers and one linear output unit, float64."""
    if layers not in (1, 2):
        raise ValueError("layers must be 1 or 2")
    modules: list[nn.Module] = []
    in_features = 4
    for _ in range(layers):
        modules.extend([nn.Linear(in_features, hidden), nn.ReLU()])
        in_features = hidden
    modules.append(nn.Linear(in_features, 1))
    return nn.Sequential(*modules).double()


def train(X_fit, y_fit, X_stop, y_stop, cfg: dict, seed: int):
    """Full-batch Adam training with early stopping on the stopping-block MSE.

    The seed is set immediately before the model is built.  The best-epoch weights are restored
    before returning; `hit_cap` is True when the 3,000-epoch cap was reached.
    """
    X_fit = np.asarray(X_fit, dtype=float)
    y_fit = np.asarray(y_fit, dtype=float)
    X_stop = np.asarray(X_stop, dtype=float)
    y_stop = np.asarray(y_stop, dtype=float)
    if X_fit.shape[0] != y_fit.size or X_stop.shape[0] != y_stop.size:
        raise ValueError("training arrays are misaligned")
    if not (
        np.isfinite(X_fit).all()
        and np.isfinite(y_fit).all()
        and np.isfinite(X_stop).all()
        and np.isfinite(y_stop).all()
    ):
        raise ValueError("training arrays must be finite")

    torch.manual_seed(int(seed))
    model = build(int(cfg["hidden"]), int(cfg["layers"]))
    optimizer = torch.optim.Adam(
        model.parameters(), lr=LR, weight_decay=float(cfg["weight_decay"])
    )
    loss_fn = nn.MSELoss()

    fit_x = _tensor(X_fit)
    fit_y = _tensor(y_fit).reshape(-1, 1)
    stop_x = _tensor(X_stop)
    stop_y = _tensor(y_stop).reshape(-1, 1)

    best_loss = float("inf")
    best_epoch = 0
    best_state = None
    wait = 0
    hit_cap = True
    history: list[float] = []

    for epoch in range(1, MAX_EPOCHS + 1):
        model.train()
        optimizer.zero_grad()
        loss = loss_fn(model(fit_x), fit_y)
        loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            stop_loss = float(loss_fn(model(stop_x), stop_y).item())
        history.append(stop_loss)

        if stop_loss < best_loss:
            best_loss = stop_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            wait = 0
        else:
            wait += 1
            if wait >= PATIENCE:
                hit_cap = False
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    model.stop_mse_history_ = history
    model.best_stop_mse_ = best_loss
    model.best_epoch_ = best_epoch
    model.hit_cap_ = hit_cap
    return model, best_epoch, hit_cap


@dataclass
class FoldFit:
    """One fold's three trained models and their fitted scaler/back-transform terms."""

    scaler: Scaler
    models: tuple[nn.Sequential, ...]
    s2: np.ndarray
    epochs: np.ndarray
    cap_flags: np.ndarray
    usable: np.ndarray


def usable_rows(X, y_log, train_idx) -> np.ndarray:
    """The fold's training rows whose four inputs and log target are all finite."""
    values = np.asarray(X, dtype=float)
    target = np.asarray(y_log, dtype=float)
    positions = _positions(train_idx, values.shape[0])
    selected = np.zeros(values.shape[0], dtype=bool)
    selected[positions] = True
    selected &= np.isfinite(values).all(axis=1) & np.isfinite(target)
    return np.flatnonzero(selected)


def fit_fold(X, y_log, train_idx, cfg: dict) -> FoldFit:
    """Fit the scaler and all three seeds on one fold's usable training rows."""
    values = np.asarray(X, dtype=float)
    target = np.asarray(y_log, dtype=float)
    usable = usable_rows(values, target, train_idx)
    if usable.size < 2:
        raise ValueError("not enough usable training rows")

    fit_rows, stop_rows = early_stop_split(usable)
    scaler = Scaler().fit(values, fit_rows, cutoff_pos=int(stop_rows[0]))
    scaled = scaler.transform(values)

    models = []
    s2 = []
    epochs = []
    cap_flags = []
    for seed in SEEDS:
        model, best_epoch, hit_cap = train(
            scaled[fit_rows],
            target[fit_rows],
            scaled[stop_rows],
            target[stop_rows],
            cfg,
            seed,
        )
        with torch.no_grad():
            fitted = model(_tensor(scaled[usable])).numpy().reshape(-1)
        residual = target[usable] - fitted
        models.append(model)
        s2.append(float(np.mean(residual ** 2)))
        epochs.append(int(best_epoch))
        cap_flags.append(bool(hit_cap))

    return FoldFit(
        scaler=scaler,
        models=tuple(models),
        s2=np.asarray(s2, dtype=float),
        epochs=np.asarray(epochs, dtype=int),
        cap_flags=np.asarray(cap_flags, dtype=bool),
        usable=usable,
    )


def back_transform(log_forecast, s2: float) -> np.ndarray:
    """exp(y_hat + s²/2), the log-normal mean on the level scale."""
    return np.exp(np.asarray(log_forecast, dtype=float) + 0.5 * float(s2))


def forecast_fold(fold_fit: FoldFit, X, forecast_idx) -> tuple[np.ndarray, np.ndarray]:
    """Return `(seed_mean, per_seed)` forecasts on the level scale.

    `per_seed` has shape (3, n_forecasts) and keeps the three seeds separate for the manifest.
    The mean is taken over levels, not over log forecasts.
    """
    values = np.asarray(X, dtype=float)
    rows = _positions(forecast_idx, values.shape[0])
    if rows.size == 0:
        raise ValueError("forecast_idx is empty")
    selected = values[rows]
    if not np.isfinite(selected).all():
        raise ValueError("forecast rows contain a non-finite input")
    scaled = fold_fit.scaler.transform(selected)

    per_seed = []
    for model, s2 in zip(fold_fit.models, fold_fit.s2):
        model.eval()
        with torch.no_grad():
            logged = model(_tensor(scaled)).numpy().reshape(-1)
        per_seed.append(back_transform(logged, s2))
    levels = np.vstack(per_seed)
    return levels.mean(axis=0), levels


def shuffled_targets(y_log, rows, year: int) -> np.ndarray:
    """Permute the log targets within `rows` using a generator seeded by the fold year."""
    target = np.asarray(y_log, dtype=float)
    positions = _positions(rows, target.size)
    shuffled = target.copy()
    if positions.size:
        generator = np.random.default_rng(int(year))
        shuffled[positions] = shuffled[positions][generator.permutation(positions.size)]
    return shuffled
