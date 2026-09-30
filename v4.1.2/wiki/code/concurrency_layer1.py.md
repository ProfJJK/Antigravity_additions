# src/cochem/concurrency/layer1.py

`python
"""Layer 1 concurrency: CLI-internal swarms under one claude.exe hosting the Fable 5.1 Director (SRS-412-03)."""
from __future__ import annotations

import logging
import os
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import psutil

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.concurrency.layer1")

# ==============================================================================
# Creation Flags & Runtime Fallbacks (SRS-412-03-FR-007, FR-006)
# ==============================================================================
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
CREATIONFLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
DEFAULT_CREATIONFLAGS: int = CREATIONFLAGS

try:
    from cochem.concurrency.runtime import (
        CREATE_NEW_PROCESS_GROUP as _R_CNPG,
        CREATE_NO_WINDOW as _R_CNW,
        CREATIONFLAGS as _R_CF,
        enforce_creationflags as _r_enforce_creationflags,
        inject_jit_startup_jitter as _r_inject_jit_startup_jitter,
        spawn_hidden_subprocess as _r_spawn_hidden_subprocess,
    )
    enforce_creationflags = _r_enforce_creationflags
    inject_jit_startup_jitter = _r_inject_jit_startup_jitter
    spawn_hidden_subprocess = _r_spawn_hidden_subprocess
except ImportError:
    def enforce_creationflags(flags: int = 0) -> int:
        """Returns ``flags`` with CREATE_NO_WINDOW and CREATE_NEW_PROCESS_GROUP OR-ed in."""
        return flags | CREATIONFLAGS

    def inject_jit_startup_jitter(min_ms: int = 100, max_ms: int = 500) -> float:
        """Sleeps for a random delay in [min_ms, max_ms]; returns that delay in seconds."""
        if isinstance(min_ms, bool) or isinstance(max_ms, bool):
            raise ValueError("jitter bounds must be numbers of milliseconds, not bool")
        if not (100 <= min_ms <= max_ms <= 500):
            raise ValueError(f"jitter range [{min_ms}, {max_ms}] ms must satisfy 100 <= min_ms <= max_ms <= 500")
        jitter_sec = random.uniform(float(min_ms), float(max_ms)) / 1000.0
        time.sleep(jitter_sec)
        return jitter_sec

    def spawn_hidden_subprocess(cmd: Sequence[str], **kwargs: Any) -> subprocess.Popen[Any]:
        """Starts ``cmd`` asynchronously (subprocess.Popen) with the mandatory flags."""
        flags = enforce_creationflags(kwargs.pop("creationflags", 0))
        argv = cmd if isinstance(cmd, str) else list(cmd)
        return subprocess.Popen(argv, creationflags=flags, **kwargs)


# ==============================================================================
# [MC-CON-14] SRS-412-03-FR-001: Fable Director CLI Command Builder
# ==============================================================================
DEFAULT_CLAUDE_PATH: str = r"C:\Users\ansac\.local\bin\claude.exe"
DIRECTOR_MODEL: str = "claude-fable-5-1"


def build_fable_director_command(
    prompt_path: Path | str,
    executable: str = DEFAULT_CLAUDE_PATH,
    model: str = DIRECTOR_MODEL,
) -> list[str]:
    """Returns ``[executable, "--model", model, "--file", str(prompt_path)]`` (SRS-412-03-FR-001)."""
    if prompt_path is None or str(prompt_path).strip() in ("", "."):
        raise ValueError("prompt_path must not be empty")
    if not executable or not str(executable).strip():
        raise ValueError("executable must not be empty")
    if not model or not str(model).strip():
        raise ValueError("model must not be empty")
    return [str(executable), "--model", str(model), "--file", str(prompt_path)]


# ==============================================================================
# [MC-CON-15] SRS-412-03-FR-002: Director 4096 MB V8 Environment
# ==============================================================================
DIRECTOR_V8_HEAP_SIZE_MB: int = 4096
DIRECTOR_NODE_OPTIONS: str = f"--max-old-space-size={DIRECTOR_V8_HEAP_SIZE_MB}"


def _split_director_heap_tokens(node_options: str) -> tuple[list[str], str | None]:
    """Splits NODE_OPTIONS into (other tokens, last heap value)."""
    tokens, kept, value, i = node_options.split(), [], None, 0
    while i < len(tokens):
        name, sep, val = tokens[i].partition("=")
        if name.replace("_", "-") != "--max-old-space-size":
            kept.append(tokens[i])
        else:
            if not sep and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i, val = i + 1, tokens[i + 1]
            value = val
        i += 1
    return kept, value


def build_director_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy base_env (os.environ when None); replace any heap-size token with 4096, keep other NODE_OPTIONS tokens."""
    env = dict(os.environ if base_env is None else base_env)
    kept, _ = _split_director_heap_tokens(" ".join(env.pop(k) for k in [k for k in env if k.upper() == "NODE_OPTIONS"]))
    return {**env, "NODE_OPTIONS": " ".join([*kept, DIRECTOR_NODE_OPTIONS])}


build_director_env = build_director_environment
inject_director_v8_memory_cap = build_director_environment


def verify_director_v8_memory_cap(env: dict[str, str]) -> bool:
    """Verifies that the effective (last) heap token is 4096 MB."""
    options = " ".join(v for k, v in env.items() if k.upper() == "NODE_OPTIONS")
    return _split_director_heap_tokens(options)[1] == str(DIRECTOR_V8_HEAP_SIZE_MB)


# ==============================================================================
# [MC-CON-16] SRS-412-03-FR-003: 20-Slot Sub-Agent Multiplexer
# ==============================================================================
MAX_CONCURRENT_SUBAGENTS: int = 20
SUPPORTED_SUBAGENT_MODELS: tuple[str, ...] = ("claude-opus-5-5", "claude-sonnet-5-5")


@dataclass
class SubAgentSlot:
    """One partitioned runtime slot held by a Layer 1 sub-agent (SRS-412-03-FR-003)."""
    slot_id: int
    agent_id: str
    model: str
    partition_id: str
    allocated_at: float = field(default_factory=time.time)


class SubAgentMultiplexer:
    """Slot table capping concurrent Director sub-agents at max_slots; one lock guards every read/write."""

    def __init__(self, max_slots: int = MAX_CONCURRENT_SUBAGENTS) -> None:
        if isinstance(max_slots, bool) or not isinstance(max_slots, int) or not 1 <= max_slots <= MAX_CONCURRENT_SUBAGENTS:
            raise ValueError(f"max_slots must be an int in 1..{MAX_CONCURRENT_SUBAGENTS}, got {max_slots!r}")
        self._max_slots = max_slots
        self._slots: dict[int, SubAgentSlot] = {}
        self._lock = threading.Lock()

    max_slots = property(lambda self: self._max_slots, doc="Slot capacity, fixed at construction.")
    active_count = property(lambda self: len(self.get_active_slots()), doc="Slots currently held.")
    available_slots = property(lambda self: self._max_slots - self.active_count, doc="Free slots.")
    is_full = property(lambda self: self.active_count >= self._max_slots, doc="True when no slot is free.")

    def allocate_slot(self, agent_id: str, model: str = "claude-sonnet-5-5") -> SubAgentSlot:
        """Claim the lowest free slot; RuntimeError when full, ValueError for a bad or duplicate request."""
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if model not in SUPPORTED_SUBAGENT_MODELS:
            raise ValueError(f"model {model!r} is not one of {SUPPORTED_SUBAGENT_MODELS}")
        with self._lock:
            if any(s.agent_id == agent_id for s in self._slots.values()):
                raise ValueError(f"agent_id {agent_id!r} already holds a slot")
            if len(self._slots) >= self._max_slots:
                raise RuntimeError(f"multiplexer full: {self._max_slots} concurrent sub-agents already active")
            slot_id = min(set(range(self._max_slots)) - self._slots.keys())
            slot = self._slots[slot_id] = SubAgentSlot(slot_id, agent_id, model, f"v8_partition_{slot_id:02d}")
            return slot

    def release_slot(self, slot_id_or_agent: int | str) -> bool:
        """Free a slot by slot id (int) or agent id (str); False when nothing matched."""
        if isinstance(slot_id_or_agent, bool) or not isinstance(slot_id_or_agent, (int, str)):
            raise TypeError(f"slot id (int) or agent id (str) expected, got {slot_id_or_agent!r}")
        with self._lock:
            key = slot_id_or_agent if isinstance(slot_id_or_agent, int) else next(
                (k for k, s in self._slots.items() if s.agent_id == slot_id_or_agent), None
            )
            return key is not None and self._slots.pop(key, None) is not None

    def get_active_slots(self) -> list[SubAgentSlot]:
        with self._lock:
            return sorted(self._slots.values(), key=lambda s: s.slot_id)


director_multiplexer: SubAgentMultiplexer = SubAgentMultiplexer()


def multiplex_subagent(agent_id: str, model: str = "claude-sonnet-5-5") -> SubAgentSlot:
    return director_multiplexer.allocate_slot(agent_id, model)


def release_subagent(slot_id_or_agent: int | str) -> bool:
    return director_multiplexer.release_slot(slot_id_or_agent)


# ==============================================================================
# [MC-CON-17] NFR-CON-03: Sub-Agent Message Latency Meter
# ==============================================================================
MAX_MESSAGE_OVERHEAD_MS: float = 10.0


class SubAgentMessageLatencyMeter:
    """Thread-safe meter of measured sub-agent message round trips; zero samples never pass (NFR-CON-03)."""

    def __init__(self, target_max_avg_ms: float = MAX_MESSAGE_OVERHEAD_MS) -> None:
        if not 0.0 < float(target_max_avg_ms) < float("inf"):
            raise ValueError(f"target_max_avg_ms must be positive and finite, got {target_max_avg_ms!r}")
        self.target_max_avg_ms: float = float(target_max_avg_ms)
        self._latencies_ms: list[float] = []
        self._lock = threading.Lock()

    def record_latency(self, latency_ms: float) -> None:
        if not 0.0 <= float(latency_ms) < float("inf"):
            raise ValueError(f"latency_ms must be a finite non-negative number, got {latency_ms!r}")
        with self._lock:
            self._latencies_ms.append(float(latency_ms))

    def record_round_trip(self, start_time: float, end_time: float | None = None) -> float:
        """Records (end - start) in ms from time.perf_counter() readings; end defaults to now."""
        latency_ms = ((time.perf_counter() if end_time is None else end_time) - start_time) * 1000.0
        self.record_latency(latency_ms)
        return latency_ms

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._latencies_ms)

    def average_latency_ms(self) -> float:
        with self._lock:
            if not self._latencies_ms:
                raise ValueError("no latency samples recorded; the average is undefined")
            return sum(self._latencies_ms) / len(self._latencies_ms)

    def is_overhead_compliant(self) -> bool:
        with self._lock:
            return bool(self._latencies_ms) and sum(self._latencies_ms) / len(self._latencies_ms) < self.target_max_avg_ms

    def reset(self) -> None:
        with self._lock:
            self._latencies_ms.clear()


MessageLatencyMeter = SubAgentMessageLatencyMeter
subagent_latency_meter: SubAgentMessageLatencyMeter = SubAgentMessageLatencyMeter()


def measure_subagent_message_latency(meter: SubAgentMessageLatencyMeter | None = None) -> float:
    return (subagent_latency_meter if meter is None else meter).average_latency_ms()


# ==============================================================================
# [MC-CON-18] FAILURE:V8 Heap Warning - Director V8 Heap Recycle Guard
# ==============================================================================
V8_HEAP_WARNING_THRESHOLD_MB: int = 3800
DEFAULT_HEAP_DRAIN_TIMEOUT_SEC: float = 30.0
DEFAULT_DIRECTOR_RETIRE_TIMEOUT_SEC: float = 10.0


class DirectorHeapRecycleError(RuntimeError):
    """Base class for Director heap-recycle failures (FAILURE:V8 Heap Warning)."""


class DirectorDrainTimeoutError(DirectorHeapRecycleError, TimeoutError):
    """In-flight sub-agents did not drain within the timeout, so the Director must not be recycled yet."""


class DirectorProcessUnknownError(DirectorHeapRecycleError, LookupError):
    """A heap measurement was requested but no Director process id is known."""


def _checked_heap_mb(value: float, what: str) -> float:
    heap = float(value)
    if not 0.0 <= heap < float("inf"):
        raise ValueError(f"{what} must be a finite non-negative number of MB, got {value!r}")
    return heap


def _process_rss_mb(pid: int) -> float:
    """Measured resident set size of ``pid`` in MB; psutil.NoSuchProcess / AccessDenied propagate."""
    return psutil.Process(int(pid)).memory_info().rss / (1024 * 1024)


def should_recycle_director(
    heap_mb: float | None = None,
    threshold_mb: float = V8_HEAP_WARNING_THRESHOLD_MB,
    *,
    pid: int | None = None,
) -> bool:
    """True when the Director heap is at or above ``threshold_mb``."""
    threshold = _checked_heap_mb(threshold_mb, "threshold_mb")
    if heap_mb is None:
        heap_mb = _process_rss_mb(os.getpid() if pid is None else pid)
    return _checked_heap_mb(heap_mb, "heap_mb") >= threshold


class DirectorHeapRecycleGuard:
    """Watches the Director heap; at >= threshold_mb drains in-flight sub-agents, retires the old Director it
    spawned, and launches a fresh Director (4096 MB V8 cap, hidden window) through the runtime launch helper."""

    def __init__(
        self,
        threshold_mb: float = V8_HEAP_WARNING_THRESHOLD_MB,
        drain_timeout_sec: float = DEFAULT_HEAP_DRAIN_TIMEOUT_SEC,
        *,
        multiplexer: SubAgentMultiplexer | None = None,
        director_pid: int | None = None,
    ) -> None:
        self.threshold_mb: float = _checked_heap_mb(threshold_mb, "threshold_mb")
        if self.threshold_mb == 0.0:
            raise ValueError("threshold_mb must be positive")
        self.drain_timeout_sec: float = _checked_heap_mb(drain_timeout_sec, "drain_timeout_sec")
        self.multiplexer: SubAgentMultiplexer = director_multiplexer if multiplexer is None else multiplexer
        self.recycle_count: int = 0
        self.director_process: subprocess.Popen[Any] | None = None
        self._external_director_pid: int | None = None if director_pid is None else int(director_pid)

    @property
    def director_pid(self) -> int | None:
        """Pid of the Director being guarded: the one this guard last spawned, else the one it was given."""
        return self.director_process.pid if self.director_process is not None else self._external_director_pid

    def get_current_rss_mb(self, pid: int | None = None) -> float:
        """Measured RSS in MB of ``pid`` (default: the guarded Director, else this process). Errors propagate."""
        if pid is None:
            pid = self.director_pid if self.director_pid is not None else os.getpid()
        return _process_rss_mb(pid)

    def should_recycle(self, current_heap_mb: float | None = None) -> bool:
        """True when the heap is at or above threshold_mb."""
        if current_heap_mb is None:
            if self.director_pid is None:
                raise DirectorProcessUnknownError("no Director pid known; pass current_heap_mb or director_pid")
            current_heap_mb = self.get_current_rss_mb(self.director_pid)
        return _checked_heap_mb(current_heap_mb, "current_heap_mb") >= self.threshold_mb

    def complete_in_flight_tasks(self, timeout_sec: float | None = None) -> bool:
        """Waits until every in-flight sub-agent slot is released; True when drained, False on timeout."""
        timeout = self.drain_timeout_sec if timeout_sec is None else _checked_heap_mb(timeout_sec, "timeout_sec")
        deadline = time.monotonic() + timeout
        while self.multiplexer.active_count > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.multiplexer.active_count == 0

    def retire_director(self, timeout_sec: float = DEFAULT_DIRECTOR_RETIRE_TIMEOUT_SEC) -> int | None:
        """Stops the Director this guard spawned and its whole process tree."""
        proc, self.director_process = self.director_process, None
        if proc is None:
            return None
        children: list[psutil.Process] = []
        if proc.poll() is None:
            try:
                children = psutil.Process(proc.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            proc.terminate()
            try:
                proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_sec)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                continue
        _gone, alive = psutil.wait_procs(children, timeout=timeout_sec)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                continue
        _gone, alive = psutil.wait_procs(alive, timeout=timeout_sec)
        if alive:
            raise DirectorHeapRecycleError(f"retired Director left live children: {[p.pid for p in alive]}")
        for stream in (proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        return proc.returncode

    def spawn_fresh_instance(
        self,
        prompt_path: Path | str,
        executable: str = DEFAULT_CLAUDE_PATH,
        model: str = DIRECTOR_MODEL,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.Popen[Any]:
        """Launches a fresh Director via spawn_hidden_subprocess (FR-007 flags) with NODE_OPTIONS 4096."""
        env = build_director_environment(extra_env)
        cmd = build_fable_director_command(prompt_path, executable=executable, model=model)
        proc = spawn_hidden_subprocess(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        self.director_process = proc
        self.recycle_count += 1
        return proc

    def recycle_if_approaching_limit(
        self,
        prompt_path: Path | str,
        current_heap_mb: float | None = None,
        executable: str = DEFAULT_CLAUDE_PATH,
        model: str = DIRECTOR_MODEL,
    ) -> subprocess.Popen[Any] | None:
        """None below threshold. At/above it: drain in-flight sub-agents, retire old, spawn fresh."""
        if not self.should_recycle(current_heap_mb):
            return None
        if not self.complete_in_flight_tasks():
            raise DirectorDrainTimeoutError(
                f"{self.multiplexer.active_count} in-flight sub-agent(s) still active after "
                f"{self.drain_timeout_sec}s; Director not recycled"
            )
        self.retire_director()
        return self.spawn_fresh_instance(prompt_path, executable=executable, model=model)


director_heap_guard: DirectorHeapRecycleGuard = DirectorHeapRecycleGuard()


# ==============================================================================
# [MC-CON-19] FAILURE:Subprocess Hang - 1740s Process-Tree Timeout Runner
# ==============================================================================
SUBPROCESS_TIMEOUT_SEC: int = 1740
LEASE_TIMEOUT_SEC: int = 1800
DEFAULT_SUBPROCESS_TIMEOUT_SEC: int = SUBPROCESS_TIMEOUT_SEC
DEFAULT_TREE_KILL_TIMEOUT_SEC: float = 5.0
_TREE_POLL_INTERVAL_SEC: float = 0.1
_TREE_SWEEP_ROUNDS: int = 5


class ProcessTreeTerminationError(RuntimeError):
    """Killing a process tree did not complete: some processes are still alive afterwards (FAILURE:Subprocess Hang)."""

    def __init__(self, message: str, survivors: list[int], access_denied: list[int]) -> None:
        super().__init__(f"{message}; survivors={survivors} access_denied={access_denied}")
        self.survivors: list[int] = list(survivors)
        self.access_denied: list[int] = list(access_denied)


class ProcessTreeTimeoutExpired(subprocess.TimeoutExpired):
    """The command exceeded its timeout and its whole process tree was killed (``terminated_pids``)."""

    def __init__(
        self,
        cmd: Any,
        timeout: float,
        output: Any = None,
        stderr: Any = None,
        terminated_pids: list[int] | None = None,
    ) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.terminated_pids: list[int] = list(terminated_pids or [])


def _validate_subprocess_timeout(timeout: float, lease_timeout_sec: float = LEASE_TIMEOUT_SEC) -> float:
    if isinstance(timeout, bool):
        raise ValueError(f"timeout must be a number of seconds, got {timeout!r}")
    value = float(timeout)
    if not 0.0 < value < float(lease_timeout_sec):
        raise ValueError(f"timeout must be > 0 and < the {lease_timeout_sec} s lease, got {timeout!r}")
    return value


def _process_or_none(pid: int) -> Any:
    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None


_SPAWN_CLOCK_TOLERANCE_SEC: float = 0.5


def _direct_children_since(parent_pid: int, since: float) -> list[Any]:
    return [
        p for p in psutil.process_iter(["ppid", "create_time"])
        if p.info["ppid"] == parent_pid and p.info["create_time"] is not None
        and p.info["create_time"] >= since - _SPAWN_CLOCK_TOLERANCE_SEC
    ]


def _track_descendants(root_pid: int, tracked: set[Any], root_spawned_at: float | None = None) -> None:
    root = _process_or_none(root_pid)
    if root is None and root_spawned_at is not None:
        tracked.update(_direct_children_since(root_pid, root_spawned_at))
    for owner in [*([root] if root is not None else []), *tracked]:
        try:
            tracked.update(owner.children(recursive=True))
        except psutil.NoSuchProcess:
            continue


def _wait_until_gone(procs: set[Any], timeout_sec: float) -> list[Any]:
    deadline = time.monotonic() + timeout_sec
    alive = [p for p in procs if p.is_running()]
    while alive and time.monotonic() < deadline:
        time.sleep(_TREE_POLL_INTERVAL_SEC)
        alive = [p for p in alive if p.is_running()]
    return alive


def terminate_process_tree(
    proc: subprocess.Popen[Any] | int | Any,
    *,
    kill_timeout_sec: float = DEFAULT_TREE_KILL_TIMEOUT_SEC,
    descendants: Any = (),
    root_spawned_at: float | None = None,
) -> list[int]:
    """Kills a process and all of its recursive descendants (FAILURE:Subprocess Hang); returns killed pids."""
    popen = proc if isinstance(proc, subprocess.Popen) else None
    pid = int(proc.pid) if hasattr(proc, "pid") else int(proc)
    targets: set[psutil.Process] = {p for p in descendants if p.is_running()}
    root: psutil.Process | None = None
    if popen is None or popen.poll() is None:
        root = proc if isinstance(proc, psutil.Process) else _process_or_none(pid)
        if root is not None and not root.is_running():
            root = None
    if root is not None:
        try:
            targets.update(root.children(recursive=True))
        except psutil.NoSuchProcess:
            root = None
    if root is None and popen is not None and root_spawned_at is not None:
        targets.update(_direct_children_since(pid, root_spawned_at))
    for tracked in list(targets):
        try:
            targets.update(tracked.children(recursive=True))
        except psutil.NoSuchProcess:
            continue

    access_denied: list[int] = []
    root_survived = False
    if root is not None:
        try:
            if popen is not None:
                popen.kill()
            else:
                root.kill()
        except psutil.NoSuchProcess:
            root = None
        except (psutil.AccessDenied, PermissionError):
            access_denied.append(pid)

    killed: set[psutil.Process] = set()
    for _round in range(_TREE_SWEEP_ROUNDS):
        fresh = targets - killed
        if not fresh:
            break
        for child in fresh:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                access_denied.append(child.pid)
        killed |= fresh
        members = {p.pid: p for p in (*killed, *([root] if root is not None else []))}
        for cand in psutil.process_iter(["ppid", "create_time"]):
            parent = members.get(cand.info["ppid"])
            if (
                parent is not None and cand.info["create_time"] is not None
                and cand.info["create_time"] >= parent.create_time() and cand not in killed
            ):
                targets.add(cand)

    if popen is not None and root is not None:
        try:
            popen.wait(timeout=kill_timeout_sec)
        except subprocess.TimeoutExpired:
            root_survived = True

    alive = _wait_until_gone(targets | ({root} if root is not None and popen is None else set()), kill_timeout_sec)
    survivors = sorted({p.pid for p in alive} | ({pid} if root_survived else set()))
    if survivors:
        raise ProcessTreeTerminationError(
            f"process tree of pid {pid} not fully terminated", survivors, sorted(set(access_denied))
        )
    return sorted({p.pid for p in killed} | ({pid} if root is not None else set()))


def run_with_timeout(
    cmd: list[str] | Any,
    timeout: float = SUBPROCESS_TIMEOUT_SEC,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Runs ``cmd`` under the 1740 s process-tree timeout (FAILURE:Subprocess Hang); returns CompletedProcess."""
    timeout_sec = _validate_subprocess_timeout(timeout)
    kwargs.setdefault("text", True)
    if kwargs.pop("capture_output", False):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    spawned_at = time.time()
    proc = spawn_hidden_subprocess(cmd, **kwargs)
    tracked: set[Any] = set()
    deadline = time.monotonic() + timeout_sec
    try:
        while True:
            _track_descendants(proc.pid, tracked, spawned_at)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                stdout, stderr = proc.communicate(timeout=min(_TREE_POLL_INTERVAL_SEC, remaining))
            except subprocess.TimeoutExpired:
                continue
            return subprocess.CompletedProcess(args=cmd, returncode=proc.returncode, stdout=stdout, stderr=stderr)
    except BaseException:
        terminate_process_tree(proc, descendants=tracked, root_spawned_at=spawned_at)
        raise

    timeout_exc = ProcessTreeTimeoutExpired(cmd, timeout_sec)
    try:
        timeout_exc.terminated_pids = terminate_process_tree(proc, descendants=tracked, root_spawned_at=spawned_at)
    except ProcessTreeTerminationError as exc:
        raise exc from timeout_exc
    try:
        timeout_exc.output, timeout_exc.stderr = proc.communicate(timeout=DEFAULT_TREE_KILL_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        raise ProcessTreeTerminationError(
            "process tree killed but its output pipes are still held open", [], []
        ) from exc
    raise timeout_exc


run_process_tree_timeout = run_with_timeout


class ProcessTreeTimeoutRunner:
    """Subprocess runner enforcing the 1740 s timeout and process-tree termination before the 1800 s lease."""

    def __init__(
        self,
        subprocess_timeout_sec: float = SUBPROCESS_TIMEOUT_SEC,
        lease_timeout_sec: float = LEASE_TIMEOUT_SEC,
    ) -> None:
        self.lease_timeout_sec: float = float(lease_timeout_sec)
        if not 0.0 < self.lease_timeout_sec <= float(LEASE_TIMEOUT_SEC):
            raise ValueError(f"lease_timeout_sec must be in (0, {LEASE_TIMEOUT_SEC}], got {lease_timeout_sec!r}")
        self.subprocess_timeout_sec: float = _validate_subprocess_timeout(subprocess_timeout_sec, self.lease_timeout_sec)

    def run(
        self,
        cmd: list[str] | Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        effective_timeout = self.subprocess_timeout_sec if timeout is None else timeout
        _validate_subprocess_timeout(effective_timeout, self.lease_timeout_sec)
        return run_with_timeout(cmd, timeout=effective_timeout, **kwargs)

    def terminate_tree(self, proc: subprocess.Popen[Any] | int | Any) -> list[int]:
        return terminate_process_tree(proc)


process_tree_timeout_runner: ProcessTreeTimeoutRunner = ProcessTreeTimeoutRunner()


# ==============================================================================
# [MC-CON-20] INTERFACE:launch_layer1_fable_director
# ==============================================================================
def launch_layer1_fable_director(
    prompt_path: Path | str,
    executable: str = DEFAULT_CLAUDE_PATH,
    model: str = DIRECTOR_MODEL,
    timeout: float = SUBPROCESS_TIMEOUT_SEC,
    base_env: dict[str, str] | None = None,
    creationflags: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Launches the Claude Fable 5.1 Director (SRS-412-03 s6) and returns its CompletedProcess."""
    timeout_sec = float(timeout)
    if not 0.0 < timeout_sec < float(LEASE_TIMEOUT_SEC):
        raise ValueError(f"timeout must be > 0 and < the {LEASE_TIMEOUT_SEC} s lease, got {timeout!r}")
    cmd = build_fable_director_command(prompt_path, executable=executable, model=model)
    if not Path(prompt_path).is_file():
        raise FileNotFoundError(f"Director prompt file not found: {prompt_path}")
    env = build_director_environment(base_env)
    flags = enforce_creationflags(creationflags)
    inject_jit_startup_jitter()
    return run_with_timeout(
        cmd,
        timeout=timeout_sec,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        creationflags=flags,
    )


launch_fable_director = launch_layer1_fable_director

`
