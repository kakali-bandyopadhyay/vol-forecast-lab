"""Run the frozen v2 evaluation or the v1-on-v2 regression set."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

import pandas as pd

from agent.graph import PRICE_SOURCE_DATE, PRICES_USD_PER_1M
from agent.tools import RESULTS_DIR
from agent.verifier import ISO_DATE, NUMBER_TOKEN
from agent.v2.graph import answer_question_v2
from agent.v2.persistence import open_memory
from agent.v2.scoring import args_match, numeric_match, routing_ok, sign_word

QUESTIONS_PATH = REPO_ROOT / "prereg" / "questions_v2.json"
FROZEN_COMMIT = "1c9922a"
DEFAULT_OUT = REPO_ROOT / "results" / "agent_v2"
V1_QUESTIONS = REPO_ROOT / "data" / "agent" / "eval_questions.csv"
V1_RESULTS = REPO_ROOT / "results" / "agent" / "agent_eval.parquet"
V1_SUMMARY = REPO_ROOT / "results" / "agent" / "agent_eval_summary.json"
V2_TOTAL_CAP = 5.00
V2_ATTEMPT_CAP = 0.25
V1_TOTAL_CAP = 2.00


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _sha256(data: bytes) -> str:
    # The repository marks JSON as text; hash its canonical LF bytes so a
    # Windows checkout with autocrlf does not spuriously fail the freeze gate.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def freeze_gate(eval_set: str, *, out_dir: Path = DEFAULT_OUT) -> dict[str, str]:
    """Reject live eval against code changes, changed prereg, or existing output."""
    status = _git("status", "--porcelain", "--untracked-files=all")
    status_lines = status.splitlines()
    tracked_changes = [line for line in status_lines if not line.startswith("??")]
    if tracked_changes:
        raise RuntimeError("live evaluation requires a clean tracked tree: " + tracked_changes[0])
    untracked_code = []
    code_dirs = {"agent", "scripts", "tests"}
    for line in status_lines:
        if not line.startswith("??"):
            continue
        path = line[3:].strip().strip('"').replace("\\", "/")
        parts = Path(path).parts
        if len(parts) > 1 and parts[0] in code_dirs and path.lower().endswith(".py"):
            untracked_code.append(path)
    if untracked_code:
        raise RuntimeError("live evaluation requires no untracked code files: " + untracked_code[0])

    current = QUESTIONS_PATH.read_bytes()
    frozen = subprocess.run(
        ["git", "show", f"{FROZEN_COMMIT}:prereg/questions_v2.json"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    if _sha256(current) != _sha256(frozen):
        raise RuntimeError("prereg/questions_v2.json does not match frozen commit 1c9922a")
    if eval_set == "v2" and (DEFAULT_OUT / "eval_v2_turns.parquet").exists():
        raise RuntimeError("results/agent_v2/eval_v2_turns.parquet already exists")
    return {
        "git_head": _git("rev-parse", "HEAD"),
        "prereg_commit": FROZEN_COMMIT,
        "questions_sha256": _sha256(current),
    }


def _answer_text(answer) -> str:
    if answer is None:
        return ""
    return " ".join(
        str(value)
        for value in (
            getattr(answer, "as_of", None),
            getattr(answer, "horizon", None),
            getattr(answer, "forecast", None),
            getattr(answer, "implied_variance", None),
            getattr(answer, "spread", None),
            getattr(answer, "text", None),
            getattr(answer, "limitations", None),
            getattr(answer, "refusal_reason", None),
        )
        if value
    )


def _evidence(result) -> list[dict]:
    return [item for handoff in result.handoffs for item in handoff.get("evidence", [])]


def _recursive_expected_numbers(value, key="") -> list[float]:
    values: list[float] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key.lower() in {"n", "count", "sample_count", "display_6dp", "display_3sf"}:
                continue
            values.extend(_recursive_expected_numbers(child, child_key))
    elif isinstance(value, list):
        for child in value:
            values.extend(_recursive_expected_numbers(child, key))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(float(value))
    return values


def _expected_signs(value) -> list[str]:
    signs: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "sign" and child in {"positive", "negative"}:
                signs.append(child)
            else:
                signs.extend(_expected_signs(child))
    elif isinstance(value, list):
        for child in value:
            signs.extend(_expected_signs(child))
    return signs


def _eligible_memory_expectation(qid: str, turn: int) -> dict | None:
    expected = {
        ("Q08", 2): ("get_forecast", {"as_of": "2020-07-01", "model": "M0"}),
        ("Q08", 3): ("get_forecast", {"as_of": "2020-07-01", "model": "M1"}),
        ("Q09", 2): ("compare_models", {"start": "2021-01-01", "end": "2021-12-31", "metric": "qlike"}),
        ("Q10", 2): ("event_effect", {"event": "fomc", "component": "on"}),
        ("Q10", 3): ("event_effect", {"event": "fomc", "component": "oc"}),
    }
    item = expected.get((qid, turn))
    if item is None:
        return None
    return {"tool": item[0], "args": item[1]}


def score_turn(question: dict, turn: dict, result, error: str | None = None) -> dict:
    """Apply the preregistered automatic checks; retain uncertain fields for review."""
    qid = question["id"]
    qturn = int(turn["turn"])
    evidence = _evidence(result) if result is not None else []
    answer = result.answer if result is not None else None
    answer_string = _answer_text(answer)
    numeric_text = ISO_DATE.sub(" ", " ".join(
        str(getattr(answer, name, "") or "")
        for name in ("forecast", "implied_variance", "spread", "text", "limitations")
    ))
    tokens = NUMBER_TOKEN.findall(numeric_text)
    expected_answer = turn.get("expected_answer", {})
    numeric_ok = all(numeric_match(tokens, value) for value in _recursive_expected_numbers(expected_answer))
    signs = _expected_signs(expected_answer)
    found_sign = sign_word(answer_string)
    sign_conflict = found_sign == "conflict" and bool(signs)
    sign_ok = all(
        found_sign != ("negative" if sign == "positive" else "positive")
        for sign in signs
    ) and numeric_ok

    winner = expected_answer.get("winner") if isinstance(expected_answer, dict) else None
    winner_ok: bool | None = True
    winner_ambiguous = False
    if winner:
        models = re.findall(r"\bM[0-4]\b", answer_string, re.IGNORECASE)
        winner_marks = re.findall(r"(?:lowest|winner|best)[^.!?]{0,32}\b(M[0-4])\b", answer_string, re.IGNORECASE)
        if winner_marks:
            winner_ok = winner_marks[-1].upper() == str(winner).upper()
        elif len(set(model.upper() for model in models)) == 1:
            winner_ok = models[0].upper() == str(winner).upper()
        else:
            winner_ok = None
            winner_ambiguous = True

    expected_n = expected_answer.get("n") if isinstance(expected_answer, dict) else None
    n_ok = True if expected_n is None else bool(
        re.search(rf"(?<!\d){int(expected_n)}(?!\d)", ISO_DATE.sub(" ", answer_string))
    )

    accepted_ids = turn.get("acceptable_chunk_ids", [])
    if accepted_ids:
        citation_ids = [
            item.get("chunk_id") if isinstance(item, dict) else getattr(item, "chunk_id", None)
            for item in (answer.citations if answer else [])
        ]
        search_evidence = [item for item in evidence if item.get("tool") == "search_fomc"]
        citation_ok = any(value in accepted_ids for value in citation_ids) and any(
            item.get("args", {}).get("doc_type") == turn.get("citation_doc_type", "statement")
            for item in search_evidence
        )
    else:
        citation_ok = True

    expected_agents = turn.get("expected_agents", [])
    agents_called = result.agents_called if result is not None else []
    routing = routing_ok(agents_called, expected_agents)
    expected_memory = _eligible_memory_expectation(qid, qturn)
    memory_args_ok = None if expected_memory is None else args_match(evidence, expected_memory["tool"], expected_memory["args"])
    expected_calls = _expected_tool_calls(turn)
    if not expected_calls and expected_memory is not None:
        expected_calls = [expected_memory]
    actual_calls = _tool_attempts(result)
    tool_args_ok = len(actual_calls) == len(expected_calls) and all(
        args_match(actual_calls, call["tool"], call["args"]) for call in expected_calls
    )
    if expected_calls and result is None:
        tool_args_ok = False

    refusal_ok: bool | None = None
    if qid == "Q11":
        refusal_ok = bool(answer and answer.refused and not agents_called and not (result.tools_called if result else []))
    elif qid == "Q12":
        has_range_check = bool(result and "get_forecast" in result.tools_called and any(
            handoff.get("status") == "out_of_range" for handoff in result.handoffs
        ))
        non_date_numbers = NUMBER_TOKEN.findall(ISO_DATE.sub(" ", answer_string))
        refusal_ok = bool(
            answer and answer.refused and has_range_check
            and all(len(token.replace(".", "").lstrip("+-0")) < 3 for token in non_date_numbers)
        )

    auto_fields = [routing, tool_args_ok, numeric_ok, sign_ok, n_ok, citation_ok]
    if winner_ok is not None:
        auto_fields.append(winner_ok)
    if memory_args_ok is not None:
        auto_fields.append(memory_args_ok)
    if refusal_ok is not None:
        auto_fields.append(refusal_ok)
    has_narrative = bool(turn.get("required_points"))
    rejections = result.rejections if result is not None else []
    needs_review = bool(
        has_narrative or winner_ambiguous or qid in {"Q11", "Q12"}
        or rejections or error or sign_conflict
    )
    review_reasons = []
    if has_narrative:
        review_reasons.append("narrative points")
    if winner_ambiguous:
        review_reasons.append("winner ambiguous")
    if qid in {"Q11", "Q12"}:
        review_reasons.append("refusal review")
    if rejections:
        review_reasons.append("verifier rejection")
    if error:
        review_reasons.append("runtime error")
    if sign_conflict:
        review_reasons.append("sign-word conflict")
    review_fields = []
    if has_narrative:
        review_fields.append("narrative_ok")
    if sign_conflict and "narrative_ok" not in review_fields:
        review_fields.append("narrative_ok")
    if winner_ambiguous:
        review_fields.append("winner_ok")
    if qid in {"Q11", "Q12"}:
        review_fields.append("refusal_ok")
    if rejections:
        review_fields.append("block_class")
    return {
        "routing_ok": routing,
        "tool_args_ok": tool_args_ok,
        "numeric_ok": numeric_ok,
        "sign_ok": sign_ok,
        "winner_ok": winner_ok,
        "n_ok": n_ok,
        "citation_ok": citation_ok,
        "memory_args_ok": memory_args_ok,
        "refusal_ok": refusal_ok,
        "auto_correct": bool(all(auto_fields) and not sign_conflict),
        "needs_review": needs_review,
        "review_reason": "; ".join(review_reasons),
        "review_fields": review_fields,
    }


def _tool_args(result) -> list[dict]:
    return [
        {"tool": item.get("tool"), "args": item.get("args", {})}
        for item in _evidence(result)
    ] if result is not None else []


def _tool_attempts(result) -> list[dict]:
    if result is None:
        return []
    attempted = []
    for handoff in result.handoffs:
        evidence = handoff.get("evidence", [])
        if evidence:
            attempted.extend({"tool": item.get("tool"), "args": item.get("args", {})} for item in evidence)
        else:
            attempted.extend(dict(item) for item in handoff.get("inputs", {}).get("tool_attempts", []))
    return attempted


def _regression_diagnostics(result) -> dict:
    answer = getattr(result, "answer", None) if result is not None else None
    handoffs = getattr(result, "handoffs", []) if result is not None else []
    citations = [
        citation.model_dump(mode="json") if hasattr(citation, "model_dump") else dict(citation)
        for citation in (getattr(answer, "citations", []) if answer else [])
    ]
    evidence = []
    for handoff in handoffs:
        evidence.extend(handoff.get("evidence", []))
    return {
        "answer_fields": answer.model_dump(mode="json", exclude={"source_rows"}) if answer else {},
        "answer_text": _answer_text(answer),
        "citations": citations,
        "stop_reason": getattr(result, "stop_reason", None) if result is not None else None,
        "attempts": int(getattr(result, "attempts", 0) or 0) if result is not None else 0,
        "rejections": list(getattr(result, "rejections", []) or []) if result is not None else [],
        "last_rejection": getattr(result, "last_rejection", None) if result is not None else None,
        "hops": int(getattr(result, "hops", 0) or 0) if result is not None else 0,
        "replans": int(getattr(result, "replans", 0) or 0) if result is not None else 0,
        "evidence_statuses": [handoff.get("status") for handoff in handoffs],
        "handoff_details": [handoff.get("detail") for handoff in handoffs],
        "tool_attempts": _tool_attempts(result),
        "evidence": evidence,
    }


def _expected_tool_calls(turn: dict) -> list[dict]:
    calls = turn.get("tool_calls")
    if calls is None and turn.get("tool_call"):
        calls = [turn["tool_call"]]
    calls = [dict(item) for item in (calls or [])]
    for item in calls:
        if item.get("tool") == "search_fomc":
            item["args"] = {k: v for k, v in item.get("args", {}).items() if k != "query"}
    if calls:
        return calls

    event_key = turn.get("row_key") or turn.get("event_row_key")
    if event_key:
        return [{"tool": "event_effect", "args": {"event": event_key["term"], "component": event_key["component"]}}]
    memory_write = turn.get("memory_write", {})
    if memory_write.get("default_model"):
        date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", turn.get("prompt", ""))
        if date:
            return [{"tool": "get_forecast", "args": {"as_of": date.group(0), "model": memory_write["default_model"]}}]
    if memory_write.get("default_metric"):
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", turn.get("prompt", ""))
        if len(dates) >= 2:
            return [{"tool": "compare_models", "args": {"start": dates[0], "end": dates[1], "metric": str(memory_write["default_metric"]).lower()}}]
    return []


def _row_for_turn(eval_run_id, repetition, thread_id, user_id, question, turn, result, error=None):
    answer = result.answer if result is not None else None
    handoffs = result.handoffs if result is not None else []
    evidence = _evidence(result) if result is not None else []
    answer_fields = answer.model_dump(mode="json", exclude={"source_rows"}) if answer is not None else {}
    row = {
        "eval_run_id": eval_run_id,
        "qid": question["id"],
        "rep": repetition,
        "turn": int(turn["turn"]),
        "thread_id": thread_id,
        "user_id": user_id,
        "prompt": turn["prompt"],
        "resolved_question": result.resolved_question if result is not None else None,
        "agents_called": result.agents_called if result is not None else [],
        "tools_called": result.tools_called if result is not None else [],
        "tool_args_per_evidence": _tool_args(result),
        "tool_attempts": _tool_attempts(result),
        "evidence_statuses": [handoff.get("status") for handoff in handoffs],
        "handoff_details": [handoff.get("detail") for handoff in handoffs],
        "citations": [citation.model_dump(mode="json") for citation in (answer.citations if answer else [])],
        "answer_fields": answer_fields,
        "answer_text": _answer_text(answer),
        "refused": bool(answer and answer.refused),
        "refusal_reason": answer.refusal_reason if answer else None,
        "stop_reason": result.stop_reason if result is not None else None,
        "attempts": result.attempts if result is not None else 0,
        "rejections": result.rejections if result is not None else [],
        "hops": result.hops if result is not None else 0,
        "replans": result.replans if result is not None else 0,
        "memory_reads": result.memory_reads if result is not None else [],
        "memory_writes": result.memory_writes if result is not None else [],
        "cost_usd": result.cost_usd if result is not None else None,
        "latency_s": result.latency_s if result is not None else None,
        "error": error,
        "question_run_incomplete": False,
    }
    row.update(score_turn(question, turn, result, error))
    return row


def _thread_id(question: dict, repetition: int, turn: dict) -> str:
    pattern = question.get("thread_id_pattern")
    if isinstance(pattern, list):
        index = int(turn.get("thread", 1)) - 1
        pattern = pattern[index]
    if pattern:
        return pattern.format(repetition=repetition)
    return f"{question['id']}-r{repetition}"


def _trace_meta(eval_set: str, eval_run_id: str, qid: str, rep: int, turn: int, thread_id: str | None) -> dict:
    return {
        "run_name": f"{qid}.r{rep}.t{turn}",
        "tags": [eval_set, eval_run_id],
        "metadata": {
            "qid": qid,
            "rep": rep,
            "turn": turn,
            "thread_id": thread_id,
            "eval_run_id": eval_run_id,
        },
    }


def _save_rows(rows: list[dict], out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _regression_output_paths(out_dir: Path) -> tuple[Path, Path]:
    data_path = out_dir / "regression_v1_on_v2.parquet"
    summary_path = out_dir / "regression_summary.json"
    numbered = list(out_dir.glob("regression_v1_on_v2_run*.parquet")) + list(
        out_dir.glob("regression_summary_run*.json")
    )
    if not data_path.exists() and not summary_path.exists() and not numbered:
        return data_path, summary_path
    index = 1
    while True:
        candidate_data = out_dir / f"regression_v1_on_v2_run{index}.parquet"
        candidate_summary = out_dir / f"regression_summary_run{index}.json"
        if not candidate_data.exists() and not candidate_summary.exists():
            return candidate_data, candidate_summary
        index += 1


def _run_v2(llm, *, repeats: int, out_dir: Path, eval_run_id: str, trace: bool, provenance: dict, search_fn=None, memory_base: Path | None = None) -> tuple[list[dict], dict]:
    frozen = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = []
    spent_total = 0.0
    incomplete = False
    memory_base = (memory_base or (REPO_ROOT / "runs" / "memory" / "eval_v2")) / eval_run_id

    # Repetition-major order is part of the preregistered run procedure.
    total_stopped = False
    question_list = frozen["questions"]
    for rep in range(1, repeats + 1):
        saver, store, close = open_memory(memory_base / f"r{rep}")
        try:
            for question_index, question in enumerate(question_list):
                spent_question = 0.0
                question_start = len(rows)
                question_stopped = False
                user_id = f"eval-r{rep}-{question['id']}"
                turns = question["turns"]
                for turn_index, turn in enumerate(turns):
                    thread_id = _thread_id(question, rep, turn)
                    question_remaining = V2_ATTEMPT_CAP - spent_question
                    total_remaining = V2_TOTAL_CAP - spent_total
                    row_budget = min(question_remaining, total_remaining)
                    if row_budget <= 0:
                        incomplete = True
                        question_limited = question_remaining < total_remaining
                        if not question_limited:
                            total_stopped = True
                            for prior_row in rows[question_start:]:
                                prior_row["question_run_incomplete"] = True
                            break
                        question_stopped = True
                        for prior_row in rows[question_start:]:
                            prior_row["question_run_incomplete"] = True
                        for skipped_turn in turns[turn_index:]:
                            skipped_thread_id = _thread_id(question, rep, skipped_turn)
                            skipped = _row_for_turn(
                                eval_run_id, rep, skipped_thread_id, user_id, question, skipped_turn, None,
                                "Budget exhausted for this question-run; turn not run",
                            )
                            skipped["stop_reason"] = "not_run_budget"
                            skipped["question_run_incomplete"] = True
                            rows.append(skipped)
                        break
                    question_limited = question_remaining < total_remaining
                    trace_meta = _trace_meta("v2", eval_run_id, question["id"], rep, int(turn["turn"]), thread_id) if trace else None
                    try:
                        result = answer_question_v2(
                            turn["prompt"], llm, model_name=getattr(llm, "model_name", getattr(llm, "model", "unknown")),
                            thread_id=thread_id, user_id=user_id, checkpointer=saver, store=store,
                            max_cost_usd=row_budget, trace_meta=trace_meta, search_fn=search_fn,
                        )
                        row = _row_for_turn(eval_run_id, rep, thread_id, user_id, question, turn, result)
                        if result.stop_reason == "cost_cap":
                            row["error"] = "CostCapExceeded: question attempt reached its budget"
                            row["needs_review"] = True
                            row["review_reason"] = "cost cap"
                            incomplete = True
                            if question_limited:
                                question_stopped = True
                            else:
                                total_stopped = True
                    except Exception as exc:
                        cost = getattr(exc, "cost_usd", None)
                        row = _row_for_turn(
                            eval_run_id, rep, thread_id, user_id, question, turn, None,
                            f"{type(exc).__name__}: {exc}",
                        )
                        row["cost_usd"] = float(cost) if cost is not None else None
                        if type(exc).__name__ == "CostCapExceeded":
                            incomplete = True
                            if question_limited:
                                question_stopped = True
                            else:
                                total_stopped = True
                    rows.append(row)
                    if row.get("cost_usd") is not None:
                        spent_question += float(row["cost_usd"])
                        spent_total += float(row["cost_usd"])
                    more_evaluation = (
                        turn_index + 1 < len(turns)
                        or question_index + 1 < len(question_list)
                        or rep < repeats
                    )
                    if spent_total >= V2_TOTAL_CAP and more_evaluation:
                        incomplete = True
                        total_stopped = True
                    if question_stopped:
                        incomplete = True
                        for prior_row in rows[question_start:]:
                            prior_row["question_run_incomplete"] = True
                        for skipped_turn in turns[turn_index + 1:]:
                            skipped_thread_id = _thread_id(question, rep, skipped_turn)
                            skipped = _row_for_turn(
                                eval_run_id, rep, skipped_thread_id, user_id, question, skipped_turn, None,
                                "Budget exhausted for this question-run; turn not run",
                            )
                            skipped["stop_reason"] = "not_run_budget"
                            skipped["question_run_incomplete"] = True
                            rows.append(skipped)
                        break
                    if total_stopped:
                        for prior_row in rows[question_start:]:
                            prior_row["question_run_incomplete"] = True
                        break
                if total_stopped:
                    break
        finally:
            close()
        if total_stopped:
            break
    return rows, {
        **provenance,
        "eval_run_id": eval_run_id,
        "n_rows": len(rows),
        "repeats": repeats,
        "incomplete": incomplete,
        "spent_total_usd": spent_total,
        "model": getattr(llm, "model_name", getattr(llm, "model", "unknown")),
        "price_source_date": PRICE_SOURCE_DATE,
    }


def _run_v1_regression(llm, *, repeats: int, out_dir: Path, eval_run_id: str, trace: bool, provenance: dict, search_fn=None) -> tuple[list[dict], dict]:
    from scripts.run_agent_eval import score_exact

    questions = pd.read_csv(V1_QUESTIONS, dtype={"expected_answer": str}).to_dict(orient="records")
    rows: list[dict] = []
    spent_total = 0.0
    incomplete = False
    for rep in range(1, repeats + 1):
        for source in questions:
            qid = str(source["qid"])
            user_id = f"regression-r{rep}-{qid}"
            thread_id = f"{qid}-r{rep}"
            budget = min(0.25, V1_TOTAL_CAP - spent_total)
            if budget <= 0:
                incomplete = True
                break
            trace_meta = _trace_meta("v1-regression", eval_run_id, qid, rep, 1, None) if trace else None
            try:
                result = answer_question_v2(
                    str(source["question"]), llm,
                    model_name=getattr(llm, "model_name", getattr(llm, "model", "unknown")),
                    thread_id=None, user_id=user_id, max_cost_usd=budget, trace_meta=trace_meta,
                    search_fn=search_fn,
                )
                raw_expected_tool = source.get("expected_tool")
                expected_tool = "" if raw_expected_tool is None or pd.isna(raw_expected_tool) else str(raw_expected_tool).strip()
                refused_expected = str(source.get("expected_answer", "")).strip().upper() == "REFUSED"
                correct_tool = (
                    not result.tools_called if not expected_tool else
                    set(result.tools_called) == {expected_tool}
                )
                row = {
                    "eval_run_id": eval_run_id, "qid": qid, "rep": rep, "turn": 1,
                    "thread_id": None, "user_id": user_id,
                    "prompt": str(source["question"]), "resolved_question": result.resolved_question,
                    "agents_called": result.agents_called, "tools_called": result.tools_called,
                    "tool_args_per_evidence": _tool_args(result),
                    "refused": result.answer.refused,
                    "refusal_reason": result.answer.refusal_reason,
                    "correct_tool": bool(correct_tool),
                    "exact_match": bool(refused_expected or score_exact(result.answer, str(source.get("expected_answer", "")))),
                    "refusal_correct": bool(result.answer.refused) == _truthy(source.get("expect_refusal", False)),
                    "cost_usd": result.cost_usd, "latency_s": result.latency_s,
                    "error": None,
                }
                row.update(_regression_diagnostics(result))
                spent_total += result.cost_usd
            except Exception as exc:
                row = {
                    "eval_run_id": eval_run_id, "qid": qid, "rep": rep, "turn": 1,
                    "thread_id": None, "user_id": user_id,
                    "prompt": str(source["question"]), "resolved_question": str(source["question"]),
                    "agents_called": [], "tools_called": [], "tool_args_per_evidence": [],
                    "refused": False, "refusal_reason": None, "correct_tool": False,
                    "exact_match": False, "refusal_correct": False,
                    "cost_usd": getattr(exc, "cost_usd", None), "latency_s": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                row.update(_regression_diagnostics(None))
                if row["cost_usd"] is not None:
                    spent_total += float(row["cost_usd"])
            rows.append(row)
        if incomplete:
            break
    summary = {
        **provenance,
        "eval_run_id": eval_run_id,
        "n_rows": len(rows),
        "repeats": repeats,
        "incomplete": incomplete,
        "spent_total_usd": spent_total,
        "model": getattr(llm, "model_name", getattr(llm, "model", "unknown")),
        "price_source_date": PRICE_SOURCE_DATE,
    }
    if V1_SUMMARY.exists():
        summary["v1_baseline"] = json.loads(V1_SUMMARY.read_text(encoding="utf-8"))
    frame = pd.DataFrame(rows)
    headline = frame["exact_match"].fillna(False).astype(bool) & frame["refusal_correct"].fillna(False).astype(bool)
    costs = pd.to_numeric(frame["cost_usd"], errors="coerce")
    latencies = pd.to_numeric(frame["latency_s"], errors="coerce").dropna()
    def could_not_verify(row):
        fields = [row.get("stop_reason"), row.get("refusal_reason"), row.get("last_rejection")]
        return any("could not verify" in str(value).lower() for value in fields if value is not None)
    summary.update({
        "headline_correct": int(headline.sum()),
        "headline_count": int(headline.sum()),
        "headline_denominator": 45,
        "headline_accuracy": None if frame.empty else float(headline.mean()),
        "mean_cost_usd": None if costs.dropna().empty else float(costs.mean()),
        "latency_p50_s": None if latencies.empty else float(latencies.quantile(0.50)),
        "latency_p95_s": None if latencies.empty else float(latencies.quantile(0.95)),
        "n_could_not_verify": int(frame.apply(could_not_verify, axis=1).sum()),
        "n_verifier_blocks": int(sum(len(value or []) for value in frame["rejections"])),
        "hops_max": None if frame.empty else int(pd.to_numeric(frame["hops"], errors="coerce").fillna(0).max()),
    })

    baseline = pd.read_parquet(V1_RESULTS)
    baseline_headline = baseline["exact_match"].fillna(False).astype(bool) & baseline["refusal_correct"].fillna(False).astype(bool)
    baseline = baseline.assign(_headline=baseline_headline)
    current_by_question = frame.assign(_headline=headline).groupby("qid")["_headline"].sum()
    baseline_by_question = baseline.groupby("qid")["_headline"].sum()
    all_qids = sorted(set(current_by_question.index) | set(baseline_by_question.index))
    summary["per_question"] = {
        qid: {
            "v2_ok": int(current_by_question.get(qid, 0)),
            "v1_ok": int(baseline_by_question.get(qid, 0)),
            "drop": int(baseline_by_question.get(qid, 0) - current_by_question.get(qid, 0)),
        }
        for qid in all_qids
    }
    return rows, summary


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def main(argv=None) -> int:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", dest="eval_set", choices=("v2", "v1-regression"), default="v2")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    if not args.dry_run:
        provenance = freeze_gate(args.eval_set, out_dir=args.out_dir or DEFAULT_OUT)
    else:
        provenance = {
            "git_head": _git("rev-parse", "HEAD"),
            "prereg_commit": FROZEN_COMMIT,
            "questions_sha256": _sha256(QUESTIONS_PATH.read_bytes()),
        }

    if args.trace:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = (
            "vfl-agent-v2-eval" if args.eval_set == "v2" else "vfl-agent-v2-regression"
        )

    if args.dry_run:
        spec = importlib.util.spec_from_file_location(
            "agent_eval_v2_test_fakes", REPO_ROOT / "tests" / "test_agent_eval_v2.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load the offline FakeLLM")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        llm = module.DryRunFakeLLM()
        search_fn = module.dry_search_fn
    else:
        from agent.v2.llm import OpenAISupervisorLLM

        llm = OpenAISupervisorLLM()
        search_fn = None

    temporary = None
    out_dir = args.out_dir
    if out_dir is None:
        if args.dry_run:
            temporary = tempfile.mkdtemp(prefix="vfl-agent-eval-dry-run-")
            out_dir = Path(temporary)
        else:
            out_dir = DEFAULT_OUT
    out_dir = out_dir if out_dir.is_absolute() else REPO_ROOT / out_dir
    eval_run_id = uuid.uuid4().hex
    if args.eval_set == "v2":
        rows, summary = _run_v2(
            llm, repeats=args.repeats, out_dir=out_dir,
            eval_run_id=eval_run_id, trace=args.trace, provenance=provenance,
            search_fn=search_fn,
            memory_base=(out_dir / "runs" / "memory" / "eval_v2") if args.dry_run else None,
        )
        path = _save_rows(rows, out_dir, "eval_v2_turns.parquet")
        summary_path = out_dir / "eval_v2_summary.json"
    else:
        rows, summary = _run_v1_regression(
            llm, repeats=args.repeats, out_dir=out_dir,
            eval_run_id=eval_run_id, trace=args.trace, provenance=provenance,
            search_fn=search_fn,
        )
        path, summary_path = _regression_output_paths(out_dir)
        path = _save_rows(rows, out_dir, path.name)
        # Include v1-on-v2 metrics beside the immutable v1 baseline.
        frame = pd.DataFrame(rows)
        summary.update({
            "n_errors": int(frame["error"].notna().sum()) if len(frame) else 0,
            "tool_accuracy": float(frame["correct_tool"].mean()) if len(frame) else None,
            "exact_accuracy": float(frame["exact_match"].mean()) if len(frame) else None,
            "refusal_accuracy": float(frame["refusal_correct"].mean()) if len(frame) else None,
            "total_cost_usd": float(frame["cost_usd"].dropna().sum()) if len(frame) else None,
        })

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"written: {path}")
    print(f"summary: {summary_path}")
    if args.trace:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
