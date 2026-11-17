# FINDINGS — `vol-forecast-lab`

What the build actually found, as distinct from what the pre-registration predicted. Numbers here
come from committed scripts; anything not yet computed is absent rather than estimated.

**Status (17 Nov 2026):** Steps 1–13 complete; Step 14 (write-up and release) in this revision.
Forecasting core: Q1's headline moves to the ex-window sample; Q1b–Q1d are not detected after the
final Holm adjustment; Q2 and Q3 are reported below. Platform: Step 10b document pipeline, Step 10c
platform slice (Kafka, AWS lake run on 16 Nov 2026 then destroyed, Spark, OpenSearch, Redis),
Step 11 agent, Step 12 MLOps and drift, Step 13 Airflow DAG and kind deployment. Claim-by-claim
evidence: `EVIDENCE.md`.

**Charts (Step 14).** `figures/results/fig1–fig4` (forecast comparison, errors over time, implied vs
realised by year, event variance split), built by `scripts/make_charts.py` from aggregates only
(ADR 0003). Ex-window mean QLIKE, descriptive: M0 0.284, M1 0.217, M2 0.199, M3 0.199, M4 0.178
(`results/loss_summary.json`); no ranking claim beyond the Holm table.

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
information is not detected. The window rule was fixed and pushed (DEVIATIONS, commit `5c718a1`)
after the full-sample p = 0.0597 was known and before any ex-window number was computed.

**Why the window matters this much.** The 71 removed forecasts are 4% of the test set but carry
92% of HAR's full-sample squared error (91% of HAR-X's). The full-sample MSE comparison is
therefore close to a comparison over spring 2020 alone.

| Test | Pair | Statistic | p-value | Reading |
|---|---|---|---|---|
| Clark–West, one-sided (full sample) | M2 vs M1 | 1.557 | 0.0597 | Not rejected at 0.05 |
| Clark–West, one-sided (ex-window, headline) | M2 vs M1 | 3.14 | 0.0009 | Rejected at 0.05; 1,632 forecasts |
| Diebold–Mariano on QLIKE | M2 vs M1 | 0.336 | 0.737 | Difference not detected; sign favours M1 |
| Diebold–Mariano on QLIKE | M1 vs M0 | −2.215 | 0.0268 (raw) | Not detected after Holm (p = 0.107); sign favours HAR |
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

**Holm:** applied once, after Step 10, across the four §5.5 Diebold–Mariano tests (Q1b M2 vs M1, Q1c M4 vs M2, Q1d M1 vs M3 and M1 vs M0), separately in the full and ex-window samples (DEVIATIONS P). Final decisions are in the Step 10 section and `results/holm_final.parquet`. Q3's four §7.3 tests use Holm within the primary and ex-window runs; Q2 is not a Holm family.

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
0.4133, and mean MSE 4.692 × 10⁻⁵ vs 4.705 × 10⁻⁵ for M1. After the final Holm family (Step 10) it remains not detected: Holm p = 1.000 full, 0.281 ex-window.

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
| DM QLIKE, M2 vs M1 | not rejected (raw p = 0.737, Holm p = 1.000) | not rejected (raw p = 0.140, Holm p = 0.281) | full (final) |
| DM QLIKE, M4 vs M2 (Q1c) | not rejected (raw p = 0.849, Holm p = 1.000) | not rejected (raw p = 0.056, Holm p = 0.224) | full (final) |
| DM QLIKE, M1 vs M0 | not rejected (raw p = 0.0268, Holm p = 0.107) | not rejected (raw p = 0.094, Holm p = 0.281) | full (final) |
| DM QLIKE, M1 vs M3 | not rejected (raw p = 0.405, Holm p = 1.000) | not rejected (raw p = 0.173, Holm p = 0.281) | full (final) |
| MZ M0 / M3 | rejected | rejected | full |
| MZ M1 | not rejected (p = 0.293) | not rejected (p = 0.298) | full |
| MZ M2 | not rejected (p = 0.858) | rejected (W = 27.9, p = 8.6 × 10⁻⁷) | ex-window |
| Q2 ex-ante spread, mean | positive, significant (t = 7.80) | positive, significant (t = 7.75) | full |
| Q2 ex-post premium, mean | positive, not significant (t = 1.06, p = 0.288) | positive, significant (t = 7.05) | ex-window |
| Q3, four Holm tests | see below | same four decisions | full |

