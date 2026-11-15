"""Structural checks for the local Airflow Compose configuration."""

from pathlib import Path
import re

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_airflow_compose_is_local_and_pinned() -> None:
    """Compose binds published ports to loopback and has no floating image tags."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.airflow.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["airflow-scheduler"]["environment"]["AIRFLOW__CORE__EXECUTOR"] == "LocalExecutor"
    ports = [port for service in services.values() for port in service.get("ports", [])]
    assert ports
    assert all(str(port).startswith("127.0.0.1:") for port in ports)
    for service in services.values():
        image = service.get("image")
        if image:
            assert not re.search(r":latest(?:$|\s)", image)
            assert ":" in image or image.startswith("${AIRFLOW_IMAGE_NAME")
    assert "airflow-worker" not in services
    assert "redis" not in services
    all_text = (REPO_ROOT / "docker-compose.airflow.yml").read_text(encoding="utf-8")
    assert "_PIP_ADDITIONAL_REQUIREMENTS" not in all_text
    assert "${AIRFLOW_ADMIN_USER" in all_text
    assert "${AIRFLOW_ADMIN_PASSWORD" in all_text
    airflow_env = services["airflow-scheduler"]["environment"]
    assert airflow_env["VFL_PYTHON"] == "/opt/vfl-venv/bin/python"
    assert airflow_env["VFL_REPO"] == "/opt/vfl"
    assert airflow_env["PYTHONPATH"] == "/opt/vfl/src:/opt/vfl"


def test_airflow_dockerfile_uses_a_separate_project_venv() -> None:
    """The pinned Airflow image stays separate from project dependencies."""
    dockerfile = (REPO_ROOT / "airflow" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM apache/airflow:3.4.0" in dockerfile
    assert "python -m venv /opt/vfl-venv" in dockerfile
    assert "COPY requirements.txt /opt/vfl-requirements.txt" in dockerfile
    assert "--index-url https://download.pytorch.org/whl/cpu torch==2.15.0" in dockerfile
    assert "/opt/vfl-venv/bin/python -m pip install" in dockerfile
    assert "-r /opt/vfl-requirements.txt" in dockerfile
    assert "COPY airflow/requirements.txt" not in dockerfile
    assert not (REPO_ROOT / "airflow" / "requirements.txt").exists()


def test_compose_contains_no_literal_secret_values() -> None:
    """Credential-bearing settings use empty-example environment variables."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.airflow.yml").read_text(encoding="utf-8"))
    assert compose["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"].startswith("${")
    assert compose["services"]["airflow-init"]["environment"]["_AIRFLOW_WWW_USER_PASSWORD"].startswith("${")
