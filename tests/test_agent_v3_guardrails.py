from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.v2.policy import SUBAGENT_TOOLS
from agent import tools as source_tools
from agent.schema import Answer
from agent.v3.agents import VFL_DOMAIN_TOOLS, build_options
from agent.v3.guardrails import (
    guardrail_decision,
    make_pre_tool_use_hook,
    post_tool_use_matcher,
    subagent_start_matcher,
    subagent_stop_matcher,
)
from agent.v3.tools import build_sdk_tools, sdk_tool_name


def test_allowed_tool_passes_for_forecast_agent():
    assert guardrail_decision("mcp__vfl__get_forecast", "forecast", hop_count=0)[0]


@pytest.mark.parametrize(
    ("agent_type", "forbidden"),
    [
        ("forecast", "event_effect"),
        ("event-effect", "get_forecast"),
        ("fomc-retrieval", "event_effect"),
    ],
)
def test_each_specialist_is_denied_a_forbidden_tool(agent_type, forbidden):
    allowed, reason = guardrail_decision(f"mcp__vfl__{forbidden}", agent_type, hop_count=0)
    assert not allowed
    assert "not allowed" in reason


def test_dispatch_at_hop_cap_is_denied():
    allowed, reason = guardrail_decision("Agent", None, hop_count=3, target_agent="forecast")
    assert not allowed
    assert "hop cap" in reason


def test_hook_logs_forbidden_tool_denial():
    recorder = {}
    hook = make_pre_tool_use_hook(recorder)
    result = asyncio.run(hook({
        "tool_name": "mcp__vfl__event_effect", "agent_type": "forecast",
        "tool_input": {"event": "cpi", "component": "on"},
    }, "call-1", None))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert recorder["guardrail_denials"][0]["agent"] == "forecast"


def test_orchestrator_structured_output_is_allowed_without_denial():
    recorder = {}
    hook = make_pre_tool_use_hook(recorder)
    result = asyncio.run(hook({
        "tool_name": "StructuredOutput", "agent_type": None,
        "tool_input": {"json": {"forecast": "0.0025"}},
    }, "structured-1", None))
    assert result == {}
    assert recorder["guardrail_denials"] == []


def test_specialist_structured_output_is_denied():
    allowed, reason = guardrail_decision(
        "StructuredOutput", "forecast", hop_count=0,
    )
    assert not allowed
    assert reason == "StructuredOutput is allowed only for the orchestrator"


def test_null_agent_type_uses_agent_id_mapped_at_subagent_start():
    recorder = {}
    start = subagent_start_matcher(recorder).hooks[0]
    pre_tool_use = make_pre_tool_use_hook(recorder)
    asyncio.run(start({"agent_id": "child-1", "agent_type": "forecast"}, None, None))
    result = asyncio.run(pre_tool_use({
        "tool_name": "mcp__vfl__get_forecast", "agent_type": None, "agent_id": "child-1",
        "tool_use_id": "tool-1", "tool_input": {"as_of": "2020-07-01", "model": "M1"},
    }, "tool-1", None))
    assert result == {}
    assert recorder["tool_contexts"][0]["agent"] == "forecast"
    assert recorder["tool_calls"][0]["agent"] == "forecast"
    assert recorder["tool_calls"][0]["status"] == "pending"


def test_null_agent_type_cross_scope_call_is_denied_and_logged():
    recorder = {}
    start = subagent_start_matcher(recorder).hooks[0]
    pre_tool_use = make_pre_tool_use_hook(recorder)
    asyncio.run(start({"agent_id": "child-1", "agent_type": "forecast"}, None, None))
    result = asyncio.run(pre_tool_use({
        "tool_name": "mcp__vfl__event_effect", "agent_type": None, "agent_id": "child-1",
        "tool_use_id": "tool-2", "tool_input": {"event": "cpi", "component": "on"},
    }, "tool-2", None))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert recorder["guardrail_denials"][0]["agent"] == "forecast"
    assert recorder["guardrail_denials"][0]["reason"] == "forecast is not allowed to call event_effect"


def test_specialist_dispatch_is_forced_to_foreground():
    recorder = {}
    hook = make_pre_tool_use_hook(recorder)
    result = asyncio.run(hook({
        "tool_name": "Agent", "agent_type": None,
        "tool_input": {"subagent_type": "forecast", "run_in_background": True},
    }, "dispatch-1", None))
    assert result["hookSpecificOutput"]["updatedInput"]["run_in_background"] is False


def test_subagent_stop_clears_agent_id_attribution():
    recorder = {}
    start = subagent_start_matcher(recorder).hooks[0]
    stop = subagent_stop_matcher(recorder).hooks[0]
    pre_tool_use = make_pre_tool_use_hook(recorder)
    asyncio.run(start({"agent_id": "child-1", "agent_type": "forecast"}, None, None))
    asyncio.run(stop({"agent_id": "child-1"}, None, None))
    result = asyncio.run(pre_tool_use({
        "tool_name": "mcp__vfl__get_forecast", "agent_type": None, "agent_id": "child-1",
        "tool_input": {"as_of": "2020-07-01", "model": "M1"},
    }, "tool-3", None))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_tools_are_scoped_natively_and_orchestrator_gets_only_agent():
    options = build_options({})
    assert options.tools == ["Agent"]
    assert options.allowed_tools == ["Agent", *VFL_DOMAIN_TOOLS, "StructuredOutput"]
    assert options.disallowed_tools == ["Agent(claude)", "Agent(general-purpose)"]
    assert set(options.agents) == {"forecast", "event-effect", "fomc-retrieval"}
    for definition_name, policy_name in (
        ("forecast", "forecast"), ("event-effect", "event-effect"),
        ("fomc-retrieval", "FOMC retrieval"),
    ):
        assert options.agents[definition_name].tools == [
            sdk_tool_name(name) for name in SUBAGENT_TOOLS[policy_name]
        ]
        assert options.agents[definition_name].mcpServers is None
        assert options.agents[definition_name].background is False
        assert options.agents[definition_name].permissionMode is None
    assert len(VFL_DOMAIN_TOOLS) == 4
    assert options.permission_mode is None
    assert options.mcp_servers.keys() == {"vfl"}
    assert options.setting_sources == []
    assert options.skills == []
    assert options.plugins == []
    assert options.output_format == {
        "type": "json_schema", "schema": Answer.model_json_schema(),
    }


