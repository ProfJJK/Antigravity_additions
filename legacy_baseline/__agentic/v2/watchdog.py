"""Daemon watchdog with spawn-storm circuit breaker for CoChem Pipeline v2.

The watchdog supervises a single long-running daemon (for example the Kanban
worker). Each call to :meth:`DaemonWatchdog.poll_cycle` checks the child once:

* still running -> nothing to do;
* exited        -> log ``DAEMON_CRASHED`` and respawn (``DAEMON_RESTARTED``);
* ``crash_threshold`` crashes inside ``window_seconds`` -> log
  ``SPAWN_STORM_DETECTED``, trip the circuit breaker and stop respawning.

All lifecycle events are written to the ``watchdog_events_v2`` SQLite table
with UTC ISO-8601 timestamps. The module is fully offline: it uses only the
standard library, never opens a network connection and never talks to Docker.
Every subprocess is spawned with ``creationflags=subprocess.CREATE_NO_WINDOW``
and ``encoding="utf-8"``.

Process-tree ownership:

* Windows - the daemon is placed in a Job Object with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. Terminating the job kills the daemon
  and every descendant; if the watchdog itself is hard-killed the OS closes the
  job handle and the whole tree dies with it.
* POSIX - the daemon leads its own session / process group, which is
  signalled as a unit.

CLI::

    python watchdog.py --cmd "python worker.py" --db events.db
        [--threshold 3] [--window 30] [--poll 1.0] [--daemon-name NAME]

Exit codes: 0 clean stop (including SIGTERM), 1 circuit breaker tripped,
130 Ctrl+C / SIGINT.

Routing constants (``HALT_SENTINEL``, ``FORBIDDEN_PROVIDERS`` and
``DEFAULT_ROUTING_FALLBACK_CHAIN``) are shared with the rest of the v2
pipeline: every fallback chain must terminate in ``HALT_SENTINEL`` and must
never route to a forbidden (local) provider.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Optional

# POSIX Popen rejects non-zero creationflags, so the flag is 0 off Windows.
if not hasattr(subprocess, "CREATE_NO_WINDOW"):
    subprocess.CREATE_NO_WINDOW = 0

__all__ = [
    "DaemonWatchdog",
    "WatchdogDB",
    "main",
    "CREATE_NO_WINDOW",
    "HALT_SENTINEL",
    "FORBIDDEN_PROVIDERS",
    "DEFAULT_ROUTING_FALLBACK_CHAIN",
    "EXIT_CLEAN",
    "EXIT_TRIPPED",
    "EXIT_INTERRUPTED",
]

#: Windows process-creation flag that suppresses the console window (0 on POSIX).
CREATE_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW
#: Terminal element of every fallback chain: stop gracefully instead of routing further.
HALT_SENTINEL: tuple[str, str] = ("halt", "graceful")
#: Providers that must never appear in any routing or fallback chain.
FORBIDDEN_PROVIDERS: frozenset[str] = frozenset({"ollama"})
#: The watchdog never routes work itself; its only fallback is a graceful halt.
DEFAULT_ROUTING_FALLBACK_CHAIN: list[tuple[str, str]] = [HALT_SENTINEL]

EXIT_CLEAN = 0
EXIT_TRIPPED = 1
EXIT_INTERRUPTED = 130


def _utc_now_iso() -> str:
    """Return the current time as a timezone-aware UTC ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _JobBasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JobExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobBasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


