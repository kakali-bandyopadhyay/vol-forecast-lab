# ADR 0005 — Kafka: at-least-once delivery, idempotent consumers, dead-letter topic

**Status:** Accepted · **Date:** 10c-1

**Context.** Documents move fetch → S3 → index through independent consumers that can crash and
restart. Lost documents are worse than duplicates; duplicates must not change the index.

**Decision.** Topics `docs.raw`, `docs.clean`, `docs.dlq`; idempotent producer (`acks=all`); manual
offset commits after the side effect; consumer dedupe on (doc_id, sha256); three attempts, then the
dead-letter topic.

**Alternatives rejected.** Exactly-once transactions (more moving parts for no gain once writes are
idempotent); auto-commit (can drop a message that failed after its offset was committed).

**Consequences.** Duplicates are harmless rather than impossible. Counters are keyed by consumer
role, so a replay overwrites the first run's JSON — the first uploader count is kept in the run log.

**Evidence.** 266 published; indexer processed 266, DLQ 0; full replay → 266 duplicates suppressed,
0 re-uploaded (`results/text/10c_kafka.json`); `tests/test_kafka.py` replays a message twice and
indexes it once.

**Revisit if.** Several producers write the same document (would need a schema registry and keys
agreed per topic).
