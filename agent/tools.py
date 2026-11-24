"""Read-only tools exposed to the Step 11 forecasting agent."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
FORECASTS = "forecasts.parquet"
MLP_FORECASTS = "mlp_forecasts.parquet"
LOSSES = "losses.parquet"
EVENTS = "q3_events.parquet"


class OutOfRangeError(ValueError):
    """A requested date falls outside the source table's coverage."""


class NoDataError(ValueError):
    """A valid request has no matching rows."""


def _path(results_dir: Path | str, name: str) -> Path:
    return Path(results_dir) / name


def _iso(value) -> str:
    return pd.Timestamp(value).date().isoformat()


def _range(frame: pd.DataFrame) -> tuple[str, str]:
    dates = pd.to_datetime(frame["date"])
    return _iso(dates.min()), _iso(dates.max())


def _check_range(value: str, bounds: tuple[str, str]) -> str:
    normalized = _iso(value)
    first, last = bounds
    if not (first <= normalized <= last):
        raise OutOfRangeError(f"{normalized} is outside data range {first}..{last}")
    return normalized


def _clean(value):
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).date().isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            return value
    return value


def _rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: _clean(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def data_range(tool_name: str, results_dir: Path | str = RESULTS_DIR, model: str | None = None):
    """Return the inclusive ISO date range for a tool's source table.

    For get_forecast, pass model="M4" to use the MLP table's range; the graph
    must pass the router-selected model so the verifier checks the right table.
    """
    if tool_name == "get_forecast":
        source = MLP_FORECASTS if model == "M4" else FORECASTS
        return _range(pd.read_parquet(_path(results_dir, source)))
    if tool_name == "compare_models":
        return _range(pd.read_parquet(_path(results_dir, LOSSES)))
    if tool_name in {"event_effect", "search_fomc"}:
        return None
    raise ValueError(f"unknown tool {tool_name!r}")


def get_forecast(as_of: str, model: str, results_dir: Path | str = RESULTS_DIR) -> dict[str, Any]:
    """Return one forecast row; M4 uses the MLP table's own date range."""
    if model not in {"M0", "M1", "M2", "M3", "M4"}:
        raise ValueError(f"unknown model {model!r}")
    source = MLP_FORECASTS if model == "M4" else FORECASTS
    frame = pd.read_parquet(_path(results_dir, source))
    first, last = data_range("get_forecast", results_dir, model)
    normalized = _check_range(as_of, (first, last))
    frame = frame[pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d") == normalized]
    if model != "M4":
        frame = frame[frame["model"] == model]
    result = _rows(frame)
    if not result:
        raise NoDataError(f"no data for {normalized}/{model}")
    if model == "M4":
        for row in result:
            row["model"] = "M4"
    return {"rows": result, "source": source, "row_keys": ["date", "model"]}


def compare_models(
    start: str,
    end: str,
    metric: str,
    results_dir: Path | str = RESULTS_DIR,
) -> dict[str, Any]:
    """Return mean loss per model over an inclusive date window."""
    if metric not in {"qlike", "squared_error"}:
        raise ValueError(f"unknown metric {metric!r}")
    frame = pd.read_parquet(_path(results_dir, LOSSES))
    first, last = _range(frame)
    start_iso = _check_range(start, (first, last))
    end_iso = _check_range(end, (first, last))
    if start_iso > end_iso:
        raise NoDataError(f"start {start_iso} is after end {end_iso}")
    dates = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    selected = frame[(dates >= start_iso) & (dates <= end_iso)]
    if selected.empty:
        raise NoDataError(f"no data for {start_iso}..{end_iso}")
    grouped = selected.groupby("model")[metric].agg(["mean", "count"]).sort_index()
    rows = [
        {"model": str(model), "mean": float(values["mean"]), "n": int(values["count"])}
        for model, values in grouped.iterrows()
    ]
    return {"rows": rows, "source": LOSSES, "row_keys": ["model"]}


def event_effect(event: str, component: str, results_dir: Path | str = RESULTS_DIR) -> dict[str, Any]:
    """Return primary-run event-effect rows for one event/component pair."""
    if event not in {"cpi", "fomc"}:
        raise ValueError(f"unknown event {event!r}")
    if component not in {"on", "oc", "gk"}:
        raise ValueError(f"unknown component {component!r}")
    frame = pd.read_parquet(_path(results_dir, EVENTS))
    selected = frame[
        (frame["run"] == "primary")
        & (frame["term"] == event)
        & (frame["component"] == component)
    ]
    if selected.empty:
        raise NoDataError(f"no data for {event}/{component}")
    return {
        "rows": _rows(selected),
        "source": EVENTS,
        "row_keys": ["run", "term", "component"],
    }


def search_fomc(
    query: str,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    doc_type: str | None = None,
    search_fn: Callable | None = None,
    results_dir: Path | str = RESULTS_DIR,
) -> dict[str, Any]:
    """Search the 10b hybrid index; the search function is imported only when used."""
    if search_fn is None:
        from volab.text.index import COLLECTION, search

        search_fn = search
        source = COLLECTION
    else:
        source = "fomc_chunks"
    kwargs: dict[str, Any] = {"mode": "hybrid", "k": 5}
    if date_from is not None:
        kwargs["date_from"] = date_from
    if date_to is not None:
        kwargs["date_to"] = date_to
    if doc_type is not None:
        if doc_type not in {"statement", "minutes"}:
            raise ValueError("doc_type must be statement or minutes")
        kwargs["doc_type"] = doc_type
    results = search_fn(query, **kwargs)
    rows = [dict(result) for result in results]
    if not rows:
        raise NoDataError(f"no data for query {query!r}")
    return {"rows": rows, "source": source, "row_keys": ["chunk_id"]}


TOOLS = {
    "get_forecast": get_forecast,
    "compare_models": compare_models,
    "event_effect": event_effect,
    "search_fomc": search_fomc,
}
