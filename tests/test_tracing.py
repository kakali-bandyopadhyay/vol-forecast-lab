"""Offline tests for tracing configuration plumbing."""

import sys

from agent.tracing import get_tracing_config


def test_empty_key_disables_tracing(monkeypatch) -> None:
    """An empty API key always disables tracing without making network calls."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "test-project")
    config = get_tracing_config()
    assert config == {
        "api_key": "",
        "tracing": False,
        "project": "test-project",
    }
    assert "langsmith" not in sys.modules
