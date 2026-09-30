# src/cochem/quarantine/sandbox.py

`python
# [MC-QVM-10] DATA_MODEL:sandbox_spec
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Define the
# SPEC: sandbox_spec record exactly as the section 7 JSON model, with a validator that rejects missing
# SPEC: or mistyped fields. Fields: image: str (configurable general-purpose sandbox image tag; the SRS
# SPEC: example tag is a leftover of the removed canary and must not pull scientific libraries);
# SPEC: network: str (e.g. "none"); memory_mb: int (e.g. 4096); cpus: int (e.g. 2); pids_limit: int
# SPEC: (e.g. 512); tmpfs_size: str (e.g. "2g"); security_opt: list (e.g. ["no-new-privileges:true"]);
# SPEC: cap_drop: list (e.g. ["ALL"]). Frozen dataclass with from_dict()/to_dict(). network must be
# SPEC: 'none', cap_drop must contain 'ALL', security_opt must contain 'no-new-privileges:true', and
# SPEC: memory_mb must not exceed 4096 (FR-007 slot size).
from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess, sys, time
from dataclasses import dataclass, field, asdict; from typing import Any
CREATE_NO_WINDOW: int = 0x08000000; DEFAULT_SANDBOX_IMAGE: str = "general-sandbox:latest"
MAX_SANDBOX_MEMORY_MB: int = 4096; DEFAULT_TMPFS_SPEC: str = '/tmp:rw,size=2g'
@dataclass(frozen=True)
class sandbox_spec:
    image: str = DEFAULT_SANDBOX_IMAGE; network: str = 'none'; memory_mb: int = 4096; cpus: int = 2
    pids_limit: int = 512; tmpfs_size: str = '2g'
    security_opt: list[str] = field(default_factory=lambda: ['no-new-privileges:true'])
    cap_drop: list[str] = field(default_factory=lambda: ['ALL'])
    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not self.image: raise ValueError('image must be non-empty str')
        if self.network != 'none': raise ValueError(f"network must be 'none', got {self.network!r}")
        if not isinstance(self.memory_mb, int) or not (0 < self.memory_mb <= MAX_SANDBOX_MEMORY_MB): raise ValueError('memory_mb out of bounds')
        if not isinstance(self.cpus, int) or self.cpus <= 0 or not isinstance(self.pids_limit, int) or self.pids_limit <= 0: raise ValueError('cpus/pids_limit positive')
        if not isinstance(self.tmpfs_size, str) or not self.tmpfs_size: raise ValueError('tmpfs_size non-empty str')
        if not isinstance(self.cap_drop, list) or 'ALL' not in self.cap_drop: raise ValueError("cap_drop must include 'ALL'")
        if not isinstance(self.security_opt, list) or 'no-new-privileges:true' not in self.security_opt: raise ValueError("security_opt missing flag")
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> sandbox_spec:
        return cls(**data)
SandboxSpec = sandbox_spec
def validate_sandbox_spec(spec: sandbox_spec) -> list[str]:
    v: list[str] = []
    if spec.network != 'none': v.append("network must be 'none'")
    if spec.memory_mb > MAX_SANDBOX_MEMORY_MB: v.append(f'memory_mb exceeds {MAX_SANDBOX_MEMORY_MB}')
    return v
# [MC-QVM-11] SRS-412-02-FR-003
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-02-FR-003: The
# SPEC: execution plane shall spawn all agent coding tasks inside ephemeral Docker containers with
# SPEC: `--network none`, `--read-only`, and `--tmpfs /tmp:rw,size=2g`. Guest side, runs in the Ubuntu
# SPEC: 24.04 VM. Return a docker run argv list that always contains --rm, --network none, --read-only,
# SPEC: --tmpfs /tmp:rw,size=2g plus --memory, --cpus, --pids-limit, --cap-drop ALL and --security-opt
# SPEC: from the sandbox_spec. Callers cannot remove these flags; an override that weakens one raises
# SPEC: ValueError.
MANDATORY_RUN_FLAGS = ('--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,size=2g')
def build_docker_run_argv(
    spec: sandbox_spec,
    image_override: str | None = None,
    extra_flags: list[str] | None = None,
    command: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> list[str]:
    """Builds physical docker run argv enforcing mandatory ephemeral isolation flags."""
    if overrides:
        for k, v in overrides.items():
            if k in ('network', 'read_only') and v not in ('none', True): raise ValueError(f'Weakening {k} prohibited')
            if k == 'cap_drop' and 'ALL' not in v: raise ValueError('Weakening cap_drop prohibited')
            if k == 'security_opt' and 'no-new-privileges:true' not in v: raise ValueError('Weakening security_opt prohibited')
    if extra_flags:
        for flag in extra_flags:
            if flag in ('--net=host', '--privileged') or (flag.startswith('--network') and flag != '--network=none'):
                raise ValueError(f'Flag {flag!r} weakens isolation and is strictly prohibited')
    argv: list[str] = [
        'docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,size=2g',
        '--memory', f'{spec.memory_mb}m', '--memory-swap', f'{spec.memory_mb}m',  # NFR-VM-02: no swap past the slot
        '--cpus', str(spec.cpus), '--pids-limit', str(spec.pids_limit),
    ]
    for opt in spec.security_opt: argv.extend(['--security-opt', opt])
    for cap in spec.cap_drop: argv.extend(['--cap-drop', cap])
    if extra_flags: argv.extend(extra_flags)
    argv.append(image_override or spec.image)
    if command: argv.extend(command)
    return argv
build_ephemeral_docker_argv = build_docker_run_argv
build_docker_run_command = build_docker_run_argv
build_ephemeral_docker_command = build_docker_run_argv
def verify_ephemeral_flags(argv: list[str]) -> bool:
    for flag in ['--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,size=2g']:
        if flag not in argv: return False
    return True
def run_in_docker_sandbox(cmd: list[str], image: str = DEFAULT_SANDBOX_IMAGE) -> list[str]:
    return build_docker_run_argv(sandbox_spec(image=image), command=cmd)
# [MC-QVM-12] NFR-VM-03
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-VM-03: Network
# SPEC: isolation shall be verifiable via physical connection drop (`Network is unreachable`). Run a
# SPEC: real container with the FR-003 argv that attempts a TCP connect to 1.1.1.1:53 and assert the
# SPEC: error text contains 'Network is unreachable'; a timeout does not count as proof.
def build_network_probe_command(spec: sandbox_spec) -> list[str]:
    return build_docker_run_argv(spec, command=['python', '-c', 'import socket; s = socket.socket(); s.connect(("1.1.1.1", 53))'])
def verify_network_isolation_drop(output_text: str) -> bool:
    if not output_text or not output_text.strip() or 'timed out' in output_text.lower(): return False
    lower = output_text.lower()
    return 'network is unreachable' in lower or 'network unreachable' in lower or 'errno 101' in lower
def verify_network_unreachable(output_text: str) -> list[str]:
    if not output_text or not output_text.strip(): return ['Empty output received']
    lower = output_text.lower()
    if 'timed out' in lower: return ['Connection timed out: timeout does not count as proof']
    if 'network is unreachable' not in lower and 'network unreachable' not in lower and 'errno 101' not in lower:
        return [f'Expected Network is unreachable, got: {output_text.strip()}']
    return []
def run_network_isolation_probe(spec: sandbox_spec | None = None) -> dict[str, Any]:
    s = spec or sandbox_spec(image='python:3.14.7-slim')
    cmd = build_network_probe_command(s)
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    combined = f'{res.stdout}\n{res.stderr}'
    return {'passed': verify_network_isolation_drop(combined), 'exit_code': res.returncode, 'evidence': combined.strip()[:500]}
def verify_in_container_network_unreachable(spec: sandbox_spec | None = None) -> bool:
    probe = run_network_isolation_probe(spec)
    if not probe['passed']: raise AssertionError(f'Network isolation probe failed: {probe["evidence"]}')
    return True
probe_network_isolation = verify_in_container_network_unreachable
verify_network_isolation = verify_in_container_network_unreachable
# [MC-QVM-13] SRS-412-02-FR-004
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-02-FR-004:
# SPEC: Student pedagogy tasks shall execute in isolated, air-gapped FERPA containers with scrubbed
# SPEC: student identifiers. Scrub student identifiers (student IDs, emails, names from the roster
# SPEC: column list) into stable salted SHA-256 pseudonyms before any payload enters the container,
# SPEC: and launch with the FR-003 flags plus a per-task --name and no bind mounts except the
# SPEC: scrubbed read-only input. Keep the salt outside the container.
from pathlib import Path
FERPA_ROSTER_IDENTIFIERS: tuple[str, ...] = ("student_id", "email", "name", "first_name", "last_name")
FERPA_CONTAINER_NAME_PREFIX: str = "cochem-ferpa-"
FERPA_INPUT_MOUNT_POINT: str = "/input"
FERPA_SALT_ENV_VAR: str = "COCHEM_FERPA_SALT"
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

def scrub_student_pseudonym(value: Any, salt: str) -> str:
    """Stable salted SHA-256 pseudonym: sha256('<salt>:<value>').hexdigest(). Salt never enters the container."""
    if not isinstance(salt, str) or not salt:
        raise ValueError("FERPA salt must be a non-empty string kept outside the container")
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()

def scrub_student_identifiers(roster: list[dict[str, Any]], salt: str, columns: tuple[str, ...] = FERPA_ROSTER_IDENTIFIERS) -> list[dict[str, Any]]:
    """Replaces every roster identifier column with its pseudonym; non-identifier columns (grades) pass through."""
    if not isinstance(roster, list): raise TypeError("roster must be a list of row dicts")
    if not isinstance(salt, str) or not salt: raise ValueError("FERPA salt must be a non-empty string")
    scrubbed: list[dict[str, Any]] = []
    for row in roster:
        if not isinstance(row, dict): raise TypeError("roster rows must be dicts")
        cleaned = dict(row)
        for col in columns:
            if col in cleaned and cleaned[col] not in (None, ""):
                cleaned[col] = scrub_student_pseudonym(cleaned[col], salt)
        scrubbed.append(cleaned)
    return scrubbed

def load_ferpa_salt(env: dict[str, str] | None = None) -> str:
    """Reads the salt from the host environment; it is never passed as -e/--env into the container."""
    salt = (env if env is not None else os.environ).get(FERPA_SALT_ENV_VAR, "")
    if not salt: raise ValueError(f"{FERPA_SALT_ENV_VAR} must be set on the host")
    return salt

def build_ferpa_docker_args(task_id: str, scrubbed_input_path: str | Path, spec: sandbox_spec | None = None,
                            extra_bind_mounts: list[str] | None = None, command: list[str] | None = None) -> list[str]:
    """FR-003 argv plus --name cochem-ferpa-<task_id> and exactly one read-only bind mount (the scrubbed input)."""
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id): raise ValueError(f"invalid task_id {task_id!r}")
    if extra_bind_mounts: raise ValueError("FERPA containers permit no bind mounts other than the scrubbed read-only input")
    host_path = Path(scrubbed_input_path).resolve()
    if not host_path.is_file(): raise ValueError(f"scrubbed input {host_path} is not a file")
    s = spec or sandbox_spec(image="ferpa-sandbox:latest")
    extra = ["--name", f"{FERPA_CONTAINER_NAME_PREFIX}{task_id}", "-v", f"{host_path}:{FERPA_INPUT_MOUNT_POINT}:ro"]
    return build_docker_run_argv(s, extra_flags=extra, command=command)

def verify_ferpa_isolation_flags(argv: list[str], scrubbed_input_path: str | Path) -> list[str]:
    """Violations if any bind mount other than <input>:/input:ro is present, the salt env leaks, or air-gap is missing."""
    v: list[str] = []
    expected = f"{Path(scrubbed_input_path).resolve()}:{FERPA_INPUT_MOUNT_POINT}:ro"
    mounts = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("-v", "--volume", "--mount")]
    if mounts != [expected]: v.append(f"bind mounts must be exactly [{expected}], got {mounts}")
    if not verify_ephemeral_flags(argv): v.append("FR-003 mandatory flags missing")
    if any(FERPA_SALT_ENV_VAR in a for a in argv): v.append("FERPA salt must not enter the container")
    return v
scrub_student_identifier = scrub_student_pseudonym
scrub_roster_data = scrub_student_identifiers
build_ferpa_container_argv = build_ferpa_docker_args
# [MC-QVM-14] NFR-VM-01
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-VM-01: Container
# SPEC: startup latency from execution request to test execution shall not exceed 1.5 seconds. Measure
# SPEC: with time.monotonic() from the execution request to the first line the container writes to
# SPEC: stdout; record the value and flag breaches above 1.5 s without failing the task.
MAX_STARTUP_LATENCY_SEC: float = 1.5; CONTAINER_STARTUP_LATENCY_MAX_SEC: float = 1.5

def measure_container_startup_latency(
    cmd: list[str] | None = None, start_time: float | None = None,
    first_stdout_time: float | None = None, max_latency_sec: float = MAX_STARTUP_LATENCY_SEC,
) -> dict[str, Any]:
    """Measures container startup latency with time.monotonic() and flags breaches > 1.5s."""
    first_line = ""
    if start_time is not None and first_stdout_time is not None:
        elapsed = float(first_stdout_time - start_time)
    elif cmd is not None:
        t_start = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW)
        try:
            if proc.stdout is not None: first_line = proc.stdout.readline()
        finally:
            proc.kill(); proc.wait()
        elapsed = time.monotonic() - t_start
    else: raise ValueError("Must provide cmd or (start_time and first_stdout_time)")
    is_breach = elapsed > max_latency_sec
    return {"latency_sec": elapsed, "breached": is_breach, "max_latency_sec": max_latency_sec, "first_line": first_line.rstrip("\r\n")}

def check_startup_latency_compliance(latency_sec: float, max_latency_sec: float = MAX_STARTUP_LATENCY_SEC) -> bool:
    """Returns True if startup latency satisfies NFR-VM-01 (<= 1.5s)."""
    return latency_sec <= max_latency_sec
# [MC-QVM-15] SRS-412-02-FR-007
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-02-FR-007: The
# SPEC: container scheduler shall enforce a concurrency limit of at most 6 parallel 4 GB sandboxes
# SPEC: inside the 32 GB VM. Use a threading.BoundedSemaphore(6) so at most 6 sandboxes with
# SPEC: memory_mb=4096 run at once (24 GB of the 32 GB VM); a seventh request blocks until a slot frees,
# SPEC: and the slot is released in a finally block.
import contextlib, threading
from typing import Callable, Generator
MAX_CONCURRENT_CONTAINERS: int = 6
CONTAINER_SLOT_MEMORY_MB: int = 4096
VM_RAM_MB: int = 32768
TOTAL_SLOT_ALLOCATION_MB: int = MAX_CONCURRENT_CONTAINERS * CONTAINER_SLOT_MEMORY_MB
assert TOTAL_SLOT_ALLOCATION_MB <= VM_RAM_MB
QUARANTINED_TASKS: set[str] = set()

@dataclass(frozen=True)
class SandboxSlot:
    slot_id: int; task_id: str; memory_mb: int; acquired_at: float

class ContainerScheduler:
    """Bounded scheduler: at most max_slots parallel 4 GB sandboxes; a seventh acquire blocks or raises."""
    def __init__(self, max_slots: int = MAX_CONCURRENT_CONTAINERS, slot_memory_mb: int = CONTAINER_SLOT_MEMORY_MB) -> None:
        if not (0 < max_slots <= MAX_CONCURRENT_CONTAINERS): raise ValueError(f"max_slots must be 1..{MAX_CONCURRENT_CONTAINERS}")
        if slot_memory_mb > CONTAINER_SLOT_MEMORY_MB: raise ValueError(f"slot_memory_mb exceeds {CONTAINER_SLOT_MEMORY_MB}")
        self.max_slots = max_slots; self.slot_memory_mb = slot_memory_mb
        self._sem = threading.BoundedSemaphore(max_slots); self._lock = threading.Lock()
        self._active: dict[int, SandboxSlot] = {}; self._next_id = 0
    @property
    def active_count(self) -> int:
        with self._lock: return len(self._active)
    @property
    def is_full(self) -> bool:
        return self.active_count >= self.max_slots
    @property
    def active_memory_mb(self) -> int:
        with self._lock: return sum(s.memory_mb for s in self._active.values())
    def acquire_slot(self, task_id: str, block: bool = True, timeout: float | None = None, spec: sandbox_spec | None = None) -> SandboxSlot:
        """Takes one semaphore permit. block=False raises BlockingIOError immediately when all slots are busy."""
        if task_id in QUARANTINED_TASKS: raise RuntimeError(f"task {task_id!r} is QUARANTINED and is never retried automatically")
        mem = spec.memory_mb if spec is not None else self.slot_memory_mb
        if mem > self.slot_memory_mb: raise ValueError(f"task memory {mem} MB exceeds slot ceiling {self.slot_memory_mb} MB")
        if not block: ok = self._sem.acquire(blocking=False)
        else: ok = self._sem.acquire(timeout=-1 if timeout is None else timeout)
        if not ok:
            if not block: raise BlockingIOError(f"all {self.max_slots} sandbox slots are active")
            raise TimeoutError(f"no sandbox slot freed within {timeout}s")
        with self._lock:
            self._next_id += 1
            slot = SandboxSlot(self._next_id, task_id, mem, time.monotonic()); self._active[slot.slot_id] = slot
        return slot
    def release_slot(self, slot: SandboxSlot) -> None:
        with self._lock:
            if slot.slot_id not in self._active: raise ValueError(f"slot {slot.slot_id} is not active")
            del self._active[slot.slot_id]
        self._sem.release()
    @contextlib.contextmanager
    def slot(self, task_id: str, block: bool = True, timeout: float | None = None, spec: sandbox_spec | None = None) -> Generator[SandboxSlot, None, None]:
        s = self.acquire_slot(task_id, block=block, timeout=timeout, spec=spec)
        try: yield s
        finally: self.release_slot(s)
    def schedule(self, task_id: str, task_fn: Callable[[], Any], spec: sandbox_spec | None = None, timeout: float | None = None) -> Any:
        """Runs task_fn inside a slot; the slot is released in the context manager's finally block."""
        with self.slot(task_id, timeout=timeout, spec=spec): return task_fn()
    run_sandboxed = schedule
MAX_PARALLEL_CONTAINERS = MAX_CONCURRENT_CONTAINERS; SLOT_MEMORY_MB = CONTAINER_SLOT_MEMORY_MB
CONTAINER_CONCURRENCY_LIMIT = MAX_CONCURRENT_CONTAINERS; TOTAL_VM_ALLOCATION_MB = TOTAL_SLOT_ALLOCATION_MB
# [MC-QVM-16] NFR-VM-02
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-VM-02: Out-of-memory
# SPEC: container terminations shall be isolated to the offending container without crashing the VM
# SPEC: kernel. Set --memory and --memory-swap to the same value so a container cannot swap past its
# SPEC: slot, and never pass --oom-kill-disable, so the kernel kills only the offending container.


def build_oom_isolated_flags(spec: Any = None, memory_mb: int = 4096) -> list[str]:
    """Builds Docker memory limit flags enforcing OOM containment isolation (NFR-VM-02).

    Sets --memory and --memory-swap to identical values to prevent swap past slot,
    and strictly omits --oom-kill-disable so kernel kills only the offending container.
    """
    if spec is not None:
        if hasattr(spec, "memory_mb"):
            mem = spec.memory_mb
        elif isinstance(spec, dict) and "memory_mb" in spec:
            mem = spec["memory_mb"]
        elif isinstance(spec, int):
            mem = spec
        else:
            mem = memory_mb
    else:
        mem = memory_mb
    val = f"{mem}m" if isinstance(mem, int) else str(mem)
    return ["--memory", val, "--memory-swap", val]


def enforce_oom_containment_limits(argv: list[str], spec: Any = None) -> list[str]:
    """Validates and enforces per-container OOM containment on docker run argv."""
    if "--oom-kill-disable" in argv:
        raise ValueError("Violates NFR-VM-02: --oom-kill-disable must never be passed to sandbox containers")
    oom_flags = build_oom_isolated_flags(spec)
    res = [arg for arg in argv if arg not in ("--memory", "--memory-swap", "--oom-kill-disable")]
    return oom_flags + res


SandboxSpec = sandbox_spec
build_oom_containment_flags = build_oom_isolated_flags
build_oom_flags = build_oom_isolated_flags
apply_oom_isolated_flags = build_oom_isolated_flags
# [MC-QVM-17] FAILURE:Container OOM Crash
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'Container
# SPEC: OOM Crash': The container scheduler records an exit code of 137, captures memory slope, and
# SPEC: quarantines the task to prevent cascading VM exhaustion. Read State.OOMKilled and ExitCode via
# SPEC: docker inspect before removal; on exit 137 store the sampled docker stats memory series and its
# SPEC: slope, and mark the task QUARANTINED so the scheduler never retries it automatically.
OOM_EXIT_CODE: int = 137
_STATS_UNITS: dict[str, int] = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3}

def parse_inspect_oom_state(inspect_output: str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    """Reads State.OOMKilled / State.ExitCode from `docker inspect` JSON (list or single object)."""
    data = json.loads(inspect_output) if isinstance(inspect_output, str) else inspect_output
    if isinstance(data, list):
        if not data: raise ValueError("docker inspect returned no objects")
        data = data[0]
    state = data.get("State") if isinstance(data, dict) else None
    if not isinstance(state, dict): raise ValueError("docker inspect JSON lacks a State object")
    return {"oom_killed": bool(state.get("OOMKilled", False)), "exit_code": int(state.get("ExitCode", -1))}

def inspect_container_state(container: str) -> dict[str, Any]:
    """Runs the real `docker inspect` before the container is removed."""
    res = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0: raise RuntimeError(f"docker inspect {container} failed: {res.stderr.strip()}")
    return parse_inspect_oom_state(res.stdout)

def parse_stats_mem_usage(mem_usage: str) -> int:
    """Converts the `docker stats --format {{.MemUsage}}` left-hand value (e.g. '412.3MiB / 4GiB') to bytes."""
    m = re.match(r"^\s*([0-9.]+)\s*([A-Za-z]+)", mem_usage.split("/")[0])
    if not m or m.group(2).lower() not in _STATS_UNITS: raise ValueError(f"unparseable MemUsage {mem_usage!r}")
    return int(float(m.group(1)) * _STATS_UNITS[m.group(2).lower()])

def sample_container_memory(container: str) -> tuple[float, int]:
    """One (monotonic_time, bytes) sample from the real `docker stats --no-stream`."""
    res = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0: raise RuntimeError(f"docker stats {container} failed: {res.stderr.strip()}")
    return (time.monotonic(), parse_stats_mem_usage(res.stdout.strip()))

def compute_memory_slope(memory_series: list[tuple[float, int]]) -> float:
    """Least-squares slope in bytes/second of (t, bytes) samples; 0.0 for fewer than two distinct times."""
    pts = [(float(t), float(b)) for t, b in memory_series]
    if len(pts) < 2: return 0.0
    n = len(pts); mt = sum(t for t, _ in pts) / n; mb = sum(b for _, b in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    return 0.0 if den == 0 else sum((t - mt) * (b - mb) for t, b in pts) / den

def handle_container_oom_crash(task_id: str, inspect_output: str | dict[str, Any] | list[Any], memory_series: list[tuple[float, int]]) -> dict[str, Any]:
    """Records exit 137, the memory series and its slope, and quarantines the task from automatic retry."""
    if not task_id: raise ValueError("task_id must be non-empty")
    state = parse_inspect_oom_state(inspect_output)
    series = [(float(t), int(b)) for t, b in memory_series]
    rec: dict[str, Any] = {"task_id": task_id, "exit_code": state["exit_code"], "oom_killed": state["oom_killed"],
                           "memory_series": series, "memory_slope": compute_memory_slope(series), "peak_bytes": max((b for _, b in series), default=0)}
    if state["oom_killed"] or state["exit_code"] == OOM_EXIT_CODE:
        QUARANTINED_TASKS.add(task_id); rec["status"] = "QUARANTINED"; rec["retry_allowed"] = False
    else:
        rec["status"] = "EXITED"; rec["retry_allowed"] = True
    return rec

def is_task_quarantined(task_id: str) -> bool:
    return task_id in QUARANTINED_TASKS
# [MC-QVM-18] SRS-412-02-FR-008
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-02-FR-008: The
# SPEC: container engine shall purge all scratch volumes, tmpfs allocations, and intermediate layers
# SPEC: immediately upon container exit. --rm already removes the container; also remove any named
# SPEC: volumes the task created and run docker image prune --filter label=cochem.task=<id> for
# SPEC: intermediate layers. Purge runs in a finally block even when the container crashed.
TASK_LABEL_KEY: str = "cochem.task"

def task_label_filter(task_id: str) -> str:
    return f"label={TASK_LABEL_KEY}={task_id}"

def build_exit_purge_commands(task_id: str, volume_names: list[str] | None = None) -> list[list[str]]:
    """argv lists run after container exit: `docker volume rm` for task volumes, `docker image prune` for task layers."""
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id): raise ValueError(f"invalid task_id {task_id!r}")
    vols = list(volume_names or [])
    if any(not isinstance(v, str) or not v for v in vols): raise TypeError("volume_names must be non-empty strings")
    cmds: list[list[str]] = []
    if vols: cmds.append(["docker", "volume", "rm", "--force", *vols])
    cmds.append(["docker", "image", "prune", "--force", "--filter", task_label_filter(task_id)])
    return cmds

def purge_on_exit(task_id: str, volume_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Runs every purge command against the real daemon; failures are reported, never raised, so all steps run."""
    out: list[dict[str, Any]] = []
    for argv in build_exit_purge_commands(task_id, volume_names):
        res = subprocess.run(argv, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        out.append({"argv": argv, "returncode": res.returncode, "stdout": res.stdout.strip(), "stderr": res.stderr.strip()})
    return out

def verify_purge_layer_prune(task_id: str, dry_run: bool = True) -> list[str]:
    """Violations if any image or volume labelled cochem.task=<id> survives; dry_run only queries, else purges first."""
    if not dry_run: purge_on_exit(task_id)
    v: list[str] = []
    for kind in ("image", "volume"):
        res = subprocess.run(["docker", kind, "ls", "--quiet", "--filter", task_label_filter(task_id)], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if res.returncode != 0: v.append(f"docker {kind} ls failed: {res.stderr.strip()}"); continue
        left = [line for line in res.stdout.split() if line]
        if left: v.append(f"{len(left)} {kind}(s) labelled {TASK_LABEL_KEY}={task_id} survive: {left}")
    return v

def run_sandbox_with_purge(argv: list[str], task_id: str, volume_names: list[str] | None = None, timeout: float | None = None) -> dict[str, Any]:
    """Runs the ephemeral container; the purge executes in a finally block even if the container crashed."""
    if not verify_ephemeral_flags(argv): raise ValueError("argv lacks FR-003 mandatory flags")
    result: dict[str, Any] = {"task_id": task_id, "returncode": None, "stdout": "", "stderr": "", "purge": []}
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        result.update(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr)
        if res.returncode == OOM_EXIT_CODE: QUARANTINED_TASKS.add(task_id); result["status"] = "QUARANTINED"
    finally:
        result["purge"] = purge_on_exit(task_id, volume_names)
    return result
# [MC-QVM-19] FAILURE:Scratch Leak
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'Scratch
# SPEC: Leak': Automated cron job inspects `/tmp` mounts every 60 seconds and unlinks unmounted orphan
# SPEC: namespaces. A daemon thread wakes every 60 s, lists cochem-labelled scratch paths under /tmp
# SPEC: that are not in /proc/self/mountinfo, and removes them; it never touches a path that belongs to
# SPEC: a running container.
SCRATCH_SWEEP_INTERVAL_SEC: float = 60.0
SCRATCH_NAMESPACE_PREFIX: str = "cochem_scratch"
MOUNTINFO_PATH: str = "/proc/self/mountinfo"

def parse_mountinfo_mount_points(mountinfo_content: str) -> set[Path]:
    """Mount points (field 5) from /proc/self/mountinfo text, with \\040-style octal escapes decoded."""
    pts: set[Path] = set()
    for line in mountinfo_content.splitlines():
        f = line.split()
        if len(f) < 5: continue
        mp = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), f[4])
        pts.add(Path(mp).resolve())
    return pts

def read_mountinfo() -> str:
    p = Path(MOUNTINFO_PATH)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

def list_running_containers() -> set[str]:
    """IDs (full and short) and names of running containers from the real `docker ps`."""
    res = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0: raise RuntimeError(f"docker ps failed: {res.stderr.strip()}")
    ids: set[str] = set()
    for line in res.stdout.splitlines():
        for tok in line.split(): ids.add(tok); ids.add(tok[:12])
    return ids

def scan_orphan_scratch_namespaces(tmp_dir: str | Path = "/tmp", mountinfo_content: str | None = None, running_containers: set[str] | None = None) -> list[Path]:
    """cochem_scratch* directories under tmp_dir that are neither mounted nor owned by a running container."""
    root = Path(tmp_dir)
    if not root.is_dir(): return []
    mounted = parse_mountinfo_mount_points(read_mountinfo() if mountinfo_content is None else mountinfo_content)
    running = list_running_containers() if running_containers is None else set(running_containers)
    orphans: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not entry.name.startswith(SCRATCH_NAMESPACE_PREFIX): continue
        owner = entry.name[len(SCRATCH_NAMESPACE_PREFIX):].lstrip("_-")
        if entry.resolve() in mounted or (owner and owner in running): continue
        orphans.append(entry)
    return orphans

def sweep_orphan_scratch_namespaces(tmp_dir: str | Path = "/tmp", mountinfo_content: str | None = None, running_containers: set[str] | None = None) -> list[Path]:
    """Unlinks every orphan found by scan_orphan_scratch_namespaces and returns the removed paths."""
    swept: list[Path] = []
    for p in scan_orphan_scratch_namespaces(tmp_dir, mountinfo_content, running_containers):
        shutil.rmtree(p, ignore_errors=False)
        if not p.exists(): swept.append(p)
    return swept

class OrphanScratchSweeper:
    """Daemon thread that sweeps orphan scratch namespaces every 60 s until stop() is called."""
    def __init__(self, tmp_dir: str | Path = "/tmp", interval_sec: float = SCRATCH_SWEEP_INTERVAL_SEC) -> None:
        if interval_sec <= 0: raise ValueError("interval_sec must be positive")
        self.tmp_dir = Path(tmp_dir); self.interval_sec = interval_sec; self.sweeps: int = 0; self.last_swept: list[Path] = []
        self._stop = threading.Event(); self._thread = threading.Thread(target=self._loop, name="cochem-scratch-sweeper", daemon=True)
    def _loop(self) -> None:
        while not self._stop.is_set():
            try: self.last_swept = sweep_orphan_scratch_namespaces(self.tmp_dir)
            except Exception as exc: self.last_swept = []; self.last_error = repr(exc)
            self.sweeps += 1
            self._stop.wait(self.interval_sec)
    def start(self) -> OrphanScratchSweeper:
        self._thread.start(); return self
    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set(); self._thread.join(join_timeout)
    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

`
