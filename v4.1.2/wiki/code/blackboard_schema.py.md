# src/cochem/blackboard/schema.py

`python
"""Universal Blackboard & Task Matrix Queue Schema (MC-BB-06..11)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# ==============================================================================
# [MC-BB-06] DATA_MODEL:jobs (SRS-412-04 §7)
# ==============================================================================

JOBS_TABLE_DDL: str = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    job_type TEXT NOT NULL,
    priority INTEGER DEFAULT 100,
    status TEXT CHECK(status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'BLOCKED')) DEFAULT 'PENDING',
    payload_json TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at INTEGER,
    attempts INTEGER DEFAULT 0,
    error_log TEXT,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER DEFAULT (strftime('%s', 'now'))
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Creates the jobs table verbatim from Chapter 4 Section 7 if not present."""
    conn.execute(JOBS_TABLE_DDL)
    conn.commit()


# ------------------------------------------------------------------------------
# End of MC-BB-06 chunk (lines 1-40)
# ------------------------------------------------------------------------------


# [MC-BB-07] SRS-412-04-FR-001
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-04-FR-001: The
# SPEC: task blackboard shall operate over SQLite in Write-Ahead Logging (WAL) mode with PRAGMA
# SPEC: synchronous = NORMAL and PRAGMA busy_timeout = 30000. Traceability test:
# SPEC: test_scenario_1_full_pipeline_bootstrap_lifecycle.
BUSY_TIMEOUT_MS: int = 30000
SYNCHRONOUS_MODE: str = "NORMAL"
JOURNAL_MODE_WAL: str = "WAL"


def apply_wal_pragmas(conn: sqlite3.Connection) -> None:
    """Configures Write-Ahead Logging (WAL), NORMAL sync, and 30s busy timeout."""
    conn.execute(f"PRAGMA journal_mode = {JOURNAL_MODE_WAL}")
    conn.execute(f"PRAGMA synchronous = {SYNCHRONOUS_MODE}")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")


def create_connection(db_path: str | Path, timeout_sec: float = 30.0) -> sqlite3.Connection:
    """SQLite WAL Connection Factory with Pragmas satisfying SRS-412-04-FR-001.

    Operates over SQLite in WAL mode with PRAGMA synchronous = NORMAL and
    PRAGMA busy_timeout = 30000. Row factory defaults to sqlite3.Row.
    """
    path_str = str(db_path)
    conn = sqlite3.connect(path_str, timeout=timeout_sec)
    conn.row_factory = sqlite3.Row
    apply_wal_pragmas(conn)
    return conn


def get_connection(db_path: str | Path, timeout_sec: float = 30.0) -> sqlite3.Connection:
    """Alias for create_connection factory."""
    return create_connection(db_path, timeout_sec=timeout_sec)


def connect(db_path: str | Path, timeout_sec: float = 30.0) -> sqlite3.Connection:
    """Alias for create_connection factory."""
    return create_connection(db_path, timeout_sec=timeout_sec)


# [MC-BB-08] FAILURE:Database Lock Contention
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'Database
# SPEC: Lock Contention': `PRAGMA busy_timeout = 30000` retries automatically for up to 30 seconds
# SPEC: before failing cleanly.
import time

LOCK_BUSY_TIMEOUT_MS: int = 30000
MAX_LOCK_RETRIES: int = 5
LOCK_RETRY_BASE_SEC: float = 0.05


def configure_busy_timeout(conn: sqlite3.Connection, timeout_ms: int = LOCK_BUSY_TIMEOUT_MS) -> None:
    """Sets PRAGMA busy_timeout: SQLite retries a locked write for up to timeout_ms before failing."""
    conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)}")


def execute_with_busy_retry(
    conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] | list[Any] = (),
    max_retries: int = MAX_LOCK_RETRIES, backoff_base_sec: float = LOCK_RETRY_BASE_SEC,
) -> sqlite3.Cursor:
    """Runs sql, retrying lock errors inside ONE busy_timeout budget, then re-raises the real error."""
    budget_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    deadline = time.monotonic() + budget_ms / 1000.0
    attempt = 0
    try:
        while True:
            try:
                return conn.execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                attempt += 1
                pause = backoff_base_sec * (2 ** (attempt - 1))
                left_ms = int((deadline - time.monotonic() - pause) * 1000)
                if ("locked" not in msg and "busy" not in msg) or attempt >= max_retries or left_ms <= 0:
                    raise
                time.sleep(pause)
                configure_busy_timeout(conn, left_ms)
    finally:
        configure_busy_timeout(conn, budget_ms)

# [MC-BB-09] SRS-412-04-FR-007
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-04-FR-007:
# SPEC: Observers, telemetry monitors, and SRE watchdogs shall connect using read-only database URIs
# SPEC: (`file:job_board.db?mode=ro`).
import urllib.parse

READONLY_URI_SUFFIX: str = "?mode=ro"


def make_readonly_uri(db_path: str | Path) -> str:
    """Constructs a standard file:...?mode=ro URI for read-only database access."""
    resolved = Path(db_path).resolve()
    return f"file:{urllib.parse.quote(resolved.as_posix(), safe='/: ')}?mode=ro"


def create_readonly_connection(db_path: str | Path, timeout_sec: float = 5.0) -> sqlite3.Connection:
    """Connects to SQLite database using read-only URI mode (?mode=ro) for observers and watchdogs."""
    uri = make_readonly_uri(db_path)
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def open_readonly(db_path: str | Path, timeout_sec: float = 5.0) -> sqlite3.Connection:
    """Alias for create_readonly_connection satisfying SRS-412-04-FR-007."""
    return create_readonly_connection(db_path, timeout_sec=timeout_sec)


def get_readonly_connection(db_path: str | Path, timeout_sec: float = 5.0) -> sqlite3.Connection:
    """Alias for create_readonly_connection factory."""
    return create_readonly_connection(db_path, timeout_sec=timeout_sec)


def is_readonly_connection(conn: sqlite3.Connection) -> bool:
    """Verifies that the given SQLite connection is in query_only read-only mode."""
    cur = conn.execute("PRAGMA query_only")
    row = cur.fetchone()
    return bool(row and row[0])

# [MC-BB-10] NFR-TM-02
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-TM-02: The SQLite
# SPEC: database WAL checkpointing shall not block read queries.
CHECKPOINT_PASSIVE: str = "PASSIVE"
CHECKPOINT_FULL: str = "FULL"
CHECKPOINT_RESTART: str = "RESTART"
CHECKPOINT_TRUNCATE: str = "TRUNCATE"


def checkpoint_wal_passive(conn: sqlite3.Connection, db_name: str = "main") -> tuple[int, int, int]:
    """Executes non-blocking PASSIVE WAL checkpoint without blocking concurrent read queries."""
    cur = conn.execute(f"PRAGMA {db_name}.wal_checkpoint(PASSIVE)")
    row = cur.fetchone()
    if row is not None:
        return (int(row[0]), int(row[1]), int(row[2]))
    return (0, 0, 0)


def wal_checkpoint(conn: sqlite3.Connection, mode: str = CHECKPOINT_PASSIVE, db_name: str = "main") -> tuple[int, int, int]:
    """Non-blocking WAL checkpoint helper satisfying NFR-TM-02."""
    cur = conn.execute(f"PRAGMA {db_name}.wal_checkpoint({mode})")
    row = cur.fetchone()
    if row is not None:
        return (int(row[0]), int(row[1]), int(row[2]))
    return (0, 0, 0)


def passive_checkpoint_path(db_path: str | Path) -> tuple[int, int, int]:
    """Opens a connection to db_path, executes PASSIVE checkpoint, and closes cleanly."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        return checkpoint_wal_passive(conn)
    finally:
        conn.close()

def is_wal_mode(conn: sqlite3.Connection) -> bool:
    """Checks whether database connection operates in WAL journal mode."""
    cur = conn.execute("PRAGMA journal_mode")
    row = cur.fetchone()
    return bool(row and str(row[0]).lower() == "wal")
# [MC-BB-11] SRS-412-04-FR-008
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-04-FR-008: Task
# SPEC: batch injection shall be completely idempotent via unique `task_id` constraints and `INSERT OR
# SPEC: IGNORE`.
import json
from collections.abc import Iterable

