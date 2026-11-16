"""API cache and limiter integration tests using an overridden fake Redis client."""

import pytest
from fastapi.testclient import TestClient

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis[lua] is required by the API cache tests")

from api import app as api_module


@pytest.fixture
def client():
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    api_module.app.dependency_overrides[api_module.get_redis_client] = lambda: redis_client
    # Fixed clock: the token bucket must not refill between requests (real clock made the 429 test flaky).
    api_module.app.dependency_overrides[api_module.get_clock] = lambda: (lambda: 1000.0)
    try:
        yield TestClient(api_module.app), redis_client
    finally:
        api_module.app.dependency_overrides.pop(api_module.get_redis_client, None)
        api_module.app.dependency_overrides.pop(api_module.get_clock, None)


def test_search_reuses_cached_response(client, monkeypatch):
    http, _ = client
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return [{"chunk_id": "c1"}]

    monkeypatch.setattr(api_module.index, "search", fake_search)
    first = http.get("/search", params={"q": "rates", "k": 4})
    second = http.get("/search", params={"q": "rates", "k": 4})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1


def test_search_rate_limit_returns_429_and_metrics_merge_cache_metrics(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(api_module.index, "search", lambda query, **kwargs: [])

    responses = [http.get("/search", params={"q": f"query {i}"}) for i in range(11)]
    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429
    assert responses[10].json() == {"detail": "rate limited"}

    metrics = http.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.json() == {"hits": 0, "misses": 10, "hit_rate": 0.0, "rate_limited": 1}


def test_search_fails_open_when_redis_is_down(client, monkeypatch):
    http, _ = client
    from redis.exceptions import ConnectionError as RedisConnectionError

    class DownRedis:
        """Every Redis call raises, as a stopped server would."""

        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RedisConnectionError("redis down")

            return fail

    down = DownRedis()
    api_module.app.dependency_overrides[api_module.get_redis_client] = lambda: down
    monkeypatch.setattr(api_module.index, "search", lambda query, **kwargs: [{"chunk_id": "c1"}])

    response = http.get("/search", params={"q": "rates"})
    assert response.status_code == 200
    assert response.json()["results"] == [{"chunk_id": "c1"}]
    assert http.get("/metrics").json()["redis_error"] is True
