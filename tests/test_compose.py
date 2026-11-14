"""Structural checks for the local platform Compose file."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_compose_profiles_and_loopback_ports():
    """All four services are profile-gated and host ports bind to loopback only."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.platform.yml").read_text(encoding="utf-8"))
    profiles = {profile for service in compose["services"].values() for profile in service.get("profiles", [])}
    assert profiles == {"kafka", "search", "cache", "vector"}
    ports = [port for service in compose["services"].values() for port in service.get("ports", [])]
    assert ports
    assert all(str(port).startswith("127.0.0.1:") for port in ports)
    assert {"kafka_data", "opensearch_data", "qdrant_data"}.issubset(compose["volumes"])
    for service_name in ("kafka", "opensearch", "redis", "qdrant"):
        assert "healthcheck" in compose["services"][service_name]


def test_kafka_init_script_reaches_bash_intact():
    """kafka-init passes one script to bash -c (list form) and escapes $topic from Compose interpolation."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.platform.yml").read_text(encoding="utf-8"))
    init = compose["services"]["kafka-init"]
    assert init["entrypoint"] == ["/bin/bash", "-ec"]
    assert isinstance(init["command"], list) and len(init["command"]) == 1
    script = init["command"][0]
    assert '"$$topic"' in script
    assert "kafka:19092" in script
