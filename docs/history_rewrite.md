# History rewrite — 17 Nov 2026

**Why.** Four files with per-date series derived from licensed price and VIX data had been committed:
`results/forecasts.parquet`, `results/mlp_forecasts.parquet`, `results/har_inputs.parquet`,
`results/losses.parquet` (first in `0097ca6`, old hash). ADR 0003 allows only statistics and aggregates in
public outputs, so before the repository was made public they were removed from every commit with
`git filter-repo --invert-paths` and added to `.gitignore`. They stay on the author's machine; the agent,
`/forecast` and the dashboard build read them there. Charts rebuild from `results/loss_summary.json`.

**What changed.** Only those four paths. Every other file, commit message, author and date is unchanged;
commits before `0097ca6` (including tag `v0.1-prereg` on `8d48051`) keep their hashes. Commits from
`0097ca6` onward have new hashes. References in the docs and result manifests were remapped
mechanically (137 replacements of 49 hashes) from filter-repo's commit map. Older notes outside the repo
that quote an old hash can be translated with the table below.

**Check.** `git log --all --oneline -- results/forecasts.parquet results/mlp_forecasts.parquet results/har_inputs.parquet results/losses.parquet` prints nothing.

## Old → new (78 commits)

| Date | Old | New | Subject |
|---|---|---|---|
| 2026-11-13 | `0097ca6` | `2b5358f` | step 7: results for M0-M2 on the test period (run from the step 7 code commit) |
| 2026-11-14 | `0c35af5` | `92b8154` | step 10: standardise MLP log target (DEVIATIONS S) + regression test |
| 2026-11-14 | `4983d5f` | `9127253` | step 10: M4 test results, shuffled check, final Holm (Q1b-Q1d not detected in either sampl |
| 2026-11-14 | `5008c38` | `d622b82` | step 8: results for M0-M3 on the test period (run from step 8 code commit 5948af1) |
| 2026-11-14 | `5168a0b` | `76ddb6d` | step 9: window rule, Q2 spread/premium, Q3 event study (code + tests) |
| 2026-11-14 | `51d0a97` | `a4134ca` | prereg: Step 10 MLP clarifications and final Holm rule before any M4 run |
| 2026-11-14 | `72ca52d` | `a79e370` | step 10: M4 MLP (mlp.py, run_mlp.py, run_holm.py) + tests; pin torch 2.15.0 |
| 2026-11-14 | `733ebc7` | `f8b7cb6` | FINDINGS: step 10 - Q1c not detected; final Holm (Q1b-Q1d not detected) |
| 2026-11-14 | `89cb078` | `b55013a` | FINDINGS: step 8 - Q1d HAR vs GARCH not detected; M3 calibration rejected; fits summary |
| 2026-11-14 | `9181046` | `53c830e` | prereg: S - standardise MLP log target (fix before any test-period run) |
| 2026-11-14 | `92f9631` | `5c718a1` | prereg: fix Feb-Apr 2020 rule and Step 9 clarifications before any Step 9 run |
| 2026-11-14 | `9eb2155` | `5948af1` | step 8: GARCH(1,1) M3 (arch 8.0.0, constant mean, exclusive cutoff); DM M1 vs M3; fits tab |
| 2026-11-14 | `af44f86` | `03ebfe3` | FINDINGS: step 8 - Q1d HAR vs GARCH not detected; M3 calibration rejected; fits summary |
| 2026-11-14 | `e4377f3` | `11f25f8` | step 9: results (Q2, Q3, Feb-Apr 2020 window check) |
| 2026-11-14 | `e4b833b` | `561f2e6` | FINDINGS: step 9 - window rule, Q2, Q3; Q1 headline ex-window |
| 2026-11-14 | `eb8bb23` | `92498f0` | step 10: M4 selection (h32_l1_wd0.001, validation QLIKE 0.2650) |
| 2026-11-15 | `0a37745` | `1acfb20` | test_tracing: assert no network call instead of sys.modules (langsmith pytest plugin prelo |
| 2026-11-15 | `132c7a4` | `be26bd8` | FINDINGS + BUILD_GUIDE: Step 10b results (retrieval eval as measured, post-hoc diagnostic  |
| 2026-11-15 | `2023ccc` | `2f30741` | P6a: Athena query runner (Codex plumbing, reviewed) |
| 2026-11-15 | `2035035` | `d0dab81` | step 10b: documents, chunks, DQ flags (before any embedding) |
| 2026-11-15 | `230c943` | `25fb8e6` | step 10b: documents/chunks rebuilt with extractor 1.2 |
| 2026-11-15 | `243dabb` | `e01ca29` | step 10b: retrieval eval set - 15 questions labelled before any embedding |
| 2026-11-15 | `2de474b` | `f1aa4a5` | P6c: architecture diagrams (Codex plumbing, reviewed) |
| 2026-11-15 | `2e3842d` | `42e2f84` | step 10b: extractor 1.1 - line-per-block PDFs, cross-break joins, attendance lists (Claude |
| 2026-11-15 | `2f87c6a` | `b87a70c` | step 10b: batch qdrant upserts and guard eval index |
| 2026-11-15 | `303e97d` | `d98d0bf` | step 10b: DQ rerun on committed code (vote_tally null fix) |
| 2026-11-15 | `31ec42c` | `80833fc` | P4c: Airflow 3.4 compose (separate vfl venv), /forecast + /compare routes, LangSmith setup |
| 2026-11-15 | `4173e3a` | `aaea10a` | api: map search/statement errors to 422/404 (plumbing) |
| 2026-11-15 | `470755c` | `f8b2891` | step 10b: retrieval eval results (pre-registered 15 questions, as measured) |
| 2026-11-15 | `4ed7a67` | `5ca9321` | step 10b: extractor 1.2 - fractions as 3-1/2, line-end hyphens resolved per document (Clau |
| 2026-11-15 | `50c0354` | `5cb5802` | P3: platform plumbing (Codex) + review fixes |
| 2026-11-15 | `53bc742` | `8fb4144` | 10c plumbing: D1 Glue columns, D2 image Lambda + ECR, data/lake ignored (Claude plumbing,  |
| 2026-11-15 | `5637533` | `dd74fcd` | test: column-order markers use body prose (attendee lines dropped in extractor 1.1) |
| 2026-11-15 | `5ce98b1` | `5eef5fc` | step 10b: documents/chunks rebuilt with extractor 1.1 |
| 2026-11-15 | `69af733` | `f3631fc` | fetch_docs: retry minutes 404s inside the release window |
| 2026-11-15 | `7354c88` | `7f946bc` | step 10b: extract, chunk, index, dq, pipeline (author) |
| 2026-11-15 | `83b95c6` | `74cf7f6` | Step 12 plumbing: MLflow tracking helper + tests (Claude plumbing, reviewed) |
| 2026-11-15 | `85cf854` | `2530e42` | step 10b: contextual header (doc type + meeting date) in index inputs |
| 2026-11-15 | `d6919e3` | `db02eaf` | P4b: API Dockerfile (+results), CI lint/smoke/docker-build/dag-import/k8s jobs (Codex plum |
| 2026-11-15 | `f573e43` | `514a6a4` | BUILD_GUIDE: P3 hash in Issues rows 1-4 |
| 2026-11-15 | `f7b4365` | `f903c4f` | P6b: kind scripts + kubeconform CI (Codex plumbing, reviewed) |
| 2026-11-15 | `fed4844` | `30a8aca` | P4a: synthetic SPY/VIX fixture (2010-2020, seeded) + smoke run (Codex plumbing, reviewed) |
| 2026-11-16 | `13a0922` | `ae66164` | 10c-5: Redis search cache + token-bucket rate limiter; live hit 37 ms, burst 10 ok / 5x429 |
| 2026-11-16 | `3801a2f` | `8da6e3e` | Step 11 eval set (15 q, reviewed) + Step 12 plumbing: stamp_snapshot, export_har_inputs (C |
| 2026-11-16 | `52ed79a` | `fdbd20d` | P9a: evidence dashboard (Codex, reviewed) + case study with 10c-2 results |
| 2026-11-16 | `5a76abc` | `28cfb9b` | 10c-2: Lambda handler + Athena SQL (2020 statements = 7 in corpus) + handler tests + image |
| 2026-11-16 | `66627a9` | `e2732e4` | test_collect_10c_evidence: drop import-time tokenizer stub (it leaked into chunk for the w |
| 2026-11-16 | `7e619c5` | `b58ae79` | BUILD_GUIDE: Issues 12–21 (P8 fixes) |
| 2026-11-16 | `92bb952` | `85a9862` | build_push: ECR login via cmd pipe; build without provenance/SBOM so Lambda accepts the im |
| 2026-11-16 | `9a5c120` | `1fd0e80` | test_kafka: scroll limit so stale-chunk test sees all points (Claude) |
| 2026-11-16 | `9adbf4a` | `59ff3f6` | 10c-4: OpenSearch BM25 index + RRF fusion; fused recall@5 0.589 vs 0.322 (10b hybrid) |
| 2026-11-16 | `c381703` | `6e5282d` | 10c-3: Spark compaction + stats (Codex P7j, reviewed) with 34/34 pandas parity |
| 2026-11-16 | `c994477` | `fecdcb1` | test_kafka: real file hash in uploader duplicate test; tokenizer-independent stale-chunk a |
| 2026-11-16 | `d0e9aba` | `e0d8d94` | 10c follow-ups (Codex P8c-P8g): DAG test runner, dev requirements, test isolation, cache v |
| 2026-11-16 | `d10a8d8` | `76f921b` | 10c plumbing: evidence collector + 10c eval runner (Codex, reviewed) |
| 2026-11-16 | `d34bbcc` | `e390ebd` | 10c plumbing: Kafka consumer runner; evidence collector reads Hive partitions and checks A |
| 2026-11-16 | `e5a22b4` | `d3876cc` | 10c-2 evidence: Lambda 266/0 errors, text + chunk parity PASS, Athena 134/132 (2020=7), Ka |
| 2026-11-16 | `f52709c` | `068cdae` | 10c-1: Kafka producer/consumers + idempotency (author; review fixes by Codex, reviewed) |
| 2026-11-16 | `f52b53e` | `344f724` | P9a: static evidence dashboard + case study draft (Codex dashboard, reviewed; case study d |
| 2026-11-17 | `062ec7c` | `2e2ae8e` | Step 11 fixes: DraftAnswer strict schema (Issue 23), run plumbing (Issue 22), schema test |
| 2026-11-17 | `0da8854` | `6d0ede9` | Step 13: k8s manifests - vfl-api Deployment + NodePort 30080, vfl-drift monthly CronJob (F |
| 2026-11-17 | `133b944` | `3c5f466` | Step 11: agent eval results (d353ace run) + FINDINGS |
| 2026-11-17 | `1ee37b6` | `ef9e8b9` | Step 12: snapshot_id stamped on results tables (stamp_snapshot; original columns unchanged |
| 2026-11-17 | `29d84b8` | `cde8289` | BUILD_GUIDE: Step 10c commit hashes |
| 2026-11-17 | `2aa9a7f` | `bc38e03` | FINDINGS: Step 11 eval results paragraph |
| 2026-11-17 | `2b6b011` | `2fc8c30` | Step 11: agent (route/tool/draft/verify graph), deterministic verifier, eval runner |
| 2026-11-17 | `3a80cb6` | `cc60e24` | Step 11 close: per-repeat results, Issues 22-26, BUILD_GUIDE hashes |
| 2026-11-17 | `42034aa` | `c924dde` | P9: ADRs 0001-0010, ARCHITECTURE, EVIDENCE register (31 claims), deployment diagram |
| 2026-11-17 | `79fc10a` | `6593a05` | BUILD_GUIDE: Issue 28 (CI collection error from airflow/ folder shadowing) |
| 2026-11-17 | `7d93ea4` | `39a65cd` | BUILD_GUIDE: Step 12 commit hashes, Who (actual), Issue 27 hash |
| 2026-11-17 | `8077aa5` | `ee6fa31` | CI: importorskip the real Airflow submodule (repo airflow/ folder shadowed the top-level n |
| 2026-11-17 | `9ea3f71` | `011163b` | Step 12: drift monitoring (PSI + QLIKE ratio), run_drift, report; 340 passed |
| 2026-11-17 | `a923d9b` | `3028994` | Step 12: MLflow logging in run_eval/run_mlp (logging only, rerun frame-identical); export_ |
| 2026-11-17 | `c4ec305` | `ebb9ef7` | Step 13: vol_pipeline DAG (manual, branch smoke/snapshot, 6 analysis + 4 document tasks, r |
| 2026-11-17 | `c830177` | `0574a7a` | Step 13: kind evidence (health 200, drift CronJob job, non-root uid 10001), kind_up rollou |
| 2026-11-17 | `dfa36f4` | `01350f9` | Step 13 plumbing: Airflow task env (VFL_MLFLOW=0, git safe.directory); DAG tests on Airflo |
| 2026-11-17 | `eb68dd2` | `d353ace` | Step 11: log last_rejection; draft prompt forbids model arithmetic (A01 smoke) |
| 2026-11-17 | `f75de27` | `b8ec6be` | CI: skip DAG tests when Airflow is not installed (python-tests jobs) |
