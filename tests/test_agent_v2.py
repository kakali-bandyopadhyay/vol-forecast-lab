"""Offline v2 orchestration tests; all LLM responses are deterministic fakes."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import re

import pandas as pd
import pytest
import hashlib

from agent.llm import DRAFT_INSTRUCTIONS
from agent.schema import Answer, Citation, RouteDecision, Usage
from agent.v2.contract import Evidence, Handoff, Plan
from agent.v2.graph import answer_question_v2
from agent.v2.llm import DRAFT_V2_INSTRUCTIONS, tool_schemas_for_agent
from agent.v2.policy import MAX_DRAFTS, MAX_HOPS, MAX_REPLANS, SUBAGENT_TOOLS
from agent.v2.verify_multi import verify_multi


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    dates = ["2020-01-02", "2020-01-03"]
    pd.DataFrame([
        {"date": day, "model": model, "realised_30d": 0.1, "raw_30d": 0.2,
         "floored_30d": 0.214, "floored": True}
        for day in dates for model in ("M0", "M1", "M2", "M3")
    ]).to_parquet(tmp_path / "forecasts.parquet", index=False)
    pd.DataFrame([
        {"date": day, "realised_30d": 0.1, "raw_30d": 0.2,
         "floored_30d": 0.214, "floored": True}
        for day in dates
    ]).to_parquet(tmp_path / "mlp_forecasts.parquet", index=False)
    pd.DataFrame([
        {"model": model, "date": day, "qlike": 0.3, "squared_error": 0.04}
        for model in ("M0", "M1", "M2", "M3") for day in dates
    ]).to_parquet(tmp_path / "losses.parquet", index=False)
    pd.DataFrame([
        {"term": event, "component": "oc", "run": "primary", "beta": beta,
         "se": 0.1, "effect": beta, "effect_ci_low": beta - 0.2,
         "effect_ci_high": beta + 0.2, "p_two_sided": 0.05, "p_holm": 0.1, "n_obs": 10}
        for event, beta in (("cpi", 0.3), ("fomc", 0.4))
    ]).to_parquet(tmp_path / "q3_events.parquet", index=False)
    return tmp_path


class FakeLLM:
    def __init__(self, plans=(), routes=(), drafts=(), replans=()):
        self.plans = deque(plans)
        self.routes = deque(routes)
        self.drafts = deque(drafts)
        self.replans = deque(replans)
        self.plan_calls = 0
        self.route_calls = []
        self.draft_calls = []
        self.replan_calls = 0
        self.model = "gpt-5.4-mini"

    def plan(self, question):
        self.plan_calls += 1
        return self.plans.popleft(), Usage(0, 0)

    def replan(self, question, handoffs):
        self.replan_calls += 1
        return self.replans.popleft(), Usage(0, 0)

    def subagent_route(self, agent, task, schemas):
        self.route_calls.append((agent, [schema["name"] for schema in schemas], schemas))
        route = self.routes.popleft()
        return route, Usage(0, 0)

    def draft_v2(self, question, handoffs, rejection):
        self.draft_calls.append((list(handoffs), rejection))
        return self.drafts.popleft(), Usage(0, 0)


def _plan(*agents):
    return Plan(agents=list(agents), subtasks={agent: f"Handle {agent}" for agent in agents})


def _route(tool, **args):
    usage = Usage(0, 0)
    return RouteDecision(tool=tool, args=args, usage=usage)


def _answer(**kwargs):
    defaults = dict(as_of=None, horizon="30 sessions", forecast=None, implied_variance=None,
                    spread=None, text="", limitations="", source_rows=[], citations=[],
                    refused=False, refusal_reason=None)
    defaults.update(kwargs)
    return Answer(**defaults)


def test_contract_forbids_extra_fields_and_unknown_agent():
    common = dict(task="forecast", inputs={}, status="out_of_scope", hop=1,
                  cost_usd_hop=0.0, cost_usd_so_far=0.0)
    with pytest.raises(Exception):
        Handoff(agent="forecast", evidence=[{"extra": "not permitted"}], unexpected=True, **common)
    with pytest.raises(Exception):
        Handoff(agent="unknown", **common)


def test_v2_draft_prompt_formats_all_numbers_and_keeps_v1_prompt_frozen():
    assert "Leave as_of null unless the question or a get_forecast row gives the date; never derive a date from snapshot_id." in DRAFT_V2_INSTRUCTIONS
    assert "Report only the quantities the question asks for; do not add p-values, standard errors, counts or dates that were not asked for." in DRAFT_V2_INSTRUCTIONS
    assert 'Write every number, p-values included, exactly as Python f"{value:.6g}" (no trailing zeros) and copy it from the typed tool evidence.' in DRAFT_V2_INSTRUCTIONS
    assert "3 decimal places" not in DRAFT_V2_INSTRUCTIONS
    # v1 DRAFT_INSTRUCTIONS as of 709a56f (sha256 of the string); a hash, not `git show`,
    # so the check also works in CI's shallow checkout.
    assert hashlib.sha256(DRAFT_INSTRUCTIONS.encode("utf-8")).hexdigest() == (
        "2ca6967d1013531e0c4d9d502c4cfe9919e1d7198b62f7993dd46f07a0f536a1"
    )


def test_invalid_route_error_is_captured_as_tool_attempt(results_dir):
    class BrokenRoute(FakeLLM):
        def __init__(self):
            super().__init__(plans=[_plan("forecast")])
        def subagent_route(self, *args):
            raise ValueError("malformed tool arguments")

    result = answer_question_v2("forecast", BrokenRoute(), model_name="fake", results_dir=results_dir)
    handoff = result.handoffs[0]
    assert handoff["status"] == "invalid_args"
    assert handoff["inputs"]["tool_attempts"] == [{
        "tool": None, "args": None, "error": "malformed tool arguments",
    }]
    assert result.tools_called == []


def test_single_forecast_route_attaches_verified_source_rows(results_dir):
    llm = FakeLLM(
        plans=[_plan("forecast")],
        routes=[_route("get_forecast", as_of="2020-01-02", model="M1")],
        drafts=[_answer(as_of="2020-01-02", forecast="0.214")],
    )
    result = answer_question_v2("M1 forecast for 2020-01-02", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.answer.forecast == "0.214"
    assert result.answer.source_rows[0]["floored_30d"] == 0.214
    assert result.agents_called == ["forecast"]
    assert result.tools_called == ["get_forecast"]
    assert result.tool_call_ids == ["forecast-h1-t1"]
    assert result.hops == 1 and result.stop_reason == "answered"


def test_two_agents_return_ordered_handoffs_and_draft_sees_both(results_dir):
    llm = FakeLLM(
        plans=[_plan("forecast", "event-effect")],
        routes=[
            _route("get_forecast", as_of="2020-01-02", model="M1"),
            _route("event_effect", event="cpi", component="oc"),
        ],
        drafts=[_answer(as_of="2020-01-02", forecast="0.214", text="CPI beta 0.300")],
    )
    result = answer_question_v2("Forecast and event estimate", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.agents_called == ["forecast", "event-effect"]
    assert result.tools_called == ["get_forecast", "event_effect"]
    assert len(llm.draft_calls[0][0]) == 2
    assert [len(h.evidence) for h in llm.draft_calls[0][0]] == [1, 1]
    assert result.hops == 2 and result.stop_reason == "answered"


def test_tool_scope_violation_is_invalid_args_and_refused(results_dir):
    llm = FakeLLM(plans=[_plan("forecast")], routes=[_route("event_effect", event="cpi", component="oc")])
    result = answer_question_v2("Forecast", llm, model_name=llm.model, results_dir=results_dir)
    assert result.answer.refused
    assert result.answer.refusal_reason == "invalid tool arguments"
    assert result.handoffs[0]["status"] == "invalid_args"
    assert result.hops == 1


def test_out_of_scope_replans_once_to_an_uncalled_agent(results_dir):
    llm = FakeLLM(
        plans=[_plan("forecast")], replans=[_plan("event-effect")],
        routes=[None, _route("event_effect", event="cpi", component="oc")],
        drafts=[_answer(text="CPI beta 0.300")],
    )
    result = answer_question_v2("CPI event estimate", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.agents_called == ["forecast", "event-effect"]
    assert result.replans == 1 and llm.replan_calls == 1
    assert result.stop_reason == "answered"


def test_second_out_of_scope_handoff_refuses_after_replan_limit(results_dir):
    llm = FakeLLM(plans=[_plan("forecast")], replans=[_plan("event-effect")], routes=[None, None])
    result = answer_question_v2("Question no tool can answer", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.answer.refused
    assert result.answer.refusal_reason == "no sub-agent can answer this"
    assert result.replans == MAX_REPLANS
    assert result.hops <= MAX_HOPS


def test_three_hop_cap_prevents_fourth_agent_dispatch(results_dir):
    llm = FakeLLM(
        plans=[_plan("forecast", "event-effect")],
        replans=[_plan("FOMC retrieval")],
        routes=[
            _route("get_forecast", as_of="2020-01-02", model="M1"),
            None,
            None,
        ],
    )
    result = answer_question_v2("Question needs many tools", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.hops <= MAX_HOPS
    assert len(llm.route_calls) <= MAX_HOPS
    assert result.stop_reason in {"max_hops", "refused"}


def test_out_of_range_is_a_clear_refusal(results_dir):
    llm = FakeLLM(plans=[_plan("forecast")], routes=[_route("get_forecast", as_of="2019-12-31", model="M1")])
    result = answer_question_v2("Forecast for 2019-12-31", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.answer.refused
    assert result.answer.refusal_reason.startswith("outside data range")


def test_multievidence_verifier_rejects_unsupported_number_and_retries_once(results_dir):
    llm = FakeLLM(
        plans=[_plan("forecast")], routes=[_route("get_forecast", as_of="2020-01-02", model="M1")],
        drafts=[_answer(as_of="2020-01-02", forecast="9.99"),
                _answer(as_of="2020-01-02", forecast="8.88")],
    )
    result = answer_question_v2("Forecast", llm, model_name=llm.model, results_dir=results_dir)
    assert result.answer.refused and result.answer.refusal_reason == "could not verify"
    assert result.attempts == MAX_DRAFTS
    assert len(llm.draft_calls) == 2


def test_citation_from_non_fomc_evidence_is_rejected():
    evidence = Evidence(tool_call_id="forecast-h1-t1", tool="get_forecast", args={},
                        rows=[{"chunk_id": "forged", "floored_30d": 0.214}], source="forecasts.parquet",
                        data_range=("2020-01-02", "2020-01-03"))
    handoff = Handoff(task="task", agent="forecast", inputs={}, evidence=[evidence], status="ok",
                      hop=1, cost_usd_hop=0, cost_usd_so_far=0)
    answer = _answer(forecast="0.214", citations=[Citation(chunk_id="forged", url="https://example.test")])
    ok, reason = verify_multi(answer, [handoff])
    assert not ok and "not from search_fomc" in reason


def test_date_inside_any_evidence_range_is_accepted():
    evidence = Evidence(tool_call_id="forecast-h1-t1", tool="get_forecast", args={},
                        rows=[{"floored_30d": 0.214}], source="forecasts.parquet",
                        data_range=("2020-01-02", "2020-01-03"))
    handoff = Handoff(task="task", agent="forecast", inputs={}, evidence=[evidence], status="ok",
                      hop=1, cost_usd_hop=0, cost_usd_so_far=0)
    ok, reason = verify_multi(_answer(as_of="2020-01-02", forecast="0.214"), [handoff])
    assert ok, reason


def test_fomc_only_evidence_supports_the_statement_date():
    evidence = Evidence(
        tool_call_id="fomc-retrieval-h1-t1", tool="search_fomc",
        args={"date_from": "2019-07-31", "date_to": "2019-07-31", "doc_type": "statement"},
        rows=[{"chunk_id": "statement-20190731-0000", "event_date": "2019-07-31", "doc_type": "statement"}],
        source="fomc_chunks", data_range=None,
    )
    handoff = Handoff(task="statement", agent="FOMC retrieval", inputs={}, evidence=[evidence],
                      status="ok", hop=1, cost_usd_hop=0, cost_usd_so_far=0)
    answer = _answer(text="The statement dated 2019-07-31 describes the economy.",
                     citations=[Citation(chunk_id="statement-20190731-0000", url="https://example.test")])
    ok, reason = verify_multi(answer, [handoff])
    assert ok, reason


def test_forecast_and_fomc_evidence_support_both_dates():
    forecast = Evidence(
        tool_call_id="forecast-h1-t1", tool="get_forecast",
        args={"as_of": "2020-01-02", "model": "M1"},
        rows=[{"date": "2020-01-02", "model": "M1", "floored_30d": 0.214}],
        source="forecasts.parquet", data_range=("2020-01-02", "2026-10-12"),
    )
    fomc = Evidence(
        tool_call_id="fomc-retrieval-h2-t1", tool="search_fomc",
        args={"date_from": "2019-07-31", "date_to": "2019-07-31", "doc_type": "statement"},
        rows=[{"chunk_id": "statement-20190731-0000", "event_date": "2019-07-31", "doc_type": "statement"}],
        source="fomc_chunks", data_range=None,
    )
    handoffs = [
        Handoff(task="forecast", agent="forecast", inputs={}, evidence=[forecast], status="ok",
                hop=1, cost_usd_hop=0, cost_usd_so_far=0),
        Handoff(task="statement", agent="FOMC retrieval", inputs={}, evidence=[fomc], status="ok",
                hop=2, cost_usd_hop=0, cost_usd_so_far=0),
    ]
    answer = _answer(as_of="2020-01-02", forecast="0.214",
                     text="The related statement is dated 2019-07-31.",
                     citations=[Citation(chunk_id="statement-20190731-0000", url="https://example.test")])
    ok, reason = verify_multi(answer, handoffs)
    assert ok, reason


def test_fomc_only_evidence_rejects_an_invented_date():
    evidence = Evidence(
        tool_call_id="fomc-retrieval-h1-t1", tool="search_fomc",
        args={"date_from": "2019-07-31", "date_to": "2019-07-31"},
        rows=[{"chunk_id": "statement-20190731-0000", "event_date": "2019-07-31"}],
        source="fomc_chunks", data_range=None,
    )
    handoff = Handoff(task="statement", agent="FOMC retrieval", inputs={}, evidence=[evidence],
                      status="ok", hop=1, cost_usd_hop=0, cost_usd_so_far=0)
    answer = _answer(text="This happened on 2018-01-01.")
    ok, reason = verify_multi(answer, [handoff])
    assert not ok
    assert reason == "date 2018-01-01 is not supported by evidence"


def test_event_effect_answer_without_dates_passes():
    evidence = Evidence(
        tool_call_id="event-effect-h1-t1", tool="event_effect",
        args={"event": "cpi", "component": "oc"},
        rows=[{"term": "cpi", "component": "oc", "run": "primary", "beta": 0.3}],
        source="q3_events.parquet", data_range=None,
    )
    handoff = Handoff(task="event estimate", agent="event-effect", inputs={}, evidence=[evidence],
                      status="ok", hop=1, cost_usd_hop=0, cost_usd_so_far=0)
    ok, reason = verify_multi(_answer(text="The beta estimate is 0.300."), [handoff])
    assert ok, reason


def test_projected_cost_cap_stops_before_next_subagent_call(results_dir):
    llm = FakeLLM(plans=[_plan("forecast")], routes=[_route("get_forecast", as_of="2020-01-02", model="M1")])
    # $0.24 plan cost means another projected $0.24 call would exceed the $0.25 cap.
    llm.plan = lambda question: (setattr(llm, "plan_calls", llm.plan_calls + 1) or _plan("forecast"), Usage(320000, 0))
    result = answer_question_v2("Forecast", llm, model_name=llm.model, results_dir=results_dir)
    assert result.stop_reason == "cost_cap"
    assert len(llm.route_calls) == 0


def test_advice_guard_has_no_llm_calls(results_dir):
    llm = FakeLLM()
    result = answer_question_v2("Should I buy this option?", llm, model_name=llm.model,
                                results_dir=results_dir)
    assert result.answer.refused
    assert result.answer.refusal_reason == "investment advice is out of scope"
    assert llm.plan_calls == 0 and not llm.route_calls and not llm.draft_calls


def test_tool_schemas_are_scoped_and_fomc_doc_type_is_agent_selected():
    assert {schema["name"] for schema in tool_schemas_for_agent("forecast")} == set(SUBAGENT_TOOLS["forecast"])
    fomc_schemas = tool_schemas_for_agent("FOMC retrieval")
    assert [schema["name"] for schema in fomc_schemas] == ["search_fomc"]
    doc_type = fomc_schemas[0]["parameters"]["properties"]["doc_type"]
    assert doc_type["enum"] == ["statement", "minutes"]


def test_v2_draft_prompt_matches_v1_with_only_requested_additions():
    expected = DRAFT_INSTRUCTIONS.replace("tool output", "typed tool evidence")
    expected = expected.replace(
        "Use dates only in YYYY-MM-DD form.",
        "Use dates only in YYYY-MM-DD form.\n"
        "Leave as_of null unless the question or a get_forecast row gives the date; never derive a date from snapshot_id.\n"
        "Report only the quantities the question asks for; do not add p-values, standard errors, counts or dates that were not asked for.",
    )
    expected = expected.replace("Write p_two_sided and p_holm values to 3 decimal places.\n", "")
    expected = expected.replace(
        'Write every other number exactly as Python f"{value:.6g}" (no trailing zeros) and copy it from the typed tool evidence.',
        'Write every number, p-values included, exactly as Python f"{value:.6g}" (no trailing zeros) and copy it from the typed tool evidence.',
    )
    expected += (
        "\nLabel forecast variance and event-study quantities as separate measures."
        "\nCite chunk_id only from search_fomc evidence."
    )
    assert DRAFT_V2_INSTRUCTIONS == expected
    assert "floored_30d" not in DRAFT_V2_INSTRUCTIONS
    assert "frozen" not in DRAFT_V2_INSTRUCTIONS.lower()
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", DRAFT_V2_INSTRUCTIONS) is None
    assert re.search(r"(?:statement|minutes)-\d{8}-\d{4}", DRAFT_V2_INSTRUCTIONS) is None


@pytest.mark.parametrize("plan,valid", [
    (_plan("forecast"), True),
    (_plan("forecast", "event-effect", "FOMC retrieval"), True),
    (Plan(agents=[], subtasks={}), False),
    (Plan(agents=["forecast", "forecast"], subtasks={"forecast": "x"}), False),
    (Plan(agents=["forecast"], subtasks={}), False),
    (Plan(agents=["forecast"], subtasks={"forecast": "  "}), False),
])
def test_policy_plan_validation_table(plan, valid):
    from agent.v2.policy import validate_plan

    assert (validate_plan(plan) is None) is valid


def test_policy_validate_plan_rejects_subtask_key_not_in_agents():
    from agent.v2.policy import validate_plan

    plan = Plan(agents=["forecast"], subtasks={"forecast": "Get a value", "event-effect": "Extra task"})
    reason = validate_plan(plan)
    assert reason is not None and "not in plan" in reason


def test_policy_validate_replan_rejects_multiple_agents_and_already_called_agent():
    from agent.v2.policy import validate_replan

    multiple_agents = _plan("forecast", "event-effect")
    already_called = _plan("forecast")
    assert validate_replan(multiple_agents, []) is not None
    assert validate_replan(already_called, ["forecast"]) is not None


@pytest.mark.parametrize("agent,tool,allowed", [
    ("forecast", "get_forecast", True), ("forecast", "event_effect", False),
    ("event-effect", "event_effect", True), ("FOMC retrieval", "search_fomc", True),
    ("unknown", "get_forecast", False),
])
def test_policy_tool_scope_table(agent, tool, allowed):
    from agent.v2.policy import tool_allowed

    assert tool_allowed(agent, tool) is allowed


@pytest.mark.parametrize("status,replans,hops,pending,expected", [
    ("out_of_range", 0, 1, [], "refuse"),
    ("no_data", 0, 1, [], "refuse"),
    ("invalid_args", 0, 1, [], "refuse"),
    ("out_of_scope", 0, 1, [], "replan"),
    ("out_of_scope", 1, 1, [], "refuse"),
    ("out_of_scope", 0, MAX_HOPS, [], "refuse"),
    ("ok", 0, 1, ["event-effect"], "dispatch"),
    ("ok", 0, MAX_HOPS, ["event-effect"], "refuse"),
    ("ok", 0, 1, [], "draft"),
])
def test_policy_next_step_behavior_table(status, replans, hops, pending, expected):
    from agent.v2.policy import next_step

    handoff = Handoff(task="task", agent="forecast", inputs={}, status=status,
                      hop=hops, cost_usd_hop=0, cost_usd_so_far=0)
    state = {"handoffs": [handoff], "replans": replans, "hops": hops, "pending": pending}
    assert next_step(state) == expected


@pytest.mark.parametrize("state,expected", [
    ({"handoffs": [Handoff(task="t", agent="forecast", inputs={}, status="out_of_range", detail="bad date", hop=1, cost_usd_hop=0, cost_usd_so_far=0)]}, "outside data range: bad date"),
    ({"handoffs": [Handoff(task="t", agent="forecast", inputs={}, status="no_data", detail="missing row", hop=1, cost_usd_hop=0, cost_usd_so_far=0)]}, "missing row"),
    ({"handoffs": [Handoff(task="t", agent="forecast", inputs={}, status="invalid_args", detail="bad", hop=1, cost_usd_hop=0, cost_usd_so_far=0)]}, "invalid tool arguments"),
    ({"stop_reason": "max_hops"}, "max hops reached"),
    ({"handoffs": [Handoff(task="t", agent="forecast", inputs={}, status="out_of_scope", detail=None, hop=1, cost_usd_hop=0, cost_usd_so_far=0)]}, "no sub-agent can answer this"),
])
def test_policy_refusal_reason_table(state, expected):
    from agent.v2.policy import refusal_reason

    assert refusal_reason(state) == expected


def test_plan_wire_schema_is_strict_compatible():
    """OpenAI strict structured outputs need every object property required and no dict maps."""
    from openai.lib._pydantic import to_strict_json_schema

    from agent.v2.llm import PlanWire

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert sorted(node.get("required", [])) == sorted(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(to_strict_json_schema(PlanWire))


def test_plan_from_wire_keeps_duplicates_for_policy():
    from agent.v2 import policy
    from agent.v2.llm import PlanStep, PlanWire, plan_from_wire

    plan = plan_from_wire(PlanWire(steps=[
        PlanStep(agent="forecast", subtask="a"),
        PlanStep(agent="forecast", subtask="b"),
    ]))
    assert plan.agents == ["forecast", "forecast"]
    assert policy.validate_plan(plan) is not None
    ok = plan_from_wire(PlanWire(steps=[PlanStep(agent="event-effect", subtask="beta")]))
    assert policy.validate_plan(ok) is None
