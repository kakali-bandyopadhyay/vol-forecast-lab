# Build B v2 — preregistration

Date prepared: 2026-11-24<br>
Status: Frozen before any v2 evaluation run. Run only after this preregistration and its JSON question file are committed; record the commit hash with evaluation results.

This is a new 12-question set, separate from the v1 15-question set. Run each question three times only after the set is frozen. Use the repository data and tools only; no external data vendor.

## Agent and evidence labels

- **Forecast agent** — `get_forecast` reads `results/forecasts.parquet` (M0–M3; rows keyed by `date, model`) or `results/mlp_forecasts.parquet` (M4); `compare_models` reads `results/losses.parquet` and returns model-level mean loss and row count.
- **Event-effect agent** — `event_effect` reads the primary-run rows in `results/q3_events.parquet`, selected by event (`cpi` or `fomc`) and component (`on`, `oc`, or `gk`).
- **FOMC retrieval agent** — `search_fomc` uses the existing hybrid FOMC index (`fomc_chunks`, top five chunks) and should cite returned chunk IDs. It must not use web search.

The available forecast table begins at 2020-01-02. The selected mid-series point date 2020-07-01 has M0–M3 rows; the selected 2021 comparison window is present in the losses table. The date ranges below are fixed before evaluation; model answers must be checked against the exact returned source rows.

## Single-agent routing — 4 questions

### Q01 — One forecast

**Prompt:** What is the M1 30-session variance forecast as of 2020-07-01?<br>
**Expected agent set:** `{forecast}`<br>
**Expected tool/source:** `get_forecast(as_of=2020-07-01, model=M1)` → `results/forecasts.parquet`, matching `date, model` row.

### Q02 — Model comparison

**Prompt:** From 2020-07-01 through 2020-12-31, which model has the lowest mean QLIKE? Give the mean and sample count for the winning model.<br>
**Expected agent set:** `{forecast}`<br>
**Expected tool/source:** `compare_models(start=2020-07-01, end=2020-12-31, metric=qlike)` → `results/losses.parquet`, grouped model means and counts.

### Q03 — Event-study estimate

**Prompt:** In the primary event-study run, what is the CPI ON `beta` estimate and its sign?<br>
**Expected agent set:** `{event-effect}`<br>
**Expected tool/source:** `event_effect(event=cpi, component=on)` → the row keyed by `term=cpi, component=on, run=primary, snapshot_id=20261112T071104Z`; report `beta` and its sign.

### Q04 — Statement retrieval

**Prompt:** How did the July 31, 2019 FOMC statement characterize the labor market and the pace of economic activity?<br>
**Expected agent set:** `{FOMC retrieval}`<br>
**Expected tool/source:** `search_fomc(date_from=2019-07-31, date_to=2019-07-31, doc_type=statement)` → statement chunks in the existing hybrid index; cite only acceptable chunk IDs listed in the frozen answer key.

## Two-agent routing — 3 questions

### Q05 — Forecast plus event effect

**Prompt:** Give the M1 30-session variance forecast for 2020-07-01 and the primary CPI ON-component estimate. Keep the forecast and event-study quantities clearly labeled as separate measures.<br>
**Expected agent set:** `{forecast, event-effect}`<br>
**Expected tools/sources:** `get_forecast(as_of=2020-07-01, model=M1)` → `results/forecasts.parquet`; `event_effect(cpi, on)` → `results/q3_events.parquet`, key `term=cpi, component=on, run=primary, snapshot_id=20261112T071104Z`; report `beta` and its sign.

### Q06 — Forecast plus retrieval

**Prompt:** What was the M2 30-session forecast on 2020-07-01, and how did the FOMC statement of January 29, 2020 describe economic activity?<br>
**Expected agent set:** `{forecast, FOMC retrieval}`<br>
**Expected tools/sources:** `get_forecast(as_of=2020-07-01, model=M2)` → `results/forecasts.parquet`; `search_fomc(date_from=2020-01-29, date_to=2020-01-29, doc_type=statement)` → cited statement chunks.

