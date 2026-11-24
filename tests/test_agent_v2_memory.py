"""Offline Build B2 memory, persistence, and turn-isolation tests."""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

import pandas as pd
import pytest
from pydantic import BaseModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.schema import Answer, RouteDecision, Usage
from agent.v2.contract import Evidence, Handoff, Plan
from agent.v2.graph import answer_question_v2, build_supervisor_graph
from agent.v2.llm import Resolved
from agent.v2.memory import (
    PrefWrite,
    load_user_prefs,
    parse_preference,
    save_user_pref,
)
from agent.v2.persistence import checkpoint_serializer, open_memory


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


class MemoryFakeLLM:
    """Small deterministic LLM fake that resolves defaults from supplied context."""

    model = "gpt-5.4-mini"
    costed_usage = Usage(10, 10)

    def __init__(self, *, repeat_old_number=False):
        self.context_calls: list[tuple[str, dict]] = []
        self.plan_questions: list[str] = []
        self.route_calls: list[tuple[str, dict]] = []
        self.draft_calls = 0
        self.repeat_old_number = repeat_old_number

    def contextualize(self, question, ctx):
        self.context_calls.append((question, json.loads(json.dumps(ctx, default=str))))
        defaults = ctx.get("defaults", {})
        used_defaults: list[str] = []
        used_turns: list[int] = []

        model_match = re.search(r"\bM[0-4]\b", question, re.IGNORECASE)
        model = model_match.group(0).upper() if model_match else defaults.get("default_model")
        if model_match is None and model is not None:
            used_defaults.append("default_model")

        metric_match = re.search(r"squared[\s_-]*error|qlike|mse", question, re.IGNORECASE)
        metric = None
        if metric_match:
            metric = "qlike" if metric_match.group(0).lower() == "qlike" else "squared_error"
        elif defaults.get("default_metric"):
            metric = defaults["default_metric"]
            used_defaults.append("default_metric")

        date_match = re.search(r"\d{4}-\d{2}-\d{2}", question)
        date_value = date_match.group(0) if date_match else None
        if date_value is None and re.search(r"\b(?:that date|same date|and for|what about)\b", question, re.IGNORECASE):
            for turn in reversed(ctx.get("turns", [])):
                prior = turn.get("resolved", "")
                prior_date = re.search(r"\d{4}-\d{2}-\d{2}", prior)
                if prior_date:
                    date_value = prior_date.group(0)
                    if turn.get("turn") is not None:
                        used_turns.append(turn["turn"])
                    break

        standalone = question
        if model and model_match is None:
            standalone += f" model {model}"
        if metric and metric_match is None:
            standalone += f" metric {metric}"
        if date_value and date_match is None:
            standalone += f" as of {date_value}"
        return Resolved(
            standalone_question=standalone,
            used_turns=used_turns,
            used_defaults=used_defaults,
        ), self.costed_usage

    def plan(self, question):
        self.plan_questions.append(question)
        agent = "event-effect" if "cpi" in question.lower() else "forecast"
        if "compare" in question.lower():
            agent = "forecast"
        return Plan(agents=[agent], subtasks={agent: question}), self.costed_usage

    def replan(self, question, handoffs):
        raise AssertionError("these tests do not expect a replan")

    def subagent_route(self, agent, task, schemas):
        if agent == "event-effect":
            args = {"event": "cpi", "component": "oc"}
            tool = "event_effect"
        elif "compare" in task.lower():
            start_end = re.findall(r"\d{4}-\d{2}-\d{2}", task)
            metric = "squared_error" if "squared_error" in task.lower() else "qlike"
            args = {"start": start_end[0], "end": start_end[1], "metric": metric}
            tool = "compare_models"
        else:
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", task)
            model_match = re.search(r"\bM[0-4]\b", task, re.IGNORECASE)
            args = {
                "as_of": date_match.group(0) if date_match else "2020-01-02",
                "model": model_match.group(0).upper() if model_match else "M1",
            }
            tool = "get_forecast"
        self.route_calls.append((tool, args))
        return RouteDecision(tool=tool, args=args, usage=self.costed_usage), self.costed_usage

    def draft_v2(self, question, handoffs, rejection):
        self.draft_calls += 1
        if self.repeat_old_number and "cpi" in question.lower():
            return Answer(forecast="0.214"), self.costed_usage
        evidence = [item for handoff in handoffs for item in handoff.evidence]
        if evidence and evidence[-1].tool == "get_forecast":
            row = evidence[-1].rows[0]
            return Answer(as_of=str(row["date"]), forecast=f"{row['floored_30d']:.3f}"), self.costed_usage
        if evidence and evidence[-1].tool == "compare_models":
            return Answer(text="Comparison completed."), self.costed_usage
        if evidence and evidence[-1].tool == "event_effect":
            return Answer(text="CPI beta is 0.300."), self.costed_usage
        return Answer(text="No answer."), self.costed_usage


