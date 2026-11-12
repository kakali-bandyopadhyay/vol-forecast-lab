# Pre-registration — `vol-forecast-lab`

**Frozen:** _YYYY-MM-DD_ (fill at freeze)
**Snapshot:** `_snapshot-id_`
**Price panel SHA-256:** `_hash_`
**VIX file SHA-256:** `_hash_`
**Event calendar SHA-256:** `_hash_` (fomc.csv + cpi.csv)
**Status:** written and committed **before any model has been fitted and before any out-of-sample forecast or event estimate has been computed.**

> Template note — delete this box at freeze. Every `_italic placeholder_` must be replaced with a
> value or "N/A – reason". Nothing in §3–§10 may change after the tag `v0.1-prereg` without a
> dated entry in `DEVIATIONS.md`.

---

## 0. Scope

This document fixes the design of one forecasting comparison, one implied-versus-realised
measurement and one event study before any of them is run. Anything not stated here may not be
added after the fact without a dated entry in `DEVIATIONS.md` recording what changed and what it
cost the claim.

Limits declared up front:

1. **Daily data only.** Realised variance is estimated from daily open/high/low/close. The proxy is
   noisy; §5 uses losses that rank forecasts correctly under a noisy, conditionally unbiased proxy
   (Patton, 2011). It does not remove the noise.
2. **SPY as a proxy for the S&P 500 index.** VIX is derived from SPX options; SPY is used because
   licensed daily index OHLC is not available at zero cost. Tracking differences are inside the series.
3. **No options-chain data.** The implied input is the VIX index level. No surface, skew, term
   structure, pricing model or options P&L.
4. **A research tool, not a trading system.** No spread or premium is turned into a rich/cheap
   verdict, signal or recommendation. Public data; research prototype; not investment advice.

---

## 1. Questions

### 1.1 Primary question (Q1 — the only headline test)

Does HAR-X — HAR plus the VIX-implied variance known at the forecast date — forecast the next
30 calendar days of realised variance better out of sample than HAR? Nested comparison on
identical dates and targets (§5.5).

### 1.2 Secondary questions (reported, Holm-corrected, never headlined over Q1)

- **Q1b:** HAR-X vs HAR on QLIKE.
- **Q1c:** small MLP (challenger) vs HAR-X on QLIKE.
- **Q1d:** HAR vs GARCH(1,1); HAR vs the persistence baseline.
- **Q2 (measurement, §6):** how VIX-implied variance compares with (a) the HAR forecast — the
  **estimated ex-ante spread** — and (b) the realised variance that followed — the **ex-post premium**.
- **Q3 (event study, §7):** are scheduled CPI and FOMC days associated with additional variance
  after controlling for recent volatility, and in which component — overnight or regular session?

No outcome is predicted for Q1–Q1d. The MLP's architecture and tuning rule are fixed in §4.3 and
its result is reported whatever it is.

---

## 2. Data

### 2.1 Sources

| Input | Source | Fields | Committed? |
|---|---|---|---|
| SPY daily prices | _Tiingo EOD_ | adjOpen, adjHigh, adjLow, adjClose | No (manifest + hash only) |
| VIX daily | _Cboe VIX history CSV_ | open, high, low, close | No (manifest + hash only) |
| VIX cross-check | _FRED VIXCLS_ | close | No (result committed) |
| FOMC scheduled statement dates | federalreserve.gov meeting calendars | date, time ET, source URL | Yes (`data/events/fomc.csv`) |
| CPI release dates | BLS release schedules | date, time ET, source URL | Yes (`data/events/cpi.csv`) |
| Trading calendar | _exchange-calendars, version_ | sessions, early closes | Version pinned |
| FOMC statements (HTML) and minutes (PDF) | federalreserve.gov | text, URL, hash | Fixtures only; raw files in snapshot |

FOMC documents feed the retrieval pipeline only (build Step 10b). They are **not** inputs to any
model, test or estimate in §4–§7.

All times are US/Eastern. Data vintage = snapshot date above.

### 2.2 Window

First session 2010-01-04, last session 2026-11-11. Sessions: 4,241.

### 2.3 Alignment and missing data

- SPY and VIX are joined on observation date. **VIX is never forward-filled.** A date missing
  either series is excluded from every analysis that needs both; excluded dates are listed in
  `data/audit.json`.
- Adjusted fields only. Within-day ratios are unaffected by the adjustment factor. Overnight
  returns use adjOpen_t / adjClose_{t−1}. No absolute adjusted level is used.

### 2.4 Audit results (filled before freeze)

