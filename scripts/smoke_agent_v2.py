"""Run one non-preregistered live smoke question against the v2 supervisor."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

from agent.v2.graph import answer_question_v2
from agent.v2.llm import OpenAISupervisorLLM


QUESTION = "What was the M2 30-session variance forecast for 2021-03-01?"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    llm = OpenAISupervisorLLM()
    result = answer_question_v2(
        QUESTION,
        llm,
        model_name=llm.model,
        max_cost_usd=0.05,
    )
    answer_text = result.answer.text
    if result.answer.forecast:
        answer_text = f"{answer_text} Forecast: {result.answer.forecast}".strip()
    if result.answer.refused:
        answer_text = f"{answer_text} Refusal: {result.answer.refusal_reason}".strip()
    print(
        "V2_SMOKE "
        f"agents={','.join(result.agents_called) or '-'} "
        f"tools={','.join(result.tools_called) or '-'} "
        f"hops={result.hops} cost_usd={result.cost_usd:.6f} "
        f"stop_reason={result.stop_reason or '-'}"
    )
    print(f"answer={answer_text or '(empty)'}")


if __name__ == "__main__":
    main()
