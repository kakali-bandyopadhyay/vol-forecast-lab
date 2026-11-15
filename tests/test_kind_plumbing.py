"""Offline checks for the local kind cluster plumbing."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_kind_config_is_pinned_and_maps_loopback_port() -> None:
    config = yaml.safe_load((ROOT / "k8s" / "kind-config.yaml").read_text(encoding="utf-8"))

    assert config["kind"] == "Cluster"
    assert config["name"] == "vfl"
    node = config["nodes"][0]
    assert re.fullmatch(r"kindest/node:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", node["image"])
    mapping = node["extraPortMappings"][0]
    assert mapping["containerPort"] == 30080
    assert mapping["hostPort"] == 30080
    assert mapping["listenAddress"] == "127.0.0.1"


def test_kind_scripts_use_cluster_name_vfl() -> None:
    up_script = (ROOT / "scripts" / "kind_up.ps1").read_text(encoding="utf-8")
    down_script = (ROOT / "scripts" / "kind_down.ps1").read_text(encoding="utf-8")

    assert "kind create cluster --config $kindConfig" in up_script
    assert "kind load docker-image vol-forecast-lab:local --name vfl" in up_script
    assert "kind delete cluster --name vfl" in down_script
    assert (ROOT / "scripts" / "kind_up.ps1").is_file()
    assert (ROOT / "scripts" / "kind_down.ps1").is_file()


def test_ci_validates_manifests_with_kubeconform() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["k8s-validate"]
    script = "\n".join(step.get("run", "") for step in job["steps"])

    assert "kubeconform -strict -summary -kubernetes-version 1.37.0 -ignore-filename-pattern kind-config.yaml k8s/" in script
    assert "sha256sum --check" in script
    assert "version=\"v0.8.0\"" in script
    assert "no manifests yet" in script
