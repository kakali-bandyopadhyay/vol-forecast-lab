"""Tests for the C-cache Redis contract; failures are author pending until cache.py is implemented."""

import pytest

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis[lua] is required by the C-cache tests")

from volab.text import cache


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


def test_identical_search_is_cached_and_ttl_is_3600(redis_client):
    search_cache = cache.SearchCache(redis_client)
    key = cache.make_key("rate hikes", None, None, None, 5, "hybrid")

    assert search_cache.get(key) is None
    search_cache.set(key, {"results": ["chunk-1"]})
    assert search_cache.get(key) == {"results": ["chunk-1"]}
    assert redis_client.ttl(key) == 3600


def test_query_key_collapses_case_and_whitespace(redis_client):
    first = cache.make_key("  Rate   Hikes\nNow ", None, None, None, 5, "hybrid")
    second = cache.make_key("rate hikes now", None, None, None, 5, "hybrid")
    assert first == second


def test_query_key_includes_k_and_mode():
    base = cache.make_key("rates", None, None, None, 5, "hybrid")
    assert cache.make_key("rates", None, None, None, 6, "hybrid") != base
    assert cache.make_key("rates", None, None, None, 5, "bm25") != base


def test_query_key_includes_index_version(monkeypatch):  # Codex fix (10c review, 16 Nov)
    base = cache.make_key("rates", None, None, None, 5, "hybrid")
    monkeypatch.setattr(cache, "INDEX_VERSION", "v2")
    assert cache.make_key("rates", None, None, None, 5, "hybrid") != base


def test_flush_search_cache_preserves_rate_limit_keys(redis_client):  # Codex fix (10c review, 16 Nov)
    redis_client.set("vfl:search:first", "1")
    redis_client.set("vfl:search:second", "2")
    redis_client.set("vfl:ratelimit:client", "3")

    assert cache.flush_search_cache(redis_client) == 2
    assert list(redis_client.scan_iter(match="vfl:search:*")) == []
    assert redis_client.get("vfl:ratelimit:client") == "3"


def test_metrics_calculate_hit_rate_and_zero_lookup(redis_client):
    assert cache.get_metrics(redis_client) == {
        "hits": 0,
        "misses": 0,
        "hit_rate": 0.0,
        "rate_limited": 0,
    }
    search_cache = cache.SearchCache(redis_client)
    assert search_cache.get("missing") is None
    search_cache.set("present", {"ok": True})
    assert search_cache.get("present") == {"ok": True}
    assert cache.get_metrics(redis_client) == {
        "hits": 1,
        "misses": 1,
        "hit_rate": 0.5,
        "rate_limited": 0,
    }


def test_token_bucket_refills_and_counts_denials(redis_client):
    limiter = cache.RateLimiter(redis_client, capacity=10, refill_per_s=5.0)
    assert all(limiter.allow("client-a", now=100.0) for _ in range(10))
    assert limiter.allow("client-a", now=100.0) is False
    assert limiter.allow("client-a", now=100.2) is True
    assert cache.get_metrics(redis_client)["rate_limited"] == 1
