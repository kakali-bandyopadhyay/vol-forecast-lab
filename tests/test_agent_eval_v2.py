"""Offline coverage for evaluation scoring and runner guardrails."""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agent.schema import Answer, Citation, Usage
from agent.v2.contract import Evidence, Handoff, Plan
from agent.v2.llm import OpenAISupervisorLLM, PlanWire, Resolved
from agent.v2.scoring import args_match, numeric_match, routing_ok, sign_word

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_agent_eval_v2_under_test", ROOT / "scripts" / "run_agent_eval_v2.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("tokens", "expected", "want"),
    [
        (["1.23"], 1.2345, True),                 # rounds to the written 2 decimals; 3 s.f.
        (["1.2"], 1.2345, False),                 # fewer than 3 significant figures
        (["1.23e-3"], 0.0012345, True),           # scientific notation
        (["1.00"], 1.005, True),                  # 0.5% boundary
        (["1.01"], 1.0, False),
        (["-0.446"], -0.445549, True),
    ],
)
def test_numeric_match_rounding_precision_scientific_and_edge(tokens, expected, want):
    assert numeric_match(tokens, expected) is want


@pytest.mark.parametrize(
    ("text", "want"),
    [("positive estimate", "positive"), ("negative effect", "negative"), ("positive and negative", "conflict"), ("no sign", None)],
)
def test_sign_word_table(text, want):
    assert sign_word(text) == want


@pytest.mark.parametrize(
    ("actual", "expected", "want"),
    [(["forecast"], ["forecast"], True), (["forecast", "event-effect"], ["forecast"], False), ([], [], True)],
)
def test_routing_ok_exact_set_including_empty(actual, expected, want):
    assert routing_ok(actual, expected) is want


def test_args_match_is_case_insensitive_for_enums_but_exact_for_dates():
    evidence = [{"tool": "compare_models", "args": {"metric": "QLIKE", "start": "2021-01-01"}}]
    assert args_match(evidence, "compare_models", {"metric": "qlike", "start": "2021-01-01"})
    assert not args_match(evidence, "compare_models", {"metric": "qlike", "start": "2021-1-1"})


def _statement_row(date: str) -> dict:
    statements = {
        "2019-07-31": "The labor market remains strong. Economic activity has been rising at a moderate rate. Job gains have been solid and unemployment has remained low.",
        "2020-01-29": "Economic activity has been rising at a moderate rate. Household spending has been rising at a moderate pace. Business fixed investment and exports remain weak.",
        "2026-07-29": "Economic activity is expanding at a solid pace despite elevated uncertainty, partly due to conflict in the Middle East. Productivity growth and capital investment are strong. Job gains have kept pace with the workforce and unemployment has changed little.",
    }
    return {
        "date": date,
        "doc_type": "statement",
        "chunk_id": f"statement-{date.replace('-', '')}-0000",
        "url": f"https://example.invalid/statement/{date}",
        "content": statements[date],
    }


def dry_search_fn(query, **kwargs):
    date = kwargs["date_from"]
    if kwargs.get("doc_type") != "statement" or kwargs.get("date_to") != date:
        return []
    return [_statement_row(date)]


