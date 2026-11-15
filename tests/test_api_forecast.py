"""Offline coverage for the read-only forecast API routes."""

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from api import app as api_module


def test_forecast_hit_reads_selected_source(tmp_path: Path, monkeypatch) -> None:
    """Forecast response identifies the row's source and date."""
    forecasts = tmp_path / "forecasts.parquet"
    mlp = tmp_path / "mlp_forecasts.parquet"
    pd.DataFrame(
        [{"date": "2020-01-02", "model": "M1", "floored_30d": 0.2}]
    ).to_parquet(forecasts)
    pd.DataFrame(
        [{"date": "2020-01-02", "floored_30d": 0.3, "seed_0": 7}]
    ).to_parquet(mlp)
    monkeypatch.setattr(api_module, "FORECASTS_PATH", forecasts)
    monkeypatch.setattr(api_module, "MLP_FORECASTS_PATH", mlp)

    response = TestClient(api_module.app).get("/forecast", params={"as_of": "2020-01-02", "model": "M4"})

    assert response.status_code == 200
    assert response.json() == {
        "source_file": "mlp_forecasts.parquet",
        "date": "2020-01-02",
        "model": "M4",
        "rows": [{"date": "2020-01-02", "floored_30d": 0.3, "seed_0": 7}],
    }


def test_forecast_missing_row_returns_404(tmp_path: Path, monkeypatch) -> None:
    """Unknown forecast date maps to 404."""
    forecasts = tmp_path / "forecasts.parquet"
    pd.DataFrame([{"date": "2020-01-02", "model": "M0", "floored_30d": 0.2}]).to_parquet(forecasts)
    monkeypatch.setattr(api_module, "FORECASTS_PATH", forecasts)

    response = TestClient(api_module.app).get("/forecast", params={"as_of": "2020-01-03", "model": "M0"})

    assert response.status_code == 404


def test_forecast_bad_date_or_model_returns_422() -> None:
    """Malformed dates and unsupported models are rejected by request validation."""
    client = TestClient(api_module.app)
    assert client.get("/forecast", params={"as_of": "2020-13-01", "model": "M0"}).status_code == 422
    assert client.get("/forecast", params={"as_of": "2020-01-01", "model": "M5"}).status_code == 422


def test_compare_returns_hand_computed_means(tmp_path: Path, monkeypatch) -> None:
    """Comparison returns per-model means and row counts for the inclusive window."""
    losses = tmp_path / "losses.parquet"
    pd.DataFrame(
        [
            {"model": "M0", "date": "2020-01-01", "qlike": 2.0, "squared_error": 4.0},
            {"model": "M0", "date": "2020-01-02", "qlike": 4.0, "squared_error": 8.0},
            {"model": "M1", "date": "2020-01-02", "qlike": 3.0, "squared_error": 9.0},
            {"model": "M1", "date": "2020-01-03", "qlike": 99.0, "squared_error": 99.0},
        ]
    ).to_parquet(losses)
    monkeypatch.setattr(api_module, "LOSSES_PATH", losses)

    response = TestClient(api_module.app).get(
        "/compare", params={"start": "2020-01-01", "end": "2020-01-02", "metric": "qlike"}
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {"model": "M0", "mean": 3.0, "n": 2},
        {"model": "M1", "mean": 3.0, "n": 1},
    ]


def test_compare_rejects_reversed_window() -> None:
    """A start date after the end date returns 422 before reading data."""
    response = TestClient(api_module.app).get(
        "/compare", params={"start": "2020-01-02", "end": "2020-01-01", "metric": "qlike"}
    )
    assert response.status_code == 422