Source: `results/window_rule.parquet` for the Step 9 rows; the four Diebold–Mariano rows are the final §5.5 Holm family from `results/holm_final.parquet` (Step 10, DEVIATIONS A.5 and P). Decisions use Holm-adjusted p-values; raw p-values are shown for reference.

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

### Step 10 — Q1c: MLP challenger vs HAR-X, and the final Holm family

**Model.** M4 per §4.3 and DEVIATIONS I–S: log of M2's four inputs, standardised on the fit block; log per-session target, standardised on the fit block (S); ReLU network, full-batch Adam, early stopping on the last 20% of each training window; three seeds averaged in levels after the exp(ŷ + s²/2) back-transform; re-trained once per calendar year.

**Implementation fix before the test run (DEVIATIONS S).** The first selection run trained on the unscaled log target and produced validation QLIKE of 4.8–7.8 for every configuration. The target was standardised, a regression test added, and selection re-run. No test-period M4 number existed before the fix (`53c830e` rule, `92b8154` code).

**Selection (validation 2016-01-04 → 2019-11-29, 985 dates, 216 trainings).** Chosen: 32 hidden units, one layer, weight decay 1e-3 – validation QLIKE 0.2650 (runner-up, same network with weight decay 1e-4: +1.6e-5; tie-break not used). One-layer 32-unit networks beat two-layer (0.280) and smaller networks (0.293–0.302); weight decay barely mattered. No training hit the 3,000-epoch cap. Committed (`92498f0`) before any test forecast.

**Integrity (§9.4).** Shuffled-target MLP: mean QLIKE 0.785 vs M0 0.509 – loses to M0, check passed. Test run: 7 folds, 1,703 forecasts on the M1 dates, none floored, no cap hits (best epochs 27–414).

**Mean QLIKE, full sample (descriptive).** M3 0.3958 · M1 0.4133 · **M4 0.4147** · M2 0.4203 · M0 0.5087.

**Q1c result.** DM QLIKE, M4 vs M2 (d = QLIKE(M4) − QLIKE(M2), HAC lag 21): full t = −0.190, raw p = 0.849, Holm p = 1.000; ex-window t = −1.911, raw p = 0.056, Holm p = 0.224. **Not detected in either sample; headline full.** No evidence that the MLP improves on HAR-X at this sample size. (§8's pre-committed reading for this outcome says "the linear form captures the information at this sample size"; that wording claims more than a non-rejection shows, so it is reported here under §5.6's rule – "not detected", never "no difference".) The point estimate favours M4 outside Feb–Apr 2020, but not significantly, and not across regimes (below).

**Regimes (VIX terciles 14.89 / 18.57; mean QLIKE M4 − M2).**

| Sample | Low | Mid | High |
|---|---|---|---|
| Full | +0.0455 (n 282) | −0.0277 (n 552) | −0.0083 (n 869) |
| Ex-window | +0.0069 (n 273) | −0.0366 (n 539) | −0.0204 (n 820) |

M4 is worse than HAR-X in the low-VIX tercile in both samples, so the DEVIATIONS Q condition fails as well.

**Calibration.** Mincer–Zarnowitz for M4 not rejected: full W = 2.11 (p = 0.349), ex-window W = 2.63 (p = 0.268). On the ex-window headline sample, M2's MZ rejects (p = 8.6 × 10⁻⁷) and M4's does not. Reported outside Holm; not a ranking claim.

**Final Holm family (§5.5).**

| Test | Full: raw p → Holm p | Ex-window: raw p → Holm p | Decision | Headline |
|---|---|---|---|---|
| Q1b M2 vs M1 | 0.737 → 1.000 | 0.140 → 0.281 | not detected | full |
| Q1c M4 vs M2 | 0.849 → 1.000 | 0.056 → 0.224 | not detected | full |
| Q1d M1 vs M3 | 0.405 → 1.000 | 0.173 → 0.281 | not detected | full |
| Q1d M1 vs M0 | 0.027 → 0.107 | 0.094 → 0.281 | not detected | full |

**Change from Step 9.** M1 vs M0 was provisionally "rejected, favours HAR" on raw p (0.027) with the headline on the ex-window sample. After Holm it is not detected in either sample, so the headline moves to the full sample. The other two provisional rows are unchanged. The Q1 headline (Clark–West, ex-window, p = 0.0009) is outside the Holm family and does not change.

