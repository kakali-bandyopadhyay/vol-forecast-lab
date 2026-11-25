"""Lean first-turn Agent SDK evaluation, with an offline-only dry run."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
import json
import os
import re
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

import pandas as pd

from agent.schema import Answer
from agent.v2.scoring import args_match, numeric_match, routing_ok
from agent.v3 import MODEL_ID, SDK_VERSION
from agent.v3.agents import build_options
from agent.v3.verify import verify_answer

QUESTIONS_PATH = REPO_ROOT / "prereg" / "questions_v2.json"
DEFAULT_OUT = REPO_ROOT / "results" / "agent_v3"
TRACE_PATH = DEFAULT_OUT / "smoke" / "trace.jsonl"
BASELINE_PATH = REPO_ROOT / "results" / "agent_v2" / "eval_v2_turns.parquet"
NUMBER_TOKEN = re.compile(r"(?<![\w.])[+-]?(?:\d+(?:,\d{3})*(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TRACE_SECRET = re.compile(r"(?i)(sk-ant-[A-Za-z0-9_-]+|bearer\s+[A-Za-z0-9._~-]+|ANTHROPIC_API_KEY\s*[:=]\s*\S+)")


def _first_turns(question_ids: set[str] | None = None) -> list[tuple[dict, dict]]:
    data = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    return [
        (question, question["turns"][0]) for question in data["questions"]
        if question_ids is None or question["id"] in question_ids
    ]


def _expected_calls(turn: dict) -> list[dict]:
    return [{"tool": call["tool"], "args": dict(call.get("args", {}))}
            for call in turn.get("tool_calls", [])]


def _expected_numbers(value: Any, key: str = "") -> list[float]:
    values: list[float] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key.lower() in {"n", "count", "sample_count", "display_6dp", "display_3sf"}:
                continue
            values.extend(_expected_numbers(child, child_key))
    elif isinstance(value, list):
        for child in value:
            values.extend(_expected_numbers(child, key))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(float(value))
    return values


def _answer_numbers(answer: Answer) -> list[str]:
    body = " ".join(str(getattr(answer, key, "") or "") for key in (
        "forecast", "implied_variance", "spread", "text", "limitations", "refusal_reason"
    ))
    return NUMBER_TOKEN.findall(ISO_DATE.sub(" ", body))


def _score(question: dict, turn: dict, answer: Answer, agents: list[str], calls: list[dict]) -> dict:
    expected_calls = _expected_calls(turn)
    numeric_ok = all(numeric_match(_answer_numbers(answer), expected) for expected in _expected_numbers(turn.get("expected_answer", {})))
    routing = routing_ok(agents, turn.get("expected_agents", []))
    args_ok = len(calls) == len(expected_calls) and all(
        args_match(calls, expected["tool"], expected["args"]) for expected in expected_calls
    )
    expected_refusal = question["id"] in {"Q11", "Q12"}
    refusal_ok = bool(answer.refused) if expected_refusal else not answer.refused
    return {
        "numeric_ok": bool(numeric_ok), "routing_ok": bool(routing), "args_ok": bool(args_ok),
        "refusal_ok": refusal_ok, "correct": bool(numeric_ok and routing and args_ok and refusal_ok),
    }


def _expected_answer(question: dict, turn: dict) -> Answer:
    """Deterministic fake-model answer used only by --dry-run."""
    qid = question["id"]
    refusal = qid in {"Q11", "Q12"}
    tokens = [f"{value:.8g}" for value in _expected_numbers(turn.get("expected_answer", {}))]
    expected = turn.get("expected_answer", {})
    text = " ".join(tokens)
    if isinstance(expected, dict):
        if expected.get("winner"):
            text += f" {expected['winner']} has the lowest mean."
        if expected.get("sign"):
            text += f" The sign is {expected['sign']}."
        if expected.get("n"):
            text += f" Sample count {expected['n']}."
    if refusal:
        text = "I cannot answer this request from the available evidence."
    return Answer(
        as_of=None, horizon="30 sessions", text=text,
        refused=refusal,
        refusal_reason="could not verify" if refusal else None,
    )


class FakeModelClient:
    """Offline deterministic stand-in; never reads credentials or contacts a service."""

    def complete(self, question: dict, turn: dict) -> tuple[Answer, list[str], list[dict]]:
        return (
            _expected_answer(question, turn),
            list(turn.get("expected_agents", [])),
            _expected_calls(turn),
        )


def _dry_run_rows(repeats: int, question_ids: set[str] | None = None, *, score: bool = True) -> list[dict]:
    rows = []
    client = FakeModelClient()
    for rep in range(1, repeats + 1):
        for question, turn in _first_turns(question_ids):
            answer, agents, calls = client.complete(question, turn)
            scores = _score(question, turn, answer, agents, calls) if score else {
                "numeric_ok": None, "routing_ok": None, "args_ok": None,
                "refusal_ok": None, "correct": None,
            }
            rows.append({
                "qid": question["id"], "rep": rep, "turn": int(turn["turn"]), "prompt": turn["prompt"],
                "agents_called": json.dumps(agents), "tools_called": json.dumps([c["tool"] for c in calls]),
                "tool_calls": json.dumps(calls, default=str), "guardrail_denials": "[]",
                "verifier_ok": True, "verifier_reason": "fake-client dry run",
                "final_answer": answer.model_dump_json(), "refused": answer.refused,
                "answer_coerced": False,
                "cost_usd": 0.0, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0,
                "token_usage": "{}",
                **scores,
            })
    return rows


def _answer_payload_with_coercion(text: str | None) -> tuple[Any, bool]:
    if not text:
        raise ValueError("SDK returned no final answer")
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\r?\n(.*?)\r?\n```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end < start:
            raise ValueError("final answer is not JSON")
        payload = json.loads(stripped[start:end + 1])
    coerced = False
    if isinstance(payload, dict):
        payload = dict(payload)
        limitations = payload.get("limitations")
        if isinstance(limitations, list) and all(isinstance(item, str) for item in limitations):
            payload["limitations"] = "; ".join(limitations)
            coerced = True
        if "source_rows" not in payload:
            payload["source_rows"] = []
            coerced = True
    return payload, coerced


def _parse_answer_with_coercion(text: str | None) -> tuple[Answer, bool]:
    payload, coerced = _answer_payload_with_coercion(text)
    return Answer.model_validate(payload), coerced


def _parse_answer(text: str | None) -> Answer:
    answer, _coerced = _parse_answer_with_coercion(text)
    return answer


def _parse_answer_or_refuse(text: str | None) -> tuple[Answer, bool]:
    raw_text = text or ""
    coerced = False
    try:
        payload, coerced = _answer_payload_with_coercion(raw_text)
        return Answer.model_validate(payload), coerced
    except Exception:
        return Answer(refused=True, refusal_reason="unparseable answer", text=raw_text), coerced


def _trace_safe(value: Any) -> Any:
    """Convert an SDK message to JSON-safe data and redact credential fields."""
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        safe = {}
        for key, child in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if (
                "header" in normalized or "apikey" in normalized or "authorization" in normalized
                or normalized in {"token", "accesstoken", "refreshtoken", "credential"}
            ):
                continue
            safe[str(key)] = _trace_safe(child)
        return safe
    if isinstance(value, (list, tuple)):
        return [_trace_safe(item) for item in value]
    if isinstance(value, str):
        return TRACE_SECRET.sub("[REDACTED]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return TRACE_SECRET.sub("[REDACTED]", str(value))


def _write_trace(trace_file, qid: str, rep: int, message: Any) -> None:
    if trace_file is None:
        return
    if is_dataclass(message):
        message_type = type(message).__name__
    else:
        message_type = type(message).__name__
    record = {"qid": qid, "rep": rep, "type": message_type, "message": _trace_safe(message)}
    trace_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    trace_file.flush()


async def _live_turn(question: dict, turn: dict, *, score: bool = True, trace_file=None, rep: int = 1) -> dict:
    from dotenv import load_dotenv
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

    # The key stays in process environment and is never logged, serialized, or copied.
    load_dotenv(REPO_ROOT / ".env", override=False)
    recorder: dict[str, Any] = {}
    options = build_options(recorder)
    started = time.perf_counter()
    result_message = None
    text_parts: list[str] = []
    async with ClaudeSDKClient(options) as client:
        await client.query(turn["prompt"])
        async for message in client.receive_response():
            _write_trace(trace_file, question["id"], rep, message)
            if isinstance(message, AssistantMessage):
                text_parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
            if isinstance(message, ResultMessage):
                result_message = message
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    raw_answer = result_message.result if result_message else "\n".join(text_parts)
    answer, answer_coerced = _parse_answer_or_refuse(raw_answer)
    calls = recorder.get("tool_calls", [])
    answer, verified, verify_reason, _ = verify_answer(answer, calls, recorder.get("tool_contexts", []))
    scores = _score(question, turn, answer, recorder.get("agents_called", []), calls) if score else {
        "numeric_ok": None, "routing_ok": None, "args_ok": None,
        "refusal_ok": None, "correct": None,
    }
    usage = getattr(result_message, "usage", None) or {}
    return {
        "agents_called": json.dumps(recorder.get("agents_called", [])),
        "tools_called": json.dumps([call["tool"] for call in calls]),
        "tool_calls": json.dumps(calls, default=str),
        "guardrail_denials": json.dumps(recorder.get("guardrail_denials", []), default=str),
        "verifier_ok": verified, "verifier_reason": verify_reason,
        "final_answer": answer.model_dump_json(), "refused": answer.refused,
        "answer_coerced": answer_coerced,
        "cost_usd": float(getattr(result_message, "total_cost_usd", 0) or 0),
        "latency_ms": int(getattr(result_message, "duration_ms", elapsed_ms) or elapsed_ms),
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "token_usage": json.dumps(usage, default=str),
        **scores,
    }


async def _live_rows(
    repeats: int, question_ids: set[str] | None = None, *, score: bool = True, trace_file=None
) -> list[dict]:
    rows = []
    for rep in range(1, repeats + 1):
        for question, turn in _first_turns(question_ids):
            result = await _live_turn(question, turn, score=score, trace_file=trace_file, rep=rep)
            rows.append({"qid": question["id"], "rep": rep, "turn": int(turn["turn"]),
                         "prompt": turn["prompt"], **result})
    return rows


def _metrics(frame: pd.DataFrame) -> dict:
    costs = frame["cost_usd"].fillna(0).astype(float).tolist()
    latencies = frame["latency_ms"].fillna(0).astype(float).tolist()
    return {
        "correct": int(frame["correct"].fillna(False).sum()), "total": int(len(frame)),
        "routing_rate": float(frame["routing_ok"].fillna(False).mean()) if len(frame) else 0.0,
        "args_rate": float(frame["args_ok"].fillna(False).mean()) if len(frame) else 0.0,
        "refusals": int(frame["refused"].fillna(False).sum()),
        "guardrail_denials": int(sum(len(json.loads(value or "[]")) for value in frame.get("guardrail_denials", []))),
        "verifier_blocks": int((~frame["verifier_ok"].fillna(False)).sum()),
        "cost_per_turn_median_usd": float(statistics.median(costs)) if costs else 0.0,
        "cost_total_usd": float(sum(costs)),
        "latency_p50_ms": float(statistics.median(latencies)) if latencies else 0.0,
        "latency_p95_ms": float(pd.Series(latencies).quantile(.95)) if latencies else 0.0,
    }


def write_baseline(out_dir: Path, question_ids: set[str] | None = None) -> dict:
    if not BASELINE_PATH.exists():
        raise FileNotFoundError(f"missing read-only v2 baseline: {BASELINE_PATH}")
    df = pd.read_parquet(BASELINE_PATH)
    first = df[df["turn"].astype(int) == 1]
    if question_ids is not None:
        first = first[first["qid"].isin(question_ids)]
    runs = []
    for rep, group in first.groupby("rep", dropna=False, sort=True):
        rows = group.rename(columns={"tool_args_ok": "args_ok", "auto_correct": "correct"}).copy()
        rows["verifier_ok"] = rows["stop_reason"].fillna("") != "could_not_verify"
        rows["guardrail_denials"] = "[]"
        rows["latency_ms"] = rows["latency_s"].fillna(0).astype(float) * 1000
        rows["cost_usd"] = rows["cost_usd"].fillna(0)
        runs.append({"rep": int(rep), "eval_run_id": str(group["eval_run_id"].iloc[0]), **_metrics(rows)})
    payload = {"source": str(BASELINE_PATH.relative_to(REPO_ROOT)), "turns_per_run": int(first["qid"].nunique()), "runs": runs}
    (out_dir / "v2_first_turn_baseline.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def write_outputs(
    rows: list[dict], out_dir: Path, *, dry_run: bool, smoke: bool = False,
    question_ids: set[str] | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_parquet(out_dir / "eval_v3_turns.parquet", index=False)
    if smoke:
        summary = {
            "label": "smoke – not scored", "rows": int(len(frame)),
            "question_ids": sorted(question_ids or set(frame["qid"].astype(str))),
            "model_id": MODEL_ID, "sdk_version": SDK_VERSION,
            "temperature": "SDK default", "dry_run": dry_run,
            "settings_isolation": {
                "setting_sources": [], "skills": [], "plugins": [],
                "note": "User/project/local settings, discovered skills, and plugins disabled.",
            },
        }
    else:
        summary = _metrics(frame)
        summary.update({
            "headline": f"{summary['correct']}/{summary['total']}",
            "model_id": MODEL_ID, "sdk_version": SDK_VERSION,
            "temperature": "SDK default", "dry_run": dry_run,
            "question_ids": sorted(question_ids or set(frame["qid"].astype(str))),
            "settings_isolation": {
                "setting_sources": [], "skills": [], "plugins": [],
                "note": "User/project/local settings, discovered skills, and plugins disabled.",
            },
        })
    (out_dir / "eval_v3_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    baseline = None if smoke else write_baseline(out_dir, question_ids)
    return {"summary": summary, "baseline_runs": len(baseline["runs"]) if baseline else 0, "rows": len(frame)}


def _parse_only(value: str | None) -> set[str] | None:
    if value is None:
        return None
    ids = {item.strip().upper() for item in value.split(",") if item.strip()}
    known = {question["id"] for question, _turn in _first_turns()}
    unknown = ids - known
    if not ids or unknown:
        raise ValueError("--only requires comma-separated question IDs from Q01 through Q12")
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", help="comma-separated question IDs, for example Q01,Q03")
    parser.add_argument("--smoke", action="store_true", help="write one unscored smoke result under the smoke/ subdirectory")
    parser.add_argument("--trace", action="store_true", help="write sanitized SDK message stream to results/agent_v3/smoke/trace.jsonl")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    try:
        question_ids = _parse_only(args.only)
    except ValueError as exc:
        parser.error(str(exc))
    if args.smoke:
        if question_ids is None:
            question_ids = {"Q01"}
        if len(question_ids) != 1:
            parser.error("--smoke accepts exactly one question ID")
    if args.trace and args.dry_run:
        parser.error("--trace captures SDK messages and requires a live run")
    if args.dry_run:
        rows = _dry_run_rows(args.repeats, question_ids, score=not args.smoke)
    elif args.trace:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("w", encoding="utf-8") as trace_file:
            rows = asyncio.run(_live_rows(args.repeats, question_ids, score=not args.smoke, trace_file=trace_file))
    else:
        rows = asyncio.run(_live_rows(args.repeats, question_ids, score=not args.smoke))
    out_dir = args.out_dir.resolve() / "smoke" if args.smoke else args.out_dir.resolve()
    output = write_outputs(rows, out_dir, dry_run=args.dry_run, smoke=args.smoke, question_ids=question_ids)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
