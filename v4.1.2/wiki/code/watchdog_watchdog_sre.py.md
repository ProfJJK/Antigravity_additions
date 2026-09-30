# src/cochem/watchdog/watchdog_sre.py

`python
# [MC-SRE-08] SRS-412-08-FR-001
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Satisfy
# SPEC: SRS-412-08-FR-001: The SRE Watchdog shall be completely sterile, importing solely the Python
# SPEC: standard library and `psutil`, and opening `job_board.db` in read-only mode (`?mode=ro`).
# SPEC: src/cochem/watchdog/watchdog_sre.py already exists (Stage 7 daemon, ~619 lines): reconcile it
# SPEC: chunk by chunk, never replace it whole. Allowed imports are the standard library and psutil only
# SPEC: (no cochem.*); run it by file path so src/cochem/__init__.py never executes. Open job_board.db
# SPEC: as file:<path>?mode=ro with uri=True. Traceability test: tests/test_ch08_watchdog_sre.py::test_s
# SPEC: cenario_3_multi_worker_lease_contention_and_zombie_reclamation, written by this chapter's E2E
# SPEC: node (the same-named test in tests/test_real_world_e2e_scenarios.py is a different suite).
"""Autonomous SRE Watchdog Daemon (SRS-412-08 / SRS-412-08-FR-001)."""
from __future__ import annotations

import argparse, hashlib, json, logging, logging.handlers, os, signal, socket
import sqlite3, stat, subprocess, sys, threading, time, urllib.parse
from collections import deque
from pathlib import Path
from typing import Any
import psutil

SQLITE_MAGIC_HEADER: bytes = b"SQLite format 3\x00"
WAL_MAGIC_NUMBERS: tuple[int, ...] = (0x377F0682, 0x377F0683)
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200


def open_readonly(db_path: Path | str, timeout: float = 5.0) -> sqlite3.Connection:
    """SRS-412-08-FR-001: Out-of-band sterile read-only URI opener (?mode=ro)."""
    p = Path(db_path).resolve().as_posix()
    uri = f"file:{urllib.parse.quote(p, safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


# ------------------------------------------------------------------------------
# End of MC-SRE-08 chunk (lines 1-40)
# ------------------------------------------------------------------------------

# [MC-SRE-09] NFR-SRE-02
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-SRE-02: The Watchdog
# SPEC: shall never modify or acquire write locks on `job_board.db`. Set PRAGMA query_only=ON on every
# SPEC: connection and never run BEGIN IMMEDIATE/EXCLUSIVE; attempted writes must surface
# SPEC: sqlite3.OperationalError, not be swallowed.
import time, urllib.parse
from collections import deque
from pathlib import Path
from typing import Any
import psutil

def enforce_query_only_guard(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA query_only = ON")

def refuse_write_locks(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA query_only = ON")

def verify_query_only_mode(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA query_only").fetchone()
    return bool(row and row[0] == 1)

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH: Path = REPO_ROOT / "job_board.db"
DEFAULT_WORKER_STATE_DIR: Path = REPO_ROOT / ".evidence" / "dsp_worker"
DEFAULT_EVIDENCE_DIR: Path = REPO_ROOT / ".evidence" / "watchdog"
DEFAULT_CLAUDE_EXE: Path = Path(r"C:\Users\ansac\.local\bin\claude.exe")
SQLITE_MAGIC_HEADER: bytes = b"SQLite format 3\x00"
WAL_MAGIC_NUMBERS: tuple[int, ...] = (0x377F0682, 0x377F0683)
CYCLE_INTERVAL_SEC: float = 30.0; CYCLE_BUDGET_SEC: float = 1.0; LEASE_GRACE_SEC: int = 60
WORKER_HEARTBEAT_STALE_SEC: float = 120.0; WORKER_JOB_TIMEOUT_SEC: float = 1740.0
NO_CONSUMER_GRACE_SEC: float = 300.0; WORKER_RSS_CAP_MB: float = 512.0
NO_CONSUMER_MARKER: str = "no live DSP worker"; INSTANCE_LOCK_NAME: str = "watchdog.pid"; EXIT_ALREADY_RUNNING: int = 3
RSS_SLOPE_WINDOW: int = 10; RSS_SLOPE_ALARM_MB_PER_MIN: float = 5.0; RSS_SLOPE_FLOOR_MB: float = 256.0
SYSTEM_MEMORY_ALARM_PCT: float = 95.0; RECOVERY_TIMEOUT_SEC: float = 1740.0; DEFAULT_COOLDOWN_SEC: float = 3600.0
SNAPSHOT_MAX_BYTES: int = 256 * 1024 * 1024; FABLE_MODEL: str = "claude-fable-5-1"
CREATE_NO_WINDOW: int = 0x08000000; CREATE_NEW_PROCESS_GROUP: int = 0x00000200
SIGNAL_ZD8_SQL: str = ("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL) OR status = 'FAILED' OR (status = 'PENDING' AND attempts >= max_attempts)")
SIGNAL_ZD8_ROWS_SQL: str = ("SELECT task_id, status, attempts, max_attempts, lease_owner, substr(error_log, 1, 500) AS error_log FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL) OR status = 'FAILED' OR (status = 'PENDING' AND attempts >= max_attempts) ORDER BY id LIMIT 25")
logger = logging.getLogger("cochem.watchdog.watchdog_sre")

# --- End of MC-SRE-09 chunk (lines 41-80) ---
# [MC-SRE-10] INTERFACE:verify_database_binary_header
# SPEC: Append after the previous chunk without editing earlier lines. Implement
# SPEC: verify_database_binary_header(db_path: Path) -> bool. Match the section 6 reference
# SPEC: implementation, composed from the helpers defined in earlier chunks. Use the 16-byte
# SPEC: SQLITE_MAGIC_HEADER constant from section 6.


def verify_database_binary_header(db_path: Path) -> bool:
    """Verifies that the database binary header matches SQLite format 3 (SRS-412-08 §6)."""
    if not db_path.exists() or db_path.stat().st_size < 16:
        return False
    with open(db_path, "rb") as f:
        header = f.read(16)
        return check_binary_header_magic(header)


def verify_wal_header(wal_path: Path) -> bool:
    """An absent or empty (checkpointed) WAL is healthy; otherwise the magic must match."""
    if not wal_path.exists() or wal_path.stat().st_size == 0:
        return True
    with open(wal_path, "rb") as f:
        header = f.read(4)
    return len(header) == 4 and int.from_bytes(header, "big") in WAL_MAGIC_NUMBERS


def check_binary_header_magic(raw_bytes: bytes) -> bool:
    """Directly verifies a 16-byte buffer against the SQLite magic header constant."""
    return len(raw_bytes) >= 16 and raw_bytes[:16] == SQLITE_MAGIC_HEADER

# --- End of MC-SRE-10 chunk (lines 81-110) ---
# [MC-SRE-11] INTERFACE:check_process_liveness
# SPEC: Append after the previous chunk without editing earlier lines. Implement
# SPEC: check_process_liveness(pid: int) -> bool. Match the section 6 reference implementation, composed
# SPEC: from the helpers defined in earlier chunks. Treat psutil.AccessDenied as not live, exactly as
# SPEC: section 6 does.
def check_process_liveness(pid: int) -> bool:
    """SRS-412-08 section 6: live means running and not a zombie; AccessDenied counts as not live."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def live_process_tree(pid: int) -> list[psutil.Process]:
    """Root process followed by its recursive children, live ones only; [] if the root is not live."""
    if not check_process_liveness(pid):
        return []
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []
    return [root] + [child for child in children if check_process_liveness(child.pid)]
# --- End of MC-SRE-11 chunk (lines 111-140) ---





# [MC-SRE-12] SRS-412-08-FR-003
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-003: Matrix
# SPEC: 1 (Process Death) shall inspect OS process trees via `psutil` to detect dead, hung, or orphaned
# SPEC: worker processes. Map job lease_owner values to PIDs and flag three cases separately: dead PID,
# SPEC: hung (stopped status or zero CPU time delta across a cycle) and orphaned (parent gone).
def parse_lease_owner_pid(lease_owner: str | None, hostname: str) -> int | None:
    """Local PID from "<host>:<pid>" or "dsp-worker@<host>:<pid>"; None for foreign or malformed owners."""
    host, sep, pid = (lease_owner or "").rsplit("@", 1)[-1].rpartition(":")
    return int(pid) if sep and host.lower() == hostname.lower() and pid.isascii() and pid.isdigit() else None

def matrix_process_death(worker_tasks: dict[int, str | None], cpu_history: dict[int, tuple[float, float]],
                         flat_window_sec: float = CYCLE_INTERVAL_SEC) -> list[str]:
    """FR-003 Matrix 1: dead, hung (stopped, or tree CPU flat for a whole cycle) and orphaned DSP workers."""
    failures: list[str] = []
    for gone in [p for p in cpu_history if not worker_tasks.get(p)]:  # PIDs gone or idle: no CPU baseline
        del cpu_history[gone]
    for pid, task in worker_tasks.items():
        try:
            proc = psutil.Process(pid) if check_process_liveness(pid) else None
            status, orphaned = (proc.status(), proc.parent() is None) if proc else ("", False)
        except psutil.Error:  # the process vanished between the liveness check and the inspection
            proc = None
        if proc is None and task:
            failures.append(f"Matrix 1: DSP worker pid {pid} died holding task {task} (orphaned lease on task {task})")
        elif proc is None:
            logger.warning("Matrix 1: idle DSP worker pid %s is dead (no task held)", pid)
        if proc is None:
            cpu_history.pop(pid, None)
            continue
        if status == psutil.STATUS_STOPPED:
            failures.append(f"Matrix 1: DSP worker pid {pid} hung (process status stopped, task {task})")
        elif task:
            base, seen = cpu_history.get(pid), time.monotonic()
            try:  # user+system CPU seconds summed over the whole live process tree
                cpu = sum(sum(p.cpu_times()[:2]) for p in live_process_tree(pid))
            except psutil.Error:  # a tree member exited mid-read: no reading this cycle
                cpu = None
            if cpu is not None and (base is None or cpu > base[1]):
                cpu_history[pid] = (seen, cpu)  # first sight or CPU progress: new (monotonic time, CPU) baseline
            elif cpu is not None and seen - base[0] >= flat_window_sec:
                failures.append(f"Matrix 1: DSP worker pid {pid} hung on task {task} "
                                f"(tree CPU time flat for {seen - base[0]:.0f}s, a whole cycle)")
        if orphaned:
            failures.append(f"Matrix 1: DSP worker pid {pid} orphaned (parent process gone, task {task})")
    return failures
# [MC-SRE-13] SRS-412-08-FR-004
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-004: Matrix
# SPEC: 2 (Zombie Deadlocks) shall detect frozen task leases where `lease_expires_at` is exceeded by
# SPEC: more than 60 seconds (calibrated to 1860s). Read 'more than 60 seconds (calibrated to 1860s)' as
# SPEC: now > lease_expires_at + 60 on the 1800 s lease, i.e. a task stalled for 1860 s. Take the grace
# SPEC: period from one constant.


def lease_overdue_sec(lease_expires_at: float | None, now: float) -> float:
    """Seconds since the lease expired (negative while it is valid); inf when no expiry is recorded."""
    if lease_expires_at is None:
        return float("inf")
    return float(now) - float(lease_expires_at)


def matrix_zombie_deadlocks(running: list[Any], now: float) -> tuple[list[str], dict[str, Any] | None]:
    """SRS-412-08-FR-004 Matrix 2: flag RUNNING leases with now > lease_expires_at + LEASE_GRACE_SEC.

    On the 1800 s lease that is a task stalled for more than 1860 s. Returns one failure string per
    zombie and the worst (largest overdue) zombie as {"task_id", "overdue", "stalled_sec"}, or None.
    """
    failures: list[str] = []
    worst: dict[str, Any] | None = None
    for row in running:
        expires = row["lease_expires_at"]
        if expires is not None and not now > expires + LEASE_GRACE_SEC:
            continue
        overdue = lease_overdue_sec(expires, now)
        task_id = row["task_id"]
        failures.append(f"Matrix 2: Zombie Deadlock detected on task {task_id} "
                        f"(lease expired {overdue:.0f}s ago, owner {row['lease_owner']})")
        if worst is None or overdue > worst["overdue"]:
            stalled = overdue + LEASE_DURATION_SEC if expires is not None else overdue
            worst = {"task_id": str(task_id), "overdue": overdue, "stalled_sec": stalled}
    return failures, worst










# [MC-SRE-14] FAILURE:False Positive Collapse
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'False
# SPEC: Positive Collapse': Threshold buffers (1860s vs 1800s lease) prevent premature resuscitation
# SPEC: during valid long-running compiles. A lease inside its 60 s grace window must never produce a
# SPEC: Matrix 2 failure or a COLLAPSED verdict.
LEASE_DURATION_SEC: int = 1800
ZOMBIE_STALL_THRESHOLD_SEC: int = LEASE_DURATION_SEC + LEASE_GRACE_SEC


def lease_within_grace_window(lease_expires_at: float | None, now: float) -> bool:
    """True while the lease is unexpired or expired at most LEASE_GRACE_SEC ago; False for None."""
    if lease_expires_at is None:
        return False
    return now <= float(lease_expires_at) + LEASE_GRACE_SEC


def confirm_verdict(matrix_failures: list[str], running: list[Any], now: float) -> tuple[str, list[str]]:
    """Drop Matrix 2 findings whose task lease is still inside the 1860 s buffer; keep all other matrices."""
    graced = {str(row["task_id"]) for row in running if lease_within_grace_window(row["lease_expires_at"], now)}
    kept: list[str] = []
    for failure in matrix_failures:
        if failure.startswith("Matrix 2:"):
            _, found, rest = failure.partition(" on task ")
            task_id = rest.split(" (lease expired", 1)[0] if found else None
            if task_id in graced:
                logger.info("false positive collapse suppressed for task %s (lease in grace window)", task_id)
                continue
        kept.append(failure)
    return ("COLLAPSED", kept) if kept else ("HEALTHY", [])

# [MC-SRE-15] SRS-412-08-FR-005
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-005: Matrix
# SPEC: 3 (Data Corruption) shall inspect database binary headers directly for the magic bytes `b"SQLite
# SPEC: format 3\x00"`. Also check the -wal file when present; a missing database is a Matrix 3 failure,
# SPEC: not a crash. Traceability test:
# SPEC: tests/test_ch08_watchdog_sre.py::test_scenario_1_full_pipeline_bootstrap_lifecycle, written by
# SPEC: this chapter's E2E node (the same-named test in tests/test_real_world_e2e_scenarios.py is a
# SPEC: different suite).
def matrix_data_corruption(db_path: Path) -> list[str]:
    """Matrix 3 (SRS-412-08-FR-005): scan the database and -wal binary headers directly.

    Missing, short, mis-headed or unreadable files each yield one "Matrix 3: ..." failure naming the file.
    """
    db_path = Path(db_path)
    wal_path = db_path.with_name(db_path.name + "-wal")
    failures: list[str] = []
    if not db_path.exists():
        failures.append(f"Matrix 3: database {db_path} is missing")
    else:
        try:
            with open(db_path, "rb") as fh:
                size = len(fh.read(16))
            if size < 16:
                failures.append(f"Matrix 3: database {db_path} is shorter than the 16-byte header ({size} bytes)")
            elif not verify_database_binary_header(db_path):
                failures.append(f"Matrix 3: database {db_path} binary header is not {SQLITE_MAGIC_HEADER!r}")
        except OSError as exc:
            failures.append(f"Matrix 3: database {db_path} unreadable ({type(exc).__name__}: {exc})")
    try:
        if not verify_wal_header(wal_path):
            failures.append(f"Matrix 3: WAL file {wal_path} magic number is invalid")
    except OSError as exc:
        failures.append(f"Matrix 3: WAL file {wal_path} unreadable ({type(exc).__name__}: {exc})")
    return failures

