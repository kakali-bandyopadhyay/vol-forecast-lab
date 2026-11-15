"""MLflow tracking helper (Step 12 plumbing; Claude, reviewed by the author).

Logging only: nothing here changes a model, a fit or a results table.
Disabled (a no-op tracker, mlflow never imported) when VFL_MLFLOW=0 or when running on CI (CI=true),
so tests and GitHub Actions never write tracking data.

Usage:
    with tracking.run("step7_eval", snapshot_id=sid, params={"model": "M2"}) as t:
        t.log_metrics({"qlike_test": 0.42})
        t.log_artifact(Path("results/forecasts.parquet"))
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from volab.data import REPO_ROOT

EXPERIMENT = "vol-forecast-lab"
TRACKING_DIR = REPO_ROOT / "mlruns"


def enabled() -> bool:
    """Tracking is on unless VFL_MLFLOW=0 or the process runs on CI."""
    if os.environ.get("VFL_MLFLOW", "1").strip() == "0":
        return False
    return os.environ.get("CI", "").strip().lower() != "true"


def code_commit(root: Path = REPO_ROOT) -> str:
    """Current git commit, or 'unknown' outside a checkout."""
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


class NullTracker:
    """Accepts every call and records nothing."""

    run_id: str | None = None

    def log_params(self, params: dict[str, Any]) -> None:
        """Ignore parameters."""

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        """Ignore metrics."""

    def log_artifact(self, path: Path) -> None:
        """Ignore artifacts."""


class MlflowTracker:
    """Thin wrapper over an active MLflow run."""

    def __init__(self, mlflow_module: Any, run_id: str) -> None:
        self._mlflow = mlflow_module
        self.run_id = run_id

    def log_params(self, params: dict[str, Any]) -> None:
        """Log parameters as strings (MLflow stores params as text)."""
        self._mlflow.log_params({key: str(value) for key, value in params.items()})

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        """Log numeric metrics, optionally at a step (e.g. fold index)."""
        self._mlflow.log_metrics({key: float(value) for key, value in metrics.items()}, step=step)

    def log_artifact(self, path: Path) -> None:
        """Attach a file (e.g. an output parquet) to the run."""
        self._mlflow.log_artifact(str(path))


@contextmanager
def run(step: str, *, snapshot_id: str, params: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None, tracking_dir: Path = TRACKING_DIR) -> Iterator[NullTracker | MlflowTracker]:
    """Open one MLflow run tagged with step, snapshot_id and code_commit; no-op when disabled."""
    if not enabled():
        yield NullTracker()
        return
    import mlflow  # imported only when tracking is on

    tracking_dir = Path(tracking_dir)
    tracking_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{(tracking_dir / 'mlflow.db').as_posix()}")
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=(tracking_dir / "artifacts").resolve().as_uri())
    mlflow.set_experiment(EXPERIMENT)
    run_tags = {"step": step, "snapshot_id": snapshot_id, "code_commit": code_commit()}
    run_tags.update(tags or {})
    with mlflow.start_run(run_name=step, tags=run_tags) as active:
        tracker = MlflowTracker(mlflow, active.info.run_id)
        if params:
            tracker.log_params(params)
        yield tracker
