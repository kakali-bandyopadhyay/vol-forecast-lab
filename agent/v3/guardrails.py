"""PreToolUse policy enforcement for SDK orchestrator and sub-agents."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import HookMatcher

from agent.v2.policy import MAX_HOPS, SUBAGENT_TOOLS, tool_allowed

_AGENT_ALIASES = {
    "forecast": "forecast",
    "event-effect": "event-effect",
    "fomc-retrieval": "FOMC retrieval",
    "fomc retrieval": "FOMC retrieval",
}


def policy_agent(agent_type: str | None) -> str | None:
    return _AGENT_ALIASES.get((agent_type or "").strip().lower())


def canonical_tool(tool_name: str) -> str:
    return tool_name.rsplit("__", 1)[-1]


def calling_agent(input_data: dict[str, Any], recorder: dict[str, Any]) -> str | None:
    """Resolve the SDK's agent_type directly or via its stable agent_id."""
    agent_id = input_data.get("agent_id")
    if agent_id:
        # SDK 0.2.189 declares agent_id on PreToolUse inputs. In the observed
        # smoke, agent_type was null, so SubagentStart supplies this ID mapping.
        agent = recorder.get("subagent_agents", {}).get(agent_id)
        if agent:
            return agent
    return policy_agent(input_data.get("agent_type"))


def guardrail_decision(
    tool_name: str,
    agent_type: str | None,
    *,
    hop_count: int,
    hop_cap: int = MAX_HOPS,
    target_agent: str | None = None,
    agent_id: str | None = None,
) -> tuple[bool, str]:
    """Return (allowed, reason); kept pure for offline policy tests."""
    agent = policy_agent(agent_type)
    if canonical_tool(tool_name) == "StructuredOutput":
        if agent is None and agent_type is None and agent_id is None:
            return True, ""
        return False, "StructuredOutput is allowed only for the orchestrator"
    if canonical_tool(tool_name) in {"Agent", "Task"}:
        if agent_id or agent_type is not None:
            return False, "only the orchestrator may dispatch a specialist"
        if policy_agent(target_agent) is None:
            return False, "only forecast, event-effect or FOMC retrieval may be dispatched"
        if hop_count >= hop_cap:
            return False, f"hop cap {hop_cap} reached"
        return True, ""
    if agent is not None:
        name = canonical_tool(tool_name)
        if name not in SUBAGENT_TOOLS.get(agent, ()) or not tool_allowed(agent, name):
            return False, f"{agent} is not allowed to call {name}"
        return True, ""
    return False, "orchestrator may only dispatch a specialist"


def make_pre_tool_use_hook(recorder: dict[str, Any], *, hop_cap: int = MAX_HOPS):
    recorder.setdefault("guardrail_denials", [])
    recorder.setdefault("tool_contexts", [])
    recorder.setdefault("agents_called", [])
    recorder.setdefault("subagent_agents", {})
    recorder.setdefault("tool_calls", [])
    recorder.setdefault("pending_agent_types", [])
    state = {"dispatches": 0}

    async def pre_tool_use(input_data, tool_use_id, context):
        del tool_use_id, context
        tool_name = str(input_data.get("tool_name", ""))
        agent_type = input_data.get("agent_type")
        agent = calling_agent(input_data, recorder)
        agent_id = input_data.get("agent_id")
        args = input_data.get("tool_input", {}) or {}
        is_dispatch = canonical_tool(tool_name) in {"Agent", "Task"}
        selected = None
        mapped = None
        if is_dispatch:
            state["dispatches"] += 1
            selected = args.get("subagent_type") or args.get("agent_type") or args.get("name")
            mapped = policy_agent(str(selected)) if selected else None
        allowed, reason = guardrail_decision(
            tool_name, agent or agent_type, hop_count=state["dispatches"] - 1,
            hop_cap=hop_cap, target_agent=str(selected) if selected else None, agent_id=agent_id,
        )
        if allowed:
            if is_dispatch and mapped:
                recorder["agents_called"].append(mapped)
                recorder["pending_agent_types"].append(mapped)
            recorder["tool_contexts"].append({
                "tool": canonical_tool(tool_name), "agent": agent, "agent_id": agent_id,
                "args": dict(args), "tool_use_id": input_data.get("tool_use_id"),
            })
            if not is_dispatch and agent is not None:
                recorder["tool_calls"].append({
                    "tool": canonical_tool(tool_name), "args": dict(args), "status": "pending",
                    "rows": [], "source": "", "detail": None,
                    "agent": agent, "agent_id": agent_id,
                    "tool_use_id": input_data.get("tool_use_id"),
                })
            if is_dispatch:
                # Agent tool input supports run_in_background. Force foreground
                # execution so the parent receives specialist evidence in-turn.
                foreground_args = {**args, "run_in_background": False}
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": foreground_args,
                }}
            return {}
        denial = {
            "tool": tool_name, "agent": agent or agent_type, "agent_id": agent_id,
            "args": dict(args), "reason": reason,
        }
        recorder["guardrail_denials"].append(denial)
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    return pre_tool_use


