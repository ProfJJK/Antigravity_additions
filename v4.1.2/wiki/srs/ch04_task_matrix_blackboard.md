# Chapter 4: Universal Blackboard & Task Matrix Queue (ch04)
**SQLite WAL Schema, Atomic BEGIN IMMEDIATE Leases, and Poison-Pill Quarantine**

- **Document ID**: SRS-412-04
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 3: Two-Staged Concurrency Layers](ch03_concurrency_layers.md) | [Chapter 5: Research-Driven TDD Pivot](ch05_research_tdd_pivot.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Universal Blackboard & Task Matrix Queue** (`job_board.db`), providing an asynchronous tuple-space coordination backplane for all pipeline workers.

---

## 2. Boundary
The Task Matrix Queue boundary includes the SQLite database file, the write-ahead log (`.db-wal`) and shared memory (`.db-shm`) files, the connection pooling layer, and atomic lease acquisition transactions.

---

## 3. Definitions
- **BEGIN IMMEDIATE**: SQLite transaction mode that acquires a reserved lock immediately, preventing writer-writer race conditions.
- **Lease Duration**: The 1800-second (30-minute) window during which a worker has exclusive ownership of a claimed task.
- **Poison-Pill Quarantine**: Transitioning tasks with `attempts >= 3` to `BLOCKED` status to prevent endless crash loops.

---

## 4. Functional Requirements

- **SRS-412-04-FR-001**: The task blackboard shall operate over SQLite in Write-Ahead Logging (WAL) mode with `PRAGMA synchronous = NORMAL` and `PRAGMA busy_timeout = 30000`.
- **SRS-412-04-FR-002**: Worker daemons shall claim tasks exclusively using atomic `BEGIN IMMEDIATE` transactions with lease expiration updates.
- **SRS-412-04-FR-003**: The default task lease duration shall be strictly 1800 seconds (30 minutes).
- **SRS-412-04-FR-004**: Active worker daemons shall refresh lease expiration timestamps every 5 seconds via heartbeat updates.
- **SRS-412-04-FR-005**: Tasks exceeding 3 failed execution attempts shall automatically transition to terminal `BLOCKED` status.
- **SRS-412-04-FR-006**: The queue shall route tasks across the Urgency (High/Med/Low) x Fidelity (High/Low) matrix with capability matching.
- **SRS-412-04-FR-007**: Observers, telemetry monitors, and SRE watchdogs shall connect using read-only database URIs (`file:job_board.db?mode=ro`).
- **SRS-412-04-FR-008**: Task batch injection shall be completely idempotent via unique `task_id` constraints and `INSERT OR IGNORE`.
- **SRS-412-04-FR-009**: Any state transition terminating 'RUNNING' status (to 'COMPLETED', 'FAILED', or 'BLOCKED') shall explicitly clear `lease_owner = NULL` and `lease_expires_at = NULL` in the same transaction to maintain the Signal ZD-8 invariant.

---

## 5. Non-Functional Requirements
- **NFR-TM-01**: Atomic lease acquisition latency shall be less than 5 milliseconds under concurrent 10-daemon contention.
- **NFR-TM-02**: The SQLite database WAL checkpointing shall not block read queries.
- **NFR-TM-03**: Zero duplicate claims shall occur under any concurrency load.

---

## 6. Interfaces & Atomic Claim Implementation

```python
import sqlite3
import time
from typing import Any

def claim_next_task(db_path: str, daemon_id: str, lease_sec: int = 1800) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    now = int(time.time())
    lease_expires = now + lease_sec
    
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.execute(
            """
            SELECT id, task_id, job_type, payload_json, attempts 
            FROM jobs 
            WHERE status = 'PENDING' AND attempts < 3
            ORDER BY priority DESC, id ASC 
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
            
        task_dict = dict(row)
        cur.execute(
            """
            UPDATE jobs 
            SET status = 'RUNNING', lease_owner = ?, lease_expires_at = ?, attempts = attempts + 1
            WHERE id = ?
            """,
            (daemon_id, lease_expires, task_dict["id"])
        )
        conn.commit()
        return task_dict
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def complete_task(db_path: str, task_id: int) -> bool:
    """Transition task to COMPLETED and clear lease_owner to maintain Signal ZD-8 invariant."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'COMPLETED', lease_owner = NULL, lease_expires_at = NULL, updated_at = strftime('%s', 'now')
            WHERE id = ? AND status = 'RUNNING'
            """,
            (task_id,)
        )
        success = cur.rowcount > 0
        conn.commit()
        return success
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def fail_task(db_path: str, task_id: int, error_log: str, max_attempts: int = 3) -> bool:
    """Transition failed task to FAILED or BLOCKED, clearing lease_owner for ZD-8 safety."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.execute("SELECT attempts FROM jobs WHERE id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return False
        attempts = row[0]
        new_status = 'BLOCKED' if attempts >= max_attempts else 'FAILED'
        cur.execute(
            """
            UPDATE jobs
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL, error_log = ?, updated_at = strftime('%s', 'now')
            WHERE id = ?
            """,
            (new_status, error_log, task_id)
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def heartbeat_lease(db_path: str, task_id: int, daemon_id: str, extend_sec: int = 1800) -> bool:
    """Refresh lease expiration timestamp for an active running task."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    now = int(time.time())
    new_expires = now + extend_sec
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.execute(
            """
            UPDATE jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND lease_owner = ? AND status = 'RUNNING'
            """,
            (new_expires, now, task_id, daemon_id)
        )
        success = cur.rowcount > 0
        conn.commit()
        return success
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

---

## 7. Data Models
```sql
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
```

---

## 8. Failure Modes & Recovery
- **Daemon Crash with Active Lease**: Watchdog detects expired `lease_expires_at < now` and resets task to `PENDING` if `attempts < 3`.
- **Database Lock Contention**: `PRAGMA busy_timeout = 30000` retries automatically for up to 30 seconds before failing cleanly.

---

## 9. Test Obligations & Verification
- Unit test verifies `BEGIN IMMEDIATE` prevents double claims across concurrent threads.
- Test verifies `attempts >= 3` transitions status to `BLOCKED`.
- Test asserts observer connection opens with `?mode=ro`.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-04-FR-001 | V4.1.2 Master Architecture Plan §5 | `test_scenario_1_full_pipeline_bootstrap_lifecycle` |
| SRS-412-04-FR-002 | PROJECT.md Interface Contracts | `test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation` |
| SRS-412-04-FR-005 | V4.1.2 Master Architecture Plan §5 | `test_scenario_2_poison_pill_quarantine_and_10_cycle_pivot` |
