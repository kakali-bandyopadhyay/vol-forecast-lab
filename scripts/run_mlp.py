"""Step 10: M4 selection and test stages.

`--stage select` runs the 18-configuration grid over the four validation folds, writes
`results/mlp_selection.parquet` and the chosen-config manifest, and stops.  `--stage test` refuses
to run unless the selection file is committed, then trains the chosen configuration once per test
year, writes `results/mlp_forecasts.parquet`, and runs the shuffled-target check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from volab.data import REPO_ROOT, load_panel_metadata  # noqa: E402
from volab.losses import qlike, squared_error  # noqa: E402
from volab.models import apply_floor, har, mlp  # noqa: E402
from volab.models.baselines import implied_variance  # noqa: E402
from volab.splits import annual_walk_forward  # noqa: E402
from volab import tracking  # noqa: E402

PANEL = REPO_ROOT / "data" / "panel" / "panel.parquet"
RESULTS = REPO_ROOT / "results"

VALID_START = pd.Timestamp("2016-01-01")
VALID_END = pd.Timestamp("2019-11-29")
TEST_START = pd.Timestamp("2020-01-01")

EXPECTED_VALID_FOLDS = 4
EXPECTED_VALID_FORECASTS = 985
EXPECTED_TEST_FOLDS = 7
EXPECTED_TEST_FORECASTS = 1703


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as saved:
        for chunk in iter(lambda: saved.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def _load_panel():
    metadata = load_panel_metadata()
    digest = sha256_of(PANEL)
    if digest != metadata["sha256"]:
        raise SystemExit("panel hash does not match the manifest; rebuild before evaluating")

    panel = pd.read_parquet(PANEL)
    features = har.har_features(panel["rv"])
    per_session_iv = (
        implied_variance(panel["vix"]) / panel["n_sessions"]
    ).to_numpy(dtype=float)
    X = mlp.log_inputs(features, per_session_iv)
    y_log = np.log(panel["rv_30d_per_session"].to_numpy(dtype=float))
    return panel, metadata, digest, X, y_log


def _folds(panel, start, end=None):
    target_ok = panel["rv_30d"].notna()
    return annual_walk_forward(panel.index, target_ok, start, end)


def _param_count(cfg: dict) -> int:
    hidden = int(cfg["hidden"])
    if int(cfg["layers"]) == 1:
        return 6 * hidden + 1
    return hidden * hidden + 7 * hidden + 1


def _require_committed(relative: str) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise SystemExit(f"{relative} is not committed; commit it first")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(f"{relative} has uncommitted changes; commit it first")


def select_stage() -> int:
    for path in ("src/volab/models/mlp.py", "scripts/run_mlp.py"):
        _require_committed(path)
    panel, metadata, digest, X, y_log = _load_panel()
    folds = _folds(panel, VALID_START, VALID_END)
    n_forecasts = sum(len(fold.forecast_idx) for fold in folds)
    if len(folds) != EXPECTED_VALID_FOLDS or n_forecasts != EXPECTED_VALID_FORECASTS:
        raise SystemExit(
            f"validation grid gave {len(folds)} folds / {n_forecasts} forecasts, "
            f"expected {EXPECTED_VALID_FOLDS} / {EXPECTED_VALID_FORECASTS}"
        )

    f_min = float(metadata["training"]["f_min"])
    n_sessions = panel["n_sessions"].to_numpy(dtype=float)
    realised = panel["rv_30d"].to_numpy(dtype=float)
    configs = {mlp.config_id(cfg): cfg for cfg in mlp.GRID}

    losses: dict[tuple[str, int], list[np.ndarray]] = {
        (config_id, seed): [] for config_id in configs for seed in mlp.SEEDS
    }
    epochs: dict[tuple[str, int], list[int]] = {
        (config_id, seed): [] for config_id in configs for seed in mlp.SEEDS
    }
    caps: dict[tuple[str, int], list[bool]] = {
        (config_id, seed): [] for config_id in configs for seed in mlp.SEEDS
    }

    for config_id, cfg in configs.items():
        for fold in folds:
            fold_fit = mlp.fit_fold(X, y_log, fold.train_idx, cfg)
            mean, per_seed = mlp.forecast_fold(fold_fit, X, fold.forecast_idx)
            if not np.isfinite(mean).all() or np.any(mean <= 0):
                raise SystemExit(f"non-positive M4 forecast for {config_id}")
            for seed_index, seed in enumerate(mlp.SEEDS):
                total = per_seed[seed_index] * n_sessions[fold.forecast_idx]
                floored = apply_floor(total, f_min).total
                losses[(config_id, seed)].append(
                    qlike(realised[fold.forecast_idx], floored)
                )
                epochs[(config_id, seed)].append(int(fold_fit.epochs[seed_index]))
                caps[(config_id, seed)].append(bool(fold_fit.cap_flags[seed_index]))

    rows = []
    for config_id, cfg in configs.items():
        for seed in mlp.SEEDS:
            key = (config_id, seed)
            rows.append(
                {
                    "config": config_id,
                    "hidden": int(cfg["hidden"]),
                    "layers": int(cfg["layers"]),
                    "weight_decay": float(cfg["weight_decay"]),
                    "seed": int(seed),
                    "mean_qlike": float(np.concatenate(losses[key]).mean()),
                    "epochs": float(np.mean(epochs[key])),
                    "cap_hits": int(np.sum(caps[key])),
                    "n_folds": len(folds),
                }
            )
    selection = pd.DataFrame(rows)
    selection.to_parquet(RESULTS / "mlp_selection.parquet", index=False)

    scores = selection.groupby("config")["mean_qlike"].mean()
    best_score = float(scores.min())
    candidates = scores[scores <= best_score + 1e-12]
    chosen = min(
        candidates.index,
        key=lambda name: (
            _param_count(configs[name]),
            -float(configs[name]["weight_decay"]),
            int(configs[name]["hidden"]),
            int(configs[name]["layers"]),
        ),
    )
    chosen_config = configs[chosen]

    manifest = {
        "chosen_config": chosen_config,
        "config_id": chosen,
        "validation_mean_qlike": float(scores[chosen]),
        "code_commit": git_commit(),
        "torch_version": torch.__version__,
        "n_configs": len(configs),
        "n_seeds": len(mlp.SEEDS),
        "n_folds": len(folds),
        "n_forecasts": int(n_forecasts),
        "panel_sha256": digest,
    }
    _write_json(RESULTS / "mlp_select_manifest.json", manifest)
    print(f"selection: {len(selection)} rows, chosen {chosen} ({best_score:.6f})")
    print(f"written: {RESULTS / 'mlp_selection.parquet'}")

    with tracking.run(
        "step10_mlp_select",
        snapshot_id=str(metadata.get("snapshot_id", "unknown")),
        params={
            "stage": "select",
            "config": chosen,
            "n_configs": len(configs),
            "n_seeds": len(mlp.SEEDS),
            "n_folds": len(folds),
        },
    ) as tracker:
        tracker.log_metrics({
            "best_validation_mean_qlike": float(best_score),
            "selection_rows": float(len(selection)),
        })
        tracker.log_artifact(RESULTS / "mlp_selection.parquet")
        tracker.log_artifact(RESULTS / "mlp_select_manifest.json")
    return 0


def test_stage() -> int:
    for path in ("src/volab/models/mlp.py", "scripts/run_mlp.py"):
        _require_committed(path)
    _require_committed("results/mlp_selection.parquet")
    _require_committed("results/mlp_select_manifest.json")
    manifest_path = RESULTS / "mlp_select_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("missing results/mlp_select_manifest.json")
    selection_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg = selection_manifest["chosen_config"]

    panel, metadata, digest, X, y_log = _load_panel()
    folds = _folds(panel, TEST_START)
    n_forecasts = sum(len(fold.forecast_idx) for fold in folds)
    if len(folds) != EXPECTED_TEST_FOLDS or n_forecasts != EXPECTED_TEST_FORECASTS:
        raise SystemExit(
            f"test grid gave {len(folds)} folds / {n_forecasts} forecasts, "
            f"expected {EXPECTED_TEST_FOLDS} / {EXPECTED_TEST_FORECASTS}"
        )

    forecasts = pd.read_parquet(RESULTS / "forecasts.parquet")
    m1 = forecasts.loc[forecasts["model"] == "M1"].set_index("date").sort_index()
    m1_dates = pd.DatetimeIndex(m1.index)
    m1_realised = m1["realised_30d"].to_numpy(dtype=float)
    test_dates = pd.DatetimeIndex(
        panel.index[np.concatenate([fold.forecast_idx for fold in folds])]
    )
    if not test_dates.equals(m1_dates):
        raise SystemExit("annual test dates do not equal the M1 forecast dates")

    f_min = float(metadata["training"]["f_min"])
    n_sessions = panel["n_sessions"].to_numpy(dtype=float)
    realised = panel["rv_30d"].to_numpy(dtype=float)

    pieces = []
    shuffled_pieces = []
    cap_hits = 0
    best_epochs = []
    for fold in folds:
        fold_fit = mlp.fit_fold(X, y_log, fold.train_idx, cfg)
        mean, per_seed = mlp.forecast_fold(fold_fit, X, fold.forecast_idx)
        total = mean * n_sessions[fold.forecast_idx]
        result = apply_floor(total, f_min)
        seed_totals = per_seed * n_sessions[fold.forecast_idx][None, :]
        fold_year = int(panel.index[fold.forecast_idx[0]].year)
        cap_hits += int(np.sum(fold_fit.cap_flags))
        best_epochs.append([int(value) for value in fold_fit.epochs])

        pieces.append(
            pd.DataFrame(
                {
                    "date": panel.index[fold.forecast_idx],
                    "realised_30d": realised[fold.forecast_idx],
                    "raw_30d": result.raw,
                    "floored_30d": result.total,
                    "floored": result.floored,
                    "seed_0": seed_totals[0],
                    "seed_1": seed_totals[1],
                    "seed_2": seed_totals[2],
                    "fold_year": fold_year,
                    "epoch_0": fold_fit.epochs[0],
                    "epoch_1": fold_fit.epochs[1],
                    "epoch_2": fold_fit.epochs[2],
                    "cap_0": fold_fit.cap_flags[0],
                    "cap_1": fold_fit.cap_flags[1],
                    "cap_2": fold_fit.cap_flags[2],
                }
            )
        )

        usable = mlp.usable_rows(X, y_log, fold.train_idx)
        shuffled_y = mlp.shuffled_targets(y_log, usable, fold_year)
        shuffled_fit = mlp.fit_fold(X, shuffled_y, fold.train_idx, cfg)
        shuffled_mean, _ = mlp.forecast_fold(
            shuffled_fit, X, fold.forecast_idx
        )
        shuffled_total = shuffled_mean * n_sessions[fold.forecast_idx]
        shuffled_floor = apply_floor(shuffled_total, f_min).total
        shuffled_pieces.append(
            qlike(realised[fold.forecast_idx], shuffled_floor)
        )

    mlp_forecasts = pd.concat(pieces, ignore_index=True).sort_values("date")
    mlp_forecasts = mlp_forecasts.reset_index(drop=True)
    if len(mlp_forecasts) != EXPECTED_TEST_FORECASTS:
        raise SystemExit(f"expected {EXPECTED_TEST_FORECASTS} M4 forecasts")
    if not pd.DatetimeIndex(mlp_forecasts["date"]).equals(m1_dates):
        raise SystemExit("M4 dates do not equal the M1 dates")
    if not np.array_equal(
        mlp_forecasts["realised_30d"].to_numpy(dtype=float), m1_realised
    ):
        raise SystemExit("M4 realised_30d does not equal the M1 realised_30d")

    shuffled_qlike = float(np.concatenate(shuffled_pieces).mean())
    m0 = (
        forecasts.loc[forecasts["model"] == "M0"]
        .set_index("date")
        .reindex(m1_dates)["floored_30d"]
        .to_numpy(dtype=float)
    )
    m0_qlike = float(qlike(m1_realised, m0).mean())
    shuffled_pass = shuffled_qlike > m0_qlike

    manifest = {
        "chosen_config": cfg,
        "code_commit": git_commit(),
        "torch_version": torch.__version__,
        "n_folds": len(folds),
        "n_forecasts": int(len(mlp_forecasts)),
        "shuffled_mean_qlike": shuffled_qlike,
        "m0_mean_qlike": m0_qlike,
        "shuffled_pass": bool(shuffled_pass),
        "cap_hits": int(cap_hits),
        "best_epochs": best_epochs,
        "panel_sha256": digest,
    }

    if not shuffled_pass:
        _write_json(RESULTS / "mlp_test_manifest.json", manifest)
        raise SystemExit(
            f"shuffled-target check failed: shuffled QLIKE {shuffled_qlike:.6f} "
            f"is not above M0 {m0_qlike:.6f}"
        )

    mlp_forecasts.to_parquet(RESULTS / "mlp_forecasts.parquet", index=False)
    _write_json(RESULTS / "mlp_test_manifest.json", manifest)
    m4_qlike = float(qlike(m1_realised, mlp_forecasts["floored_30d"].to_numpy(dtype=float)).mean())
    m4_mse = float(squared_error(m1_realised, mlp_forecasts["raw_30d"].to_numpy(dtype=float)).mean())
    with tracking.run(
        "step10_mlp_test",
        snapshot_id=str(metadata.get("snapshot_id", "unknown")),
        params={
            "stage": "test",
            "config": selection_manifest.get("config_id", "unknown"),
            "n_folds": len(folds),
            "n_forecasts": len(mlp_forecasts),
        },
    ) as tracker:
        tracker.log_metrics({
            "m4_qlike": m4_qlike,
            "m4_mse": m4_mse,
            "shuffled_mean_qlike": float(shuffled_qlike),
            "m0_mean_qlike": float(m0_qlike),
            "shuffled_pass": float(shuffled_pass),
            "cap_hits": float(cap_hits),
        })
        tracker.log_artifact(RESULTS / "mlp_forecasts.parquet")
        tracker.log_artifact(RESULTS / "mlp_test_manifest.json")
    print(f"test: {len(mlp_forecasts)} forecasts, cap hits {cap_hits}")
    print(f"shuffled QLIKE {shuffled_qlike:.6f} > M0 {m0_qlike:.6f}: {shuffled_pass}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("select", "test"), required=True)
    args = parser.parse_args()
    if args.stage == "select":
        return select_stage()
    return test_stage()


if __name__ == "__main__":
    raise SystemExit(main())
