"""Contract tests for the Step 13 Airflow DAG."""

from __future__ import annotations

from datetime import timedelta

import pytest
pytest.importorskip("airflow.dag_processing.dagbag")  # real Airflow only: the repo's airflow/ folder (Dockerfile) shadows the top-level name
from airflow.dag_processing.dagbag import DagBag

pytestmark = pytest.mark.airflow

ANALYSIS = [
    "audit_snapshot", "build_rv", "refit_models", "evaluate", "q2_events", "drift_report",
]
DOCUMENTS = ["fetch_docs", "extract", "chunk", "embed_index"]


def _load_dag():
    bag = DagBag(dag_folder="dags")  # Airflow 3.4: no include_examples; LOAD_EXAMPLES=False in the runner
    assert bag.import_errors == {}
    assert "vol_pipeline" in bag.dags
    return bag.dags["vol_pipeline"]


def _linear_order(dag, start: str) -> list[str]:
    order = [start]
    task = dag.get_task(start)
    while task.downstream_task_ids:
        assert len(task.downstream_task_ids) == 1
        task = dag.get_task(next(iter(task.downstream_task_ids)))
        order.append(task.task_id)
    return order


def test_dag_imports_without_errors():
    dag = _load_dag()
    assert dag.dag_id == "vol_pipeline"


def test_analysis_and_document_task_order():
    dag = _load_dag()
    assert _linear_order(dag, "audit_snapshot") == ANALYSIS
    assert _linear_order(dag, "fetch_docs") == DOCUMENTS
    assert dag.get_task("choose_path").downstream_task_ids == {"smoke_run", "audit_snapshot"}


def test_dag_settings_and_defaults():
    dag = _load_dag()
    assert dag.schedule is None
    assert dag.catchup is False
    for task in dag.tasks:
        assert task.retries == 1
        assert task.retry_delay == timedelta(minutes=2)
    assert dag.params["use_snapshot"] is False


def test_forced_audit_failure_leaves_analysis_upstream_failed():
    dag = _load_dag()
    dag.get_task("audit_snapshot").bash_command = "exit 1"
    for name in DOCUMENTS:
        task = dag.get_task(name)
        task.bash_command = "true"
        task.env = None
    for task in dag.tasks:
        task.retries = 0

    run = dag.test(run_conf={"use_snapshot": True})

    def state(task_id: str) -> str:
        return str(run.get_task_instance(task_id).state).lower()

    assert state("audit_snapshot") == "failed"
    for name in ANALYSIS[1:]:
        assert state(name) == "upstream_failed"
    assert state("smoke_run") == "skipped"
    # Proves the in-memory overrides were used: the real document stages cannot succeed here
    # (read-only repo, no keys, no Qdrant), so "success" means "true" ran, not run_text_pipeline.py.
    for name in DOCUMENTS:
        assert state(name) == "success"