# [MC-SRE-16] SRS-412-08-FR-006
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-006: Matrix
# SPEC: 4 (Resource Exhaustion) shall monitor memory consumption slope and trigger alarms on leaking
# SPEC: subprocesses. Keep a bounded deque of (timestamp, rss_mb) per PID and alarm on a least-squares
# SPEC: slope above a named MB/min threshold; the chapter gives no number, so expose it as a constant.
def rss_slope_mb_per_min(samples: deque[tuple[float, float]]) -> float:
    """Least-squares slope of (time_s, rss_mb) samples in MB/min; 0.0 below 3 samples or with no time spread."""
    n = len(samples)
    if n < 3:
        return 0.0
    t0 = min(t for t, _ in samples)
    if max(t for t, _ in samples) == t0:  # no time spread; a rounded mean would leave a non-zero denom
        return 0.0
    mean_t = sum(t - t0 for t, _ in samples) / n
    mean_r = sum(r for _, r in samples) / n
    denom = sum((t - t0 - mean_t) ** 2 for t, _ in samples)
    return 60.0 * sum((t - t0 - mean_t) * (r - mean_r) for t, r in samples) / denom


def matrix_resource_exhaustion(pids: list[int], rss_history: dict[int, deque[tuple[float, float]]],
                               now: float, slope_alarm: float = RSS_SLOPE_ALARM_MB_PER_MIN,
                               slope_floor_mb: float = RSS_SLOPE_FLOOR_MB,
                               window: int = RSS_SLOPE_WINDOW) -> list[str]:
    """Matrix 4 (FR-006): real psutil RSS per live PID, leak slope over a full window, RSS cap, host memory."""
    failures, seen = [], set()
    for pid in [p for p in pids if check_process_liveness(p)]:
        try:
            rss_mb = psutil.Process(pid).memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        seen.add(pid)
        history = rss_history.get(pid)
        if history is None or history.maxlen != window:
            history = rss_history[pid] = deque(history or (), maxlen=window)
        history.append((now, rss_mb))
        if rss_mb > WORKER_RSS_CAP_MB:
            failures.append(f"Matrix 4: DSP worker pid {pid} RSS {rss_mb:.0f} MB over {WORKER_RSS_CAP_MB:.0f} MB cap")
        slope = rss_slope_mb_per_min(history)
        if len(history) == window and slope > slope_alarm and rss_mb > slope_floor_mb:
            failures.append(f"Matrix 4: DSP worker pid {pid} leaking {slope:.1f} MB/min (RSS {rss_mb:.0f} MB)")
    for gone in set(rss_history) - seen:
        rss_history.pop(gone)
    mem_pct = psutil.virtual_memory().percent
    if mem_pct > SYSTEM_MEMORY_ALARM_PCT:
        failures.append(f"Matrix 4: host memory {mem_pct:.1f}% used (> {SYSTEM_MEMORY_ALARM_PCT:.0f}%)")
    return failures
