# ADR 0003 — Hash-pinned snapshot and the licence boundary

**Status:** Accepted · **Date:** Step 1

**Context.** Results must be reproducible from the same inputs, and the price data come from a vendor
licence that does not allow redistribution of row-level data.

**Decision.** Every pull is written to a snapshot with a SHA-256 manifest; the audit task checks it
before anything else runs; the snapshot ID is stamped into every results table and the drift report.
Raw and snapshot folders are git-ignored, and so are per-date result series (forecasts, losses, HAR inputs; removed from history on 17 Nov 2026, `docs/history_rewrite.md`). Public outputs (FINDINGS, dashboard) carry statistics and
aggregates only — never price, return, realised-variance or forecast series.

**Second rewrite (17 Nov 2026).** The 67 ex-dividend-day rows remain in commits before this release
so tag `v0.1-prereg` is never moved (PREREGISTRATION §12). Their retention is disclosed here; those
rows have been removed from the current tree. `results/drift_report.parquet` and figure 2 (monthly
QLIKE ratio and PSI) remain as aggregates that cannot be inverted to prices or VIX.

**Alternatives rejected.** Fetch-on-run (results drift with vendor revisions); committing the data
(licence breach).

**Consequences.** A clean clone reproduces the pipeline on the synthetic fixture, not the headline
numbers; reproducing those needs the user's own data key and the same snapshot date. Stated in the
README boundary.

**Evidence.** `data/snapshot_manifest.json`, `data/audit.json`; `snapshot_id` 20261112T071104Z in the
drift report; dashboard build test that fails on series-like keys.

**Revisit if.** The study moves to a data source whose licence allows redistribution.
