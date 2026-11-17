# ADR 0007 — Research agent with a copy-only numeric verifier

**Status:** Accepted, with two known limits · **Date:** Step 11 (closed 2026-11-17)

**Context.** An LLM that explains forecasts will happily do arithmetic or round numbers, producing
figures that no computation produced.

**Decision.** LangGraph agent whose tools read only committed `results/*.parquet`
(`get_forecast`, `compare_models`, `event_effect`, `search_fomc`). A verifier node rejects any number
not present in a tool output; one retry, then refuse. Advice and out-of-range dates are refused. Model
pinned; per-run cost cap USD 0.50 checked before every LLM call; LangSmith tracing.

**Alternatives rejected.** Free-text answers checked by a second LLM (a judge can also invent);
letting the model compute differences (the A01 smoke produced −0.000659647, a number no tool output
contains — the verifier refused it, which is the point).

**Consequences.** Headline accuracy 0.844 (38/45; 15 questions × 3 repeats), tool choice 45/45,
refusals 6/6, total cost USD 0.064, latency p50 4.1 s / p95 6.9 s. No number absent from a tool output
reached a final answer. Two limits stay open: the verifier checks presence, not field (2 answers gave
`beta` instead of `effect`), and does not enforce p-value precision (Issues 25–26). Fixing them on the
same questions would be tuning on the test set.

**Evidence.** `results/agent/` eval files at `d353ace`; FINDINGS Step 11.

**Revisit if.** A field-aware verifier is built — evaluate it on a new held-out question set.
