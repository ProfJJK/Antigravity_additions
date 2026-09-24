"""TDD contract for CoChem Pipeline v2 Phase 2 monitors.

Targets (loaded strictly by path from this workspace, never from site-packages
or any other checkout):
    __agentic/v2/watchdog.py            (DaemonWatchdog, WatchdogDB)
    __agentic/v2/cochem_idle_sidecar.py (IdleSidecarDB, HardwareMonitor,
                                         HeavyTaskPolicyEngine)

Contract:
    T1 / AC1  A killed daemon is detected, ``DAEMON_CRASHED`` and a restart
              event (``DAEMON_RESTARTED`` or ``RESTART``) are logged into
              ``watchdog_events_v2`` and a fresh worker process is running.
    T2 / AC2  ``crash_threshold`` crashes inside ``window_seconds`` log
              ``SPAWN_STORM_DETECTED`` and stop all respawning; crashes spaced
              wider than the sliding window never trip the breaker.
    T3 / AC3  Heavy tasks are allowed only if the CPU is idle (proved with a
              real CPU load), quota is ``QUOTA_OK`` (read live from SQLite,
              including external writers) and daily spend + cost <= budget.
    T4 / AC4  Every subprocess call in both modules passes
              ``creationflags=CREATE_NO_WINDOW`` and ``encoding='utf-8'``; the
              daemon really gets a window-less console, and terminating the
              watchdog (in-process or by killing the supervisor process)
              leaves no orphaned worker or grandchild processes.
    T5 / AC5  No network/Docker/LLM client imports, no ``ollama`` routing,
              every fallback chain ends in ``('halt', 'graceful')``; a harness
              process guarded by ``sys.addaudithook`` exercises both modules
              and records zero network events.

No mocking or monkeypatching of any kind is used: real worker processes and
real SQLite databases are created under ``tmp_path``. Every subprocess call in
this file (and in the generated scripts) passes ``creationflags`` and
``encoding='utf-8'``.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Optional

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
V2_DIR = WORKSPACE_ROOT / "__agentic" / "v2"
if str(V2_DIR) not in sys.path:
    sys.path.append(str(V2_DIR))

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
HALT_SENTINEL = ("halt", "graceful")
RESTART_EVENT_TYPES = ("DAEMON_RESTARTED", "RESTART")
CHAIN_NAME_RE = re.compile(r"(FALLBACK|CHAIN|ROUT)", re.IGNORECASE)

SUBPROCESS_FUNCS = frozenset(
    {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
)
FORBIDDEN_OS_FUNCS = frozenset({"system", "popen"})
FORBIDDEN_OS_PREFIXES = ("spawn", "exec")
FORBIDDEN_ASYNC_FUNCS = frozenset({"create_subprocess_exec", "create_subprocess_shell"})
FORBIDDEN_IMPORTS = frozenset(
    {
        "requests", "httpx", "aiohttp", "urllib3", "urllib.request", "http.client",
        "socket", "ssl", "websockets", "docker", "grpc", "openai", "anthropic",
        "ollama", "google.generativeai", "ftplib", "smtplib", "xmlrpc",
    }
)
TARGET_FILES = ("watchdog.py", "cochem_idle_sidecar.py")


# ---------------------------------------------------------------------------
# Module loading (strictly by path from this workspace)
# ---------------------------------------------------------------------------


def _load_v2_module(stem: str) -> ModuleType:
    """Load ``__agentic/v2/<stem>.py`` under a unique name; ImportError if absent."""
    unique = f"_cochem_v2_{stem}"
    cached = sys.modules.get(unique)
    if cached is not None:
        return cached
    path = V2_DIR / f"{stem}.py"
    if not path.is_file():
        raise ImportError(f"CoChem v2 module not found: {path}")
    spec = importlib.util.spec_from_file_location(unique, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(unique, None)
        raise
    return module


def _get_watchdog() -> ModuleType:
    return _load_v2_module("watchdog")


def _get_idle_sidecar() -> ModuleType:
    return _load_v2_module("cochem_idle_sidecar")


def _source_path(filename: str) -> Path:
    path = V2_DIR / filename
    if not path.is_file():
        raise ImportError(f"CoChem v2 source not found: {path}")
    return path


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _pid_alive(pid: Optional[int]) -> bool:
    """Return True if ``pid`` refers to a running process."""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        process_query_limited_information = 0x1000
        still_active = 259
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _force_kill(pid: Optional[int]) -> None:
    """Kill ``pid`` if it is still running (TerminateProcess on Windows, SIGKILL on POSIX)."""
    if pid is None or not _pid_alive(pid):
        return
    kill_signal = signal.SIGTERM if os.name == "nt" else signal.SIGKILL
    with contextlib.suppress(OSError):
        os.kill(int(pid), kill_signal)


def _force_kill_all(pids: Iterable[Optional[int]]) -> None:
    for pid in pids:
        _force_kill(pid)


def _wait_until(predicate: Callable[[], bool], timeout: float = 15.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# ---------------------------------------------------------------------------
# Worker scripts
# ---------------------------------------------------------------------------

LOOPING_WORKER = '''
"""Looping test daemon: announces itself via ready_<pid>.json, optionally spawns a grandchild."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
SLEEP_LOOP = """
import time
while True:
    time.sleep(0.1)
"""


