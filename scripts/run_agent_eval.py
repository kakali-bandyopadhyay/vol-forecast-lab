"""Run the pre-registered Step 11 agent eval and score it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

import pandas as pd

from agent.graph import CostCapExceeded, PRICE_SOURCE_DATE, PRICES_USD_PER_1M, answer_question
from agent.schema import Answer
from agent.verifier import ISO_DATE, NUMBER_TOKEN

RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "agent" / "eval_questions.csv"
DEFAULT_OUT = RESULTS_DIR / "agent" / "agent_eval.parquet"


def score_exact(answer: Answer, expected_answer: str) -> bool:
    """Compare the answer's numbers to the expected string, ignoring dates."""
    if answer.refused:
        return True
    text = " ".join(
        str(value)
        for value in (
            answer.forecast,
            answer.implied_variance,
            answer.spread,
            answer.text,
            answer.limitations,
        )
        if value
    )
    return str(expected_answer) in NUMBER_TOKEN.findall(ISO_DATE.sub(" ", text))


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _expected_tool(row) -> str | None:
    value = row.get("expected_tool")
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def run_eval(
    questions: pd.DataFrame,
    llm,
    *,
    model_name: str,
    results_dir: Path | str = RESULTS_DIR,
    repeats: int = 3,
    search_fn=None,
    max_cost_usd: float = 0.50,
) -> pd.DataFrame:
    """Run each question `repeats` times and return one row per attempt."""
    if repeats < 1:
        raise ValueError("repeats must be positive")
    rows: list[dict] = []
    for question_row in questions.to_dict(orient="records"):
        qid = str(question_row["qid"])
        question = str(question_row["question"])
        expected_tool = _expected_tool(question_row)
        expect_refusal = _truthy(question_row.get("expect_refusal", False))
        expected_answer = str(question_row.get("expected_answer", ""))

        for repeat in range(repeats):
            base = {
                "qid": qid,
                "repeat": repeat,
                "tool": None,
                "correct_tool": False,
                "exact_match": False,
                "refusal_correct": False,
                "cost_usd": 0.0,
                "latency_s": 0.0,
                "refused": False,
                "refusal_reason": None,
                "attempts": 0,
                "answer_text": "",
                "error": None,
                "cost_cap": False,
            }
            try:
                result = answer_question(
                    question,
                    llm,
                    model_name=model_name,
                    results_dir=results_dir,
                    search_fn=search_fn,
                    max_cost_usd=max_cost_usd,
                )
            except Exception as exc:
                base.update({
                    "cost_usd": float(getattr(exc, "cost_usd", 0.0)),
                    "error": f"{type(exc).__name__}: {exc}",
                    "cost_cap": isinstance(exc, CostCapExceeded),
                })
                rows.append(base)
                continue

            answer = result.answer
            base.update({
                "tool": result.tool,
                "correct_tool": (
                    result.tool is None if expected_tool is None
                    else str(result.tool) == expected_tool
                ),
                "exact_match": score_exact(answer, expected_answer),
                "refusal_correct": bool(answer.refused) == expect_refusal,
                "cost_usd": float(result.cost_usd),
                "latency_s": float(result.latency_s),
                "refused": bool(answer.refused),
                "refusal_reason": answer.refusal_reason,
                "attempts": int(result.attempts),
                "answer_text": answer.text,
            })
            rows.append(base)
    return pd.DataFrame(rows)


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", ""))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-cost-usd", type=float, default=0.50)
    args = parser.parse_args()
    if not args.model:
        raise SystemExit("--model or AGENT_MODEL is required")
    if args.model not in PRICES_USD_PER_1M:
        raise SystemExit(f"no price for model {args.model!r}; add it to PRICES_USD_PER_1M")

    questions = pd.read_csv(args.questions, dtype={"expected_answer": str})
    from agent.llm import OpenAIAgentLLM

    result = run_eval(
        questions,
        OpenAIAgentLLM(model=args.model),
        model_name=args.model,
        repeats=args.repeats,
        max_cost_usd=args.max_cost_usd,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(args.out, index=False)

    headline = (result["exact_match"] & result["refusal_correct"]).mean()
    errors = result["error"].fillna("").astype(str)
    summary = {
        "n_questions": int(result["qid"].nunique()),
        "repeats": int(args.repeats),
        "n_errors": int((errors != "").sum()),
        "n_cost_cap": int(result["cost_cap"].sum()),
        "tool_accuracy": float(result["correct_tool"].mean()),
        "exact_accuracy": float(result["exact_match"].mean()),
        "refusal_accuracy": float(result["refusal_correct"].mean()),
        "headline_accuracy": float(headline),
        "total_cost_usd": float(result["cost_usd"].sum()),
        "mean_cost_usd": float(result["cost_usd"].mean()),
        "latency_p50_s": float(result["latency_s"].quantile(0.50)),
        "latency_p95_s": float(result["latency_s"].quantile(0.95)),
        "model": args.model,
        "price_source_date": PRICE_SOURCE_DATE,
        "git_head": _git_head(),
    }
    summary_path = args.out.parent / "agent_eval_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