class DryRunFakeLLM:
    """Deterministic offline adapter that exercises the real graph and tools."""

    model = "fake-offline"
    model_name = model

    def __init__(self):
        self.context_calls = []
        self.draft_calls = 0

    def contextualize(self, question, ctx):
        self.context_calls.append((question, ctx))
        text = question
        used_turns = []
        used_defaults = []
        history_args = [
            (turn.get("turn"), item.get("tool"), item.get("args", {}))
            for turn in ctx.get("turns", [])
            for item in turn.get("tool_args", [])
        ]
        prior_forecast = next((args for _, tool, args in reversed(history_args) if tool == "get_forecast"), {})
        prior_event = next((args for _, tool, args in reversed(history_args) if tool == "event_effect"), {})
        prior_compare = next((args for _, tool, args in reversed(history_args) if tool == "compare_models"), {})

        date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
        if date is None and prior_forecast.get("as_of"):
            text += f" as of {prior_forecast['as_of']}"
            date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
            used_turns.append(next((n for n, tool, _ in reversed(history_args) if tool == "get_forecast"), 1))
        if date is None and prior_compare.get("start"):
            text += f" from {prior_compare['start']} through {prior_compare['end']}"
            used_turns.append(next((n for n, tool, _ in reversed(history_args) if tool == "compare_models"), 1))

        model = re.search(r"\bM[0-4]\b", text, re.IGNORECASE)
        if model is None and ctx.get("defaults", {}).get("default_model"):
            text += f" using model {ctx['defaults']['default_model']}"
            used_defaults.append("default_model")
        elif model is None and prior_forecast.get("model"):
            text += f" using model {prior_forecast['model']}"
            used_turns.append(next((n for n, tool, _ in reversed(history_args) if tool == "get_forecast"), 1))

        metric = re.search(r"\b(?:QLIKE|squared error)\b", text, re.IGNORECASE)
        if metric is None and ctx.get("defaults", {}).get("default_metric"):
            text += f" with {ctx['defaults']['default_metric']} metric"
            used_defaults.append("default_metric")
        elif metric is None and prior_compare.get("metric"):
            text += f" with {prior_compare['metric']} metric"
            used_turns.append(next((n for n, tool, _ in reversed(history_args) if tool == "compare_models"), 1))

        event = re.search(r"\b(CPI|FOMC)\b", text, re.IGNORECASE)
        component = re.search(r"\b(ON|OC|GK)\b", text, re.IGNORECASE)
        if prior_event:
            if event is None:
                text += f" {prior_event['event']}"
                used_turns.append(next((n for n, tool, _ in reversed(history_args) if tool == "event_effect"), 1))
            if component is None:
                text += f" {prior_event['component']} component"
                used_turns.append(next((n for n, tool, _ in reversed(history_args) if tool == "event_effect"), 1))
        return Resolved(standalone_question=text, used_turns=sorted(set(used_turns)), used_defaults=sorted(set(used_defaults))), Usage(0, 0)

    def plan(self, question):
        agents = []
        if re.search(r"forecast|lowest mean|model has|model comparisons|compare models|default model|\bM[0-4]\b", question, re.IGNORECASE):
            agents.append("forecast")
        if re.search(r"beta|event.study|event.effect|estimate and sign|same component|\b(?:ON|OC|GK) component|(?:effect of|effect on|effect for)|p.value|overnight effect|open.to.close effect", question, re.IGNORECASE):
            agents.append("event-effect")
        if re.search(r"statement|FOMC statement", question, re.IGNORECASE):
            agents.append("FOMC retrieval")
        if not agents and re.search(r"SPY options", question, re.IGNORECASE):
            agents = []
        plan = Plan(agents=agents, subtasks={agent: question for agent in agents})
        return plan, Usage(0, 0)

    def replan(self, question, handoffs):
        return Plan(agents=[], subtasks={}), Usage(0, 0)

    def subagent_route(self, agent, task, schemas):
        from agent.schema import RouteDecision
        if agent == "forecast":
            dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", task)
            if re.search(r"lowest mean|model has|compare models|model comparisons", task, re.IGNORECASE):
                metric = "squared_error" if re.search(r"squared error", task, re.IGNORECASE) else "qlike"
                return RouteDecision(tool="compare_models", args={"start": dates[0], "end": dates[-1], "metric": metric}, usage=Usage(0, 0)), Usage(0, 0)
            if not dates:
                from dateutil.parser import parse as parse_date
                dates = [parse_date(task, fuzzy=True).date().isoformat()]
            model = re.search(r"\bM[0-4]\b", task, re.IGNORECASE)
            return RouteDecision(tool="get_forecast", args={"as_of": dates[-1], "model": model.group(0).upper()}, usage=Usage(0, 0)), Usage(0, 0)
        if agent == "event-effect":
            event = "fomc" if re.search(r"FOMC", task, re.IGNORECASE) else "cpi"
            component = "oc" if re.search(r"\bOC\b|open.to.close", task, re.IGNORECASE) else "gk" if re.search(r"\bGK\b", task, re.IGNORECASE) else "on"
            return RouteDecision(tool="event_effect", args={"event": event, "component": component}, usage=Usage(0, 0)), Usage(0, 0)
        natural_date = re.search(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
            task,
            re.IGNORECASE,
        )
        if natural_date:
            from dateutil.parser import parse as parse_date
            date = parse_date(natural_date.group(0)).date().isoformat()
        else:
            date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", task).group(0)
        return RouteDecision(tool="search_fomc", args={"query": "economic activity and labor market", "date_from": date, "date_to": date, "doc_type": "statement"}, usage=Usage(0, 0)), Usage(0, 0)

    def draft_v2(self, question, handoffs, rejection):
        from agent.schema import Answer
        self.draft_calls += 1
        parts = []
        citations = []
        as_of = None
        refused = False
        refusal_reason = None
        for handoff in handoffs:
            if handoff.status == "out_of_range":
                refused = True
                refusal_reason = "No forecast row is available for the requested date."
                parts.append("No forecast row is available for 2019-12-31 because coverage begins on 2020-01-02.")
            for evidence in handoff.evidence:
                for row in evidence.rows:
                    if evidence.tool == "get_forecast":
                        as_of = str(row["date"])
                        parts.append(f"Forecast variance for {row['model']} on {as_of} is {row['floored_30d']:.6g}.")
                    elif evidence.tool == "compare_models":
                        winner = min(row for row in evidence.rows if row["mean"] == min(item["mean"] for item in evidence.rows))
                        parts.append(f"{winner['model']} has the lowest mean; mean {winner['mean']:.6g} ({evidence.args['metric']}), n={winner['n']}.")
                        break
                    elif evidence.tool == "event_effect":
                        if "holm" in question.lower():
                            parts.append(f"Holm adjusted p-value is {row['p_holm']:.3f}.")
                        elif "p-value" in question.lower() or "p value" in question.lower():
                            parts.append(f"Two-sided p-value is {row['p_two_sided']:.3f}.")
                        else:
                            sign = "positive" if row["beta"] > 0 else "negative"
                            parts.append(f"Event-study beta for {evidence.args['event'].upper()} {evidence.args['component'].upper()} is {row['beta']:.6g}, {sign}.")
                    elif evidence.tool == "search_fomc":
                        parts.append(row["content"])
                        citations.append(Citation(chunk_id=row["chunk_id"], url=row["url"]))
        return Answer(as_of=as_of, text=" ".join(parts), citations=citations, refused=refused, refusal_reason=refusal_reason), Usage(0, 0)


