# ADR 0006 — Serverless AWS lake with cost controls that outlive it

**Status:** Accepted · **Date:** 2026-11-14 (author decisions K1a, K2a); run and destroyed 2026-11-16

**Context.** The cloud path must reproduce the local pipeline, cost almost nothing, and leave nothing
running.

**Decision.** Terraform: S3 `raw/ → clean/ → curated/`; extraction Lambda shipped as a container image
from ECR (PyMuPDF + pyarrow exceed the zip limit); Glue table with partition projection (no crawlers);
Athena workgroup with a 100 MB scan cutoff. A separate budget root (USD 10/month, alerts at 50/80/100%,
`prevent_destroy`) is applied first and never destroyed; the data-lake root is destroyed the same day.
Lambda role limited to reading `raw/*`, writing `clean/*` and its own log group.

**Alternatives rejected.** Zip + layers (size limit); Glue crawlers (schema drift, extra runs); one
Terraform root (destroying the lake would destroy the alarm).

**Consequences.** Lambda memory 1,024 MB instead of the specified 512 (recorded deviation). Destroy
needed a policy fix (Glue user-defined functions) and a second run; both runs are kept.

**Evidence.** Lambda 266 invocations / 0 errors; lake text parity 266/266; chunk parity 6,620 = 6,620;
Athena ≤ 20,748 bytes per query (billed at the 10 MB minimum); `terraform state list` empty after
destroy (`results/text/10c_evidence.json`, `10c_athena.json`, `10c_destroy.txt`).

**Revisit if.** The lake must run continuously — then SSO roles, permission boundaries and Lake
Formation replace the build user (see the case study's "at bank scale" table).