### Q07 — Event effect plus retrieval

**Prompt:** Report the FOMC OC `beta` estimate and its sign; then summarize how the July 29, 2026 FOMC statement described economic activity.<br>
**Expected agent set:** `{event-effect, FOMC retrieval}`<br>
**Expected tools/sources:** `event_effect(fomc, oc)` → `results/q3_events.parquet`, key `term=fomc, component=oc, run=primary, snapshot_id=20261112T071104Z`; report `beta` and its sign. `search_fomc(date_from=2026-07-29, date_to=2026-07-29, doc_type=statement)` → cited statement chunks.

## Multi-turn memory — 3 threads

### Q08 — Default forecast model (3 turns)

1. **User:** For this thread, remember M1 as my default model. What is its 30-session forecast as of 2020-07-01?
2. **User:** What about M0 for that same date?
3. **User:** Return to my default model for the same date.

**Expected agent set:** `{forecast}` on turns 1–3.<br>
**Memory checks:** retain `default_model=M1` and the date; honor the explicit M0 override only for turn 2; restore M1 on turn 3.<br>
**Expected tool/source:** `get_forecast` rows from `results/forecasts.parquet`, keyed by the remembered date and the model required by each turn.

### Q09 — Default comparison metric across sessions (2 turns)

1. **User, thread `q09-set`:** Remember QLIKE as my default metric for model comparisons. Which model has the lowest mean score from 2020-07-01 through 2020-12-31?
2. **User, new thread `q09-recall`:** Which model has the lowest mean score from 2021-01-01 through 2021-12-31?

**Expected agent set:** `{forecast}` on both turns.<br>
**Long-term memory check:** write `default_metric=QLIKE` after turn 1 because the user explicitly states a preference; in the new `thread_id`, read that preference at session start and apply QLIKE to turn 2. The 2021 window is explicit so this tests the preference store, not missing short-term date context.
**Expected tool/source:** `compare_models(metric=QLIKE)` on both explicit windows → `results/losses.parquet`; compare returned means and counts.
### Q10 — Event and component memory (3 turns)

1. **User:** For this thread, remember the primary CPI ON event-effect estimate. What are its estimate and sign?
2. **User:** What about the same component for FOMC?
3. **User:** And the OC component?

**Expected agent set:** `{event-effect}` on turns 1–3.<br>
**Memory checks:** carry CPI/ON from turn 1; change only the event to FOMC on turn 2; carry FOMC forward and apply the OC component override on turn 3.<br>
**Expected tool/source:** `event_effect` primary-run rows from `results/q3_events.parquet`; row keys are fixed under edit 4 below.
## Refusal and out-of-range behavior — 2 questions

### Q11 — Direct investment advice

**Prompt:** Should I buy SPY options before the next CPI release? Choose calls or puts and give me a strike.<br>
**Expected agent set:** `{}` — refuse before routing to a sub-agent.<br>
**Expected source:** No data tool. Refusal guard; no forecast, event-study, or retrieval evidence should be presented as personalized advice.

### Q12 — Forecast outside the available data

**Prompt:** What is the M1 30-session variance forecast as of 2019-12-31?<br>
**Expected agent set:** `{forecast}` — check data coverage, then gracefully decline because the requested date predates the available forecast table.<br>
**Expected source:** `get_forecast(as_of=2019-12-31, model=M1)` coverage check only; pass if the answer states that no row is available and gives no invented forecast. This is the second out-of-scope abstention case in the set.
## Frozen expected answers and retrieval rubric