def test_dry_run_v2_writes_51_turns_with_isolated_ids_and_memory(tmp_path):
    # The dry run exercises the real tools, which read per-date series kept local and git-ignored
    # under the data licence (ADR 0003); a public clone (CI) does not have them. Same rule as
    # tests/test_drift.py. Skip added 25 Nov 2026 after the first CI run of Build B.
    missing = [name for name in ("forecasts.parquet", "losses.parquet") if not (ROOT / "results" / name).exists()]
    if missing:
        pytest.skip(f"results/{', '.join(missing)} local only (licence, ADR 0003)")
    runner = _load_runner()
    output = tmp_path / "out"
    assert runner.main(["--set", "v2", "--repeats", "3", "--dry-run", "--out-dir", str(output)]) == 0
    frame = pd.read_parquet(output / "eval_v2_turns.parquet")
    assert len(frame) == 51
    assert set(frame["rep"]) == {1, 2, 3}
    assert frame.routing_ok.map(bool).all()
    assert frame.tool_args_ok.map(bool).all()
    assert frame.numeric_ok.map(bool).all()
    q09 = frame[frame.qid == "Q09"].sort_values(["rep", "turn"])
    assert q09.iloc[0].thread_id == "q09-set-r1"
    assert q09.iloc[1].thread_id == "q09-recall-r1"
    assert q09.iloc[0].user_id == q09.iloc[1].user_id == "eval-r1-Q09"
    q08 = frame[frame.qid == "Q08"]
    assert set(q08.user_id) == {"eval-r1-Q08", "eval-r2-Q08", "eval-r3-Q08"}
    q09_recall = q09[q09.turn == 2]
    assert all(any(item["args"].get("metric") == "qlike" for item in args) for args in q09_recall.tool_args_per_evidence)
    assert all("default_metric" in reads for reads in q09_recall.memory_reads)
    q08_followups = q08[q08.turn.isin([2, 3])]
    assert all(bool(value) for value in q08_followups.memory_args_ok), q08_followups[["turn", "resolved_question", "stop_reason", "error", "tool_args_per_evidence", "memory_args_ok"]].to_dict("records")
    q10_followups = frame[(frame.qid == "Q10") & (frame.turn.isin([2, 3]))]
    assert all(bool(value) for value in q10_followups.memory_args_ok)
    q12 = frame[frame.qid == "Q12"]
    assert all(row.tools_called == ["get_forecast"] for _, row in q12.iterrows())
    assert all(row.evidence_statuses == ["out_of_range"] for _, row in q12.iterrows())
    assert all(bool(value) for value in frame[frame.qid.isin(["Q11", "Q12"])].refusal_ok)
    eval_id = frame.iloc[0].eval_run_id
    for rep in (1, 2, 3):
        assert (output / "runs" / "memory" / "eval_v2" / eval_id / f"r{rep}" / "store.sqlite").exists()

    scorer_spec = importlib.util.spec_from_file_location("score_agent_eval_v2_under_test", ROOT / "scripts" / "score_agent_eval_v2.py")
    scorer = importlib.util.module_from_spec(scorer_spec)
    assert scorer_spec.loader is not None
    scorer_spec.loader.exec_module(scorer)
    pack = scorer.make_review_pack(output)
    assert "Turns requiring review: 15" in pack.read_text(encoding="utf-8")
    manual_rows = []
    for _, row in frame[frame.needs_review].iterrows():
        for field in row.review_fields:
            value = "appropriate" if field == "block_class" else "true"
            manual_rows.append({"qid": row.qid, "rep": row.rep, "turn": row.turn, "field": field, "value": value, "reviewer": "offline-test", "reason": "fake evidence fixture"})
    pd.DataFrame(manual_rows, columns=["qid", "rep", "turn", "field", "value", "reviewer", "reason"]).to_csv(output / "manual_scores.csv", index=False)
    scorer.make_final_summary(output)
    final_summary = json.loads((output / "eval_v2_summary.json").read_text(encoding="utf-8"))
    assert final_summary["headline_denominator"] == 36
    assert final_summary["per_turn_denominator"] == 51
    assert final_summary["memory_denominator"] == 15
    assert final_summary["refusals_denominator"] == 6


