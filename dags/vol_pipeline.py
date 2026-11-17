"""Manual snapshot-based pipeline DAG for the pre-registered study.

The schedule is None because this is a snapshot-based, pre-registered study: refits and
evaluations must be started deliberately, never by a recurring scheduler.

choose_path and gate_docs run in Airflow's own Python (the stated exception to "every task
uses $VFL_PYTHON"); every analysis and document task runs the repository virtualenv via
BashOperator. Secrets are read from Airflow Variables, never stored in this file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from airflow.sdk import DAG, Param
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import BranchPythonOperator, ShortCircuitOperator

VFL_COMMAND = 'cd "$VFL_REPO" && "$VFL_PYTHON" scripts/'
DOC_ENV = {
    "OPENAI_API_KEY": "{{ var.value.openai_api_key }}",
    "QDRANT_URL": "{{ var.value.qdrant_url }}",
}


def _path_branch(**context) -> str:
    return "audit_snapshot" if context["params"]["use_snapshot"] else "smoke_run"


def _docs_gate(**context) -> bool:
    return bool(context["params"]["use_snapshot"])


with DAG(
    dag_id="vol_pipeline",
    description="Snapshot-based vol-forecast-lab analysis and document pipeline",
    schedule=None,
    catchup=False,
    start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        "use_snapshot": Param(False, type="boolean"),
        "snapshot": Param("", type="string", pattern="^[A-Za-z0-9_.-]*$"),
    },
    tags=["vol-forecast-lab", "manual"],
) as dag:
    choose_path = BranchPythonOperator(
        task_id="choose_path",
        python_callable=_path_branch,
    )
    smoke_run = BashOperator(
        task_id="smoke_run",
        bash_command=VFL_COMMAND + "smoke_run.py",
    )
    audit_snapshot = BashOperator(
        task_id="audit_snapshot",
        bash_command=(
            VFL_COMMAND
            + "audit_snapshot.py{% if params.snapshot %} --snapshot '{{ params.snapshot }}'{% endif %}"
        ),
    )
    build_rv = BashOperator(
        task_id="build_rv",
        bash_command=VFL_COMMAND + "build_panel.py",
    )
    refit_models = BashOperator(
        task_id="refit_models",
        bash_command=VFL_COMMAND + "run_eval.py",
    )
    evaluate = BashOperator(
        task_id="evaluate",
        bash_command=VFL_COMMAND + "run_holm.py",
    )
    q2_events = BashOperator(
        task_id="q2_events",
        bash_command=VFL_COMMAND + "run_q2_q3.py",
    )
    drift_report = BashOperator(
        task_id="drift_report",
        bash_command=VFL_COMMAND + "run_drift.py",
    )

    gate_docs = ShortCircuitOperator(
        task_id="gate_docs",
        python_callable=_docs_gate,
    )
    fetch_docs = BashOperator(
        task_id="fetch_docs",
        bash_command=VFL_COMMAND + "run_text_pipeline.py --stage fetch",
        env=DOC_ENV,
        append_env=True,
    )
    extract = BashOperator(
        task_id="extract",
        bash_command=VFL_COMMAND + "run_text_pipeline.py --stage extract",
        env=DOC_ENV,
        append_env=True,
    )
    chunk = BashOperator(
        task_id="chunk",
        bash_command=VFL_COMMAND + "run_text_pipeline.py --stage chunk",
        env=DOC_ENV,
        append_env=True,
    )
    embed_index = BashOperator(
        task_id="embed_index",
        bash_command=VFL_COMMAND + "run_text_pipeline.py --stage index",
        env=DOC_ENV,
        append_env=True,
    )

    choose_path >> [smoke_run, audit_snapshot]
    audit_snapshot >> build_rv >> refit_models >> evaluate >> q2_events >> drift_report

    # The document branch is independent of the audit: gate_docs is a root task and
    # short-circuits the whole branch when use_snapshot is false.
    gate_docs >> fetch_docs >> extract >> chunk >> embed_index
