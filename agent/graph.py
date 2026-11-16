"""LangGraph orchestration for the Step 11 forecasting agent."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.schema import Answer
from agent.tools import (
    RESULTS_DIR,
    NoDataError,
    OutOfRangeError,
    TOOLS,
    data_range,
)
from agent.verifier import verify

PRICES_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini-2026-03-17": (0.75, 4.50),
    "gpt-5.4-mini": (0.75, 4.50),
}
PRICE_SOURCE_DATE = "2026-11-17"
MAX_DRAFTS = 2

_ADVICE = re.compile(r"\b(buy|sell|should i|trade|trading)\b", re.IGNORECASE)


class CostCapExceeded(RuntimeError):
    """Raised before the next LLM call when the run would exceed its cap."""

    def __init__(self, message: str, *, cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = float(cost_usd)


@dataclass
class AgentResult:
    answer: Answer
    tool: str | None
    attempts: int
    cost_usd: float
    latency_s: float


class _State(TypedDict, total=False):
    question: str
    decision: Any
    output: dict[str, Any] | None
    bounds: tuple[str, str] | None
    answer: Answer | None
    rejection: str | None
    attempts: int
    refusal: bool


def is_advice(question: str) -> bool:
    """Return True for investment-advice wording."""
    return _ADVICE.search(question) is not None


def answer_question(
    question: str,
    llm,
    *,
    model_name: str,
    results_dir: Path | str = RESULTS_DIR,
    search_fn=None,
    max_cost_usd: float = 0.50,
) -> AgentResult:
    """Run the advice guard, then the route → tool → draft → verify graph."""
    started = time.perf_counter()
    if is_advice(question):
        return AgentResult(
            answer=Answer(refused=True, refusal_reason="investment advice is out of scope"),
            tool=None,
            attempts=0,
            cost_usd=0.0,
            latency_s=time.perf_counter() - started,
        )

    cost = 0.0

    def add_cost(usage) -> None:
        nonlocal cost
        price_in, price_out = PRICES_USD_PER_1M.get(model_name, (0.0, 0.0))
        cost += (
            usage.input_tokens * price_in + usage.output_tokens * price_out
        ) / 1_000_000

    def check_cap() -> None:
        if cost > max_cost_usd:
            raise CostCapExceeded(
                f"cost {cost:.4f} > cap {max_cost_usd}", cost_usd=cost
            )

    def route_node(state: _State) -> _State:
        check_cap()
        decision = llm.route(state["question"])
        add_cost(decision.usage)
        return {"decision": decision}

    def tool_node(state: _State) -> _State:
        decision = state["decision"]
        fn = TOOLS.get(decision.tool)
        if fn is None:
            return {
                "answer": Answer(refused=True, refusal_reason="invalid tool arguments"),
                "refusal": True,
            }
        try:
            kwargs = dict(decision.args)
            kwargs["results_dir"] = results_dir
            if decision.tool == "search_fomc" and search_fn is not None:
                kwargs["search_fn"] = search_fn
            output = fn(**kwargs)
            bounds = data_range(
                decision.tool, results_dir, model=decision.args.get("model")
            )
        except OutOfRangeError as exc:
            return {
                "answer": Answer(refused=True, refusal_reason=f"outside data range: {exc}"),
                "refusal": True,
            }
        except NoDataError as exc:
            return {
                "answer": Answer(refused=True, refusal_reason=str(exc)),
                "refusal": True,
            }
        except (ValueError, TypeError):
            return {
                "answer": Answer(refused=True, refusal_reason="invalid tool arguments"),
                "refusal": True,
            }
        return {"output": output, "bounds": bounds, "refusal": False}

    def draft_node(state: _State) -> _State:
        check_cap()
        answer, usage = llm.draft(
            state["question"], state["output"], state.get("rejection")
        )
        add_cost(usage)
        return {
            "answer": answer,
            "attempts": state.get("attempts", 0) + 1,
            "rejection": None,
        }

    def verify_node(state: _State) -> _State:
        decision = state["decision"]
        ok, reason = verify(
            state["answer"],
            state["output"]["rows"],
            state.get("bounds"),
            decision.tool,
        )
        if ok:
            return {
                "answer": state["answer"].model_copy(
                    update={"source_rows": state["output"]["rows"]}
                )
            }
        attempts = state.get("attempts", 0)
        if attempts < MAX_DRAFTS:
            return {"answer": None, "rejection": reason}
        return {
            "answer": Answer(refused=True, refusal_reason="could not verify"),
            "rejection": reason,
        }

    def after_tool(state: _State) -> str:
        return END if state.get("refusal") else "draft"

    def after_verify(state: _State) -> str:
        return END if state.get("answer") is not None else "draft"

    graph = StateGraph(_State)
    graph.add_node("route", route_node)
    graph.add_node("tool", tool_node)
    graph.add_node("draft", draft_node)
    graph.add_node("verify", verify_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "tool")
    graph.add_conditional_edges("tool", after_tool, {"draft": "draft", END: END})
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", after_verify, {"draft": "draft", END: END})
    compiled = graph.compile()
    final = compiled.invoke({"question": question, "attempts": 0})

    decision = final.get("decision")
    tool = None if decision is None else decision.tool
    return AgentResult(
        answer=final["answer"],
        tool=tool,
        attempts=int(final.get("attempts", 0)),
        cost_usd=float(cost),
        latency_s=time.perf_counter() - started,
    )
