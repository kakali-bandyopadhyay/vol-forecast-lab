# FINDINGS — `vol-forecast-lab`

What the build actually found, as distinct from what the pre-registration predicted. Numbers here
come from committed scripts; anything not yet computed is absent rather than estimated.

**Status:** Steps 1–7 complete. Q1 is answered below as a non-rejection; Q2 and Q3 are not yet run.

---

## Results

### Step 7 — Q1: does VIX-implied variance add information beyond HAR?

Test period 2020-01-02 → 2026-10-12: 1,703 forecasts from 82 refit blocks (81 × 21 sessions plus a
final block of 2), HAC lag 21. Produced by `scripts/run_eval.py`; the panel hash, F_min and the
Diebold–Mariano convention are recorded in `results/eval_manifest.json`.
Its `code_commit` field names the revision checked out when the script last ran, which is not
necessarily the commit carrying this file.

**Not detected.** Clark–West does not reject at 5% (t = 1.557, one-sided p = 0.0597). The §8 reading
is "incremental information not detected at this sample size", not "no incremental information",
and the interval carries the claim:

> mean f_t = +4.87 × 10⁻⁶, 95% interval [−1.26 × 10⁻⁶, +1.10 × 10⁻⁵]

The interval spans zero, so neither a small gain nor a small loss for HAR-X is excluded here.

| Test | Pair | Statistic | p-value | Reading |
|---|---|---|---|---|
| Clark–West, one-sided (headline) | M2 vs M1 | 1.557 | 0.0597 | Not rejected at 0.05 |
| Diebold–Mariano on QLIKE | M2 vs M1 | 0.336 | 0.737 | No difference detected; sign favours M1 |
| Diebold–Mariano on QLIKE | M1 vs M0 | −2.215 | 0.0268 | Favours HAR; Holm pending |
| Mincer–Zarnowitz | M0 | 58.81 | 1.7 × 10⁻¹³ | Calibration rejected |
| Mincer–Zarnowitz | M1 | 2.46 | 0.293 | Not rejected |
| Mincer–Zarnowitz | M2 | 0.31 | 0.858 | Not rejected |

**Diebold–Mariano sign convention:** d = loss(first) − loss(second), so a negative statistic favours
the first model.

**The two losses disagree, and that is the substantive finding.** QLIKE ranks HAR ahead of HAR-X;
MSE ranks HAR-X ahead. Clark–West is built on squared errors and therefore agrees with MSE — its
positive mean favours HAR-X. §8 pre-commits to reporting both and saying which periods drive the
gap, rather than choosing between them.

| Model | mean QLIKE | mean MSE |
|---|---|---|
| M0 | 0.508748 | 7.635 × 10⁻⁵ |
| M1 HAR | 0.413265 | 4.705 × 10⁻⁵ |
| M2 HAR-X | 0.420345 | 4.545 × 10⁻⁵ |

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

**Holm:** deferred. It is applied once, when all four §5.5 tests exist, after Steps 8 and 10. The
Diebold–Mariano p-values above are raw and no adjusted value is quoted here.

**Feb–Apr 2020:** the §8 check for whether a result is driven by that window is **not** computed at
Step 7. It is reported alongside the regime tables at Step 9, which need the same split for the
spread and premium results. Nothing above should be read as having excluded that window.

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