# [MC-SRE-17] SRS-412-08-FR-009
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-009: The
# SPEC: Watchdog shall periodically evaluate the Signal ZD-8 heuristic: `SELECT COUNT(*) FROM jobs WHERE
# SPEC: (status != 'RUNNING' AND lease_owner IS NOT NULL) OR status = 'FAILED' OR (status = 'PENDING'
# SPEC: AND attempts >= max_attempts)`. Any non-zero count shall immediately trigger a `COLLAPSED`
# SPEC: state, freeze active workers, and dispatch an Evidence Bundle for resuscitation. Run the Signal
# SPEC: ZD-8 statement verbatim from section 3. As specified, a single FAILED row trips COLLAPSED; do
# SPEC: not quietly narrow the predicate.
def evaluate_signal_zd8(conn: sqlite3.Connection) -> tuple[int, list[dict[str, Any]]]:
    """Signal ZD-8 (SRS-412-08-FR-009): run the section 3 statement verbatim, then list up to 25 rows.

    The row query reuses the WHERE predicate of SIGNAL_ZD8_SQL unchanged and selects only the columns that
    exist in jobs (error_log cut to 500 characters). sqlite3.Error propagates to the caller.
    """
    count = int(conn.execute(SIGNAL_ZD8_SQL).fetchone()[0])
    if count == 0:
        return 0, []
    present = {str(info[1]) for info in conn.execute("PRAGMA table_info(jobs)")}
    wanted = ("task_id", "status", "attempts", "max_attempts", "lease_owner", "error_log")
    cols = [f"substr({c}, 1, 500) AS {c}" if c == "error_log" else c for c in wanted if c in present]
    predicate = SIGNAL_ZD8_SQL.split(" WHERE ", 1)[1]
    cur = conn.execute(f"SELECT {', '.join(cols)} FROM jobs WHERE {predicate} ORDER BY rowid LIMIT 25")
    names = [desc[0] for desc in cur.description]
    return count, [dict(zip(names, tuple(row))) for row in cur.fetchall()]


def signal_zd8_failures(count: int, rows: list[dict[str, Any]]) -> list[str]:
    """Any non-zero ZD-8 count is one "Signal ZD-8: ..." failure listing at most 5 TASK=STATUS pairs."""
    if not count:
        return []
    listing = ", ".join(f"{row.get('task_id')}={row.get('status')}" for row in rows[:5])
    return [f"Signal ZD-8: {count} illegal queue state(s) ({listing})"]








# [MC-SRE-18] DATA_MODEL:evidence_bundle
# SPEC: Append after the previous chunk without editing earlier lines. Define the evidence_bundle record
# SPEC: exactly as the section 7 JSON model, with a validator that rejects missing or mistyped fields.
# SPEC: Fields: verdict: str (e.g. "COLLAPSED"); timestamp: int (e.g. 1727575200); matrix_failures: list
# SPEC: (e.g. ["Matrix 2: Zombie Deadlock detected on task WBS-04-12"]); stalled_task_id: str (e.g.
# SPEC: "WBS-04-12"); stalled_duration_sec: int (e.g. 1895); system_memory_used_pct: float (e.g. 82.5);
# SPEC: recovery_action: str (e.g. "TRIGGER_FABLE_RESUSCITATION"). verdict is the literal COLLAPSED,
# SPEC: timestamp is integer epoch seconds, matrix_failures is a list of strings. Write the bundle
# SPEC: atomically (temp file + os.replace) and mark it read-only so it is immutable.
EVIDENCE_BUNDLE_FIELDS: dict[str, type] = {"verdict": str, "timestamp": int, "matrix_failures": list,
    "stalled_task_id": str, "stalled_duration_sec": int, "system_memory_used_pct": float, "recovery_action": str}
