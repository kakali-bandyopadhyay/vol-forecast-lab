from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent import tools as source_tools
from agent.v3.tools import build_sdk_tools, execute_tool

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def skip_without_local_data():
    if not (REPO / "results" / "forecasts.parquet").exists() or not (REPO / "results" / "losses.parquet").exists():
        pytest.skip("forecast/loss parquet fixtures are local only (licence, ADR 0003)")


def test_get_forecast_argument_mapping(monkeypatch, tmp_path):
    seen = {}

    def fixture_tool(as_of, model, results_dir=None):
        seen.update(as_of=as_of, model=model, results_dir=results_dir)
        return {"rows": [{"date": as_of, "model": model}], "source": "fixture", "row_keys": ["date", "model"]}

    monkeypatch.setattr(source_tools, "get_forecast", fixture_tool)
    actual = execute_tool("get_forecast", {"as_of": "2020-07-01", "model": "M1"}, results_dir=tmp_path)
    assert seen == {"as_of": "2020-07-01", "model": "M1", "results_dir": tmp_path}
    assert actual["status"] == "ok"
    assert actual["rows"][0]["model"] == "M1"


def test_relative_results_dir_resolves_from_repo_root(monkeypatch, tmp_path):
    seen = {}

    def fixture_tool(as_of, model, results_dir=None):
        seen["results_dir"] = results_dir
        return {"rows": [{"date": as_of}], "source": "fixture", "row_keys": ["date"]}

    monkeypatch.setattr(source_tools, "get_forecast", fixture_tool)
    monkeypatch.chdir(tmp_path)
    execute_tool("get_forecast", {"as_of": "2020-07-01", "model": "M1"}, results_dir=Path("results/test-fixture"))
    assert seen["results_dir"] == (REPO / "results" / "test-fixture").resolve()


@pytest.mark.parametrize(
    ("exception", "status"),
    [(source_tools.OutOfRangeError("outside"), "out_of_range"),
     (source_tools.NoDataError("empty"), "no_data"),
     (ValueError("bad args"), "invalid_args")],
)
def test_domain_errors_are_structured(monkeypatch, exception, status):
    def fixture_tool(**kwargs):
        raise exception

    monkeypatch.setattr(source_tools, "get_forecast", fixture_tool)
    actual = execute_tool("get_forecast", {"as_of": "bad", "model": "M1"})
    assert actual["status"] == status
    assert actual["detail"]
    assert actual["rows"] == []


def test_sdk_handler_returns_structured_error(monkeypatch):
    monkeypatch.setattr(source_tools, "get_forecast", lambda **kwargs: (_ for _ in ()).throw(source_tools.OutOfRangeError("outside")))
    handlers, _server = build_sdk_tools()
    result = asyncio.run(handlers[0].handler({"as_of": "bad", "model": "M1"}))
    assert result["is_error"] is True
    assert result["structuredContent"]["status"] == "out_of_range"