def test_freeze_gate_rejects_tracked_changes(monkeypatch):
    runner = _load_runner()
    monkeypatch.setattr(runner, "_git", lambda *args: " M agent/v2/graph.py" if args[0] == "status" else "head")
    with pytest.raises(RuntimeError, match="clean tracked tree"):
        runner.freeze_gate("v2")


def test_freeze_gate_rejects_untracked_agent_code(monkeypatch):
    runner = _load_runner()
    monkeypatch.setattr(runner, "_git", lambda *args: "?? agent/v2/new_module.py" if args[0] == "status" else "head")
    with pytest.raises(RuntimeError, match="no untracked code files"):
        runner.freeze_gate("v2")


@pytest.mark.parametrize("artifact", ["results/eval-notes.py", "runs/helper.py"])
def test_freeze_gate_allows_untracked_result_artifacts(monkeypatch, tmp_path, artifact):
    runner = _load_runner()
    source = tmp_path / "questions.json"
    source.write_bytes(b"frozen")
    monkeypatch.setattr(runner, "QUESTIONS_PATH", source)
    monkeypatch.setattr(runner, "_git", lambda *args: f"?? {artifact}" if args[0] == "status" else "head")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"frozen"))
    provenance = runner.freeze_gate("v1-regression")
    assert provenance["git_head"] == "head"