**Files.** `results/mlp_selection.parquet`, `mlp_select_manifest.json`, `mlp_forecasts.parquet` (local only, git-ignored under the data licence — `docs/history_rewrite.md`), `mlp_test_manifest.json`, `mlp_tests.parquet`, `mlp_regimes.parquet`, `holm_final.parquet`, `holm_manifest.json`.

### Step 10b — Unstructured-data pipeline: FOMC statements and minutes

**Scope.** Retrieval and citation only (PREREGISTRATION §2.1, §10 item 6): no sentiment, text features or signals; nothing here feeds M0–M4 or the event study.

**Corpus (snapshot 20261112T071104Z).** 134 scheduled FOMC dates (2010-01-27 → 2026-10-28). Fetched 134 statements (HTML) and 132 minutes (PDF). Not fetched: 2012-10-24 minutes (the PDF URL returns 404; the Fed publishes that meeting's minutes as HTML only – known gap, no substitute source) and 2026-10-28 minutes (not yet released at fetch time; retried on the next run).

**Extraction and storage.** 266 documents in `data/text/documents.parquet` (fixed schema; vote record as fields), extractor 1.2. All 134 statements parse their vote record; from 2026-06-17 the statements give a tally ("by a 9 – 3 vote") and name only dissenters, stored as `vote_tally`. Minutes: two-column reading order, paragraphs rebuilt on line-per-block PDFs (2024+), headers/footers, attendance lists, projection tables and chart labels removed; real compound hyphens kept ("longer-run"), fractions written "3-1/2" as in the statements. Extracted/raw word ratio 0.85–0.91 in every year (the difference is attendance lists, footnotes and tables, by design). A fresh extraction with the committed code reproduces documents.parquet exactly.

**Chunks.** 6,620 chunks (365 statement, 6,255 minutes), paragraph-based, ≤ 300 tokens (median ~239), 1.50M tokens in total.

**Data quality.** 2 flags: `minutes_missing` 2012-10-24 (the known gap above); `not_yet_released` 2026-10-28 (info). No other flags.

**Index.** Qdrant v1.20.1, dense (text-embedding-3-small, 1,536 d) + keyword vectors (IDF weighting in Qdrant), fused by reciprocal rank fusion. Each chunk is indexed with a contextual header (document type and meeting date), because the eval questions name meetings and the chunk text carries no date; chunk text, chunk_ids and labels are unchanged; decided before the first index run. Idempotence: the successful index run upserted 6,620; the second run upserted 0, unchanged 6,620.

**Retrieval evaluation (15 questions labelled before any embedding; 8 statement, 7 minutes; no filters).**

| Mode | recall@5 | hit@5 | MRR@10 | recall@5 statements (8) | recall@5 minutes (7) |
|---|---|---|---|---|---|
| dense | 0.389 | 0.467 | 0.286 | 0.250 | 0.548 |
| hybrid (RRF) | 0.322 | 0.400 | 0.244 | 0.125 | 0.548 |
| keyword only | 0.122 | 0.200 | 0.130 | 0.000 | 0.262 |

Result as measured: retrieval is weak on the statement questions, and fusion with the keyword vector does not improve on dense alone.

**Post-hoc diagnostic (not part of the pre-registered evaluation; labels unchanged).** Each statement question's gold set contains only the statement chunk, but the same meeting's minutes record the same decision or dissent. Reading the top 5 for the 8 statement questions: a chunk from the same meeting that names the dissenter or carries the answer phrase appears in the top 5 for 6 of 8 questions (hybrid) and 5 of 8 (dense), against 1 and 2 of 8 under the pre-registered labels. The low statement scores therefore mostly reflect the single-document-type gold sets rather than missed content. Remaining misses: q07 (March 2022 forward guidance) and q08 (July 2026 dissent size) – no same-meeting chunk with the answer in the top 5; q01 dense ranks other 2010 meetings with the same dissenter first.

**Process notes.** The first eval call ran against an empty collection (a single upsert of 6,620 points exceeded Qdrant's 32 MB request limit); its outputs were discarded before inspection, upserts are now batched (256) and the eval refuses an incomplete index. Embedding cost ≈ USD 0.03 (ESTIMATE from 1.5M tokens at list price; check the OpenAI usage page).

**Limitations.** One officer-list chunk remains at the end of 2024+ minutes; "forwardlooking" ×7 left by the hyphen rule; short fragments ("Core PCE inflation" ×29, the secretary's name ×18) merge into neighbouring chunks. Eval set is small (15) and single-labelled; a future eval should label every chunk that answers a question, across document types – as a new, separately committed set.

### 10c-1/10c-2 Kafka and AWS serverless lake (AWS run 16 Nov 2026)

- Kafka (local, KRaft): 266 documents published; indexer processed 266, DLQ 0; replay of all 266 →
  266 duplicates suppressed, 0 re-uploaded (`results/text/10c_kafka.json`).
- Lambda `vfl-extract` (container image): 266 invocations, 0 errors. Text parity lake vs local
  266/266 identical; chunk parity 6,620 rebuilt = 6,620 stored, row for row
  (`results/text/10c_evidence.json`).
- Athena: 134 statements / 132 minutes across 34 partitions; partition check 7 rows (2020
  statements); at most 20,748 bytes scanned per query, billed at the 10 MB minimum
  (`results/text/10c_athena.json`).
- Everything except the budget stack destroyed the same day; both destroy attempts kept in
  `results/text/10c_destroy.txt` (the first stopped on a missing Glue permission).

### 10c-3 Spark compaction (16 Nov 2026)

- Job: `spark/compact_stats.py` (Codex-written P7j, reviewed by Kakali); run in Docker `spark:4.1.2-java21-python3` (no local Java/winutils).
- Result: 266 docs → 34 (year, doc_type) groups; one curated file per group; exact median (`percentile`, not approx).
- Parity vs pandas (`documents.parquet`): 34/34 PASS (counts/dates exact, mean/median ≤1e-9) → `results/text/10c_spark_stats.parquet`.
- Fix: `check_spark_parity` accepted only a file; Spark writes `stats.parquet` as a folder (`.exists()`).
- Note: the wrong-type test passed on any exception (the stub passed it); assertion narrowed in `e0d8d94` (BUILD_GUIDE Issue 21).

### 10c-4 OpenSearch BM25 + RRF fusion (16 Nov 2026)

- Index `fomc_chunks_v1` (English analyzer, `header^2`): 6,620 chunks; reload idempotent (6,620 created → 6,620 updated). Module: `opensearch_index.py` (Kakali; Claude review fixes typed by Kakali).
- Eval (same 15 committed 10b questions, all; columns recall@5/hit@5/MRR@10): dense 0.389/0.467/0.286 (identical to 10b → wiring check); `bm25_os` 0.367/0.400/0.288 (10b sparse 0.122/0.200/0.130); `fused_os` 0.589/0.667/0.402 (10b hybrid 0.322/0.400/0.244).
- Minutes: fused hit@5 1.00. Statements remain weak (fused hit@5 0.375).
- BM25 ablation (post-hoc diagnostic, 15 questions): the `header^2` boost adds 1–2 hits@5 (English 0.333→0.400; standard 0.267→0.400); analyzer choice makes no recall/hit difference with the boost. 10c config kept; n=15, so directional only.
- Caveats: n=15 (one question ≈ 6.7 pp hit@5); date filters proven by the Docker test only (question file has no filters).
- Incidents: the Docker test wrote 3 docs into the real index (fixed by delete + reindex); eval needs `.env` loaded for `OPENAI_API_KEY`.

### 10c-5 Redis cache + rate limiter (16 Nov 2026)

- `cache.py` (Kakali): key = sha256 of the normalised query (lower-case, whitespace collapsed) + date_from/date_to/doc_type/k/mode, TTL 3600 s; hit/miss/rate-limited counters on `/metrics`; atomic Lua token bucket (capacity 10, refill 5/s) keyed by client IP. Tests Codex (P7f); API wiring Codex, with Claude fixes (fixed test clock, fail-open on Redis errors, 0.25 s timeouts).
- Live (Docker redis 7.4.12): miss 4,013 ms (includes cold start) → hit 37 ms for a case/spacing variant; 15-request burst → 10 allowed, 5 × 429; `/metrics` hits 11, misses 1, hit_rate 0.917, rate_limited 5.
- Tests: 13 passed (cache + API); full suite 249 passed, 1 skipped (excl. test_text.py).
- Caveats: rate limit is per IP, so clients behind one NAT share a bucket. Fixed after this run in `e0d8d94`: the cache key now includes `VFL_INDEX_VERSION` (results were up to 1 h stale after a reindex) and fakeredis moved to `requirements-dev.txt` (BUILD_GUIDE Issues 18–19).
- Incident: the first API rate-limit test used the real clock and failed 2/15 runs (a token refilled if 11 requests took > 0.2 s); fixed with an injected clock (15/15).

**Step 11 process note.** The first live run at `2fc8c30` was invalid: 39 of 45 attempts hit the OpenAI schema error, and no A01–A13 answers were seen. Re-run at the new commit.

**Step 11 A01 smoke note.** A single-question smoke run showed A01 was refused ("could not verify") after two drafts. The draft prompt was changed after the A01 smoke runs; only A01's outcome had been seen at that point, not the full eval. The change forbids model-side arithmetic and tells a retry to remove the rejected item.

**Step 11 – agent eval (pre-registered set, 15 questions × 3 repeats, gpt-5.4-mini-2026-03-17, commit d353ace).** Headline accuracy = exact_match AND refusal_correct (a correct refusal scores exact = True): **0.844 (38/45)**. Tool choice 45/45; refusals 6/6 (advice guard, out-of-range date); forecast lookups 15/15; model comparisons 9/9; event effects 7/9; p-values 1/6. Cost USD 0.064 in total (0.0014 per attempt; list prices as of 2026-11-17); latency p50 4.1 s, p95 6.9 s.

Errors: 2 answers reported the log-scale coefficient (beta) instead of the effect – both values are in the tool output, so the copy-only verifier accepted them; 5 p-value answers ignored the 3 dp rule – 3 were refused after two drafts ("could not verify"), 2 gave the correct value at 6 significant figures and scored as not exact. No number absent from the tool output reached a final answer.

By repeat (pre-registered: each reported separately): repeat 0 = 13/15, repeat 1 = 14/15, repeat 2 = 11/15; tool choice 15/15 in each; cost USD 0.020 / 0.023 / 0.020.

Not fixed: the two verifier limits above (wrong field accepted; p-value precision not enforced) are left open (BUILD_GUIDE Issues 25–26). Fixing them and re-running the same 15 questions would be tuning on the test set, so 0.844 stands as the result.

Process: an earlier run at 2fc8c30 was invalid (structured-output schema rejected by the API; no A01–A13 answers were seen); the reported run is at d353ace.

**Step 12 – drift monitoring.** QLIKE ratio M2/M1 (63 sessions; degraded = 3 consecutive month-ends > 1.10): degraded at 11 of 79 month-ends (2020-06…09, 2021-03, 2021-07…10, 2024-10, 2026-05); latest (2026-10) not degraded. PSI of the four HAR inputs (training deciles to 2019-12-02; trailing 126 sessions, BUILD_GUIDE Issue 27; threshold 0.25) flagged 40–78 of 78 month-ends. A post-hoc check found 126-session windows inside the training period also exceed 0.25 in 46–100% of cases: for persistent regressors this PSI rule cannot separate drift from normal regime variation. Reported as a limit, not tuned; a block-bootstrap null threshold tested on later data would be the fix.

**Step 13 – Airflow DAG and local Kubernetes.** DAG tests 4 passed (a forced audit failure leaves 5
downstream tasks `upstream_failed`; the document stubs run). kubeconform 4/4 valid (Kubernetes
1.37.0). In kind: `/health` 200 on 127.0.0.1:30080; pod runs as uid 10001; a job created from the
drift CronJob completed with the same 11 degraded month-ends as Step 12. The full snapshot DAG run
(`use_snapshot=true`) was not executed. Evidence: `docs/step13_evidence.md`.

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

- **FOMC minutes for 2012-10-24 are missing.** The Fed publishes an HTML minutes page but the
  PDF URL returns 404. The DQ `minutes_missing` flag remains; it is 1 of 133 meetings and no
  retrieval result depends on it.

- **The agent's `search_fomc` answers cannot be fully verified.** The verifier cannot check dates
  inside returned FOMC text, and any number quoted from that text fails the numeric rule. None of
  A01–A15 uses `search_fomc`, so the agent eval is unaffected.

- **The agent's advice filter over-matches "trading day".** A question such as "forecast for the
  first trading day of 2021" is refused as investment advice. The pre-registered guard is unchanged.

---

_Final for the Step 14 release (17 Nov 2026). Later changes are dated entries._