def _run(question, llm, results_dir, *, thread_id, user_id="user-1", saver=None, store=None, **kwargs):
    return answer_question_v2(
        question,
        llm,
        model_name=llm.model,
        results_dir=results_dir,
        thread_id=thread_id,
        user_id=user_id,
        checkpointer=saver,
        store=store,
        **kwargs,
    )


def test_two_turn_same_thread_resets_handoffs_and_cost_to_current_turn(results_dir):
    llm = MemoryFakeLLM()
    saver, store = InMemorySaver(), InMemoryStore()
    first = _run("M1 forecast for 2020-01-02", llm, results_dir,
                 thread_id="turn-reset", saver=saver, store=store)
    second = _run("M1 forecast for 2020-01-03", llm, results_dir,
                  thread_id="turn-reset", saver=saver, store=store)

    assert first.turn == 1 and second.turn == 2
    assert len(first.handoffs) == len(second.handoffs) == 1
    assert second.handoffs[0]["evidence"][0]["args"]["as_of"] == "2020-01-03"
    assert second.cost_usd == pytest.approx(4 * 52.5e-6)
    assert first.cost_usd == pytest.approx(3 * 52.5e-6)
    assert len(llm.plan_questions) == 2
    assert len(llm.context_calls) == 1


def test_previous_turn_number_cannot_pass_without_current_evidence(results_dir):
    llm = MemoryFakeLLM(repeat_old_number=True)
    saver, store = InMemorySaver(), InMemoryStore()
    _run("M1 forecast for 2020-01-02", llm, results_dir,
         thread_id="no-answer-leak", saver=saver, store=store)
    result = _run("What is the CPI effect?", llm, results_dir,
                  thread_id="no-answer-leak", saver=saver, store=store)

    assert result.answer.refused and result.answer.refusal_reason == "could not verify"
    assert result.handoffs[0]["evidence"][0]["tool"] == "event_effect"
    assert len(result.handoffs) == 1
    assert "0.214" not in json.dumps(llm.context_calls[-1][1])


def test_thread_model_default_precedence_and_isolation(results_dir):
    llm = MemoryFakeLLM()
    saver, store = InMemorySaver(), InMemoryStore()
    thread_a = "thread-a"
    first = _run(
        "Remember M2 as my default model for this thread. What is its forecast as of 2020-01-02?",
        llm, results_dir, thread_id=thread_a, saver=saver, store=store,
    )
    second = _run("And for 2020-01-03?", llm, results_dir,
                  thread_id=thread_a, saver=saver, store=store)
    third = _run("What about M3 for that date?", llm, results_dir,
                 thread_id=thread_a, saver=saver, store=store)
    fourth = _run("And for 2020-01-02?", llm, results_dir,
                  thread_id=thread_a, saver=saver, store=store)
    isolated = _run("What is the forecast as of 2020-01-02?", llm, results_dir,
                    thread_id="thread-b", saver=saver, store=store)

    assert [first.handoffs[0]["evidence"][0]["args"]["model"],
            second.handoffs[0]["evidence"][0]["args"]["model"],
            third.handoffs[0]["evidence"][0]["args"]["model"],
            fourth.handoffs[0]["evidence"][0]["args"]["model"]] == ["M2", "M2", "M3", "M2"]
    assert second.memory_reads == ["default_model"]
    assert "M2" not in isolated.resolved_question
    assert isolated.handoffs[0]["evidence"][0]["args"]["model"] == "M1"


def test_user_metric_default_crosses_threads_but_not_users(results_dir):
    llm = MemoryFakeLLM()
    saver, store = InMemorySaver(), InMemoryStore()
    saved = _run(
        "Remember squared error as my default metric. Compare models from 2020-01-02 to 2020-01-03.",
        llm, results_dir, thread_id="metric-a", saver=saver, store=store, user_id="same-user",
    )
    same_user = _run(
        "Compare models from 2020-01-02 to 2020-01-03.", llm, results_dir,
        thread_id="metric-b", saver=saver, store=store, user_id="same-user",
    )
    other_user = _run(
        "Compare models from 2020-01-02 to 2020-01-03.", llm, results_dir,
        thread_id="metric-c", saver=saver, store=store, user_id="other-user",
    )

    assert saved.memory_writes == [{"scope": "user", "key": "default_metric", "value": "squared_error"}]
    assert same_user.handoffs[0]["evidence"][0]["args"]["metric"] == "squared_error"
    assert "default_metric" in same_user.memory_reads
    assert other_user.handoffs[0]["evidence"][0]["args"]["metric"] == "qlike"
    assert "default_metric" not in other_user.memory_reads


def test_poisoned_store_values_and_unknown_keys_are_ignored():
    store = InMemoryStore()
    store.put(("prefs", "poison-user"), "default_metric", {"value": "0.42"})
    store.put(("prefs", "poison-user"), "unknown_key", {"value": "0.42"})
    assert load_user_prefs(store, "poison-user") == {}
    assert parse_preference("Remember 0.42 as my default metric.") is None


