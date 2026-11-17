# ADR 0002 — Forecast evaluation protocol

**Status:** Accepted · **Date:** 2026-11-12 (frozen in the pre-registration), clarified 2026-11-14

**Context.** Q1 asks whether VIX-implied variance adds information beyond HAR at the 30-day horizon.
HAR-X (M2) nests HAR (M1), so a plain Diebold–Mariano test on MSE is biased against the larger model.
Variance losses also disagree: MSE is dominated by a few crash dates, QLIKE is not.

**Decision.** Expanding walk-forward with no target overlap across the train/forecast boundary; refit
every 21 sessions. Q1 = one-sided Clark–West, HAC lag 21. Report QLIKE and MSE side by side.
Diebold–Mariano on QLIKE for the other comparisons, with Holm applied once across the four §5.5 tests
after Step 10. Mincer–Zarnowitz for calibration. A planted-leak check on the real panel.

**Alternatives rejected.** DM on MSE for nested models; choosing one loss after seeing both; Holm per
step (would have let a raw p = 0.027 stand as "rejected").

**Consequences.** The headline is narrow and defensible: Clark–West rejects outside Feb–Apr 2020
(t = 3.14, p = 0.0009, n = 1,632) and does not on the full sample (t = 1.557, p = 0.0597). After the
final Holm family no DM comparison is detected; M1 vs M0 moved from provisional "rejected" to "not
detected" (raw 0.027 → Holm 0.107).

**Evidence.** `results/tests.parquet`, `tests_ex_window.parquet`, `holm_final.parquet`,
`eval_manifest.json`; planted leak (RV_{t+1} added) cuts QLIKE by 10.5%, so the harness detects a
one-session leak.

**Revisit if.** Intraday data allow a realised-kernel target, or a different horizon is pre-registered.
