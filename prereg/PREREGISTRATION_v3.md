# Agent v3 SDK lean evaluation — pre-registration draft

**Status:** Draft for Kakali's review and commit before any live run. No results are included here.

- **Scored set:** Turn 1 of each frozen question Q01–Q12 in `prereg/questions_v2.json` (12 turns total). Memory follow-up turns are excluded.
- **Metrics and scorer:** Headline correct/12, routing, tool arguments, refusals, guardrail denials, verifier blocks, cost per turn (median and total), and p50/p95 latency. Numeric, routing, and argument checks use the unchanged `agent.v2.scoring.numeric_match`, `routing_ok`, and `args_match` helpers.
- **Model:** `claude-haiku-4-5-20251001`.
- **Temperature:** The installed SDK does not expose a temperature option; use and record the SDK default.
- **Run count and hop cap:** One run; maximum three specialist hops/turns, from `agent.v2.policy.MAX_HOPS`.
- **Comparison basis:** The same 12 first turns from all three existing v2 runs, reported separately by run. The v2 parquet is read only.

v2 used OpenAI models and v3 uses Claude: the comparison is framework + model together, not a framework benchmark.

No tuning after the run.

Before registration, live plumbing smokes on Q01 were run to confirm sub-agent tool calls pass the guardrail; they are not scored and their results are not used.

## Fixed before the scored run
- **Answer format:** the orchestrator returns the v2 Answer schema through the SDK structured-output tool; numeric fields are strings.
- **Parser:** exactly two coercions are applied and recorded per turn (`answer_coerced`): `limitations` given as a list is joined with "; ", and a missing `source_rows` becomes `[]`. Any other mismatch is recorded as a refusal with reason "unparseable answer".
- **Agent settings:** specialists run in the foreground; each specialist sees only its own tools; the orchestrator may dispatch only the three named specialists and call the structured-output tool; the PreToolUse hook enforces this and logs denials. Orchestrator turn limit 10 (separate from the specialist hop cap).
- **Isolation:** no user or project settings, skills or plugins are loaded (`setting_sources=[]`).
- **Smokes:** eight live plumbing smokes on Q01 found and fixed integration issues (background sub-agents, MCP scope, tool permissions, result capture, answer format, structured-output permission). No question text, expected value, scorer or verifier was changed as a result.
