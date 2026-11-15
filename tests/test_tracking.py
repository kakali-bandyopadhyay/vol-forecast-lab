"""Tests for the MLflow tracking helper (Step 12 plumbing)."""

import builtins

import pytest

from volab import tracking


def test_disabled_on_ci_and_by_flag_is_a_no_op_without_importing_mlflow(monkeypatch, tmp_path):
    """CI=true or VFL_MLFLOW=0 → NullTracker; mlflow must not even be imported."""
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "mlflow" or name.startswith("mlflow."):
            raise AssertionError("mlflow imported while tracking is disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    for env in ({"CI": "true"}, {"CI": "", "VFL_MLFLOW": "0"}):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        with tracking.run("toy", snapshot_id="S", params={"a": 1}, tracking_dir=tmp_path) as tracker:
            tracker.log_metrics({"m": 1.0})
            tracker.log_artifact(tmp_path / "missing.parquet")
        assert isinstance(tracker, tracking.NullTracker)
    assert not (tmp_path / "mlflow.db").exists()


def test_enabled_run_records_tags_params_metrics_and_artifact(monkeypatch, tmp_path):
    """A real local run carries step/snapshot/commit tags, params, metrics and the artifact."""
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("CI", "")
    monkeypatch.setenv("VFL_MLFLOW", "1")
    artifact = tmp_path / "out.txt"
    artifact.write_text("x", encoding="utf-8")
    with tracking.run("step7_eval", snapshot_id="20261112T071104Z", params={"model": "M2"},
                      tracking_dir=tmp_path / "mlruns") as tracker:
        tracker.log_metrics({"qlike": 0.5}, step=1)
        tracker.log_artifact(artifact)
        run_id = tracker.run_id
    client = mlflow.tracking.MlflowClient(tracking_uri=f"sqlite:///{(tmp_path / 'mlruns' / 'mlflow.db').as_posix()}")
    data = client.get_run(run_id).data
    assert data.tags["step"] == "step7_eval" and data.tags["snapshot_id"] == "20261112T071104Z"
    assert "code_commit" in data.tags
    assert data.params == {"model": "M2"} and data.metrics["qlike"] == 0.5
    assert [item.path for item in client.list_artifacts(run_id)] == ["out.txt"]
