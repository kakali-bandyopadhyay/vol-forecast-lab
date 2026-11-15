"""Run the repository's Athena SQL files and save query metadata and sample rows."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_DIR = REPO_ROOT / "sql" / "athena"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "text" / "10c_athena.json"


def _row_values(row: dict[str, Any]) -> list[str | None]:
    return [item.get("VarCharValue") for item in row.get("Data", [])]


def fetch_rows(client: Any, query_execution_id: str) -> list[list[str | None]]:
    """Read every result page, dropping Athena's column-name row on the first page."""
    rows: list[list[str | None]] = []
    request: dict[str, Any] = {"QueryExecutionId": query_execution_id}
    first_page = True
    while True:
        response = client.get_query_results(**request)
        result_rows = response.get("ResultSet", {}).get("Rows", [])
        if first_page and result_rows:
            result_rows = result_rows[1:]
        rows.extend(_row_values(row) for row in result_rows)
        first_page = False
        token = response.get("NextToken")
        if not token:
            return rows
        request["NextToken"] = token


def run_queries(
    sql_dir: Path,
    output_path: Path,
    client: Any,
    *,
    timeout: float = 120,
    price_per_tb: float = 5.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    report: Callable[[str], None] = print,
) -> int:
    """Execute sorted SQL files using an injected Athena client; return a process status."""
    if not sql_dir.is_dir():
        report(f"No Athena SQL folder: {sql_dir}")
        return 0
    sql_files = sorted(sql_dir.glob("*.sql"))
    if not sql_files:
        report(f"No Athena SQL files in: {sql_dir}")
        return 0

    records: dict[str, dict[str, Any]] = {}
    failed_files: list[str] = []
    for sql_file in sql_files:
        query_id: str | None = None
        try:
            started = client.start_query_execution(
                QueryString=sql_file.read_text(encoding="utf-8"),
                QueryExecutionContext={"Database": "vfl_docs"},
                WorkGroup="vfl-wg",
            )["QueryExecutionId"]
            query_id = started
            deadline = monotonic_fn() + timeout
            while True:
                execution = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
                state = execution["Status"]["State"]
                if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    break
                if monotonic_fn() >= deadline:
                    state = "TIMEOUT"
                    client.stop_query_execution(QueryExecutionId=query_id)
                    break
                sleep_fn(1)

            stats = execution.get("Statistics", {})
            bytes_scanned = int(stats.get("DataScannedInBytes", 0) or 0)
            result_rows = fetch_rows(client, query_id) if state == "SUCCEEDED" else []
            records[sql_file.name] = {
                "query_execution_id": query_id,
                "state": state,
                "data_scanned_bytes": bytes_scanned,
                "engine_ms": int(stats.get("EngineExecutionTimeInMillis", 0) or 0),
                "row_count": len(result_rows),
                "rows": result_rows[:50],
                "cost_usd_estimate": bytes_scanned / 1e12 * price_per_tb,
                "cost_label": "ESTIMATE",
            }
            reason = execution["Status"].get("StateChangeReason")
            if reason:
                records[sql_file.name]["reason"] = reason
            if state != "SUCCEEDED":
                failed_files.append(sql_file.name)
                report(f"{sql_file.name}: query {state.lower()}")
        except Exception as exc:
            records[sql_file.name] = {
                "query_execution_id": query_id,
                "state": "ERROR",
                "data_scanned_bytes": 0,
                "engine_ms": 0,
                "row_count": 0,
                "rows": [],
                "cost_usd_estimate": 0.0,
                "cost_label": "ESTIMATE",
                "error": str(exc),
            }
            failed_files.append(sql_file.name)
            report(f"{sql_file.name}: {exc}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(records, output, indent=2)
        output.write("\n")
    return 1 if failed_files else 0


def main(
    argv: list[str] | None = None,
    *,
    client: Any | None = None,
    sql_dir: Path = DEFAULT_SQL_DIR,
    output_path: Path = DEFAULT_OUTPUT,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    report: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="vfl-build", help="AWS profile (default: vfl-build)")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--price-per-tb", type=float, default=5.0)
    args = parser.parse_args(argv)

    if not sql_dir.is_dir():
        report(f"No Athena SQL folder: {sql_dir}")
        return 0
    if not any(sql_dir.glob("*.sql")):
        report(f"No Athena SQL files in: {sql_dir}")
        return 0

    if client is None:
        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        client = session.client("athena")
    return run_queries(
        sql_dir,
        output_path,
        client,
        timeout=args.timeout,
        price_per_tb=args.price_per_tb,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        report=report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