def test_builtin_agent_dispatch_is_denied():
    allowed, reason = guardrail_decision(
        "Agent", None, hop_count=0, target_agent="claude"
    )
    assert not allowed
    assert "only forecast" in reason
    nested, nested_reason = guardrail_decision(
        "Agent", "claude", hop_count=0, target_agent="forecast", agent_id="builtin-1"
    )
    assert not nested
    assert "only the orchestrator" in nested_reason


def test_orchestrator_direct_domain_tool_call_is_denied():
    recorder = {}
    hook = make_pre_tool_use_hook(recorder)
    result = asyncio.run(hook({
        "tool_name": "mcp__vfl__get_forecast", "agent_type": None,
        "tool_input": {"as_of": "2020-07-01", "model": "M1"},
    }, "root-call", None))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert recorder["guardrail_denials"][0]["reason"] == "orchestrator may only dispatch a specialist"


def test_sdk_tool_result_updates_pretool_call_record(monkeypatch):
    recorder = {"tool_calls": [{
        "tool": "get_forecast", "args": {"as_of": "2020-07-01", "model": "M1"},
        "status": "pending", "rows": [], "source": "", "detail": None,
        "agent": "forecast", "agent_id": "child-1", "tool_use_id": "tool-1",
    }]}
    monkeypatch.setattr(source_tools, "get_forecast", lambda **kwargs: {
        "rows": [{"date": "2020-07-01", "model": "M1"}], "source": "fixture",
        "row_keys": ["date", "model"],
    })
    handlers, _server = build_sdk_tools(recorder=recorder)
    result = asyncio.run(handlers[0].handler({"as_of": "2020-07-01", "model": "M1"}))
    assert result["structuredContent"]["status"] == "ok"
    assert len(recorder["tool_calls"]) == 1
    assert recorder["tool_calls"][0]["status"] == "ok"
    assert recorder["tool_calls"][0]["agent"] == "forecast"
    assert recorder["tool_calls"][0]["rows"][0]["date"] == "2020-07-01"


def test_post_tool_result_updates_exact_tool_use_id():
    recorder = {"tool_calls": [
        {"tool": "get_forecast", "args": {"model": "M1"}, "status": "pending",
         "rows": [], "source": "", "tool_use_id": "call-1"},
        {"tool": "get_forecast", "args": {"model": "M1"}, "status": "pending",
         "rows": [], "source": "", "tool_use_id": "call-2"},
    ]}
    post_tool_use = post_tool_use_matcher(recorder).hooks[0]
    asyncio.run(post_tool_use({
        "tool_name": "mcp__vfl__get_forecast", "tool_use_id": "call-2",
        "tool_response": {"structuredContent": {
            "status": "ok", "rows": [{"date": "2020-07-01"}],
            "source": "forecasts.parquet", "detail": None,
        }},
    }, "call-2", None))
    assert recorder["tool_calls"][0]["status"] == "pending"
    assert recorder["tool_calls"][1]["status"] == "ok"
    assert recorder["tool_calls"][1]["source"] == "forecasts.parquet"
    assert recorder["tool_calls"][1]["rows"] == [{"date": "2020-07-01"}]


def _smoke_trace_records():
    trace_path = Path(__file__).resolve().parents[1] / "results" / "agent_v3" / "smoke" / "trace.jsonl"
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_post_tool_result_replays_mcp_content_shape_from_smoke_trace():
    records = _smoke_trace_records()
    result_block = next(
        block
        for record in records
        if record.get("type") == "UserMessage"
        for block in record["message"].get("content", [])
        if block.get("tool_use_id") and any(
            content.get("type") == "text"
            and isinstance(content.get("text"), str)
            and json.loads(content["text"]).get("tool") == "get_forecast"
            for content in block.get("content", [])
        )
    )
    tool_text_blocks = result_block["content"]
    payload = json.loads(tool_text_blocks[0]["text"])
    recorder = {"tool_calls": [{
        "tool": "get_forecast", "tool_use_id": result_block["tool_use_id"],
        "status": "pending", "rows": [], "source": "", "detail": None,
    }]}
    post_tool_use = post_tool_use_matcher(recorder).hooks[0]
    asyncio.run(post_tool_use({
        "tool_name": "mcp__vfl__get_forecast",
        "tool_use_id": result_block["tool_use_id"],
        "tool_response": tool_text_blocks,
    }, result_block["tool_use_id"], None))
    call = recorder["tool_calls"][0]
    assert call["status"] == "ok"
    assert call["rows"] == payload["rows"]
    assert call["source"] == payload["source"]
