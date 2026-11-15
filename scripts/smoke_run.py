"""Run snapshot audit and panel build in an isolated temporary repository copy."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".venv", ".git", "data", "results", "mlruns"}
SNAPSHOT_ID = "20240101T000000Z"


def _ignore(directory: str, names: list[str]) -> set[str]:
    """Exclude requested heavyweight and live-data directories from the copy."""
    return EXCLUDED_DIRS.intersection(names)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command and relay its captured output to the caller."""
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result


def main() -> int:
    """Prepare a disposable repo copy, then run audit and panel scripts in it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository root")
    args = parser.parse_args()
    repo = args.repo.resolve()
    fixture_data = repo / "fixtures" / "synthetic" / "data"
    if not fixture_data.is_dir():
        print(f"synthetic fixture directory not found: {fixture_data}", file=sys.stderr)
        return 2
    if not (repo / "data" / "events").is_dir():
        print("real event CSV directory not found: data/events", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="vol-forecast-lab-smoke-") as temp_name:
        temp_root = Path(temp_name) / "repo"
        shutil.copytree(repo, temp_root, ignore=_ignore)
        shutil.copytree(fixture_data, temp_root / "data")
        shutil.copytree(repo / "data" / "events", temp_root / "data" / "events")

        audit = _run(
            [sys.executable, "scripts/audit_snapshot.py", "--snapshot", SNAPSHOT_ID], temp_root,
        )
        audit_json = temp_root / "data" / "audit.json"
        missing_sessions: int | str = "unavailable"
        missing_spy: list[str] | str = "unavailable"
        missing_vix: list[str] | str = "unavailable"
        if audit_json.is_file():
            audit_payload = json.loads(audit_json.read_text(encoding="utf-8"))
            missing_sessions = audit_payload["checks"]["missing_sessions"]["missing_count"]
            missing_spy = audit_payload["checks"]["excluded_dates"]["missing_spy"]
            missing_vix = audit_payload["checks"]["excluded_dates"]["missing_vix"]
        audit_checks_pass = (
            audit.returncode == 0
            and missing_sessions == 0
            and missing_spy == []
            and missing_vix == []
        )
        audit_status = "passed" if audit_checks_pass else "failed"
        panel = _run(
            [sys.executable, "scripts/build_panel.py", "--snapshot", SNAPSHOT_ID], temp_root,
        )
        panel_match = re.search(r"^panel\s+(\d+) rows\b", panel.stdout, re.MULTILINE)
        panel_rows = panel_match.group(1) if panel_match else "unavailable"
        print(f"missing_sessions: {missing_sessions}")
        print(f"missing_spy: {missing_spy}")
        print(f"missing_vix: {missing_vix}")
        print(f"audit status: {audit_status}")
        print(f"panel rows: {panel_rows}")
        print(f"audit exit code: {audit.returncode}")
        print(f"panel exit code: {panel.returncode}")
        return 0 if audit_checks_pass and panel.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
