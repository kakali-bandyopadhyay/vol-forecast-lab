"""SQLite-backed thread checkpointing and long-term preference storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from agent.schema import Answer, Citation
from agent.v2.contract import Evidence, Handoff, Plan

# Checkpoint loading is a trust boundary: msgpack can rebuild arbitrary classes.
# Only the agent's own state types are rebuilt; any other class in a checkpoint
# comes back as raw data. An explicit list is strict regardless of
# LANGGRAPH_STRICT_MSGPACK, so future langgraph upgrades keep thread memory working.
CHECKPOINT_TYPES = (Plan, Handoff, Evidence, Answer, Citation)


def checkpoint_serializer() -> JsonPlusSerializer:
    """Serializer that rebuilds only the agent's state types from checkpoints."""
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


def open_memory(directory: Path | str = Path("runs/memory")) -> tuple[SqliteSaver, SqliteStore, Callable[[], None]]:
    """Open checkpoint and store databases in ``directory`` and return a closer."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint_conn = sqlite3.connect(directory / "checkpoints.sqlite", check_same_thread=False)
    store_conn = sqlite3.connect(
        directory / "store.sqlite", check_same_thread=False, isolation_level=None
    )
    saver = SqliteSaver(checkpoint_conn, serde=checkpoint_serializer())
    store = SqliteStore(store_conn)
    saver.setup()
    store.setup()

    closed = False

    def close() -> None:
        nonlocal closed
        if not closed:
            closed = True
            checkpoint_conn.close()
            store_conn.close()

    return saver, store, close
