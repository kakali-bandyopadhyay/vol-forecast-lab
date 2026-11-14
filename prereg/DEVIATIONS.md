# Deviations log - vol-forecast-lab

Every departure from PREREGISTRATION.md, dated, with what it cost the claim.

A deviation log that stays empty is more likely to indicate under-reporting than a clean run.

## Before freeze

## After freeze

- 2026-11-14 — Clarification (spec silent), not a departure. §4 M3 and BUILD_GUIDE Step 8 do not name a mean model. Fixed before any M3 run: `arch` constant mean (package default); the forecast is the sum of the N_t step-ahead conditional variances ÷ 10,000, with no μ² term added. Cost to the claim: none; chosen with no test-period M3 output in view.
- 2026-11-14 — Clarification (spec silent), not a departure. §8 "Any result driven by Feb–Apr 2020 alone" does not define "driven by" or "without that window". Fixed before any Step 9 code is written and before any ex-window number is computed:
  1. Window W = 2020-02-15 → 2020-04-30 (§3.5).
  2. Results built on a 30-day target (Q1 Clark–West, Q1b/Q1d Diebold–Mariano, Mincer–Zarnowitz, Q2 ex-post premium) drop every forecast date t that lies in W or whose target sessions (t, t + 30 calendar days] include a session in W: t = 2020-01-21 → 2020-04-30, 71 of 1,703 forecasts; 1,632 remain. The Q2 ex-ante spread uses the same 1,632 dates, so spread and premium stay on identical rows. Daily results without a 30-day target (Q3 §7.3 and §7.4) drop the 52 sessions in W, as §7.3 already lists. Models are not re-estimated; only evaluation rows are removed; the remaining rows are concatenated and the HAC lag recomputed by the same rule (§5.5 → 21; §7.3 → 5).
  3. Decision per result: Clark–West one-sided rejection at 0.05; Diebold–Mariano two-sided rejection at 0.05 and its sign; Mincer–Zarnowitz rejection at 0.05; Q2 sign of the mean and two-sided HAC rejection at 0.05; Q3 Holm-adjusted rejection at 0.05 and its sign.
  4. Reading. Same decision in both samples → headline the full sample, ex-window shown beside it. Different decision → headline the ex-window result, full sample shown beside it, and the difference stated. The rule is symmetric: it applies whether W creates or masks a rejection, including when it changes the Q1 decision.
  5. Holm-family tests (Q1b–Q1d) are classified on raw p-values at Step 9 and that classification is provisional. The final classification is made after Step 10, with Holm applied separately within each sample over the same four §5.5 tests.
  6. Applies to the Step 7 (Q1, Q1b) and Step 8 (Q1d) results already reported; their full-sample numbers do not change.
  Cost to the claim: none on the full-sample numbers. The ex-window view is a pre-committed robustness reading, not an additional test in any Holm family. Written after the full-sample Q1 result (Clark–West p = 0.0597) was known and before any ex-window number was computed.
- 2026-11-14 — Clarification (spec silent), not a departure. §6 Q2 sample: the test-period forecast dates (1,703, 2020-01-02 → 2026-10-12) for both the ex-ante spread S_t and the ex-post premium P_t, so the two are on identical dates. S_t uses the raw M1 totals (§3.4 floors only what goes to QLIKE; M1 floored 0). Units: 30-day total variance, decimal. Cost to the claim: none.
- 2026-11-14 — Clarification (spec silent), not a departure. §7.1/§7.3 warm-up: log RV_m,t−1 needs 22 sessions, so the first estimation session is 2010-02-04 (panel index 22) and n = 4,219. This drops CPI 2010-01-15 and FOMC 2010-01-27; regression counts are CPI 199, FOMC 133, dual 10, emergency 5. The §7.1 counts (200 / 134) are calendar counts and stay as written. Cost to the claim: one CPI and one FOMC day fewer in the estimates.
- 2026-11-14 — Clarification (spec silent), not a departure. §7.3 c_k = 1% of the median of x_k over the estimation rows of the primary run (n = 4,219), fixed once per component and reused in every robustness run; the GK robustness run gets its own c on the same rows. Cost to the claim: none.
- 2026-11-14 — Clarification (spec silent), not a departure. §7.3/§7.4 weekday dummies: Tuesday–Friday, with Monday as the reference category. Cost to the claim: none; the weekday coefficients are read relative to Monday.
- 2026-11-14 — Clarification (spec silent), not a departure. §7.3 Holm family: four p-values — β_CPI overnight (one-sided, H-CPI), β_FOMC regular session (one-sided, H-FOMC), β_FOMC overnight and β_CPI regular session (two-sided, secondary). Holm is applied within the primary run and within the ex-window run, so the §8 rule compares like with like. The other robustness runs (2020 onward, GK, no weekday dummies, no dual days) report raw p-values, labelled as such. Cost to the claim: none.
- 2026-11-14 — Clarification (spec silent), not a departure. §7.4 sample: the same rows as the §7.3 primary run; β_CPI and β_FOMC tested two-sided, outside the Holm family. Cost to the claim: none.
