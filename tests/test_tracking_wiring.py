"""Isolated checks of the pipeline tracking blocks without running model pipelines."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

from volab import tracking


ROOT = Path(__file__).resolve().parents[1]


class RecordingTracker:
    def __init__(self, record):
        self.record = record

    def log_metrics(self, metrics, step=None):
        self.record["metrics"] = dict(metrics)
        self.record["metric_step"] = step

    def log_artifact(self, path):
        self.record.setdefault("artifacts", []).append(Path(path))


class TrackingRecorder:
    def __init__(self):
        self.runs = []

    @contextmanager
    def run(self, step, *, snapshot_id, params=None, **_kwargs):
        record = {"step": step, "snapshot_id": snapshot_id, "params": params or {}}
        self.runs.append(record)
        yield RecordingTracker(record)


def _tracking_block(script: str, function: str, bindings: dict):
    tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
    definition = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
    )
    tracking_node = next(
        node for node in ast.walk(definition)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and isinstance(item.context_expr.func.value, ast.Name)
            and item.context_expr.func.value.id == "tracking"
            and item.context_expr.func.attr == "run"
            for item in node.items
        )
    )
    statements = []
    if function == "main":
        metrics_assignment = next(
            node for node in definition.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "metrics" for target in node.targets)
        )
        statements.append(metrics_assignment)
    statements.append(tracking_node)
    module = ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[]))
    exec(compile(module, str(ROOT / "scripts" / script), "exec"), bindings)


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setenv("VFL_MLFLOW", "0")
    result = TrackingRecorder()
    monkeypatch.setattr(tracking, "run", result.run)
    return result


def test_run_eval_tracking_wiring(recorder, tmp_path):
    class MeanValue:
        def __init__(self, value):
            self.value = value

        def mean(self):
            return self.value

    models = ("M0", "M1", "M2", "M3")
    bindings = {
        "tracking": tracking,
        "MODELS": models,
        "qlike_loss": {model: MeanValue(0.1) for model in models},
        "mse_loss": {model: MeanValue(0.2) for model in models},
        "planted_leak": 0.3,
        "cw_p": 0.04,
        "cw_mean": 0.5,
        "folds": [1, 2],
        "index": [1, 2, 3],
        "f_min": 0.01,
        "lag": 4,
        "metadata": {"snapshot_id": "snapshot-eval"},
        "RESULTS": tmp_path,
    }
    _tracking_block("run_eval.py", "main", bindings)

    run = recorder.runs[0]
    assert run["step"] == "step7_eval"
    assert run["snapshot_id"] == "snapshot-eval"
    assert {"models", "n_folds", "n_forecasts", "f_min", "hac_lag"} <= run["params"].keys()
    assert {"qlike_M2", "mse_M2", "clark_west_pvalue"} <= run["metrics"].keys()
    assert len(run["artifacts"]) == 5


def test_mlp_select_tracking_wiring(recorder, tmp_path):
    _tracking_block("run_mlp.py", "select_stage", {
        "tracking": tracking,
        "metadata": {"snapshot_id": "snapshot-select"},
        "RESULTS": tmp_path,
        "best_score": 0.42,
        "selection": [1, 2, 3],
        "chosen": "cfg-a",
        "configs": [1, 2],
        "mlp": type("Mlp", (), {"SEEDS": [1, 2]})(),
        "folds": [1],
        "n_forecasts": 8,
    })
    run = recorder.runs[0]
    assert run["step"] == "step10_mlp_select"
    assert run["snapshot_id"] == "snapshot-select"
    assert {"stage", "config", "n_configs", "n_seeds", "n_folds"} <= run["params"].keys()
    assert {"best_validation_mean_qlike"} <= run["metrics"].keys()
    assert len(run["artifacts"]) == 2


def test_mlp_test_tracking_wiring(recorder, tmp_path):
    _tracking_block("run_mlp.py", "test_stage", {
        "tracking": tracking,
        "metadata": {"snapshot_id": "snapshot-test"},
        "RESULTS": tmp_path,
        "selection_manifest": {"config_id": "cfg-a"},
        "folds": [1, 2],
        "mlp_forecasts": [1, 2, 3],
        "m4_qlike": 0.11,
        "m4_mse": 0.22,
        "shuffled_qlike": 0.33,
        "m0_qlike": 0.44,
        "shuffled_pass": True,
        "cap_hits": 0,
    })
    run = recorder.runs[0]
    assert run["step"] == "step10_mlp_test"
    assert run["snapshot_id"] == "snapshot-test"
    assert {"stage", "config", "n_folds", "n_forecasts"} <= run["params"].keys()
    assert {"m4_qlike", "m4_mse"} <= run["metrics"].keys()
    assert len(run["artifacts"]) == 2
