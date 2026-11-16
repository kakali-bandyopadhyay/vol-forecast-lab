"""Author-owned Redis search cache, metrics and token-bucket limiter."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

HITS_KEY = "vfl:metrics:hits"
MISSES_KEY = "vfl:metrics:misses"
RATE_LIMITED_KEY = "vfl:metrics:rate_limited"
RATE_LIMIT_PREFIX = "vfl:ratelimit:"
INDEX_VERSION = os.getenv("VFL_INDEX_VERSION", "v1")  # Codex fix (P8e)

TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

local elapsed = now - ts
if elapsed < 0 then
    elapsed = 0
end
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(capacity / rate) + 1)
return allowed
"""


def make_key(
    query: str,
    date_from: str | None,
    date_to: str | None,
    doc_type: str | None,
    k: int,
    mode: str,
) -> str:
    """Return the normalized Redis key for one search request."""
    normalized = " ".join(query.lower().split())
    parts = [
        normalized,
        "" if date_from is None else str(date_from),
        "" if date_to is None else str(date_to),
        "" if doc_type is None else str(doc_type),
        str(k),
        str(mode),
        INDEX_VERSION,  # Codex fix (P8e)
    ]
    return "vfl:search:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def flush_search_cache(client) -> int:
    """Delete search response keys using Redis SCAN, returning the deleted count.  # Codex fix (P8e)"""
    deleted = 0
    batch: list[str] = []
    for key in client.scan_iter(match="vfl:search:*", count=500):
        batch.append(key)
        if len(batch) == 500:
            deleted += int(client.delete(*batch))
            batch.clear()
    if batch:
        deleted += int(client.delete(*batch))
    return deleted


class SearchCache:
    """JSON response cache with a fixed TTL and hit/miss counters."""

    def __init__(self, client, ttl: int = 3600):
        self.client = client
        self.ttl = ttl

    def get(self, key: str) -> dict[str, Any] | None:
        value = self.client.get(key)
        if value is None:
            self.client.incr(MISSES_KEY)
            return None
        self.client.incr(HITS_KEY)
        return json.loads(value)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self.client.set(key, json.dumps(value), ex=self.ttl)


class RateLimiter:
    """Atomic Redis token bucket keyed by client id."""

    def __init__(self, client, capacity: int = 10, refill_per_s: float = 5.0):
        self.client = client
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self._script = client.register_script(TOKEN_BUCKET_LUA)

    def allow(self, client_id: str, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        allowed = self._script(
            keys=[f"{RATE_LIMIT_PREFIX}{client_id}"],
            args=[self.capacity, self.refill_per_s, now],
        )
        if int(allowed) == 0:
            self.client.incr(RATE_LIMITED_KEY)
            return False
        return True


def get_metrics(client) -> dict[str, Any]:
    """Return hit, miss and rate-limit counters, with a zero-safe hit rate."""
    hits = int(client.get(HITS_KEY) or 0)
    misses = int(client.get(MISSES_KEY) or 0)
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": hits / total if total else 0.0,
        "rate_limited": int(client.get(RATE_LIMITED_KEY) or 0),
    }
