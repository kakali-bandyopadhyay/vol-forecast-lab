# FINDINGS — `vol-forecast-lab`

What the build actually found, as distinct from what the pre-registration predicted. Numbers here
come from committed scripts; anything not yet computed is absent rather than estimated.

**Status:** Steps 1–9 complete. Q1's headline moves to the ex-window sample; Q1d (HAR vs GARCH)
is unchanged; Q2 and Q3 are reported below.

---

## Results

### Step 7 — Q1: does VIX-implied variance add information beyond HAR?

Test period 2020-01-02 → 2026-10-12: 1,703 forecasts from 82 refit blocks (81 × 21 sessions plus a
final block of 2), HAC lag 21. Produced by `scripts/run_eval.py`; the panel hash, F_min and the
Diebold–Mariano convention are recorded in `results/eval_manifest.json`.
Its `code_commit` field names the revision checked out when the script last ran, which is not
necessarily the commit carrying this file.

**Full sample: not detected. Ex-window: detected, and the headline.** On all 1,703 forecasts
Clark–West does not reject (t = 1.557, one-sided p = 0.0597), with mean f_t = +4.87 × 10⁻⁶ and
95% interval [−1.26 × 10⁻⁶, +1.10 × 10⁻⁵]. Outside Feb–Apr 2020 the same test rejects
(t = 3.14, one-sided p = 0.0009, n = 1,632), with mean f_t = +8.9 × 10⁻⁷ and 95% interval
[3.3 × 10⁻⁷, 1.4 × 10⁻⁶]. Under the symmetric §8 rule the ex-window result is the headline; the
full sample is shown beside it. The §8 reading is "incremental information not detected on the
full sample", not "no incremental information"; the ex-window interval excludes zero.

**§8 reading for the headline.** Outside Feb–Apr 2020, VIX-implied variance adds out-of-sample
information beyond lagged realised variance at the 30-day horizon. On the full sample that
information is not detected. The window rule was fixed and pushed (DEVIATIONS, commit `92f9631`)
after the full-sample p = 0.0597 was known and before any ex-window number was computed.

**Why the window matters this much.** The 71 removed forecasts are 4% of the test set but carry
92% of HAR's full-sample squared error (91% of HAR-X's). The full-sample MSE comparison is
therefore close to a comparison over spring 2020 alone.

| Test | Pair | Statistic | p-value | Reading |
|---|---|---|---|---|
| Clark–West, one-sided (full sample) | M2 vs M1 | 1.557 | 0.0597 | Not rejected at 0.05 |
| Clark–West, one-sided (ex-window, headline) | M2 vs M1 | 3.14 | 0.0009 | Rejected at 0.05; 1,632 forecasts |
| Diebold–Mariano on QLIKE | M2 vs M1 | 0.336 | 0.737 | Difference not detected; sign favours M1 |
| Diebold–Mariano on QLIKE | M1 vs M0 | −2.215 | 0.0268 | Favours HAR; Holm pending |
| Mincer–Zarnowitz | M0 | 58.81 | 1.7 × 10⁻¹³ | Calibration rejected |
| Mincer–Zarnowitz | M1 | 2.46 | 0.293 | Not rejected |
| Mincer–Zarnowitz | M2 | 0.31 | 0.858 | Not rejected |

All rows except the ex-window Clark–West row are full-sample.

**Diebold–Mariano sign convention:** d = loss(first) − loss(second), so a negative statistic favours
the first model.

**QLIKE and MSE disagree, and the ex-window ordering reverses.** On the full sample QLIKE ranks
HAR ahead of HAR-X (0.4133 vs 0.4203) while MSE ranks HAR-X ahead (4.545 × 10⁻⁵ vs 4.705 × 10⁻⁵).
Outside Feb–Apr 2020 the ordering flips: QLIKE favours HAR-X (0.199 vs 0.217), while plain MSE
favours HAR (4.06 × 10⁻⁶ vs 4.26 × 10⁻⁶). Clark–West can reject in favour of HAR-X even when
plain MSE is lower for HAR, because it corrects for the noise of estimating the extra coefficient;
a lower MSE is not a nested-model test. §8 pre-commits to reporting both and saying which periods
drive the gap, rather than choosing between them.

| Model | mean QLIKE | mean MSE |
|---|---|---|
| M0 | 0.508748 | 7.635 × 10⁻⁵ |
| M1 HAR | 0.413265 | 4.705 × 10⁻⁵ |
| M2 HAR-X | 0.420345 | 4.545 × 10⁻⁵ |

| Ex-window (n = 1,632) | mean QLIKE | mean MSE |
|---|---|---|
| M1 HAR | 0.216611 | 4.065 × 10⁻⁶ |
| M2 HAR-X | 0.199432 | 4.256 × 10⁻⁶ |

