"""Offline contract tests for the Step 11 forecasting agent."""

from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def toy_results(tmp_path: Path) -> Path:
    """Write small source tables using the production column names."""
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    pd.DataFrame(
        [
            {
                "date": date,
                "model": model,
                "realised_30d": 0.10 + i / 100,
                "raw_30d": 0.20 + i / 100,
                "floored_30d": 0.21 + i / 100,
                "floored": True,
            }
            for i, date in enumerate(dates)
            for model in ("M0", "M1", "M2", "M3")
        ]
    ).to_parquet(tmp_path / "forecasts.parquet", index=False)
    pd.DataFrame(
        [
            {
                "date": date,
                "realised_30d": 0.15 + i / 100,
                "raw_30d": 0.25 + i / 100,
                "floored_30d": 0.26 + i / 100,
                "floored": False,
                "seed_0": i,
            }
            for i, date in enumerate(dates)
        ]
    ).to_parquet(tmp_path / "mlp_forecasts.parquet", index=False)
    pd.DataFrame(
        [
            {"model": model, "date": date, "qlike": qlike, "squared_error": se}
            for model, rows in {
                "M0": [(dates[0], 2.0, 4.0), (dates[1], 4.0, 8.0)],
                "M1": [(dates[0], 1.0, 2.0), (dates[1], 3.0, 6.0)],
                "M2": [(dates[1], 5.0, 10.0), (dates[2], 7.0, 14.0)],
                "M3": [(dates[0], 8.0, 16.0)],
            }.items()
            for date, qlike, se in rows
        ]
    ).to_parquet(tmp_path / "losses.parquet", index=False)
    pd.DataFrame(
        [
            {
                "term": term,
                "component": component,
                "run": run,
                "beta": beta,
                "se": 0.1,
                "effect": beta,
                "effect_ci_low": beta - 0.2,
                "effect_ci_high": beta + 0.2,
                "p_two_sided": 0.05,
                "p_holm": 0.1,
                "n_obs": 100,
            }
            for term, component, run, beta in (
                ("cpi", "on", "primary", 0.3),
                ("cpi", "on", "sensitivity", 0.9),
                ("fomc", "oc", "primary", 0.4),
            )
        ]
    ).to_parquet(tmp_path / "q3_events.parquet", index=False)
    return tmp_path


def _schema():
    return importlib.import_module("agent.schema")


def _tools():
    return importlib.import_module("agent.tools")


def _verifier():
    return importlib.import_module("agent.verifier")


def _graph():
    return importlib.import_module("agent.graph")


def _answer(**overrides):
    defaults = {
        "as_of": None,
        "horizon": "30 sessions",
        "forecast": None,
        "implied_variance": None,
        "spread": None,
        "text": "",
        "limitations": "",
        "source_rows": [],
        "citations": [],
        "refused": False,
        "refusal_reason": None,
    }
    defaults.update(overrides)
    return _schema().Answer(**defaults)


def test_verifier_rejects_unsupported_number_and_accepts_6sf_and_3dp():
    verifier = _verifier()
    rows = [{"raw_30d": 1.23456789}]
    bounds = ("2020-01-01", "2020-01-31")

    ok, reason = verifier.verify(_answer(text="Value 9.87"), rows, bounds, "get_forecast")
    assert not ok and "9.87" in reason
    assert verifier.verify(_answer(text="Value 1.23457"), rows, bounds, "get_forecast")[0]
    assert verifier.verify(_answer(text="Value 1.235"), rows, bounds, "get_forecast")[0]


def test_verifier_checks_dates_inclusively_at_data_range_boundaries():
    verifier = _verifier()
    bounds = ("2020-01-02", "2020-01-04")
    assert verifier.verify(_answer(text="From 2020-01-02 to 2020-01-04"), [], bounds, "compare_models")[0]
    ok, reason = verifier.verify(_answer(text="Data from 2020-01-01"), [], bounds, "compare_models")
    assert not ok and "2020-01-01" in reason


def test_verifier_rejects_unknown_citation_chunks_and_citations_for_other_tools():
    schema = _schema()
    verifier = _verifier()
    answer = _answer(citations=[schema.Citation(chunk_id="missing", url="https://example.test/doc")])
    assert not verifier.verify(answer, [{"chunk_id": "known"}], None, "search_fomc")[0]

    known = _answer(citations=[schema.Citation(chunk_id="known", url="https://example.test/doc")])
    ok, reason = verifier.verify(known, [{"chunk_id": "known"}], None, "get_forecast")
    assert not ok and "known" in reason


