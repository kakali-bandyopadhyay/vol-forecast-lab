# ADR 0009 — Airflow DAG with an audit gate; local Kubernetes (kind)

**Status:** Accepted · **Date:** 2026-11-17 (Step 13)

**Context.** The pipeline must run end to end in a fixed order, stop on bad data, and show how the
read-only API and the monthly drift report would be deployed — without a cloud cluster.

**Decision.** Airflow 3.4 DAG `vol_pipeline`: `audit_snapshot → build_rv → refit_models → evaluate →
q2_events → drift_report`, plus the document branch `fetch_docs → extract → chunk → embed_index`.
`schedule=None`, no catch-up (a pre-registered study is run deliberately, not on a timer); retries 1;
a failed audit marks every downstream task `upstream_failed`. Fixture by default; `use_snapshot` param
validated by regex and quoted. Secrets via Airflow Variables. kind cluster: API Deployment + NodePort
Service with readiness and liveness probes and resource limits; drift CronJob with
`concurrencyPolicy: Forbid`, `backoffLimit: 2`; ConfigMap for paths and thresholds; pods run as
uid 10001, `runAsNonRoot`, no privilege escalation; no secrets in manifests.

**Alternatives rejected.** cron + scripts (no dependency graph or failure states); a managed cloud
cluster (cost, and not needed to prove the manifests); `runAsNonRoot` with a named user only (the
kubelet cannot verify a name is non-root).

**Consequences.** Local orchestration and a local cluster, not a production deployment. The full
snapshot DAG run (`use_snapshot=true`) has not been executed; the drift job inside the image reports
`code_commit: unknown` because the image carries no `.git`.

**Evidence.** DAG tests 4 passed (forced audit failure → 5 downstream `upstream_failed`; document
stubs ran); kubeconform 4/4 valid against Kubernetes 1.37.0 (also in CI); `/health` 200 in kind;
`id` in the pod → uid 10001; drift job Complete (`docs/step13_evidence.md`).

**Revisit if.** The services move to a managed cluster: add network policies, image signing and a
secrets manager.
