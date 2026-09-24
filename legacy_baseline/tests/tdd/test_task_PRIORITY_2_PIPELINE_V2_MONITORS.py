"""TDD contract for CoChem Pipeline v2 Phase 2 monitors.

Targets:
    __agentic/v2/watchdog.py            (DaemonWatchdog, WatchdogDB)
    __agentic/v2/cochem_idle_sidecar.py (IdleSidecarDB, HardwareMonitor,
                                         HeavyTaskPolicyEngine)

Both modules are resolved ONLY from WORKSPACE_ROOT/__agentic/v2, where
WORKSPACE_ROOT is two directories above this file. No other checkout or
install location is consulted. Every test is fully offline: real worker
subprocesses and real SQLite databases are created under tmp_path; no
network, no Docker.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
import json
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
V2_DIR = WORKSPACE_ROOT / "__agentic" / "v2"

for _p in (WORKSPACE_ROOT, V2_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
HALT_SENTINEL = ("halt", "graceful")

SUBPROCESS_FUNCS = {"Popen", "run", "call", "check_call", "check_output"}
FORBIDDEN_IMPORTS = {
    "requests",
    "httpx",
    "aiohttp",
    "docker",
    "urllib.request",
    "urllib3",
    "http.client",
    "websockets",
}
RESTART_EVENT_TYPES = ("DAEMON_RESTARTED", "RESTART")
CHAIN_NAME_RE = re.compile(r"(FALLBACK|CHAIN|ROUT)", re.IGNORECASE)

_MODULE_CACHE: dict[str, ModuleType] = {}


# ---------------------------------------------------------------------------
# Module / file resolution
# ---------------------------------------------------------------------------


def _find_target_file(name: str) -> Path:
    cand = V2_DIR / name
    if cand.is_file():
        return cand
    raise FileNotFoundError(f"{name} not found in {V2_DIR}")


def _is_in_v2_dir(mod: ModuleType) -> bool:
    mod_file = getattr(mod, "__file__", None)
    if not mod_file:
        return False
    return Path(mod_file).resolve().parent == V2_DIR.resolve()


def _load_module(stem: str, marker_attr: str) -> ModuleType:
    if stem in _MODULE_CACHE:
        return _MODULE_CACHE[stem]

    errors: list[str] = []

    try:
        mod = importlib.import_module(f"__agentic.v2.{stem}")
        if not _is_in_v2_dir(mod):
            errors.append(
                f"__agentic.v2.{stem}: resolved outside {V2_DIR} ({getattr(mod, '__file__', '?')})"
            )
        elif hasattr(mod, marker_attr):
            _MODULE_CACHE[stem] = mod
            return mod
        else:
            errors.append(f"__agentic.v2.{stem}: missing {marker_attr}")
    except ImportError as exc:
        errors.append(f"__agentic.v2.{stem}: {exc!r}")

    # Load by physical path to avoid clashing with same-named PyPI packages
    # (e.g. the third-party ``watchdog`` filesystem-events library).
    try:
        path = _find_target_file(f"{stem}.py")
        unique = f"_cochem_v2_{stem}"
        spec = importlib.util.spec_from_file_location(unique, path)
        if spec is not None and spec.loader is not None:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[unique] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, marker_attr):
                _MODULE_CACHE[stem] = mod
                return mod
            errors.append(f"{path}: missing {marker_attr}")
    except FileNotFoundError as exc:
        errors.append(str(exc))

    raise ImportError(f"Unable to import CoChem v2 module '{stem}': " + " | ".join(errors))


def _get_watchdog() -> ModuleType:
    return _load_module("watchdog", "DaemonWatchdog")


def _get_idle_sidecar() -> ModuleType:
    return _load_module("cochem_idle_sidecar", "HeavyTaskPolicyEngine")


def _module_source_path(mod: ModuleType, filename: str) -> Path:
    path = _find_target_file(filename)
    mod_file = getattr(mod, "__file__", None)
    assert mod_file and Path(mod_file).resolve() == path.resolve(), (
        f"loaded module {mod_file!r} is not {path}"
    )
    return path


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _pid_alive(pid: Optional[int]) -> bool:
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
    if _pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            return


def _wait_until(predicate: Callable[[], bool], timeout: float = 15.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def _current_pid(wd: Any) -> Optional[int]:
    for attr in (
        "process",
        "proc",
        "_process",
        "_proc",
        "daemon_process",
        "_daemon_process",
        "daemon_proc",
        "_daemon_proc",
        "child",
    ):
        obj = getattr(wd, attr, None)
        pid = getattr(obj, "pid", None)
        if isinstance(pid, int):
            return pid
    for attr in ("current_pid", "pid"):
        pid = getattr(wd, attr, None)
        if isinstance(pid, int):
            return pid
    return None


def _is_tripped(wd: Any) -> bool:
    for attr in ("is_tripped", "tripped", "_tripped", "circuit_open"):
        if hasattr(wd, attr):
            val = getattr(wd, attr)
            if callable(val):
                val = val()
            if val is True:
                return True
    return False


def _safe_terminate(wd: Any, extra_pids: list[Optional[int]]) -> None:
    if wd is not None:
        try:
            wd.terminate()
        except Exception as exc:  # cleanup must still sweep the remaining pids
            sys.stderr.write(f"watchdog terminate() raised during cleanup: {exc!r}\n")
    for pid in extra_pids:
        _force_kill(pid)


# ---------------------------------------------------------------------------
# Worker scripts & DB helpers
# ---------------------------------------------------------------------------

LOOPING_WORKER = """\
import os
import sys
import time
from pathlib import Path

