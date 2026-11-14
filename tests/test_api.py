"""Offline smoke tests for API health and unavailable author stubs."""

from fastapi.testclient import TestClient

from api.app import app


def test_health_returns_version():
    """Health endpoint reports a serving state and application version."""
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_search_returns_501_until_index_stub_is_implemented():
    """Search reports a clear 501 while the author-owned index stub is present."""
    response = TestClient(app).get("/search", params={"q": "minutes"})
    assert response.status_code == 501
    assert "index stub" in response.json()["detail"]