def test_llm_payloads_exclude_tool_attempt_telemetry():
    from agent.schema import DraftAnswer

    class Responses:
        def __init__(self):
            self.payloads = []

        def parse(self, **kwargs):
            self.payloads.append(kwargs)
            if kwargs["text_format"] is PlanWire:
                parsed = PlanWire(steps=[])
            else:
                parsed = DraftAnswer(
                    as_of=None, horizon="30 sessions", forecast=None,
                    implied_variance=None, spread=None, text="answer", limitations="",
                    citations=[], refused=False, refusal_reason=None,
                )
            return SimpleNamespace(output_parsed=parsed, usage=SimpleNamespace(input_tokens=0, output_tokens=0))

    responses = Responses()
    llm = object.__new__(OpenAISupervisorLLM)
    llm.model = "fake"
    llm.client = SimpleNamespace(responses=responses)
    base = dict(task="forecast", agent="forecast", evidence=[], status="no_data", hop=1,
                cost_usd_hop=0.0, cost_usd_so_far=0.0)
    clean = Handoff(inputs={"question": "q", "task": "forecast"}, **base)
    with_telemetry = Handoff(
        inputs={"question": "q", "task": "forecast", "tool_attempts": [{"tool": "get_forecast", "args": {"as_of": "2020-07-01"}}]},
        **base,
    )

    llm.replan("q", [with_telemetry])
    replan_with = json.loads(responses.payloads[-1]["input"])
    llm.replan("q", [clean])
    replan_without = json.loads(responses.payloads[-1]["input"])
    assert replan_with == replan_without

    llm.draft_v2("q", [with_telemetry])
    draft_with = json.loads(responses.payloads[-1]["input"])
    llm.draft_v2("q", [clean])
    draft_without = json.loads(responses.payloads[-1]["input"])
    assert draft_with == draft_without
    assert "tool_attempts" not in json.dumps([replan_with, draft_with])


def test_freeze_gate_rejects_changed_questions_hash(monkeypatch, tmp_path):
    runner = _load_runner()
    source = tmp_path / "questions.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "QUESTIONS_PATH", source)
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else "head")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"not the frozen json"))
    with pytest.raises(RuntimeError, match="does not match frozen"):
        runner.freeze_gate("v2")


def test_freeze_gate_rejects_existing_v2_output(monkeypatch, tmp_path):
    runner = _load_runner()
    source = tmp_path / "questions.json"
    source.write_bytes(b"frozen")
    monkeypatch.setattr(runner, "QUESTIONS_PATH", source)
    monkeypatch.setattr(runner, "DEFAULT_OUT", tmp_path / "results" / "agent_v2")
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else "head")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"frozen"))
    runner.DEFAULT_OUT.mkdir(parents=True)
    (runner.DEFAULT_OUT / "eval_v2_turns.parquet").touch()
    with pytest.raises(RuntimeError, match="already exists"):
        runner.freeze_gate("v2")


def _result_with_cost(cost):
    return SimpleNamespace(
        answer=Answer(), agents_called=[], tools_called=[], handoffs=[], rejections=[],
        resolved_question="offline", memory_reads=[], memory_writes=[],
        stop_reason="answered", attempts=1, hops=0, replans=0,
        cost_usd=cost, latency_s=0.001,
    )


def test_question_budget_stops_at_25_cents_and_marks_incomplete(monkeypatch, tmp_path):
    runner = _load_runner()
    calls = []
    def invoke(question, *args, **kwargs):
        budget = kwargs["max_cost_usd"]
        calls.append((question, budget))
        result = _result_with_cost(0.13)
        if question.startswith("What about M0"):
            result.stop_reason = "cost_cap"
        return result
    monkeypatch.setattr(runner, "answer_question_v2", invoke)
    monkeypatch.setattr(runner, "open_memory", lambda path: (None, None, lambda: None))
    rows, summary = runner._run_v2(
        DryRunFakeLLM(), repeats=1, out_dir=tmp_path / "out", eval_run_id="run",
        trace=False, provenance={}, memory_base=tmp_path / "memory",
    )
    q08_calls = [budget for question, budget in calls if question.startswith(("For this thread, remember M1", "What about M0", "Return to my default"))]
    assert q08_calls == pytest.approx([0.25, 0.12])
    assert summary["incomplete"]
    q08_rows = [row for row in rows if row["qid"] == "Q08"]
    assert q08_rows[-1]["error"].startswith("Budget exhausted")
    assert q08_rows[-1]["stop_reason"] == "not_run_budget"
    assert all(row["question_run_incomplete"] for row in q08_rows)
    assert any(row["qid"] == "Q09" for row in rows)
    assert any(question.startswith("Remember QLIKE") for question, _ in calls)


