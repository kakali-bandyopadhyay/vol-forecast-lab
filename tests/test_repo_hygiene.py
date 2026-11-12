"""Repository hygiene guards.

Tracked Markdown must not carry `](mailto:` links — a pasted-notion-style artefact that would put
an email address in a public repository. The test reads only files git actually tracks, so
untracked drafts and build output are out of scope.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked(pattern: str) -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    result = subprocess.run(
        ["git", "ls-files", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_tracked_markdown_has_no_mailto_links():
    offenders = []
    for relative in _tracked("*.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8", errors="replace")
        if "](mailto:" in text:
            offenders.append(relative)

    assert not offenders, f"mailto links in tracked markdown: {offenders}"


def test_the_guard_can_see_the_tracked_markdown():
    """A guard that silently finds no files would pass forever."""
    assert _tracked("*.md"), "no tracked Markdown found; the guard would be vacuous"
