"""TDD contract for CoChem Pipeline v2 Phase 2 monitors.

Targets:
    __agentic/v2/watchdog.py            (DaemonWatchdog, WatchdogDB)
    __agentic/v2/cochem_idle_sidecar.py (IdleSidecarDB, HardwareMonitor,
                                         HeavyTaskPolicyEngine)

Both modules are resolved from the workspace root first and from
D:\\__CoChem second. Every test is fully offline: real worker subprocesses and
real SQLite databases are created under tmp_path; no network, no Docker.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
COCHEM_ROOT = Path(r"D:\__CoChem")

for _p in (
    COCHEM_ROOT / "__agentic" / "v2",
    COCHEM_ROOT,
    WORKSPACE_ROOT / "__agentic" / "v2",
    WORKSPACE_ROOT,
):
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
    candidates = [
        WORKSPACE_ROOT / "__agentic" / "v2" / name,
        COCHEM_ROOT / "__agentic" / "v2" / name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        f"{name} not found; looked in: " + ", ".join(str(c) for c in candidates)
    )


def _load_module(stem: str, marker_attr: str) -> ModuleType:
    if stem in _MODULE_CACHE:
        return _MODULE_CACHE[stem]

    errors: list[str] = []

    try:
        mod = importlib.import_module(f"__agentic.v2.{stem}")
        if hasattr(mod, marker_attr):
            _MODULE_CACHE[stem] = mod
            return mod
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

    try:
        mod = importlib.import_module(stem)
        if hasattr(mod, marker_attr):
            _MODULE_CACHE[stem] = mod
            return mod
        errors.append(f"{stem} ({getattr(mod, '__file__', '?')}): missing {marker_attr}")
    except ImportError as exc:
        errors.append(f"{stem}: {exc!r}")

    raise ImportError(f"Unable to import CoChem v2 module '{stem}': " + " | ".join(errors))


def _get_watchdog() -> ModuleType:
    return _load_module("watchdog", "DaemonWatchdog")


def _get_idle_sidecar() -> ModuleType:
    return _load_module("cochem_idle_sidecar", "HeavyTaskPolicyEngine")


def _module_source_path(mod: ModuleType, filename: str) -> Path:
    try:
        return _find_target_file(filename)
    except FileNotFoundError:
        mod_file = getattr(mod, "__file__", None)
        assert mod_file, f"cannot locate source for {filename}"
        return Path(mod_file)


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
            pass


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
        except Exception:
            pass
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

        # WatchdogDB public API reflects the same rows.
        api_rows = wd_mod.WatchdogDB(db_path).get_events(event_type="DAEMON_CRASHED")
        assert isinstance(api_rows, list) and api_rows
        assert all(isinstance(r, dict) for r in api_rows)
        assert any(r.get("pid") == initial_pid for r in api_rows)
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
    finally:
        _safe_terminate(wd, _all_pids(db_path))


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
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
    assert str(mode).lower() == "wal", f"expected WAL journal_mode, got {mode!r}"
    assert {"quota_state", "credit_ledger_v2"} <= tables, f"missing tables: {tables!r}"

    idle_monitor = side.HardwareMonitor(cpu_threshold=100.0)
    busy_monitor = side.HardwareMonitor(cpu_threshold=0.0)

    cpu = idle_monitor.get_cpu_percent(interval=0.05)
    assert isinstance(cpu, float) and 0.0 <= cpu <= 100.0
    assert idle_monitor.is_cpu_idle(interval=0.05) is True
    assert busy_monitor.is_cpu_idle(interval=0.05) is False

    idle_engine = side.HeavyTaskPolicyEngine(db, idle_monitor)
    busy_engine = side.HeavyTaskPolicyEngine(db, busy_monitor)
    heavy_cost = 0.10

    # Quota exhausted -> blocked even when idle.
    db.set_quota_status("QUOTA_EXHAUSTED", details="test: exhausted")
    assert db.get_quota_status() == "QUOTA_EXHAUSTED"
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is False

    # Quota OK but CPU busy -> blocked.
    db.set_quota_status("QUOTA_OK", details="test: ok")
    assert db.get_quota_status() == "QUOTA_OK"
    assert busy_engine.can_execute_heavy(estimated_cost=heavy_cost) is False

    # Quota OK and CPU idle, no spend -> allowed.
    assert db.get_daily_spend() == pytest.approx(0.0)
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is True

    # Daily budget exceeded -> blocked.
    db.record_spend("task-budget-001", "cochem-coder", 26.0)
    assert db.get_daily_spend() == pytest.approx(26.0)
    assert idle_engine.can_execute_heavy(estimated_cost=heavy_cost) is False

    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        ledger_rows = conn.execute("SELECT COUNT(*) FROM credit_ledger_v2").fetchone()[0]
    finally:
        conn.close()
    assert ledger_rows >= 1, "record_spend did not persist into credit_ledger_v2"

    # Heavy-task classification by cost threshold.
    assert idle_engine.is_heavy_task(estimated_cost=0.0) is False
    assert idle_engine.is_heavy_task(estimated_cost=0.01) is False
    assert idle_engine.is_heavy_task(estimated_cost=side.DEFAULT_HEAVY_COST_THRESHOLD) is True
    assert idle_engine.is_heavy_task(estimated_cost=1.0) is True


# ---------------------------------------------------------------------------
# [T4] AC4 - subprocess flags and clean termination
# ---------------------------------------------------------------------------


def test_subprocess_flags_and_clean_termination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
                f"{fname}:{call.lineno} subprocess call missing creationflags"
            )
            assert "encoding" in kw, f"{fname}:{call.lineno} subprocess call missing encoding"
            enc = kw["encoding"]
            if isinstance(enc, ast.Constant):
                assert str(enc.value).lower() in ("utf-8", "utf8"), (
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

    # Runtime spy on the stdlib (not the module under test) to capture spawn kwargs.
    recorded: list[dict] = []
    real_popen = subprocess.Popen

    class _SpyPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            recorded.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _SpyPopen)

    worker = _write_worker(tmp_path, "term_worker.py", LOOPING_WORKER)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    db_path = tmp_path / "watchdog_term.db"

    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker), str(ready_dir)],
        db_path=db_path,
        poll_interval=0.05,
    )
    pid: Optional[int] = None
    try:
        proc = wd.spawn_daemon()
        pid = proc.pid
        assert _wait_until(lambda: _ready_file(ready_dir, pid).exists()), "worker never became ready"
        assert _pid_alive(pid), "daemon should be running before terminate()"

        if recorded:
            kwargs = recorded[-1]
            assert str(kwargs.get("encoding", "")).lower() in ("utf-8", "utf8")
            if os.name == "nt":
                assert int(kwargs.get("creationflags", 0)) & CREATE_NO_WINDOW, (
                    "daemon spawned without CREATE_NO_WINDOW"
                )

        wd.terminate()
        assert _wait_until(lambda: not _pid_alive(pid), timeout=15.0), (
            f"daemon pid {pid} still alive after terminate() (orphaned process)"
        )
        assert not _pid_alive(_current_pid(wd))
    finally:
        _safe_terminate(wd, [pid, *_all_pids(db_path)])


# ---------------------------------------------------------------------------
# [T5] AC5 - offline isolation and routing sentinels
# ---------------------------------------------------------------------------


def test_offline_isolation_and_routing_sentinels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    # Runtime offline execution: any socket connect/resolve attempt is recorded and refused.
    attempts: list[str] = []

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        attempts.append(repr(args[:2]))
        raise OSError("network access is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:9")

    wd_db_path = tmp_path / "offline_watchdog.db"
    wdb = wd_mod.WatchdogDB(wd_db_path)
    wdb.init_schema()
    wdb.log_event("kanban_worker", "OFFLINE_PROBE", pid=os.getpid(), details="offline")
    probe = wdb.get_events(event_type="OFFLINE_PROBE")
    assert probe and probe[0].get("event_type") == "OFFLINE_PROBE"
    assert probe[0].get("pid") == os.getpid()

    worker = _write_worker(tmp_path, "offline_worker.py", LOOPING_WORKER)
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    wd = wd_mod.DaemonWatchdog(
        target_cmd=[sys.executable, str(worker), str(ready_dir)],
        db_path=tmp_path / "offline_daemon.db",
        poll_interval=0.05,
    )
    pid: Optional[int] = None
    try:
        proc = wd.spawn_daemon()
        pid = proc.pid
        assert _wait_until(lambda: _ready_file(ready_dir, pid).exists())
        assert wd.poll_cycle() is not False
        assert _pid_alive(pid)
    finally:
        _safe_terminate(wd, [pid, *_all_pids(tmp_path / "offline_daemon.db")])
    assert _wait_until(lambda: not _pid_alive(pid), timeout=15.0)

    side_db_path = tmp_path / "offline_sidecar.db"
    sdb = side.IdleSidecarDB(side_db_path, daily_budget=25.0)
    sdb.init_schema()
    sdb.set_quota_status("QUOTA_OK")
    engine = side.HeavyTaskPolicyEngine(sdb, side.HardwareMonitor(cpu_threshold=100.0))
    assert engine.can_execute_heavy(estimated_cost=0.10) is True

    assert not attempts, f"modules attempted network access: {attempts!r}"
    for db_file in (wd_db_path, tmp_path / "offline_daemon.db", side_db_path):
        assert db_file.exists() and tmp_path in db_file.resolve().parents
