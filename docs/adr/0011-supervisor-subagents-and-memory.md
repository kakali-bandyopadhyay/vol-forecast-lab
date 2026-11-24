# ADR 0011 — Supervisor with tool-scoped sub-agents, typed handoffs and scoped agent memory

**Status:** Accepted, with known limits · **Date:** 2026-11-25 (Build B, agent v2)

**Context.** The v1 agent (ADR 0007) is one LangGraph loop over four tools. Two new needs: questions that
need two evidence sources in one answer (e.g. a forecast plus what the FOMC statement said), and memory –
follow-ups inside a conversation and user preferences across conversations. Both must keep the v1
guarantee that no number absent from tool output reaches a final answer, and must stay inside a cost and
step budget.

**Decision.**
1. _Supervisor + sub-agents._ An LLM supervisor proposes a plan of 1–3 sub-agents; a hand-written policy
   validates it (known agents only, no duplicates, max 3 hops, max 1 replan that adds exactly one new
   agent). Three sub-agents – forecast, event-effect, FOMC retrieval – are LangGraph subgraphs, each
   bound only to its own tools, with a run-time `tool_allowed` check as a second line.
2. _Typed handoff._ Sub-agents return a Pydantic `Handoff` (task, agent, inputs, evidence with
   tool-call ids, status, detail, hop, cost this hop, cost so far) – never free text. The draft step sees
   only typed evidence; the copy-only verifier checks the final answer against all evidence of the turn;
   one retry, then refuse.
3. _Memory scope._ Thread memory = SQLite checkpointer keyed by `thread_id`. Long-term memory = SQLite
   store per user, holding only two argument defaults (`default_model`, `default_metric`). Writes only
   from explicit statements (no question, no negation, a write cue, exactly one value); values validated
   on write and on read; precedence explicit in the question > thread > user. The question rewriter sees
   the last 3 turns' questions, resolved questions and tool arguments – never earlier answers. Memory can
   change which tool arguments are used, never a number in the answer.
4. _Guardrails logged per turn:_ hops, replans, cost per hop and in total (USD 0.25 per attempt, USD 5 per
   evaluation), verifier rejections with reasons.

**Alternatives rejected.**
- _Single agent with more tools._ One prompt choosing among every tool gives no scope boundary (any step
  can call any tool), no per-capability cost/latency attribution, and tool-choice errors grow with the
  toolset. Kept as the v1 baseline for comparison instead.
- _Swarm / peer-to-peer handoffs._ No single place to enforce hop and cost caps or to collect evidence
  for one verification; harder to trace.
- _Free-text sub-agent replies._ The verifier could not tie a number to a tool call, and text from a
  retrieval agent could carry instructions into the next step.
- _LLM-decided memory writes._ In review, rule-free parsing wrote preferences from questions ("Is M2 my
  default model?"), negations and "switch from M1 to M3" (saved M1). Explicit-only rules fixed all four.
- _Storing past answers in memory._ Stale numbers would re-enter answers without passing the verifier.

**Consequences.** Pre-registered v2 set (12 × 3, frozen `1c9922a`): headline 20/36, routing 46/51,
memory follow-ups 9/15, refusals 6/6, 0 unverified numbers in answers, USD 0.0023 per turn, p50 9.5 s /
p95 15.7 s, max 2 hops. Costs of the design: v1 regression on v2 latency p50 7.5 s vs 4.1 s and USD 0.0017
vs 0.0014 per attempt (extra planning call); headline 30/45 vs 38/45, explained by answer format (beta vs
effect, full-precision p-values), not routing (tool choice 45/45). Limits found: the verifier blocks
written dates (16 inappropriate blocks), it does not check labels, implicit follow-ups ("the same
component for FOMC") were not resolved (0/6 while explicit preferences scored 9/9), and a retrieval-backend
error escaped as an exception rather than a typed `Handoff` status.

**Evidence.** `results/agent_v2/eval_v2_summary.json`, `regression_summary_run1–3.json`;
`prereg/PREREGISTRATION_v2.md`, `prereg/DEVIATIONS.md` (T–Z); `docs/langsmith/`; FINDINGS "Agent v2".

**Revisit if.** A date- and label-aware verifier and a standalone-rewrite step are built – evaluate them
on a new held-out question set (v3), not on this one; or a fourth capability makes the plan space large
enough that a learned router beats the validated LLM plan.
