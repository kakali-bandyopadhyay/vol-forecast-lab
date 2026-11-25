# Evidence register — vol-forecast-lab

Every public claim, the file that holds it, and how to check it. Numbers are copied from committed
result files and `FINDINGS.md`; where they differ, the result file wins. "Measured" = produced by a
committed script or recorded run; "Boundary" = deliberately not claimed.

Market-data results need your own data access and snapshot `20261112T071104Z`; everything else runs
from the repo (AWS rows needed a live account on 16 Nov 2026 and cannot be re-run without one).

## 1. Research study (pre-registered, tag `v0.1-prereg`)

| # | Claim | Value | Evidence file | Reproduce / check | Commit |
|---|---|---|---|---|---|
| R1 | Test set | 1,703 forecasts, 82 refit blocks, 2020-01-02 → 2026-10-12 | `results/eval_manifest.json` | `python scripts/run_eval.py` | `7775f9d` / `2b5358f` |
| R2 | Q1 headline: VIX-implied variance adds information beyond HAR outside Feb–Apr 2020 | Clark–West t = 3.14, one-sided p = 0.0009, n = 1,632 | `results/tests_ex_window.parquet` | `run_eval.py`; window rule in `prereg/DEVIATIONS.md` (`5c718a1`) | `7775f9d` / `2b5358f`; `76ddb6d` / `11f25f8` |
| R3 | Q1 full sample: not detected | t = 1.557, p = 0.0597 | `results/tests.parquet` | as R2 | `7775f9d` / `2b5358f` |
| R4 | Spring 2020 dominates MSE | 71 forecasts (4%) carry 92% of HAR's squared error and 91% of HAR-X's | `results/loss_summary.json`, `results/losses.parquet` | `run_eval.py`; window rule in `prereg/DEVIATIONS.md` | `76ddb6d` / `11f25f8` |
| R5 | Harness detects a one-session leak | RV_{t+1} in M1 cuts QLIKE by 10.5% | FINDINGS Step 7 (leaky forecasts not written) | `planted_leak_drop()` in `scripts/run_eval.py` (manifest field `planted_leak_qlike_drop`); synthetic test with shuffled placebo in `tests/test_stats.py` | `7775f9d` / `2b5358f` |
| R6 | No DM comparison detected after the final Holm family | Holm p: M2 v M1 1.000, M4 v M2 1.000, M1 v M3 1.000, M1 v M0 0.107 (full) | `results/holm_final.parquet` | `python scripts/run_holm.py` | `a79e370` / `9127253` |
| R7 | GARCH(1,1) all folds converged | 82/82, max α+β = 0.974 | `results/garch_fits.parquet` | `run_eval.py` | `5948af1` / `d622b82` |
| R8 | MLP integrity: shuffled-target check passed | 0.785 vs M0 0.509 (mean QLIKE) | `results/mlp_tests.parquet` | `python scripts/run_mlp.py` | `a79e370` / `92b8154` / `92498f0` / `9127253` |
| R9 | Q2: implied variance above the HAR forecast on average | ex-ante spread mean 1.096 × 10⁻³, HAC t = 7.80 (full) | `results/q2_summary.parquet` | `python scripts/run_q2_q3.py` | `76ddb6d` / `11f25f8` |
| R10 | Q2: ex-post premium positive outside spring 2020 | mean 1.207 × 10⁻³, t = 7.05 (ex-window); full t = 1.06, not significant | `results/q2_summary.parquet` | as R9 | `76ddb6d` / `11f25f8` |
| R11 | Q3: CPI overnight variance higher | +37% (95% CI +4% to +80%), Holm p = 0.033 | `results/q3_events.parquet` | as R9 | `76ddb6d` / `11f25f8` |
| R12 | Q3: FOMC session variance higher | +84% (+30% to +160%), Holm p = 0.001 | `results/q3_events.parquet` | as R9 | `76ddb6d` / `11f25f8` |

## 2. Document pipeline and platform

| # | Claim | Value | Evidence file | Reproduce / check | Commit |
|---|---|---|---|---|---|
| P1 | Corpus | 134 statements + 132 minutes = 266 docs → 6,620 chunks, 1.50M tokens; 2 DQ flags (1 known gap, 1 not yet released) | `results/text/text_manifest.json`, `dq_flags.parquet` | `python scripts/run_text_pipeline.py` | `f8b2891` |
| P2 | Extraction is deterministic | fresh extraction reproduces `documents.parquet` exactly | FINDINGS 10b | re-run extract, compare hash | `5ca9321` |
| P3 | Index is idempotent | Qdrant 6,620 then 0 upserts; OpenSearch 6,620 created → 6,620 updated | FINDINGS 10b, 10c-4 | second run of the indexer | 10b / `f100594` |
| P4 | Fused BM25 + dense scored higher (n = 15 questions, no test; second design on the same frozen set) | recall@5 / hit@5 / MRR@10: fused 0.589 / 0.667 / 0.402; dense 0.389 / 0.467 / 0.286; BM25 0.367 / 0.400 / 0.288 | `results/text/retrieval_eval_10c.parquet` | `python scripts/run_eval_10c.py` (load `.env`; check `_count` = 6,620 before and after) | `f100594` |
| P5 | Step 10b hybrid lost to dense (reported miss) | recall@5 0.322 vs 0.389 | `results/text/retrieval_eval.parquet` | `run_text_pipeline.py` eval stage | `f8b2891` |
| P6 | Kafka: replay suppresses every duplicate | 266 published; indexer 266, DLQ 0; replay 266 suppressed, 0 re-uploaded | `results/text/10c_kafka.json` | `python scripts/run_kafka_consumers.py`; `tests/test_kafka.py` | `068cdae` |
| P7 | Cloud path equals local path | Lambda 266 / 0 errors; text parity 266/266; chunk parity 6,620 = 6,620 | `results/text/10c_evidence.json` | `python scripts/collect_10c_evidence.py` (live AWS) | `479a395` |
| P8 | Athena cost bounded | ≤ 20,748 bytes per query (billed at the 10 MB minimum); 34 partitions | `results/text/10c_athena.json` | `python scripts/run_athena.py` (live AWS) | `479a395` |
| P9 | Nothing left running | destroy output; `terraform state list` empty | `results/text/10c_destroy.txt` | — | `479a395` |
| P10 | Spark matches pandas | 34/34 groups PASS (counts/dates exact, mean/median ≤ 1e-9) | `results/text/10c_spark_stats.parquet` | `scripts/run_spark.ps1`, `python scripts/check_spark_parity.py` | `a599116` |
| P11 | Cache and limiter work | miss 4,013 ms → hit 37 ms; burst 15 → 10 allowed, 5 × 429; hit_rate 0.917 | FINDINGS 10c-5 | `python scripts/measure_cache_latency.py` | `ab94e8d` |