def validate_evidence_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Check the section 7 fields (exact types: bool is not int, int is not float); ValueError names each."""
    if not isinstance(bundle, dict):
        raise ValueError(f"evidence_bundle must be a dict, got {type(bundle).__name__}")
    bad = [f"{key}: missing" for key in EVIDENCE_BUNDLE_FIELDS if key not in bundle]
    for key, kind in EVIDENCE_BUNDLE_FIELDS.items():
        if key in bundle and type(bundle[key]) is not kind:
            bad.append(f"{key}: expected {kind.__name__}, got {type(bundle[key]).__name__}")
    if type(bundle.get("matrix_failures")) is list and not all(type(f) is str for f in bundle["matrix_failures"]):
        bad.append("matrix_failures: every entry must be str")
    if type(bundle.get("verdict")) is str and bundle["verdict"] != "COLLAPSED":
        bad.append(f"verdict: expected 'COLLAPSED', got {bundle['verdict']!r}")
    if "job_board_snapshot" in bundle:  # FR-007 self-containment: an absent snapshot must say why
        snap, why = bundle["job_board_snapshot"], bundle.get("job_board_snapshot_absent_reason")
        if snap is not None and type(snap) is not str:
            bad.append(f"job_board_snapshot: expected str or None, got {type(snap).__name__}")
        elif snap is None and not (type(why) is str and why.strip()):
            bad.append("job_board_snapshot_absent_reason: required non-empty str when job_board_snapshot is None")
        elif snap is not None and why is not None:
            bad.append("job_board_snapshot_absent_reason: must be None when a snapshot is present")
    if bad:
        raise ValueError("invalid evidence_bundle: " + "; ".join(bad))
    return bundle

def write_evidence_bundle(path: Path, bundle: dict[str, Any]) -> Path:
    """Validate, write via temp file + os.replace, chmod read-only; FileExistsError if path exists (immutable)."""
    validate_evidence_bundle(bundle)
    if (path := Path(path)).exists():
        raise FileExistsError(f"evidence bundle {path} already exists and is immutable")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:  # the temp file never outlives this call, also when the write fails
        tmp.write_text(json.dumps({"evidence_bundle": bundle}, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    os.chmod(path, stat.S_IREAD)
    return path
# [MC-SRE-19] SRS-412-08-FR-007
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-007: Upon
# SPEC: confirming a `COLLAPSED` state, the Watchdog shall freeze active processes and assemble a self-
# SPEC: contained Evidence Bundle. Freeze with psutil.Process.suspend() on the worker tree and always
# SPEC: resume in a finally block once the bundle is dispatched. Include the DB snapshot and Python
# SPEC: stack traces.
def freeze_workers(pids: list[int]) -> list[int]:
    """FR-007: psutil suspend() on every live process of each worker tree (never the watchdog); frozen roots."""
    frozen: list[int] = []
    for pid in [p for p in pids if p != os.getpid()]:
        tree = [proc for proc in live_process_tree(pid) if proc.pid != os.getpid()]
        try:
            for proc in tree:
                proc.suspend()
            frozen += [pid] if tree else []
        except psutil.Error as exc:  # a partial freeze is undone and the tree is not reported as frozen
            logger.warning("could not freeze worker tree %s: %s", pid, exc)
            thaw_workers([pid])
    return frozen

def thaw_workers(pids: list[int]) -> None:
    """Resume every live process of each frozen tree; one failed resume never stops the others."""
    for proc in [p for pid in pids for p in live_process_tree(pid)]:
        try:
            proc.resume()
        except psutil.Error as exc:  # vanished or access denied: logged, the remaining processes still resume
            logger.warning("could not resume pid %s: %s", proc.pid, exc)

def assemble_evidence_bundle(diagnosis: dict[str, Any], frozen: list[int], db_path: Path,
                             bundles_dir: Path, worker_state_dir: Path, recovery_action: str) -> Path:
    """FR-007: bundle dir with the read-only DB snapshot, real stack traces and evidence_bundle.json."""
    stamp = bundles_dir / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(diagnosis["timestamp"]))
    for n in range(10**6):  # mkdir is the existence check: two processes racing on one stamp cannot collide
        bundle_dir = Path(f"{stamp}_{n}") if n else stamp
        try:
            bundle_dir.mkdir(parents=True)
            break
        except FileExistsError:
            if bundle_dir.is_dir():  # a real collision on this stamp: try the next suffix
                continue
            raise  # e.g. bundles_dir is a regular file: fail at once, never spin through the suffixes
    else:
        raise FileExistsError(f"no free bundle directory name under {bundles_dir} for {stamp.name}")
    snapshot, snapshot_absent_reason = _snapshot_job_board(db_path, bundle_dir)
    task, sec = (stalled["task_id"], stalled["stalled_sec"]) if (stalled := diagnosis.get("stalled")) else ("", 0)
    bundle = {"verdict": diagnosis["verdict"], "timestamp": int(diagnosis["timestamp"]), "host": socket.gethostname(),
              "matrix_failures": list(diagnosis["matrix_failures"]), "stalled_task_id": str(task),
              "stalled_duration_sec": int(sec) if sec != float("inf") else -1, "recovery_action": recovery_action,
              "system_memory_used_pct": float(psutil.virtual_memory().percent), "frozen_worker_pids": list(frozen),
              "stack_traces": _stack_traces(worker_state_dir, diagnosis["zd8_rows"]), "watchdog_pid": os.getpid(),
              "job_board_snapshot": snapshot, "job_board_snapshot_absent_reason": snapshot_absent_reason,
              "job_status_counts": diagnosis["status_counts"], "signal_zd8_count": diagnosis["zd8_count"],
              "job_board_path": str(db_path), "signal_zd8_rows": diagnosis["zd8_rows"],
              "ready_pending_jobs": diagnosis["ready_pending"],
              "job_board_readable": diagnosis.get("job_board_readable"), "job_board_error": diagnosis.get("job_board_error")}
    return write_evidence_bundle(bundle_dir / "evidence_bundle.json", bundle)

def freeze_and_dispatch(diagnosis: dict[str, Any], db_path: Path, bundles_dir: Path, worker_state_dir: Path,
                        recovery_action: str, dispatch: Any = None, freeze: bool = True) -> tuple[Path, Any, list[int]]:
    """FR-007: freeze the worker trees, assemble and dispatch the bundle; the finally always thaws them."""
    frozen = freeze_workers(list(diagnosis["worker_tasks"])) if freeze else []
    try:
        path = assemble_evidence_bundle(diagnosis, frozen, db_path, bundles_dir, worker_state_dir, recovery_action)
        return path, (dispatch(path) if dispatch is not None else None), frozen
    finally:
        thaw_workers(frozen)
# [MC-SRE-20] SRS-412-08-FR-008
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-008: The
# SPEC: Watchdog shall initiate self-healing by launching an out-of-band `claude.exe` Fable 5.1 recovery
# SPEC: process with the Evidence Bundle. Build an argv list (no shell=True), launch detached, and pass
# SPEC: the bundle path. The executable path must be configurable and recovery must be disable-able
# SPEC: (--no-recovery). Keep the existing --claude-exe flag so tests launch a real stand-in executable
# SPEC: instead of claude.exe.
def build_recovery_command(claude_exe: Path, bundle_path: Path, db_path: Path,
                           permission_mode: str = "bypassPermissions") -> list[str]:
    """Return the argv list (never a shell string) for the out-of-band Fable 5.1 recovery session."""
    claude_exe, bundle_path = Path(claude_exe), Path(bundle_path)
    exe = [sys.executable, str(claude_exe)] if claude_exe.suffix.lower() == ".py" else [str(claude_exe)]
    prompt = (
        f"You are the Fable 5.1 SRE resuscitation agent for the CoChem V4.1.2 pipeline at {REPO_ROOT}. "
        f"The out-of-band SRE Watchdog declared a COLLAPSED state. Read the Evidence Bundle at {bundle_path} "
        f"(a read-only job_board snapshot sits beside it). DSP worker PIDs listed in frozen_worker_pids were "
        f"suspended while the bundle was assembled and have been resumed. Diagnose the root cause against "
        f"wiki/srs/ch04_task_matrix_blackboard.md and ch08_watchdog_sre.md, then repair the live job board "
        f"({db_path}) with minimal, transactional (BEGIN IMMEDIATE) edits so that Signal ZD-8 returns 0, "
        f"and fix the code defect if one caused the collapse. Never delete or recreate job_board.db. "
        f"Write your findings and every change you made to {bundle_path.parent / 'RECOVERY_REPORT.md'}.")
    return [*exe, "-p", prompt, "--model", FABLE_MODEL, "--permission-mode", permission_mode,
            "--add-dir", str(REPO_ROOT), "--add-dir", str(bundle_path.parent),
            "--append-system-prompt", str(bundle_path)]

class RecoveryLaunchError(OSError):
    """FR-008: the recovery executable exists but the operating system refused to start it."""


def launch_recovery(claude_exe: Path, bundle_path: Path, db_path: Path,
                    permission_mode: str = "bypassPermissions",
                    recovery_enabled: bool = True) -> subprocess.Popen | None:
    """Start the detached recovery process (FR-008); None when disabled (--no-recovery) or exe missing.

    A Popen OSError (bad executable, access denied ...) is raised as RecoveryLaunchError, never swallowed.
    """
    claude_exe, bundle_path = Path(claude_exe), Path(bundle_path)
    if not recovery_enabled:
        logger.info("recovery disabled (--no-recovery); bundle %s not dispatched", bundle_path)
        return None
    if not claude_exe.is_file():
        logger.error("recovery executable not found at %s; recovery not launched", claude_exe)
        return None
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--max-old-space-size=4096"
    detach: dict[str, Any] = ({"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                              else {"start_new_session": True})
    argv = build_recovery_command(claude_exe, bundle_path, db_path, permission_mode)
    with open(bundle_path.parent / "recovery_session.log", "ab") as log:
        try:
            proc = subprocess.Popen(argv, cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, **detach)
        except OSError as exc:
            logger.error("recovery executable %s could not be started: %s: %s", claude_exe, type(exc).__name__, exc)
            raise RecoveryLaunchError(f"{claude_exe} could not be started: {type(exc).__name__}: {exc}") from exc
    logger.info("recovery pid %s launched with bundle %s", proc.pid, bundle_path)
    return proc




# [MC-SRE-21] NFR-SRE-03
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-SRE-03: Self-healing
# SPEC: trigger sequence shall initiate within 5 seconds of structural collapse confirmation. Measure
# SPEC: from COLLAPSED confirmation to recovery Popen return with time.monotonic().
SELF_HEALING_DEADLINE_SEC: float = 5.0


def trigger_self_healing(diagnosis: dict[str, Any], db_path: Path, bundles_dir: Path, worker_state_dir: Path,
                         claude_exe: Path, permission_mode: str = "bypassPermissions",
                         recovery_enabled: bool = True, freeze: bool = True,
                         confirmed_at: float | None = None) -> dict[str, Any]:
    """NFR-SRE-03: freeze, bundle and launch recovery; latency runs from COLLAPSED to Popen return.

    The bundle's recovery_action states what will be attempted (TRIGGER_FABLE_RESUSCITATION only when the
    executable exists). The launch outcome - LAUNCHED, DISABLED, EXECUTABLE_MISSING or LAUNCH_FAILED - is
    written to the read-only companion record recovery_launch.json beside the (immutable) bundle. A launch
    that was required but did not start never counts as meeting the deadline and is logged at error level.
    """
    start = time.monotonic() if confirmed_at is None else float(confirmed_at)
    exe_present = Path(claude_exe).is_file()
    action = ("NONE_RECOVERY_DISABLED" if not recovery_enabled else
              "TRIGGER_FABLE_RESUSCITATION" if exe_present else "NONE_RECOVERY_EXECUTABLE_MISSING")
    marks: dict[str, float] = {}
    errors: list[str] = []

    def _dispatch(bundle_path: Path) -> subprocess.Popen | None:
        try:
            proc = launch_recovery(claude_exe, bundle_path, db_path, permission_mode, recovery_enabled)
        except RecoveryLaunchError as exc:  # recorded below in recovery_launch.json, state and heartbeat
            errors.append(str(exc))
            proc = None
        marks["launched"] = time.monotonic()
        return proc

    bundle, proc, frozen = freeze_and_dispatch(diagnosis, db_path, bundles_dir, worker_state_dir, action,
                                               dispatch=_dispatch, freeze=freeze)
    latency = marks["launched"] - start
    outcome = ("LAUNCHED" if proc is not None else "DISABLED" if not recovery_enabled else
               "LAUNCH_FAILED" if errors else "EXECUTABLE_MISSING")
    error = errors[0] if errors else (f"recovery executable not found at {claude_exe}"
                                      if outcome == "EXECUTABLE_MISSING" else None)
    met = latency <= SELF_HEALING_DEADLINE_SEC and outcome in ("LAUNCHED", "DISABLED")
    if outcome in ("LAUNCH_FAILED", "EXECUTABLE_MISSING"):
        logger.error("NFR-SRE-03 missed: no recovery process started (%s: %s); bundle %s", outcome, error, bundle)
    elif not met:
        logger.error("NFR-SRE-03 missed: self-healing trigger took %.3fs (deadline %.1fs)", latency,
                     SELF_HEALING_DEADLINE_SEC)
    record = _write_readonly_json(bundle.parent / "recovery_launch.json", {"recovery_launch": {
        "outcome": outcome, "bundle_recovery_action": action, "recovery_pid": proc.pid if proc is not None else None,
        "claude_exe": str(claude_exe), "error": error, "bundle": str(bundle), "timestamp": int(time.time()),
        "trigger_latency_sec": latency, "deadline_met": met}})
    return {"bundle": bundle, "recovery_pid": proc.pid if proc is not None else None, "frozen_pids": frozen,
            "trigger_latency_sec": latency, "deadline_met": met, "recovery_outcome": outcome,
            "recovery_error": error, "recovery_record": record}


def _write_readonly_json(path: Path, data: dict[str, Any]) -> Path:
    """Write a companion evidence record atomically (temp file + os.replace) and mark it read-only."""
    if path.exists():
        raise FileExistsError(f"evidence record {path} already exists and is immutable")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    os.chmod(path, stat.S_IREAD)
    return path
# [MC-SRE-22] SRS-412-08-FR-002
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-08-FR-002: The
# SPEC: Watchdog shall execute an exhaustive 4-Matrix Diagnostic Engine on a continuous 30-second loop.
# SPEC: Schedule on time.monotonic() so a slow cycle does not drift the 30 s cadence; a matrix exception
# SPEC: is recorded as that matrix's failure, not a loop crash.
# Errors a single matrix step can raise from its real inputs: file/OS access (OSError), the job board
# (sqlite3.Error), the process table (psutil.Error) and malformed column values in the external board, which
# SQLite does not type-check, e.g. TEXT in lease_expires_at (TypeError/ValueError). Anything else propagates.
MATRIX_STEP_ERRORS: tuple[type[Exception], ...] = (OSError, sqlite3.Error, psutil.Error, TypeError, ValueError)
# Errors a whole daemon cycle may raise and the loop can survive (the cycle records them in state/heartbeat).
CYCLE_RECOVERABLE_ERRORS: tuple[type[Exception], ...] = (OSError, sqlite3.Error, psutil.Error)


def run_diagnostic_matrices(db_path: Path, worker_tasks: dict[int, str | None], hostname: str, now: float,
                            cpu_history: dict[int, tuple[float, float]],
                            rss_history: dict[int, deque[tuple[float, float]]]) -> dict[str, Any]:
    """FR-002: Matrix 3, board read, Matrices 1, 2, 4 and Signal ZD-8, each step guarded on its own.

    When the job board cannot be read, the board-derived fields (zd8_count, zd8_rows, status_counts,
    ready_pending) are None - never zeros - and job_board_readable/job_board_error say why.
    """
    failures: list[str] = []
    board: dict[str, Any] | None = None
    board_error: str | None = None
    tasks = dict(worker_tasks)
    def guarded(prefix: str, step: Any) -> Any:  # a step error becomes this matrix's failure; the rest still run
        try:
            return step()
        except MATRIX_STEP_ERRORS as exc:
            failures.append(f"{prefix}diagnostic raised {type(exc).__name__}: {exc}")
            return None
    def matrix_1() -> list[str]:  # local lease owners of RUNNING jobs join the known workers
        tasks.update({pid: row["task_id"] for row in running if not tasks.get(
            pid := parse_lease_owner_pid(row["lease_owner"], hostname)) and pid is not None})
        return matrix_process_death(tasks, cpu_history)

    failures += guarded("Matrix 3: ", lambda: matrix_data_corruption(db_path)) or []
    if failures:
        board_error = "not read: Matrix 3 reported the job board file as corrupt or unreadable"
    else:
        try:
            board = read_job_board(db_path, now)
        except (OSError, sqlite3.Error) as exc:
            board_error = f"{type(exc).__name__}: {exc}"
            failures.append(f"Matrix 3: diagnostic raised {board_error}")
    running = [dict(row) for row in board["running"]] if board is not None else []
    failures += guarded("Matrix 1: ", matrix_1) or []
    zombie = guarded("Matrix 2: ", lambda: matrix_zombie_deadlocks(running, now)) or ([], None)
    failures += zombie[0]
    failures += guarded("Matrix 4: ", lambda: matrix_resource_exhaustion(list(tasks), rss_history, now)) or []
    if board is not None:
        failures += guarded("Signal ZD-8: ", lambda: signal_zd8_failures(board["zd8_count"], board["zd8_rows"])) or []
    verdict, kept = guarded("Matrix 2: ", lambda: confirm_verdict(failures, running, now)) or ("COLLAPSED", failures)
    measured = board if board is not None else {"zd8_count": None, "zd8_rows": None, "status_counts": None,
                                                "ready_pending": None}
    return {"verdict": verdict, "timestamp": int(now), "matrix_failures": kept, "stalled": zombie[1],
            "zd8_count": measured["zd8_count"], "zd8_rows": measured["zd8_rows"],
            "status_counts": measured["status_counts"], "ready_pending": measured["ready_pending"],
            "worker_tasks": tasks, "running": running, "job_board_readable": board is not None,
            "job_board_error": board_error}

def run_watchdog_loop(cycle: Any, stop_event: threading.Event, interval: float = CYCLE_INTERVAL_SEC,
                      max_cycles: int | None = None) -> int:
    """FR-002: cycle() on a drift-free time.monotonic() grid; missed deadlines are skipped, not replayed.

    Only CYCLE_RECOVERABLE_ERRORS are survived (logged at error level; the daemon's cycle has already
    recorded them in its heartbeat/state). Any other exception ends the loop and the daemon, whose
    heartbeat then ceases so the Host Warden restarts it (SRS-412-08 section 8, Watchdog Crash).
    """
    start, runs = time.monotonic(), 0
    while not stop_event.is_set() and (max_cycles is None or runs < max_cycles):
        try:
            cycle()
        except CYCLE_RECOVERABLE_ERRORS as exc:
            logger.error("watchdog cycle %d failed (%s: %s); the loop continues", runs + 1, type(exc).__name__, exc)
        runs += 1
        if max_cycles is None or runs < max_cycles:
            slot = (time.monotonic() - start) // interval + 1  # next grid deadline; overrun slots are skipped
            stop_event.wait(max(0.0, start + slot * interval - time.monotonic()))
    return runs
# [MC-SRE-23] NFR-SRE-01
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-SRE-01: Watchdog
# SPEC: monitoring loop execution time shall not exceed 1.0 second per 30-second cycle. Log a budget
# SPEC: breach with the measured duration; do not skip matrices to meet it.
def run_budgeted_cycle(cycle: Any, budget_sec: float = CYCLE_BUDGET_SEC) -> dict[str, Any]:
    """NFR-SRE-01: run one full diagnostic cycle and measure it against the per-cycle budget.

    `cycle()` runs to completion every time (all matrices, nothing is skipped or cut short to meet the
    budget). The duration is measured with time.perf_counter(). The returned dict is a copy of the
    cycle's diagnosis plus `cycle_duration_sec` (float) and `budget_breached` (bool). A breach is logged
    with logger.warning naming the measured duration and the budget. Exceptions from `cycle()` propagate.
    """
    started = time.perf_counter()
    diagnosis = cycle()
    duration = time.perf_counter() - started
    result: dict[str, Any] = dict(diagnosis)
    result["cycle_duration_sec"] = float(duration)
    result["budget_breached"] = bool(duration > budget_sec)
    if result["budget_breached"]:
        logger.warning("NFR-SRE-01 budget breach: watchdog cycle took %.3fs, exceeding the %.3fs budget "
                       "(all matrices still ran)", duration, budget_sec)
    return result









# [MC-SRE-19 tail] Evidence Bundle helpers for assemble_evidence_bundle (lines 421-475 hold only 49 code lines).


def _snapshot_job_board(db_path: Path, bundle_dir: Path) -> tuple[str | None, str | None]:
    """FR-007: sqlite backup-API copy of the job board from a read-only connection, marked read-only.

    Taken only when the binary header is valid and the file is no larger than SNAPSHOT_MAX_BYTES. Returns
    (snapshot path, None) or (None, reason): the reason is stored in the bundle as
    job_board_snapshot_absent_reason (checked by validate_evidence_bundle) and logged at error level.
    """
    db_path = Path(db_path)
    reason: str | None = None
    if not verify_database_binary_header(db_path):
        reason = f"not taken: {db_path} is missing or has no valid SQLite header {SQLITE_MAGIC_HEADER!r}"
    elif db_path.stat().st_size > SNAPSHOT_MAX_BYTES:
        reason = f"not taken: {db_path} is {db_path.stat().st_size} bytes, over the {SNAPSHOT_MAX_BYTES}-byte cap"
    if reason is not None:
        logger.error("job board snapshot %s", reason)
        return None, reason
    snapshot = bundle_dir / "job_board_snapshot.db"
    try:
        src = open_readonly(db_path)
        try:
            dst = sqlite3.connect(str(snapshot))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:  # the backup failed: the bundle states it (never a silent None)
        reason = f"backup failed: {type(exc).__name__}: {exc}"
        logger.error("job board snapshot %s", reason)
        snapshot.unlink(missing_ok=True)
        return None, reason
    os.chmod(snapshot, stat.S_IREAD)
    return str(snapshot), None


def _stack_traces(worker_state_dir: Path, zd8_rows: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Real stack traces only: a stdlib+psutil watchdog cannot read another process's Python frames.

    Collects the last 200 lines of each worker *.log in worker_state_dir and the error_log text of each
    Signal ZD-8 row (none when the board was unreadable). Python frames of a live worker are available only
    when the worker itself dumps them there: faulthandler.dump_traceback_later(interval, repeat=True,
    file=<worker_state_dir>/worker_<pid>.log) writes every thread's stack periodically, so the last dump
    before the freeze is collected here. An unreadable log is recorded with its OSError. {} when none exist.
    """
    traces: dict[str, list[str]] = {}
    for log in sorted(worker_state_dir.glob("*.log")) if worker_state_dir.is_dir() else []:
        try:
            traces[log.name] = log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        except OSError as exc:
            traces[log.name] = [f"log unreadable: {type(exc).__name__}: {exc}"]
    for row in zd8_rows or []:
        if row.get("error_log"):
            traces[f"zd8:{row.get('task_id')}"] = str(row["error_log"]).splitlines()
    return traces