def write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def console_info() -> dict:
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetConsoleWindow.argtypes = []
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    kernel32.GetConsoleProcessList.restype = wintypes.DWORD
    buf = (wintypes.DWORD * 16)()
    count = kernel32.GetConsoleProcessList(buf, 16)
    return {"hwnd": kernel32.GetConsoleWindow() or 0, "console_procs": int(count)}


def main() -> None:
    ready_dir = Path(sys.argv[1])
    ready_dir.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    write_json_atomic(ready_dir / f"ready_{pid}.json", {"pid": pid, **console_info()})
    if "--grandchild" in sys.argv[2:]:
        child = subprocess.Popen(
            [sys.executable, "-c", SLEEP_LOOP],
            creationflags=_NO_WINDOW,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        write_json_atomic(ready_dir / f"grandchild_{pid}.json", {"pid": child.pid})
    while True:
        time.sleep(0.1)


main()
'''

CRASHING_WORKER = '''
"""Crash-looping test daemon: logs its PID, then exits immediately with code 42."""
import os
import sys

with open(sys.argv[1], "a", encoding="utf-8") as fh:
    print(os.getpid(), file=fh)
sys.exit(42)
'''

SLOW_CRASH_WORKER = '''
"""Slow-crashing test daemon: logs its PID, lives 0.6 s, then exits with code 3."""
import os
import sys
import time

with open(sys.argv[1], "a", encoding="utf-8") as fh:
    print(os.getpid(), file=fh)
time.sleep(0.6)
sys.exit(3)
'''

CPU_BURN = '''
import time
end = time.monotonic() + 20.0
counter = 0
while time.monotonic() < end:
    counter += 1
'''

SUPERVISOR_HARNESS = '''
"""Supervisor harness: runs DaemonWatchdog until it is signalled; always terminates it."""
import importlib.util
import sys
import time

module_path, db_path, ready_dir, worker = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("_cochem_v2_watchdog_supervisor", module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

wd = module.DaemonWatchdog(
    target_cmd=[sys.executable, worker, ready_dir, "--grandchild"],
    db_path=db_path,
    poll_interval=0.05,
)
wd.spawn_daemon()
try:
    while wd.poll_cycle() is not False:
        time.sleep(0.05)
finally:
    wd.terminate()
'''

OFFLINE_HARNESS = '''
"""Offline harness: audit-hook guarded run of watchdog.py and cochem_idle_sidecar.py."""
import sys

NETWORK_EVENTS = frozenset({
    "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyname_ex", "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg",
})
network_events: list = []
popen_records: list = []


def audit(event: str, args: tuple) -> None:
    if event in NETWORK_EVENTS:
        network_events.append([event, repr(args)])
        raise RuntimeError(f"network access forbidden in offline harness: {event}")
    if event == "subprocess.Popen":
        popen_records.append(repr((args[0], args[1])))


sys.addaudithook(audit)

import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def wait_until(predicate, timeout: float = 15.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def main() -> None:
    wd_path, side_path, work_dir, worker = sys.argv[1:5]
    work = Path(work_dir)
    ready_dir = work / "ready"
    ready_dir.mkdir(parents=True, exist_ok=True)
    probe_db = work / "offline_probe.db"
    daemon_db = work / "offline_daemon.db"
    side_db = work / "offline_sidecar.db"
    results: dict = {}
    try:
        wd_mod = load("_cochem_v2_watchdog_offline", wd_path)
        side_mod = load("_cochem_v2_sidecar_offline", side_path)

        wdb = wd_mod.WatchdogDB(probe_db)
        wdb.init_schema()
        wdb.log_event("kanban_worker", "OFFLINE_PROBE", pid=os.getpid(), details="offline")
        results["probe_rows"] = [
            {"event_type": r.get("event_type"), "daemon_name": r.get("daemon_name"), "pid": r.get("pid")}
            for r in wdb.get_events(event_type="OFFLINE_PROBE")
        ]

        wd = wd_mod.DaemonWatchdog(
            target_cmd=[sys.executable, worker, str(ready_dir)],
            db_path=daemon_db,
            poll_interval=0.05,
        )
        events_db = wd_mod.WatchdogDB(daemon_db)
        try:
            proc = wd.spawn_daemon()
            results["worker_pid"] = proc.pid
            results["worker_ready"] = wait_until(lambda: (ready_dir / f"ready_{proc.pid}.json").exists())
            results["first_poll_ok"] = bool(wd.poll_cycle())
            proc.kill()
            proc.wait(timeout=15)

            def crash_and_restart() -> bool:
                wd.poll_cycle()
                crashed = events_db.get_events(event_type="DAEMON_CRASHED")
                restarted = [
                    r for t in ("DAEMON_RESTARTED", "RESTART") for r in events_db.get_events(event_type=t)
                ]
                return bool(crashed) and bool(restarted)

            results["crash_and_restart"] = wait_until(crash_and_restart, timeout=15.0, step=0.1)
            results["crashed_pids"] = [r.get("pid") for r in events_db.get_events(event_type="DAEMON_CRASHED")]
        finally:
            wd.terminate()

        sdb = side_mod.IdleSidecarDB(side_db, daily_budget=25.0)
        sdb.init_schema()
        sdb.set_quota_status("QUOTA_OK")
        engine = side_mod.HeavyTaskPolicyEngine(sdb, side_mod.HardwareMonitor(cpu_threshold=100.0))
        results["can_execute_heavy"] = engine.can_execute_heavy(estimated_cost=0.10)
    finally:
        report = {
            "harness_pid": os.getpid(),
            "network_events": list(network_events),
            "popen_records": list(popen_records),
            "results": results,
            "db_files": [str(p) for p in (probe_db, daemon_db, side_db)],
        }
        with open(work / "offline_report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)


main()
'''


def _write_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _json_pids(directory: Path, pattern: str) -> set[int]:
    pids: set[int] = set()
    for path in directory.glob(pattern):
        data = _read_json(path)
        if data is not None and isinstance(data.get("pid"), int):
            pids.add(data["pid"])
    return pids


def _ready_pids(ready_dir: Path) -> set[int]:
    """PIDs of every worker that announced itself in ``ready_dir``."""
    return _json_pids(ready_dir, "ready_*.json")


def _grandchild_pids(ready_dir: Path) -> set[int]:
    """PIDs of every grandchild reported by a worker in ``ready_dir``."""
    return _json_pids(ready_dir, "grandchild_*.json")


def _all_spawned_pids(ready_dir: Path) -> list[Optional[int]]:
    if not ready_dir.is_dir():
        return []
    return [*_ready_pids(ready_dir), *_grandchild_pids(ready_dir)]


def _launch_log_pids(launch_log: Path) -> list[int]:
    if not launch_log.exists():
        return []
    with open(launch_log, "r", encoding="utf-8") as fh:
        return [int(token) for token in fh.read().split()]


# ---------------------------------------------------------------------------
# SQLite helpers (short-lived connections)
# ---------------------------------------------------------------------------


def _events(db_path: Path, event_types: Optional[tuple[str, ...]] = None) -> list[dict[str, Any]]:
    """Rows of ``watchdog_events_v2``; a missing table counts as zero rows."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM watchdog_events_v2").fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        conn.close()
    if event_types is None:
        return rows
    return [r for r in rows if r.get("event_type") in event_types]


def _count(db_path: Path, event_types: tuple[str, ...]) -> int:
    return len(_events(db_path, event_types))


def _quota_status_column(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(quota_state)").fetchall()}
    finally:
        conn.close()
    for candidate in ("status", "state"):
        if candidate in columns:
            return candidate
    raise AssertionError(f"quota_state has neither a status nor a state column: {sorted(columns)}")


def _external_quota_write(db_path: Path, status: str) -> None:
    """Update the quota row from a separate connection, as another process would."""
    column = _quota_status_column(db_path)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        cur = conn.execute(f"UPDATE quota_state SET {column} = ? WHERE provider = 'default'", (status,))
        conn.commit()
        assert cur.rowcount == 1, "external writer found no quota_state row for provider 'default'"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    with open(path, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=str(path))


def _subprocess_calls(tree: ast.Module) -> list[tuple[str, ast.Call]]:
    """Every call to a subprocess spawning function, as (function name, node)."""
    module_aliases: set[str] = {"subprocess"}
    direct_names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_aliases.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_FUNCS:
                    direct_names[alias.asname or alias.name] = alias.name

    calls: list[tuple[str, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ):
            calls.append((func.attr, node))
        elif isinstance(func, ast.Name) and func.id in direct_names:
            calls.append((direct_names[func.id], node))
    return calls


def _mentions_create_no_window(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "CREATE_NO_WINDOW")
        or (isinstance(n, ast.Attribute) and n.attr == "CREATE_NO_WINDOW")
        for n in ast.walk(node)
    )


def _forbidden_process_calls(tree: ast.Module) -> list[str]:
    """os.system/popen/spawn*/exec* and asyncio subprocess calls, as 'line: name'."""
    os_aliases: set[str] = {"os"}
    direct_os: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                direct_os[alias.asname or alias.name] = alias.name

    def _is_forbidden_os(name: str) -> bool:
        return name in FORBIDDEN_OS_FUNCS or name.startswith(FORBIDDEN_OS_PREFIXES)

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in os_aliases and _is_forbidden_os(func.attr):
                hits.append(f"{node.lineno}: os.{func.attr}")
            if func.attr in FORBIDDEN_ASYNC_FUNCS:
                hits.append(f"{node.lineno}: {func.attr}")
        elif isinstance(func, ast.Name):
            if func.id in direct_os and _is_forbidden_os(direct_os[func.id]):
                hits.append(f"{node.lineno}: os.{direct_os[func.id]}")
            if func.id in FORBIDDEN_ASYNC_FUNCS:
                hits.append(f"{node.lineno}: {func.id}")
    return hits


def _module_assignment(tree: ast.Module, name: str) -> Optional[ast.expr]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
                return node.value
    return None


def _string_constants(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, sub in value.items():
            out.extend(_flatten_strings(key))
            out.extend(_flatten_strings(sub))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        out = []
        for sub in value:
            out.extend(_flatten_strings(sub))
        return out
    return []


def _is_sentinel_node(node: ast.expr) -> bool:
    if isinstance(node, ast.Name) and node.id == "HALT_SENTINEL":
        return True
    if isinstance(node, ast.Tuple):
        return [getattr(e, "value", None) for e in node.elts] == list(HALT_SENTINEL)
    return False


def _assert_chain_terminates(name: str, chain: Any) -> None:
    """Recursively assert a runtime chain (or dict of chains) ends in HALT_SENTINEL, without ollama."""
    if isinstance(chain, dict):
        for key, sub in chain.items():
            _assert_chain_terminates(f"{name}[{key!r}]", sub)
        return
    lowered = [s.lower() for s in _flatten_strings(chain)]
    assert not any("ollama" in s for s in lowered), f"{name} routes to ollama: {chain!r}"
    if isinstance(chain, (list, tuple)) and chain:
        if tuple(chain) == HALT_SENTINEL:
            return
        last = chain[-1]
        assert isinstance(last, (list, tuple)) and tuple(last) == HALT_SENTINEL, (
            f"{name} fallback chain must end in {HALT_SENTINEL}, got {last!r}"
        )


def _forbidden_imports(tree: ast.Module) -> list[str]:
    def _hit(name: str) -> bool:
        return name in FORBIDDEN_IMPORTS or name.split(".")[0] in FORBIDDEN_IMPORTS

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(f"{node.lineno}: {a.name}" for a in node.names if _hit(a.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _hit(node.module):
                hits.append(f"{node.lineno}: {node.module}")
            hits.extend(
                f"{node.lineno}: {node.module}.{a.name}"
                for a in node.names
                if f"{node.module}.{a.name}" in FORBIDDEN_IMPORTS
            )
    return hits


# ---------------------------------------------------------------------------
# [T1] AC1 - crash detection and restart
# ---------------------------------------------------------------------------


def test_watchdog_detects_crash_and_restarts(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()

    worker = _write_script(tmp_path, "kanban_worker_loop.py", LOOPING_WORKER)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    db_path = tmp_path / "watchdog_v2.db"

    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker), str(ready_dir)],
        db_path=db_path,
        crash_threshold=3,
        window_seconds=30,
        poll_interval=0.05,
        daemon_name="kanban_worker",
    )
    try:
        # 1. Spawn.
        proc = wd.spawn_daemon()
        assert isinstance(proc, subprocess.Popen), "spawn_daemon must return subprocess.Popen"
        initial_pid = proc.pid
        assert _wait_until(lambda: (ready_dir / f"ready_{initial_pid}.json").exists()), (
            "worker never wrote its ready file"
        )
        assert _pid_alive(initial_pid)

        # 2. Healthy phase: no false crash detection, no respawn.
        for _ in range(3):
            assert wd.poll_cycle(), "poll_cycle must be truthy while the daemon is healthy"
            time.sleep(0.05)
        assert _count(db_path, ("DAEMON_CRASHED",)) == 0, "healthy daemon reported as crashed"
        assert _ready_pids(ready_dir) == {initial_pid}, "watchdog spawned extra workers while healthy"

        # 3. Crash the daemon externally.
        proc.kill()
        proc.wait(timeout=15)

        def _crash_and_restart_logged() -> bool:
            wd.poll_cycle()
            return _count(db_path, ("DAEMON_CRASHED",)) >= 1 and _count(db_path, RESTART_EVENT_TYPES) >= 1

        assert _wait_until(_crash_and_restart_logged, timeout=15.0, step=0.05), (
            "watchdog did not log DAEMON_CRASHED + restart events"
        )

        # 4. Crash row, restart row, and a genuinely new running worker.
        crashed = _events(db_path, ("DAEMON_CRASHED",))
        assert any(r.get("pid") == initial_pid and r.get("daemon_name") == "kanban_worker" for r in crashed), (
            f"no DAEMON_CRASHED row for pid {initial_pid} / kanban_worker: {crashed!r}"
        )
        assert _events(db_path, RESTART_EVENT_TYPES), "no DAEMON_RESTARTED/RESTART row"

        assert _wait_until(lambda: len(_ready_pids(ready_dir) - {initial_pid}) >= 1), (
            "restarted worker never wrote its ready file"
        )
        new_pids = _ready_pids(ready_dir) - {initial_pid}
        assert len(new_pids) == 1, f"expected exactly one restarted worker, got {new_pids}"
        second_pid = next(iter(new_pids))
        assert second_pid != initial_pid
        assert _pid_alive(second_pid), "restarted worker is not running"

        # 5. Second crash: still below the breaker threshold.
        os.kill(second_pid, signal.SIGTERM)
        poll_results: list[bool] = []

        def _second_restart() -> bool:
            poll_results.append(bool(wd.poll_cycle()))
            return (
                _count(db_path, ("DAEMON_CRASHED",)) >= 2
                and len(_ready_pids(ready_dir) - {initial_pid, second_pid}) >= 1
            )

        assert _wait_until(_second_restart, timeout=15.0, step=0.05), (
            "watchdog did not recover from the second crash"
        )
        assert all(poll_results), "poll_cycle returned falsy below the crash threshold"
        third_pids = _ready_pids(ready_dir) - {initial_pid, second_pid}
        assert len(third_pids) == 1, f"expected exactly one third worker, got {third_pids}"
        third_pid = next(iter(third_pids))
        assert _pid_alive(third_pid), "third worker is not running"
        assert _count(db_path, ("SPAWN_STORM_DETECTED",)) == 0, "2 crashes < threshold 3 must not trip"
        assert wd.poll_cycle(), "watchdog must keep supervising after 2 crashes"

        # 6. Public API reflects the same rows.
        api_rows = wd_mod.WatchdogDB(db_path).get_events(event_type="DAEMON_CRASHED")
        assert isinstance(api_rows, list) and len(api_rows) >= 2
        assert all(isinstance(r, dict) for r in api_rows)
        assert any(r.get("pid") == initial_pid for r in api_rows)
    finally:
        wd.terminate()
        _force_kill_all(_all_spawned_pids(ready_dir))


# ---------------------------------------------------------------------------
# [T2] AC2 - spawn storm circuit breaker
# ---------------------------------------------------------------------------


def test_watchdog_spawn_storm_circuit_breaker(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()

    # Part A: rapid crash loop trips the breaker and stops respawning.
    crashing = _write_script(tmp_path, "crashing_worker.py", CRASHING_WORKER)
    storm_log = tmp_path / "storm_launches.log"
    storm_db = tmp_path / "watchdog_storm.db"
    storm_wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(crashing), str(storm_log)],
        db_path=storm_db,
        crash_threshold=3,
        window_seconds=10,
        poll_interval=0.01,
        daemon_name="kanban_worker",
    )
    try:
        assert isinstance(storm_wd.spawn_daemon(), subprocess.Popen)
        tripped = False
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if storm_wd.poll_cycle() is False:
                tripped = True
                break
            time.sleep(0.02)
        assert tripped, "circuit breaker never tripped on a crash-looping worker"

        storm_rows = _events(storm_db, ("SPAWN_STORM_DETECTED",))
        assert any(r.get("daemon_name") == "kanban_worker" for r in storm_rows), (
            f"SPAWN_STORM_DETECTED not logged for kanban_worker: {storm_rows!r}"
        )
        crashes = _count(storm_db, ("DAEMON_CRASHED",))
        assert 3 <= crashes <= 4, f"breaker should trip after ~3 rapid crashes, saw {crashes}"
        launches = _launch_log_pids(storm_log)
        assert 3 <= len(launches) <= 4, f"expected 3-4 launches before tripping, saw {len(launches)}"
        assert len(launches) == len(set(launches)), f"launch log has duplicate PIDs: {launches}"

        restarts = _count(storm_db, RESTART_EVENT_TYPES)
        for _ in range(10):
            assert storm_wd.poll_cycle() is False, "breaker reset itself after tripping"
            time.sleep(0.1)
        assert len(_launch_log_pids(storm_log)) == len(launches), "watchdog respawned after the storm"
        assert _count(storm_db, ("DAEMON_CRASHED",)) == crashes
        assert _count(storm_db, RESTART_EVENT_TYPES) == restarts
        assert _wait_until(lambda: not any(_pid_alive(p) for p in launches), timeout=5.0), (
            "a crash-loop worker is still alive after the breaker tripped"
        )
    finally:
        storm_wd.terminate()
        _force_kill_all(list(_launch_log_pids(storm_log)))

    # Part B: crashes spaced wider than the sliding window never trip the breaker.
    slow = _write_script(tmp_path, "slow_crash_worker.py", SLOW_CRASH_WORKER)
    slow_log = tmp_path / "slow_launches.log"
    slow_db = tmp_path / "watchdog_window.db"
    slow_wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(slow), str(slow_log)],
        db_path=slow_db,
        crash_threshold=3,
        window_seconds=0.3,
        poll_interval=0.05,
        daemon_name="kanban_worker",
    )
    try:
        slow_wd.spawn_daemon()
        results: list[bool] = []

        def _four_crashes() -> bool:
            results.append(bool(slow_wd.poll_cycle()))
            return _count(slow_db, ("DAEMON_CRASHED",)) >= 4

        assert _wait_until(_four_crashes, timeout=20.0, step=0.05), "slow-crash worker did not crash 4 times"
        assert all(results), "poll_cycle tripped although crashes were outside the sliding window"
        assert _count(slow_db, ("SPAWN_STORM_DETECTED",)) == 0, "false SPAWN_STORM_DETECTED"
    finally:
        slow_wd.terminate()
        _force_kill_all(list(_launch_log_pids(slow_log)))


# ---------------------------------------------------------------------------
# [T3] AC3 - CPU and credit gating
# ---------------------------------------------------------------------------


def test_idle_sidecar_cpu_and_credit_gating(tmp_path: Path) -> None:
    side = _get_idle_sidecar()

    assert side.DEFAULT_IDLE_CPU_THRESHOLD == 25.0
    assert side.DEFAULT_DAILY_CREDIT_BUDGET == 25.00
    assert side.DEFAULT_HEAVY_COST_THRESHOLD == 0.05

    db_path = tmp_path / "sidecar_v2.db"
    db = side.IdleSidecarDB(db_path, daily_budget=25.0)
    db.init_schema()

    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert str(mode).lower() == "wal", f"expected WAL journal_mode, got {mode!r}"
    assert {"quota_state", "credit_ledger_v2"} <= tables, f"missing tables: {tables!r}"

    idle_monitor = side.HardwareMonitor(cpu_threshold=100.0)
    idle_engine = side.HeavyTaskPolicyEngine(db, idle_monitor)
    cpu = idle_monitor.get_cpu_percent(interval=0.05)
    assert isinstance(cpu, float) and 0.0 <= cpu <= 100.0
    assert idle_monitor.is_cpu_idle(interval=0.05) is True

    # Quota via the public API.
    db.set_quota_status("QUOTA_EXHAUSTED")
    assert db.get_quota_status() == "QUOTA_EXHAUSTED"
    assert idle_engine.can_execute_heavy(estimated_cost=0.10) is False
    db.set_quota_status("QUOTA_OK")
    assert db.get_quota_status() == "QUOTA_OK"
    assert idle_engine.can_execute_heavy(estimated_cost=0.10) is True

    # Quota via an external writer: the engine must read the ledger live.
    _external_quota_write(db_path, "QUOTA_EXHAUSTED")
    assert db.get_quota_status() == "QUOTA_EXHAUSTED"
    assert idle_engine.can_execute_heavy(estimated_cost=0.10) is False
    _external_quota_write(db_path, "QUOTA_OK")
    assert db.get_quota_status() == "QUOTA_OK"
    assert idle_engine.can_execute_heavy(estimated_cost=0.10) is True

    # Budget headroom on a fresh ledger.
    budget_path = tmp_path / "budget.db"
    budget_db = side.IdleSidecarDB(budget_path, daily_budget=25.0)
    budget_db.init_schema()
    budget_db.set_quota_status("QUOTA_OK")
    budget_engine = side.HeavyTaskPolicyEngine(budget_db, idle_monitor)
    assert budget_db.get_daily_spend() == pytest.approx(0.0)
    budget_db.record_spend("task-1", "cochem-coder", 24.0)
    assert budget_db.get_daily_spend() == pytest.approx(24.0)
    conn = sqlite3.connect(str(budget_path), timeout=10)
    try:
        ledger_rows = conn.execute("SELECT COUNT(*) FROM credit_ledger_v2").fetchone()[0]
    finally:
        conn.close()
    assert ledger_rows == 1, f"record_spend should persist exactly one ledger row, found {ledger_rows}"
    assert budget_engine.can_execute_heavy(estimated_cost=0.5) is True
    assert budget_engine.can_execute_heavy(estimated_cost=1.5) is False
    budget_db.record_spend("task-2", "cochem-coder", 2.0)
    assert budget_db.get_daily_spend() == pytest.approx(26.0)
    assert budget_engine.can_execute_heavy(estimated_cost=0.10) is False

    # Heavy-task classification by cost threshold.
    assert idle_engine.is_heavy_task(estimated_cost=0.0) is False
    assert idle_engine.is_heavy_task(estimated_cost=0.01) is False
    assert idle_engine.is_heavy_task(estimated_cost=side.DEFAULT_HEAVY_COST_THRESHOLD) is True
    assert idle_engine.is_heavy_task(estimated_cost=1.0) is True

    # Real CPU load: heavy work is refused while busy and allowed once idle again.
    busy_monitor = side.HardwareMonitor(cpu_threshold=50.0)
    busy_engine = side.HeavyTaskPolicyEngine(db, busy_monitor)
    load: list[subprocess.Popen] = []

    def _stop_load() -> None:
        for p in load:
            if p.poll() is None:
                p.kill()
        for p in load:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=10)

    try:
        for _ in range(max(2, os.cpu_count() or 1)):
            load.append(
                subprocess.Popen(
                    [sys.executable, "-c", CPU_BURN],
                    creationflags=_NO_WINDOW,
                    encoding="utf-8",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        time.sleep(0.5)
        loaded_cpu = busy_monitor.get_cpu_percent(interval=0.5)
        assert loaded_cpu > 50.0, f"precondition: CPU load {loaded_cpu:.1f}% did not exceed 50%"
        assert busy_monitor.is_cpu_idle(interval=0.3) is False
        assert db.get_quota_status() == "QUOTA_OK"
        assert busy_engine.can_execute_heavy(estimated_cost=0.10) is False, "heavy task allowed under load"
        _stop_load()
        assert _wait_until(lambda: busy_engine.can_execute_heavy(estimated_cost=0.10), timeout=20.0, step=0.2), (
            "heavy task still refused after the CPU load was removed"
        )
    finally:
        _stop_load()


# ---------------------------------------------------------------------------
# [T4] AC4 - subprocess flags and clean termination
# ---------------------------------------------------------------------------


def test_subprocess_flags_and_clean_termination(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()
    side = _get_idle_sidecar()

    # A) Static: every subprocess call carries CREATE_NO_WINDOW and utf-8.
    popen_in_watchdog = 0
    for fname in TARGET_FILES:
        tree = _parse(_source_path(fname))
        for func_name, call in _subprocess_calls(tree):
            where = f"{fname}:{call.lineno} subprocess.{func_name}"
            if fname == "watchdog.py" and func_name == "Popen":
                popen_in_watchdog += 1
            assert all(k.arg is not None for k in call.keywords), f"{where} uses **kwargs (flags unverifiable)"
            kw = {k.arg: k.value for k in call.keywords}
            assert "creationflags" in kw, f"{where} missing creationflags"
            assert _mentions_create_no_window(kw["creationflags"]), f"{where} creationflags lacks CREATE_NO_WINDOW"
            assert "encoding" in kw, f"{where} missing encoding"
            enc = kw["encoding"]
            assert isinstance(enc, ast.Constant) and isinstance(enc.value, str), (
                f"{where} encoding must be a string literal"
            )
            assert enc.value.lower() in ("utf-8", "utf8"), f"{where} encoding must be utf-8, got {enc.value!r}"
        forbidden = _forbidden_process_calls(tree)
        assert not forbidden, f"{fname} uses forbidden process APIs: {forbidden}"
    assert popen_in_watchdog >= 1, "watchdog.py must spawn the daemon via subprocess.Popen"
    assert wd_mod.CREATE_NO_WINDOW == 0x08000000
    assert side.CREATE_NO_WINDOW == 0x08000000

    worker = _write_script(tmp_path, "term_worker.py", LOOPING_WORKER)

    # B) In-process: window-less console, tree terminated without orphans.
    ready_b = tmp_path / "ready_inproc"
    ready_b.mkdir()
    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker), str(ready_b), "--grandchild"],
        db_path=tmp_path / "watchdog_term.db",
        poll_interval=0.05,
    )
    try:
        proc = wd.spawn_daemon()
        worker_pid = proc.pid
        grand_file = ready_b / f"grandchild_{worker_pid}.json"
        assert _wait_until(lambda: grand_file.exists()), "worker never reported its grandchild"
        info = _read_json(ready_b / f"ready_{worker_pid}.json")
        grand = _read_json(grand_file)
        assert info is not None and grand is not None
        grandchild_pid = int(grand["pid"])
        assert _pid_alive(worker_pid) and _pid_alive(grandchild_pid)
        if os.name == "nt":
            assert info.get("hwnd") == 0, f"daemon has a console window (hwnd={info.get('hwnd')})"
            assert info.get("console_procs") == 1, (
                f"daemon shares a console with {info.get('console_procs')} processes; CREATE_NO_WINDOW not applied"
            )
        wd.terminate()
        assert _wait_until(lambda: not _pid_alive(worker_pid) and not _pid_alive(grandchild_pid), timeout=15.0), (
            "terminate() left the worker or its grandchild running (orphans)"
        )
        wd.terminate()
    finally:
        wd.terminate()
        _force_kill_all(_all_spawned_pids(ready_b))

    # C) Supervisor process signalled: its daemon tree must die with it.
    ready_c = tmp_path / "ready_supervisor"
    ready_c.mkdir()
    harness = _write_script(tmp_path, "supervisor_harness.py", SUPERVISOR_HARNESS)
    out_path = tmp_path / "supervisor_stdout.txt"
    err_path = tmp_path / "supervisor_stderr.txt"
    supervisor: Optional[subprocess.Popen] = None
    with open(out_path, "w", encoding="utf-8") as out_fh, open(err_path, "w", encoding="utf-8") as err_fh:
        try:
            supervisor = subprocess.Popen(
                [
                    sys.executable, str(harness), str(_source_path("watchdog.py")),
                    str(tmp_path / "watchdog_supervisor.db"), str(ready_c), str(worker),
                ],
                creationflags=_NO_WINDOW,
                encoding="utf-8",
                stdout=out_fh,
                stderr=err_fh,
            )
            assert _wait_until(lambda: bool(_ready_pids(ready_c)) and bool(_grandchild_pids(ready_c)), timeout=20.0), (
                "supervised worker/grandchild never became ready"
            )
            sup_worker = next(iter(_ready_pids(ready_c)))
            sup_grand = next(iter(_grandchild_pids(ready_c)))
            assert _pid_alive(sup_worker) and _pid_alive(sup_grand)

            os.kill(supervisor.pid, signal.SIGTERM if os.name == "nt" else signal.SIGINT)
            try:
                supervisor.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pytest.fail("supervisor harness did not exit within 15 s of being signalled")
            assert _wait_until(lambda: not _pid_alive(sup_worker) and not _pid_alive(sup_grand), timeout=15.0), (
                "worker/grandchild survived the supervisor's termination (orphaned processes)"
            )
        finally:
            if supervisor is not None and supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=10)
            _force_kill_all(_all_spawned_pids(ready_c))


# ---------------------------------------------------------------------------
# [T5] AC5 - offline isolation and routing sentinels
# ---------------------------------------------------------------------------


def test_offline_isolation_and_routing_sentinels(tmp_path: Path) -> None:
    modules = {"watchdog.py": _get_watchdog(), "cochem_idle_sidecar.py": _get_idle_sidecar()}

    # A) Static and runtime routing/offline checks.
    for fname, mod in modules.items():
        tree = _parse(_source_path(fname))

        assert tuple(mod.HALT_SENTINEL) == HALT_SENTINEL, f"{fname}: HALT_SENTINEL wrong"
        assert "ollama" in mod.FORBIDDEN_PROVIDERS, f"{fname}: FORBIDDEN_PROVIDERS lacks ollama"

        halt_node = _module_assignment(tree, "HALT_SENTINEL")
        assert halt_node is not None, f"{fname}: HALT_SENTINEL not defined at module level"
        assert tuple(ast.literal_eval(halt_node)) == HALT_SENTINEL

        forbidden_node = _module_assignment(tree, "FORBIDDEN_PROVIDERS")
        assert forbidden_node is not None, f"{fname}: FORBIDDEN_PROVIDERS not defined at module level"
        assert "ollama" in _string_constants(forbidden_node)

        allowed = {id(n) for n in ast.walk(forbidden_node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "11434" not in node.value, f"{fname}:{node.lineno} references the ollama port 11434"
                if id(node) not in allowed:
                    assert "ollama" not in node.value.lower(), (
                        f"{fname}:{node.lineno} mentions ollama outside FORBIDDEN_PROVIDERS"
                    )

        for node in tree.body:
            targets: list[ast.expr] = []
            value: Optional[ast.expr] = None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            for tgt in targets:
                if not (isinstance(tgt, ast.Name) and CHAIN_NAME_RE.search(tgt.id)):
                    continue
                if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
                    is_sentinel_itself = [getattr(e, "value", None) for e in value.elts] == list(HALT_SENTINEL)
                    assert is_sentinel_itself or _is_sentinel_node(value.elts[-1]), (
                        f"{fname}:{node.lineno} {tgt.id} must end in HALT_SENTINEL"
                    )

        for name, val in vars(mod).items():
            if not name.startswith("__") and CHAIN_NAME_RE.search(name) and isinstance(val, (list, tuple, dict)):
                _assert_chain_terminates(f"{fname}:{name}", val)

        bad_imports = _forbidden_imports(tree)
        assert not bad_imports, f"{fname} imports network/docker/LLM libraries: {bad_imports}"

    # B) Runtime offline proof in an audit-hook guarded harness process.
    worker = _write_script(tmp_path, "offline_worker.py", LOOPING_WORKER)
    harness = _write_script(tmp_path, "offline_harness.py", OFFLINE_HARNESS)
    ready_dir = tmp_path / "ready"
    env = dict(os.environ)
    env.update(
        {
            "DOCKER_HOST": "tcp://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
    )
    try:
        completed = subprocess.run(
            [
                sys.executable, str(harness), str(_source_path("watchdog.py")),
                str(_source_path("cochem_idle_sidecar.py")), str(tmp_path), str(worker),
            ],
            creationflags=_NO_WINDOW,
            encoding="utf-8",
            capture_output=True,
            timeout=90,
            env=env,
            check=False,
        )
        assert completed.returncode == 0, f"offline harness failed:\n{completed.stderr}"
        report = _read_json(tmp_path / "offline_report.json")
        assert report is not None, "offline harness wrote no report"

        assert report["network_events"] == [], f"network access attempted: {report['network_events']!r}"
        popen_records: list[str] = report["popen_records"]
        assert popen_records, "audit hook recorded no subprocess.Popen events"
        for record in popen_records:
            lowered = record.lower()
            for banned in ("docker", "ollama", "curl", "wget"):
                assert banned not in lowered, f"forbidden subprocess ({banned}): {record}"

        results: dict[str, Any] = report["results"]
        harness_pid = report["harness_pid"]
        assert any(
            r.get("event_type") == "OFFLINE_PROBE" and r.get("pid") == harness_pid for r in results["probe_rows"]
        ), f"OFFLINE_PROBE row missing for harness pid {harness_pid}: {results['probe_rows']!r}"
        assert results["worker_ready"] is True
        assert results["first_poll_ok"] is True
        assert results["crash_and_restart"] is True, "harness did not observe DAEMON_CRASHED + restart"
        assert results["worker_pid"] in results["crashed_pids"]
        assert results["can_execute_heavy"] is True

        tmp_resolved = tmp_path.resolve()
        for db_file in report["db_files"]:
            db = Path(db_file)
            assert db.exists(), f"missing db file {db}"
            assert tmp_resolved in db.resolve().parents, f"db file outside tmp_path: {db}"
    finally:
        _force_kill_all(_all_spawned_pids(ready_dir))
