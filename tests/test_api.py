"""Offline tests for API health, search/statement routing and error mapping."""

from fastapi.testclient import TestClient

from api.app import app


def test_health_returns_version():
    """Health endpoint reports a serving state and application version."""
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_search_invalid_date_returns_422():
    """A malformed date filter is rejected before any index call reaches Qdrant."""
    response = TestClient(app).get("/search", params={"q": "minutes", "date_from": "2020-13-01"})
    assert response.status_code == 422
    assert "YYYY-MM-DD" in response.json()["detail"]


def test_search_returns_index_results(monkeypatch):
    """Search passes filters through and wraps the index results."""
    seen = {}

    def fake_search(query, **kwargs):
        seen.update(kwargs, query=query)
        return [{"chunk_id": "statement-20200129-0000", "url": "https://example.test"}]

    monkeypatch.setattr("api.app.index.search", fake_search)
    response = TestClient(app).get("/search", params={"q": "rates", "date_from": "2020-01-01", "doc_type": "statement", "k": 3})
    assert response.status_code == 200
    assert response.json()["results"][0]["chunk_id"] == "statement-20200129-0000"
    assert seen == {"query": "rates", "date_from": "2020-01-01", "date_to": None, "doc_type": "statement", "k": 3}


def test_statement_missing_date_returns_404(monkeypatch):
    """An unknown statement date maps KeyError to 404."""
    def missing(date):
        raise KeyError(date)

    monkeypatch.setattr("api.app.index.get_statement", missing)
    assert TestClient(app).get("/statement/2099-01-01").status_code == 404


def test_statement_bad_date_returns_422(monkeypatch):
    """A malformed statement date maps ValueError to 422."""
    def bad(date):
        raise ValueError("date must use YYYY-MM-DD")

    monkeypatch.setattr("api.app.index.get_statement", bad)
    assert TestClient(app).get("/statement/2020-1-29").status_code == 422
