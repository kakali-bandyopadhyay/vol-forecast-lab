# ADR 0012 — Lean Claude Agent SDK port

**Status:** Accepted, with evaluation limits · **Date:** 2026-11-25

## Context

The Morgan Stanley PT-JR043379 description names the Claude Agent SDK as an example framework for agentic workflows. The existing v2 supervisor uses LangGraph, three tool-scoped specialists, a copy-only verifier, and explicit-only memory. A preregistered lean port was requested to compare one first-turn run against the existing v2 first-turn runs without changing the v2 evaluation.

The v3 code and preregistration were committed as 1eece83 before the scored run. The evaluation used the first turn of Q01–Q12 and claude-agent-sdk 0.2.189.

## Decision

Keep v2 intact and add v3 as a separate implementation. Use the SDK client for an orchestrator and the forecast, event-effect, and FOMC retrieval specialists. Wrap the existing VFL functions as in-process SDK MCP tools. Define each specialist's tool list from the v2 policy and apply a PreToolUse guardrail as the audited second layer. Reuse the v2 verifier and scoring helpers unchanged.

The orchestrator dispatches specialists in the foreground and uses the SDK structured-output option with the v2 Answer schema. The parser permits only two documented shape adjustments: join a string list in limitations with a semicolon separator, and supply an empty source_rows list when the field is missing. Other schema mismatches remain refusals. The session has a ten-turn orchestrator cap and the policy hop cap for specialist dispatches.

## Alternatives considered

- Replacing or extending the v2 LangGraph path would make the comparison less isolated and increase the scope of the port.
- Porting memory and follow-up turns would exceed the lean first-turn evaluation.
- Adding LangSmith tracing or tuning prompts after the run was outside the frozen plan.
- Hiding session-level in-process MCP tools from the orchestrator is not supported by this SDK path; the runtime hook denies its domain-tool calls.

## Consequences and evidence

The scored v3 run returned 2/12 correct, with routing 12/12, arguments 6/12, six verifier blocks, one guardrail denial, USD 0.25069815 total, and latency p50/p95 of 13.47/21.26 seconds. Each of the three v2 first-turn runs is reported separately in FINDINGS.md and results/agent_v3/v2_first_turn_baseline.json.

The first v3 run was invalidated by a Docker/Qdrant outage. Its outputs remain in results/agent_v3/eval_v3_summary_run1_qdrant_down.json and eval_v3_turns_run1_qdrant_down.parquet; the full set was rerun with the same code after the fomc_chunks collection was confirmed at 6,620 points. DEVIATIONS AA records both attempts.

The comparison combines the framework and model: v2 used OpenAI and v3 used Claude. It is not a framework benchmark. This is one run, with no memory and no LangSmith traces. SDK 0.2.189 exposes session-level in-process MCP tools to the orchestrator, so the hook is necessary to deny root and cross-scope calls.

## Revisit when

Revisit this decision if the SDK can natively scope in-process MCP tools per agent, if the evaluation is expanded to repeated runs or memory follow-ups, or if a planned LangSmith evaluation is approved.
