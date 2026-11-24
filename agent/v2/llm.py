"""OpenAI Responses API adapter and generic prompts for the v2 supervisor."""

from __future__ import annotations

import json

from agent.llm import OpenAIAgentLLM, TOOL_SCHEMAS, _usage
from agent.schema import Answer, DraftAnswer, RouteDecision
from pydantic import BaseModel, ConfigDict

from agent.v2.contract import AgentName, Handoff, Plan


class PlanStep(BaseModel):
    """One wire-format plan step (strict structured outputs reject dict maps)."""

    model_config = ConfigDict(extra="forbid")
    agent: AgentName
    subtask: str


class PlanWire(BaseModel):
    """API-side plan: an ordered list of steps, converted to the Plan contract."""

    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStep]


class Resolved(BaseModel):
    """Strict structured result from the memory-aware question rewriter."""

    model_config = ConfigDict(extra="forbid", strict=True)
    standalone_question: str
    used_turns: list[int]
    used_defaults: list[str]


def plan_from_wire(wire: PlanWire) -> Plan:
    """Keep duplicate agents in order so policy.validate_plan can reject them."""
    return Plan(
        agents=[step.agent for step in wire.steps],
        subtasks={step.agent: step.subtask for step in wire.steps},
    )


def _handoff_for_llm(handoff: Handoff) -> dict:
    """Serialize the frozen handoff contract without runner-only telemetry."""
    payload = handoff.model_dump(mode="json")
    inputs = dict(payload.get("inputs", {}))
    inputs.pop("tool_attempts", None)
    payload["inputs"] = inputs
    return payload

PLAN_INSTRUCTIONS = """You are a research-assistant supervisor. Return a plan, not an answer.
Choose the smallest ordered set of distinct specialists needed: forecast for forecast values or
model comparisons; event-effect for event-study estimates; FOMC retrieval for Federal Reserve
statement text. Give each selected specialist a concise, answerable subtask. Use only these names:
forecast, event-effect, FOMC retrieval. Do not invent evidence or include a final answer."""

CONTEXTUALIZE_INSTRUCTIONS = """Rewrite the current user question as a standalone question. Use the supplied memory context only to fill missing model, metric, date, date window, event, or component details. An explicit mention in the current question wins over defaults. Never add numbers other than dates or date-window values that appear in the context or question. Never answer the question. Return the standalone question plus the turn indexes and default keys actually used; do not claim any context was used unless it appears in the supplied context."""

REPLAN_INSTRUCTIONS = """Existing specialists could not fully answer the task. Return a plan that
adds exactly one specialist not already called, with a focused subtask. Do not repeat a called
specialist. If no available specialist can help, return an empty steps list.
Return a plan only; do not answer the user."""

SUBAGENT_INSTRUCTIONS = {
    "forecast": "Use only forecast or model-comparison tools. Call the tool that directly answers the assigned subtask. Do not answer in prose.",
    "event-effect": "Use only the event-effect tool for CPI or FOMC component estimates. Do not answer in prose.",
    "FOMC retrieval": "Use only search_fomc. For a statement question, search statements only: set doc_type to statement and set date_from and date_to to the same exact statement date. Do not use minutes. Return tool evidence, not prose.",
}

DRAFT_V2_INSTRUCTIONS = """Answer only from the supplied typed tool evidence. Never give investment advice.
Use dates only in YYYY-MM-DD form.
Leave as_of null unless the question or a get_forecast row gives the date; never derive a date from snapshot_id.
Report only the quantities the question asks for; do not add p-values, standard errors, counts or dates that were not asked for.
Write every number, p-values included, exactly as Python f\"{value:.6g}\" (no trailing zeros) and copy it from the typed tool evidence.
Never calculate new numbers (no differences, ratios, percentages or averages). Set implied_variance and
spread to null unless the typed tool evidence contains those exact values.
Use horizon \"30 sessions\"; put caveats in limitations.
Only include citations when the typed tool evidence comes from search_fomc.
If a rejection is supplied, remove or correct the rejected item; do not repeat it, and do not mention
the rejection in the answer.
Label forecast variance and event-study quantities as separate measures.
Cite chunk_id only from search_fomc evidence."""


class OpenAISupervisorLLM(OpenAIAgentLLM):
    """Structured plan, tool routing, and answer drafting over the v1 client."""

    def plan(self, question: str) -> tuple[Plan, object]:
        response = self.client.responses.parse(
            model=self.model,
            instructions=PLAN_INSTRUCTIONS,
            input=question,
            text_format=PlanWire,
        )
        if response.output_parsed is None:
            raise ValueError("model returned no structured plan")
        return plan_from_wire(response.output_parsed), _usage(response)

    def contextualize(self, question: str, ctx: dict) -> tuple[Resolved, object]:
        response = self.client.responses.parse(
            model=self.model,
            instructions=CONTEXTUALIZE_INSTRUCTIONS,
            input=json.dumps({"question": question, "memory_context": ctx}),
            text_format=Resolved,
        )
        if response.output_parsed is None:
            raise ValueError("model returned no structured question resolution")
        return response.output_parsed, _usage(response)

    def replan(self, question: str, handoffs: list[Handoff]) -> tuple[Plan, object]:
        called = [handoff.agent for handoff in handoffs]
        payload = {
            "question": question,
            "called_agents": called,
            "handoffs": [_handoff_for_llm(handoff) for handoff in handoffs],
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=REPLAN_INSTRUCTIONS,
            input=json.dumps(payload),
            text_format=PlanWire,
        )
        if response.output_parsed is None:
            raise ValueError("model returned no structured replan")
        return plan_from_wire(response.output_parsed), _usage(response)

    def subagent_route(self, agent: str, task: str, schemas) -> tuple[RouteDecision | None, object]:
        response = self.client.responses.create(
            model=self.model,
            instructions=SUBAGENT_INSTRUCTIONS[agent],
            input=task,
            tools=schemas,
            tool_choice="auto",
        )
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if len(calls) > 1:
            raise ValueError(f"expected at most one tool call, got {len(calls)}")
        usage = _usage(response)
        if not calls:
            return None, usage
        call = calls[0]
        return RouteDecision(tool=call.name, args=json.loads(call.arguments or "{}"), usage=usage), usage

    def draft_v2(
        self,
        question: str,
        handoffs: list[Handoff],
        rejection: str | None = None,
    ) -> tuple[Answer, object]:
        payload = {
            "question": question,
            "handoffs": [_handoff_for_llm(handoff) for handoff in handoffs],
            "rejection": rejection,
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=DRAFT_V2_INSTRUCTIONS,
            input=json.dumps(payload),
            text_format=DraftAnswer,
        )
        if response.output_parsed is None:
            raise ValueError("model returned no structured answer")
        return Answer(**response.output_parsed.model_dump()), _usage(response)


def tool_schemas_for_agent(agent: str) -> list[dict]:
    """Return copies of only the v1 function schemas visible to this agent."""
    from copy import deepcopy

    from agent.v2.policy import SUBAGENT_TOOLS

    allowed = set(SUBAGENT_TOOLS[agent])
    schemas = [deepcopy(schema) for schema in TOOL_SCHEMAS if schema.get("name") in allowed]
    # v1's tool accepts this optional filter; add it to the v2 schema only.
    for schema in schemas:
        if schema["name"] == "search_fomc":
            schema["parameters"]["properties"]["doc_type"] = {
                "type": "string", "enum": ["statement", "minutes"]
            }
    return schemas
