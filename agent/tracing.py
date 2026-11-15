"""Load optional LangSmith tracing configuration without initializing clients."""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def get_tracing_config() -> dict[str, Any]:
    """Read LangSmith settings from the repository .env and process environment."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)
    api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
    tracing_value = os.getenv("LANGSMITH_TRACING", "false").strip().lower()
    project = os.getenv("LANGSMITH_PROJECT", "").strip()
    tracing_enabled = bool(api_key) and tracing_value in {"1", "true", "yes", "on"}
    return {"api_key": api_key, "tracing": tracing_enabled, "project": project}
