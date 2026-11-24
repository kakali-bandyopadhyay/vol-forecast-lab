"""Supervisor routing policy. Kakali hand-codes these function bodies."""

from __future__ import annotations

SUBAGENT_TOOLS: dict[str, tuple[str, ...]] = {
    "forecast": ("get_forecast", "compare_models"),
    "event-effect": ("event_effect",),
    "FOMC retrieval": ("search_fomc",),
}
MAX_HOPS = 3
MAX_REPLANS = 1
MAX_DRAFTS = 2


_HARD_FAILURES = ("out_of_range", "no_data", "invalid_args")


def _field(obj, name, default=None):
    """Read a handoff/plan field whether it is a model or a plain mapping."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _last_handoff(state):
    handoffs = state.get("handoffs") or ()
    return handoffs[-1] if handoffs else None


def validate_plan(plan) -> str | None:
    """Return None when a plan has 1–3 unique agents and non-empty subtasks."""
    agents = list(_field(plan, "agents", ()) or ())
    if not 1 <= len(agents) <= MAX_HOPS:
        return f"plan must name 1 to {MAX_HOPS} agents"
    if len(set(agents)) != len(agents):
        return "plan lists an agent more than once"
    subtasks = _field(plan, "subtasks", {}) or {}
    for agent in agents:
        subtask = subtasks.get(agent, "")
        if not isinstance(subtask, str) or not subtask.strip():
            return f"agent {agent!r} has a blank subtask"
    unknown = set(subtasks) - set(agents)
    if unknown:
        return f"subtask for agent not in plan: {sorted(unknown)[0]!r}"
    return None


def validate_replan(plan, called) -> str | None:
    """Return None when a replan adds exactly one agent that has not been called."""
    agents = list(_field(plan, "agents", ()) or ())
    if len(agents) != 1:
        return f"replan must name exactly one agent, got {len(agents)}"
    if agents[0] in (called or ()):
        return f"agent {agents[0]!r} has already been called"
    return None


def tool_allowed(agent: str, tool: str) -> bool:
    """Return whether a tool belongs to the named sub-agent's tool scope."""
    return tool in SUBAGENT_TOOLS.get(agent, ())


def next_step(state) -> str:
    """Choose dispatch, replan, draft, or refuse using the §4 behavior table."""
    status = _field(_last_handoff(state), "status")
    hops = state.get("hops", 0) or 0
    pending = state.get("pending") or ()

    if status in _HARD_FAILURES:
        return "refuse"
    if status == "out_of_scope":
        if (state.get("replans", 0) or 0) < MAX_REPLANS and hops < MAX_HOPS:
            return "replan"
        return "refuse"
    if pending:
        return "dispatch" if hops < MAX_HOPS else "refuse"
    return "draft"


def refusal_reason(state) -> str:
    """Map the latest stop condition to the v1-compatible refusal wording."""
    handoff = _last_handoff(state)
    status = _field(handoff, "status")
    detail = _field(handoff, "detail")

    if status == "out_of_range":
        return "outside data range: " + (detail or "")
    if status == "no_data":
        return detail or "no data"
    if status == "invalid_args":
        return "invalid tool arguments"
    if status == "out_of_scope":
        return "no sub-agent can answer this"
    if state.get("stop_reason") == "max_hops" or state.get("pending"):
        return "max hops reached"
    return "could not complete the request"
