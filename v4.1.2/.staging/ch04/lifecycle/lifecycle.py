"""Blackboard task lifecycle (SRS-412-04 FR-005, FR-009): lease-clearing terminal transitions on job_board.db."""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except ImportError:
    agy_sdk = None

logger = logging.getLogger("cochem.blackboard.lifecycle")

# ==============================================================================
# [MC-BB-21] SRS-412-04-FR-009
# SPEC: Any state transition terminating 'RUNNING' status (to 'COMPLETED', 'FAILED',
# SPEC: or 'BLOCKED') shall explicitly clear lease_owner = NULL and lease_expires_at = NULL
# SPEC: in the same transaction to maintain the Signal ZD-8 invariant.
# ==============================================================================

SQLITE_TIMEOUT_SEC: float = 30.0
STATUS_RUNNING: str = "RUNNING"
TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "FAILED", "BLOCKED"})


def clear_lease_and_transition(
    conn: sqlite3.Connection,
    task_id: int | str,
    new_status: str,
    error_log: str | None = None,
) -> bool:
    """One UPDATE out of RUNNING that NULLs the lease (Signal ZD-8). The caller owns BEGIN/COMMIT."""
    if new_status not in TERMINAL_STATUSES:
        raise ValueError(f"new_status must be one of {sorted(TERMINAL_STATUSES)}, got {new_status!r}")
    if isinstance(task_id, bool) or not isinstance(task_id, (int, str)):
        raise TypeError(f"task_id must be int (jobs.id) or str (jobs.task_id), got {type(task_id).__name__}")
    col = "id" if isinstance(task_id, int) else "task_id"
    extra, params = (", error_log = ?", (new_status, error_log)) if error_log is not None else ("", (new_status,))
    sql = (
        f"UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL{extra},"
        f" updated_at = strftime('%s','now') WHERE {col} = ? AND status = 'RUNNING'"
    )
    return conn.execute(sql, (*params, task_id)).rowcount > 0