def test_tools_select_forecasts_events_and_search_rows_and_compute_comparison(toy_results: Path):
    tools = _tools()
    forecast = tools.get_forecast(as_of="2020-01-02", model="M2", results_dir=toy_results)
    assert forecast["source"] == "forecasts.parquet"
    assert forecast["row_keys"] == ["date", "model"]
    assert len(forecast["rows"]) == 1
    assert forecast["rows"][0]["model"] == "M2"

    mlp = tools.get_forecast(as_of="2020-01-02", model="M4", results_dir=toy_results)
    assert mlp["source"] == "mlp_forecasts.parquet"
    assert mlp["row_keys"] == ["date", "model"]
    assert len(mlp["rows"]) == 1 and mlp["rows"][0]["model"] == "M4"
    assert mlp["rows"][0]["seed_0"] == 1

    comparison = tools.compare_models(
        start="2020-01-01", end="2020-01-02", metric="qlike", results_dir=toy_results
    )
    assert comparison == {
        "rows": [
            {"model": "M0", "mean": 3.0, "n": 2},
            {"model": "M1", "mean": 2.0, "n": 2},
            {"model": "M2", "mean": 5.0, "n": 1},
            {"model": "M3", "mean": 8.0, "n": 1},
        ],
        "source": "losses.parquet",
        "row_keys": ["model"],
    }

    event = tools.event_effect(event="cpi", component="on", results_dir=toy_results)
    assert event["source"] == "q3_events.parquet"
    assert event["row_keys"] == ["run", "term", "component"]
    assert len(event["rows"]) == 1 and event["rows"][0]["run"] == "primary"

    captured = {}

    def search_fn(query, *, mode, k):
        captured.update(query=query, mode=mode, k=k)
        return [{"chunk_id": "chunk-1", "url": "https://example.test", "text": "minutes"}]

    search = tools.search_fomc(query="inflation", search_fn=search_fn, results_dir=toy_results)
    assert captured == {"query": "inflation", "mode": "hybrid", "k": 5}
    assert search["rows"][0]["chunk_id"] == "chunk-1"
    assert search["row_keys"] == ["chunk_id"]


def test_tools_reject_invalid_event_or_component(toy_results: Path):
    tools = _tools()
    with pytest.raises(ValueError):
        tools.event_effect(event="jobs", component="on", results_dir=toy_results)
    with pytest.raises(ValueError):
        tools.event_effect(event="cpi", component="total", results_dir=toy_results)


def test_tools_report_source_ranges_and_reject_dates_outside_them(toy_results: Path):
    tools = _tools()
    assert tools.data_range("get_forecast", toy_results) == ("2020-01-01", "2020-01-03")
    with pytest.raises(tools.OutOfRangeError):
        tools.get_forecast(as_of="2019-12-31", model="M0", results_dir=toy_results)
    with pytest.raises(tools.OutOfRangeError):
        tools.compare_models(start="2019-12-31", end="2020-01-02", metric="qlike", results_dir=toy_results)


def test_advice_question_is_refused_without_routing(toy_results: Path):
    graph = _graph()

    class FakeLLM:
        route_calls = 0

        def route(self, question):
            self.route_calls += 1
            raise AssertionError("advice must be refused before routing")

        def draft(self, question, tool_output, rejection=None):
            raise AssertionError("advice must not be drafted")

    llm = FakeLLM()
    result = graph.answer_question(
        "Should I buy this?", llm, model_name="fake", results_dir=toy_results
    )
    assert llm.route_calls == 0
    assert result.answer.refused and result.tool is None


def test_out_of_range_question_is_refused_without_drafting(toy_results: Path):
    graph = _graph()
    schema = _schema()

    class FakeLLM:
        draft_calls = 0

        def route(self, question):
            return schema.RouteDecision(
                tool="get_forecast", args={"as_of": "2019-12-31", "model": "M0"},
                usage=schema.Usage(input_tokens=1, output_tokens=1),
            )

        def draft(self, question, tool_output, rejection=None):
            self.draft_calls += 1
            raise AssertionError("out-of-range requests must not be drafted")

    llm = FakeLLM()
    result = graph.answer_question("Forecast on 2019-12-31", llm, model_name="fake", results_dir=toy_results)
    assert result.answer.refused and "outside data range" in result.answer.refusal_reason.lower()
    assert llm.draft_calls == 0