# ------------------------------------------------------------------------------
# Stage 7 daemon shell (pre-existing SREWatchdog/CLI, reconciled onto the MC-SRE-11..23 chunk
# functions above). Lines from here on are outside the Batch 7 chunk bounds (111-635).
# ------------------------------------------------------------------------------
def _read_json(path: Path) -> dict[str, Any] | None:
    """Parsed JSON object from `path`, or None when the file is absent, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("cannot read %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_job_board(db_path: Path, now: float) -> dict[str, Any]:
    """One read-only pass over the job board; reads only columns the jobs table really has."""
    conn = open_readonly(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        running = [dict(row) for row in conn.execute(
            "SELECT task_id, lease_owner, lease_expires_at FROM jobs WHERE status = 'RUNNING'")]
        zd8_count, zd8_rows = evaluate_signal_zd8(conn)
        ready_sql = "SELECT COUNT(*) FROM jobs WHERE status = 'PENDING' AND attempts < max_attempts"
        params: tuple[int, ...] = ()
        if "not_before" in columns:
            ready_sql += " AND (not_before IS NULL OR not_before <= ?)"
            params = (int(now),)
        ready_pending = conn.execute(ready_sql, params).fetchone()[0]
        status_counts = {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")}
    finally:
        conn.close()
    return {"running": running, "zd8_count": zd8_count, "zd8_rows": zd8_rows,
            "ready_pending": ready_pending, "status_counts": status_counts}


class SREWatchdog:
    """Daemon shell: worker discovery, persistent state, cooldown, recovery supervision, heartbeat."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, worker_state_dir: Path = DEFAULT_WORKER_STATE_DIR,
                 evidence_dir: Path = DEFAULT_EVIDENCE_DIR, claude_exe: Path = DEFAULT_CLAUDE_EXE,
                 recovery_enabled: bool = True, permission_mode: str = "bypassPermissions",
                 cooldown_sec: float = DEFAULT_COOLDOWN_SEC, freeze_workers: bool = True) -> None:
        self.db_path = Path(db_path)
        self.worker_state_dir = Path(worker_state_dir)
        self.evidence_dir = Path(evidence_dir)
        self.bundles_dir = self.evidence_dir / "bundles"
        self.state_file = self.evidence_dir / "watchdog_state.json"
        self.heartbeat_file = self.evidence_dir / "watchdog_heartbeat.json"
        self.lock_file = self.evidence_dir / INSTANCE_LOCK_NAME
        self._lock_held = False
        self.claude_exe = Path(claude_exe)
        self.recovery_enabled = recovery_enabled
        self.permission_mode = permission_mode
        self.cooldown_sec = cooldown_sec
        self.freeze_workers = freeze_workers
        self.hostname = socket.gethostname()
        self.stop_event = threading.Event()
        self.cpu_history: dict[int, tuple[float, float]] = {}
        self.rss_history: dict[int, deque[tuple[float, float]]] = {}
        self.no_consumer_since: float | None = None
        self.cycles = 0
        self.state: dict[str, Any] = {"frozen_pids": [], "recovery_pid": None, "recovery_started": None,
                                      "recovery_create_time": None, "last_signature": None,
                                      "last_trigger_ts": 0.0, "last_bundle": None,
                                      "last_recovery_outcome": None, "last_recovery_error": None,
                                      "last_cycle_error": None, "consecutive_cycle_failures": 0}

    # -- persistent state ------------------------------------------------------------
    def load_state(self) -> None:
        saved = _read_json(self.state_file)
        if saved:
            self.state.update(saved)

    def save_state(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.state_file, self.state)

    def write_heartbeat(self, verdict: str, duration: float, failures: list[str] | None = None) -> None:
        """Heartbeat read by the Host Warden, which restarts the watchdog when it ceases."""
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.heartbeat_file, {"pid": os.getpid(), "ts": time.time(), "cycle": self.cycles,
                                                 "verdict": verdict, "cycle_duration_sec": round(duration, 4),
                                                 "matrix_failures": list(failures or []),
                                                 "last_cycle_error": self.state.get("last_cycle_error"),
                                                 "consecutive_cycle_failures": self.state.get("consecutive_cycle_failures", 0),
                                                 "last_recovery_outcome": self.state.get("last_recovery_outcome"),
                                                 "last_recovery_error": self.state.get("last_recovery_error")})

    # -- worker discovery --------------------------------------------------------------
    def load_worker_records(self) -> list[tuple[Path, dict[str, Any]]]:
        records = []
        if self.worker_state_dir.is_dir():
            for path in sorted(self.worker_state_dir.glob("worker_*.json")):
                record = _read_json(path)
                if record and isinstance(record.get("pid"), int):
                    records.append((path, record))
        return records

    def worker_record_failures(self, workers: list[tuple[Path, dict[str, Any]]], ready_pending: int,
                               now: float) -> list[str]:
        """Matrix 1 checks that need the worker heartbeat records: stale heartbeat, job timeout, no consumer."""
        failures: list[str] = []
        live = 0
        for path, rec in workers:
            pid = rec["pid"]
            if not check_process_liveness(pid):
                try:  # the record is the watchdog's evidence input, not the blackboard
                    path.replace(path.with_suffix(".dead"))
                except OSError as exc:
                    logger.warning("could not archive worker record %s: %s", path, exc)
                continue
            live += 1
            age = now - float(rec.get("ts", 0))
            if age > WORKER_HEARTBEAT_STALE_SEC:
                failures.append(f"Matrix 1: DSP worker pid {pid} hung (heartbeat {age:.0f}s old)")
            started = rec.get("current_task_started")
            if started and now - float(started) > WORKER_JOB_TIMEOUT_SEC:
                failures.append(f"Matrix 1: DSP worker pid {pid} hung on task {rec.get('current_task')} "
                                f"for {now - float(started):.0f}s")
        if ready_pending and live == 0:
            self.no_consumer_since = self.no_consumer_since or now
            if now - self.no_consumer_since >= NO_CONSUMER_GRACE_SEC:
                failures.append(f"Matrix 1: {ready_pending} ready PENDING job(s) and {NO_CONSUMER_MARKER} for "
                                f"{now - self.no_consumer_since:.0f}s")
        else:
            self.no_consumer_since = None
        return failures

    @staticmethod
    def is_no_consumer_failure(failure: str) -> bool:
        """The starvation finding (ready work, zero live workers): DEGRADED, not COLLAPSED (see diagnose)."""
        return NO_CONSUMER_MARKER in failure

    # -- one diagnostic pass -----------------------------------------------------------
    def diagnose(self) -> dict[str, Any]:
        now = time.time()
        workers = self.load_worker_records()
        worker_tasks = {rec["pid"]: rec.get("current_task") for _, rec in workers}
        diagnosis = run_diagnostic_matrices(self.db_path, worker_tasks, self.hostname, now,
                                            self.cpu_history, self.rss_history)
        extra = self.worker_record_failures(workers, diagnosis["ready_pending"], now)
        collapse = [f for f in extra if not self.is_no_consumer_failure(f)]
        starved = [f for f in extra if self.is_no_consumer_failure(f)]
        if collapse:
            diagnosis["matrix_failures"] = diagnosis["matrix_failures"] + collapse
            diagnosis["verdict"] = "COLLAPSED"
        # Starvation with a legal board is an operational gap (no worker deployed / all workers exited), not a
        # queue collapse: nothing on the board can be repaired and a resuscitation session cannot register the
        # worker tasks (Install-PipelineDaemons.ps1 needs elevation). Paging Fable for it re-pages every cooldown
        # (2026-09-30 bundles 20260930T085228Z / 20260930T085258Z). It is reported as DEGRADED in the heartbeat
        # and log, and listed in the bundle when another matrix collapses at the same time.
        diagnosis["degraded_failures"] = starved
        if starved:
            if diagnosis["verdict"] == "COLLAPSED":
                diagnosis["matrix_failures"] = diagnosis["matrix_failures"] + starved
            else:
                diagnosis["verdict"] = "DEGRADED"
                diagnosis["matrix_failures"] = list(starved)
        diagnosis["workers"] = [rec for _, rec in workers]
        return diagnosis

    @staticmethod
    def failure_signature(failures: list[str]) -> str:
        # Durations/sizes change every cycle; the signature keys on the failure kinds and subjects.
        normalized = sorted(" ".join(w for w in f.split() if not any(ch.isdigit() for ch in w) or "-" in w)
                            for f in failures)
        return hashlib.sha256("\n".join(normalized).encode()).hexdigest()[:16]

    # -- freeze / thaw ---------------------------------------------------------------
    def freeze(self, workers: list[dict[str, Any]]) -> list[int]:
        return freeze_workers([rec["pid"] for rec in workers])

    def thaw(self) -> None:
        thaw_workers(list(self.state.get("frozen_pids", [])))
        self.state["frozen_pids"] = []

    # -- recovery process tracking -----------------------------------------------------
    def _recovery_process(self) -> psutil.Process | None:
        pid = self.state.get("recovery_pid")
        if not pid:
            return None
        try:
            proc = psutil.Process(pid)
            created = self.state.get("recovery_create_time")
            if created is not None and abs(proc.create_time() - created) > 1.0:
                return None  # pid reused by an unrelated process
            return proc if check_process_liveness(pid) else None
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def supervise_recovery(self) -> bool:
        """Returns True while a recovery process is active; clears the record once it ends."""
        proc = self._recovery_process()
        if proc is not None:
            started = float(self.state.get("recovery_started") or time.time())
            if time.time() - started <= RECOVERY_TIMEOUT_SEC:
                return True
            logger.error("recovery pid %s exceeded %ss; terminating its process tree", proc.pid, RECOVERY_TIMEOUT_SEC)
            for p in [*proc.children(recursive=True), proc]:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    logger.debug("recovery process %s already gone", p.pid)
        if self.state.get("recovery_pid"):
            logger.info("recovery pid %s finished", self.state["recovery_pid"])
            self.state["recovery_pid"] = None
        if self.state.get("frozen_pids"):
            self.thaw()
        return False

    # -- collapse handling (FR-007 / FR-008 / NFR-SRE-03) -------------------------------
    def build_recovery_command(self, bundle_path: Path) -> list[str]:
        return build_recovery_command(self.claude_exe, bundle_path, self.db_path, self.permission_mode)

    def handle_collapse(self, diagnosis: dict[str, Any], confirmed_at: float | None = None) -> Path | None:
        signature = self.failure_signature(diagnosis["matrix_failures"])
        since_last = time.time() - float(self.state.get("last_trigger_ts") or 0)
        if signature == self.state.get("last_signature") and since_last < self.cooldown_sec:
            logger.warning("COLLAPSED (signature %s already dispatched %.0fs ago; cooldown %.0fs)",
                           signature, since_last, self.cooldown_sec)
            return None
        freeze = self.freeze_workers and self.recovery_enabled
        self.state["frozen_pids"] = list(diagnosis["worker_tasks"]) if freeze else []
        self.save_state()  # persisted before freezing, so a restarted watchdog can thaw them
        try:
            outcome = trigger_self_healing(diagnosis, self.db_path, self.bundles_dir, self.worker_state_dir,
                                           self.claude_exe, self.permission_mode, self.recovery_enabled,
                                           freeze, confirmed_at)
        finally:
            self.state["frozen_pids"] = []  # trigger_self_healing resumes the workers in its finally
            self.save_state()
        bundle_path = outcome["bundle"]
        self.state.update(last_signature=signature, last_trigger_ts=time.time(), last_bundle=str(bundle_path),
                          last_recovery_outcome=outcome["recovery_outcome"],
                          last_recovery_error=outcome["recovery_error"])
        logger.error("COLLAPSED: %s | evidence bundle %s", "; ".join(diagnosis["matrix_failures"]), bundle_path)
        if outcome["recovery_pid"] is not None:
            self.state["recovery_pid"] = outcome["recovery_pid"]
            self.state["recovery_started"] = time.time()
            try:
                self.state["recovery_create_time"] = psutil.Process(outcome["recovery_pid"]).create_time()
            except psutil.NoSuchProcess:
                self.state["recovery_create_time"] = None  # it has already exited
            logger.error("Fable 5.1 resuscitation launched (pid %s, %.2fs after confirmation)",
                         outcome["recovery_pid"], outcome["trigger_latency_sec"])
        self.save_state()
        return bundle_path

    def _cycle(self) -> dict[str, Any]:
        recovering = self.supervise_recovery()
        diagnosis = self.diagnose()
        if diagnosis["verdict"] == "COLLAPSED":
            confirmed_at = time.monotonic()
            if recovering:
                logger.warning("COLLAPSED while recovery pid %s is active: %s", self.state["recovery_pid"],
                               "; ".join(diagnosis["matrix_failures"]))
            else:
                diagnosis["bundle"] = self.handle_collapse(diagnosis, confirmed_at)
        elif diagnosis["verdict"] == "DEGRADED":
            logger.error("DEGRADED (no resuscitation: board legal, no consumer to restart from here): %s",
                         "; ".join(diagnosis["matrix_failures"]))
        elif self.cycles % 20 == 0:
            logger.info("HEALTHY %s", diagnosis["status_counts"])
        return diagnosis

    def run_cycle(self) -> dict[str, Any]:
        """One budgeted cycle. A recoverable failure (CYCLE_RECOVERABLE_ERRORS) is recorded in the state
        file and the heartbeat (last_cycle_error, consecutive_cycle_failures, verdict CYCLE_FAILED) and then
        re-raised to the loop; any other exception propagates and ends the daemon."""
        started = time.perf_counter()
        try:
            diagnosis = run_budgeted_cycle(self._cycle)
        except CYCLE_RECOVERABLE_ERRORS as exc:
            self.cycles += 1
            self.state["consecutive_cycle_failures"] = int(self.state.get("consecutive_cycle_failures") or 0) + 1
            self.state["last_cycle_error"] = {"cycle": self.cycles, "ts": time.time(),
                                              "type": type(exc).__name__, "message": str(exc)}
            logger.error("watchdog cycle %d failed (%d consecutive): %s: %s", self.cycles,
                         self.state["consecutive_cycle_failures"], type(exc).__name__, exc)
            self.write_heartbeat("CYCLE_FAILED", time.perf_counter() - started)
            self.save_state()
            raise
        self.state["consecutive_cycle_failures"] = 0
        self.cycles += 1
        self.write_heartbeat(diagnosis["verdict"], diagnosis["cycle_duration_sec"], diagnosis.get("matrix_failures"))
        self.save_state()
        return diagnosis

    # -- single-instance guard ----------------------------------------------------------
    # Two watchdogs on one evidence dir share the state/heartbeat files and each pages its own Fable session
    # for the same event (2026-09-30: PIDs 31124 and 37536, four recovery sessions). One pid file, O_EXCL.
    def acquire_instance_lock(self) -> int | None:
        """Take the evidence-dir pid lock; returns the pid of a live holder when another watchdog owns it."""
        for _ in range(3):
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_lock_pid(self.lock_file)
                if holder is not None and holder != os.getpid() and _is_live_watchdog(holder):
                    return holder
                try:  # stale lock: holder dead, pid reused by something else, or unreadable content
                    self.lock_file.unlink()
                except FileNotFoundError:
                    logger.debug("stale lock %s vanished before removal", self.lock_file)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n")
            self._lock_held = True
            return None
        return _read_lock_pid(self.lock_file) or -1  # lost the race repeatedly: report whoever holds it

    def release_instance_lock(self) -> None:
        if self._lock_held and _read_lock_pid(self.lock_file) == os.getpid():
            self.lock_file.unlink(missing_ok=True)
        self._lock_held = False

    def run(self, once: bool = False, interval: float = CYCLE_INTERVAL_SEC) -> int:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        holder = self.acquire_instance_lock()
        if holder is not None:
            logger.error("SRE Watchdog pid %s already monitors %s (lock %s); this instance (pid %s) exits",
                         holder, self.db_path, self.lock_file, os.getpid())
            return EXIT_ALREADY_RUNNING
        try:
            self.load_state()
            if self.state.get("frozen_pids") and self._recovery_process() is None:
                self.thaw()  # a previous watchdog died between freeze and thaw
            logger.info("SRE Watchdog pid %s monitoring %s (read-only) every %.0fs", os.getpid(), self.db_path, interval)
            run_watchdog_loop(self.run_cycle, self.stop_event, interval, max_cycles=1 if once else None)
            self.save_state()
        finally:
            self.release_instance_lock()
        return 0