@contextlib.contextmanager
def immediate_transaction(
    db_path: str | Path,
    timeout_sec: float = SQLITE_TIMEOUT_SEC,
) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE on an autocommit connection: COMMIT on exit, ROLLBACK and re-raise on error, always close."""
    conn = sqlite3.connect(str(db_path), timeout=timeout_sec, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout_sec * 1000)}")
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ==============================================================================
# [MC-BB-22] SRS-412-04-FR-005
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-04-FR-005: Tasks
# SPEC: exceeding 3 failed execution attempts shall automatically transition to terminal `BLOCKED`
# SPEC: status. Traceability test: test_scenario_2_poison_pill_quarantine_and_10_cycle_pivot.
# ==============================================================================

MAX_ATTEMPTS_BEFORE_QUARANTINE: int = 3
STATUS_BLOCKED: str = "BLOCKED"
STATUS_FAILED: str = "FAILED"
STATUS_COMPLETED: str = "COMPLETED"


def determine_failure_status(attempts: int, max_attempts: int = MAX_ATTEMPTS_BEFORE_QUARANTINE) -> str:
    """BLOCKED when attempts >= max_attempts (poison pill), else FAILED (SRS-412-04-FR-005)."""
    return STATUS_BLOCKED if attempts >= max_attempts else STATUS_FAILED


def is_poison_pill(attempts: int, max_attempts: int = MAX_ATTEMPTS_BEFORE_QUARANTINE) -> bool:
    """True when the attempt count reaches the quarantine threshold."""
    return attempts >= max_attempts


def quarantine_poison_pill(
    conn: sqlite3.Connection,
    task_id: int | str,
    error_log: str = "",
    max_attempts: int = MAX_ATTEMPTS_BEFORE_QUARANTINE,
) -> tuple[bool, str]:
    """Move a RUNNING row to BLOCKED (attempts >= max_attempts) or FAILED inside the caller's transaction.

    Reads ``attempts, status`` of the row, picks the status and issues the guarded UPDATE of
    ``clear_lease_and_transition`` (``AND status = 'RUNNING'``, lease NULLed). Returns
    ``(transitioned, new_status)`` where ``transitioned`` is that UPDATE's real rowcount result: False
    for a row that is not RUNNING. A missing row returns ``(False, "")``: no attempts count exists, so
    no status is chosen and nothing is written. ``error_log`` is stored verbatim. The caller owns
    BEGIN/COMMIT; SQLite errors propagate.
    """
    col = "id" if isinstance(task_id, int) else "task_id"
    row = conn.execute(f"SELECT attempts, status FROM jobs WHERE {col} = ?", (task_id,)).fetchone()
    if row is None:
        return False, ""
    new_status = determine_failure_status(int(row[0]), max_attempts=max_attempts)
    return clear_lease_and_transition(conn, task_id, new_status, error_log=error_log), new_status


# ==============================================================================
# [MC-BB-23] INTERFACE:complete_task
# SPEC: Append after the previous chunk without editing earlier lines. Implement complete_task(db_path:
# SPEC: str, task_id: int) -> bool. Transition task to COMPLETED and clear lease_owner to maintain
# SPEC: Signal ZD-8 invariant. Match the section 6 reference implementation, composed from the helpers
# SPEC: defined in earlier chunks.
# ==============================================================================


def complete_task(db_path: str | Path, task_id: int) -> bool:
    """Transition a RUNNING task to COMPLETED and clear its lease (Signal ZD-8 invariant).

    Runs one ``BEGIN IMMEDIATE`` transaction through ``immediate_transaction``; the UPDATE issued by
    ``clear_lease_and_transition`` is guarded by ``AND status = 'RUNNING'``. Returns True only when
    this call moved the row out of RUNNING; False for an unknown id, a PENDING row, or a row that is
    already terminal (e.g. a second or concurrent completion). SQLite errors propagate unchanged.
    """
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise TypeError(f"complete_task expects the integer jobs.id, got {type(task_id).__name__}")
    with immediate_transaction(db_path) as conn:
        return clear_lease_and_transition(conn, task_id, STATUS_COMPLETED)


# ==============================================================================
# [MC-BB-24] INTERFACE:fail_task
# SPEC: Append after the previous chunk without editing earlier lines. Implement fail_task(db_path: str,
# SPEC: task_id: int, error_log: str, max_attempts: int = 3) -> bool. Transition failed task to FAILED
# SPEC: or BLOCKED, clearing lease_owner for ZD-8 safety. Match the section 6 reference implementation,
# SPEC: composed from the helpers defined in earlier chunks.
# ==============================================================================


def fail_task(db_path: str | Path, task_id: int, error_log: str, max_attempts: int = 3) -> bool:
    """Move a RUNNING task to FAILED, or to BLOCKED once attempts >= max_attempts, clearing its lease.

    One ``BEGIN IMMEDIATE`` transaction via ``immediate_transaction``: the attempts count is read and the
    guarded UPDATE (``AND status = 'RUNNING'``, from ``clear_lease_and_transition``) is issued under the
    same reserved lock. ``error_log`` is stored verbatim; ``attempts`` is left unchanged (the claim
    increments it). Returns True only when this call moved the row out of RUNNING; False for an unknown
    id, a PENDING row or an already terminal row, all of which are left untouched. Stricter than the
    SRS s6 reference, whose UPDATE had no status guard. SQLite errors propagate unchanged.
    """
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise TypeError(f"fail_task expects the integer jobs.id, got {type(task_id).__name__}")
    if not isinstance(error_log, str):
        raise TypeError(f"fail_task expects error_log as str, got {type(error_log).__name__}")
    with immediate_transaction(db_path) as conn:
        row = conn.execute(
            "SELECT attempts FROM jobs WHERE id = ? AND status = ?",
            (task_id, STATUS_RUNNING),
        ).fetchone()
        if row is None:
            return False
        new_status = determine_failure_status(int(row[0]), max_attempts=max_attempts)
        return clear_lease_and_transition(conn, task_id, new_status, error_log=error_log)