| Check | Rule | Result |
|---|---|---|
| Missing sessions vs calendar | listed | 0 |
| OHLC consistency | L ≤ min(O,C), H ≥ max(O,C) | 0 violations |
| Zero-range days | logged, kept | 0 |
| VIX Cboe vs FRED close | abs diff ≤ 0.01 | 0 mismatches on 4,240 shared sessions (max 0.000000) |
| Ex-dividend spot check | adjusted overnight ratio ≈ raw gap net of dividend | 67/67 (worst 2.67 bp) |
| Event rows checked against source page | every row | 10/10 spot-check, external review (DeepSeek, 11 Nov), briefs/vfl-event-calendar-audit_2026-11-11.md |
| Early-close sessions | flagged | 34 |
| VIX rows on non-session dates | listed, excluded by the session join | 33, all from 2022-05-30; US market holidays, none repeats the prior close (not forward-fills) |
| Excluded dates (§2.3) | listed in `data/audit.json` | 0 |

Source: `data/audit.json`, snapshot `20261112T071104Z`, reproducible with
`scripts/audit_snapshot.py`.

---

## 3. Definitions

### 3.1 Daily realised-variance proxies (daily variance of log returns, decimal)

- Overnight: r_on,t = ln(O_t / C_{t−1}); regular session: r_oc,t = ln(C_t / O_t);
  close-to-close: r_t = r_on,t + r_oc,t.
- Garman–Klass (session): GK_t = ½·[ln(H_t/L_t)]² − (2 ln 2 − 1)·r_oc,t².
- **Primary proxy:** RV_t = r_on,t² + GK_t (covers the overnight gap and the session range).
- **Robustness proxies:** close-to-close r_t²; r_on,t² + Parkinson_t.
- Note: r_t² = r_on² + r_oc² + 2·r_on·r_oc. The cross term is reported (mean and share of
  r_t² variance, training window) and never dropped silently.

### 3.2 Target (matched to the VIX horizon)

For forecast date t (close): **RV_t^{30} = Σ RV_s over sessions s with t < date(s) ≤ t + 30
calendar days.** N_t = the number of such sessions, taken from the exchange calendar (known in
advance). Models forecast the per-session mean; the forecast of the total = per-session forecast
× N_t. Sessions excluded under §2.3 are recorded; a target with any excluded session is dropped.

### 3.3 Implied variance

IV_t = (VIX_t / 100)² × 30/365 — implied total variance over the next 30 calendar days, on the
same scale as RV_t^{30}. The ~15-minute gap between the VIX and SPY closes is stated, not adjusted.

### 3.4 Positivity rule (all models)

Every forecast passed to QLIKE is max(F, F_min), F_min = 1st percentile of RV_t^{30} in the
initial training window. The number of floored forecasts is reported per model.

### 3.5 Regimes (for reporting only)

VIX terciles with thresholds fixed from the initial training window: low / mid / high. Calendar
sub-periods: 2020, 2021–2023, 2024–end; and the test period with and without 2020-02-15 → 2020-04-30.

---

## 4. Models (fixed list — no additions without a deviation entry)

| ID | Model | Inputs at t | Estimation |
|---|---|---|---|
| M0 | Persistence baseline | per-session mean RV over the previous 21 sessions | none |
| M1 | HAR (Corsi, 2009), levels | RV_d, RV_w (5-session mean), RV_m (22-session mean) | OLS, hand-coded |
| M2 | HAR-X | M1 inputs + IV_t / N_t (per-session implied variance) | OLS, hand-coded |
| M3 | GARCH(1,1), normal QMLE | daily close-to-close returns (%) | `arch` _version_; N_t-step variances summed |
| M4 | MLP (challenger) | log of M2's four inputs, standardised on training rows | PyTorch _version_; §4.3 |
| R1 | VIX-implied (reference only) | IV_t | none; used in §6, not ranked against models |

### 4.3 MLP architecture and tuning rule

Grid: hidden units {8, 16, 32}; layers {1, 2}; weight decay {0, 1e-4, 1e-3}; Adam, lr 1e-3;
early stopping patience 20 epochs on the last 20% of each training window; seeds {_s1, s2, s3_}.
Output = log per-session variance; back-transformed with the training-residual variance correction.
**Selection:** lowest mean QLIKE on the validation period (§5.1) across seeds. Reported forecast =
mean of the three seeds. The grid is not extended after freeze.

---

## 5. Evaluation design

### 5.1 Splits

