"""Run the B2 thread/user memory smoke sequence (not a preregistered evaluation)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

from agent.v2.graph import answer_question_v2
from agent.v2.llm import OpenAISupervisorLLM
from agent.v2.persistence import open_memory


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    llm = OpenAISupervisorLLM()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    memory_dir = REPO_ROOT / "runs" / "memory" / "smoke" / run_id
    saver, store, close = open_memory(memory_dir)
    user_id = "smoke"

    def run(thread_id: str, question: str) -> None:
        result = answer_question_v2(
            question,
            llm,
            model_name=llm.model,
            thread_id=thread_id,
            user_id=user_id,
            checkpointer=saver,
            store=store,
            max_cost_usd=0.25,
        )
        tool_args = [
            {"tool": evidence["tool"], "args": evidence["args"]}
            for handoff in result.handoffs
            for evidence in handoff["evidence"]
        ]
        print(
            f"V2_MEMORY_SMOKE thread={thread_id} turn={result.turn} "
            f"resolved_question={result.resolved_question!r} "
            f"agents={result.agents_called!r} tools_args={tool_args!r} "
            f"memory_reads={result.memory_reads!r} memory_writes={result.memory_writes!r} "
            f"cost_usd={result.cost_usd:.6f} stop_reason={result.stop_reason} "
            f"last_rejection={result.last_rejection!r}"
        )

    try:
        run("s1", "Remember M2 as my default model for this thread. What is its 30-session forecast as of 2022-03-01?")
        run("s1", "And for 2022-03-15?")
        run("s1", "What about M3 for that date?")
        run("s2", "Remember squared error as my default metric. Compare models from 2022-01-03 to 2022-03-31.")
        run("s3", "Compare models from 2022-01-03 to 2022-03-31.")
    finally:
        close()


if __name__ == "__main__":
    main()
