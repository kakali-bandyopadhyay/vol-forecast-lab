"""Claude Agent SDK orchestration and tool-scoped specialist definitions."""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

from agent.schema import Answer
from agent.v2.policy import MAX_HOPS, SUBAGENT_TOOLS
from agent.v3 import MODEL_ID
from agent.v3.guardrails import (
    hook_matcher,
    post_tool_use_matcher,
    subagent_start_matcher,
    subagent_stop_matcher,
)
from agent.v3.tools import build_sdk_tools, sdk_tool_name

ORCHESTRATOR_MAX_TURNS = 10
VFL_DOMAIN_TOOLS = [
    "mcp__vfl__get_forecast",
    "mcp__vfl__compare_models",
    "mcp__vfl__event_effect",
    "mcp__vfl__search_fomc",
]

ORCHESTRATOR_INSTRUCTIONS = """You are a research-assistant supervisor. Dispatch only forecast, event-effect or FOMC retrieval. Choose the smallest set needed and give each focused subtask to its named specialist; dispatch specialists in the foreground and wait for their returned evidence before answering. Never call domain tools directly or use another agent. Use only supplied typed tool evidence; never invent evidence or give investment advice. Report only quantities requested, never calculate new numbers, and copy each number from evidence using Python f\"{value:.6g}\" precision. Keep dates in YYYY-MM-DD form; leave as_of null unless the question or a get_forecast row gives the date; use horizon \"30 sessions\" and put caveats in limitations. Leave implied_variance and spread null unless evidence contains those exact values. Cite chunk_id only from FOMC retrieval evidence. Return one JSON object matching the v2 Answer fields: as_of, horizon, forecast, implied_variance, spread, text, limitations, citations, refused, and refusal_reason. If evidence is missing or cannot support the answer, refuse."""

SUBAGENT_INSTRUCTIONS = {
    "forecast": "Use only forecast or model-comparison tools. Call the tool that directly answers the assigned task. Return evidence, not prose.",
    "event-effect": "Use only the event-effect tool for CPI or FOMC component estimates. Return evidence, not prose.",
    "fomc-retrieval": "Use only search_fomc. For a statement question, set doc_type to statement and date_from and date_to to the same exact statement date. Do not use minutes. Return evidence, not prose.",
}

AGENT_POLICY_NAMES = {
    "forecast": "forecast",
    "event-effect": "event-effect",
    "fomc-retrieval": "FOMC retrieval",
}


def build_options(recorder: dict, *, results_dir=None) -> ClaudeAgentOptions:
    handlers, server = build_sdk_tools(results_dir=results_dir, recorder=recorder)
    del handlers
    agents = {
        agent_key: AgentDefinition(
            description=f"Specialist for {AGENT_POLICY_NAMES[agent_key]} questions.",
            prompt=SUBAGENT_INSTRUCTIONS[agent_key],
            tools=[sdk_tool_name(tool_name) for tool_name in SUBAGENT_TOOLS[policy_name]],
            model=MODEL_ID,
            maxTurns=MAX_HOPS,
            # Specialists must finish before the orchestrator answers.
            background=False,
        )
        for agent_key, policy_name in AGENT_POLICY_NAMES.items()
    }
    # SDK 0.2.189 registers in-process MCP servers at session scope. The root
    # gets only the Agent built-in and PreToolUse denies its direct MCP calls;
    # each specialist gets only its policy tools from its own definition.
    return ClaudeAgentOptions(
        model=MODEL_ID,
        system_prompt=ORCHESTRATOR_INSTRUCTIONS,
        agents=agents,
        tools=["Agent"],
        # SDK allowed_tools pre-approves MCP use for specialists; PreToolUse
        # still denies root calls and cross-scope specialist calls.
        allowed_tools=["Agent", *VFL_DOMAIN_TOOLS, "StructuredOutput"],
        disallowed_tools=["Agent(claude)", "Agent(general-purpose)"],
        mcp_servers={"vfl": server},
        strict_mcp_config=True,
        hooks={
            "PreToolUse": [hook_matcher(recorder, hop_cap=MAX_HOPS)],
            "PostToolUse": [post_tool_use_matcher(recorder)],
            "SubagentStart": [subagent_start_matcher(recorder)],
            "SubagentStop": [subagent_stop_matcher(recorder)],
        },
        # SDK max_turns counts orchestrator turns; specialist dispatches stay capped
        # separately by the PreToolUse hook using policy.MAX_HOPS.
        max_turns=ORCHESTRATOR_MAX_TURNS,
        setting_sources=[],
        # None discovers CLI/user skills; [] disables discovered skills and plugins.
        skills=[],
        plugins=[],
        output_format={"type": "json_schema", "schema": Answer.model_json_schema()},
    )
