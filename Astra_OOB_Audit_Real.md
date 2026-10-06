**No. The patch closes one narrow failure case, but the fail-open vulnerability is not definitively closed.** It rejects an unchanged or missing declared target after an otherwise successful execution. It does **not** establish that this job produced the required artifact.

This assessment is based on the supplied code; I have not executed it or inspected the imported components.

1. **Critical: omitting `target_file` bypasses verification entirely.**

   Both initialization and verification are conditional:

   ```python
   if target_file:
       ...
   if target_file_path:
       ...
   ```

   A missing, `null`, or empty `target_file` permits completion based on exit code zero, unless stderr contains the exact recognized diagnostic.

   **This is already reachable through your own pipeline:** the generated `CHAPTER_DRAFT` and `SYNTHESIS` payloads contain no `target_file`. Those jobs can still silently do nothing and become `COMPLETED`.

   Require a validated output contract for every job type. For intentionally non-file-producing jobs, use a different explicit, independently checked success condition.

2. **High: a changed hash does not prove this job changed the file.**

   There is no exclusive ownership of a target across jobs. For example:

   - Jobs A and B both snapshot target hash `H0`.
   - A successfully writes content with hash `H1`.
   - B performs no work and exits zero.
   - B observes `H1 != H0` and becomes `COMPLETED`.

   The same false attribution can come from another process or a human edit. Hash comparison establishes different observed bytes, not authorship or successful execution.

   Use isolated, per-attempt output locations and controlled publication. Serialize publication to shared targets and check the expected destination version before replacing it.

3. **High: the daemon’s own writes can satisfy the guard.**

   There is no restriction preventing `target_file` from naming a daemon-owned file. The daemon writes the result JSON **before** performing verification:

   ```python
   result_file.write_text(...)
   # Then hash target_file_path
   ```

   If the target names that result file, the daemon itself creates or modifies the target even when the worker does nothing. Heartbeat and telemetry files provide other potential cases.

   Validate canonical target paths against approved artifact locations, excluding state, telemetry, results, prompts, queue files, and other control files.

4. **High: RAM-disk gathering can publish stale content, and missing source files still only warn.**

   The missing-source branch remains:

   ```python
   logger.warning("Gather sequence: expected RAM disk file %s not found!", ramdisk_file)
   ```

   It does **not** raise. Usually the final hash check then rejects the unchanged destination, but an independent modification can make it pass.

   More directly, if the RAM workspace contains stale content different from the initial physical file, a no-op Director response can cause `copy2()` to publish that stale content. The hash check then passes despite this attempt producing nothing.

   Require attempt-specific source artifacts, verify their provenance and acceptance conditions, and raise immediately when a required source is missing. Successful copying alone does not establish that the source is the correct output.

5. **High: lease fencing does not reliably fence execution or publication.**

   Several issues combine here:

   - `_fenced_update()` checks owner and status, but not lease expiry or a unique claim generation.
   - All concurrent attempts in one daemon share `daemon_id`.
   - After reclamation and a new claim by the same daemon, an old attempt can match the new attempt’s owner and update its row.
   - RAM-disk publication occurs before checking `beat.lost`.
   - `complete_task()` returns a boolean, but its caller ignores it and unconditionally logs success, runs downstream DAG logic, and returns `"COMPLETED"`.

   A failed completion update does not itself mark the database row completed, but the caller still reports success and can perform downstream side effects.

   Assign each claim a unique token; require that token and an unexpired lease for heartbeat and terminal transitions. Check completion’s return value. Fence artifact publication too—database fencing alone cannot stop a stale worker overwriting files.

6. **High: artifact existence or mutation is weaker than task acceptance.**

   A newly created empty file passes because `sha256_pre is None`. An existing file with an added newline, corrupted content, or a destructive rewrite also passes.

   Conversely, a correct idempotent execution producing identical bytes fails.

   Define success according to the job: expected files, format/schema, required content, and relevant validation. Treat the hash as evidence about content, not as the success criterion itself.

7. **Additional bypass surface: path validation is inconsistent.**

   Layer 2 has no repository-containment check. Absolute paths and traversal can select unrelated files. Layer 1 validates the physical destination only inside the gathering branch and does not independently constrain the resolved RAM source to its workspace.

   Resolve and validate both source and destination before execution. Where concurrent actors can replace symlinks or Windows junctions, address those races as well; an early `resolve()` is not a lasting containment guarantee.

There is also a recovery defect: completion and DAG transitions use separate transactions. A failure after `complete_task()` can leave a completed parent without its children or a completed chapter without releasing synthesis. The exception handler then cannot requeue that completed row.

**The defensible claim today is:** “For a declared target, an unchanged or missing file is rejected, provided no other actor or daemon operation changes it.” That is useful protection, but substantially narrower than fail-closed execution.

Before declaring closure, test at least: omitted targets, concurrent jobs sharing a target, daemon-owned targets, stale/missing RAM artifacts, empty or invalid outputs, same-daemon lease reclamation, and a rejected completion update. Each must prevent false completion and unauthorized downstream advancement.

STDERR:
Reading prompt from stdin...
OpenAI Codex v0.160.0
--------
workdir: D:\__CoChem\__agentic\v4.2.0
model: gpt-6-astra
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: none
reasoning summaries: none
session id: 01a10e81-cf99-7560-b63e-2b126f943a6e
--------
user
You are GPT-6 Astra.
We are doing an out-of-band audit of the CoChem v4.2.0 pipeline.
A critical fail-open vulnerability was discovered: 'agy' blocked headless tool execution, exiting with code 0, and the daemon blindly marked jobs COMPLETED without checking if the file actually changed.
I have patched `worker_daemon.py` to mathematically verify physical file creation/modification (via sha256_pre vs sha256_post) before marking a job COMPLETED, and to raise RuntimeErrors on RAM disk sync failures.

Audit this patched code. Is the fail-open vulnerability definitively closed? Are there any remaining bypasses?

