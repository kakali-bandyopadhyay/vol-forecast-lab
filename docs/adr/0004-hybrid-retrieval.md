# ADR 0004 — Hybrid retrieval with a contextual header

**Status:** Accepted at Step 10b; revised at 10c-4 (keyword side moved to OpenSearch BM25)

**Context.** FOMC questions turn on exact terms (named dissenters, "target range") and on meeting
dates, while chunk text carries no date. Embeddings blur exact terms.

**Decision.** Index each chunk with a contextual header (document type + meeting date) and fuse a dense
ranking (text-embedding-3-small in Qdrant) with a keyword ranking by reciprocal rank fusion (k = 60).
At 10c-4 the keyword side became OpenSearch BM25 (English analyzer, `header^2`).

**Alternatives rejected.** Dense only (misses exact terms); a cross-encoder reranker (extra model and
latency before the basics were measured).

**Consequences.** Step 10b's hybrid (Qdrant keyword vector) lost to dense: recall@5 0.322 vs 0.389.
With BM25 the fused mode leads: recall@5 0.589, hit@5 0.667, MRR@10 0.402 vs dense 0.389 / 0.467 /
0.286, on the same 15 frozen questions. Statement questions stay weak (fused hit@5 0.375), mostly
because gold sets were labelled within one document type.

**Evidence.** `results/text/retrieval_eval.parquet`, `retrieval_eval_10c.parquet`,
`10c_bm25_ablation.json`. n = 15, so one question ≈ 6.7 pp of hit@5: directional, not a benchmark.

**Revisit if.** A new eval set labels every answering chunk across document types (committed before it
is run).