def test_agent_happy_path_routes_calls_tool_and_verifies_once(toy_results: Path):
    graph = _graph()
    schema = _schema()

    class FakeLLM:
        def route(self, question):
            return schema.RouteDecision(
                tool="get_forecast", args={"as_of": "2020-01-01", "model": "M0"},
                usage=schema.Usage(input_tokens=1, output_tokens=1),
            )

        def draft(self, question, tool_output, rejection=None):
            value = tool_output["rows"][0]["floored_30d"]
            return _answer(forecast=f"{value:.6g}", text=f"Forecast {value:.6g}"), schema.Usage(1, 1)

    result = graph.answer_question("What is the M0 forecast?", FakeLLM(), model_name="fake", results_dir=toy_results)
    assert not result.answer.refused
    assert result.attempts == 1
    assert result.tool == "get_forecast"


def test_agent_retries_one_failed_draft_then_accepts_verified_answer(toy_results: Path):
    graph = _graph()
    schema = _schema()

    class FakeLLM:
        draft_calls = 0

        def route(self, question):
            return schema.RouteDecision(
                tool="get_forecast", args={"as_of": "2020-01-01", "model": "M0"},
                usage=schema.Usage(1, 1),
            )

        def draft(self, question, tool_output, rejection=None):
            self.draft_calls += 1
            value = tool_output["rows"][0]["floored_30d"]
            number = "999" if self.draft_calls == 1 else f"{value:.6g}"
            return _answer(text=f"Forecast {number}"), schema.Usage(1, 1)

    llm = FakeLLM()
    result = graph.answer_question("What is the M0 forecast?", llm, model_name="fake", results_dir=toy_results)
    assert llm.draft_calls == 2
    assert result.attempts == 2 and not result.answer.refused


def test_agent_refuses_when_both_drafts_fail_verification(toy_results: Path):
    graph = _graph()
    schema = _schema()

    class FakeLLM:
        draft_calls = 0

        def route(self, question):
            return schema.RouteDecision(
                tool="get_forecast", args={"as_of": "2020-01-01", "model": "M0"},
                usage=schema.Usage(1, 1),
            )

        def draft(self, question, tool_output, rejection=None):
            self.draft_calls += 1
            return _answer(text="Unsupported value 999"), schema.Usage(1, 1)

    llm = FakeLLM()
    result = graph.answer_question("What is the M0 forecast?", llm, model_name="fake", results_dir=toy_results)
    assert llm.draft_calls == 2
    assert result.attempts == 2 and result.answer.refused
    assert result.answer.refusal_reason == "could not verify"
    assert "999" in result.last_rejection


def test_cost_cap_stops_before_next_llm_call(toy_results: Path, monkeypatch):
    graph = _graph()
    schema = _schema()
    monkeypatch.setitem(graph.PRICES_USD_PER_1M, "test-cost-cap", (1_000_000.0, 1_000_000.0))

    class FakeLLM:
        route_calls = 0
        draft_calls = 0

        def route(self, question):
            self.route_calls += 1
            return schema.RouteDecision(
                tool="get_forecast", args={"as_of": "2020-01-01", "model": "M0"},
                usage=schema.Usage(input_tokens=1, output_tokens=1),
            )

        def draft(self, question, tool_output, rejection=None):
            self.draft_calls += 1
            return _answer(text="ok"), schema.Usage(1, 1)

    llm = FakeLLM()
    with pytest.raises(graph.CostCapExceeded):
        graph.answer_question(
            "What is the M0 forecast?", llm, model_name="test-cost-cap", results_dir=toy_results,
            max_cost_usd=0.5,
        )
    assert llm.route_calls == 1 and llm.draft_calls == 0


