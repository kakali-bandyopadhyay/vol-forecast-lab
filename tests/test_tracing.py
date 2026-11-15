"""Offline tests for tracing configuration plumbing."""

import socket

import pytest

from agent.tracing import get_tracing_config


@pytest.fixture
def no_network(monkeypatch):
    """Fail the test if anything opens a network connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_empty_key_disables_tracing(monkeypatch, no_network) -> None:
    """An empty API key always disables tracing without making network calls.

    (Checks the network, not sys.modules: the langsmith package registers a pytest plugin,
    so it can already be imported in CI before this test runs.)
    """
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "test-project")
    config = get_tracing_config()
    assert config == {
        "api_key": "",
        "tracing": False,
        "project": "test-project",
    }
