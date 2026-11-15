"""Offline checks for the Docker image and GitHub Actions CI workflow."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_pinned_non_root_healthchecked_image() -> None:
    """The API image must pin Python, drop root, and define a health check."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"(?m)^FROM python:3\.12\.\d+-slim(?:-[\w.-]+)?$", dockerfile)
    assert not re.search(r"(?m)^FROM\s+\S*:latest\b", dockerfile, re.IGNORECASE)
    assert re.search(r"(?m)^USER\s+(?!root\s*$)(?:[a-z_][\w.-]*|\d+)\s*$", dockerfile)
    assert re.search(r"(?m)^HEALTHCHECK\s+", dockerfile)


def test_ci_workflow_has_five_jobs_and_versioned_actions() -> None:
    """The CI workflow must retain its jobs and pin every action by tag."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert set(jobs) == {"python-tests", "terraform", "docker-build", "dag-import", "k8s-dry-run"}
    for job in jobs.values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert re.search(r"@[vV]?\d+(?:\.\d+)*(?:[-+][\w.-]+)?$", step["uses"])


def test_ci_runs_lint_and_synthetic_smoke() -> None:
    """Python CI runs Ruff before tests and the synthetic smoke afterward."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["python-tests"]["steps"]
    commands = [step.get("run", "") for step in steps]

    lint_index = next(i for i, command in enumerate(commands) if "ruff check --select E9,F63,F7,F82 ." in command)
    pytest_index = next(i for i, command in enumerate(commands) if "python -m pytest -q" in command)
    smoke_index = next(i for i, command in enumerate(commands) if "python scripts/smoke_run.py" in command)
    assert lint_index < pytest_index < smoke_index
