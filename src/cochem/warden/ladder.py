"""Persist Host Warden recovery events using the MC-HW-55 telemetry contract.

The recovery state machine and Hyper-V execution are separate concerns. This
module owns only the SQLite audit sink. ``COCHEM_JOB_DB`` can select the default
database before this module is imported.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any


REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH: Path = Path(os.environ.get("COCHEM_JOB_DB", str(REPO_ROOT / "job_board.db"))).expanduser()


def emit_recovery_event(
    conn: sqlite3.Connection | str | Path | None = None,
    event_data: dict[str, Any] | None = None,
) -> int:
    """Commit one complete event and return its positive, monotonic row ID.

    A sole dictionary argument is the event and uses ``DEFAULT_DB_PATH``.
    Injected connections remain open; internally opened connections always
    close. An injected connection should be dedicated to telemetry because the
    contract explicitly commits its transaction.
    """
    if isinstance(conn, dict) and event_data is None:
        event_data = conn
        conn = None
    if event_data is None:
        event_data = {}
    if not isinstance(event_data, dict):
        raise TypeError("event_data must be a dictionary")
    if conn is not None and not isinstance(conn, (sqlite3.Connection, str, Path)):
        raise TypeError("conn must be a SQLite connection, path, or None")
    if isinstance(conn, str) and not conn:
        raise ValueError("A database path must not be empty")

    # Normalize columns and serialize before opening a write transaction. The
    # details payload retains the caller's original tier/action and metadata.
    raw_tier = event_data.get("tier")
    tier = int(raw_tier) if raw_tier is not None else 1
    raw_action = event_data.get("action")
    action = str(raw_action) if raw_action is not None else "unspecified_recovery"
    details = json.dumps(event_data, ensure_ascii=False, allow_nan=False)

    active_conn: sqlite3.Connection
    if isinstance(conn, sqlite3.Connection):
        active_conn = conn
        owns_connection = False
    else:
        active_conn = sqlite3.connect(str(DEFAULT_DB_PATH if conn is None else conn))
        owns_connection = True
    try:
        active_conn.execute("PRAGMA busy_timeout=5000")
        active_conn.execute("PRAGMA journal_mode=WAL")
        active_conn.execute(
            """CREATE TABLE IF NOT EXISTS recovery_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                tier INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL
            )"""
        )
        cursor = active_conn.execute(
            "INSERT INTO recovery_telemetry (tier, action, details) VALUES (?, ?, ?)",
            (tier, action, details),
        )
        row_id = cursor.lastrowid
        if row_id is None or row_id <= 0:
            raise RuntimeError("Recovery telemetry insert did not produce a positive row ID")
        active_conn.commit()
        return int(row_id)
    except Exception:
        active_conn.rollback()
        raise
    finally:
        if owns_connection:
            active_conn.close()