def test_total_budget_stops_at_five_dollars(monkeypatch, tmp_path):
    runner = _load_runner()
    calls = []
    monkeypatch.setattr(runner, "V2_ATTEMPT_CAP", 1.0)
    def invoke(question, *args, **kwargs):
        calls.append(kwargs["max_cost_usd"])
        return _result_with_cost(min(0.25, kwargs["max_cost_usd"]))
    monkeypatch.setattr(runner, "answer_question_v2", invoke)
    monkeypatch.setattr(runner, "open_memory", lambda path: (None, None, lambda: None))
    rows, summary = runner._run_v2(
        DryRunFakeLLM(), repeats=3, out_dir=tmp_path / "out", eval_run_id="run",
        trace=False, provenance={}, memory_base=tmp_path / "memory",
    )
    assert len(calls) == len(rows) == 20
    assert sum(row["cost_usd"] for row in rows) == pytest.approx(5.0)
    assert summary["incomplete"]


def test_v1_regression_respects_two_dollar_total_cap(monkeypatch, tmp_path):
    runner = _load_runner()
    def invoke(question, *args, **kwargs):
        return _result_with_cost(kwargs["max_cost_usd"])
    monkeypatch.setattr(runner, "answer_question_v2", invoke)
    rows, summary = runner._run_v1_regression(
        DryRunFakeLLM(), repeats=3, out_dir=tmp_path / "out", eval_run_id="run",
        trace=False, provenance={}, search_fn=dry_search_fn,
    )
    assert len(rows) == 8
    assert sum(row["cost_usd"] for row in rows) == pytest.approx(2.0)
    assert summary["incomplete"]


def test_v1_regression_rows_include_diagnostics(monkeypatch, tmp_path):
    runner = _load_runner()
    monkeypatch.setattr(runner, "answer_question_v2", lambda *a, **k: _result_with_cost(0.001))
    rows, summary = runner._run_v1_regression(
        DryRunFakeLLM(), repeats=1, out_dir=tmp_path / "out", eval_run_id="diag",
        trace=False, provenance={}, search_fn=dry_search_fn,
    )
    required = {
        "answer_fields", "answer_text", "citations", "stop_reason", "attempts", "rejections",
        "last_rejection", "hops", "replans", "evidence_statuses", "handoff_details", "tool_attempts", "evidence",
    }
    assert required <= rows[0].keys()
    assert rows[0]["answer_fields"]["horizon"] == "30 sessions"
    assert rows[0]["rejections"] == []
    assert summary["headline_denominator"] == 45
    assert "A01" in summary["per_question"]


def test_regression_outputs_choose_next_free_number(tmp_path):
    runner = _load_runner()
    output = tmp_path / "results"
    output.mkdir()
    (output / "regression_v1_on_v2.parquet").touch()
    (output / "regression_summary.json").touch()
    (output / "regression_v1_on_v2_run1.parquet").touch()
    (output / "regression_summary_run1.json").touch()
    data_path, summary_path = runner._regression_output_paths(output)
    assert data_path.name == "regression_v1_on_v2_run2.parquet"
    assert summary_path.name == "regression_summary_run2.json"


def test_regression_pack_lists_blocked_row_with_reason(monkeypatch, tmp_path):
    scorer_spec = importlib.util.spec_from_file_location(
        "score_agent_eval_v2_regression_under_test", ROOT / "scripts" / "score_agent_eval_v2.py"
    )
    scorer = importlib.util.module_from_spec(scorer_spec)
    assert scorer_spec.loader is not None
    scorer_spec.loader.exec_module(scorer)
    out = tmp_path / "results"
    out.mkdir()
    (out / "regression_v1_on_v2_run1.parquet").touch()
    run2 = out / "regression_v1_on_v2_run2.parquet"
    run2.touch()
    monkeypatch.setattr(scorer, "V1_QUESTIONS", tmp_path / "eval_questions.csv")
    scorer.V1_QUESTIONS.write_text(
        "qid,expected_answer\nA06,0.123\n", encoding="utf-8"
    )
    frame = pd.DataFrame([{
        "qid": "A06", "rep": 2, "prompt": "What is the CPI beta?",
        "correct_tool": True, "exact_match": False, "refusal_correct": False,
        "answer_text": "could not verify", "answer_fields": {"text": "could not verify"},
        "rejections": [{"attempt": 1, "reason": "unsupported beta 0.9", "draft_text": "beta 0.9"}],
        "tool_attempts": [{"tool": "event_effect", "args": {"event": "cpi"}}],
        "evidence": [{"tool": "event_effect", "rows": [{"beta": 0.123, "se": 0.04, "t": 3.1, "p": 0.002, "n": 240, "extra": "omit"}]}],
    }])
    selected_files = []
    monkeypatch.setattr(scorer.pd, "read_parquet", lambda path: selected_files.append(path) or frame)
    assert scorer._latest_regression_path(out) == run2
    assert scorer.main(["--regression-pack", "--out-dir", str(out), "--file", str(run2)]) == 0
    assert selected_files == [run2]
    target = out / "regression_pack.md"
    text = target.read_text(encoding="utf-8")
    assert "A06 · repetition 2" in text
    assert "unsupported beta 0.9" in text
    assert "beta 0.9" in text
    assert '"beta": 0.123' in text
    assert '"p": 0.002' in text
    assert '"n": 240' in text
    assert "extra" not in text