ready_dir = Path(sys.argv[1])
ready_dir.mkdir(parents=True, exist_ok=True)
with open(ready_dir / f"ready_{os.getpid()}.txt", "w", encoding="utf-8") as fh:
    fh.write("ready")
while True:
    time.sleep(0.1)
"""

CRASHING_WORKER = """\
import sys
sys.exit(42)
"""

# Daemon that owns a grandchild process and reports whether it has a console window.
TREE_WORKER = """\
import os
import subprocess
import sys
import time
from pathlib import Path

ready_dir = Path(sys.argv[1])
ready_dir.mkdir(parents=True, exist_ok=True)


def _atomic_write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time\\nwhile True: time.sleep(0.1)"],
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0,
    encoding="utf-8",
)
_atomic_write(ready_dir / f"grandchild_{os.getpid()}.txt", str(grandchild.pid))

if os.name == "nt":
    import ctypes
    console_window = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
else:
    console_window = 0
_atomic_write(ready_dir / f"ready_{os.getpid()}.txt", str(console_window))
while True:
    time.sleep(0.1)
"""

# Offline harness executed in a fresh interpreter with a sys.addaudithook guard.
OFFLINE_HARNESS = """\
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

NET_EVENTS = {
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.gethostbyaddr",
    "socket.sendto",
    "socket.sendmsg",
}
net_events = []
popen_events = []


def _hook(event, args):
    if event in NET_EVENTS:
        net_events.append({"event": event, "args": repr(args)[:300]})
    elif event == "subprocess.Popen":
        executable = args[0] if len(args) > 0 else None
        argv = args[1] if len(args) > 1 else None
        if isinstance(argv, (list, tuple)):
            argv_s = [str(a) for a in argv]
        elif argv is None:
            argv_s = []
        else:
            argv_s = [str(argv)]
        popen_events.append({
            "executable": None if executable is None else str(executable),
            "argv": argv_s,
        })


sys.addaudithook(_hook)

watchdog_path, sidecar_path, work_dir, worker_path, result_path = sys.argv[1:6]
work = Path(work_dir)