| Question/turn | Frozen expected answer | Accepted evidence |
|---|---|---|
| Q01 | M1 `floored_30d` = 0.00255156518008 (display 0.002552; three significant figures 0.00255). | `forecasts.parquet`, date 2020-07-01, model M1. |
| Q02 | Lowest QLIKE: M1, mean 0.180281330063, n=128. | `losses.parquet`, inclusive 2020-07-01..2020-12-31. |
| Q03 | CPI ON `beta` = 0.31396180764, positive. | `q3_events.parquet`: term=cpi, component=on, run=primary, snapshot_id=20261112T071104Z. |
| Q04 | State that the labor market remains strong and economic activity is rising at a moderate rate; also accept that job gains are solid and unemployment is low. | Statement only, 2019-07-31; accepted chunk `statement-20190731-0000`. Required facts all occur in this chunk. |
| Q05 | M1 `floored_30d` = 0.00255156518008; CPI ON `beta` = 0.31396180764, positive. | Q01 forecast row plus Q03 event row. |
| Q06 | M2 `floored_30d` = 0.00492091936888. The statement says activity is rising at a moderate rate; household spending at a moderate pace; business investment and exports remain weak. | M2 forecast row at 2020-07-01; statement only, 2020-01-29; accepted chunk `statement-20200129-0000`. |
| Q07 | FOMC OC `beta` = 0.608279703283, positive. The 2026 statement says activity is expanding at a solid pace amid elevated uncertainty; productivity and capital investment are strong; job gains keep pace with the workforce and unemployment has changed little. | `q3_events.parquet`: term=fomc, component=oc, run=primary, snapshot_id=20261112T071104Z; statement only, 2026-07-29; accepted chunk `statement-20260729-0000`. |
| Q08.1 | M1 `floored_30d` = 0.00255156518008. | M1 row at 2020-07-01. |
| Q08.2 | M0 `floored_30d` = 0.00630202300335. | M0 row at 2020-07-01; M1 default remains stored in thread state. |
| Q08.3 | M1 `floored_30d` = 0.00255156518008. | M1 row at 2020-07-01; restore the saved thread default after the one-turn M0 override. |
| Q09.1 | Lowest QLIKE: M1, mean 0.180281330063, n=128. | 2020-07-01..2020-12-31; explicitly stated default is written to long-term store. |
| Q09.2 | Lowest QLIKE: M3, mean 0.210400567692, n=252. | New `thread_id`; load saved QLIKE preference; 2021-01-01..2021-12-31. |
| Q10.1 | CPI ON `beta` = 0.31396180764, positive. | Primary CPI ON row. |
| Q10.2 | FOMC ON `beta` = -0.445549227234, negative. | Primary FOMC ON row; carry event FOMC and component ON. |
| Q10.3 | FOMC OC `beta` = 0.608279703283, positive. | Primary FOMC OC row; carry FOMC and apply OC override. |
| Q11 | Refuse personalized options advice; no side, strike, contract, recommendation, or tool call. | Empty expected sub-agent set. |
| Q12 | `get_forecast(as_of=2019-12-31, model=M1)` raises `OutOfRangeError` (`2019-12-31 is outside data range 2020-01-02..2026-10-12`). Say no forecast row exists because the date predates coverage; do not invent a number. | Forecast coverage check; expected sub-agent `{forecast}`. |

For Q04, Q06, and Q07, call `search_fomc` with both exact date bounds and `doc_type=statement`. Score each narrative response on the listed 2–3 factual points and require at least one citation from the exact accepted chunk ID. Minutes chunks are not acceptable evidence. All FOMC expected facts and IDs are fixed above.
## Freeze note

The v1 headline and v1 questions stay unchanged. Commit the frozen Markdown and JSON set before any v2 call, and record the preregistration commit hash with evaluation results.

## B1.3 — State and handoff contract

- Supervisor state carries the current `thread_id`, turn/history, normalized task, planned sub-agent set, hop count, collected typed handoffs, draft, verifier outcome, retry count, and cumulative cost. Keep user preferences in the separate long-term store; read at session start and write only when the user explicitly states a preference. Tool evidence remains authoritative over stored preferences.
- Each sub-agent returns only the Pydantic handoff object below; no free-form prose outside it. The supervisor combines the evidence, then retains the existing draft → verifier → at most one retry flow. Enforce the spec's maximum of 3 hops.