def test_run_eval_repeats_each_question_and_scores_refusal_rows(toy_results: Path):
    runner = importlib.import_module("scripts.run_agent_eval")
    schema = _schema()

    questions = pd.DataFrame(
        [
            {
                "qid": "q1", "question": "What is the M0 forecast?", "expected_tool": "get_forecast",
                "expected_answer": "0.21", "rounding": "6sf", "expect_refusal": False,
            },
            {
                "qid": "q2", "question": "Should I trade?", "expected_tool": None,
                "expected_answer": "", "rounding": "", "expect_refusal": True,
            },
        ]
    )

    class FakeLLM:
        def route(self, question):
            return schema.RouteDecision(
                tool="get_forecast", args={"as_of": "2020-01-01", "model": "M0"},
                usage=schema.Usage(1, 1),
            )

        def draft(self, question, tool_output, rejection=None):
            value = tool_output["rows"][0]["floored_30d"]
            return _answer(text=f"Forecast {value:.6g}"), schema.Usage(1, 1)

    result = runner.run_eval(questions, FakeLLM(), model_name="fake", results_dir=toy_results, repeats=3)
    assert len(result) == 6
    assert "last_rejection" in result.columns
    assert set(result["repeat"]) == {0, 1, 2}
    assert result.groupby("qid")["repeat"].nunique().to_dict() == {"q1": 3, "q2": 3}
    refusal_rows = result[result["qid"] == "q2"]
    assert refusal_rows["refusal_correct"].all()
    assert runner.score_exact(_answer(text="The value is 0.21"), "0.21")
    assert runner.score_exact(_answer(refused=True), "anything")


def test_verifier_ignores_model_digits_and_accepts_supported_mean():
    verifier = _verifier()
    rows = [{"mean": 0.0981894}]

    ok, reason = verifier.verify(
        _answer(text="M2 had the lowest mean QLIKE (0.0981894) among M0–M3."),
        rows,
        None,
        "compare_models",
    )

    assert ok, reason


def test_verifier_rejects_non_iso_date_numbers():
    verifier = _verifier()

    ok, reason = verifier.verify(
        _answer(text="on 2 January 2020"), [], None, "compare_models"
    )

    assert not ok
    assert "2" in reason or "2020" in reason


def test_verifier_preserves_negative_effect_sign():
    verifier = _verifier()
    rows = [{"effect": -0.359528}]

    assert verifier.verify(
        _answer(text="Effect -0.359528"), rows, None, "event_effect"
    )[0]
    ok, reason = verifier.verify(
        _answer(text="Effect 0.359528"), rows, None, "event_effect"
    )
    assert not ok and "0.359528" in reason


def test_verifier_accepts_allowed_constant_and_matching_exponent_form():
    verifier = _verifier()
    rows = [{"raw_30d": 1e-6}]

    assert verifier.verify(
        _answer(text="30-day value 1e-06"), rows, None, "get_forecast"
    )[0]
    ok, reason = verifier.verify(
        _answer(text="30-day value 1.0e-06"), rows, None, "get_forecast"
    )
    assert not ok and "1.0e-06" in reason


def test_score_exact_shares_verifier_number_token_and_ignores_model_digits():
    verifier = _verifier()
    runner = importlib.import_module("scripts.run_agent_eval")

    assert runner.NUMBER_TOKEN is verifier.NUMBER_TOKEN
    assert runner.score_exact(_answer(text="M2 … 0.0981894"), "0.0981894")
    assert not runner.score_exact(_answer(text="M2 …"), "2")


def test_run_eval_continues_after_a_route_error(tmp_path: Path):
    runner = importlib.import_module("scripts.run_agent_eval")
    questions = pd.DataFrame([{
        "qid": "q1",
        "question": "What is the M0 forecast?",
        "expected_tool": "get_forecast",
        "expected_answer": "0.21",
        "rounding": "6sf",
        "expect_refusal": False,
    }])

    class FailingLLM:
        def route(self, question):
            raise ValueError("route failed")

        def draft(self, question, tool_output, rejection=None):
            raise AssertionError("draft must not run after a route error")

    result = runner.run_eval(
        questions, FailingLLM(), model_name="fake", results_dir=tmp_path, repeats=2
    )

    assert len(result) == 2
    assert set(result["error"]) == {"ValueError: route failed"}
    assert not result[["correct_tool", "exact_match", "refusal_correct"]].any().any()


def test_draft_answer_schema_is_strict_compatible():
    from openai.lib._pydantic import to_strict_json_schema
    from agent.schema import DraftAnswer

    schema = to_strict_json_schema(DraftAnswer)
    assert sorted(schema["required"]) == sorted(schema["properties"])

    def walk(node):
        if isinstance(node, dict):
            if "items" in node:
                item = node["items"]
                assert "type" in item or "$ref" in item or "anyOf" in item
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