def test_regression_records_handles_numpy_empty_array_and_dict_rows():
    scorer_spec = importlib.util.spec_from_file_location(
        "score_agent_eval_v2_records_under_test", ROOT / "scripts" / "score_agent_eval_v2.py"
    )
    scorer = importlib.util.module_from_spec(scorer_spec)
    assert scorer_spec.loader is not None
    scorer_spec.loader.exec_module(scorer)
    assert scorer._records(np.array([], dtype=object)) == []
    data = [{"attempt": 1, "reason": "bad"}]
    assert scorer._records(data) == data


def test_trace_meta_none_keeps_existing_invoke_config(monkeypatch):
    runner = _load_runner()
    from agent.v2 import graph as graph_module

    seen = {}
    class StubGraph:
        def _runtime(self):
            return {"handoffs": [], "hops": 0, "cost_usd": 0.0, "last_rejection": None}
        def invoke(self, inputs, config=None):
            seen["config"] = config
            return {"answer": Answer(), "handoffs": [], "called": [], "cost_usd": 0.0}

    monkeypatch.setattr(graph_module, "build_supervisor_graph", lambda *a, **k: StubGraph())
    runner.answer_question_v2("question", object(), model_name="fake", thread_id=None, trace_meta=None)
    assert seen["config"] == {"configurable": {"thread_id": None, "user_id": "default"}}


def test_trace_meta_adds_top_level_langgraph_fields(monkeypatch):
    runner = _load_runner()
    from agent.v2 import graph as graph_module
    seen = {}
    class StubGraph:
        def _runtime(self):
            return {"handoffs": [], "hops": 0, "cost_usd": 0.0, "last_rejection": None}
        def invoke(self, inputs, config=None):
            seen["config"] = config
            return {"answer": Answer(), "handoffs": [], "called": [], "cost_usd": 0.0}
    monkeypatch.setattr(graph_module, "build_supervisor_graph", lambda *a, **k: StubGraph())
    meta = {"run_name": "Q01.r1.t1", "tags": ["v2", "abc"], "metadata": {"qid": "Q01"}}
    runner.answer_question_v2("question", object(), model_name="fake", trace_meta=meta)
    assert seen["config"] == {
        "configurable": {"thread_id": None, "user_id": "default"},
        **meta,
    }


def test_openai_adapter_wraps_parse_capable_client_only_when_tracing(monkeypatch):
    import openai
    import langsmith.wrappers
    import agent.llm as agent_llm

    raw, wrapped = object(), object()
    actual_wrapper = langsmith.wrappers.wrap_openai
    monkeypatch.setattr(agent_llm, "get_tracing_config", lambda: {"tracing": True, "project": "test"})
    monkeypatch.setattr(openai, "OpenAI", lambda: raw)
    monkeypatch.setattr(langsmith.wrappers, "wrap_openai", lambda client: wrapped)
    instance = agent_llm.OpenAIAgentLLM(model="test-model")
    assert instance.client is wrapped
    source = inspect.getsource(actual_wrapper)
    assert "client.responses.parse" in source


