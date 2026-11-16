# From central-bank documents to governed hybrid search: an evidence-gated data and AI platform

**Tags:** Data · AI · Architecture case study
**Status (16 Nov 2026):** Steps 0–10b complete and measured. Step 10c platform slice designed, plumbed and tested offline; the AWS evidence run is pending — the results table below is filled from the run's output files, nothing estimated.
**Code:** vol-forecast-lab (private until release) · **Diagrams:** `figures/diagrams/platform.svg`, `step10b.svg`, `step10c.svg`

---

## The problem

Rates and volatility desks read every FOMC statement and set of minutes, but the text sits in PDFs and HTML pages that no system can query by meeting, by date or by who dissented. I wanted one pipeline that turns those documents into searchable, auditable data — and that I could prove behaves the same way every time it runs, at a known cost.

The same repository holds a pre-registered realised-volatility forecasting study (HAR-X vs HAR, Clark–West test). The document pipeline is designed so that FOMC text can later feed those models as event features.

## What I built

1. **Unstructured-data pipeline (Step 10b, done).** Fetch → extract → chunk → data-quality checks → index → evaluate, each stage writing Parquet with a fixed schema.
   - Corpus: **134 statements and 132 minutes = 266 documents → 6,620 chunks, 1.50M tokens.**
   - Index: Qdrant with dense embeddings (text-embedding-3-small) plus a keyword vector with server-side IDF, fused with reciprocal rank fusion (RRF). Each chunk carries a contextual header (document type + meeting date).
   - Idempotent: a full re-run upserts **6,620, then 0**.
2. **Platform slice (Step 10c, built and tested offline; AWS run pending).**
   - **Kafka** topics `docs.raw`, `docs.clean`, `docs.dlq`: idempotent producer (`acks=all`), manual commits, three attempts then dead-letter queue.
   - **AWS serverless lake** via Terraform: S3 `raw/ → clean/ → curated/`, an extraction Lambda shipped as a container image from ECR, Glue table with partition projection, Athena workgroup with a 100 MB scan cutoff.
   - **PySpark** compaction and statistics, checked against pandas.
   - **OpenSearch** BM25 fused with Qdrant dense results (RRF, k = 60), rerun on the same frozen questions.
   - **Redis** search cache and per-client token-bucket rate limiter behind the API.
3. **Runtime and delivery.** FastAPI service in Docker (`/search`, `/forecast`, `/compare`, `/health`, `/metrics`); Airflow 3.4 for orchestration; a local Kubernetes (kind) cluster; CI running tests on Python 3.12 and 3.14, Terraform validate, Docker build, DAG import and manifest validation. Every image and package version is pinned.

## Architecture

![Platform architecture](../figures/diagrams/platform.svg)

Documents enter once, are published as events, land raw in S3, are extracted by the Lambda into a typed clean layer, are queryable in Athena, compacted by Spark, and indexed into two search engines whose results are fused before the API serves them. Every hop is idempotent and every layer is checked against the one before it.

## Eight decisions and why

| # | Decision | Alternative rejected | Why |
|---|---|---|---|
| 1 | Hybrid retrieval (dense + keyword, RRF) with a contextual header | Dense only | Central-bank text turns on exact terms ("dissent", named members) that embeddings blur |
| 2 | Evaluation questions written and labels frozen **before** any embedding | Tune questions after seeing results | Stops me grading my own homework; post-hoc diagnostics are labelled as such |
| 3 | Kafka with idempotent producer, manual commit, DLQ; at-least-once delivery plus consumer dedupe on (doc_id, sha256) | Exactly-once transactions | Simpler to operate; duplicates are made harmless instead of impossible |
| 4 | Lambda as a container image | Zip + layers | PyMuPDF and pyarrow exceed the 250 MB unzipped limit |
| 5 | Athena partition projection | Glue crawlers | No crawler runs, no schema drift, predictable partitions |
| 6 | Paragraph arrays stored in the lake + a chunk-parity gate | Re-chunk from flat text | Guarantees the cloud path produces the same 6,620 chunks as the local path |
| 7 | Clean-layer publisher reads S3 metadata and a checkpoint | Read every Parquet file | Cheaper, restart-safe, no extra IAM read on content |
| 8 | Budget stack applied separately and never destroyed; same-day teardown of everything else | One Terraform root | The cost alarm must outlive the infrastructure it watches |

