# src/cochem/blackboard/leases.py

`python
"""Blackboard lease layer for job_board.db (SRS-412-04): BEGIN IMMEDIATE claims, routing, heartbeats, reclaim."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import statistics
import threading
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from cochem.blackboard.lifecycle import clear_lease_and_transition, determine_failure_status
from cochem.blackboard.schema import BUSY_TIMEOUT_MS

# [MC-BB-12] SRS-412-04-FR-003
# SPEC: The default task lease duration shall be strictly 1800 seconds (30 minutes).
# SPEC: Claims skip rows with attempts >= 3 (section 6); connections wait up to 30 s for locks.
DEFAULT_LEASE_SEC: int = 1800
MAX_CLAIM_ATTEMPTS: int = 3
SQLITE_TIMEOUT_SEC: float = 30.0
LOCK_RETRY_SLEEP_SEC: float = 0.0002  # pause between BEGIN IMMEDIATE retries while another daemon holds the lock
_THREAD_STATE = threading.local()  # per-thread connection cache used by begin_immediate

# [MC-BB-13] SRS-412-04-FR-006
# SPEC: Route tasks across the Urgency (High/Med/Low) x Fidelity (High/Low) matrix with capability matching.
# SPEC: The section 7 jobs table has no urgency/fidelity columns: both derive from priority and payload_json.
URGENCY_LEVELS: tuple[str, ...] = ("HIGH", "MED", "LOW")
FIDELITY_LEVELS: tuple[str, ...] = ("HIGH", "LOW")
URGENCY_HIGH_MIN_PRIORITY: int = 200
URGENCY_MED_MIN_PRIORITY: int = 100


def _payload_level(payload: dict[str, Any] | None, key: str, levels: tuple[str, ...]) -> str | None:
    value = str(payload.get(key, "")).strip().upper() if isinstance(payload, dict) else ""
    value = "MED" if value == "MEDIUM" else value
    return value if value in levels else None


def derive_urgency(priority: int, payload: dict[str, Any] | None = None) -> str:
    """payload["urgency"] wins when it names a level; else priority >= 200 HIGH, >= 100 MED, else LOW."""
    level = _payload_level(payload, "urgency", URGENCY_LEVELS)
    if level is None:
        high, med = priority >= URGENCY_HIGH_MIN_PRIORITY, priority >= URGENCY_MED_MIN_PRIORITY
        level = "HIGH" if high else ("MED" if med else "LOW")
    return level


def derive_fidelity(priority: int, payload: dict[str, Any] | None = None) -> str:
    """payload["fidelity"] wins when it names a level; else HIGH when priority >= 200, else LOW."""
    level = _payload_level(payload, "fidelity", FIDELITY_LEVELS)
    return level if level is not None else ("HIGH" if priority >= URGENCY_HIGH_MIN_PRIORITY else "LOW")


def classify_task(priority: int, payload_json: str | None) -> tuple[str, str]:
    """(urgency, fidelity); payload_json that is not a JSON object is classified from priority alone."""
    try:
        decoded: Any = json.loads(payload_json) if payload_json else None
    except json.JSONDecodeError:
        decoded = None
    payload = decoded if isinstance(decoded, dict) else None
    return derive_urgency(priority, payload), derive_fidelity(priority, payload)


def capability_matches(capabilities: Iterable[tuple[str, str]] | None, urgency: str, fidelity: str) -> bool:
    """None serves every cell; otherwise (urgency, fidelity) must be listed (case-insensitive)."""
    cell = (str(urgency).upper(), str(fidelity).upper())
    return capabilities is None or any((str(u).upper(), str(f).upper()) == cell for u, f in capabilities)


def select_routed_task(
    cur: sqlite3.Cursor,
    capabilities: Iterable[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Section 6 SELECT inside BEGIN IMMEDIATE; returns the first row whose matrix cell the daemon serves."""
    caps = None if capabilities is None else [(str(u).upper(), str(f).upper()) for u, f in capabilities]
    cur.execute(
        "SELECT id, task_id, job_type, payload_json, attempts, priority FROM jobs "
        "WHERE status = 'PENDING' AND attempts < 3 ORDER BY priority DESC, id ASC"
    )
    for row in cur:  # rows are read only until the first match
        if capability_matches(caps, *classify_task(row[5], row[3])):
            return {
                "id": row[0],
                "task_id": row[1],
                "job_type": row[2],
                "payload_json": row[3],
                "attempts": row[4],
            }
    return None


# [MC-BB-14] SRS-412-04-FR-002
# SPEC: Daemons claim only inside atomic BEGIN IMMEDIATE transactions: commit on success, rollback and re-raise.
class _ThreadConnections(dict):  # one connection per thread and database: opening one costs ~4 ms (NFR-TM-01)
    def __del__(self) -> None:  # the owning thread ended: close its connections (possibly from another thread)
        close_thread_connections(self)


def _thread_connection(db_path: str, timeout_sec: float) -> sqlite3.Connection:
    cache = _THREAD_STATE.__dict__.setdefault("connections", _ThreadConnections())
    if db_path not in cache:
        conn = sqlite3.connect(db_path, timeout=timeout_sec, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous = NORMAL")
        cache[db_path] = conn
    return cache[db_path]


def close_thread_connections(cache: dict[str, sqlite3.Connection] | None = None) -> None:
    """Close the calling thread's cached connections (daemon shutdown, end of a worker thread)."""
    cache = _THREAD_STATE.__dict__.get("connections", {}) if cache is None else cache
    while cache:
        cache.popitem()[1].close()


@contextlib.contextmanager
def begin_immediate(db_path: str, timeout_sec: float = SQLITE_TIMEOUT_SEC) -> Iterator[sqlite3.Cursor]:
    """Yield a cursor inside a BEGIN IMMEDIATE transaction; isolation_level=None makes it the only transaction."""
    conn, deadline = _thread_connection(db_path, timeout_sec), time.monotonic() + timeout_sec
    cur = conn.cursor()
    try:
        conn.execute("PRAGMA busy_timeout = 0")  # the lock wait is retried below: SQLite's own sleeps are coarse
        while not conn.in_transaction:
            try:
                cur.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:  # a lock still held at the deadline is raised, not hidden
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(LOCK_RETRY_SLEEP_SEC)
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        yield cur
        conn.commit()
    finally:
        if conn.in_transaction:  # reached on any exception: roll back, and the exception keeps propagating
            conn.rollback()
        cur.close()


# [MC-BB-15] INTERFACE:claim_next_task
# SPEC: Implement claim_next_task(db_path: str, daemon_id: str, lease_sec: int = 1800) -> dict[str, Any] | None.
# SPEC: Match the section 6 reference implementation, composed from begin_immediate (MC-BB-14),
# SPEC: select_routed_task (MC-BB-13) and DEFAULT_LEASE_SEC (MC-BB-12).


def claim_next_task(
    db_path: str,
    daemon_id: str,
    lease_sec: int = DEFAULT_LEASE_SEC,
    *,
    capabilities: Iterable[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Atomically lease the next PENDING task (SRS-412-04 section 6, FR-002/FR-003/FR-006).

    Inside one BEGIN IMMEDIATE transaction: select the highest-priority PENDING row with
    attempts < 3 (priority DESC, id ASC) whose Urgency x Fidelity cell matches
    ``capabilities`` (None = every cell), then mark it RUNNING, owned by ``daemon_id``,
    with lease_expires_at = now + lease_sec and attempts incremented.

    Returns the pre-update row as a dict with keys id, task_id, job_type, payload_json,
    attempts (attempts is the value before the increment), or None when nothing matches.
    """
    now = int(time.time())
    lease_expires = now + lease_sec
    with begin_immediate(db_path) as cur:
        task_dict = select_routed_task(cur, capabilities)
        if task_dict is None:
            return None
        cur.execute(
            """
            UPDATE jobs
            SET status = 'RUNNING', lease_owner = ?, lease_expires_at = ?, attempts = attempts + 1
            WHERE id = ?
            """,
            (daemon_id, lease_expires, task_dict["id"]),
        )
        return task_dict


# [MC-BB-16] NFR-TM-03
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-TM-03: Zero duplicate
# SPEC: claims shall occur under any concurrency load.


class DuplicateClaimError(RuntimeError):
    """Raised when one jobs.id was handed to more than one claimant (NFR-TM-03 violation)."""

    def __init__(self, duplicate_ids: list[int], counts: dict[int, int]) -> None:
        self.duplicate_ids = list(duplicate_ids)
        self.counts = dict(counts)
        detail = ", ".join(f"{job_id} x{self.counts[job_id]}" for job_id in self.duplicate_ids)
        super().__init__(f"NFR-TM-03 violated: duplicate claims for job ids {detail}")


def _count_claims(claimed_ids: Iterable[int]) -> Counter[int]:
    """Counts every claimed jobs.id; each id is coerced with int() so '7' and 7 are the same claim."""
    return Counter(int(job_id) for job_id in claimed_ids)


def find_duplicate_claims(claimed_ids: Iterable[int]) -> list[int]:
    """Returns the sorted jobs.id values that appear more than once in claimed_ids."""
    counts = _count_claims(claimed_ids)
    return sorted(job_id for job_id, seen in counts.items() if seen > 1)


def assert_no_duplicate_claims(claimed_ids: Iterable[int]) -> None:
    """Raises DuplicateClaimError naming every id claimed more than once; returns None otherwise.

    claimed_ids is consumed exactly once, so generators are counted in full.
    """
    counts = _count_claims(claimed_ids)
    duplicates = sorted(job_id for job_id, seen in counts.items() if seen > 1)
    if duplicates:
        raise DuplicateClaimError(duplicates, {job_id: counts[job_id] for job_id in duplicates})


# [MC-BB-17] NFR-TM-01
# SPEC: Lease acquisition latency shall be < 5 ms under concurrent 10-daemon contention. Every latency is a
# SPEC: real perf_counter timing of claim_next_task on a daemon connection that is already open.
LEASE_LATENCY_BUDGET_MS: float = 5.0


def measure_claim_latency(
    db_path: str,
    daemon_count: int = 10,
    claims_per_daemon: int = 1,
    lease_sec: int = DEFAULT_LEASE_SEC,
) -> dict[str, Any]:
    """Run daemon_count real threads, released together, each timing claims_per_daemon claims."""
    barrier, lock, errors = threading.Barrier(daemon_count), threading.Lock(), []
    latencies_ms: list[float] = []
    claimed_ids: list[int] = []

    def _worker(index: int) -> None:
        try:
            _thread_connection(db_path, SQLITE_TIMEOUT_SEC).execute("SELECT count(*) FROM jobs").fetchone()  # open it
            barrier.wait()
            for _ in range(claims_per_daemon):
                start = time.perf_counter()
                task = claim_next_task(db_path, f"latency-probe-{index}", lease_sec)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                with lock:
                    latencies_ms.append(elapsed_ms)
                    if task is not None:
                        claimed_ids.append(int(task["id"]))
        except BaseException as exc:  # re-raised in the caller below; abort frees waiting peers
            errors.append(exc)
            barrier.abort()
        finally:
            close_thread_connections()

    threads = [threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(daemon_count)]
    for thread in threads:
        thread.start()
    for thread in threads:  # every worker is joined; no timeout, no latency is dropped
        thread.join()
    if errors:
        raise errors[0]
    ordered = sorted(latencies_ms)  # p95 by nearest rank: ordered[ceil(0.95 * n) - 1]
    return {
        "daemon_count": daemon_count,
        "latencies_ms": latencies_ms,
        "median_ms": statistics.median(ordered) if ordered else None,
        "p95_ms": ordered[max(0, -(-95 * len(ordered) // 100) - 1)] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
        "claimed_ids": claimed_ids,
    }


# [MC-BB-18] INTERFACE:heartbeat_lease
# SPEC: Append after the previous chunk without editing earlier lines. Implement
# SPEC: heartbeat_lease(db_path: str, task_id: int, daemon_id: str, extend_sec: int = 1800) -> bool.
# SPEC: Refresh lease expiration timestamp for an active running task. Match the section 6 reference
# SPEC: implementation, composed from the helpers defined in earlier chunks.


def heartbeat_lease(db_path: str, task_id: int, daemon_id: str, extend_sec: int = DEFAULT_LEASE_SEC) -> bool:
    """Refresh lease expiration timestamp for an active running task (SRS-412-04 section 6).

    task_id is the integer jobs.id. The UPDATE runs inside a BEGIN IMMEDIATE transaction
    (begin_immediate commits on success, rolls back and re-raises on error, always closes).
    Returns True only when a RUNNING row leased by daemon_id was refreshed; a wrong owner,
    a non-RUNNING status or an unknown id changes nothing and returns False.
    """
    now = int(time.time())
    new_expires = now + extend_sec
    with begin_immediate(db_path) as cur:
        cur.execute(
            """
            UPDATE jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND lease_owner = ? AND status = 'RUNNING'
            """,
            (new_expires, now, task_id, daemon_id),
        )
        return cur.rowcount > 0


# [MC-BB-19] SRS-412-04-FR-004
# SPEC: Active worker daemons shall refresh lease expiration timestamps every 5 seconds via heartbeat updates.
HEARTBEAT_INTERVAL_SEC: int = 5


class LeaseHeartbeat:
    """Refresh a claimed lease every interval_sec until stopped, the lease is lost, or heartbeat_lease raises."""

    def __init__(
        self,
        db_path: str,
        task_id: int,
        daemon_id: str,
        interval_sec: float = HEARTBEAT_INTERVAL_SEC,
        extend_sec: int = DEFAULT_LEASE_SEC,
    ) -> None:
        self.db_path, self.task_id, self.daemon_id = db_path, task_id, daemon_id
        self.interval_sec, self.extend_sec = interval_sec, extend_sec
        self.beats, self.lost = 0, False
        self.error: Exception | None = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-heartbeat-{task_id}", daemon=True)

    def _run(self) -> None:
        try:
            while not self._stop_event.wait(self.interval_sec):
                if not heartbeat_lease(self.db_path, self.task_id, self.daemon_id, self.extend_sec):
                    self.lost = True
                    return
                self.beats += 1
        except Exception as exc:  # kept on the instance and re-raised by stop()
            self.error = exc
        finally:
            close_thread_connections()

    def start(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        if self._thread.ident is not None:
            self._thread.join(timeout)
        if self.error is not None:
            raise self.error

    def __enter__(self) -> LeaseHeartbeat:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


# [MC-BB-20] FAILURE:Daemon Crash with Active Lease
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'Daemon
# SPEC: Crash with Active Lease': Watchdog detects expired `lease_expires_at < now` and resets task to
# SPEC: `PENDING` if `attempts < 3`. The reset to PENDING must also clear lease_owner and
# SPEC: lease_expires_at.
def reclaim_expired_leases(
    db_path: str,
    now: int | None = None,
    max_attempts: int = MAX_CLAIM_ATTEMPTS,
) -> dict[str, list[int]]:
    """Reclaim RUNNING rows whose lease expired (lease_expires_at < now) in one BEGIN IMMEDIATE.

    attempts < max_attempts -> PENDING with lease_owner/lease_expires_at cleared (claimable again);
    otherwise the lease is cleared and the row quarantined via determine_failure_status (BLOCKED).
    Unexpired leases and non-RUNNING rows are never touched. Ids are returned in ascending order.
    """
    now = int(time.time()) if now is None else int(now)
    result: dict[str, list[int]] = {"reset": [], "blocked": []}
    with begin_immediate(db_path) as cur:
        cur.execute(
            "SELECT id, attempts, lease_owner, lease_expires_at FROM jobs"
            " WHERE status = 'RUNNING' AND lease_expires_at < ? ORDER BY id ASC",
            (now,),
        )
        expired = [dict(row) for row in cur.fetchall()]
        for row in expired:
            job_id, attempts = int(row["id"]), int(row["attempts"] or 0)
            if attempts < max_attempts:
                cur.execute(
                    "UPDATE jobs SET status = 'PENDING', lease_owner = NULL, lease_expires_at = NULL,"
                    " updated_at = ? WHERE id = ? AND status = 'RUNNING' AND lease_expires_at < ?",
                    (now, job_id, now),
                )
                if cur.rowcount > 0:
                    result["reset"].append(job_id)
            else:
                error_log = (
                    f"Lease expired: owner {row['lease_owner']!r} lease_expires_at "
                    f"{row['lease_expires_at']} < now {now} after {attempts} attempt(s)"
                )
                status = determine_failure_status(attempts, max_attempts)
                if clear_lease_and_transition(cur.connection, job_id, status, error_log=error_log):
                    result["blocked"].append(job_id)
    return result

`