def _read_lock_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _is_live_watchdog(pid: int) -> bool:
    """True when pid is a running process whose command line names this module (AccessDenied counts as live)."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return "watchdog_sre" in " ".join(proc.cmdline())
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def _configure_logging(log_file: Path | None) -> None:
    # With a log file, stderr is left for uncaught tracebacks (the task redirects it to *.stderr.log).
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3,
                                                         encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CoChem V4.1.2 out-of-band SRE Watchdog (4-Matrix engine)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--worker-state-dir", type=Path, default=DEFAULT_WORKER_STATE_DIR)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--claude-exe", type=Path, default=DEFAULT_CLAUDE_EXE)
    parser.add_argument("--permission-mode", default="bypassPermissions",
                        choices=["bypassPermissions", "acceptEdits", "default", "plan"])
    parser.add_argument("--no-recovery", action="store_true", help="diagnose and bundle only; never launch claude.exe")
    parser.add_argument("--no-freeze", action="store_true", help="do not suspend DSP workers on collapse")
    parser.add_argument("--cooldown-sec", type=float, default=DEFAULT_COOLDOWN_SEC)
    parser.add_argument("--interval", type=float, default=CYCLE_INTERVAL_SEC)
    parser.add_argument("--log-file", type=Path, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.log_file)

    watchdog = SREWatchdog(args.db, args.worker_state_dir, args.evidence_dir, args.claude_exe,
                           recovery_enabled=not args.no_recovery, permission_mode=args.permission_mode,
                           cooldown_sec=args.cooldown_sec, freeze_workers=not args.no_freeze)

    def _request_stop(signum: int, _frame: Any) -> None:
        logger.info("signal %s received; stopping", signum)
        watchdog.stop_event.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _request_stop)
    return watchdog.run(once=args.once, interval=args.interval)


if __name__ == "__main__":
    sys.exit(main())

`
