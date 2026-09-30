# Chapter 8: Autonomous SRE Watchdog Daemon (ch08)
**Out-of-Band Sterility, 4-Matrix Diagnostic Engine, and Self-Healing Triggers**

- **Document ID**: SRS-412-08
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 1: Host Warden](ch01_host_warden.md) | [Chapter 4: Task Matrix Blackboard](ch04_task_matrix_blackboard.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Autonomous SRE Watchdog Daemon** (`watchdog_sre.py`), providing out-of-band monitoring, multi-matrix failure diagnostics, and automated self-healing resuscitation.

---

## 2. Boundary
The SRE Watchdog operates strictly out-of-band. It accesses `job_board.db` in read-only mode (`?mode=ro`), monitors host and guest operating system processes via `psutil`, and executes independent recovery subprocesses upon detecting catastrophic collapse.

---

## 3. Definitions
- **Absolute Sterility**: Prohibition against importing pipeline application code; relying solely on Python Standard Library and `psutil`.
- **4-Matrix Diagnostics**: Exhaustive classification covering Process Death, Zombie Deadlocks, Data Corruption, and Resource Exhaustion.
- **Evidence Bundle**: An immutable diagnostic package containing system metrics, DB snapshots, and stack traces assembled upon collapse.
- **Signal ZD-8**: The canonical illegal state heuristic query detecting corrupted queue states: `SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL) OR status = 'FAILED' OR (status = 'PENDING' AND attempts >= max_attempts)`.

---

## 4. Functional Requirements

- **SRS-412-08-FR-001**: The SRE Watchdog shall be completely sterile, importing solely the Python standard library and `psutil`, and opening `job_board.db` in read-only mode (`?mode=ro`).
- **SRS-412-08-FR-002**: The Watchdog shall execute an exhaustive 4-Matrix Diagnostic Engine on a continuous 30-second loop.
- **SRS-412-08-FR-003**: Matrix 1 (Process Death) shall inspect OS process trees via `psutil` to detect dead, hung, or orphaned worker processes.
- **SRS-412-08-FR-004**: Matrix 2 (Zombie Deadlocks) shall detect frozen task leases where `lease_expires_at` is exceeded by more than 60 seconds (calibrated to 1860s).
- **SRS-412-08-FR-005**: Matrix 3 (Data Corruption) shall inspect database binary headers directly for the magic bytes `b"SQLite format 3\x00"`.
- **SRS-412-08-FR-006**: Matrix 4 (Resource Exhaustion) shall monitor memory consumption slope and trigger alarms on leaking subprocesses.
- **SRS-412-08-FR-007**: Upon confirming a `COLLAPSED` state, the Watchdog shall freeze active processes and assemble a self-contained Evidence Bundle.
- **SRS-412-08-FR-008**: The Watchdog shall initiate self-healing by launching an out-of-band `claude.exe` Fable 5.1 recovery process with the Evidence Bundle.
- **SRS-412-08-FR-009**: The Watchdog shall periodically evaluate the Signal ZD-8 heuristic: `SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL) OR status = 'FAILED' OR (status = 'PENDING' AND attempts >= max_attempts)`. Any non-zero count shall immediately trigger a `COLLAPSED` state, freeze active workers, and dispatch an Evidence Bundle for resuscitation.


---

## 5. Non-Functional Requirements
- **NFR-SRE-01**: Watchdog monitoring loop execution time shall not exceed 1.0 second per 30-second cycle.
- **NFR-SRE-02**: The Watchdog shall never modify or acquire write locks on `job_board.db`.
- **NFR-SRE-03**: Self-healing trigger sequence shall initiate within 5 seconds of structural collapse confirmation.

---

## 6. Interfaces & Sterile Header Check Implementation

```python
import os
import psutil
from pathlib import Path

SQLITE_MAGIC_HEADER = b"SQLite format 3\x00"

def verify_database_binary_header(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size < 16:
        return False
    with open(db_path, "rb") as f:
        header = f.read(16)
        return header == SQLITE_MAGIC_HEADER

def check_process_liveness(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
```

---

## 7. Data Models
```json
{
  "evidence_bundle": {
    "verdict": "COLLAPSED",
    "timestamp": 1727575200,
    "matrix_failures": ["Matrix 2: Zombie Deadlock detected on task WBS-04-12"],
    "stalled_task_id": "WBS-04-12",
    "stalled_duration_sec": 1895,
    "system_memory_used_pct": 82.5,
    "recovery_action": "TRIGGER_FABLE_RESUSCITATION"
  }
}
```

---

## 8. Failure Modes & Recovery
- **Watchdog Crash**: Monitored by the Host Warden on Windows 11; Warden restarts the Watchdog daemon if its heartbeat ceases.
- **False Positive Collapse**: Threshold buffers (1860s vs 1800s lease) prevent premature resuscitation during valid long-running compiles.

---

## 9. Test Obligations & Verification
- Unit test verifies binary header check matches `b"SQLite format 3\x00"`.
- Test verifies out-of-band read-only connection leaves database WAL file untouched.
- Test verifies process tree tracking cleanly identifies exited subprocess handles.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-08-FR-001 | V4.1.2 Master Architecture Plan §9 | `test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation` |
| SRS-412-08-FR-005 | SRE Watchdog Mandate R2 | `test_scenario_1_full_pipeline_bootstrap_lifecycle` |