def subagent_start_matcher(recorder: dict[str, Any]) -> HookMatcher:
    recorder.setdefault("subagent_agents", {})
    recorder.setdefault("pending_agent_types", [])

    async def subagent_start(input_data, tool_use_id, context):
        del tool_use_id, context
        agent_id = input_data.get("agent_id")
        agent = policy_agent(input_data.get("agent_type"))
        pending = recorder["pending_agent_types"]
        if agent in pending:
            pending.remove(agent)
        elif pending:
            # Some CLI hook events report the generic "claude" agent type.
            # Match its start to the named Agent dispatch that preceded it.
            agent = pending.pop(0)
        if agent_id and agent:
            recorder["subagent_agents"][agent_id] = agent
        return {}

    return HookMatcher(hooks=[subagent_start], on_failure="block")


def subagent_stop_matcher(recorder: dict[str, Any]) -> HookMatcher:
    recorder.setdefault("subagent_agents", {})

    async def subagent_stop(input_data, tool_use_id, context):
        del tool_use_id, context
        agent_id = input_data.get("agent_id")
        if agent_id:
            recorder["subagent_agents"].pop(agent_id, None)
        return {}

    return HookMatcher(hooks=[subagent_stop], on_failure="block")


def post_tool_use_matcher(recorder: dict[str, Any]) -> HookMatcher:
    """Attach tool output to the exact call ID captured by PreToolUse."""
    recorder.setdefault("tool_calls", [])

    def response_payload(response: Any) -> dict[str, Any]:
        def parse_text_blocks(blocks: Any) -> dict[str, Any] | None:
            if not isinstance(blocks, list):
                return None
            for block in blocks:
                if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                    continue
                try:
                    import json

                    decoded = json.loads(block["text"])
                except (TypeError, ValueError):
                    continue
                if isinstance(decoded, dict):
                    return decoded
            return None

        if isinstance(response, dict):
            payload = response.get("structuredContent") or response.get("structured_content")
            if isinstance(payload, dict):
                return payload
            payload = parse_text_blocks(response.get("content"))
            if payload is not None:
                return payload
        elif isinstance(response, list):
            payload = parse_text_blocks(response)
            if payload is not None:
                return payload
        return {"status": "no_data", "rows": [], "source": "", "detail": "missing structured tool result"}

    async def post_tool_use(input_data, tool_use_id, context):
        del context
        call_id = input_data.get("tool_use_id") or tool_use_id
        tool_name = canonical_tool(str(input_data.get("tool_name", "")))
        payload = response_payload(input_data.get("tool_response"))
        entry = next((
            call for call in recorder["tool_calls"]
            if call.get("tool_use_id") == call_id and call.get("tool") == tool_name
        ), None)
        if entry is not None:
            entry.update({
                "status": payload.get("status", "no_data"),
                "rows": payload.get("rows", []),
                "source": payload.get("source", ""),
                "detail": payload.get("detail"),
            })
        return {}

    return HookMatcher(hooks=[post_tool_use], on_failure="block")


def hook_matcher(recorder: dict[str, Any], *, hop_cap: int = MAX_HOPS) -> HookMatcher:
    return HookMatcher(hooks=[make_pre_tool_use_hook(recorder, hop_cap=hop_cap)], on_failure="block")
