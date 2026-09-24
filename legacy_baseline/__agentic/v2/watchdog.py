"""Daemon watchdog with spawn-storm circuit breaker for CoChem Pipeline v2."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Optional

__all__ = [
    "DaemonWatchdog",
    "WatchdogDB",
    "CREATE_NO_WINDOW",
    "HALT_SENTINEL",
    "FORBIDDEN_PROVIDERS",
    "DEFAULT_ROUTING_FALLBACK_CHAIN",
]

CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
HALT_SENTINEL: tuple[str, str] = ("halt", "graceful")
FORBIDDEN_PROVIDERS: tuple[str, ...] = ("ollama",)
DEFAULT_ROUTING_FALLBACK_CHAIN: list[tuple[str, str]] = [HALT_SENTINEL]

WATCHDOG_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS watchdog_events_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER DEFAULT NULL,
    daemon_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    pid INTEGER DEFAULT NULL,
    details TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class WatchdogDB:
    """SQLite event log for watchdog lifecycle events (watchdog_events_v2)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_schema(self) -> None:
        if not self._schema_ready:
            self.init_schema()

    def init_schema(self) -> None:
        """Enable WAL mode and create watchdog_events_v2 if missing."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(WATCHDOG_EVENTS_DDL)
            conn.commit()
        finally:
            conn.close()
        self._schema_ready = True

    def log_event(
        self,
        daemon_name: str,
        event_type: str,
        pid: Optional[int] = None,
        task_id: Optional[int] = None,
        details: Optional[str] = None,
    ) -> None:
        """Insert a single event row and commit."""
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO watchdog_events_v2 (task_id, daemon_name, event_type, pid, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, daemon_name, event_type, pid, details),
            )
            conn.commit()
        finally:
            conn.close()

    def get_events(
        self,
        event_type: Optional[str] = None,
        daemon_name: Optional[str] = None,
    ) -> list[dict]:
        """Return events (oldest first), optionally filtered by type and daemon name."""
        self._ensure_schema()
        clauses: list[str] = []
        params: list[str] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if daemon_name is not None:
            clauses.append("daemon_name = ?")
            params.append(daemon_name)
        sql = "SELECT * FROM watchdog_events_v2"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC"

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


class DaemonWatchdog:
    """Restarts a crashed daemon; trips a circuit breaker on rapid crash loops."""

    def __init__(
        self,
        target_cmd: list[str],
        db_path: str | Path,
        crash_threshold: int = 3,
        window_seconds: float = 30.0,
        poll_interval: float = 0.05,
        daemon_name: str = "kanban_worker",
    ) -> None:
        self.target_cmd = list(target_cmd)
        self.crash_threshold = int(crash_threshold)
        self.window_seconds = float(window_seconds)
        self.poll_interval = float(poll_interval)
        self.daemon_name = daemon_name
        self.proc: Optional[subprocess.Popen] = None
        self.current_pid: Optional[int] = None
        self.is_tripped: bool = False
        self.crash_times: list[float] = []
        self.db = WatchdogDB(db_path)
        self.db.init_schema()

    def spawn_daemon(self) -> subprocess.Popen:
        """Start the target command without a console window."""
        self.proc = subprocess.Popen(
            self.target_cmd,
            creationflags=CREATE_NO_WINDOW,
            encoding="utf-8",
        )
        self.current_pid = self.proc.pid
        return self.proc

    def poll_cycle(self) -> bool:
        """Check the daemon once. Returns False when the circuit breaker is tripped."""
        if self.is_tripped:
            return False
        if self.proc is None:
            self.spawn_daemon()
            return True

        ret = self.proc.poll()
        if ret is None:
            return True

        crashed_pid = self.current_pid or self.proc.pid
        self.db.log_event(
            self.daemon_name,
            "DAEMON_CRASHED",
            pid=crashed_pid,
            details=f"Exit code {ret}",
        )
        now = time.monotonic()
        self.crash_times.append(now)
        self.crash_times = [t for t in self.crash_times if now - t <= self.window_seconds]

        if len(self.crash_times) >= self.crash_threshold:
            self.is_tripped = True
            self.proc = None
            self.current_pid = None
            self.db.log_event(
                self.daemon_name,
                "SPAWN_STORM_DETECTED",
                pid=crashed_pid,
                details=f"Spawn storm: {len(self.crash_times)} crashes in {self.window_seconds}s",
            )
            return False

        new_proc = self.spawn_daemon()
        self.db.log_event(
            self.daemon_name,
            "DAEMON_RESTARTED",
            pid=new_proc.pid,
            details="Restarted daemon after crash",
        )
        return True

    def run(self) -> None:
        """Poll until the circuit breaker trips; always terminates the daemon on exit."""
        try:
            while self.poll_cycle():
                time.sleep(self.poll_interval)
        finally:
            self.terminate()

    def terminate(self) -> None:
        """Terminate the daemon gracefully, escalating to kill after 5 seconds."""
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.proc = None
        self.current_pid = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervise a CoChem v2 daemon process.")
    parser.add_argument("--db-path", required=True, help="SQLite database for watchdog events.")
    parser.add_argument("--daemon-name", default="kanban_worker")
    parser.add_argument("--crash-threshold", type=int, default=3)
    parser.add_argument("--window-seconds", type=float, default=30.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("target_cmd", nargs=argparse.REMAINDER, help="Command to supervise (after --).")
    args = parser.parse_args(argv)

    target_cmd = [part for part in args.target_cmd if part != "--"]
    if not target_cmd:
        parser.error("a target command is required")

    watchdog = DaemonWatchdog(
        target_cmd=target_cmd,
        db_path=args.db_path,
        crash_threshold=args.crash_threshold,
        window_seconds=args.window_seconds,
        poll_interval=args.poll_interval,
        daemon_name=args.daemon_name,
    )
    try:
        watchdog.run()
    except KeyboardInterrupt:
        return 130
    return 1 if watchdog.is_tripped else 0


if __name__ == "__main__":
    sys.exit(main())