**Floor (§3.4):** floored counts are M0 0, M1 0, M2 1, R1 0. One M2 forecast in April 2020 was
negative before flooring and was raised to F_min = 3.722081 × 10⁻⁴; it is the only date on which
the raw and floored series differ. A negative variance forecast is precisely what §3.4 exists to
catch.

**Integrity (§9.1):** the planted-leak check was run on the real panel, not only on synthetic data.
Adding RV_{t+1} to M1 cuts QLIKE by **10.5%**, consistent with 7.5–10.9% on synthetic data, so the
evaluation demonstrably detects a leak of one session in twenty-one. The leaky forecasts are not
written to any result file and the real specification is untouched. The evaluation was re-run after
the degenerate-input guards and the Holm removal: every M0–M2 statistic and p-value is unchanged,
to zero difference, against a local scratch copy of the pre-fix table.

**Holm:** Q1b–Q1d remain deferred to Step 10 and provisional at Step 9; the fourth §5.5 test is
Q1c (M4 vs M2). The Diebold–Mariano p-values in this file are raw and no adjusted value is quoted
here. Q3's four §7.3 tests use Holm within the primary and ex-window runs; Q2 is not a Holm family.

**Feb–Apr 2020 (§8).** The rule removes 71 forecasts — t in the window, or a target session in it
— leaving 1,632. The full window-rule table is `results/window_rule.parquet` and the ex-window
tests are `results/tests_ex_window.parquet`. The headline moves to the ex-window sample for Q1,
MZ M2, HAR vs persistence, and the ex-post premium; Q3 decisions are the same in both runs.

### Step 8 — Q1d: HAR vs GARCH(1,1) on QLIKE

M3 is a constant-mean GARCH(1,1) on daily close-to-close percent returns; its 30-day forecast is
the sum of the 1- to N_t-step-ahead conditional variance forecasts ÷ 10,000. The mean model is the
`arch` constant mean (DEVIATIONS, 2026-11-14). All 82 folds converged, none at the α+β bound,
maximum α+β = 0.974, and there were no other warnings.

**Not detected.** Diebold–Mariano on QLIKE gives t = 0.833, raw p = 0.405, so a QLIKE difference
between HAR and GARCH is not detected. The point estimates favour M3: mean QLIKE 0.3958 vs M1
0.4133, and mean MSE 4.692 × 10⁻⁵ vs 4.705 × 10⁻⁵ for M1. Holm remains pending, so the p-value is
raw.

| Test | Pair | Statistic | p-value | Reading |
|---|---|---|---|---|
| Diebold–Mariano on QLIKE | M1 vs M3 | 0.833 | 0.405 | Difference not detected; sign favours M3 |
| Mincer–Zarnowitz | M3 | 15.33 | 4.7 × 10⁻⁴ | Calibration rejected |

**M3 calibration:** the MZ Wald statistic is 15.33 (p = 4.7 × 10⁻⁴), with a = 1.37 × 10⁻³ and
b = 0.667, so M3's calibration is rejected.

**Information sets:** both models estimate on data through the session before each refit date.
HAR has about 21 fewer training rows, because its newest rows do not yet have a complete 30-day
target. This is not a leak.

### Step 9 — Q2 and Q3: spread/premium and events

**Window-rule decisions (§8).**

| Result | Full sample | Ex-window | Headline |
|---|---|---|---|
| Clark–West, M2 vs M1 | not rejected (t = 1.557, p = 0.0597) | rejected (t = 3.14, p = 0.0009) | ex-window |
| DM QLIKE, M2 vs M1 | not rejected (p = 0.737) | not rejected (p = 0.140) | full (provisional, Holm at Step 10) |
| DM QLIKE, M1 vs M0 | rejected, favours HAR (p = 0.0268) | not rejected (p = 0.094) | ex-window (provisional, Holm at Step 10) |
| DM QLIKE, M1 vs M3 | not rejected (p = 0.405) | not rejected (p = 0.173) | full (provisional, Holm at Step 10) |
| MZ M0 / M3 | rejected | rejected | full |
| MZ M1 | not rejected (p = 0.293) | not rejected (p = 0.298) | full |
| MZ M2 | not rejected (p = 0.858) | rejected (W = 27.9, p = 8.6 × 10⁻⁷) | ex-window |
| Q2 ex-ante spread, mean | positive, significant (t = 7.80) | positive, significant (t = 7.75) | full |
| Q2 ex-post premium, mean | positive, not significant (t = 1.06, p = 0.288) | positive, significant (t = 7.05) | ex-window |
| Q3, four Holm tests | see below | same four decisions | full |

Source: `results/window_rule.parquet`. The three Diebold–Mariano rows use raw p-values and are provisional (DEVIATIONS A.5).

**Q2 — implied versus realised (§6).** 30-day total variance, decimal; test-period dates.

