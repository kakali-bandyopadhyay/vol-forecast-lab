# ADR 0008 — Drift monitoring: trailing-window PSI plus a QLIKE-ratio rule

**Status:** Accepted, PSI limit reported · **Date:** 2026-11-17 (Issue 27, before any drift code ran)

**Context.** Step 12 specified monthly PSI of the four HAR inputs against training deciles. A null
simulation showed calendar-month samples (~21 sessions) flag drift in 88% of months with no drift at
all (median PSI 1.42); 126 sessions bring that to ~0%.

**Decision.** PSI computed at each month-end on the trailing 126 sessions (deciles, ε = 1e-6 and the
0.25 threshold unchanged). Model degradation = rolling 63-session QLIKE ratio M2/M1 > 1.10 at three
consecutive month-ends. Logged as a deviation from the Step 12 wording.

**Alternatives rejected.** Calendar-month PSI (alarm every month); tuning the PSI threshold after
seeing results.

**Consequences.** The QLIKE rule is informative: 11 of 79 month-ends degraded (2020-06…09, 2021-03,
2021-07…10, 2024-10, 2026-05); latest month not degraded. PSI is not: inputs flagged at 40–78 of 78
month-ends, and 126-session windows inside the training period also exceed 0.25 in 46–100% of cases.
For persistent regressors this PSI rule cannot separate drift from regime variation — reported as a
limit, not tuned.

**Evidence.** `results/drift_report.parquet`, `drift_summary.json`; the kind CronJob run reproduced the
same 11 months (`docs/step13_evidence.md`).

**Revisit if.** A block-bootstrap null threshold is fixed in advance and tested on later data.