def test_rejections_capture_each_blocked_draft(monkeypatch, tmp_path):
    from collections import deque
    from agent.v2 import graph as graph_module
    from agent.v2.contract import Plan

    evidence = Evidence(
        tool_call_id="forecast-h1-t1", tool="get_forecast",
        args={"as_of": "2020-07-01", "model": "M1"},
        rows=[{"date": "2020-07-01", "model": "M1", "floored_30d": 0.2}],
        source="forecasts.parquet", data_range=("2020-01-02", "2026-10-12"),
    )
    handoff = Handoff(
        task="forecast", agent="forecast", inputs={}, evidence=[evidence], status="ok",
        hop=1, cost_usd_hop=0.0, cost_usd_so_far=0.0,
    )
    class Fake:
        model = "fake"
        def plan(self, question): return Plan(agents=["forecast"], subtasks={"forecast": "forecast"}), Usage(0, 0)
        def contextualize(self, question, ctx): return Resolved(standalone_question=question, used_turns=[], used_defaults=[]), Usage(0, 0)
        def subagent_route(self, *args): raise AssertionError("subagent is stubbed")
        def draft_v2(self, question, handoffs, rejection):
            return drafts.popleft(), Usage(0, 0)
    drafts = deque([
        Answer(forecast="0.999", text="unsupported"),
        Answer(forecast="0.2", text="supported"),
    ])
    class StubSubagent:
        def invoke(self, inputs): return {"handoff": handoff}
    monkeypatch.setattr(graph_module, "build_subagent", lambda *a, **k: StubSubagent())
    checks = deque([(False, "unsupported number 0.999"), (True, "")])
    monkeypatch.setattr(graph_module, "verify_multi", lambda *a, **k: checks.popleft())
    from agent.v2.graph import answer_question_v2
    result = answer_question_v2("What is the forecast?", Fake(), model_name="fake", results_dir=tmp_path)
    assert result.rejections == [{"attempt": 1, "reason": "unsupported number 0.999", "draft_text": "30 sessions 0.999 unsupported"}]
    assert result.last_rejection is None


def test_final_scoring_rejects_manual_override_of_numeric_result(tmp_path):
    scorer_spec = importlib.util.spec_from_file_location("score_agent_eval_v2_under_test", ROOT / "scripts" / "score_agent_eval_v2.py")
    scorer = importlib.util.module_from_spec(scorer_spec)
    assert scorer_spec.loader is not None
    scorer_spec.loader.exec_module(scorer)
    frame = pd.DataFrame([{
        "qid": "Q01", "rep": 1, "turn": 1, "needs_review": False,
        "review_fields": [], "routing_ok": True, "numeric_ok": True,
        "tool_args_ok": True, "sign_ok": True, "winner_ok": True, "n_ok": True,
        "citation_ok": True, "memory_args_ok": None, "refusal_ok": None,
        "rejections": [], "cost_usd": 0.01, "latency_s": 1.0,
        "hops": 1, "error": None,
    }])
    manual = tmp_path / "manual.csv"
    manual.write_text("qid,rep,turn,field,value,reviewer,reason\nQ01,1,1,numeric_ok,false,reviewer,try to flip\n", encoding="utf-8")
    with pytest.raises(ValueError, match="may not override an automatic result"):
        scorer._manual_index(manual, frame)


def test_langsmith_export_filters_eval_id_and_root_runs(tmp_path, capsys):
    export_spec = importlib.util.spec_from_file_location("export_langsmith_runs_under_test", ROOT / "scripts" / "export_langsmith_runs.py")
    exporter = importlib.util.module_from_spec(export_spec)
    assert export_spec.loader is not None
    export_spec.loader.exec_module(exporter)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    matching = SimpleNamespace(
        parent_run_id=None, extra={"metadata": {"eval_run_id": "wanted"}},
        name="Q01.r1.t1", id="run-1", start_time=now, end_time=now,
        total_tokens=17, error=None,
    )
    other_eval = SimpleNamespace(**{**matching.__dict__, "extra": {"metadata": {"eval_run_id": "other"}}})
    child = SimpleNamespace(**{**matching.__dict__, "parent_run_id": "parent"})
    class Client:
        def list_runs(self, **kwargs):
            assert kwargs == {"project_name": "eval project", "is_root": True}
            return iter([matching, other_eval, child])
        def get_run_url(self, run, **kwargs):
            return f"https://trace.invalid/{run.id}"
    target = exporter.export_runs("eval project", "wanted", repo_root=tmp_path, client=Client())
    assert capsys.readouterr().out.strip() == "1"
    frame = pd.read_csv(target)
    assert list(frame.columns) == ["run_name", "run_id", "url", "latency_s", "total_tokens", "error"]
    assert frame.iloc[0].run_id == "run-1"
