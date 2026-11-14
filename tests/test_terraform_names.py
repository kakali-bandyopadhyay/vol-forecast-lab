"""Ensure top-level AWS resource naming literals use the vfl prefix."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = REPO_ROOT / "infra"
NAME_ASSIGNMENT = re.compile(r'^  (name|bucket|function_name|role)\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def test_terraform_resource_name_literals_have_vfl_prefix():
    """Literal resource identifiers use vfl- or vfl_; Lambda log groups must be /aws/lambda/vfl-*."""
    files = [path for path in INFRA_DIR.rglob("*.tf") if ".terraform" not in path.parts]
    assert files
    literals = [(path.relative_to(INFRA_DIR).as_posix(), key, value) for path in files for key, value in NAME_ASSIGNMENT.findall(path.read_text(encoding="utf-8"))]
    assert literals
    invalid = [f"{path}:{key}={value}" for path, key, value in literals if not value.startswith(("vfl-", "vfl_", "/aws/lambda/vfl-"))]
    assert not invalid, "Non-vfl resource name literals: " + ", ".join(invalid)
