"""TDD contract for PRIORITY_2_PIPELINE_V2_MONITORS (watchdog + idle sidecar).

Target modules (always loaded by file path from this workspace, because the
pip package ``watchdog`` can shadow a plain import):

* ``__agentic/v2/watchdog.py``            - DaemonWatchdog / WatchdogDB / CLI
* ``__agentic/v2/cochem_idle_sidecar.py`` - IdleSidecarDB / HardwareMonitor /
  HeavyTaskPolicyEngine

Acceptance criteria:

* AC1 ``test_watchdog_detects_crash_and_restarts``
* AC2 ``test_watchdog_spawn_storm_circuit_breaker``
* AC3 ``test_idle_sidecar_cpu_and_credit_gating``
* AC4 ``test_subprocess_flags_and_clean_termination``
* AC5 ``test_offline_isolation_and_routing_sentinels``

No mocks and no monkeypatching: real processes, real SQLite files under
``tmp_path``, real CPU load, and a Python audit hook for the offline proof.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

import psutil

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
V2_DIR = WORKSPACE_ROOT / "__agentic" / "v2"
WATCHDOG_PATH = V2_DIR / "watchdog.py"
SIDECAR_PATH = V2_DIR / "cochem_idle_sidecar.py"
TARGET_PATHS = (WATCHDOG_PATH, SIDECAR_PATH)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HALT = ("halt", "graceful")
RESTART_EVENTS = ("DAEMON_RESTARTED", "RESTART")
CHAIN_NAME_RE = re.compile(r"(FALLBACK|CHAIN|ROUT)", re.IGNORECASE)
SUBPROCESS_FUNCS = frozenset({"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"})
BANNED_SUBPROCESS_FUNCS = frozenset({"getoutput", "getstatusoutput"})
FORBIDDEN_IMPORTS = (
    "socket", "ssl", "requests", "httpx", "aiohttp", "urllib3", "urllib.request", "http.client",
    "websockets", "docker", "grpc", "openai", "anthropic", "ollama", "google.generativeai",
    "ftplib", "smtplib", "xmlrpc",
)

# --------------------------------------------------------------------------- worker scripts

LONG_WORKER = """\
import os
import sys
import time

with open(sys.argv[1], "a", encoding="utf-8") as fh:
    fh.write(f"{os.getpid()}\\n")
    fh.flush()
    os.fsync(fh.fileno())
while True:
    time.sleep(0.2)
"""

CRASH_WORKER = """\
import os
import sys

with open(sys.argv[1], "a", encoding="utf-8") as fh:
    fh.write(f"{os.getpid()}\\n")
    fh.flush()
    os.fsync(fh.fileno())
sys.exit(7)
"""

TREE_WORKER = """\
import json
import os
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(600)"],
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    encoding="utf-8",
)
out_path = sys.argv[1]
tmp_path = out_path + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump({"worker": os.getpid(), "grandchild": grandchild.pid}, fh)
    fh.flush()
    os.fsync(fh.fileno())
os.replace(tmp_path, out_path)
while True:
    time.sleep(0.2)
"""

OFFLINE_HARNESS = """\
import sys

NET_EVENTS = []


def _audit(event, args):
    if event.startswith("socket.") or event.startswith("urllib.Request"):
        NET_EVENTS.append(event)


sys.addaudithook(_audit)

import importlib.util
import json
import time
from pathlib import Path


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wd_mod = _load("cochem_v2_watchdog_offline", sys.argv[1])
sc_mod = _load("cochem_v2_idle_sidecar_offline", sys.argv[2])
work = Path(sys.argv[3])

wdb = wd_mod.WatchdogDB(work / "offline_wd.db")
wdb.init_schema()
wdb.log_event("offline_daemon", "DAEMON_CRASHED", 1, "offline harness")
events = wdb.get_events(event_type="DAEMON_CRASHED", limit=5)
if len(events) != 1:
    raise SystemExit(f"expected 1 DAEMON_CRASHED event, got {events!r}")

wd = wd_mod.DaemonWatchdog(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    work / "offline_wd.db",
    crash_threshold=3,
    window_seconds=30.0,
    poll_interval=0.05,
)
try:
    wd.spawn_daemon()
    for _ in range(3):
        wd.poll_cycle()
        time.sleep(0.05)
finally:
    wd.terminate()

