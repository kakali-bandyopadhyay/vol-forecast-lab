"""Export root LangSmith runs for one evaluation id without printing secrets."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]


def _metadata(run) -> dict:
    extra = getattr(run, "extra", None) or {}
    if not isinstance(extra, dict):
        return {}
    metadata = extra.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def export_runs(project: str, eval_run_id: str, *, repo_root: Path = REPO_ROOT, client=None) -> Path:
    if client is None:
        from dotenv import load_dotenv
        load_dotenv(repo_root / ".env", override=False)
        from langsmith import Client
        client = Client()

    rows = []
    for run in client.list_runs(project_name=project, is_root=True):
        if getattr(run, "parent_run_id", None) is not None:
            continue
        if _metadata(run).get("eval_run_id") != eval_run_id:
            continue
        started = getattr(run, "start_time", None)
        ended = getattr(run, "end_time", None)
        latency = (ended - started).total_seconds() if started and ended else None
        rows.append({
            "run_name": getattr(run, "name", None),
            "run_id": str(getattr(run, "id", "")),
            "url": client.get_run_url(run=run, project_name=project),
            "latency_s": latency,
            "total_tokens": getattr(run, "total_tokens", None),
            "error": getattr(run, "error", None),
        })

    safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "_", project)
    target = repo_root / "docs" / "langsmith" / f"{safe_project}_runs.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = ["run_name", "run_id", "url", "latency_s", "total_tokens", "error"]
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(len(rows))
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="vfl-agent-v2-eval")
    parser.add_argument("--eval-run-id", required=True)
    args = parser.parse_args(argv)
    export_runs(args.project, args.eval_run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
