"""Central SQLite connection policy for Hadron v2.4."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_BUSY_TIMEOUT_MS = 5000


def connect_db(
    path: str | Path,
    *,
    timeout: float = 30.0,
    row_factory: Any | None = None,
) -> sqlite3.Connection:
    """Open a normal Hadron database connection with consistent safety PRAGMAs."""
    target = (
        ":memory:"
        if str(path) == ":memory:"
        else Path(path)
    )
    conn = sqlite3.connect(
        target,
        timeout=float(timeout),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


def initialize_runtime_pragmas(path: str | Path) -> dict[str, str]:
    """Configure durable runtime settings once for a migrated database."""
    conn = connect_db(path)
    try:
        journal = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.commit()
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
        busy = conn.execute("PRAGMA busy_timeout").fetchone()
        sync = conn.execute("PRAGMA synchronous").fetchone()
        return {
            "journal_mode": str(journal[0]) if journal else "unknown",
            "foreign_keys": str(foreign_keys[0]) if foreign_keys else "unknown",
            "busy_timeout_ms": str(busy[0]) if busy else "unknown",
            "synchronous": str(sync[0]) if sync else "unknown",
        }
    finally:
        conn.close()


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "connect_db",
    "initialize_runtime_pragmas",
]
