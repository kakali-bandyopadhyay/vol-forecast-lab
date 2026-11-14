# Deviations log - vol-forecast-lab

Every departure from PREREGISTRATION.md, dated, with what it cost the claim.

A deviation log that stays empty is more likely to indicate under-reporting than a clean run.

## Before freeze

## After freeze

- 2026-11-14 — Clarification (spec silent), not a departure. §4 M3 and BUILD_GUIDE Step 8 do not name a mean model. Fixed before any M3 run: `arch` constant mean (package default); the forecast is the sum of the N_t step-ahead conditional variances ÷ 10,000, with no μ² term added. Cost to the claim: none; chosen with no test-period M3 output in view.