## Controls built in

| Requirement | Control | Evidence |
|---|---|---|
| Cost | USD 10 budget with 50/80/100% alerts; Athena 100 MB scan cutoff; destroy the same day | budget plan; `10c_athena.json` bytes scanned; `10c_destroy.txt` |
| Security | Lambda role limited to reading `raw/*`, writing `clean/*` and its own log group; build user scoped to one ECR repository; ECR scan on push; no secrets in git (history check in every commit routine) | Terraform plan; IAM policy files |
| Reliability | Idempotent writes at every hop; retries then DLQ; replay test proves a message delivered twice is indexed once | `tests/test_kafka.py`; `10c_kafka.json` counters |
| Data quality | DQ flags per document; lake text must equal the local text for every document; chunk parity row for row | DQ report; `10c_evidence.json` |
| Reproducibility | Pinned data snapshot, model snapshot, package and image versions; CI on every push | CI history |

## Results (measured)

**Step 10b retrieval — 15 pre-registered questions, no filters**

| Mode | Recall@5 | MRR@10 | Statements recall@5 | Minutes recall@5 |
|---|---|---|---|---|
| Dense | 0.389 | 0.286 | 0.250 | 0.548 |
| Hybrid (RRF) | 0.322 | 0.244 | 0.125 | 0.548 |
| Keyword | 0.122 | 0.130 | 0.000 | 0.262 |

**Step 10c platform — pending AWS run** (filled from output files only)

| Check | Result |
|---|---|
| Kafka processed / duplicates suppressed / DLQ | _pending_ |
| Lambda invocations / errors (target 266 / 0) | _pending_ |
| Athena counts (target 134 statements / 132 minutes); bytes scanned and cost | _pending_ |
| Text parity and chunk parity | _pending_ |
| Recall@5: OpenSearch BM25 / dense / fused | _pending_ |
| Cache hit rate | _pending_ |

## What failed, and why

- **Hybrid did not beat dense.** On statement questions hybrid scored 0.125 against dense 0.250. A post-hoc check (labelled, gold labels unchanged) found a same-meeting chunk naming the answer in the hybrid top five for **6 of 8** statement questions (dense 5 of 8). The gold sets were labelled within one document type, while the same fact often appears in both the statement and the minutes. Lesson: label gold sets across document types.
- **A per-document index call would have erased the index.** The indexing function deletes points it no longer sees, which is correct for a full batch and destructive when a consumer calls it one document at a time. Caught in review before any run; fixed with an explicit flag and a two-document test.
- **Test doubles that lie.** A fake S3 returned a field the real listing API never returns, so a checkpoint test passed against behaviour production would not have. Fixed by making fakes match the real API.
- **Known data gap.** The 24 Oct 2012 minutes exist only as HTML in the source; recorded as a known gap rather than patched.

## What changes at bank scale

| Here | In a bank |
|---|---|
| Local Kafka | Managed Kafka (MSK) with a schema registry and data contracts per topic |
| Local Spark container | Glue jobs or EMR with job bookmarks |
| Local OpenSearch, Redis | OpenSearch Service, ElastiCache, both in private subnets |
| IAM user with inline policies | SSO roles, permission boundaries, Lake Formation column-level access |
| Public documents | PII classification and masking at the clean layer; retention rules |
| Frozen eval set | Model-risk review: versioned eval sets, approval before a retrieval change ships, drift monitoring |

## Who did what

Design, decisions, author code (extraction, chunking, indexing, Kafka producer and consumers, evaluation) and every review: me. Infrastructure scaffolding and test harnesses were generated with AI coding assistants from written briefs and reviewed line by line before commit.

## Next

Run the 10c AWS evidence and fill the table above; research agent with tracing and a hard cost cap; MLOps tracking; Airflow DAG and Kubernetes deployment; public release.
