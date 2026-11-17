# Architecture decision records — vol-forecast-lab

Each record states the context, the decision, the alternatives rejected, the consequences (including
what it cost), the evidence that the decision held, and what would make us revisit it. Decisions are
the author's; the records were written up on 17 Nov 2026 from `BUILD_GUIDE.md`, `prereg/DEVIATIONS.md`
and `FINDINGS.md`. Numbers come from committed result files only.

| # | Decision | Status |
|---|---|---|
| [0001](0001-preregister-before-results.md) | Pre-register hypotheses, tests and eval sets before any result | Accepted |
| [0002](0002-forecast-evaluation-protocol.md) | Clark–West for the nested Q1 test; QLIKE and MSE both reported; one Holm family | Accepted |
| [0003](0003-snapshot-and-licence-boundary.md) | Hash-pinned data snapshot; no row-level market data published | Accepted |
| [0004](0004-hybrid-retrieval.md) | Hybrid retrieval (dense + BM25, RRF) with a contextual header | Accepted (revised at 10c-4) |
| [0005](0005-kafka-at-least-once.md) | Kafka at-least-once with consumer dedupe and a dead-letter topic | Accepted |
| [0006](0006-serverless-lake-and-cost-controls.md) | Serverless AWS lake, budget stack kept separate, same-day destroy | Accepted |
| [0007](0007-agent-copy-only-verifier.md) | Agent reads results only; a copy-only numeric verifier gates every answer | Accepted, 2 known limits |
| [0008](0008-drift-monitoring.md) | Drift = trailing-window PSI plus a QLIKE-ratio degradation rule | Accepted, PSI limit reported |
| [0009](0009-orchestration-airflow-kind.md) | Airflow DAG with an audit gate; local Kubernetes (kind) with hardened pods | Accepted |
| [0010](0010-cache-and-rate-limit.md) | Redis search cache and token-bucket limiter, failing open | Accepted |
