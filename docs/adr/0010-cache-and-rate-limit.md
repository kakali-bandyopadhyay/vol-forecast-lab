# ADR 0010 — Redis search cache and token-bucket rate limiter, failing open

**Status:** Accepted · **Date:** 10c-5 (2026-11-16)

**Context.** Hybrid search costs an embedding call and two engine queries; repeated questions and
bursts should not repeat that work or overload the API.

**Decision.** Cache key = SHA-256 of the normalised query (lower-case, whitespace collapsed) plus
date range, doc_type, k and mode; TTL 3,600 s. Atomic Lua token bucket per client IP (capacity 10,
refill 5/s). Hit/miss/limited counters on `/metrics`. One limiter and cache per Redis client;
0.25 s timeouts; on a Redis error the API serves uncached and unlimited (fail open).

**Alternatives rejected.** In-process cache (lost on restart, not shared across replicas); fail closed
(a cache outage would take search down).

**Consequences.** Results can be up to an hour stale after a reindex (the key has no index version);
clients behind one NAT share a bucket.

**Evidence.** Miss 4,013 ms (incl. cold start) → hit 37 ms for a case/spacing variant; 15-request burst
→ 10 allowed, 5 × 429; `/metrics` hit_rate 0.917 (FINDINGS 10c-5).

**Revisit if.** Reindexing becomes routine — add the index version to the cache key.