CODE:
"""Chapter 3 Two-Staged Concurrency Worker Daemon (SRS-412-03).

This module implements the complete, production-ready worker daemon coordinating
Layer 1 CLI-internal sub-agent swarms and Layer 2 supervised OS-level daemon queues
over an SQLite WAL task queue (job_board.db).

Key Architectural Invariants Enforced:
1. Layer 1 CLI-Level Swarms (SRS-412-03-FR-001):
   Executes through a single claude.exe hosting the Claude Fable 5.1 Director.
2. Layer 1 V8 Heap Memory Cap (SRS-412-03-FR-002):
   Director process enforces NODE_OPTIONS="--max-old-space-size=4096".
3. Sub-Agent Multiplexing (SRS-412-03-FR-003):
   Director multiplexes up to 20 concurrent Claude Opus 5.5 / Sonnet 5.5 sub-agents
   via SubAgentMultiplexer slot allocation and tracking.
4. Layer 2 OS-Level Supervised Queues (SRS-412-03-FR-004):
   Domain daemons coordinate exclusively through the SQLite WAL task queue.
5. Layer 2 Worker Memory Cap (SRS-412-03-FR-005):
   Worker CLI processes enforce NODE_OPTIONS="--max-old-space-size=512".
6. JIT Startup & Poll Loop Jitter (SRS-412-03-FR-006):
   Randomized jitter between 100ms and 500ms injected on every poll cycle.
7. Windows Process Creation Flags (SRS-412-03-FR-007):
   Enforces creationflags = 0x08000000 | 0x00000200 (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP).
8. Cognitive Complexity Score Routing (SRS-412-03-FR-008):
   Scores 1-4 -> Gemini Flash, 5-8 -> Sonnet 5.5, 9-10 -> Opus 5.5.
9. Desktop Heap Budget (NFR-CON-01):
   Probes and safeguards desktop heap allocation below 15% ceiling under 20 agents.
10. Zero Handle Leaks (NFR-CON-02):
    Continuous monitoring of OS process handle counts with zero leak tolerance.
11. Sub-Agent Latency Overhead (NFR-CON-03):
    Sub-agent communication overhead measured and kept under 10ms average.
12. Failure Recovery & Process Lifecycle:
    - V8 Heap Warning (SRS §8): Active DirectorHeapRecycleGuard triggers at >= 3800 MB,
      completing in-flight sub-agent tasks and spawning a fresh Director instance.
    - Subprocess Hang (SRS §8): 1740s timeout with continuous descendant tracking,
      terminating hung process trees cleanly before 1800s lease expires without orphan leaks.
13. Concurrency Profile Telemetry (SRS §7):
    Emits serialized concurrency_profile snapshots tracking active sub-agents and daemons.
14. Upstream Rate-Limit Pause (SRS-412-07 §8):
    A PAUSED task result re-queues the job with a retry_after_sec visibility delay and
    suspends claiming until the pause expires.

Physical, zero-mock execution. General systems software architecture.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import asdict, dataclass, field
import json
import logging
import logging.handlers
import math
import numbers
import os
from pathlib import Path
import random
import shutil
import signal
import socket
import sqlite3
from cochem.blackboard.schema import get_connection
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence

import psutil

# Add repository src to sys.path to guarantee cochem.concurrency imports
SRC_DIR = Path(__file__).resolve().parents[3] / "src"
if SRC_DIR.is_dir() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

# Import canonical Chapter 3 concurrency modules
try:
    from cochem.concurrency import layer1, layer2, runtime
except ImportError:
    # Fallback to local staging imports if executed inside staging package
    ch03_dir = Path(__file__).resolve().parents[1]
    if str(ch03_dir / "layer1") not in sys.path:
        sys.path.insert(0, str(ch03_dir / "layer1"))
        sys.path.insert(0, str(ch03_dir / "layer2"))
        sys.path.insert(0, str(ch03_dir / "runtime"))
    import layer1  # type: ignore
    import layer2  # type: ignore
    import runtime  # type: ignore

# FR-009/FR-010 gates; no staging fallback, so a missing guardrail fails the import instead of spawning unguarded.
from cochem.concurrency.context_guardrail import (
    ContextBatchLimitError,
    ContextWindowGuardrail,
    HardwareGuard,
    HardwareGuardTimeout,
)
from cochem.dsp.hardware_guard import compute_jittered_backoff

logger = logging.getLogger("cochem.concurrency.worker_daemon")

# ==============================================================================
# Architectural Constants (SRS-412-03)
# ==============================================================================
CREATE_NO_WINDOW: int = runtime.CREATE_NO_WINDOW
CREATE_NEW_PROCESS_GROUP: int = runtime.CREATE_NEW_PROCESS_GROUP
CREATIONFLAGS: int = runtime.CREATIONFLAGS
DEFAULT_CREATIONFLAGS: int = runtime.DEFAULT_CREATIONFLAGS

DEFAULT_CLAUDE_PATH: str = layer1.DEFAULT_CLAUDE_PATH
DIRECTOR_MODEL: str = layer1.DIRECTOR_MODEL
DIRECTOR_V8_HEAP_SIZE_MB: int = layer1.DIRECTOR_V8_HEAP_SIZE_MB
DIRECTOR_NODE_OPTIONS: str = layer1.DIRECTOR_NODE_OPTIONS
V8_HEAP_WARNING_THRESHOLD_MB: int = layer1.V8_HEAP_WARNING_THRESHOLD_MB

MAX_CONCURRENT_SUBAGENTS: int = layer1.MAX_CONCURRENT_SUBAGENTS
SUPPORTED_SUBAGENT_MODELS: tuple[str, ...] = layer1.SUPPORTED_SUBAGENT_MODELS
MAX_MESSAGE_OVERHEAD_MS: float = layer1.MAX_MESSAGE_OVERHEAD_MS

LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB: int = layer2.LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB
LAYER2_NODE_OPTIONS: str = layer2.LAYER2_NODE_OPTIONS
WORKER_RSS_CAP_MB: int = 512
EXIT_RSS_RECYCLE: int = 3

JIT_JITTER_MIN_MS: int = runtime.JIT_JITTER_MIN_MS
JIT_JITTER_MAX_MS: int = runtime.JIT_JITTER_MAX_MS
JIT_JITTER_RANGE_MS: tuple[int, int] = runtime.JIT_JITTER_RANGE_MS

LEASE_SEC: int = layer1.LEASE_TIMEOUT_SEC
# Longest a leased task waits for the FR-010 hardware guard before it is requeued; well inside the lease.
SPAWN_WAIT_SEC: float = 600.0
SUBPROCESS_TIMEOUT_SEC: int = layer1.SUBPROCESS_TIMEOUT_SEC
LEASE_TIMEOUT_SEC: int = layer1.LEASE_TIMEOUT_SEC
HEARTBEAT_SEC: float = 5.0
SQLITE_BUSY_TIMEOUT_MS: int = layer2.SQLITE_BUSY_TIMEOUT_MS
SQLITE_WAL_MODE: str = layer2.SQLITE_WAL_MODE

DESKTOP_HEAP_MAX_ALLOCATION_KB: int = layer2.DESKTOP_HEAP_MAX_ALLOCATION_KB
DESKTOP_HEAP_THRESHOLD_PERCENT: float = layer2.DESKTOP_HEAP_THRESHOLD_PERCENT

MODEL_TIER_FLASH: str = runtime.MODEL_TIER_FLASH
MODEL_TIER_SONNET: str = runtime.MODEL_TIER_SONNET
MODEL_TIER_OPUS: str = runtime.MODEL_TIER_OPUS
MODEL_TIER_FABLE: str = runtime.MODEL_TIER_FABLE
MODEL_TIER_GEMINI_PRO: str = runtime.MODEL_TIER_GEMINI_PRO
COMPLEXITY_ROUTING_MAP: dict[int, str] = runtime.COMPLEXITY_ROUTING_MAP

REPO_ROOT: Path = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) >= 4 else Path.cwd()
DEFAULT_DB_PATH: Path = REPO_ROOT / "job_board.db"
DEFAULT_STATE_DIR: Path = REPO_ROOT / ".evidence" / "concurrency_worker"

# Layer 2 workers run through the Antigravity CLI (agy), which serves Gemini Flash natively.
DEFAULT_AGY_PATH: str = shutil.which("agy") or str(Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe")
# ==============================================================================
# Re-exported Canonical Functions & Classes
# ==============================================================================
enforce_creationflags = runtime.enforce_creationflags
spawn_hidden_process = runtime.spawn_hidden_process
spawn_hidden_subprocess = runtime.spawn_hidden_subprocess
get_jit_startup_jitter_ms = runtime.get_jit_startup_jitter_ms
get_jit_startup_jitter_sec = runtime.get_jit_startup_jitter_sec
inject_jit_startup_jitter = runtime.inject_jit_startup_jitter
route_by_complexity = runtime.route_by_complexity
ComplexityRouter = runtime.ComplexityRouter
concurrency_profile = runtime.concurrency_profile
ConcurrencyProfile = runtime.ConcurrencyProfile

build_fable_director_command = layer1.build_fable_director_command
build_director_environment = layer1.build_director_environment
verify_director_v8_memory_cap = layer1.verify_director_v8_memory_cap
SubAgentSlot = layer1.SubAgentSlot
SubAgentMultiplexer = layer1.SubAgentMultiplexer
director_multiplexer = layer1.director_multiplexer
SubAgentMessageLatencyMeter = layer1.SubAgentMessageLatencyMeter
subagent_latency_meter = layer1.subagent_latency_meter
DirectorHeapRecycleGuard = layer1.DirectorHeapRecycleGuard
director_heap_guard = layer1.director_heap_guard
ProcessTreeTimeoutExpired = layer1.ProcessTreeTimeoutExpired
ProcessTreeTerminationError = layer1.ProcessTreeTerminationError
terminate_process_tree = layer1.terminate_process_tree
run_with_timeout = layer1.run_with_timeout
launch_layer1_fable_director = layer1.launch_layer1_fable_director

build_layer2_worker_env = layer2.build_layer2_worker_env
verify_layer2_worker_env = layer2.verify_layer2_worker_env
spawn_layer2_worker_process = layer2.spawn_layer2_worker_process
get_layer2_queue_connection = layer2.get_layer2_queue_connection
coordinate_layer2_daemons = layer2.coordinate_layer2_daemons
probe_desktop_heap_usage = layer2.probe_desktop_heap_usage
verify_desktop_heap_budget = layer2.verify_desktop_heap_budget
ProcessHandleLeakSampler = layer2.ProcessHandleLeakSampler
run_poll_loop_handle_probe = layer2.run_poll_loop_handle_probe
verify_poll_loop_zero_handle_leaks = layer2.verify_poll_loop_zero_handle_leaks


# ==============================================================================
# Layer 2 agy Worker Launcher (SRS-412-03-FR-005, FR-007, FR-008, SRS s8)
# ==============================================================================
AGY_MODEL_MAP = {
    "Gemini Flash": "gemini-3.8-flash-high",
    "Sonnet 5.5": "claude-sonnet-5-5-high",
    "Opus 5.5": "claude-opus-5-5-high",
    "Fable 5.1": "claude-fable-5-1",
    "Gemini Pro 3.1": "gemini-3.1-pro-high",
    "claude-5.5-haiku": "claude-sonnet-5-5-low",
}

def build_layer2_agy_command(
    prompt_path: Path | str,
    executable: str = DEFAULT_AGY_PATH,
    agent: str = "cochem-coder",
    model: str | None = None,
) -> list[str]:
    """Builds a headless agy command with tool permissions explicitly enabled."""
    if prompt_path is None or str(prompt_path).strip() in ("", "."):
        raise ValueError("prompt_path must not be empty")
    if not executable or not str(executable).strip():
        raise ValueError("executable must not be empty")
    cmd = [str(executable), "--agent", str(agent)]
    if model:
        cmd.extend(["--model", str(model)])
    cmd.extend(["-p", str(prompt_path), "--dangerously-skip-permissions"])
    return cmd


def launch_layer2_worker(
    prompt_path: Path | str,
    agent: str,
    model: str | None = None,
    executable: str = DEFAULT_AGY_PATH,
    timeout: float = SUBPROCESS_TIMEOUT_SEC,
    base_env: dict[str, str] | None = None,
    creationflags: int = runtime.DEFAULT_CREATIONFLAGS,
) -> subprocess.CompletedProcess[str]:
    """Runs one Layer 2 agy worker under the 512 MB V8 cap and the 1740 s process-tree timeout."""
    cmd = build_layer2_agy_command(prompt_path, executable=executable, agent=agent, model=model)
    if not Path(prompt_path).is_file():
        raise FileNotFoundError(f"Layer 2 prompt file not found: {prompt_path}")
    return run_with_timeout(
        cmd,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=build_layer2_worker_env(base_env),
        creationflags=enforce_creationflags(creationflags),
    )


# ==============================================================================
# Layer 2 SQLite WAL Queue Management & Lease Fencing (SRS-412-03-FR-004)
# ==============================================================================
def make_daemon_id(pid: int | None = None) -> str:
    """Constructs stable daemon identity: layer2-worker@<hostname>:<pid>."""
    return f"layer2-worker@{socket.gethostname()}:{os.getpid() if pid is None else pid}"


def connect_job_board(db_path: Path | str):
    return get_connection(db_path, timeout_sec=SQLITE_BUSY_TIMEOUT_MS / 1000.0)


def claim_next_task(db_path: Path, daemon_id: str, lease_sec: int = LEASE_SEC) -> dict[str, Any] | None:
    """Atomically claims highest priority ready PENDING task using BEGIN IMMEDIATE."""
    conn = connect_job_board(db_path)
    try:
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT id, task_id, job_type, payload_json, attempts, max_attempts
                FROM jobs
                WHERE status = 'PENDING' AND attempts < max_attempts
                  AND (not_before IS NULL OR not_before <= ?)
                ORDER BY priority DESC, created_at ASC, id ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None

            conn.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', lease_owner = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (daemon_id, now + lease_sec, now, row["id"]),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        task = dict(row)
        task["attempts"] += 1
        return task
    finally:
        conn.close()


def _fenced_update(db_path: Path, job_id: int, daemon_id: str, sql_set: str, params: tuple[Any, ...]) -> bool:
    """Fences status updates to ensure only the active lease owner can modify the task."""
    conn = connect_job_board(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"UPDATE jobs SET {sql_set} WHERE id = ? AND status = 'RUNNING' AND lease_owner = ?",
                (*params, job_id, daemon_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return cur.rowcount > 0
    finally:
        conn.close()


def heartbeat_lease(db_path: Path, job_id: int, daemon_id: str, lease_sec: int = LEASE_SEC) -> bool:
    """Refreshes lease expiration timestamp fenced on active ownership."""
    now = int(time.time())
    return _fenced_update(db_path, job_id, daemon_id, "lease_expires_at = ?, updated_at = ?", (now + lease_sec, now))


def complete_task(db_path: Path, job_id: int, daemon_id: str, result_path: str | None) -> bool:
    """Marks task COMPLETED and releases lease."""
    return _fenced_update(
        db_path, job_id, daemon_id,
        "status = 'COMPLETED', lease_owner = NULL, lease_expires_at = NULL, not_before = NULL, "
        "result_path = ?, error_log = NULL, updated_at = ?",
        (result_path, int(time.time())),
    )


def fail_task(db_path: Path, job_id: int, daemon_id: str, error_log: str) -> bool:
    """Marks non-retryable failure as FAILED (or BLOCKED if max attempts reached)."""
    return _fenced_update(
        db_path, job_id, daemon_id,
        "status = CASE WHEN attempts >= max_attempts THEN 'BLOCKED' ELSE 'FAILED' END, "
        "lease_owner = NULL, lease_expires_at = NULL, not_before = NULL, error_log = ?, updated_at = ?",
        (error_log, int(time.time())),
    )


def retry_or_block_task(db_path: Path, job_id: int, daemon_id: str, error_log: str, attempts: int) -> bool:
    """Requeues transient failure with jittered backoff or BLOCKED at max attempts."""
    now = int(time.time())
    delay = int(min(1800, 30 * (2 ** max(0, attempts - 1))))
    jittered_delay = delay // 2 + random.randint(0, delay // 2)
    return _fenced_update(
        db_path, job_id, daemon_id,
        "status = CASE WHEN attempts >= max_attempts THEN 'BLOCKED' ELSE 'PENDING' END, "
        "not_before = CASE WHEN attempts >= max_attempts THEN NULL ELSE ? END, "
        "lease_owner = NULL, lease_expires_at = NULL, error_log = ?, updated_at = ?",
        (now + jittered_delay, error_log, now),
    )


def defer_throttled_task(db_path: Path, job_id: int, daemon_id: str, error_log: str, delay_sec: float) -> bool:
    """Requeues a task the FR-010 hardware guard kept from spawning; the claim's attempt is refunded
    because no agent ever ran, so throttling alone can never push a task to BLOCKED."""
    now = int(time.time())
    return _fenced_update(
        db_path, job_id, daemon_id,
        "status = 'PENDING', attempts = MAX(0, attempts - 1), not_before = ?, "
        "lease_owner = NULL, lease_expires_at = NULL, error_log = ?, updated_at = ?",
        (now + max(1, int(delay_sec)), error_log, now),
    )


# SRS-412-07 §8: bounds on an upstream rate-limit pause; the floor still releases the claim loop for one cycle.
PAUSE_MIN_SEC: float = 1.0
PAUSE_MAX_SEC: float = 3600.0


def extract_pause(result_data: Mapping[str, Any]) -> float | None:
    """Returns the retry_after_sec of a SRS-412-07 §8 pause signalled by a task result, else None.

    A pause is signalled when ``result_data["status"] == "PAUSED"``, or when ``result_data["stdout"]`` is a str
    whose last non-empty line is a JSON object with ``"status": "PAUSED"`` (the Pedagogy Engine prints its result
    dict as its final line, so a Layer 2 agent hands the pause back through stdout). ``retry_after_sec`` must be a
    non-bool int/float, or a str parsing to one, that is finite and >= 0. Malformed input never raises.
    """
    candidates: list[Mapping[str, Any]] = [result_data]
    stdout = result_data.get("stdout")
    lines = [line.strip() for line in stdout.splitlines() if line.strip()] if isinstance(stdout, str) else []
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict):
            candidates.append(parsed)
    for candidate in candidates:
        value = candidate.get("retry_after_sec")
        if candidate.get("status") != "PAUSED" or isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            seconds = float(value.strip() if isinstance(value, str) else value)
        except (ValueError, OverflowError):
            continue
        if math.isfinite(seconds) and seconds >= 0.0:
            return seconds
    return None


def clamp_pause(seconds: float) -> float:
    """Clamps a pause into [PAUSE_MIN_SEC, PAUSE_MAX_SEC]."""
    return min(PAUSE_MAX_SEC, max(PAUSE_MIN_SEC, float(seconds)))


def reap_expired_leases(db_path: Path) -> int:
    """Reclaims expired leases or leases belonging to dead worker processes."""
    conn = connect_job_board(db_path)
    try:
        now = int(time.time())
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            logger.warning("Database locked during reap_expired_leases: %s", exc)
            return 0
            
        try:
            rows = conn.execute(
                "SELECT id, lease_owner, lease_expires_at FROM jobs WHERE status = 'RUNNING'"
            ).fetchall()
            reap_ids: list[int] = []
            prefix = f"layer2-worker@{socket.gethostname()}:"
            for r in rows:
                if r["lease_expires_at"] is None or r["lease_expires_at"] < now:
                    reap_ids.append(r["id"])
                elif r["lease_owner"] and r["lease_owner"].startswith(prefix):
                    owner_pid = r["lease_owner"][len(prefix):]
                    if owner_pid.isdigit() and int(owner_pid) != os.getpid() and not psutil.pid_exists(int(owner_pid)):
                        reap_ids.append(r["id"])

            cur = conn.executemany(
                """
                UPDATE jobs
                SET status = CASE WHEN attempts >= max_attempts THEN 'BLOCKED' ELSE 'PENDING' END,
                    error_log = SUBSTR(COALESCE(error_log || char(10), '') || 'Lease reclaimed at ' || ?, -8000),
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'RUNNING'
                """,
                [(now, now, job_id) for job_id in reap_ids],
            )
            conn.execute("COMMIT")
            return cur.rowcount
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


class LeaseHeartbeat(threading.Thread):
    """Background heartbeat thread refreshing active task lease every 5s."""

    def __init__(self, db_path: Path, job_id: int, daemon_id: str, on_beat: Any = None) -> None:
        super().__init__(name=f"lease-heartbeat-{job_id}", daemon=True)
        self.db_path = db_path
        self.job_id = job_id
        self.daemon_id = daemon_id
        self.on_beat = on_beat
        self.started_at = time.monotonic()
        self.lost = threading.Event()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=HEARTBEAT_SEC * 2)

    def run(self) -> None:
        while not self._stop_event.wait(HEARTBEAT_SEC):
            if time.monotonic() - self.started_at > SUBPROCESS_TIMEOUT_SEC:
                logger.error("Job %s exceeded timeout %ss; allowing lease to expire", self.job_id, SUBPROCESS_TIMEOUT_SEC)
                return
            try:
                if not heartbeat_lease(self.db_path, self.job_id, self.daemon_id):
                    logger.error("Lease on job %s was lost", self.job_id)
                    self.lost.set()
                    return
            except sqlite3.Error as exc:
                logger.warning("Heartbeat update for job %s encountered: %s", self.job_id, exc)
            if self.on_beat is not None:
                self.on_beat()


# ==============================================================================
# Worker Daemon Main Implementation
# ==============================================================================
class ConcurrencyWorkerDaemon:
    """Supervised Layer 2 worker daemon coordinating through SQLite WAL."""

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        state_dir: Path = DEFAULT_STATE_DIR,
        rss_cap_mb: int = WORKER_RSS_CAP_MB,
        multiplexer: SubAgentMultiplexer | None = None,
        heap_guard: DirectorHeapRecycleGuard | None = None,
        latency_meter: SubAgentMessageLatencyMeter | None = None,
        hardware_guard: HardwareGuard | None = None,
        context_guardrail: ContextWindowGuardrail | None = None,
        spawn_wait_sec: float = SPAWN_WAIT_SEC,
    ) -> None:
        self.db_path = Path(db_path)
        self.state_dir = Path(state_dir)
        self.results_dir = self.state_dir / "results"
        self.telemetry_dir = self.state_dir / "telemetry"
        self.rss_cap_mb = rss_cap_mb
        self.daemon_id = make_daemon_id()
        self.heartbeat_file = self.state_dir / f"worker_{os.getpid()}.json"
        self.profile_file = self.telemetry_dir / "concurrency_profile.json"
        self.stop_event = threading.Event()
        self.current_task: str | None = None
        self.current_task_started: float | None = None
        self.tasks_processed: int = 0
        self._last_reap: float = 0.0

        # Layer 1 Concurrency Invariants
        self.multiplexer: SubAgentMultiplexer = multiplexer if multiplexer is not None else director_multiplexer
        self.heap_guard: DirectorHeapRecycleGuard = heap_guard if heap_guard is not None else director_heap_guard
        self.latency_meter: SubAgentMessageLatencyMeter = latency_meter if latency_meter is not None else subagent_latency_meter

        # FR-010: one guard shared with the multiplexer, so the Director tree, this worker and the agy
        # workers it spawns all count against the 32 GB swarm budget. FR-009: pre-load token scan.
        self.hardware_guard: HardwareGuard = hardware_guard if hardware_guard is not None else self.multiplexer.hardware_guard
        self.hardware_guard.register_swarm_pid(os.getpid())
        self.context_guardrail: ContextWindowGuardrail = (
            context_guardrail if context_guardrail is not None else self.multiplexer.context_guardrail
        )
        self.spawn_wait_sec = float(spawn_wait_sec)
        self.last_hardware: dict[str, Any] | None = None
        self._throttle_attempt = 0
        # SRS-412-07 §8: time.monotonic() deadline before which no new task is claimed.
        self.queue_paused_until: float = 0.0

        # Layer 2 Concurrency Invariants
        self.handle_sampler = ProcessHandleLeakSampler()
        self.handle_sampler.sample()

    def write_heartbeat_file(self) -> None:
        """Emits worker heartbeat and liveness snapshot."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        desktop_heap_stat = probe_desktop_heap_usage(self.multiplexer.active_count)
        self.handle_sampler.sample()
        record = {
            "pid": os.getpid(),
            "daemon_id": self.daemon_id,
            "db_path": str(self.db_path),
            "timestamp": time.time(),
            "current_task": self.current_task,
            "current_task_started": self.current_task_started,
            "tasks_processed": self.tasks_processed,
            "rss_mb": psutil.Process().memory_info().rss / (1024 * 1024),
            "desktop_heap": desktop_heap_stat,
            "handle_leak_delta": self.handle_sampler.get_leak_delta(),
            "active_subagents": self.multiplexer.active_count,
            "hardware": self.last_hardware,
            "queue_paused_until": (time.time() + self.queue_paused_until - time.monotonic()
                                   if self.queue_paused_until > time.monotonic() else 0.0),
        }
        tmp = self.heartbeat_file.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        try:
            os.replace(tmp, self.heartbeat_file)
        except PermissionError:
            time.sleep(0.05)
            try:
                os.replace(tmp, self.heartbeat_file)
            except PermissionError:
                logger.warning("PermissionError while replacing heartbeat file")

    def emit_concurrency_profile(self) -> concurrency_profile:
        """Instantiates, emits, and serializes the Section 7 Concurrency Profile data model."""
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)

        # Query active Layer 2 daemons from job board
        active_daemons = 1
        try:
            telemetry = coordinate_layer2_daemons(self.db_path)
            active_daemons = int(telemetry.get("active_daemons", 1))
        except Exception as exc:
            logger.error("Failed to coordinate layer2 daemons telemetry: %s", exc)

        # Measure the live shared Director process; 0/0 when no Director is running (never this worker)
        director_pid, director_rss_mb = self.multiplexer.director_pid, 0
        if director_pid is not None:
            try:
                director_rss_mb = int(psutil.Process(director_pid).memory_info().rss // (1024 * 1024))
            except psutil.NoSuchProcess:
                director_pid = None

        profile = concurrency_profile(
            layer1_director_pid=director_pid or 0,
            active_subagents=self.multiplexer.active_count,
            v8_heap_allocated_mb=director_rss_mb,
            layer2_active_daemons=max(1, active_daemons),
            jit_jitter_range_ms=JIT_JITTER_RANGE_MS,
        )
        self.profile_file.write_text(profile.to_json(indent=2), encoding="utf-8")
        return profile

    def clean_heartbeat_file(self) -> None:
        """Removes ephemeral worker heartbeat file on clean shutdown."""
        try:
            self.heartbeat_file.unlink()
        except (FileNotFoundError, PermissionError):
            logger.debug("Failed to unlink heartbeat file")

    def check_rss_recycle(self) -> bool:
        """Checks if process resident set size exceeds the 512 MB worker cap."""
        rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        if rss_mb > self.rss_cap_mb:
            logger.warning("Worker RSS %.1f MB exceeds %d MB cap; initiating self-recycle", rss_mb, self.rss_cap_mb)
            return True
        return False

    def process_task(self, task: dict[str, Any]) -> str:
        """Dispatches leased task through Cognitive Complexity routing and Layer 1/2 execution."""
        task_id = task["task_id"]
        job_id = task["id"]
        attempts = task["attempts"]

        self.current_task = task_id
        self.current_task_started = time.time()
        self.write_heartbeat_file()

        beat = LeaseHeartbeat(self.db_path, job_id, self.daemon_id, on_beat=self.write_heartbeat_file)
        beat.start()

        started_at = time.time()
        slot: SubAgentSlot | None = None
        try:
            # Parse payload
            try:
                payload = json.loads(task["payload_json"])
                if not isinstance(payload, dict):
                    raise ValueError("payload_json must deserialize to a dictionary")
            except Exception as exc:
                beat.stop()
                fail_task(self.db_path, job_id, self.daemon_id, f"Invalid payload: {exc}")
                return "FAILED"

            # FR-015: Fail-Open Guard State
            target_file = payload.get("target_file")
            target_file_path = None
            sha256_pre = None
            if target_file:
                target_file_path = (REPO_ROOT / target_file).resolve()
                if target_file_path.exists():
                    import hashlib
                    sha256_pre = hashlib.sha256(target_file_path.read_bytes()).hexdigest()

            # Route by Cognitive Complexity Score (SRS-412-03-FR-008)
            score = payload.get("cognitive_complexity", payload.get("complexity_score", 5))
            fallback = payload.get("fallback_tier")
            if fallback:
                model_tier = fallback
                payload["use_director"] = False
                score = 5  # Force score down to bypass Director logic
                logger.info("Task %s fallback triggered: forcing tier %s", task_id, model_tier)
            else:
                try:
                    model_tier = route_by_complexity(score)
                except ValueError as exc:
                    logger.warning("Invalid complexity score %r: %s; falling back to score 5", score, exc)
                    score = 5
                    model_tier = route_by_complexity(score)

            payload["routed_model_tier"] = model_tier
            payload["complexity_score"] = score
            logger.info("Task %s (score %s) routed to tier: %s", task_id, score, model_tier)

            # FR-009: count the prompt and every file it references before any agent loads them;
            # an oversized task goes back to the WBS for fracturing instead of being retried.
            scan = self.context_guardrail.scan(task_id, payload)
            if scan.exceeds_boundary:
                beat.stop()
                if not self.context_guardrail.fracture(self.db_path, job_id, scan, payload, lease_owner=self.daemon_id):
                    return "LEASE_LOST"
                return "BLOCKED"
            logger.info("Task %s scanned at %d tokens (%s, batch limit %d)",
                        task_id, scan.total_tokens, scan.size_class, scan.batch_limit)

            prompt_file =self.state_dir / f"prompt_{task_id}.md"
            prompt_file.write_text(payload.get("prompt", f"# Task {task_id}"), encoding="utf-8")
            try:
                # Check if task requires Layer 1 Director execution
                if payload.get("use_director", False) or score >= 9:
                    subagent_model = layer1.claude_model_id(model_tier)
                    # Allocate slot on the SubAgentMultiplexer (SRS-412-03-FR-003)
                    # The multiplexer waits on the FR-010 hardware guard before claiming the slot.
                    slot = self.multiplexer.allocate_slot(task_id, subagent_model, spawn_wait_sec=self.spawn_wait_sec)
                    logger.info("Allocated multiplexer slot %d (%s) for task %s", slot.slot_id, slot.subagent_type, task_id)

                    # Check Director V8 Heap Warning (Failure Mode: V8 Heap Warning at 3800 MB)
                    director_pid = self.multiplexer.director_pid
                    if director_pid is not None and layer1.should_recycle_director(pid=director_pid):
                        logger.warning("Director pid %d approaching 3800 MB heap limit; recycling...", director_pid)
                        self.multiplexer.recycle_director()

                    # Run as a native sub-agent inside the shared Layer 1 Director process (SRS-412-03-FR-003)
                    res = self.multiplexer.dispatch(slot, payload.get("prompt", f"# Task {task_id}"))

                    # Gather sequence: sync the edited file from the RAM disk back to the physical source tree
                    
                    target_file = payload.get("target_file")

                    if target_file and not res.is_error:

                        if self.multiplexer._session is None or self.multiplexer._session.workspace is None:

                            raise RuntimeError("Gather sequence: _session or workspace is None!")

                        else:

                            ramdisk_file = self.multiplexer._session.workspace.work_dir / target_file

                            if ramdisk_file.exists():

                                physical_file = (REPO_ROOT / target_file).resolve()

                                if REPO_ROOT not in physical_file.parents and physical_file != REPO_ROOT:

                                    raise RuntimeError(f"Gather sequence: target_file {target_file} escapes REPO_ROOT")

                                else:

                                    physical_file.parent.mkdir(parents=True, exist_ok=True)

                                    shutil.copy2(ramdisk_file, physical_file)

                                    logger.info("Gather sequence: synced %s from RAM disk to %s", target_file, physical_file)

                            else:

                                logger.warning("Gather sequence: expected RAM disk file %s not found!", ramdisk_file)

                    logger.info("Task %s sub-agent round trip %.1f ms, IPC overhead %.3f ms",
                                task_id, res.latency_ms, res.ipc_overhead_ms)
                    if res.is_error:
                        raise RuntimeError(f"Director sub-agent turn failed: {res.text.strip()[-500:]}")

                    result_data = {
                        "task_id": task_id,
                        "model_tier": model_tier,
                        "mode": "layer1_director",
                        "slot_id": slot.slot_id,
                        "subagent_type": res.subagent_type,
                        "request_id": res.request_id,
                        "batch_id": res.batch_id,
                        "tool_use_id": res.tool_use_id,
                        "director_pid": res.director_pid,
                        "stdout": res.text,
                        "returncode": 0,
                    }
                else:
                    # FR-010: no agy spawn while CPU, RAM, swarm RSS or Disk I/O is over its ceiling.
                    snapshot = self.hardware_guard.wait_for_spawn_capacity(timeout_sec=self.spawn_wait_sec)
                    self.last_hardware = snapshot.to_dict()
                    # Map job_type to the correct Antigravity agent name
                    agent_mapping = {
                        'micro_code': 'cochem-coder',
                        'macro_audit': 'cochem-audit',
                        'research': 'researcher'
                    }
                    agent_name = agent_mapping.get(task.get('job_type'), 'cochem-coder')
                    
                    # Layer 2 agy worker under 512 MB V8 cap (SRS-412-03-FR-005)
                    if model_tier not in AGY_MODEL_MAP:
                        raise ValueError(f"Unmapped Layer 2 model tier: {model_tier}")
                    agy_model = AGY_MODEL_MAP[model_tier]
                    res = launch_layer2_worker(prompt_file, agent_name, model=agy_model)
                    if res.returncode != 0:
                        raise RuntimeError(f"agy worker exited {res.returncode}: {(res.stderr or '').strip()[-500:]}")
                    # agy can abort a headless turn yet report exit code zero.
                    # Reject this diagnostic even when no target_file was supplied.
                    if "jetski: no output produced" in (res.stderr or "").casefold():
                        raise RuntimeError("agy worker produced no output despite exit code 0: jetski: no output produced")
                    result_data = {
                        "task_id": task_id,
                        "model_tier": model_tier,
                        "mode": "layer2_worker",
                        "agy_agent": agent_name,
                        "agy_model": agy_model,
                        "v8_max_old_space_mb": LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB,
                        "stdout": res.stdout,
                        "returncode": res.returncode,
                    }
            finally:
                try:
                    prompt_file.unlink()
                except FileNotFoundError:
                    logger.debug("Prompt file %s already removed", prompt_file)

            beat.stop()
            if beat.lost.is_set():
                logger.error("Task %s completed after lease was reclaimed", task_id)
                return "LEASE_LOST"

            # Record result
            self.results_dir.mkdir(parents=True, exist_ok=True)
            result_file: Path = self.results_dir / f"{task_id}.json"
            record: dict[str, Any] = {
                "task_id": task_id,
                "daemon_id": self.daemon_id,
                "attempt": attempts,
                "started_at": started_at,
                "finished_at": time.time(),
                "result": result_data,
            }
            result_file.write_text(json.dumps(record, indent=2), encoding="utf-8")

            # SRS-412-07 §8: Upstream rate limit visibility deferral with jitter
            pause = extract_pause(result_data)
            if pause is not None:
                delay = clamp_pause(pause)
                jittered_pause: float = delay + compute_jittered_backoff(0, base_delay=0.1, max_delay=3.0)
                if pause > PAUSE_MAX_SEC:
                    logger.warning("Task %s pause %.0fs exceeds %.0fs ceiling; clamped", task_id, pause, PAUSE_MAX_SEC)
                if not defer_throttled_task(self.db_path, job_id, self.daemon_id,
                                            f"paused by upstream rate limit: retry after {jittered_pause:.1f}s", jittered_pause):
                    logger.error("Task %s paused after its lease was reclaimed", task_id)
                    return "LEASE_LOST"
                self.queue_paused_until = max(self.queue_paused_until, time.monotonic() + jittered_pause)
                logger.warning("Task %s PAUSED by rate limit; queue consumption paused %.1fs (not_before refreshed)",
                               task_id, jittered_pause)
                return "PAUSED"

            # FR-015: Fail-Open Guard Verification
            if target_file_path:
                if not target_file_path.exists():
                    raise RuntimeError(f"Fail-Open Guard: target_file {target_file} was not created!")
                import hashlib
                sha256_post = hashlib.sha256(target_file_path.read_bytes()).hexdigest()
                if sha256_pre is not None and sha256_pre == sha256_post:
                    raise RuntimeError(f"Fail-Open Guard: target_file {target_file} was not modified by the agent! It exited silently.")

            complete_task(self.db_path, job_id, self.daemon_id, str(result_file))
            logger.info("Task %s successfully COMPLETED", task_id)

            # Phase 2.2: Scatter Controller (MANIFEST)
            dag_node = payload.get("dag_node")
            if dag_node == "MANIFEST":
                import uuid
                chapter_count = payload.get("chapter_count", 6)
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("BEGIN IMMEDIATE")
                    # Insert Chapter Drafts
                    for i in range(chapter_count):
                        child_task_id = f"CHAP-{uuid.uuid4().hex[:8].upper()}"
                        child_payload = json.dumps({
                            "dag_node": "CHAPTER_DRAFT",
                            "parent_job_id": job_id,
                            "chapter_index": i + 1,
                            "prompt": f"Write chapter {i + 1}"
                        })
                        conn.execute(
                            "INSERT INTO jobs (task_id, job_type, priority, status, payload_json, parent_job_id) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (child_task_id, "micro_code", 100, "PENDING", child_payload, job_id)
                        )
                    # Insert Synthesis
                    synth_task_id = f"SYNTH-{uuid.uuid4().hex[:8].upper()}"
                    synth_payload = json.dumps({
                        "dag_node": "SYNTHESIS",
                        "parent_job_id": job_id,
                        "prompt": "Synthesize the chapters"
                    })
                    conn.execute(
                        "INSERT INTO jobs (task_id, job_type, priority, status, payload_json, parent_job_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (synth_task_id, "micro_code", 100, "BLOCKED", synth_payload, job_id)
                    )
                    conn.commit()
                logger.info("Task %s (MANIFEST) spawned %d CHAPTER_DRAFT tasks and 1 SYNTHESIS task", task_id, chapter_count)

            # Phase 2.3: Gather Barrier (CHAPTER_DRAFT)
            elif dag_node == "CHAPTER_DRAFT":
                parent_job_id = payload.get("parent_job_id")
                if parent_job_id:
                    with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("BEGIN IMMEDIATE")
                        # Check if any siblings are not completed
                        # Use like '%"dag_node": "CHAPTER_DRAFT"%' as fallback if json_extract is missing
                        pending = conn.execute(
                            "SELECT 1 FROM jobs WHERE parent_job_id = ? AND status != 'COMPLETED' AND payload_json LIKE '%\"dag_node\": \"CHAPTER_DRAFT\"%'",
                            (parent_job_id,)
                        ).fetchone()
                        if not pending:
                            conn.execute(
                                "UPDATE jobs SET status = 'PENDING' WHERE parent_job_id = ? AND status = 'BLOCKED' AND payload_json LIKE '%\"dag_node\": \"SYNTHESIS\"%'",
                                (parent_job_id,)
                            )
                            logger.info("All siblings completed. Unblocked SYNTHESIS for parent %s", parent_job_id)
                        conn.commit()

            return "COMPLETED"

        except (HardwareGuardTimeout, ContextBatchLimitError) as exc:
            beat.stop()
            logger.warning("Task %s deferred, no execution ran: %s", task_id, exc)
            jittered_throttle: float = compute_jittered_backoff(attempts, base_delay=5.0, max_delay=60.0)
            defer_throttled_task(self.db_path, job_id, self.daemon_id, f"spawn throttled: {exc}", jittered_throttle)
            return "PENDING"
        except Exception as exc:
            beat.stop()
            err_msg: str = str(exc)
            is_quota_exhausted: bool = any(
                token in err_msg.lower() for token in (
                    "credit balance is too low", "429", "resource_exhausted", "rate_limit_exceeded",
                    "monthly spend limit", "spend limit", "quota",
                )
            )

            if is_quota_exhausted:
                pivot_count: int = int(payload.get("pivot_count", 0)) + 1
                payload["pivot_count"] = pivot_count
                max_pivot_cycles: int = 3

                if pivot_count > max_pivot_cycles:
                    logger.critical("Task %s exhausted %d pivot cycles. Triggering HARD_ABORT.", task_id, max_pivot_cycles)
                    fail_task(self.db_path, job_id, self.daemon_id, f"[HARD_ABORT: ARCHITECTURE WALL] Pivot ceiling reached: {exc}")
                    return "BLOCKED"

                now_ts: int = int(time.time())

                if model_tier in (MODEL_TIER_OPUS, MODEL_TIER_SONNET, MODEL_TIER_FABLE, "gpt-6-astra"):
                    payload["fallback_tier"] = (
                        MODEL_TIER_GEMINI_PRO if model_tier == "gpt-6-astra" else "gpt-6-astra"
                    )
                    logger.warning("Quota exhausted on %s for task %s (pivot %d/%d). Falling back to %s for immediate retry.",
                                   model_tier, task_id, pivot_count, max_pivot_cycles, payload["fallback_tier"])

                    jittered_sec: float = compute_jittered_backoff(0, base_delay=1.0, max_delay=1.0)
                    fenced = _fenced_update(
                        self.db_path,
                        job_id,
                        self.daemon_id,
                        "status = 'PENDING', attempts = MAX(0, attempts - 1), payload_json = ?, not_before = ?, "
                        "lease_owner = NULL, lease_expires_at = NULL, error_log = ?, updated_at = ?",
                        (
                            json.dumps(payload),
                            now_ts + max(1, int(jittered_sec)),
                            f"Quota exhausted; fallback to {payload['fallback_tier']} (pivot {pivot_count})",
                            now_ts,
                        ),
                    )
                    if not fenced:
                        logger.error("Task %s lease lost during fallback deferral", task_id)
                        return "LEASE_LOST"
                    return "PENDING"
                elif model_tier == MODEL_TIER_GEMINI_PRO:
                    logger.warning("Gemini Pro fallback exhausted quota on task %s (pivot %d/%d). Reverting to Claude with backoff.",
                                   task_id, pivot_count, max_pivot_cycles)
                    payload.pop("fallback_tier", None)
                    jittered_sec = compute_jittered_backoff(pivot_count, base_delay=300.0, max_delay=900.0)
                    fenced = _fenced_update(
                        self.db_path,
                        job_id,
                        self.daemon_id,
                        "status = 'PENDING', attempts = MAX(0, attempts - 1), payload_json = ?, not_before = ?, "
                        "lease_owner = NULL, lease_expires_at = NULL, error_log = ?, updated_at = ?",
                        (
                            json.dumps(payload),
                            now_ts + max(1, int(jittered_sec)),
                            f"Dual provider exhaustion; cooling down {jittered_sec:.1f}s (pivot {pivot_count})",
                            now_ts,
                        ),
                    )
                    if not fenced:
                        logger.error("Task %s lease lost during dual exhaustion deferral", task_id)
                        return "LEASE_LOST"
                    return "PENDING"

            logger.exception("Task %s execution raised: %s", task_id, exc)
            retry_or_block_task(self.db_path, job_id, self.daemon_id, str(exc), attempts)
            max_attempts_limit: int = int(task.get("max_attempts", 3))
            return "BLOCKED" if attempts >= max_attempts_limit else "PENDING"
        finally:
            beat.stop()
            if slot is not None:
                # By object: dispatch already freed it on failure, and its id may now belong to another task.
                self.multiplexer.release_slot(slot)
            self.current_task = None
            self.current_task_started = None
            self.tasks_processed += 1
            self.handle_sampler.sample()
            self.emit_concurrency_profile()
            self.write_heartbeat_file()

    def run(self, max_tasks: int | None = None, exit_when_idle: bool = False) -> int:
        """Executes daemon continuous poll loop with JIT jitter on every cycle."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)

        if not self.db_path.is_file():
            logger.error("SQLite WAL database not found at: %s", self.db_path)
            return 2

        logger.info("Starting Concurrency Worker Daemon %s on %s", self.daemon_id, self.db_path)
        exit_code = 0
        futures = set()
        dispatch_times = []
        spawn_window = 3.0
        spawn_burst = 3
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SUBAGENTS)

        try:
            while not self.stop_event.is_set():
                self.write_heartbeat_file()
                self.emit_concurrency_profile()

                # Periodic lease reaper
                if time.monotonic() - self._last_reap >= 30.0:
                    self._last_reap = time.monotonic()
                    reaped = reap_expired_leases(self.db_path)
                    if reaped > 0:
                        logger.info("Reaped %d expired/abandoned task lease(s)", reaped)

                # Clean up finished futures
                done = {fut for fut in futures if fut.done()}
                futures -= done
                if max_tasks is not None and self.tasks_processed >= max_tasks and not futures:
                    logger.info("Processed %d tasks (max_tasks reached), exiting", self.tasks_processed)
                    break

                # FR-009: Check dynamic context batch limit
                if len(futures) >= min(MAX_CONCURRENT_SUBAGENTS, self.context_guardrail.current_batch_limit):
                    jitter_delay: float = compute_jittered_backoff(0, base_delay=1.0, max_delay=3.0)
                    self.stop_event.wait(jitter_delay)
                    continue

                # FR-010: claim nothing while the hardware guard has spawning paused.
                snapshot = self.hardware_guard.poll()
                self.last_hardware = snapshot.to_dict()
                if not snapshot.spawn_allowed:
                    delay = compute_jittered_backoff(self._throttle_attempt, base_delay=0.5, max_delay=10.0)
                    self._throttle_attempt += 1
                    logger.info("Spawning paused (%s); next claim attempt in %.2f s",
                                "; ".join(snapshot.violations) or "no RAM headroom", delay)
                    self.stop_event.wait(delay)
                    continue
                self._throttle_attempt = 0

                # SRS-412-07 §8: claim nothing until an upstream rate-limit pause expires.
                remaining = self.queue_paused_until - time.monotonic()
                if remaining > 0.0:
                    wait = min(remaining, 10.0)
                    logger.info("Queue consumption paused by upstream rate limit; next claim attempt in %.1f s "
                                "(%.0f s remain)", wait, remaining)
                    self.stop_event.wait(wait)
                    continue

                # Admit three dispatches per rolling window, before claiming a lease.
                # Short waits keep heartbeat, reaping and completion handling responsive.
                now = time.monotonic()
                dispatch_times = [stamp for stamp in dispatch_times
                                  if now - stamp < spawn_window]
                if len(dispatch_times) >= spawn_burst:
                    self.stop_event.wait(min(0.1, spawn_window - (now - dispatch_times[0])))
                    continue

                try:
                    task = claim_next_task(self.db_path, self.daemon_id)
                except Exception as exc:
                    logger.warning("Database busy during claim: %s; retrying", exc)
                    jitter_delay: float = compute_jittered_backoff(0, base_delay=0.5, max_delay=2.0)
                    self.stop_event.wait(jitter_delay)
                    continue

                if task is None:
                    if exit_when_idle and not futures:
                        logger.info("Queue empty and exit_when_idle set; terminating daemon")
                        break
                    if self.check_rss_recycle() and not futures:
                        exit_code = 3
                        break
                    jitter_delay: float = compute_jittered_backoff(0, base_delay=1.0, max_delay=5.0)
                    self.stop_event.wait(jitter_delay)
                    continue

                fut = executor.submit(self.process_task, task)
                futures.add(fut)
                dispatch_times.append(time.monotonic())
        except Exception as exc:
            logger.exception("Concurrency Worker Daemon loop encountered a fatal error: %s", exc)
            exit_code = 1
        finally:
            executor.shutdown(wait=True)
            # The worker_<pid>.json record is the watchdog's Matrix 1 input: a record left behind after exit reads as a
            # dead worker holding its last task (2026-10-03 bundle 20261003T203006Z); HEAD cleaned it here before the
            # ThreadPoolExecutor rewrite dropped the call.
            self.clean_heartbeat_file()
            self.stop_event.set()
        return exit_code


def configure_logging(verbose: bool = False) -> None:
    """Configures stream logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(process)d] %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for Chapter 3 Concurrency Worker Daemon."""
    parser = argparse.ArgumentParser(description="Chapter 3 Concurrency Worker Daemon (SRS-412-03)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to SQLite job_board.db")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="State directory")
    parser.add_argument("--max-tasks", type=int, default=None, help="Maximum tasks to process")
    parser.add_argument("--exit-when-idle", action="store_true", help="Exit when queue is empty")
    parser.add_argument("--rss-cap-mb", type=int, default=WORKER_RSS_CAP_MB, help="Worker RSS memory cap in MB")
    parser.add_argument("--profile", action="store_true", help="Print current concurrency profile JSON")
    parser.add_argument("--probe-heap", action="store_true", help="Probe Desktop Heap usage budget")
    parser.add_argument("--probe-handles", action="store_true", help="Probe handle leak status")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.profile:
        prof = concurrency_profile(
            layer1_director_pid=os.getpid(),
            active_subagents=0,
            v8_heap_allocated_mb=int(psutil.Process().memory_info().rss // (1024 * 1024)),
            layer2_active_daemons=1,
        )
        print(prof.to_json(indent=2))
        return 0

    if args.probe_heap:
        result = probe_desktop_heap_usage()
        print(json.dumps(result, indent=2))
        return 0

    if args.probe_handles:
        sampler = ProcessHandleLeakSampler()
        sampler.sample()
        print(json.dumps({"baseline_handles": sampler.baseline_handles, "leak_delta": sampler.get_leak_delta()}, indent=2))
        return 0

    daemon = ConcurrencyWorkerDaemon(
        db_path=args.db,
        state_dir=args.state_dir,
        rss_cap_mb=args.rss_cap_mb,
    )

    def handle_signal(sig: int, frame: Any) -> None:
        logger.info("Signal %s received; stopping worker daemon...", sig)
        daemon.stop_event.set()

    for signame in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, signame):
            signal.signal(getattr(signal, signame), handle_signal)

    return daemon.run(max_tasks=args.max_tasks, exit_when_idle=args.exit_when_idle)


if __name__ == "__main__":
    sys.exit(main())


codex
**No. The patch closes one narrow failure case, but the fail-open vulnerability is not definitively closed.** It rejects an unchanged or missing declared target after an otherwise successful execution. It does **not** establish that this job produced the required artifact.

This assessment is based on the supplied code; I have not executed it or inspected the imported components.

1. **Critical: omitting `target_file` bypasses verification entirely.**

   Both initialization and verification are conditional:

   ```python
   if target_file:
       ...
   if target_file_path:
       ...
   ```

   A missing, `null`, or empty `target_file` permits completion based on exit code zero, unless stderr contains the exact recognized diagnostic.

   **This is already reachable through your own pipeline:** the generated `CHAPTER_DRAFT` and `SYNTHESIS` payloads contain no `target_file`. Those jobs can still silently do nothing and become `COMPLETED`.

   Require a validated output contract for every job type. For intentionally non-file-producing jobs, use a different explicit, independently checked success condition.

2. **High: a changed hash does not prove this job changed the file.**

   There is no exclusive ownership of a target across jobs. For example:

   - Jobs A and B both snapshot target hash `H0`.
   - A successfully writes content with hash `H1`.
   - B performs no work and exits zero.
   - B observes `H1 != H0` and becomes `COMPLETED`.

   The same false attribution can come from another process or a human edit. Hash comparison establishes different observed bytes, not authorship or successful execution.

   Use isolated, per-attempt output locations and controlled publication. Serialize publication to shared targets and check the expected destination version before replacing it.

3. **High: the daemon’s own writes can satisfy the guard.**

   There is no restriction preventing `target_file` from naming a daemon-owned file. The daemon writes the result JSON **before** performing verification:

   ```python
   result_file.write_text(...)
   # Then hash target_file_path
   ```

   If the target names that result file, the daemon itself creates or modifies the target even when the worker does nothing. Heartbeat and telemetry files provide other potential cases.

   Validate canonical target paths against approved artifact locations, excluding state, telemetry, results, prompts, queue files, and other control files.

4. **High: RAM-disk gathering can publish stale content, and missing source files still only warn.**

   The missing-source branch remains:

   ```python
   logger.warning("Gather sequence: expected RAM disk file %s not found!", ramdisk_file)
   ```

   It does **not** raise. Usually the final hash check then rejects the unchanged destination, but an independent modification can make it pass.

   More directly, if the RAM workspace contains stale content different from the initial physical file, a no-op Director response can cause `copy2()` to publish that stale content. The hash check then passes despite this attempt producing nothing.

   Require attempt-specific source artifacts, verify their provenance and acceptance conditions, and raise immediately when a required source is missing. Successful copying alone does not establish that the source is the correct output.

5. **High: lease fencing does not reliably fence execution or publication.**

   Several issues combine here:

   - `_fenced_update()` checks owner and status, but not lease expiry or a unique claim generation.
   - All concurrent attempts in one daemon share `daemon_id`.
   - After reclamation and a new claim by the same daemon, an old attempt can match the new attempt’s owner and update its row.
   - RAM-disk publication occurs before checking `beat.lost`.
   - `complete_task()` returns a boolean, but its caller ignores it and unconditionally logs success, runs downstream DAG logic, and returns `"COMPLETED"`.

   A failed completion update does not itself mark the database row completed, but the caller still reports success and can perform downstream side effects.

   Assign each claim a unique token; require that token and an unexpired lease for heartbeat and terminal transitions. Check completion’s return value. Fence artifact publication too—database fencing alone cannot stop a stale worker overwriting files.

6. **High: artifact existence or mutation is weaker than task acceptance.**

   A newly created empty file passes because `sha256_pre is None`. An existing file with an added newline, corrupted content, or a destructive rewrite also passes.

   Conversely, a correct idempotent execution producing identical bytes fails.

   Define success according to the job: expected files, format/schema, required content, and relevant validation. Treat the hash as evidence about content, not as the success criterion itself.

7. **Additional bypass surface: path validation is inconsistent.**

   Layer 2 has no repository-containment check. Absolute paths and traversal can select unrelated files. Layer 1 validates the physical destination only inside the gathering branch and does not independently constrain the resolved RAM source to its workspace.

   Resolve and validate both source and destination before execution. Where concurrent actors can replace symlinks or Windows junctions, address those races as well; an early `resolve()` is not a lasting containment guarantee.

There is also a recovery defect: completion and DAG transitions use separate transactions. A failure after `complete_task()` can leave a completed parent without its children or a completed chapter without releasing synthesis. The exception handler then cannot requeue that completed row.

**The defensible claim today is:** “For a declared target, an unchanged or missing file is rejected, provided no other actor or daemon operation changes it.” That is useful protection, but substantially narrower than fail-closed execution.

Before declaring closure, test at least: omitted targets, concurrent jobs sharing a target, daemon-owned targets, stale/missing RAM artifacts, empty or invalid outputs, same-daemon lease reclamation, and a rejected completion update. Each must prevent false completion and unauthorized downstream advancement.
tokens used
16,031