# ADR 0001 — Pre-register before any result

**Status:** Accepted · **Date:** 2026-11-12 (tag `v0.1-prereg`)

**Context.** A forecasting study with many models, samples and losses has enough freedom to find a
"significant" result by choice of specification. A retrieval and agent evaluation can be tuned to its
own test questions in the same way.

**Decision.** Freeze `prereg/PREREGISTRATION.md` (questions Q1–Q3, models M0–M4, losses, tests,
Holm families, the §8 reading rules) and tag it before any model fit. Write the 15 retrieval questions
and the 15 agent questions, with answers, before the first index or agent run. Log every later
departure in `prereg/DEVIATIONS.md` with what it cost the claim.

**Alternatives rejected.** Exploratory analysis first, write-up later (no protection against
specification search); hold-out set only (does not stop changing the test after seeing it).

**Consequences.** Misses are reported as misses: hybrid retrieval lost to dense in Step 10b, the agent's
two verifier limits stay open rather than being tuned on the test set (BUILD_GUIDE Issues 25–26), and
§8's pre-committed wording for Q1c was overridden only towards a weaker claim. One rule — the
Feb–Apr 2020 window rule — was fixed after the full-sample Clark–West p = 0.0597 was known; it was
pushed (`5c718a1`) before any ex-window number was computed, and FINDINGS says so.

**Evidence.** Tag `v0.1-prereg`; DEVIATIONS entries dated before each affected run (I–S before any M4
forecast); FINDINGS process notes on the invalid first agent run and the A01 smoke change.

**Revisit if.** A new question is added — it needs its own frozen protocol and a new, separately
committed eval set.