def _load(unique, path):
    spec = importlib.util.spec_from_file_location(unique, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    spec.loader.exec_module(mod)
    return mod


wd_mod = _load("_offline_cochem_v2_watchdog", watchdog_path)
side = _load("_offline_cochem_v2_idle_sidecar", sidecar_path)

wd_db_path = work / "offline_watchdog.db"
wdb = wd_mod.WatchdogDB(wd_db_path)
wdb.init_schema()
wdb.log_event("kanban_worker", "OFFLINE_PROBE", pid=os.getpid(), details="offline")
probe_rows = wdb.get_events(event_type="OFFLINE_PROBE")

daemon_db_path = work / "offline_daemon.db"
ready_dir = work / "ready_offline"
ready_dir.mkdir(parents=True, exist_ok=True)
wd = wd_mod.DaemonWatchdog(
    target_cmd=[sys.executable, worker_path, str(ready_dir)],
    db_path=daemon_db_path,
    poll_interval=0.05,
)
daemon_pid = None
poll_result = None
ready_seen = False
try:
    proc = wd.spawn_daemon()
    daemon_pid = proc.pid
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if (ready_dir / f"ready_{daemon_pid}.txt").exists():
            ready_seen = True
            break
        time.sleep(0.05)
    poll_result = wd.poll_cycle()
finally:
    wd.terminate()

side_db_path = work / "offline_sidecar.db"
sdb = side.IdleSidecarDB(side_db_path, daily_budget=25.0)
sdb.init_schema()
sdb.set_quota_status("QUOTA_OK")
engine = side.HeavyTaskPolicyEngine(sdb, side.HardwareMonitor(cpu_threshold=100.0))
can_execute = engine.can_execute_heavy(estimated_cost=0.10)

result = {
    "net_events": net_events,
    "popen_events": popen_events,
    "probe_rows": probe_rows,
    "harness_pid": os.getpid(),
    "poll_result": poll_result,
    "daemon_pid": daemon_pid,
    "ready_seen": ready_seen,
    "can_execute": can_execute,
    "db_paths": [str(wd_db_path), str(daemon_db_path), str(side_db_path)],
}
with open(result_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, default=str)
"""


def _write_worker(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _ready_file(ready_dir: Path, pid: Optional[int]) -> Path:
    return ready_dir / f"ready_{pid}.txt"


def _query_events(db_path: Path, event_types: Optional[tuple[str, ...]] = None) -> list[dict]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM watchdog_events_v2").fetchall()]
    finally:
        conn.close()
    if event_types is None:
        return rows
    return [r for r in rows if r.get("event_type") in event_types]


def _all_pids(db_path: Path) -> list[Optional[int]]:
    try:
        return [r.get("pid") for r in _query_events(db_path)]
    except sqlite3.Error:
        return []


def _journal_mode(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()


def _row_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        return {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    with open(path, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=str(path))


def _subprocess_calls(tree: ast.Module) -> list[ast.Call]:
    subprocess_aliases = {"subprocess"}
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    subprocess_aliases.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_FUNCS:
                    direct_names.add(alias.asname or alias.name)

    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id in subprocess_aliases
        ):
            calls.append(node)
        elif isinstance(func, ast.Name) and func.id in direct_names:
            calls.append(node)
    return calls


def _module_assignment(tree: ast.Module, name: str) -> Optional[ast.AST]:
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
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out.extend(_flatten_strings(k))
            out.extend(_flatten_strings(v))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        out = []
        for v in value:
            out.extend(_flatten_strings(v))
        return out
    return []


def _assert_chain_terminates(name: str, chain: Any) -> None:
    if isinstance(chain, dict):
        for key, sub in chain.items():
            _assert_chain_terminates(f"{name}[{key!r}]", sub)
        return
    if isinstance(chain, (list, tuple)) and chain:
        if tuple(chain) == HALT_SENTINEL:
            return
        last = chain[-1]
        assert isinstance(last, (list, tuple)) and tuple(last) == HALT_SENTINEL, (
            f"{name} fallback chain must end in {HALT_SENTINEL}, got {last!r}"
        )
        lowered = [s.lower() for s in _flatten_strings(chain)]
        assert not any("ollama" in s for s in lowered), f"{name} routes to ollama: {chain!r}"


# ---------------------------------------------------------------------------
# [T1] AC1 - crash detection and restart
# ---------------------------------------------------------------------------


def test_watchdog_detects_crash_and_restarts(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()

    worker = _write_worker(tmp_path, "mock_kanban_worker.py", LOOPING_WORKER)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    db_path = tmp_path / "watchdog_v2.db"

    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker), str(ready_dir)],
        db_path=db_path,
        crash_threshold=3,
        window_seconds=30.0,
        poll_interval=0.05,
        daemon_name="kanban_worker",
    )
    initial_pid: Optional[int] = None
    new_pid: Optional[int] = None
    try:
        proc = wd.spawn_daemon()
        assert isinstance(proc, subprocess.Popen), "spawn_daemon must return subprocess.Popen"
        initial_pid = proc.pid
        assert _wait_until(lambda: _ready_file(ready_dir, initial_pid).exists()), (
            "mock worker never became ready"
        )
        assert _pid_alive(initial_pid)

        proc.kill()
        proc.wait(timeout=15)
        assert not _pid_alive(initial_pid)

        def _crash_and_restart_logged() -> bool:
            wd.poll_cycle()
            return bool(
                _query_events(db_path, ("DAEMON_CRASHED",))
                and _query_events(db_path, RESTART_EVENT_TYPES)
            )

        assert _wait_until(_crash_and_restart_logged, timeout=15.0, step=0.1), (
            "watchdog did not log DAEMON_CRASHED + restart events"
        )

        crashed = _query_events(db_path, ("DAEMON_CRASHED",))
        assert any(r.get("pid") == initial_pid for r in crashed), (
            f"no DAEMON_CRASHED row for pid {initial_pid}: {crashed!r}"
        )
        assert all(r.get("daemon_name") == "kanban_worker" for r in crashed)

        restarts = _query_events(db_path, RESTART_EVENT_TYPES)
        assert restarts, "no DAEMON_RESTARTED/RESTART row"

        new_pid = _current_pid(wd)
        if new_pid is None or new_pid == initial_pid:
            restart_pids = [r.get("pid") for r in restarts if r.get("pid") not in (None, initial_pid)]
            new_pid = restart_pids[-1] if restart_pids else new_pid
        assert new_pid is not None, "could not determine restarted daemon PID"
        assert new_pid != initial_pid, "restarted daemon reused the crashed PID"
        assert _wait_until(lambda: _ready_file(ready_dir, new_pid).exists()), (
            "restarted worker never became ready"
        )
        assert _pid_alive(new_pid), "restarted daemon is not running"

        # (a) event store runs in WAL mode.
        assert _journal_mode(db_path) == "wal", "watchdog_events_v2 db must use WAL journal_mode"

        # (b) the restart event names the new live pid.
        assert any(r.get("pid") == new_pid for r in restarts), (
            f"no restart row carries the new daemon pid {new_pid}: {restarts!r}"
        )

        # (c) a healthy daemon is left alone: no spurious crash/restart rows.
        crashed_before = len(_query_events(db_path, ("DAEMON_CRASHED",)))
        restarts_before = len(_query_events(db_path, RESTART_EVENT_TYPES))
        for _ in range(5):
            assert _pid_alive(new_pid), "restarted worker died during steady-state polling"
            assert wd.poll_cycle() is True, "poll_cycle must return True while the daemon is healthy"
            time.sleep(0.1)
        assert len(_query_events(db_path, ("DAEMON_CRASHED",))) == crashed_before, (
            "poll_cycle logged DAEMON_CRASHED for a healthy daemon"
        )
        assert len(_query_events(db_path, RESTART_EVENT_TYPES)) == restarts_before, (
            "poll_cycle restarted a healthy daemon"
        )
        killed_rows = [
            r for r in _query_events(db_path, ("DAEMON_CRASHED",)) if r.get("pid") == initial_pid
        ]
        assert len(killed_rows) == 1, (
            f"expected exactly one DAEMON_CRASHED row for pid {initial_pid}, got {killed_rows!r}"
        )
        assert _current_pid(wd) == new_pid, "watchdog switched daemons without a crash"

        # WatchdogDB public API reflects the same rows.
        api_rows = wd_mod.WatchdogDB(db_path).get_events(event_type="DAEMON_CRASHED")
        assert isinstance(api_rows, list) and api_rows
        assert all(isinstance(r, dict) for r in api_rows)
        assert any(r.get("pid") == initial_pid for r in api_rows)
        assert all(r.get("event_type") == "DAEMON_CRASHED" for r in api_rows)
    finally:
        _safe_terminate(wd, [initial_pid, new_pid, *_all_pids(db_path)])


# ---------------------------------------------------------------------------
# [T2] AC2 - spawn storm circuit breaker
# ---------------------------------------------------------------------------


def test_watchdog_spawn_storm_circuit_breaker(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()

    worker = _write_worker(tmp_path, "crashing_worker.py", CRASHING_WORKER)
    db_path = tmp_path / "watchdog_storm.db"

    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker)],
        db_path=db_path,
        crash_threshold=3,
        window_seconds=10.0,
        poll_interval=0.01,
        daemon_name="kanban_worker",
    )
    try:
        first = wd.spawn_daemon()
        assert isinstance(first, subprocess.Popen)

        tripped = False
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            result = wd.poll_cycle()
            if result is False or _is_tripped(wd):
                tripped = True
                break
            time.sleep(0.05)

        assert tripped, "circuit breaker never tripped on a crash-looping worker"

        storm = _query_events(db_path, ("SPAWN_STORM_DETECTED",))
        assert storm, "SPAWN_STORM_DETECTED not logged to watchdog_events_v2"

        crashes = _query_events(db_path, ("DAEMON_CRASHED",))
        assert 3 <= len(crashes) <= 4, (
            f"expected breaker to trip after ~3 rapid crashes, saw {len(crashes)}"
        )

        # Once tripped, no further respawns may happen.
        restarts_before = len(_query_events(db_path, RESTART_EVENT_TYPES))
        crashes_before = len(crashes)
        for _ in range(5):
            result = wd.poll_cycle()
            assert result is False or _is_tripped(wd), "breaker reset itself immediately"
            time.sleep(0.05)
        assert len(_query_events(db_path, RESTART_EVENT_TYPES)) == restarts_before, (
            "watchdog kept respawning after SPAWN_STORM_DETECTED"
        )
        assert len(_query_events(db_path, ("DAEMON_CRASHED",))) == crashes_before

        current = _current_pid(wd)
        if current is not None:
            assert _wait_until(lambda: not _pid_alive(current), timeout=10.0)

        recorded_pids = [p for p in _all_pids(db_path) if isinstance(p, int)]
        assert recorded_pids, "no pids recorded in watchdog_events_v2"
        assert _wait_until(
            lambda: not any(_pid_alive(p) for p in recorded_pids), timeout=10.0
        ), f"processes still alive after breaker trip: {[p for p in recorded_pids if _pid_alive(p)]}"
    finally:
        _safe_terminate(wd, _all_pids(db_path))

    # Scenario 2: crashes spaced wider than the window never trip the breaker,
    # proving the window slides instead of counting crashes forever.
    slide_db = tmp_path / "watchdog_sliding.db"
    wd2 = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker)],
        db_path=slide_db,
        crash_threshold=3,
        window_seconds=0.3,
        poll_interval=0.01,
        daemon_name="kanban_worker",
    )
    try:
        first2 = wd2.spawn_daemon()
        assert isinstance(first2, subprocess.Popen)
        for i in range(5):
            time.sleep(0.6)
            child = _current_pid(wd2)
            assert child is not None, f"iteration {i}: watchdog has no current daemon"
            assert _wait_until(lambda: not _pid_alive(child), timeout=15.0), (
                f"iteration {i}: crashing worker {child} never exited"
            )
            result = wd2.poll_cycle()
            assert result is True, f"iteration {i}: poll_cycle returned {result!r} for spaced crashes"
            assert _is_tripped(wd2) is False, f"iteration {i}: breaker tripped on spaced crashes"

        crashes2 = _query_events(slide_db, ("DAEMON_CRASHED",))
        restarts2 = _query_events(slide_db, RESTART_EVENT_TYPES)
        storms2 = _query_events(slide_db, ("SPAWN_STORM_DETECTED",))
        assert len(crashes2) == 5, f"expected 5 DAEMON_CRASHED rows, got {len(crashes2)}"
        assert len(restarts2) >= 5, f"expected >=5 restart rows, got {len(restarts2)}"
        assert storms2 == [], f"sliding window wrongly detected a storm: {storms2!r}"
    finally:
        _safe_terminate(wd2, _all_pids(slide_db))


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

    mode = _journal_mode(db_path)
    tables = _table_names(db_path)
    assert mode == "wal", f"expected WAL journal_mode, got {mode!r}"
    assert {"quota_state", "credit_ledger_v2"} <= tables, f"missing tables: {tables!r}"

    idle_monitor = side.HardwareMonitor(cpu_threshold=100.0)
    busy_monitor = side.HardwareMonitor(cpu_threshold=0.0)
    idle_engine = side.HeavyTaskPolicyEngine(db, idle_monitor)
    busy_engine = side.HeavyTaskPolicyEngine(db, busy_monitor)
    heavy_cost = 0.10

    # Quota exhausted -> blocked even when idle.
    db.set_quota_status("QUOTA_EXHAUSTED", details="test: exhausted")
    assert db.get_quota_status() == "QUOTA_EXHAUSTED"
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is False

    # Quota OK but CPU busy under a real CPU-burning load -> blocked.
    db.set_quota_status("QUOTA_OK", details="test: ok")
    assert db.get_quota_status() == "QUOTA_OK"
    burner = subprocess.Popen(
        [sys.executable, "-c", "while True: pass"],
        creationflags=CREATE_NO_WINDOW,
        encoding="utf-8",
    )
    try:
        time.sleep(0.3)
        assert burner.poll() is None, "CPU burner exited prematurely"
        loaded_cpu = busy_monitor.get_cpu_percent(interval=0.2)
        assert isinstance(loaded_cpu, float) and 0.0 < loaded_cpu <= 100.0, (
            f"CPU sample under real load must be positive, got {loaded_cpu!r}"
        )
        assert side.HardwareMonitor(cpu_threshold=0.0).is_cpu_idle(interval=0.2) is False
        assert busy_engine.can_execute_heavy(estimated_cost=heavy_cost) is False
    finally:
        burner.kill()
        burner.wait(timeout=15)
    assert not _pid_alive(burner.pid), "CPU burner left running"

    # Idle monitor after load removed.
    cpu = idle_monitor.get_cpu_percent(interval=0.05)
    assert isinstance(cpu, float) and 0.0 <= cpu <= 100.0
    assert idle_monitor.is_cpu_idle(interval=0.05) is True

    # Quota OK and CPU idle, no spend -> allowed.
    assert db.get_daily_spend() == pytest.approx(0.0)
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is True

    # Cross-instance freshness: quota state is read from SQLite on every decision.
    db_other = side.IdleSidecarDB(db_path, daily_budget=25.0)
    db_other.set_quota_status("QUOTA_EXHAUSTED")
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is False, (
        "engine used a cached quota state instead of reading SQLite"
    )
    db_other.set_quota_status("QUOTA_OK")
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is True

    # Daily budget exceeded -> blocked.
    db.record_spend("task-budget-001", "cochem-coder", 26.0)
    assert db.get_daily_spend() == pytest.approx(26.0)
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is False
    assert _row_count(db_path, "credit_ledger_v2") == 1, (
        "record_spend did not persist exactly one row into credit_ledger_v2"
    )

    # Budget boundary on a fresh database: spend + cost == budget is allowed.
    edge_path = tmp_path / "sidecar_budget_edge.db"
    edge_db = side.IdleSidecarDB(edge_path, daily_budget=25.0)
    edge_db.init_schema()
    edge_db.set_quota_status("QUOTA_OK")
    edge_engine = side.HeavyTaskPolicyEngine(edge_db, side.HardwareMonitor(cpu_threshold=100.0))
    edge_db.record_spend("t1", "cochem-coder", 24.0)
    assert edge_db.get_daily_spend() == pytest.approx(24.0)
    assert edge_engine.can_execute_heavy(estimated_cost=1.0) is True, "24 + 1 == 25 must be allowed"
    assert edge_engine.can_execute_heavy(estimated_cost=1.5) is False, "24 + 1.5 > 25 must be blocked"
    edge_other = side.IdleSidecarDB(edge_path, daily_budget=25.0)
    edge_other.record_spend("t2", "cochem-coder", 2.0)
    assert edge_db.get_daily_spend() == pytest.approx(26.0)
    assert edge_engine.can_execute_heavy(estimated_cost=0.10) is False
    assert _row_count(edge_path, "credit_ledger_v2") == 2, (
        "credit_ledger_v2 row count must equal the number of record_spend calls"
    )

    # Heavy-task classification by cost threshold.
    assert idle_engine.is_heavy_task(estimated_cost=0.0) is False
    assert idle_engine.is_heavy_task(estimated_cost=0.01) is False
    assert idle_engine.is_heavy_task(estimated_cost=side.DEFAULT_HEAVY_COST_THRESHOLD) is True
    assert idle_engine.is_heavy_task(estimated_cost=1.0) is True


# ---------------------------------------------------------------------------
# [T4] AC4 - subprocess flags and clean termination
# ---------------------------------------------------------------------------


def _names_create_no_window(expr: ast.AST) -> bool:
    for n in ast.walk(expr):
        if isinstance(n, ast.Name) and n.id == "CREATE_NO_WINDOW":
            return True
        if isinstance(n, ast.Attribute) and n.attr == "CREATE_NO_WINDOW":
            return True
    return False


def test_subprocess_flags_and_clean_termination(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()
    side = _get_idle_sidecar()

    sources = {
        "watchdog.py": _module_source_path(wd_mod, "watchdog.py"),
        "cochem_idle_sidecar.py": _module_source_path(side, "cochem_idle_sidecar.py"),
    }

    watchdog_calls = 0
    for fname, path in sources.items():
        tree = _parse(path)
        calls = _subprocess_calls(tree)
        if fname == "watchdog.py":
            watchdog_calls = len(calls)
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            assert "creationflags" in kw, (
                f"{fname}:{call.lineno} subprocess call missing explicit creationflags"
            )
            assert _names_create_no_window(kw["creationflags"]), (
                f"{fname}:{call.lineno} creationflags must reference CREATE_NO_WINDOW, "
                f"got {ast.unparse(kw['creationflags'])}"
            )
            assert "encoding" in kw, f"{fname}:{call.lineno} subprocess call missing explicit encoding"
            enc = kw["encoding"]
            assert isinstance(enc, ast.Constant) and isinstance(enc.value, str), (
                f"{fname}:{call.lineno} encoding must be the literal 'utf-8', got {ast.unparse(enc)}"
            )
            assert enc.value.lower() in ("utf-8", "utf8"), (
                f"{fname}:{call.lineno} encoding must be utf-8, got {enc.value!r}"
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    assert node.func.attr not in ("system", "popen"), (
                        f"{fname}:{node.lineno} uses os.{node.func.attr}; use subprocess with flags"
                    )
    assert watchdog_calls >= 1, "watchdog.py must spawn the daemon via subprocess"

    assert wd_mod.CREATE_NO_WINDOW == CREATE_NO_WINDOW

    # Behaviour: the daemon owns a grandchild; terminate() must reap the whole tree.
    worker = _write_worker(tmp_path, "tree_worker.py", TREE_WORKER)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    db_path = tmp_path / "watchdog_term.db"

    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker), str(ready_dir)],
        db_path=db_path,
        poll_interval=0.05,
    )
    pid: Optional[int] = None
    grandchild_pid: Optional[int] = None
    try:
        proc = wd.spawn_daemon()
        assert isinstance(proc, subprocess.Popen)
        pid = proc.pid
        ready = _ready_file(ready_dir, pid)
        gc_file = ready_dir / f"grandchild_{pid}.txt"
        assert _wait_until(lambda: ready.exists() and gc_file.exists(), timeout=20.0), (
            "tree worker never reported ready/grandchild"
        )
        with open(gc_file, "r", encoding="utf-8") as fh:
            grandchild_pid = int(fh.read().strip())
        with open(ready, "r", encoding="utf-8") as fh:
            console_window = int(fh.read().strip())

        assert _pid_alive(pid), "daemon should be running before terminate()"
        assert _pid_alive(grandchild_pid), "grandchild should be running before terminate()"
        if os.name == "nt":
            assert console_window == 0, (
                f"daemon has a console window (hwnd={console_window}); CREATE_NO_WINDOW not applied"
            )

        wd.terminate()
        gc = grandchild_pid
        assert _wait_until(lambda: not _pid_alive(pid), timeout=15.0), (
            f"daemon pid {pid} still alive after terminate()"
        )
        assert _wait_until(lambda: not _pid_alive(gc), timeout=15.0), (
            f"grandchild pid {gc} orphaned after terminate()"
        )
        assert not _pid_alive(_current_pid(wd))

        # terminate() is idempotent.
        wd.terminate()
    finally:
        _safe_terminate(wd, [pid, *_all_pids(db_path)])
        _force_kill(grandchild_pid)


# ---------------------------------------------------------------------------
# [T5] AC5 - offline isolation and routing sentinels
# ---------------------------------------------------------------------------


def test_offline_isolation_and_routing_sentinels(tmp_path: Path) -> None:
    wd_mod = _get_watchdog()
    side = _get_idle_sidecar()

    modules = {
        "watchdog.py": (wd_mod, _module_source_path(wd_mod, "watchdog.py")),
        "cochem_idle_sidecar.py": (side, _module_source_path(side, "cochem_idle_sidecar.py")),
    }

    for fname, (mod, path) in modules.items():
        tree = _parse(path)

        # Constants: runtime and literal source definition.
        assert tuple(mod.HALT_SENTINEL) == HALT_SENTINEL, f"{fname}: HALT_SENTINEL wrong"
        assert "ollama" in mod.FORBIDDEN_PROVIDERS, f"{fname}: FORBIDDEN_PROVIDERS lacks ollama"

        halt_node = _module_assignment(tree, "HALT_SENTINEL")
        assert halt_node is not None, f"{fname}: HALT_SENTINEL not defined at module level"
        assert tuple(ast.literal_eval(halt_node)) == HALT_SENTINEL

        forbidden_node = _module_assignment(tree, "FORBIDDEN_PROVIDERS")
        assert forbidden_node is not None, f"{fname}: FORBIDDEN_PROVIDERS not defined at module level"
        assert "ollama" in _string_constants(forbidden_node)

        # 'ollama' may appear as a literal only inside FORBIDDEN_PROVIDERS.
        allowed_ids = {id(n) for n in ast.walk(forbidden_node)}
        stray = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.strip().lower() == "ollama"
            and id(n) not in allowed_ids
        ]
        assert not stray, f"{fname}: 'ollama' literal used outside FORBIDDEN_PROVIDERS at lines {stray}"

        # Literal fallback/routing chains in source must end in the halt sentinel.
        for node in tree.body:
            targets: list[ast.expr] = []
            value: Optional[ast.expr] = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            for tgt in targets:
                if isinstance(tgt, ast.Name) and CHAIN_NAME_RE.search(tgt.id):
                    if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
                        last = value.elts[-1]
                        ok = (isinstance(last, ast.Name) and last.id == "HALT_SENTINEL") or (
                            isinstance(last, ast.Tuple)
                            and [getattr(e, "value", None) for e in last.elts] == list(HALT_SENTINEL)
                        )
                        is_sentinel_itself = [
                            getattr(e, "value", None) for e in value.elts
                        ] == list(HALT_SENTINEL)
                        assert ok or is_sentinel_itself, (
                            f"{fname}:{node.lineno} {tgt.id} must end in HALT_SENTINEL"
                        )

        # Runtime fallback/routing chains.
        for name, val in vars(mod).items():
            if name.startswith("__") or not CHAIN_NAME_RE.search(name):
                continue
            if isinstance(val, (list, tuple, dict)):
                _assert_chain_terminates(f"{fname}:{name}", val)

        # No network or Docker client libraries.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_hit = alias.name.split(".")[0] in FORBIDDEN_IMPORTS
                    assert not (root_hit or alias.name in FORBIDDEN_IMPORTS), (
                        f"{fname}:{node.lineno} imports network/docker lib {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod_name = node.module
                assert mod_name.split(".")[0] not in FORBIDDEN_IMPORTS and mod_name not in FORBIDDEN_IMPORTS, (
                    f"{fname}:{node.lineno} imports network/docker lib {mod_name}"
                )
                if mod_name in ("urllib", "http"):
                    names = {a.name for a in node.names}
                    assert not names & {"request", "client"}, (
                        f"{fname}:{node.lineno} imports {mod_name}.{names & {'request', 'client'}}"
                    )

    # Runtime offline execution in a fresh interpreter guarded by sys.addaudithook.
    harness = _write_worker(tmp_path, "offline_harness.py", OFFLINE_HARNESS)
    worker = _write_worker(tmp_path, "offline_worker.py", LOOPING_WORKER)
    work_dir = tmp_path / "offline_work"
    work_dir.mkdir()
    result_path = tmp_path / "offline_result.json"

    env = os.environ.copy()
    env["DOCKER_HOST"] = "tcp://127.0.0.1:9"
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"

    daemon_pid: Optional[int] = None
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(harness),
                str(modules["watchdog.py"][1]),
                str(modules["cochem_idle_sidecar.py"][1]),
                str(work_dir),
                str(worker),
                str(result_path),
            ],
            creationflags=CREATE_NO_WINDOW,
            encoding="utf-8",
            capture_output=True,
            timeout=120,
            env=env,
        )
        assert completed.returncode == 0, (
            f"offline harness failed (rc={completed.returncode})\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
        with open(result_path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        daemon_pid = result.get("daemon_pid")

        assert result["net_events"] == [], f"modules attempted network access: {result['net_events']!r}"

        for ev in result["popen_events"]:
            exe = ev.get("executable")
            argv = ev.get("argv") or []
            names = []
            if exe:
                names.append(os.path.basename(exe).lower())
            if argv:
                names.append(os.path.basename(argv[0]).lower())
            assert not any("docker" in n for n in names), f"docker process spawned: {ev!r}"

        probe = result["probe_rows"]
        assert probe, "OFFLINE_PROBE row not returned by get_events"
        assert probe[0].get("event_type") == "OFFLINE_PROBE"
        assert probe[0].get("pid") == result["harness_pid"]

        assert result["ready_seen"] is True, "offline daemon never became ready"
        assert result["poll_result"] is not False, "poll_cycle reported a trip for a healthy daemon"
        assert isinstance(daemon_pid, int)
        assert _wait_until(lambda: not _pid_alive(daemon_pid), timeout=15.0), (
            f"offline daemon {daemon_pid} survived terminate()"
        )
        assert result["can_execute"] is True

        tmp_resolved = tmp_path.resolve()
        for db_str in result["db_paths"]:
            db_file = Path(db_str)
            assert db_file.exists(), f"db file missing: {db_file}"
            assert tmp_resolved in db_file.resolve().parents, f"db escaped tmp_path: {db_file}"
    finally:
        _force_kill(daemon_pid)