@pytest.mark.parametrize("question,expected", [
    ("Remember M2 as my default model.", ("user", "default_model", "M2")),
    ("Set the default metric to squared error.", ("user", "default_metric", "squared_error")),
    ("Remember M2 as my default model for this thread.", ("thread", "default_model", "M2")),
    ("What about M0 as my default model?", None),
    ("Do not remember M2 as my default model.", None),
    ("Remember the CPI ON estimate.", None),
    ("Remember the M2 estimate.", None),
])
def test_parse_preference_table(question, expected):
    write = parse_preference(question)
    actual = None if write is None else (write.scope, write.key, write.value)
    assert actual == expected


def test_advice_on_second_turn_is_refused_before_contextualize(results_dir):
    llm = MemoryFakeLLM()
    saver, store = InMemorySaver(), InMemoryStore()
    _run("Remember M2 as my default model for this thread. Forecast 2020-01-02.",
         llm, results_dir, thread_id="advice-thread", saver=saver, store=store)
    context_calls_before = len(llm.context_calls)
    advice = _run("Should I buy an option?", llm, results_dir,
                  thread_id="advice-thread", saver=saver, store=store)
    assert advice.answer.refused
    assert advice.answer.refusal_reason == "investment advice is out of scope"
    assert len(llm.context_calls) == context_calls_before
    assert advice.turn == 2


def test_cost_cap_path_records_exactly_one_turn(results_dir):
    llm = MemoryFakeLLM()
    saver, store = InMemorySaver(), InMemoryStore()
    result = _run("M1 forecast for 2020-01-02", llm, results_dir,
                  thread_id="cost-cap-thread", saver=saver, store=store, max_cost_usd=0.001)
    assert result.stop_reason == "cost_cap"
    assert result.answer.refused
    assert result.turn == 1
    snapshot = build_supervisor_graph(
        MemoryFakeLLM(), model_name=llm.model, results_dir=results_dir,
        checkpointer=saver, store=store,
    ).get_state({"configurable": {"thread_id": "cost-cap-thread", "user_id": "user-1"}})
    assert len(snapshot.values["turns"]) == 1


def test_open_memory_reopens_user_preference_and_checkpoint_turn(results_dir, tmp_path):
    saver, store, close = open_memory(tmp_path)
    llm = MemoryFakeLLM()
    result = _run(
        "Remember M2 as my default model. Forecast as of 2020-01-02.",
        llm, results_dir, thread_id="sqlite-thread", saver=saver, store=store, user_id="sqlite-user",
    )
    close()

    saver2, store2, close2 = open_memory(tmp_path)
    try:
        assert load_user_prefs(store2, "sqlite-user")["default_model"] == "M2"
        compiled = build_supervisor_graph(
            MemoryFakeLLM(), model_name="gpt-5.4-mini", results_dir=results_dir,
            checkpointer=saver2, store=store2,
        )
        snapshot = compiled.get_state({"configurable": {"thread_id": "sqlite-thread", "user_id": "sqlite-user"}})
        assert snapshot.values["turns"] == [{
            "turn": 1,
            "question": "Remember M2 as my default model. Forecast as of 2020-01-02.",
            "resolved": result.resolved_question,
            "tool_args": [{"tool": "get_forecast", "args": {"as_of": "2020-01-02", "model": "M2"}}],
        }]
    finally:
        close2()


def test_resolved_schema_is_strict_compatible():
    from openai.lib._pydantic import to_strict_json_schema
    from pydantic import ValidationError

    schema = to_strict_json_schema(Resolved)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    with pytest.raises(ValidationError):
        Resolved(standalone_question="question", used_turns=[], used_defaults=[], answer="no")


def test_reopened_checkpoint_rebuilds_state_types_without_unregistered_warning(results_dir, tmp_path, caplog):
    saver, store, close = open_memory(tmp_path)
    _run(
        "Remember M2 as my default model. Forecast as of 2020-01-02.",
        MemoryFakeLLM(), results_dir, thread_id="serde-thread", saver=saver, store=store, user_id="serde-user",
    )
    close()

    saver2, store2, close2 = open_memory(tmp_path)
    try:
        compiled = build_supervisor_graph(
            MemoryFakeLLM(), model_name="gpt-5.4-mini", results_dir=results_dir,
            checkpointer=saver2, store=store2,
        )
        with caplog.at_level("WARNING"):
            values = compiled.get_state({"configurable": {"thread_id": "serde-thread"}}).values
        assert isinstance(values["answer"], Answer)
        assert isinstance(values["plan"], Plan)
        assert values["handoffs"] and all(isinstance(item, Handoff) for item in values["handoffs"])
        assert all(isinstance(ev, Evidence) for item in values["handoffs"] for ev in item.evidence)
        assert "unregistered type" not in caplog.text
    finally:
        close2()


class _NotAllowed(BaseModel):
    value: int


def test_checkpoint_serializer_does_not_rebuild_unlisted_classes():
    serde = checkpoint_serializer()
    restored = serde.loads_typed(serde.dumps_typed({"x": _NotAllowed(value=1)}))
    assert not isinstance(restored["x"], _NotAllowed)
    plan = Plan(agents=["forecast"], subtasks={"forecast": "t"})
    assert serde.loads_typed(serde.dumps_typed(plan)) == plan