SQL_INSERT_OR_IGNORE_TASK: str = """
INSERT OR IGNORE INTO jobs (
    task_id, job_type, priority, status, payload_json, attempts
) VALUES (?, ?, ?, 'PENDING', ?, 0)
"""

def inject_task(
    conn: sqlite3.Connection,
    task_id: str,
    payload: Any,
    job_type: str = "micro_code",
    priority: int = 100,
) -> bool:
    """Idempotently injects a single task into the blackboard queue (SRS-412-04-FR-008)."""
    payload_str = payload if isinstance(payload, str) else json.dumps(payload)
    cur = conn.execute(SQL_INSERT_OR_IGNORE_TASK, (task_id, job_type, priority, payload_str))
    return cur.rowcount > 0


def inject_task_batch(conn: sqlite3.Connection, tasks: Iterable[dict[str, Any]], commit: bool = True) -> int:
    """Idempotently injects a batch of tasks into the blackboard queue (SRS-412-04-FR-008)."""
    rows = [
        (str(t["task_id"]), str(t.get("job_type", "micro_code")), int(t.get("priority", 100)),
         t.get("payload_json") if isinstance(t.get("payload_json"), str) else json.dumps(t.get("payload", {})))
        for t in tasks
    ]
    init_changes = conn.total_changes
    conn.executemany(SQL_INSERT_OR_IGNORE_TASK, rows)
    if commit:
        conn.commit()
    return conn.total_changes - init_changes

inject_tasks = inject_task_batch

`
