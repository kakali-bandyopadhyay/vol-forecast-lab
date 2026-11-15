"""Offline tests for the Athena query runner."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import run_athena


class FakeAthena:
    def __init__(self, states, *, bytes_scanned=2_000_000_000_000, rows=None):
        self.states = iter(states)
        self.bytes_scanned = bytes_scanned
        self.rows = rows or [{"Data": [{"VarCharValue": "column"}]}, {"Data": [{"VarCharValue": "value"}]}]
        self.started = []
        self.stopped = []

    def start_query_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"QueryExecutionId": "query-123"}

    def get_query_execution(self, **_kwargs):
        state = next(self.states)
        return {
            "QueryExecution": {
                "Status": {"State": state},
                "Statistics": {
                    "DataScannedInBytes": self.bytes_scanned,
                    "EngineExecutionTimeInMillis": 42,
                },
            }
        }

    def stop_query_execution(self, **kwargs):
        self.stopped.append(kwargs["QueryExecutionId"])

    def get_query_results(self, **_kwargs):
        return {"ResultSet": {"Rows": self.rows}}


def _sql_file(folder: Path, name: str = "sample.sql") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("SELECT 1", encoding="utf-8")


def test_success_saves_rows_stats_and_estimated_cost(tmp_path: Path):
    sql_dir = tmp_path / "sql"
    _sql_file(sql_dir)
    client = FakeAthena(["SUCCEEDED"])
    output = tmp_path / "results" / "10c_athena.json"

    status = run_athena.main(
        ["--price-per-tb", "3.5"], client=client, sql_dir=sql_dir, output_path=output
    )

    assert status == 0
    assert client.started[0]["WorkGroup"] == "vfl-wg"
    assert client.started[0]["QueryExecutionContext"] == {"Database": "vfl_docs"}
    record = json.loads(output.read_text(encoding="utf-8"))["sample.sql"]
    assert record["query_execution_id"] == "query-123"
    assert record["state"] == "SUCCEEDED"
    assert record["data_scanned_bytes"] == 2_000_000_000_000
    assert record["engine_ms"] == 42
    assert record["row_count"] == 1
    assert record["rows"] == [["value"]]
    assert record["cost_usd_estimate"] == 7.0
    assert record["cost_label"] == "ESTIMATE"


def test_failed_query_returns_nonzero_and_names_file(tmp_path: Path):
    sql_dir = tmp_path / "sql"
    _sql_file(sql_dir, "broken.sql")
    messages = []

    status = run_athena.main(
        [], client=FakeAthena(["FAILED"]), sql_dir=sql_dir,
        output_path=tmp_path / "out.json", report=messages.append
    )

    assert status != 0
    assert any("broken.sql" in message for message in messages)


def test_timeout_returns_nonzero(tmp_path: Path):
    sql_dir = tmp_path / "sql"
    _sql_file(sql_dir)
    ticks = iter([0.0, 0.0, 1.0])
    messages = []

    client = FakeAthena(["RUNNING", "RUNNING"])
    status = run_athena.main(
        ["--timeout", "1"], client=client,
        sql_dir=sql_dir, output_path=tmp_path / "out.json",
        sleep_fn=lambda _seconds: None, monotonic_fn=lambda: next(ticks), report=messages.append
    )

    assert status != 0
    record = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["sample.sql"]
    assert record["state"] == "TIMEOUT"
    assert client.stopped == ["query-123"]
    assert any("sample.sql" in message for message in messages)


def test_empty_sql_folder_reports_and_exits_zero_without_output(tmp_path: Path):
    sql_dir = tmp_path / "empty"
    sql_dir.mkdir()
    messages = []
    output = tmp_path / "out.json"

    status = run_athena.main(
        [], client=None, sql_dir=sql_dir, output_path=output, report=messages.append
    )

    assert status == 0
    assert messages and "No Athena SQL files" in messages[0]
    assert not output.exists()