class _ProcessTreeJob:
    """Windows Job Object that owns a daemon and every process it spawns.

    Unlike ``taskkill /T``, which walks parent PIDs and so cannot reach the
    children of a daemon that already exited, the job keeps tracking all
    descendants. ``KILL_ON_JOB_CLOSE`` also kills the tree if the watchdog
    itself dies without calling :meth:`DaemonWatchdog.terminate`: the job
    handle is non-inheritable, so the watchdog holds the only reference and
    the OS closes it when the watchdog process ends.
    """

    def __init__(self, pid: int) -> None:
        """Create a kill-on-close job and assign process ``pid`` to it.

        Raises:
            OSError: If the job cannot be created or the process cannot be assigned.
        """
        self._handle = _kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _JobExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                self._handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            proc_handle = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            if not proc_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not _kernel32.AssignProcessToJobObject(self._handle, proc_handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _kernel32.CloseHandle(proc_handle)
        except OSError:
            self.close()
            raise

    def kill_all(self) -> None:
        """Terminate every process still in the job and release the job. Idempotent."""
        if self._handle:
            _kernel32.TerminateJobObject(self._handle, 1)
        self.close()

    def close(self) -> None:
        """Release the job handle (killing any remaining members). Idempotent."""
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


WATCHDOG_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS watchdog_events_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    daemon_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    pid INTEGER,
    details TEXT,
    timestamp TEXT NOT NULL
)
"""
WATCHDOG_EVENTS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_watchdog_events_v2_type ON watchdog_events_v2(event_type, timestamp)"
)
_EVENT_COLUMNS = ("id", "daemon_name", "event_type", "pid", "details", "timestamp")


class WatchdogDB:
    """SQLite event log for watchdog lifecycle events (``watchdog_events_v2``).

    A fresh, short-lived connection is opened per operation so the object is
    safe to share between the watchdog loop and external readers. The schema
    is created lazily on first use if :meth:`init_schema` was not called.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Bind to ``db_path``; the file and its parent directory are created on first use."""
        self.db_path = Path(db_path)
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        """Open a connection that waits up to 5 s on a locked database."""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_schema(self) -> None:
        """Create the schema once per instance if it has not been created yet."""
        if not self._schema_ready:
            self.init_schema()

    def init_schema(self) -> None:
        """Enable WAL, create ``watchdog_events_v2`` and its index. Idempotent.

        A table created by an older schema revision without a ``timestamp``
        column is migrated in place: the column is added and back-filled from
        the legacy ``created_at`` column (SQLite ``CURRENT_TIMESTAMP`` is UTC).
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(WATCHDOG_EVENTS_DDL)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(watchdog_events_v2)")}
            if "timestamp" not in columns:
                conn.execute("ALTER TABLE watchdog_events_v2 ADD COLUMN timestamp TEXT")
                if "created_at" in columns:
                    conn.execute(
                        "UPDATE watchdog_events_v2 "
                        "SET timestamp = strftime('%Y-%m-%dT%H:%M:%S+00:00', created_at) "
                        "WHERE timestamp IS NULL AND created_at IS NOT NULL"
                    )
            conn.execute(WATCHDOG_EVENTS_INDEX_DDL)
            conn.commit()
        finally:
            conn.close()
        self._schema_ready = True

    def log_event(
        self,
        daemon_name: str,
        event_type: str,
        pid: Optional[int] = None,
        details: Optional[str] = None,
    ) -> int:
        """Insert a single event row stamped with the current UTC time and commit.

        Args:
            daemon_name: Logical name of the supervised daemon.
            event_type: Event label, e.g. ``DAEMON_CRASHED`` or ``SPAWN_STORM_DETECTED``.
            pid: OS process id the event refers to, if any.
            details: Free-form human-readable context.

        Returns:
            The row id of the inserted event.
        """
        self._ensure_schema()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO watchdog_events_v2 (daemon_name, event_type, pid, details, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (daemon_name, event_type, pid, details, _utc_now_iso()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_events(
        self,
        event_type: Optional[str] = None,
        limit: Optional[int] = 50,
        daemon_name: Optional[str] = None,
    ) -> list[dict]:
        """Return the most recent ``limit`` events (oldest first) as dicts.

        Args:
            event_type: Only return events of this type.
            limit: Maximum number of rows (the newest ones); ``None`` for all.
            daemon_name: Only return events of this daemon.

        Raises:
            ValueError: If ``limit`` is negative.
        """
        if limit is not None and int(limit) < 0:
            raise ValueError(f"limit must be >= 0, got {limit!r}")
        self._ensure_schema()
        clauses: list[str] = []
        params: list[object] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if daemon_name is not None:
            clauses.append("daemon_name = ?")
            params.append(daemon_name)
        sql = f"SELECT {', '.join(_EVENT_COLUMNS)} FROM watchdog_events_v2"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
        rows.reverse()
        return rows


class DaemonWatchdog:
    """Restarts a crashed daemon; trips a circuit breaker on rapid crash loops.

    Attributes:
        proc: The currently supervised ``subprocess.Popen`` (``None`` when not running).
        current_pid: PID of ``proc`` (``None`` when not running).
        is_tripped: ``True`` once a spawn storm was detected; no further respawns happen.
        crash_times: Monotonic timestamps of crashes still inside the sliding window.
    """

    def __init__(
        self,
        target_cmd: list[str],
        db_path: str | Path,
        crash_threshold: int = 3,
        window_seconds: float = 30.0,
        poll_interval: float = 0.05,
        daemon_name: str = "kanban_worker",
    ) -> None:
        """Configure the watchdog and initialise its event database.

        Args:
            target_cmd: Command line (argv list) of the daemon to supervise.
            db_path: SQLite file receiving ``watchdog_events_v2`` rows.
            crash_threshold: Crashes within ``window_seconds`` that trip the breaker (>= 1).
            window_seconds: Length of the sliding crash-counting window (> 0).
            poll_interval: Sleep between polls in :meth:`run` (>= 0).
            daemon_name: Logical name recorded with every event.

        Raises:
            ValueError: If any argument is out of range or ``target_cmd`` is empty.
        """
        self.target_cmd = [str(part) for part in target_cmd]
        if not self.target_cmd:
            raise ValueError("target_cmd must contain at least one element")
        self.crash_threshold = int(crash_threshold)
        if self.crash_threshold < 1:
            raise ValueError(f"crash_threshold must be >= 1, got {crash_threshold!r}")
        self.window_seconds = float(window_seconds)
        if not self.window_seconds > 0.0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds!r}")
        self.poll_interval = float(poll_interval)
        if not self.poll_interval >= 0.0:
            raise ValueError(f"poll_interval must be >= 0, got {poll_interval!r}")
        self.daemon_name = daemon_name
        self.proc: Optional[subprocess.Popen] = None
        self.current_pid: Optional[int] = None
        self.is_tripped: bool = False
        self.crash_times: list[float] = []
        self._job: Optional[_ProcessTreeJob] = None
        self._stop_event = threading.Event()
        self.db = WatchdogDB(db_path)
        self.db.init_schema()

    def spawn_daemon(self) -> subprocess.Popen:
        """Start the target command without a console window and return its ``Popen``.

        If a previously spawned daemon is still running it is returned as-is,
        so repeated calls never orphan a live child.

        Raises:
            OSError: If the command cannot be executed.
        """
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        # Clean up leftovers of a previous daemon before losing track of them.
        self._reap_descendants()
        # On POSIX the daemon leads its own session so its whole process group
        # can be signalled; on Windows the tree is tracked by a Job Object.
        self.proc = subprocess.Popen(
            self.target_cmd,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8",
            start_new_session=(os.name != "nt"),
        )
        self.current_pid = self.proc.pid
        if os.name == "nt":
            try:
                self._job = _ProcessTreeJob(self.proc.pid)
            except OSError:
                # Assignment can be refused (e.g. restrictive parent job);
                # terminate() then falls back to taskkill /T.
                self._job = None
        return self.proc

    def _reap_descendants(self) -> None:
        """Kill processes left behind by the last daemon, even if the daemon itself exited.

        Windows: terminate the daemon's Job Object. POSIX: SIGKILL the
        daemon's process group (its pgid equals its pid via ``start_new_session``).
        """
        if os.name == "nt":
            if self._job is not None:
                self._job.kill_all()
                self._job = None
            return
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return

    def _register_crash(self, pid: Optional[int]) -> bool:
        """Record a crash in the sliding window; trip the breaker and return True on a spawn storm."""
        now = time.monotonic()
        self.crash_times.append(now)
        self.crash_times = [t for t in self.crash_times if now - t <= self.window_seconds]
        if len(self.crash_times) < self.crash_threshold:
            return False

        self.is_tripped = True
        self.proc = None
        self.current_pid = None
        self.db.log_event(
            self.daemon_name,
            "SPAWN_STORM_DETECTED",
            pid,
            f"Spawn storm: {len(self.crash_times)} crashes in {self.window_seconds}s; respawning halted",
        )
        return True

    def _try_spawn(self, event_type: Optional[str]) -> bool:
        """Spawn the daemon, logging ``event_type`` on success.

        A spawn failure is logged as ``SPAWN_FAILED`` and counted as a crash,
        so a permanently broken command still trips the circuit breaker
        instead of raising out of the poll loop.

        Returns:
            False if the breaker tripped, True otherwise.
        """
        try:
            proc = self.spawn_daemon()
        except OSError as exc:
            self.proc = None
            self.current_pid = None
            self.db.log_event(self.daemon_name, "SPAWN_FAILED", None, f"{type(exc).__name__}: {exc}")
            return not self._register_crash(None)
        if event_type is not None:
            self.db.log_event(self.daemon_name, event_type, proc.pid, "Restarted daemon after crash")
        return True

    def poll_cycle(self) -> bool:
        """Check the daemon once, restarting it if it exited.

        Returns:
            False when the circuit breaker is tripped (now or earlier), True otherwise.
        """
        if self.is_tripped:
            return False
        if self.proc is None:
            return self._try_spawn(None)

        ret = self.proc.poll()
        if ret is None:
            return True

        crashed_pid = self.current_pid or self.proc.pid
        # The daemon is gone, but anything it spawned would otherwise be orphaned.
        self._reap_descendants()
        self.db.log_event(self.daemon_name, "DAEMON_CRASHED", crashed_pid, f"Exit code {ret}")
        if self._register_crash(crashed_pid):
            return False
        return self._try_spawn("DAEMON_RESTARTED")

    def request_stop(self) -> None:
        """Ask :meth:`run` to leave its loop at the next opportunity (signal-safe)."""
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        """True once :meth:`request_stop` was called."""
        return self._stop_event.is_set()

    def run(self) -> None:
        """Poll until the breaker trips or a stop is requested; always terminates the daemon on exit."""
        try:
            while not self._stop_event.is_set():
                if not self.poll_cycle():
                    break
                self._stop_event.wait(self.poll_interval)
        finally:
            self.terminate()

    def _kill_tree_windows(self, proc: subprocess.Popen) -> None:
        """Kill ``proc`` and all of its descendants with ``taskkill /T /F``.

        Used only when no Job Object could be attached. The open ``Popen``
        handle keeps the PID from being reused, so the tree walk cannot hit an
        unrelated process. Falls back to killing only the direct child if
        ``taskkill`` is unavailable or fails.
        """
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding="utf-8",
            )
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        if proc.poll() is None:
            proc.kill()

    def _terminate_group_posix(self, proc: subprocess.Popen) -> None:
        """SIGTERM the daemon's process group, escalating to SIGKILL after 5 seconds."""
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
            return
        # The leader exited; reap any group members that ignored SIGTERM.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def terminate(self) -> None:
        """Stop the daemon and every process it spawned, leaving no orphans. Idempotent.

        Windows: the daemon's Job Object is terminated (``taskkill /T /F`` is
        the fallback when no job could be attached). POSIX: SIGTERM to the
        daemon's process group, escalating to SIGKILL after 5 seconds.
        """
        proc = self.proc
        if proc is not None and proc.poll() is None:
            if os.name == "nt":
                if self._job is not None:
                    self._reap_descendants()
                else:
                    self._kill_tree_windows(proc)
            else:
                self._terminate_group_posix(proc)
        self._reap_descendants()
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.proc = None
        self.current_pid = None


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (new short options plus the legacy long aliases)."""
    parser = argparse.ArgumentParser(
        description="Supervise a CoChem v2 daemon process with a spawn-storm circuit breaker.",
        allow_abbrev=False,
    )
    parser.add_argument("--cmd", default=None,
                        help='Daemon command line as one string, POSIX shell-quoted (e.g. "python worker.py").')
    parser.add_argument("--db", "--db-path", dest="db", required=True, help="SQLite database for watchdog events.")
    parser.add_argument("--threshold", "--crash-threshold", dest="threshold", type=int, default=3,
                        help="Crashes inside the window that trip the breaker (default 3).")
    parser.add_argument("--window", "--window-seconds", dest="window", type=float, default=30.0,
                        help="Sliding crash window in seconds (default 30).")
    parser.add_argument("--poll", "--poll-interval", dest="poll", type=float, default=1.0,
                        help="Seconds between liveness checks (default 1.0).")
    parser.add_argument("--daemon-name", default="kanban_worker", help="Logical daemon name for event rows.")
    parser.add_argument("target_cmd", nargs=argparse.REMAINDER,
                        help="Alternative to --cmd: the daemon command after '--'.")
    return parser


def _resolve_target_cmd(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[str]:
    """Return the daemon argv from ``--cmd`` or the trailing ``-- cmd ...`` form (exactly one)."""
    trailing = list(args.target_cmd)
    if trailing and trailing[0] == "--":
        trailing = trailing[1:]
    if args.cmd is not None and trailing:
        parser.error("give the daemon command either via --cmd or after '--', not both")
    if args.cmd is not None:
        try:
            target = shlex.split(args.cmd, posix=True)
        except ValueError as exc:
            parser.error(f"cannot parse --cmd: {exc}")
        if not target:
            parser.error("--cmd must not be empty")
        return target
    if not trailing:
        parser.error("a daemon command is required (--cmd \"...\" or -- cmd ...)")
    return trailing


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: supervise the daemon until a spawn storm or a stop signal.

    SIGTERM, SIGINT (and SIGBREAK on Windows) stop the loop; ``run()`` then
    terminates the daemon tree before returning.

    Returns:
        0 on clean stop, 1 when the circuit breaker tripped, 130 on Ctrl+C / SIGINT.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    target_cmd = _resolve_target_cmd(parser, args)

    try:
        watchdog = DaemonWatchdog(
            target_cmd=target_cmd,
            db_path=args.db,
            crash_threshold=args.threshold,
            window_seconds=args.window,
            poll_interval=args.poll,
            daemon_name=args.daemon_name,
        )
    except ValueError as exc:
        parser.error(str(exc))

    received: list[int] = []

    def _on_signal(signum: int, _frame: object) -> None:
        received.append(signum)
        watchdog.request_stop()

    stop_signals = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):
        stop_signals.append(signal.SIGBREAK)
    previous: dict[int, object] = {}
    if threading.current_thread() is threading.main_thread():
        for sig in stop_signals:
            previous[sig] = signal.signal(sig, _on_signal)

    watchdog.db.log_event(
        args.daemon_name,
        "WATCHDOG_STARTED",
        os.getpid(),
        f"cmd={shlex.join(target_cmd)} threshold={args.threshold} window={args.window}s poll={args.poll}s",
    )
    interrupted = False
    try:
        watchdog.run()
    except KeyboardInterrupt:
        interrupted = True
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    if interrupted or signal.SIGINT in received:
        code, reason = EXIT_INTERRUPTED, "interrupted (SIGINT)"
    elif watchdog.is_tripped:
        code, reason = EXIT_TRIPPED, "circuit breaker tripped"
    elif received:
        code, reason = EXIT_CLEAN, f"stopped by signal {received[0]}"
    else:
        code, reason = EXIT_CLEAN, "clean stop"
    watchdog.db.log_event(args.daemon_name, "WATCHDOG_STOPPED", os.getpid(), f"exit={code} reason={reason}")
    return code


if __name__ == "__main__":
    sys.exit(main())