| Period | Dates | Use |
|---|---|---|
| Initial training | first session → 2015-12-31 | first fit; fixes F_min and regime thresholds |
| Validation | 2016-01-01 → 2019-12-31 | MLP selection only |
| **Test** | **2020-01-01 → last session with a complete target** | **all reported comparisons; untouched before freeze** |

### 5.2 Re-estimation

Expanding window. M1–M3 re-estimated every 21 sessions; M4 re-trained once per calendar year with
the frozen configuration. The last training target must end before the forecast date
(gap = the target's 30-calendar-day span).

### 5.3 Losses

QLIKE: L(RV, F) = RV/F − ln(RV/F) − 1 (primary ranking loss). MSE on total variance.

### 5.4 Calibration

Mincer–Zarnowitz per model: RV_t^{30} = a + b·F_t + e; joint test a = 0, b = 1, HAC errors.

### 5.5 Tests

- **Q1 (headline): Clark–West** for nested MSE comparison, M2 (HAR-X) vs M1 (HAR):
  f_t = e_{1,t}² − [e_{2,t}² − (F_{1,t} − F_{2,t})²]; one-sided (H_A: HAR-X better), α = 0.05,
  Newey–West HAC t-stat.
- **Secondary (Diebold–Mariano on QLIKE, two-sided):** Q1b M2 vs M1; Q1c M4 vs M2; Q1d M1 vs M3,
  M1 vs M0. **Holm** across this family at α = 0.05.
- HAC lag for overlapping targets: max(21, ⌊4(T/100)^{2/9}⌋).

### 5.6 Effective sample — stated before results

Test forecasts ≈ _n_; non-overlapping 30-day windows ≈ _n/21_. Small differences may not be
detectable; a non-rejection is reported as "not detected", never as "no difference".

---

## 6. Implied versus realised (Q2)

- **Estimated ex-ante spread:** S_t = IV_t − F_t^{M1} (HAR forecast, which uses no VIX input).
- **Ex-post premium:** P_t = IV_t − RV_t^{30}.
- Report for each: mean, median, share positive, HAC t-stat (lag as §5.5), by year and by regime.
- No signal, threshold, trade rule or P&L is derived from either series.

---

## 7. Event study (Q3)

### 7.1 Events and conventions

Scheduled CPI releases (08:30 ET, before the open) and scheduled FOMC statements (released during
the regular session). A day with both events carries both indicators and is listed; no day is
dropped for overlap.

**Sample.** Every session from 2010-01-04 to the last session in the snapshot: 134 FOMC days,
200 CPI days, and 10 days carrying both. This is the full history, not the untouched test period
of §5.1, which governs the forecast comparison only. The event study is descriptive and in-sample;
no reading of it is a forecast. Robustness: re-run on 2020-01-01 onward.

**Non-session events.** An event whose `event_date` is not an XNYS session is excluded by rule
(`in_event_sample = false`); `effective_session` is retained for transparency but never used to
reassign the event. Two CPI releases fall on Good Friday, 2017-04-14 and 2020-04-10, so the CPI
event sample is 200 of 202 rows.

**Unscheduled actions.** Excluded as events; entered only as an emergency control, set on the
session that first prices each action — by release date, with an action announced on a non-session
mapped to the next session:

| Control date | Basis |
|---|---|
| 2010-05-10 | Sunday 2010-05-09 action; first priced Monday |
| 2019-10-11 | decided 2019-10-04, released 2019-10-11 |
| 2020-03-03 | released in session |
| 2020-03-16 | Sunday 2020-03-15 action; first priced Monday |
| 2020-03-23 | notation vote with a released statement |

Not used, with reason: 2010-10-15 and 2014-03-04, no released statement recorded; 2020-03-17,
scheduled meeting cancelled, nothing announced; 2020-03-19, 2020-03-31, 2020-08-27 and
2025-08-22, notation votes with no released statement recorded in `unscheduled.csv`.

The control is there because Feb–Apr 2020 is a high-leverage block for the γ coefficients in a log
specification, and because unmetered crisis days inflate the residual variance that sets every HAC
standard error. It is not there to absorb anything from β_FOMC: no scheduled FOMC date falls in
March 2020, so those actions never enter the FOMC indicator.

### 7.2 Signed hypotheses

- H-CPI: the CPI effect is positive in the **overnight** component.
- H-FOMC: the FOMC effect is positive in the **regular-session** component.
Opposite-component coefficients are reported as secondary.

### 7.3 Specification

For component k ∈ {overnight r_on², regular-session r_oc²}:
log(x_{k,t} + c_k) = α + β_CPI·CPI_t + β_FOMC·FOMC_t + β_EMG·EMG_t + γ₁·log RV_d,t−1 +
γ₂·log RV_w,t−1 + γ₃·log RV_m,t−1 + δ·weekday dummies + ε_t,
with c_k = 1% of the full-sample median of x_k.

The model has no CPI×FOMC interaction; β_CPI and β_FOMC are estimated almost entirely from
single-event days, and on the 10 dual days their effects are assumed additive in logs — that is,
multiplicative in levels. Additivity is an assumption of the specification, not a finding, and the
dual-day cut below shows how much it matters.

Newey–West HAC (lag 5). One-sided tests for H-CPI and H-FOMC; Holm across the four event
coefficients. β_EMG is a nuisance control and sits outside the Holm family. Report event counts,
coefficients, 95% CIs, implied multiplicative effects, and the weekday coefficients as a
diagnostic of how large a general Wednesday effect is.

Robustness: GK in place of r_oc²; excluding 2020-02-15 → 2020-04-30, which is complementary to the
emergency control — one drops the block, the other meters it; the model without weekday dummies; a
cut that drops the 10 dual days.

### 7.4 VIX change on event days

ΔVIX_t = α + β_CPI·CPI_t + β_FOMC·FOMC_t + β_EMG·EMG_t + λ·VIX_{t−1} + ρ·ΔVIX_{t−1} +
ε_t, HAC (lag 5) — compared with a level-matched baseline, not with zero. Both event regressions
carry the same controls, so a single crisis day cannot dominate ΔVIX on its own; β_EMG sits
outside the Holm family here too.

### 7.5 Stated limit

With daily data, regular-session variance on an FOMC day includes the hours before the statement.
This study measures the session, not the announcement window.

---

## 8. Pre-committed readings

| Outcome | Reading |
|---|---|
| Q1: Clark–West rejects | VIX-implied variance adds out-of-sample information beyond lagged RV at the 30-day horizon. Report the size and regime breakdown. |
| Q1: no rejection | Incremental information not detected at this sample size; report the interval on the loss difference. |
| Q1b disagrees with Q1 | Report both; MSE and QLIKE weight errors differently — state which periods drive the gap. |
| Q1c: MLP better than HAR-X | Report only after §9 checks pass and the result holds across regimes. |
| Q1c: MLP not better | Report; the linear form captures the information at this sample size. |
| Q2: mean ex-post premium > 0 | Report size, years and regimes where it was negative. |
| Q2: mean ex-post premium ≤ 0 | Check units and the 30/365 convention before reporting. |
| Q3: signs as hypothesised | Report with counts and CIs; session-level, not announcement-window. |
| Q3: effect in the other component | Check event times and calendar first; then report. |
| Any result driven by Feb–Apr 2020 alone | Headline the result without that window; show both. |

---

## 9. Leakage and integrity checks (must pass before any result is reported)

1. Planted-leak test: add RV_{t+1} to the M1 inputs; test QLIKE must fall sharply. Proves the
   evaluation can detect leakage. The real specification is never altered.
2. Every input timestamp ≤ forecast timestamp — unit-tested.
3. No training target overlaps the forecast date — unit-tested.
4. Shuffled-target MLP must lose to M0.
5. Proxy check (training window): mean(RV primary)/mean(r²) and the cross-term share, reported.

---

## 10. Deliberate non-claims

1. No trading strategy, signal, recommendation or options P&L.
2. No claim about implied-volatility surfaces, skew or term structure.
3. No claim about intraday-sampled realised variance or announcement windows.
4. No claim beyond SPY and the stated window.
5. No causal claim about what CPI or FOMC content does — only timing of realised variance.
6. No text-derived signal, sentiment score or feature. FOMC documents are retrieved and cited for
   explanation only.

---

## 11. Threats to validity, stated before results

- Noisy daily proxy; SPY rather than the index.
- Single price vendor; VIX cross-checked on the close only.
- Test period dominated by a few stress episodes.
- Overlapping 30-day targets reduce the effective sample (§5.6).
- Pre-registered tuning still adds selection risk to the MLP.

---

## 12. Repository integrity

- This file is committed and tagged **`v0.1-prereg`** before any model is fitted. The tag is never
  moved, amended or force-pushed. _Optional: OSF registration — DOI_.
- Snapshot pulled once, hashed, never regenerated. `data/raw/` is git-ignored; the manifest and
  audit results are committed. No vendor data is committed. CI runs on a synthetic fixture.
- Code committed with this file: fetch, audit, calendar and RV-definition modules only.

---

## 13. Sign-off

Design frozen: _YYYY-MM-DD_.
No model has been fitted and no out-of-sample forecast or event estimate computed at the time of writing.