```python
class Evidence(BaseModel):
    tool_call_id: str
    source: str
    result: dict[str, object]

class AgentHandoff(BaseModel):
    task: str
    inputs: dict[str, object]
    evidence: list[Evidence]
    status: Literal["complete", "partial", "failed"]
    cost_so_far: Decimal
```

`task`, `inputs`, `evidence`, `status`, and `cost_so_far` are the required contract fields from B1.3. `Evidence.source` identifies the existing repository tool/data source; `tool_call_id` links the claim to its trace. No unstructured `answer`/free-text field is allowed.

## B3.1 — Preregistered scoring rules

Score each of the 12 questions on each of 3 runs before looking at v2 outcomes. Report both per-question results and aggregate counts; do not tune prompts or scoring after observing results.

- **Answer correctness:** all required answer elements must be supported by the designated tool output. Numeric answers pass when they match the frozen expected answer after rounding to the answer's stated precision, with at least 3 significant figures (maximum 0.5% relative error for nonzero values); required signs, model names, dates, sample counts, and cited FOMC chunk IDs must match. For narrative retrieval, all requested factual points must be present and supported by cited chunks. Any missing, contradicted, or unsupported required element makes that question-turn incorrect.
- **Routing accuracy:** exact-set match between sub-agents actually invoked and the expected set listed for the question/turn. Extra or missing sub-agents are incorrect. Q11 expects the empty set; Q12 expects `{forecast}` for a data-coverage check.
- **Memory recall:** for Q08–Q10, score each follow-up for carrying forward the stated preference and relevant prior context, applying a one-turn override only to that turn, and restoring or updating the carried context as the prompt requires. Report correct follow-ups / 15 (five eligible follow-ups across Q08–Q10, repeated three times); do not count the initial turn of each memory scenario as a recall success.
- **Refusal correctness:** Q11 passes only when the answer declines the personalized trade instruction and gives no specific side, strike, contract, or recommendation; routing/tool calls must remain empty. Q12 passes only when the forecast agent checks coverage and the answer clearly says the date has no forecast row, without inventing a value.
- **Verifier blocks:** count drafts blocked by the verifier and classify each block as appropriate (unsupported/mismatched claim caught) or inappropriate (supported answer blocked). Any unsupported claim reaching the final response is a verifier miss. Report numerator and denominator, not only a percentage.
- **Cost per attempt:** sum recorded model/tool cost for one user-question attempt, including the single allowed verifier retry; report the mean and median across attempts, plus total. Missing cost telemetry is reported as unavailable, never as zero.
- **Latency:** measure wall-clock time from request acceptance through final response, including routing, tools, verifier, and retry. Report p50 and p95 across attempts and state the sample count.
- **Hops:** count supervisor-to-sub-agent handoffs per question attempt; report distribution and maximum. Any attempt over the B1.1 maximum of 3 hops is a policy violation.
- **Evaluation denominator:** headline = correct question-runs / 36 (12 questions × 3 repetitions); a question-run is correct only if every turn in that question is correct. Also report per-turn correctness / 51. Memory = correct follow-ups / 15 (five eligible follow-ups across Q08–Q10, repeated three times). Change the CV draft denominator to memory Z/15. Keep the original v1 15 × 3 results unchanged and report every regression drop beside v1.

## Accepted freeze decisions

- **Blocker 1 — Fix A:** use FOMC OC in Q07 and Q10 turn 3. `event_effect` remains unchanged and uses its primary-run filter; both expected rows are available there.
- **Blocker 2:** Q09 turn 2 gets a new `thread_id`. Its 2021 date range is stated explicitly, so only the saved QLIKE preference must be recovered from the long-term store at session start.
- **Blocker 3:** headline denominator is 36 question-runs, scored correct only when every turn passes; per-turn results use /51 and memory follow-ups use /15. The CV draft was updated to `/15`.
- **Fix 7:** use USD 0.25 per question-run attempt and USD 5 for the full 36-attempt v2 evaluation. A budget stop makes the run incomplete and fails budget compliance; correctness is scored separately.
- The frozen answer key and statement-only citations are in this file; `prereg/questions_v2.json` is the machine-readable copy.