sdb = sc_mod.IdleSidecarDB(work / "offline_sidecar.db", daily_budget=1.0)
sdb.init_schema()
sdb.set_quota_status("QUOTA_OK")
sdb.record_spend("t", "agent", 0.01)
engine = sc_mod.HeavyTaskPolicyEngine(sdb, sc_mod.HardwareMonitor(100.0))
allowed = engine.can_execute_heavy(estimated_cost=0.1)
print(json.dumps({"net_events": NET_EVENTS, "allowed": allowed}))
sys.stdout.flush()
"""

# --------------------------------------------------------------------------- generic helpers

_MODULE_CACHE: dict[str, ModuleType] = {}


def _load_module(unique_name: str, path: Path) -> ModuleType:
    """Import ``path`` under ``unique_name`` (never via sys.path, so pip packages cannot shadow it)."""
    if unique_name in _MODULE_CACHE:
        return _MODULE_CACHE[unique_name]
    assert path.is_file(), f"target module missing: {path}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    assert spec is not None and spec.loader is not None, f"cannot build import spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(unique_name, None)
        raise
    _MODULE_CACHE[unique_name] = module
    return module


def _watchdog() -> ModuleType:
    return _load_module("cochem_v2_watchdog_ut", WATCHDOG_PATH)


def _sidecar() -> ModuleType:
    return _load_module("cochem_v2_idle_sidecar_ut", SIDECAR_PATH)


def _write_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _cmd_token(value: Any) -> str:
    """Render one --cmd token: POSIX-style path, double-quoted only if it contains a space."""
    text = Path(value).as_posix() if isinstance(value, Path) else str(value)
    return f'"{text}"' if " " in text else text


def _cmd_string(*tokens: Any) -> str:
    return " ".join(_cmd_token(t) for t in tokens)


def _wait_until(predicate: Callable[[], Any], timeout: float, interval: float = 0.05) -> Any:
    """Poll ``predicate`` until it returns a truthy value or ``timeout`` elapses; return the last value."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def _read_pids(path: Path) -> list[int]:
    """Return the pids of all complete (newline-terminated) lines in a pid log."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return [int(line) for line in text.split("\n")[:-1] if line.strip().isdigit()]


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _is_running(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _create_time(pid: int) -> Optional[float]:
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _is_gone(pid: int, created: Optional[float]) -> bool:
    """True if ``pid`` no longer names the process first observed at ``created`` (or is a zombie)."""
    try:
        proc = psutil.Process(pid)
        if created is not None and abs(proc.create_time() - created) > 0.01:
            return True
        return proc.status() == psutil.STATUS_ZOMBIE
    except psutil.Error:
        return True


def _kill_tree(pid: int) -> None:
    """Kill ``pid`` and all of its descendants (children first)."""
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    try:
        procs = parent.children(recursive=True)
    except psutil.Error:
        procs = []
    procs.append(parent)
    for proc in procs:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=5)


def _kill_if_matches(pid: int, marker: str) -> bool:
    """Kill the tree of ``pid`` only if its command line contains ``marker`` (guards against pid reuse)."""
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
        if marker not in cmdline or proc.status() == psutil.STATUS_ZOMBIE:
            return False
    except psutil.Error:
        return False
    _kill_tree(pid)
    return True


def _db_rows(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db), timeout=5)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _try_db_rows(db: Path, sql: str, params: tuple = ()) -> Optional[list[tuple]]:
    """Like :func:`_db_rows` but returns None while the DB/table does not exist yet or is locked."""
    if not db.exists():
        return None
    try:
        return _db_rows(db, sql, params)
    except sqlite3.OperationalError:
        return None


def _assert_utc_iso(value: Any, context: str) -> datetime:
    assert isinstance(value, str), f"{context}: timestamp must be a str, got {value!r}"
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{context}: timestamp {value!r} is not timezone-aware"
    assert parsed.utcoffset() == timedelta(0), f"{context}: timestamp {value!r} is not UTC"
    return parsed


class _WatchdogCli:
    """Runs ``watchdog.py`` as a real CLI process with stdout/stderr captured to files."""

    def __init__(self, tmp_path: Path, tag: str, cmd_string: str, db: Path) -> None:
        self.out_path = tmp_path / f"{tag}_stdout.txt"
        self.err_path = tmp_path / f"{tag}_stderr.txt"
        self._out = open(self.out_path, "w", encoding="utf-8")
        self._err = open(self.err_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(WATCHDOG_PATH), "--cmd", cmd_string, "--db", str(db),
             "--threshold", "3", "--poll", "0.1"],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            stdout=self._out,
            stderr=self._err,
            creationflags=_NO_WINDOW,
            encoding="utf-8",
        )

    def output(self) -> str:
        for fh in (self._out, self._err):
            if not fh.closed:
                fh.flush()
        with open(self.out_path, encoding="utf-8", errors="replace") as fh:
            out = fh.read()
        with open(self.err_path, encoding="utf-8", errors="replace") as fh:
            err = fh.read()
        return f"[watchdog rc={self.proc.poll()}]\n--- stdout ---\n{out}\n--- stderr ---\n{err}"

    def exited(self) -> bool:
        return self.proc.poll() is not None

    def close(self) -> None:
        _kill_tree(self.proc.pid)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self._out.close()
        self._err.close()


# --------------------------------------------------------------------------- AST helpers


def _parse(path: Path) -> ast.Module:
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=str(path))


def _is_subprocess_create_no_window(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "CREATE_NO_WINDOW"
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
    )


def _is_os_forbidden(attr: str) -> bool:
    return attr in {"system", "popen"} or attr.startswith(("spawn", "exec"))


def _subprocess_violations(path: Path) -> tuple[list[str], int]:
    """Return (violations, number of subprocess.Popen calls) for the process-spawning AST rule."""
    tree = _parse(path)
    module_aliases = {"subprocess"}
    os_aliases = {"os"}
    asyncio_aliases = {"asyncio"}
    from_subprocess: dict[str, str] = {}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == "subprocess":
                    module_aliases.add(bound)
                elif alias.name == "os":
                    os_aliases.add(bound)
                elif alias.name == "asyncio":
                    asyncio_aliases.add(bound)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module == "subprocess" and alias.name in SUBPROCESS_FUNCS:
                    from_subprocess[bound] = alias.name
                if node.module == "os" and _is_os_forbidden(alias.name):
                    violations.append(f"line {node.lineno}: forbidden import os.{alias.name}")
                if node.module == "asyncio" and alias.name.startswith("create_subprocess_"):
                    violations.append(f"line {node.lineno}: forbidden import asyncio.{alias.name}")
                if alias.name == "DETACHED_PROCESS":
                    violations.append(f"line {node.lineno}: DETACHED_PROCESS imported")

    popen_calls = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id == "DETACHED_PROCESS") or (
            isinstance(node, ast.Attribute) and node.attr == "DETACHED_PROCESS"
        ):
            violations.append(f"line {node.lineno}: DETACHED_PROCESS referenced")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: Optional[str] = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner = func.value.id
            if owner in module_aliases and func.attr in SUBPROCESS_FUNCS:
                name = func.attr
            elif owner in os_aliases and _is_os_forbidden(func.attr):
                violations.append(f"line {node.lineno}: forbidden call os.{func.attr}")
            elif owner in asyncio_aliases and func.attr.startswith("create_subprocess_"):
                violations.append(f"line {node.lineno}: forbidden call asyncio.{func.attr}")
        elif isinstance(func, ast.Name) and func.id in from_subprocess:
            name = from_subprocess[func.id]
        if name is None:
            continue
        where = f"line {node.lineno}: subprocess.{name}"
        if name in BANNED_SUBPROCESS_FUNCS:
            violations.append(f"{where} is forbidden outright")
            continue
        if name == "Popen":
            popen_calls += 1
        if any(kw.arg is None for kw in node.keywords):
            violations.append(f"{where} uses a **kwargs splat")
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        flags = keywords.get("creationflags")
        if flags is None:
            violations.append(f"{where} lacks creationflags=")
        elif not _is_subprocess_create_no_window(flags):
            violations.append(
                f"{where} creationflags must be literally subprocess.CREATE_NO_WINDOW, got {ast.unparse(flags)}"
            )
        enc = keywords.get("encoding")
        if not (isinstance(enc, ast.Constant) and isinstance(enc.value, str)
                and enc.value.lower().replace("_", "-") in {"utf-8", "utf8"}):
            got = ast.unparse(enc) if enc is not None else None
            violations.append(f'{where} lacks encoding="utf-8" (got {got})')
    return violations, popen_calls


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _module_level_assignments(tree: ast.Module) -> list[tuple[str, ast.expr, int]]:
    """(target name, value, line) for module-level assignments, including inside top-level if/try blocks."""
    found: list[tuple[str, ast.expr, int]] = []

    def visit(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        found.append((target.id, stmt.value, stmt.lineno))
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                found.append((stmt.target.id, stmt.value, stmt.lineno))
            elif isinstance(stmt, ast.If):
                visit(stmt.body)
                visit(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                visit(stmt.body)
                visit(stmt.orelse)
                visit(stmt.finalbody)
                for handler in stmt.handlers:
                    visit(handler.body)

    visit(tree.body)
    return found


def _is_halt_node(node: ast.expr) -> bool:
    if isinstance(node, ast.Name) and node.id == "HALT_SENTINEL":
        return True
    return (
        isinstance(node, ast.Tuple)
        and len(node.elts) == 2
        and all(isinstance(e, ast.Constant) for e in node.elts)
        and tuple(e.value for e in node.elts) == HALT
    )


def _is_forbidden_module(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN_IMPORTS)


def _offline_violations(path: Path) -> list[str]:
    """Forbidden network imports, stray 'ollama' strings and fallback chains not ending in HALT_SENTINEL."""
    tree = _parse(path)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(f"line {node.lineno}: import {a.name}" for a in node.names
                              if _is_forbidden_module(a.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_forbidden_module(node.module):
                violations.append(f"line {node.lineno}: from {node.module} import ...")
            else:
                violations.extend(f"line {node.lineno}: from {node.module} import {a.name}" for a in node.names
                                  if _is_forbidden_module(f"{node.module}.{a.name}"))
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
            if fname in {"__import__", "import_module"} and _is_forbidden_module(node.args[0].value):
                violations.append(f"line {node.lineno}: dynamic import of {node.args[0].value}")

    assignments = _module_level_assignments(tree)
    allowed = _docstring_ids(tree)
    for name, value, _line in assignments:
        if name == "FORBIDDEN_PROVIDERS":
            allowed.update(id(n) for n in ast.walk(value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "ollama" in node.value.lower() \
                and id(node) not in allowed:
            violations.append(f"line {node.lineno}: 'ollama' outside FORBIDDEN_PROVIDERS: {node.value!r}")

    for name, value, line in assignments:
        if not CHAIN_NAME_RE.search(name) or not isinstance(value, (ast.List, ast.Tuple)):
            continue
        if not value.elts or not _is_halt_node(value.elts[-1]):
            violations.append(f"line {line}: chain {name} does not end with HALT_SENTINEL / ('halt', 'graceful')")
        if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and "ollama" in n.value.lower()
               for n in ast.walk(value)):
            violations.append(f"line {line}: chain {name} mentions ollama")
    return violations


# =========================================================================== AC1


def test_watchdog_detects_crash_and_restarts(tmp_path: Path) -> None:
    """AC1: a killed daemon is logged as DAEMON_CRASHED and restarted; events are UTC, WAL, indexed."""
    worker = _write_script(tmp_path, "long_worker.py", LONG_WORKER)
    pid_log = tmp_path / "long_pids.log"
    db = tmp_path / "watchdog_cli.db"
    cli = _WatchdogCli(tmp_path, "ac1", _cmd_string(Path(sys.executable), worker, pid_log), db)
    crash_sql = "SELECT id FROM watchdog_events_v2 WHERE event_type='DAEMON_CRASHED' AND pid=?"
    restart_sql = "SELECT id FROM watchdog_events_v2 WHERE event_type IN (?, ?) AND pid=?"
    try:
        def first_alive() -> Optional[int]:
            pids = _read_pids(pid_log)
            return pids[0] if pids and _is_running(pids[0]) else None

        _wait_until(lambda: first_alive() or cli.exited(), 20)
        first_pid = first_alive()
        assert first_pid is not None, f"watchdog never started a live worker.\n{cli.output()}"

        psutil.Process(first_pid).kill()

        def second_alive() -> Optional[int]:
            return next((p for p in _read_pids(pid_log) if p != first_pid and _is_running(p)), None)

        _wait_until(lambda: second_alive() or cli.exited(), 20)
        second_pid = second_alive()
        assert second_pid is not None, f"worker {first_pid} was not restarted.\n{cli.output()}"
        assert not cli.exited(), f"watchdog exited after a single crash.\n{cli.output()}"

        _wait_until(lambda: _try_db_rows(db, crash_sql, (first_pid,))
                    and _try_db_rows(db, restart_sql, (*RESTART_EVENTS, second_pid)), 10)
        out = cli.output()

        journal = _db_rows(db, "PRAGMA journal_mode")[0][0]
        assert str(journal).lower() == "wal", f"journal_mode is {journal!r}, expected wal.\n{out}"
        columns = {row[1] for row in _db_rows(db, "PRAGMA table_info(watchdog_events_v2)")}
        required = {"id", "daemon_name", "event_type", "pid", "details", "timestamp"}
        assert required <= columns, f"watchdog_events_v2 missing columns {sorted(required - columns)}.\n{out}"

        crash_ids = [r[0] for r in _db_rows(db, crash_sql, (first_pid,))]
        assert crash_ids, f"no DAEMON_CRASHED row for pid {first_pid}.\n{out}"
        restart_ids = [r[0] for r in _db_rows(db, restart_sql, (*RESTART_EVENTS, second_pid))]
        assert restart_ids, f"no DAEMON_RESTARTED/RESTART row for pid {second_pid}.\n{out}"
        assert max(restart_ids) > min(crash_ids), f"restart row must be logged after the crash row.\n{out}"

        now = datetime.now(timezone.utc)
        for row_id, ts in _db_rows(db, "SELECT id, timestamp FROM watchdog_events_v2"):
            parsed = _assert_utc_iso(ts, f"watchdog_events_v2 row {row_id}")
            assert abs(now - parsed) < timedelta(minutes=5), f"row {row_id} timestamp {ts!r} not recent"

        index = _db_rows(db, "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_watchdog_events_v2_type'")
        assert index, f"index ix_watchdog_events_v2_type missing.\n{out}"
    finally:
        cli.close()
        for pid in _read_pids(pid_log):
            _kill_if_matches(pid, worker.name)

    # b) in-process API
    wd_mod = _watchdog()
    api = wd_mod.WatchdogDB(tmp_path / "api.db")
    api.init_schema()
    api.log_event("kanban_worker", "DAEMON_CRASHED", 4242, "exit code 9")
    for i in range(5):
        api.log_event("kanban_worker", "DAEMON_RESTARTED", 5000 + i)

    crashed = api.get_events(event_type="DAEMON_CRASHED")
    assert len(crashed) == 1, f"expected one DAEMON_CRASHED event, got {crashed!r}"
    event = crashed[0]
    assert isinstance(event, dict)
    assert event["pid"] == 4242
    assert event["details"] == "exit code 9", f"4th positional argument must be details: {event!r}"
    assert event["daemon_name"] == "kanban_worker"
    assert "timestamp" in event, f"event dict lacks 'timestamp': {event!r}"
    _assert_utc_iso(event["timestamp"], "get_events")

    limited = api.get_events(limit=3)
    assert len(limited) == 3 and all(isinstance(e, dict) for e in limited), limited
    assert len(api.get_events(event_type="DAEMON_RESTARTED", limit=2)) == 2


# =========================================================================== AC2


def test_watchdog_spawn_storm_circuit_breaker(tmp_path: Path) -> None:
    """AC2: 3 crashes inside the window trip the breaker; no further respawns happen."""
    worker = _write_script(tmp_path, "crash_worker.py", CRASH_WORKER)

    # a) CLI
    pid_log = tmp_path / "storm_cli_pids.log"
    db = tmp_path / "storm_cli.db"
    cli = _WatchdogCli(tmp_path, "ac2", _cmd_string(Path(sys.executable), worker, pid_log), db)
    storm_sql = "SELECT id FROM watchdog_events_v2 WHERE event_type='SPAWN_STORM_DETECTED' ORDER BY id"
    try:
        _wait_until(lambda: _try_db_rows(db, storm_sql) or cli.exited(), 30, interval=0.1)
        storm_rows = _try_db_rows(db, storm_sql)
        assert storm_rows, f"no SPAWN_STORM_DETECTED event recorded.\n{cli.output()}"

        spawns = len(_read_pids(pid_log))
        time.sleep(2.0)
        spawns_after = len(_read_pids(pid_log))
        out = cli.output()
        assert spawns_after == spawns, f"respawned after breaker tripped ({spawns} -> {spawns_after}).\n{out}"
        assert 3 <= spawns <= 4, f"expected 3-4 spawns before the breaker, got {spawns}.\n{out}"
        crash_ids = [r[0] for r in _db_rows(db, "SELECT id FROM watchdog_events_v2 WHERE event_type='DAEMON_CRASHED'")]
        assert 3 <= len(crash_ids) <= 4, f"expected 3-4 DAEMON_CRASHED rows, got {len(crash_ids)}.\n{out}"
        assert max(crash_ids) < storm_rows[0][0], f"crash rows must precede the storm row.\n{out}"
    finally:
        cli.close()
        for pid in _read_pids(pid_log):
            _kill_if_matches(pid, worker.name)

    # b) in-process
    wd_mod = _watchdog()
    pid_log_b = tmp_path / "storm_api_pids.log"
    db_b = tmp_path / "storm_api.db"
    wd = wd_mod.DaemonWatchdog([sys.executable, str(worker), str(pid_log_b)], db_b,
                               crash_threshold=3, window_seconds=30.0, poll_interval=0.05)
    try:
        wd.spawn_daemon()
        result = True
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = wd.poll_cycle()
            if result is False:
                break
            time.sleep(0.05)
        assert result is False, "poll_cycle never returned False despite a crash loop"
        count = len(_read_pids(pid_log_b))
        assert wd.poll_cycle() is False, "a tripped breaker must stay tripped"
        time.sleep(0.5)
        assert len(_read_pids(pid_log_b)) == count, "poll_cycle spawned a worker after the breaker tripped"
        assert wd_mod.WatchdogDB(db_b).get_events(event_type="SPAWN_STORM_DETECTED"), \
            "SPAWN_STORM_DETECTED not logged by the in-process watchdog"
    finally:
        wd.terminate()
        for pid in _read_pids(pid_log_b):
            _kill_if_matches(pid, worker.name)


# =========================================================================== AC3

QUOTA_DDL = (
    "CREATE TABLE IF NOT EXISTS quota_state (provider TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'QUOTA_OK', "
    "updated_at TEXT NOT NULL, details TEXT)"
)
LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS credit_ledger_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
    "agent_name TEXT NOT NULL, cost REAL NOT NULL, timestamp TEXT NOT NULL, date TEXT NOT NULL)"
)


def test_idle_sidecar_cpu_and_credit_gating(tmp_path: Path) -> None:
    """AC3: heavy tasks need idle CPU AND QUOTA_OK AND remaining daily budget (live DB reads)."""
    sc = _sidecar()
    ledger = tmp_path / "ledger.db"
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    yesterday_dt = now - timedelta(days=1)
    yesterday = yesterday_dt.date().isoformat()

    # Simulate the Phase-1 init_db having created the shared tables first.
    conn = sqlite3.connect(str(ledger), timeout=5)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(QUOTA_DDL)
        conn.execute(LEDGER_DDL)
        conn.execute("INSERT INTO quota_state (provider, status, updated_at, details) VALUES (?, ?, ?, ?)",
                     ("default", "QUOTA_EXHAUSTED", now.isoformat(), "forced 429"))
        conn.executemany(
            "INSERT INTO credit_ledger_v2 (task_id, agent_name, cost, timestamp, date) VALUES (?, ?, ?, ?, ?)",
            [("task-1", "agent_a", 0.30, now.isoformat(), today),
             ("task-2", "agent_a", 0.20, now.isoformat(), today),
             ("task-0", "agent_a", 5.00, yesterday_dt.isoformat(), yesterday)])
        conn.commit()
    finally:
        conn.close()

    db = sc.IdleSidecarDB(ledger, daily_budget=1.0)
    db.init_schema()
    assert db.get_quota_status() == "QUOTA_EXHAUSTED"
    assert abs(db.get_daily_spend() - 0.5) < 1e-9
    assert abs(db.get_daily_spend(target_date=yesterday) - 5.0) < 1e-9
    assert sc.IdleSidecarDB(ledger, daily_budget=1.0).get_quota_status() == "QUOTA_EXHAUSTED"

    idle_engine = sc.HeavyTaskPolicyEngine(db, sc.HardwareMonitor(cpu_threshold=100.0))
    assert idle_engine.can_execute_heavy(estimated_cost=0.1) is False, "exhausted quota must block heavy tasks"

    db.set_quota_status("QUOTA_OK")
    assert _db_rows(ledger, "SELECT status FROM quota_state WHERE provider='default'") == [("QUOTA_OK",)]
    assert db.get_quota_status() == "QUOTA_OK"
    heavy_types = sc.HEAVY_WORKFLOW_TYPES
    assert len(heavy_types) > 0 and all(isinstance(t, str) for t in heavy_types), heavy_types
    assert idle_engine.can_execute_heavy(estimated_cost=0.1) is True
    assert idle_engine.can_execute_heavy(estimated_cost=0.1, workflow_type=sorted(heavy_types)[0]) is True

    db.record_spend("task-9", "agent_b", 0.1)
    rows = _db_rows(ledger, "SELECT agent_name, cost, date FROM credit_ledger_v2 WHERE task_id='task-9'")
    assert len(rows) == 1 and rows[0][0] == "agent_b" and abs(rows[0][1] - 0.1) < 1e-9, rows
    assert rows[0][2] == datetime.now(timezone.utc).date().isoformat(), rows
    assert abs(db.get_daily_spend() - 0.6) < 1e-9
    assert idle_engine.can_execute_heavy(estimated_cost=0.5) is False, "0.6 + 0.5 exceeds the 1.0 budget"
    assert idle_engine.can_execute_heavy(estimated_cost=0.3) is True

    assert idle_engine.is_heavy_task(estimated_cost=0.5) is True
    assert idle_engine.is_heavy_task(estimated_cost=0.0) is False
    for wt in heavy_types:
        assert idle_engine.is_heavy_task(workflow_type=wt, estimated_cost=0.0) is True, wt
    assert idle_engine.is_heavy_task(workflow_type="lightweight_lint_zz", estimated_cost=0.0) is False

    # Real CPU load: quota is OK and budget remains, so only the CPU gate can block now.
    load_procs: list[subprocess.Popen] = []
    try:
        for _ in range(os.cpu_count() or 1):
            load_procs.append(subprocess.Popen(
                [sys.executable, "-c", "while True: pass"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW, encoding="utf-8"))
        probe = sc.HardwareMonitor(cpu_threshold=50.0)
        loaded = _wait_until(lambda: probe.get_cpu_percent(interval=0.5) > 50.0, 10, interval=0.0)
        assert loaded, "could not drive system CPU above 50% with busy-loop processes"
        busy_engine = sc.HeavyTaskPolicyEngine(db, sc.HardwareMonitor(cpu_threshold=50.0))
        assert busy_engine.monitor.is_cpu_idle(interval=0.3) is False
        assert busy_engine.can_execute_heavy(estimated_cost=0.1) is False, "busy CPU must block heavy tasks"
    finally:
        for proc in load_procs:
            _kill_tree(proc.pid)
            proc.wait(timeout=10)
    assert idle_engine.can_execute_heavy(estimated_cost=0.1) is True


# =========================================================================== AC4


def test_subprocess_flags_and_clean_termination(tmp_path: Path) -> None:
    """AC4: literal subprocess.CREATE_NO_WINDOW + utf-8 on every spawn; terminate() leaves no orphans."""
    report: list[str] = []
    for path in TARGET_PATHS:
        violations, popen_calls = _subprocess_violations(path)
        report.extend(f"{path.name} {v}" for v in violations)
        if path == WATCHDOG_PATH and popen_calls == 0:
            report.append("watchdog.py contains no subprocess.Popen call")
    assert not report, "subprocess AST rule violated:\n" + "\n".join(report)

    worker = _write_script(tmp_path, "tree_worker.py", TREE_WORKER)
    wd_mod = _watchdog()

    # a) in-process terminate()
    json_a = tmp_path / "tree_api.json"
    wd = wd_mod.DaemonWatchdog([sys.executable, str(worker), str(json_a)], tmp_path / "tree_api.db",
                               crash_threshold=3, window_seconds=30.0, poll_interval=0.05)
    pids_a: list[int] = []
    try:
        proc = wd.spawn_daemon()
        assert isinstance(proc, subprocess.Popen)
        info = _wait_until(lambda: _read_json(json_a), 20)
        assert info, "tree worker never reported its pids"
        pids_a = [info["worker"], info["grandchild"]]
        created = {pid: _create_time(pid) for pid in pids_a}
        assert all(_is_running(pid) for pid in pids_a), f"worker/grandchild not alive: {info}"
        wd.terminate()
        gone = _wait_until(lambda: all(_is_gone(p, created[p]) for p in pids_a), 10)
        assert gone, f"terminate() left processes behind: {[p for p in pids_a if not _is_gone(p, created[p])]}"
        wd.terminate()  # idempotent
    finally:
        wd.terminate()
        for pid in pids_a:
            _kill_if_matches(pid, worker.name)
            _kill_if_matches(pid, "time.sleep(600)")

    # b) CLI: terminating the watchdog process must take the whole tree down
    json_b = tmp_path / "tree_cli.json"
    cli = _WatchdogCli(tmp_path, "ac4", _cmd_string(Path(sys.executable), worker, json_b), tmp_path / "tree_cli.db")
    pids_b: list[int] = []
    survivors: list[int] = []
    try:
        _wait_until(lambda: _read_json(json_b) or cli.exited(), 20)
        info = _read_json(json_b)
        assert info, f"watchdog CLI never started the tree worker.\n{cli.output()}"
        pids_b = [info["worker"], info["grandchild"]]
        created = {pid: _create_time(pid) for pid in pids_b}
        assert all(_is_running(pid) for pid in pids_b), f"worker/grandchild not alive: {info}"
        cli.proc.terminate()
        _wait_until(lambda: all(_is_gone(p, created[p]) for p in pids_b), 15)
        survivors = [p for p in pids_b if not _is_gone(p, created[p])]
    finally:
        cli.close()
        for pid in pids_b:
            _kill_if_matches(pid, worker.name)
            _kill_if_matches(pid, "time.sleep(600)")
    assert not survivors, f"orphans left after terminating the watchdog CLI: {survivors}"


# =========================================================================== AC5


def test_offline_isolation_and_routing_sentinels(tmp_path: Path) -> None:
    """AC5: no network imports/activity; fallback chains end in HALT_SENTINEL; ollama is forbidden."""
    report: list[str] = []
    for path in TARGET_PATHS:
        report.extend(f"{path.name} {v}" for v in _offline_violations(path))
    assert not report, "offline/routing AST rule violated:\n" + "\n".join(report)

    for module in (_watchdog(), _sidecar()):
        name = Path(module.__file__).name
        assert module.HALT_SENTINEL == HALT, f"{name}.HALT_SENTINEL = {module.HALT_SENTINEL!r}"
        assert isinstance(module.FORBIDDEN_PROVIDERS, frozenset), \
            f"{name}.FORBIDDEN_PROVIDERS must be a frozenset, got {type(module.FORBIDDEN_PROVIDERS).__name__}"
        assert "ollama" in module.FORBIDDEN_PROVIDERS, f"{name}: ollama not forbidden"
        for attr, value in vars(module).items():
            if not CHAIN_NAME_RE.search(attr) or not isinstance(value, (list, tuple)) or not value:
                continue
            if not all(isinstance(item, tuple) for item in value):
                continue
            assert tuple(value[-1]) == HALT, f"{name}.{attr} must end with {HALT}, got {value!r}"
            assert "ollama" not in repr(value).lower(), f"{name}.{attr} routes to ollama: {value!r}"

    harness = _write_script(tmp_path, "offline_harness.py", OFFLINE_HARNESS)
    env = dict(os.environ)
    env.update({
        "DOCKER_HOST": "tcp://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    })
    result = subprocess.run(
        [sys.executable, str(harness), str(WATCHDOG_PATH), str(SIDECAR_PATH), str(tmp_path)],
        capture_output=True, timeout=60, cwd=str(tmp_path), env=env,
        creationflags=_NO_WINDOW, encoding="utf-8",
    )
    assert result.returncode == 0, f"offline harness failed (rc={result.returncode}):\n{result.stderr}\n{result.stdout}"
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, f"offline harness printed nothing.\n{result.stderr}"
    payload = json.loads(lines[-1])
    assert payload["net_events"] == [], f"network activity detected: {payload['net_events']}"
    assert payload["allowed"] is True, f"heavy task refused in offline harness: {payload}"