## 3. Agent, MLOps, orchestration

| # | Claim | Value | Evidence file | Reproduce / check | Commit |
|---|---|---|---|---|---|
| A1 | Agent accuracy (15 frozen questions × 3) | 0.844 (38/45); tool choice 45/45; refusals 6/6 | `results/agent/agent_eval.parquet`, `agent_eval_summary.json` | `python scripts/run_agent_eval.py` (OpenAI key; costs ~USD 0.06) | `87a1523`, `376abe5` |
| A2 | No unverified number reached an answer | 0 of 45 | as A1 | as A1 | `376abe5` |
| A3 | Agent cost and latency | USD 0.064 total; p50 4.1 s, p95 6.9 s | as A1 | as A1 | `376abe5` |
| A4 | Agent v2 accuracy (12 pre-registered questions × 3, frozen `1c9922a`) | 20/36 question-runs; 29/51 turns; routing 46/51; memory follow-ups 9/15; refusals 6/6 | `results/agent_v2/eval_v2_summary.json`, `eval_v2_turns.parquet`, `manual_scores.csv` | `python scripts/run_agent_eval_v2.py --set v2 --repeats 3 --trace` then `python scripts/score_agent_eval_v2.py --final` (OpenAI key, Qdrant; ~USD 0.12) | `71b8152` |
| A5 | Agent v2: no unverified number reached an answer | 0 verifier misses; 18 blocks (2 appropriate, 16 inappropriate – written dates) | as A4 | as A4 | `71b8152` |
| A6 | Agent v2 cost, latency, hops | USD 0.0023 per turn, 0.119 total; p50 9.5 s, p95 15.7 s; max 2 hops (cap 3) | as A4 | as A4 | `71b8152` |
| A7 | v1 15 × 3 regression on v2 (v1 numbers unchanged) | 30/45 vs v1 38/45; tool choice 45/45; drop explained by answer format | `results/agent_v2/regression_summary_run3.json`, `regression_v1_on_v2_run3.parquet` | `python scripts/run_agent_eval_v2.py --set v1-regression --repeats 3 --trace` | `71b8152` |
| A8 | Agent v2 traced with LangSmith | 51 eval traces + 45 regression traces; screenshots | `docs/langsmith/` | open the screenshots / CSV run lists | `71b8152` |
| A9 | Agent v3, Claude Agent SDK port (first turn of each pre-registered question, one scored run) | 2/12 correct; routing 12/12; args 6/12; 6 verifier blocks; 1 guardrail denial; USD 0.02013895 median / 0.25069815 total; p50/p95 13.47/21.26 s | results/agent_v3/eval_v3_summary.json, eval_v3_turns.parquet, v2_first_turn_baseline.json; outage attempt eval_v3_*_run1_qdrant_down.* | Compare summary and per-turn rows with FINDINGS Agent v3; the v2 baseline JSON reports each run separately | 1eece83 |
| M1 | Drift: model degradation rule | 11 of 79 month-ends degraded; latest (2026-10) not | `results/drift_report.parquet`, `drift_summary.json` | `python scripts/run_drift.py` | `e292c9c` |
| M2 | Drift: PSI cannot separate drift from regime variation (reported limit) | 40–78 of 78 month-ends flagged; 46–100% inside training | as M1 | as M1 | `e292c9c` |
| O1 | DAG failure rule works | 4 tests passed; forced audit failure → 5 downstream `upstream_failed` | `tests/test_dag_vol_pipeline.py` | `scripts/run_dag_tests.ps1` | `c849f27` |
| O2 | Manifests valid | kubeconform 4/4 (Kubernetes 1.37.0), also in CI | `docs/step13_evidence.md` | see command in that file | `8cd27ea` |
| O3 | API serves from kind; pod runs non-root | `/health` 200; uid 10001; drift job Complete with the same 11 months as M1 | `docs/step13_evidence.md` | `scripts/kind_up.ps1` | `8cd27ea` |

## 4. Boundaries (not claimed)

- Full snapshot DAG run (`use_snapshot=true`): not executed.
- Production deployment: none; kind is local. The drift job in the image reports `code_commit: unknown`.
- AWS rows (P7–P9) cannot be re-run from a clone without an account; their outputs are committed.
- Evals have 15 questions each: one question ≈ 6.7 pp of hit@5 or accuracy per repeat.
- Agent verifier limits (wrong field accepted; p-value precision not enforced): open, BUILD_GUIDE Issues 25–26.
- No trading signal, threshold or rule is drawn from any result.
