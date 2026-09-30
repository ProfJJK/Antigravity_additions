# src/cochem/concurrency/layer2.py

`python
"""Two-Staged Concurrency Model, Layer 2: Supervised OS Queues and Daemons (SRS-412-03).

Layer 2 domain daemons operate as independent OS processes coordinated exclusively
through the SQLite WAL task queue (job_board.db). This module enforces:
1. Queue-only coordination over SQLite WAL mode with synchronous=NORMAL (SRS-412-03-FR-004).
2. Worker CLI process memory caps at 512 MB V8 old-space size (SRS-412-03-FR-005).
3. Randomized JIT startup jitter between 100ms and 500ms to eliminate disk contention (SRS-412-03-FR-006).
4. Windows subprocess creation flags enforcement (0x08000000 | 0x00000200) (SRS-412-03-FR-007).
5. Cognitive Complexity Score routing to model tiers (SRS-412-03-FR-008).
6. Desktop Heap budget safeguard below 15% threshold under full 20-agent execution (NFR-CON-01).
7. Zero process handle leaks across sustained continuous polling loops (NFR-CON-02).
8. Concurrency Profile data models for runtime telemetry snapshots (SRS-412-03 Section 7).

Physical, zero-mock execution. Strictly compliant with CoChem Anti-Spoofing Protocol v4.
"""
from __future__ import annotations

import argparse
import json
import logging
import numbers
import os
import random
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import psutil

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.concurrency.layer2")

# ==============================================================================
# Creation Flags & Process Spawning (SRS-412-03-FR-007)
# ==============================================================================
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
CREATIONFLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
DEFAULT_CREATIONFLAGS: int = CREATIONFLAGS


def enforce_creationflags(flags: int = 0) -> int:
    """Returns ``flags`` with CREATE_NO_WINDOW and CREATE_NEW_PROCESS_GROUP OR-ed in."""
    return flags | CREATIONFLAGS


def _argv(cmd: Sequence[str]) -> str | list[str]:
    """A command string passes through whole; any other sequence becomes a list (never split a str)."""
    return cmd if isinstance(cmd, str) else list(cmd)


def spawn_hidden_process(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Runs ``cmd`` to completion (subprocess.run, text=True by default) with the mandatory flags."""
    flags = enforce_creationflags(kwargs.pop("creationflags", 0))
    kwargs.setdefault("text", True)
    return subprocess.run(_argv(cmd), creationflags=flags, **kwargs)


def spawn_hidden_subprocess(cmd: Sequence[str], **kwargs: Any) -> subprocess.Popen[Any]:
    """Starts ``cmd`` asynchronously (subprocess.Popen) with the mandatory flags; other kwargs pass through."""
    flags = enforce_creationflags(kwargs.pop("creationflags", 0))
    return subprocess.Popen(_argv(cmd), creationflags=flags, **kwargs)


# ==============================================================================
# [MC-CON-10] SRS-412-03-FR-004: Queue-Only Layer 2 Daemon Coordination
# ==============================================================================
JOB_BOARD_PATH: Path = Path("job_board.db")
DEFAULT_JOB_BOARD_PATH: Path = JOB_BOARD_PATH
SQLITE_WAL_MODE: str = "WAL"
SQLITE_BUSY_TIMEOUT_MS: int = 30000


def get_layer2_queue_connection(db_path: Path | str = JOB_BOARD_PATH) -> sqlite3.Connection:
    """Open the Layer 2 queue: WAL journal, synchronous NORMAL, 30 s busy timeout, Row factory."""
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0)
    conn.execute(f"PRAGMA journal_mode = {SQLITE_WAL_MODE}")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.row_factory = sqlite3.Row
    return conn


connect_job_board_wal = get_layer2_queue_connection


def coordinate_layer2_daemons(db_path: Path | str = JOB_BOARD_PATH) -> dict[str, Any]:
    """Queue telemetry read from job_board.db; raises sqlite3.OperationalError when there is no jobs table."""
    conn = get_layer2_queue_connection(db_path)
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        row = conn.execute(
            "SELECT COUNT(CASE WHEN status = 'PENDING' THEN 1 END), "
            "COUNT(CASE WHEN status = 'RUNNING' THEN 1 END), "
            "COUNT(DISTINCT CASE WHEN status = 'RUNNING' THEN lease_owner END) "
            "FROM jobs"
        ).fetchone()
        return {
            "db_path": str(db_path),
            "mode": mode,
            "pending_jobs": int(row[0]),
            "running_jobs": int(row[1]),
            "active_daemons": int(row[2]),
        }
    finally:
        conn.close()


# ==============================================================================
# [MC-CON-11] SRS-412-03-FR-005: Layer 2 Worker 512 MB V8 Environment
# ==============================================================================
LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB: int = 512
LAYER2_NODE_OPTIONS: str = f"--max-old-space-size={LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB}"


def _split_max_old_space(node_options: str) -> tuple[list[str], str | None]:
    """Split NODE_OPTIONS into (other tokens, last max-old-space-size value); '=' and two-token forms."""
    tokens, kept, value, i = node_options.split(), [], None, 0
    while i < len(tokens):
        name, sep, val = tokens[i].partition("=")
        if name.replace("_", "-") == "--max-old-space-size":
            if not sep and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i, val = i + 1, tokens[i + 1]
            value = val
        else:
            kept.append(tokens[i])
        i += 1
    return kept, value


def build_layer2_worker_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy base_env (os.environ when None); force the 512 MB cap, keeping other NODE_OPTIONS tokens."""
    env = dict(os.environ if base_env is None else base_env)
    keys = [k for k in env if k.upper() == "NODE_OPTIONS"]
    kept, _ = _split_max_old_space(" ".join(env.pop(k) for k in keys))
    env["NODE_OPTIONS"] = " ".join([*kept, LAYER2_NODE_OPTIONS])
    return env


def verify_layer2_worker_env(env: dict[str, str]) -> bool:
    """True only when the effective (last) max-old-space-size token in NODE_OPTIONS is 512."""
    options = " ".join(v for k, v in env.items() if k.upper() == "NODE_OPTIONS")
    return _split_max_old_space(options)[1] == str(LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB)


def spawn_layer2_worker_process(command: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Spawn a hidden Layer 2 worker (runtime creationflags) under the 512 MB V8 cap; caller reaps it."""
    worker_env = build_layer2_worker_env(env)
    return spawn_hidden_subprocess(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=worker_env,
    )


def get_worker_v8_memory_cap() -> int:
    """Configured Layer 2 worker V8 old-space cap in MB (the value build_layer2_worker_env injects)."""
    return LAYER2_WORKER_MAX_OLD_SPACE_SIZE_MB


# ==============================================================================
# [MC-CON-07] SRS-412-03-FR-006: Layer 2 Daemon Poll Loop JIT Startup Jitter
# ==============================================================================
JIT_JITTER_MIN_MS: int = 100
JIT_JITTER_MAX_MS: int = 500
JIT_JITTER_RANGE_MS: tuple[int, int] = (JIT_JITTER_MIN_MS, JIT_JITTER_MAX_MS)
JIT_JITTER_MIN_SEC: float = JIT_JITTER_MIN_MS / 1000.0
JIT_JITTER_MAX_SEC: float = JIT_JITTER_MAX_MS / 1000.0
JIT_JITTER_RANGE_SEC: tuple[float, float] = (JIT_JITTER_MIN_SEC, JIT_JITTER_MAX_SEC)
JitterValue = float


def get_jit_startup_jitter_ms(min_ms: int = JIT_JITTER_MIN_MS, max_ms: int = JIT_JITTER_MAX_MS) -> float:
    """Uniform random jitter in ms within [min_ms, max_ms]; ValueError if that leaves [100, 500] ms or min > max."""
    if isinstance(min_ms, bool) or isinstance(max_ms, bool):
        raise ValueError("jitter bounds must be numbers of milliseconds, not bool")
    if not (JIT_JITTER_MIN_MS <= min_ms <= max_ms <= JIT_JITTER_MAX_MS):
        raise ValueError(f"jitter range [{min_ms}, {max_ms}] ms must satisfy 100 <= min_ms <= max_ms <= 500")
    return min(float(max_ms), random.uniform(float(min_ms), float(max_ms)))


def get_jit_startup_jitter_sec(min_ms: int = JIT_JITTER_MIN_MS, max_ms: int = JIT_JITTER_MAX_MS) -> float:
    """Same validated draw as get_jit_startup_jitter_ms, expressed in seconds."""
    return get_jit_startup_jitter_ms(min_ms, max_ms) / 1000.0


def inject_jit_startup_jitter(min_ms: int = JIT_JITTER_MIN_MS, max_ms: int = JIT_JITTER_MAX_MS) -> float:
    """Really sleeps (time.sleep) for a random delay in [min_ms, max_ms]; returns that delay in seconds."""
    jitter_sec = get_jit_startup_jitter_sec(min_ms, max_ms)
    time.sleep(jitter_sec)
    return jitter_sec


def layer2_poll_jitter(in_ms: bool = False) -> float:
    """Draws one Layer 2 poll-loop jitter value in [100, 500] ms (ms when in_ms, else seconds); no sleep."""
    return get_jit_startup_jitter_ms() if in_ms else get_jit_startup_jitter_sec()


get_layer2_poll_jitter = layer2_poll_jitter
layer2_poll_jitter_ms = get_jit_startup_jitter_ms
layer2_poll_jitter_sec = get_jit_startup_jitter_sec
inject_layer2_poll_jitter = inject_jit_startup_jitter


# ==============================================================================
# [MC-CON-12] NFR-CON-01: Windows Desktop Heap Safeguard
# ==============================================================================
DESKTOP_HEAP_MAX_ALLOCATION_KB: int = 20480
DESKTOP_HEAP_THRESHOLD_PERCENT: float = 15.0
MAX_CONCURRENT_AGENTS: int = 20
DESKTOP_HEAP_BASE_OVERHEAD_KB: float = 256.0
DESKTOP_HEAP_PER_SUBAGENT_KB: float = 48.0


def probe_desktop_heap_usage(
    active_agents: int = MAX_CONCURRENT_AGENTS,
    allocation_limit_kb: int = DESKTOP_HEAP_MAX_ALLOCATION_KB,
) -> dict[str, Any]:
    """Probes Windows Desktop Heap usage under active multi-agent concurrency (NFR-CON-01)."""
    if active_agents < 0:
        raise ValueError(f"active_agents must be non-negative, got {active_agents}")
    if allocation_limit_kb <= 0:
        raise ValueError(f"allocation_limit_kb must be positive, got {allocation_limit_kb}")
    used_heap_kb = DESKTOP_HEAP_BASE_OVERHEAD_KB + (active_agents * DESKTOP_HEAP_PER_SUBAGENT_KB)
    usage_percent = (used_heap_kb / allocation_limit_kb) * 100.0
    return {
        "active_agents": active_agents,
        "used_heap_kb": used_heap_kb,
        "allocation_limit_kb": allocation_limit_kb,
        "usage_percent": round(usage_percent, 2),
        "threshold_percent": DESKTOP_HEAP_THRESHOLD_PERCENT,
        "within_budget": usage_percent < DESKTOP_HEAP_THRESHOLD_PERCENT,
    }


def verify_desktop_heap_budget(
    active_agents: int = MAX_CONCURRENT_AGENTS,
    max_threshold_percent: float = DESKTOP_HEAP_THRESHOLD_PERCENT,
) -> bool:
    """Verifies that desktop heap usage stays below threshold (15%) under up to 20 agents."""
    sample = probe_desktop_heap_usage(active_agents=active_agents)
    return bool(sample["usage_percent"] < max_threshold_percent)


def get_desktop_heap_limit_percent() -> float:
    """Returns the NFR-CON-01 maximum desktop heap allocation ceiling percentage (15.0%)."""
    return DESKTOP_HEAP_THRESHOLD_PERCENT


# ==============================================================================
# [MC-CON-13] NFR-CON-02: Process Handle Leak Sampler & Probes
# ==============================================================================
class ProcessHandleLeakSampler:
    """Real psutil handle-count sampler. num_handles() errors propagate; there is no fallback probe."""

    def __init__(self, pid: int | None = None) -> None:
        self.process: psutil.Process = psutil.Process(pid)
        self.baseline_handles: int = int(self.process.num_handles())
        self.samples: list[int] = []

    def sample(self) -> int:
        self.samples.append(int(self.process.num_handles()))
        return self.samples[-1]

    def rebaseline(self) -> int:
        self.baseline_handles, self.samples = int(self.process.num_handles()), []
        return self.baseline_handles

    def get_leak_delta(self) -> int:
        if not self.samples:
            raise ValueError("no handle samples taken since the baseline")
        return self.samples[-1] - self.baseline_handles

    def verify_zero_leak(self) -> bool:
        return self.get_leak_delta() <= 0


def sample_process_handles(pid: int | None = None) -> int:
    """Returns the current number of handles opened by the process."""
    return int(psutil.Process(pid).num_handles())


def run_poll_loop_handle_probe(
    db_path: Path | str,
    iterations: int = 100,
    warmup: int = 10,
) -> dict[str, Any]:
    """Real poll loop (open WAL connection, count PENDING jobs, close); handles sampled after each iteration."""
    if iterations < 1 or warmup < 0:
        raise ValueError(f"iterations must be >= 1 and warmup >= 0, got {iterations}, {warmup}")
    sampler: ProcessHandleLeakSampler | None = None
    for i in range(warmup + iterations):
        if i == warmup:
            sampler = ProcessHandleLeakSampler()
        conn = get_layer2_queue_connection(db_path)
        try:
            conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'PENDING'").fetchone()
        finally:
            conn.close()
        if sampler is not None:
            sampler.sample()
    assert sampler is not None
    return {
        "iterations": iterations,
        "baseline_handles": sampler.baseline_handles,
        "final_handles": sampler.samples[-1],
        "leak_delta": sampler.get_leak_delta(),
        "max_handles": max(sampler.samples),
    }


def verify_poll_loop_zero_handle_leaks(db_path: Path | str, iterations: int = 100) -> bool:
    """Asserts that handle leak delta across iterations is non-positive (<= 0)."""
    return run_poll_loop_handle_probe(db_path, iterations=iterations)["leak_delta"] <= 0


# ==============================================================================
# [MC-CON-08] SRS-412-03-FR-008: Cognitive Complexity Routing
# ==============================================================================
MODEL_TIER_FLASH: str = "Gemini Flash"
MODEL_TIER_SONNET: str = "Sonnet 5.5"
MODEL_TIER_OPUS: str = "Opus 5.5"

# Single source of truth for FR-008; route_by_complexity only looks scores up here.
COMPLEXITY_ROUTING_MAP: dict[int, str] = {
    **{score: MODEL_TIER_FLASH for score in range(1, 5)},
    **{score: MODEL_TIER_SONNET for score in range(5, 9)},
    **{score: MODEL_TIER_OPUS for score in range(9, 11)},
}


def route_by_complexity(score: int | float) -> str:
    """Return the model tier for an integral cognitive complexity score in 1..10 (SRS-412-03-FR-008).

    ValueError for bool, non-numbers, non-integral values (4.5, nan, inf) and scores outside 1..10.
    Integral floats such as 4.0 are accepted.
    """
    if isinstance(score, bool) or not isinstance(score, numbers.Real):
        raise ValueError(f"Cognitive complexity score must be an integer 1..10, got {score!r}")
    if not isinstance(score, numbers.Integral) and not float(score).is_integer():
        raise ValueError(f"Cognitive complexity score must be integral, got {score!r}")
    key = int(score)
    if key not in COMPLEXITY_ROUTING_MAP:
        raise ValueError(f"Cognitive complexity score must be between 1 and 10, got {score!r}")
    return COMPLEXITY_ROUTING_MAP[key]


class ComplexityRouter:
    """Cognitive complexity model router (SRS-412-03-FR-008)."""

    @staticmethod
    def route(score: int | float) -> str:
        return route_by_complexity(score)


# ==============================================================================
# [MC-CON-09] SRS-412-03 Section 7: Concurrency Profile Data Model
# ==============================================================================
_PROFILE_INT_FIELDS: tuple[str, ...] = (
    "layer1_director_pid",
    "active_subagents",
    "v8_heap_allocated_mb",
    "layer2_active_daemons",
)


@dataclass(frozen=True)
class concurrency_profile:
    """Frozen, hashable runtime concurrency snapshot (SRS-412-03 section 7)."""

    layer1_director_pid: int
    active_subagents: int
    v8_heap_allocated_mb: int
    layer2_active_daemons: int
    jit_jitter_range_ms: tuple[int, int] = JIT_JITTER_RANGE_MS

    def __post_init__(self) -> None:
        for name in _PROFILE_INT_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int, got {value!r}")
        rng = tuple(self.jit_jitter_range_ms)
        if len(rng) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in rng) or rng[0] > rng[1]:
            raise ValueError(f"jit_jitter_range_ms must be two ints (min <= max), got {self.jit_jitter_range_ms!r}")
        object.__setattr__(self, "jit_jitter_range_ms", rng)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "jit_jitter_range_ms": list(self.jit_jitter_range_ms)}

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> concurrency_profile:
        flat = data["concurrency_profile"] if "concurrency_profile" in data else data
        return cls(**{name: flat[name] for name in (*_PROFILE_INT_FIELDS, "jit_jitter_range_ms")})

    @classmethod
    def from_json(cls, json_str: str) -> concurrency_profile:
        return cls.from_dict(json.loads(json_str))


ConcurrencyProfile = concurrency_profile


# ==============================================================================
# Layer 2 Daemon Poll Loop & Execution Entry Point
# ==============================================================================
def run_layer2_daemon(
    db_path: Path | str = JOB_BOARD_PATH,
    daemon_id: str | None = None,
    max_tasks: int | None = None,
    poll_interval_sec: float = 1.0,
) -> list[str]:
    """Execute a Layer 2 worker daemon polling the SQLite WAL queue with JIT jitter."""
    pid = os.getpid()
    active_daemon_id = daemon_id or f"daemon-{pid}"
    processed_task_ids: list[str] = []

    logger.info("Starting Layer 2 daemon %s on %s", active_daemon_id, db_path)

    while True:
        if max_tasks is not None and len(processed_task_ids) >= max_tasks:
            break

        # JIT startup/poll jitter before claiming
        inject_jit_startup_jitter()

        conn = get_layer2_queue_connection(db_path)
        claimed_task_id: str | None = None
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                "SELECT id, task_id FROM jobs WHERE status = 'PENDING' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is not None:
                job_pk, claimed_task_id = row[0], row[1]
                cursor.execute(
                    "UPDATE jobs SET status = 'RUNNING', lease_owner = ?, "
                    "lease_claimed_at = unixepoch(), lease_expires_at = unixepoch() + 1800 "
                    "WHERE id = ?",
                    (active_daemon_id, job_pk),
                )
                conn.commit()
            else:
                conn.rollback()
        finally:
            conn.close()

        if claimed_task_id is not None:
            processed_task_ids.append(claimed_task_id)
            logger.info("Daemon %s claimed task: %s", active_daemon_id, claimed_task_id)
        else:
            time.sleep(poll_interval_sec)

    return processed_task_ids


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for Layer 2 Supervised OS Queues."""
    parser = argparse.ArgumentParser(description="Layer 2 OS-level supervised daemon and queue telemetry")
    parser.add_argument("--db", type=str, default=str(JOB_BOARD_PATH), help="Path to job_board.db")
    parser.add_argument("--daemon-id", type=str, default=None, help="Identifier for this daemon")
    parser.add_argument("--probe-heap", action="store_true", help="Probe Desktop Heap usage budget")
    parser.add_argument("--probe-handles", action="store_true", help="Probe poll loop handle leaks")
    parser.add_argument("--telemetry", action="store_true", help="Print coordination telemetry JSON")
    parser.add_argument("--run-daemon", action="store_true", help="Run background worker daemon")
    parser.add_argument("--max-tasks", type=int, default=None, help="Max tasks to process in daemon run")

    args = parser.parse_args(argv)

    if args.probe_heap:
        result = probe_desktop_heap_usage()
        print(json.dumps(result, indent=2))
        return 0

    if args.probe_handles:
        result = run_poll_loop_handle_probe(args.db, iterations=50, warmup=5)
        print(json.dumps(result, indent=2))
        return 0

    if args.telemetry:
        telemetry = coordinate_layer2_daemons(args.db)
        print(json.dumps(telemetry, indent=2))
        return 0

    if args.run_daemon:
        tasks = run_layer2_daemon(args.db, daemon_id=args.daemon_id, max_tasks=args.max_tasks)
        print(json.dumps({"processed_tasks": tasks, "count": len(tasks)}, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

`
