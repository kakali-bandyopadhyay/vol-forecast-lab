"""Build a compact human review pack or the final v2 evaluation summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

import pandas as pd

DEFAULT_OUT = REPO_ROOT / "results" / "agent_v2"
MANUAL_FIELDS = {"narrative_ok", "winner_ok", "refusal_ok", "block_class"}
BLOCK_CLASSES = {"appropriate", "inappropriate", "miss"}
V1_QUESTIONS = REPO_ROOT / "data" / "agent" / "eval_questions.csv"


def _list(value):
    if value is None:
        return []
    if hasattr(value, "tolist") and not isinstance(value, list):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return list(value) if isinstance(value, tuple) else value


def _records(value):
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
    if isinstance(value, dict):
        return [value]
    return list(value) if isinstance(value, (list, tuple)) else []


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _load_inputs(out_dir: Path):
    path = out_dir / "eval_v2_turns.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_parquet(path)
    frozen = json.loads((REPO_ROOT / "prereg" / "questions_v2.json").read_text(encoding="utf-8"))
    turns = {
        (question["id"], int(turn["turn"])): turn
        for question in frozen["questions"] for turn in question["turns"]
    }
    return frame, turns, frozen


def make_review_pack(out_dir: Path = DEFAULT_OUT) -> Path:
    frame, turns, _ = _load_inputs(out_dir)
    selected = frame[frame["needs_review"].fillna(False)]
    lines = ["# Build B v2 manual review pack", "", f"Turns requiring review: {len(selected)}", ""]
    for _, row in selected.iterrows():
        key = (str(row.qid), int(row.turn))
        frozen_turn = turns.get(key, {})
        lines.extend([
            f"## {row.qid} · repetition {row.rep} · turn {row.turn}",
            "",
            f"- Prompt: {row.prompt}",
            f"- Resolved question: {row.resolved_question}",
            f"- Final answer: {row.answer_text}",
            f"- Citations: {json.dumps(_list(row.citations), ensure_ascii=False)}",
            f"- Frozen rubric points: {json.dumps(frozen_turn.get('required_points', frozen_turn.get('correct_if', frozen_turn.get('expected_outcome', ''))), ensure_ascii=False)}",
            f"- Auto scores: {json.dumps({name: row.get(name) for name in ('routing_ok','tool_args_ok','numeric_ok','sign_ok','winner_ok','n_ok','citation_ok','memory_args_ok','refusal_ok')}, ensure_ascii=False, default=str)}",
            f"- Needs review: {row.review_reason}",
        ])
        for rejection in _list(row.rejections):
            lines.append(f"- Verifier rejection (attempt {rejection.get('attempt')}): {rejection.get('reason')}")
            lines.append(f"  - Rejected draft: {rejection.get('draft_text')}")
        lines.extend(["", "Suggested manual fields: " + ", ".join(_list(row.review_fields)), ""])
    target = out_dir / "review_pack.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _latest_regression_path(out_dir: Path) -> Path:
    base = out_dir / "regression_v1_on_v2.parquet"
    numbered = []
    for path in out_dir.glob("regression_v1_on_v2_run*.parquet"):
        try:
            index = int(path.stem.removeprefix("regression_v1_on_v2_run"))
        except ValueError:
            continue
        numbered.append((index, path))
    if numbered:
        return max(numbered, key=lambda item: item[0])[1]
    if base.exists():
        return base
    raise FileNotFoundError(base)


def _evidence_row(row) -> tuple[str, dict]:
    evidence = _records(row.get("evidence"))
    attempts = _records(row.get("tool_attempts"))
    preferred = [item.get("tool") for item in attempts if isinstance(item, dict)]
    preferred.extend(item.get("tool") for item in evidence if isinstance(item, dict))
    selected = next(
        (item for tool in preferred for item in evidence if isinstance(item, dict) and item.get("tool") == tool),
        evidence[0] if evidence else {},
    )
    if not selected:
        return "unknown", {"detail": "no evidence row recorded"}
    tool = str(selected.get("tool", "unknown"))
    rows = _records(selected.get("rows"))
    if not rows:
        return tool, {"detail": "no evidence rows recorded"}
    values = rows[0]
    if tool == "event_effect":
        allowed = {"beta", "se", "t", "p", "n"}
        values = {key: value for key, value in values.items() if key in allowed}
    return tool, values


def make_regression_pack(out_dir: Path = DEFAULT_OUT) -> Path:
    """Write concise diagnostics for failed or verifier-blocked regression rows."""
    frame = pd.read_parquet(_latest_regression_path(out_dir))
    expected = pd.read_csv(V1_QUESTIONS, dtype=str).set_index("qid")["expected_answer"].to_dict()
    lines = ["# v1 Regression on v2 diagnostics", ""]
    selected_count = 0
    for _, row in frame.iterrows():
        rejections = _records(row.get("rejections"))
        ok = all(_bool(row.get(field)) for field in ("correct_tool", "exact_match", "refusal_correct"))
        if ok and not rejections:
            continue
        selected_count += 1
        answer_fields = row.get("answer_fields")
        if isinstance(answer_fields, str):
            try:
                answer_fields = json.loads(answer_fields)
            except json.JSONDecodeError:
                answer_fields = {}
        answer_text = row.get("answer_text")
        if not answer_text and isinstance(answer_fields, dict):
            answer_text = answer_fields.get("text", "")
        tool, evidence_row = _evidence_row(row)
        lines.extend([
            f"## {row.get('qid')} · repetition {row.get('rep')}",
            "",
            f"- Prompt: {row.get('prompt', '')}",
            f"- Expected answer: {expected.get(str(row.get('qid')), '')}",
            f"- Final answer: {answer_text or ''}",
        ])
        for rejection in rejections:
            lines.append(f"- Verifier rejection (attempt {rejection.get('attempt')}): {rejection.get('reason', '')}")
            lines.append(f"  - Rejected draft: {rejection.get('draft_text', '')}")
        lines.extend([
            f"- Evidence row ({tool}): {json.dumps(evidence_row, ensure_ascii=False, default=str)}",
            "",
        ])
    if selected_count == 0:
        lines.append("No failed or verifier-blocked regression rows.")
    target = out_dir / "regression_pack.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _manual_index(path: Path, frame: pd.DataFrame) -> dict[tuple[str, int, int, str], str]:
    if not path.exists():
        raise FileNotFoundError(path)
    manual = pd.read_csv(path, dtype=str).fillna("")
    required = {"qid", "rep", "turn", "field", "value", "reviewer", "reason"}
    missing = required - set(manual.columns)
    if missing:
        raise ValueError(f"manual score file missing columns: {sorted(missing)}")
    indexed = {}
    row_lookup = {
        (str(row.qid), int(row.rep), int(row.turn)): row
        for _, row in frame.iterrows()
    }
    for _, score in manual.iterrows():
        key = (str(score.qid), int(score.rep), int(score.turn))
        field = str(score.field).strip()
        if field not in MANUAL_FIELDS:
            raise ValueError(f"manual field {field!r} may not override an automatic result")
        row = row_lookup.get(key)
        if row is None:
            raise ValueError(f"manual score references unknown turn {key}")
        allowed = _list(row.review_fields)
        if field not in allowed:
            raise ValueError(f"{field} is not marked needs_review for {key}; automatic results cannot be overridden")
        value = str(score.value).strip()
        if field == "block_class":
            if value not in BLOCK_CLASSES:
                raise ValueError(f"block_class must be one of {sorted(BLOCK_CLASSES)}")
        elif value.lower() not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError(f"{field} must be a boolean")
        index_key = (*key, field)
        if index_key in indexed:
            raise ValueError(f"duplicate manual score for {index_key}")
        indexed[index_key] = value
    return indexed


def _turn_correct(row, manual: dict) -> bool:
    required_manual = [field for field in _list(row.review_fields) if field != "block_class"]
    if any(field not in manual for field in required_manual):
        return False
    fields = ["routing_ok", "tool_args_ok", "numeric_ok", "sign_ok", "n_ok", "citation_ok"]
    for field in ("winner_ok", "memory_args_ok", "refusal_ok"):
        value = row.get(field)
        if field in manual or (value is not None and not pd.isna(value)):
            fields.append(field)
    if "narrative_ok" in manual or row.qid in {"Q04", "Q06", "Q07"}:
        fields.append("narrative_ok")
    for field in fields:
        value = manual.get(field)
        if value is not None:
            passed = _bool(value)
        else:
            raw = row.get(field)
            passed = _bool(raw) if raw is not None and not pd.isna(raw) else False
        if not passed:
            return False
    return True


def _pctl(values, q):
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return None if clean.empty else float(clean.quantile(q))


def make_final_summary(out_dir: Path = DEFAULT_OUT) -> Path:
    frame, _, _ = _load_inputs(out_dir)
    run_meta = json.loads((out_dir / "eval_v2_summary.json").read_text(encoding="utf-8"))
    manual_path = out_dir / "manual_scores.csv"
    manual = _manual_index(manual_path, frame)
    frame = frame.copy()
    frame["manual_correct"] = False
    frame["turn_correct"] = False
    manual_by_turn = {}
    for (qid, rep, turn, field), value in manual.items():
        manual_by_turn.setdefault((qid, rep, turn), {})[field] = value
    for idx, row in frame.iterrows():
        merged = manual_by_turn.get((str(row.qid), int(row.rep), int(row.turn)), {})
        frame.at[idx, "turn_correct"] = _turn_correct(row, merged)
        frame.at[idx, "manual_correct"] = bool(merged)

    question_runs = frame.groupby(["qid", "rep"], dropna=False)["turn_correct"].all()
    routing_n = int(frame["routing_ok"].map(_bool).sum())
    args_n = int(frame["tool_args_ok"].map(_bool).sum())
    followups = frame[frame.apply(lambda r: r.get("memory_args_ok") is not None and not pd.isna(r.get("memory_args_ok")), axis=1)]
    refusal_rows = frame[frame["qid"].isin(["Q11", "Q12"])]
    block_counts = {name: 0 for name in ("appropriate", "inappropriate", "miss", "unclassified")}
    for _, row in frame.iterrows():
        rejections = _list(row.rejections)
        if not rejections:
            continue
        cls = manual_by_turn.get((str(row.qid), int(row.rep), int(row.turn)), {}).get("block_class")
        if cls in block_counts:
            block_counts[cls] += len(rejections)
        else:
            block_counts["unclassified"] += len(rejections)

    costs = pd.to_numeric(frame["cost_usd"], errors="coerce")
    latency = pd.to_numeric(frame["latency_s"], errors="coerce")
    per_question_cost = frame.assign(_cost=costs).groupby(["qid", "rep"])["_cost"].sum(min_count=1)
    hops = pd.to_numeric(frame["hops"], errors="coerce").fillna(0).astype(int)
    summary = {
        "headline_correct_question_runs": int(question_runs.sum()),
        "headline_denominator": 36,
        "per_turn_correct": int(frame["turn_correct"].sum()),
        "per_turn_denominator": 51,
        "routing_correct": routing_n,
        "routing_denominator": 51,
        "tool_args_correct": args_n,
        "tool_args_denominator": 51,
        "memory_correct_followups": int(followups["memory_args_ok"].map(_bool).sum()),
        "memory_denominator": 15,
        "refusals_correct": int(refusal_rows.apply(lambda r: _bool(manual_by_turn.get((str(r.qid), int(r.rep), int(r.turn)), {}).get("refusal_ok", False)), axis=1).sum()),
        "refusals_denominator": 6,
        "verifier_blocks": int(sum(block_counts.values())),
        "verifier_blocks_appropriate": block_counts["appropriate"],
        "verifier_blocks_inappropriate": block_counts["inappropriate"],
        "verifier_blocks_unclassified": block_counts["unclassified"],
        "verifier_misses": block_counts["miss"],
        "cost_per_turn_usd": {"mean": None if costs.dropna().empty else float(costs.mean()), "median": None if costs.dropna().empty else float(costs.median()), "total": None if costs.dropna().empty else float(costs.sum()), "n": int(costs.notna().sum())},
        "cost_per_question_run_usd": {"mean": None if per_question_cost.dropna().empty else float(per_question_cost.mean()), "median": None if per_question_cost.dropna().empty else float(per_question_cost.median()), "total": None if per_question_cost.dropna().empty else float(per_question_cost.sum()), "n": int(per_question_cost.notna().sum())},
        "latency_s": {"p50": _pctl(latency, 0.50), "p95": _pctl(latency, 0.95), "n": int(latency.notna().sum())},
        "hops_distribution": {str(value): int(count) for value, count in hops.value_counts().sort_index().items()},
        "hops_max": None if hops.empty else int(hops.max()),
        "hops_violations_over_3": int((hops > 3).sum()),
        "budget_compliance": bool(
            not run_meta.get("incomplete", False)
            and not frame["error"].fillna("").str.contains("Budget exhausted|CostCapExceeded", case=False).any()
            and per_question_cost.dropna().le(0.25 + 1e-9).all()
            and (costs.dropna().sum() <= 5.0 + 1e-9)
        ),
        "incomplete": bool(run_meta.get("incomplete", False) or frame["error"].fillna("").str.contains("incomplete|CostCapExceeded", case=False).any()),
        "model": run_meta.get("model"),
        "price_source_date": run_meta.get("price_source_date"),
        "git_head": run_meta.get("git_head"),
        "prereg_commit": run_meta.get("prereg_commit"),
        "questions_sha256": run_meta.get("questions_sha256"),
        "eval_run_id": run_meta.get("eval_run_id"),
        "repeats": run_meta.get("repeats"),
        "n_rows": int(len(frame)),
        "per_question": {
            str(qid): {
                "correct_question_runs": int(values.sum()),
                "question_run_denominator": int(len(values)),
                "correct_turns": int(frame.loc[frame.qid == qid, "turn_correct"].sum()),
                "turn_denominator": int((frame.qid == qid).sum()),
            }
            for qid, values in question_runs.groupby(level=0)
        },
    }
    target = out_dir / "eval_v2_summary.json"
    target.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pack", action="store_true")
    mode.add_argument("--regression-pack", action="store_true")
    mode.add_argument("--final", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out_dir = args.out_dir if args.out_dir.is_absolute() else REPO_ROOT / args.out_dir
    if args.pack:
        target = make_review_pack(out_dir)
    elif args.regression_pack:
        target = make_regression_pack(out_dir)
    else:
        target = make_final_summary(out_dir)
    print(f"written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