| Series | Sample | n | Mean | Median | Share > 0 | HAC t |
|---|---|---|---|---|---|---|
| Ex-ante spread (IV − HAR) | full | 1,703 | 1.096 × 10⁻³ | 6.31 × 10⁻⁴ | 78.2% | 7.80 |
| Ex-ante spread | ex-window | 1,632 | 1.064 × 10⁻³ | 6.29 × 10⁻⁴ | 78.0% | 7.75 |
| Ex-post premium (IV − RV30) | full | 1,703 | 6.34 × 10⁻⁴ | 1.124 × 10⁻³ | 85.9% | 1.06 |
| Ex-post premium | ex-window (headline) | 1,632 | 1.207 × 10⁻³ | 1.126 × 10⁻³ | 87.6% | 7.05 |

Where the premium was negative (§8 Q2 reading): on the full sample, 2020 (mean −1.68 × 10⁻³,
although its median is positive and 75% of its days are positive — a few crash-period targets
outweigh the rest) and the low-VIX regime (mean −4.2 × 10⁻⁴, not significant). Outside the window
no year and no regime has a negative mean; 2025 is positive but not significant (t = 0.48).
The spread is negative in the low-VIX regime (mean −2.9 × 10⁻⁴, t = −5.8: implied variance below
the HAR forecast when VIX is low) and close to zero in 2024 (t = 0.19). Year and regime tables:
`results/q2_summary.parquet`. No signal, threshold or trade rule is drawn from either series (§6).

**Q3 — event study (§7).** Primary run 2010-02-04 → 2026-11-11, 4,219 sessions; CPI 199, FOMC 133,
dual 10, emergency 5 (DEVIATIONS D). HAC lag 5. Effect = exp(β) − 1, a multiplicative effect on
x + c.

| Coefficient | β | Effect (95% CI) | Holm p | Reading |
|---|---|---|---|---|
| H-CPI: CPI, overnight (one-sided) | +0.314 | +37% (+4% to +80%) | 0.033 | Rejected; positive as hypothesised |
| H-FOMC: FOMC, session (one-sided) | +0.608 | +84% (+30% to +160%) | 0.001 | Rejected; positive as hypothesised |
| FOMC, overnight (two-sided, secondary) | −0.446 | −36% (−55% to −10%) | 0.033 | Rejected; negative |
| CPI, session (two-sided, secondary) | −0.158 | −15% (−36% to +14%) | 0.290 | Not rejected |

The ex-window run gives the same four decisions, so the full sample is the headline.

FOMC overnight: §8 asks for event times and the calendar to be checked before reporting an effect
in the other component. Every FOMC statement is intraday (24 pre-2013 rows carry an assumed 14:15
time, intraday under either candidate time; `data/events/GAPS.md`), so the overnight component of
an FOMC day is the night before the statement. The result reads as lower overnight variance going
into FOMC days, net of a general Wednesday overnight effect that the weekday dummies already
absorb (Wednesday overnight β = −0.334, p = 0.0008, vs Monday).

Robustness (raw p, not Holm-adjusted): the H-CPI and H-FOMC signs hold in every run. H-CPI
overnight: 2020+ β = +0.82, no weekday dummies +0.26, no dual days +0.30. H-FOMC session: GK in
place of r_oc² +0.60, 2020+ +0.71, no weekday +0.64, no dual +0.64. FOMC overnight is negative
in every run and not significant on 2020+ (β = −0.40, p = 0.13). CPI on GK is +0.07 (p = 0.23).

Weekday diagnostic (session, vs Monday): Tuesday–Friday all +0.19 to +0.30, so a general midweek
session effect exists, and H-FOMC is estimated net of it.

§7.4 VIX change on event days: β_CPI = −0.11 (p = 0.38), β_FOMC = −0.14 (p = 0.51), level-matched
through VIX_{t−1}. No event-day VIX change detected. Secondary; outside Holm.

Emergency control: only 2 events remain in the ex-window run, so its standard errors there are not
meaningful (p = 1 × 10⁻¹³⁰). It is a nuisance control outside the Holm family; no inference is
quoted from it.

Stated limit (§7.5): daily data measure the whole regular session on an FOMC day, including the
hours before the statement — not the announcement window.

---

## Limitations

Known, declared, and not repaired. Each is either inherent to the design or a deliberate choice
recorded before results.

- **M4's early-stopping holdout is not purged.** The fit block stops one row short of the stopping
  block, so the last fit targets — each covering 30 calendar days — extend into the stopping
  block. The overlap is confined to the training window and never reaches the train/forecast
  boundary that §9.3 protects, so it cannot leak test-period information; its effect is at most to
  make early stopping slightly less conservative. Left unpurged because §4.3 freezes "the last 20%
  of each training window" and the only affected model is M4, the challenger, whose result is
  reported whatever it is. See `splits.early_stop_split`.

---

_Results are added per step as they are computed._
