from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import json

import pandas as pd

from scripts import run_agent_eval_v3 as runner


def test_dry_run_writes_12_rows_and_expected_columns(tmp_path, monkeypatch):
    rows = runner._dry_run_rows(1)
    assert len(rows) == 12
    monkeypatch.setattr(runner, "BASELINE_PATH", runner.BASELINE_PATH)
    output = runner.write_outputs(rows, tmp_path, dry_run=True)
    turns = pd.read_parquet(tmp_path / "eval_v3_turns.parquet")
    summary = json.loads((tmp_path / "eval_v3_summary.json").read_text(encoding="utf-8"))
    assert output["rows"] == 12
    assert len(turns) == 12
    assert {"qid", "turn", "agents_called", "tool_calls", "guardrail_denials", "verifier_ok",
            "final_answer", "answer_coerced", "cost_usd", "latency_ms", "input_tokens", "output_tokens", "token_usage"}.issubset(turns.columns)
    assert output["baseline_runs"] == 3
    assert summary["model_id"] == "claude-haiku-4-5-20251001"
    assert summary["dry_run"] is True
    assert summary["settings_isolation"]["setting_sources"] == []
    assert summary["settings_isolation"]["skills"] == []
    assert summary["settings_isolation"]["plugins"] == []


def test_only_smoke_uses_fake_client_and_writes_unscored_q01(tmp_path, capsys):
    result = runner.main([
        "--only", "Q01", "--smoke", "--dry-run", "--out-dir", str(tmp_path),
    ])
    assert result == 0
    smoke_dir = tmp_path / "smoke"
    turns = pd.read_parquet(smoke_dir / "eval_v3_turns.parquet")
    summary = json.loads((smoke_dir / "eval_v3_summary.json").read_text(encoding="utf-8"))
    assert len(turns) == 1
    assert turns.loc[0, "qid"] == "Q01"
    assert turns.loc[0, "correct"] is None or pd.isna(turns.loc[0, "correct"])
    assert summary["label"] == "smoke – not scored"
    assert summary["question_ids"] == ["Q01"]
    assert summary["settings_isolation"]["setting_sources"] == []
    assert summary["settings_isolation"]["skills"] == []
    assert summary["settings_isolation"]["plugins"] == []
    assert not (smoke_dir / "v2_first_turn_baseline.json").exists()
    assert '"rows": 1' in capsys.readouterr().out


def test_trace_writer_redacts_credentials_and_headers():
    @dataclass
    class FakeSDKMessage:
        content: str
        headers: dict
        api_key: str

    output = StringIO()
    runner._write_trace(output, "Q01", 1, FakeSDKMessage(
        content="response mentions sk-ant-secret-value",
        headers={"x-api-key": "secret"},
        api_key="secret",
    ))
    trace = output.getvalue()
    assert '"type": "FakeSDKMessage"' in trace
    assert "headers" not in trace
    assert "api_key" not in trace
    assert "secret" not in trace
    assert "[REDACTED]" in trace


def test_limited_coercions_preserve_other_type_mismatch():
    fence = chr(96) * 3
    final = (
        f'{fence}json\n{{"as_of":"2020-07-01","forecast":0.00255157,'
        f'"limitations":[],"refused":false}}\n{fence}'
    )
    payload, coerced = runner._answer_payload_with_coercion(final)
    assert coerced is True
    assert payload["limitations"] == ""
    assert payload["source_rows"] == []
    assert isinstance(payload["forecast"], (int, float))
    answer, answer_coerced = runner._parse_answer_or_refuse(final)
    assert answer.refused
    assert answer.refusal_reason == "unparseable answer"
    assert answer.text == final
    assert answer_coerced is True


def test_parse_answer_strips_one_json_code_fence():
    fence = chr(96) * 3
    raw = f'{fence}json\n{{"as_of":"2020-07-01","forecast":"0.0025","limitations":[],"refused":false}}\n{fence}'
    answer, answer_coerced = runner._parse_answer_or_refuse(raw)
    assert answer.as_of == "2020-07-01"
    assert answer.forecast == "0.0025"
    assert answer.limitations == ""
    assert answer.source_rows == []
    assert answer_coerced is True


def test_wrong_type_number_field_stays_unparseable():
    raw = '{"as_of":"2020-07-01","forecast":0.0025,"limitations":[],"refused":false}'
    answer, answer_coerced = runner._parse_answer_or_refuse(raw)
    assert answer.refused
    assert answer.refusal_reason == "unparseable answer"
    assert answer.text == raw
    assert answer_coerced is True


def test_unparseable_answer_keeps_raw_text_and_distinct_reason():
    raw = "```json\nnot-json\n```"
    answer, answer_coerced = runner._parse_answer_or_refuse(raw)
    assert answer.refused
    assert answer.refusal_reason == "unparseable answer"
    assert answer.text == raw
    assert answer_coerced is False
