"""Idle-hardware and credit-budget gating for heavy tasks in CoChem Pipeline v2.

A task is "heavy" when its workflow type is listed in ``HEAVY_WORKFLOW_TYPES``
or its estimated cost is at least ``DEFAULT_HEAVY_COST_THRESHOLD`` credits.
The :class:`HeavyTaskPolicyEngine` allows a heavy task only when all three
gates pass:

1. provider quota status is ``QUOTA_OK`` (``quota_state`` table);
2. today's (UTC) spend plus the estimated cost stays within the daily budget
   (``credit_ledger_v2`` table);
3. system CPU utilisation is at or below the idle threshold (psutil).

State lives in a local SQLite database in WAL mode, shared with the Phase-1
pipeline tables; every read is a live query on a short-lived connection. The
module is fully offline: it never opens a network connection, never talks to
Docker and spawns no subprocesses.

CLI (continuous monitoring, one JSON gate decision per line)::

    python cochem_idle_sidecar.py --db PATH [--cpu-threshold 25] [--budget 25]
        [--interval 5] [--once] [--cost 0.1] [--workflow-type full_pipeline]

Routing constants (``HALT_SENTINEL``, ``FORBIDDEN_PROVIDERS`` and
``DEFAULT_ROUTING_FALLBACK_CHAIN``) match the rest of the v2 pipeline: every
fallback chain must terminate in ``HALT_SENTINEL`` and must never route to a
forbidden (local) provider.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
from typing import Any, Optional

import psutil

# POSIX Popen rejects non-zero creationflags, so the flag is 0 off Windows.
if not hasattr(subprocess, "CREATE_NO_WINDOW"):
    subprocess.CREATE_NO_WINDOW = 0

__all__ = [
    "IdleSidecarDB",
    "HardwareMonitor",
    "HeavyTaskPolicyEngine",
    "HEAVY_WORKFLOW_TYPES",
    "DEFAULT_IDLE_CPU_THRESHOLD",
    "DEFAULT_DAILY_CREDIT_BUDGET",
    "DEFAULT_HEAVY_COST_THRESHOLD",
    "QUOTA_OK",
    "QUOTA_EXHAUSTED",
    "QUOTA_RECOVERING",
    "QUOTA_STATES",
    "CREATE_NO_WINDOW",
    "HALT_SENTINEL",
    "FORBIDDEN_PROVIDERS",
    "DEFAULT_ROUTING_FALLBACK_CHAIN",
    "main",
]

#: Windows process-creation flag that suppresses the console window (0 on POSIX).
CREATE_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW
#: Terminal element of every fallback chain: stop gracefully instead of routing further.
HALT_SENTINEL: tuple[str, str] = ("halt", "graceful")
#: Providers that must never appear in any routing or fallback chain.
FORBIDDEN_PROVIDERS: frozenset[str] = frozenset({"ollama"})
#: When a heavy task is refused, the sidecar never reroutes it; it halts gracefully.
DEFAULT_ROUTING_FALLBACK_CHAIN: list[tuple[str, str]] = [HALT_SENTINEL]

#: CPU utilisation (percent) at or below which the machine counts as idle.
DEFAULT_IDLE_CPU_THRESHOLD: float = 25.0
#: Maximum credits that may be spent per UTC day.
DEFAULT_DAILY_CREDIT_BUDGET: float = 25.00
#: Estimated cost (credits) at or above which a task is considered heavy.
DEFAULT_HEAVY_COST_THRESHOLD: float = 0.05
#: Default CPU sampling window in seconds for the idle gate.
DEFAULT_CPU_SAMPLE_INTERVAL: float = 0.1

#: Workflow types that are always heavy, regardless of their cost estimate.
HEAVY_WORKFLOW_TYPES: frozenset[str] = frozenset({
    "full_pipeline",
    "srs_generation",
    "tdd_contract",
    "code_generation",
    "deep_research",
    "batch_refactor",
})

QUOTA_OK = "QUOTA_OK"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
QUOTA_RECOVERING = "QUOTA_RECOVERING"
QUOTA_STATES: tuple[str, ...] = (QUOTA_OK, QUOTA_EXHAUSTED, QUOTA_RECOVERING)

QUOTA_STATE_DDL = """
CREATE TABLE IF NOT EXISTS quota_state (
    provider TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'QUOTA_OK',
    updated_at TEXT NOT NULL,
    details TEXT
)
"""
CREDIT_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS credit_ledger_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    cost REAL NOT NULL,
    timestamp TEXT NOT NULL,
    date TEXT NOT NULL
)
"""
CREDIT_LEDGER_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_credit_ledger_v2_date ON credit_ledger_v2(date)",
    "CREATE INDEX IF NOT EXISTS ix_credit_ledger_v2_task ON credit_ledger_v2(task_id)",
)
#: Columns added in place to a credit ledger created by an older schema revision.
_LEDGER_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("agent_name", "TEXT"),
    ("timestamp", "TEXT"),
    ("date", "TEXT"),
)


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _validate_amount(value: float, name: str) -> float:
    """Return ``value`` as a float, rejecting negative, NaN and infinite amounts.

    NaN must be rejected explicitly: every comparison against it is False, so
    it would otherwise slip through the budget gate.

    Raises:
        ValueError: If ``value`` is not a finite, non-negative number.
    """
    amount = float(value)
    if not math.isfinite(amount) or amount < 0.0:
        raise ValueError(f"{name} must be a finite, non-negative number, got {value!r}")
    return amount


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of ``table`` (empty if it does not exist)."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class IdleSidecarDB:
    """SQLite store for provider quota status and the daily credit ledger.

    A fresh, short-lived connection is opened per operation. The schema is
    created lazily on first use if :meth:`init_schema` was not called. Tables
    already created by the Phase-1 pipeline are reused as-is; a legacy
    ``quota_state`` with a ``state`` column instead of ``status`` is read and
    written through that column.
    """

    def __init__(self, db_path: str | Path, daily_budget: float = DEFAULT_DAILY_CREDIT_BUDGET) -> None:
        """Bind to ``db_path`` with a daily credit budget.

        Raises:
            ValueError: If ``daily_budget`` is negative or not finite.
        """
        self.db_path = Path(db_path)
        self.daily_budget = _validate_amount(daily_budget, "daily_budget")
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
        """Enable WAL and create ``quota_state`` / ``credit_ledger_v2`` plus indexes. Idempotent.

        A ledger created by an older revision (without ``agent_name`` /
        ``timestamp`` / ``date``) gets those columns added; ``date`` is
        back-filled from a legacy ``created_at`` column when present.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(QUOTA_STATE_DDL)
            conn.execute(CREDIT_LEDGER_DDL)
            ledger_cols = _table_columns(conn, "credit_ledger_v2")
            for column, decl in _LEDGER_MIGRATION_COLUMNS:
                if column not in ledger_cols:
                    conn.execute(f"ALTER TABLE credit_ledger_v2 ADD COLUMN {column} {decl}")
            if "created_at" in ledger_cols:
                conn.execute(
                    "UPDATE credit_ledger_v2 SET date = DATE(created_at) "
                    "WHERE date IS NULL AND created_at IS NOT NULL"
                )
                conn.execute(
                    "UPDATE credit_ledger_v2 "
                    "SET timestamp = strftime('%Y-%m-%dT%H:%M:%S+00:00', created_at) "
                    "WHERE timestamp IS NULL AND created_at IS NOT NULL"
                )
            for ddl in CREDIT_LEDGER_INDEX_DDL:
                conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()
        self._schema_ready = True

    @staticmethod
    def _quota_columns(conn: sqlite3.Connection) -> tuple[str, Optional[str]]:
        """Return (status column, updated-at column or None) for the existing ``quota_state``.

        Raises:
            sqlite3.OperationalError: If the table has neither ``status`` nor ``state``.
        """
        columns = _table_columns(conn, "quota_state")
        if "status" in columns:
            status_col = "status"
        elif "state" in columns:
            status_col = "state"
        else:
            raise sqlite3.OperationalError("quota_state has neither a 'status' nor a 'state' column")
        if "updated_at" in columns:
            time_col: Optional[str] = "updated_at"
        elif "last_checked_at" in columns:
            time_col = "last_checked_at"
        else:
            time_col = None
        return status_col, time_col

    def set_quota_status(self, status: str, provider: str = "default", details: Optional[str] = None) -> None:
        """Upsert the quota status for ``provider``, stamping ``updated_at`` in UTC.

        Raises:
            ValueError: If ``status`` is not one of ``QUOTA_STATES``.
        """
        if status not in QUOTA_STATES:
            raise ValueError(f"invalid quota status {status!r}; expected one of {QUOTA_STATES}")
        self._ensure_schema()
        conn = self._connect()
        try:
            status_col, time_col = self._quota_columns(conn)
            columns = ["provider", status_col, "details"]
            values: list[Any] = [provider, status, details]
            if time_col is not None:
                columns.append(time_col)
                values.append(_utc_now().isoformat())
            updates = ", ".join(f"{col} = excluded.{col}" for col in columns[1:])
            conn.execute(
                f"INSERT INTO quota_state ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT(provider) DO UPDATE SET {updates}",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def get_quota_status(self, provider: str = "default") -> str:
        """Return the provider's quota status (live read).

        Without a row for ``provider`` the answer is conservative: any
        exhausted provider yields ``QUOTA_EXHAUSTED``, otherwise ``QUOTA_OK``.
        """
        self._ensure_schema()
        conn = self._connect()
        try:
            status_col, _ = self._quota_columns(conn)
            row = conn.execute(
                f"SELECT {status_col} FROM quota_state WHERE provider = ?", (provider,)
            ).fetchone()
            if row is not None:
                return str(row[0])
            exhausted = conn.execute(
                f"SELECT 1 FROM quota_state WHERE {status_col} = ? LIMIT 1", (QUOTA_EXHAUSTED,)
            ).fetchone()
        finally:
            conn.close()
        return QUOTA_EXHAUSTED if exhausted is not None else QUOTA_OK

    def record_spend(self, task_id: str | int, agent_name: str, cost: float) -> int:
        """Append a spend entry (UTC timestamp and UTC date) to ``credit_ledger_v2``.

        Returns:
            The row id of the new ledger entry.

        Raises:
            ValueError: If ``cost`` is negative or not finite.
        """
        amount = _validate_amount(cost, "cost")
        now = _utc_now()
        self._ensure_schema()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO credit_ledger_v2 (task_id, agent_name, cost, timestamp, date) VALUES (?, ?, ?, ?, ?)",
                (str(task_id), str(agent_name), amount, now.isoformat(), now.date().isoformat()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_daily_spend(self, target_date: Optional[str | date] = None) -> float:
        """Sum the ledger for ``target_date`` (``YYYY-MM-DD`` or a date; default: today in UTC)."""
        if target_date is None:
            day = _utc_now().date().isoformat()
        elif isinstance(target_date, date):
            day = target_date.isoformat()
        else:
            day = date.fromisoformat(str(target_date)).isoformat()
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0.0) FROM credit_ledger_v2 WHERE date = ?", (day,)
            ).fetchone()
            return float(row[0])
        finally:
            conn.close()


class HardwareMonitor:
    """Samples system CPU utilisation to decide whether the machine is idle."""

    def __init__(self, cpu_threshold: float = DEFAULT_IDLE_CPU_THRESHOLD) -> None:
        """Set the idle threshold in percent; values <= 0 mean "never idle"."""
        self.cpu_threshold = float(cpu_threshold)

    def get_cpu_percent(self, interval: float = DEFAULT_CPU_SAMPLE_INTERVAL) -> float:
        """Return system-wide CPU utilisation over ``interval`` seconds, clamped to [0, 100].

        Negative intervals are treated as 0 (non-blocking sample since the last call).
        """
        cpu = float(psutil.cpu_percent(interval=max(0.0, float(interval))))
        return min(100.0, max(0.0, cpu))

    def is_idle_at(self, cpu_percent: float) -> bool:
        """Return True if ``cpu_percent`` counts as idle under this monitor's threshold."""
        # A non-positive threshold means "never idle" rather than "idle only at exactly 0%".
        if self.cpu_threshold <= 0.0:
            return False
        return float(cpu_percent) <= self.cpu_threshold

    def is_cpu_idle(self, interval: float = DEFAULT_CPU_SAMPLE_INTERVAL) -> bool:
        """Return True if CPU utilisation is at or below the idle threshold."""
        if self.cpu_threshold <= 0.0:
            return False
        return self.is_idle_at(self.get_cpu_percent(interval=interval))


class HeavyTaskPolicyEngine:
    """Allows heavy tasks only when quota is OK, budget remains, and the CPU is idle."""

    def __init__(
        self,
        db: IdleSidecarDB,
        monitor: HardwareMonitor,
        heavy_cost_threshold: float = DEFAULT_HEAVY_COST_THRESHOLD,
        provider: str = "default",
    ) -> None:
        """Combine a quota/ledger store with a hardware monitor.

        Raises:
            ValueError: If ``heavy_cost_threshold`` is negative or not finite.
        """
        self.db = db
        self.monitor = monitor
        self.heavy_cost_threshold = _validate_amount(heavy_cost_threshold, "heavy_cost_threshold")
        self.provider = provider

    def is_heavy_task(self, workflow_type: Optional[str] = None, estimated_cost: float = 0.0) -> bool:
        """Return True if the workflow type is heavy or the cost reaches the heavy threshold.

        Raises:
            ValueError: If ``estimated_cost`` is negative or not finite.
        """
        cost = _validate_amount(estimated_cost, "estimated_cost")
        if workflow_type is not None and workflow_type in HEAVY_WORKFLOW_TYPES:
            return True
        return cost >= self.heavy_cost_threshold

    def can_execute_heavy(self, estimated_cost: float = 0.0, workflow_type: Optional[str] = None) -> bool:
        """Return True only if quota, daily budget and CPU idleness all permit the task.

        Gates are checked cheapest first; the CPU sample (which blocks briefly)
        runs only when quota and budget already pass.

        Raises:
            ValueError: If ``estimated_cost`` is negative or not finite.
        """
        return bool(self.evaluate(estimated_cost, workflow_type, short_circuit=True)["allowed"])

    def evaluate(
        self,
        estimated_cost: float = 0.0,
        workflow_type: Optional[str] = None,
        short_circuit: bool = False,
    ) -> dict[str, Any]:
        """Evaluate every gate and return a JSON-serialisable decision record.

        Args:
            estimated_cost: Credits the task is expected to spend.
            workflow_type: Optional workflow label (used for ``is_heavy``).
            short_circuit: Stop at the first failing gate (skips the CPU sample
                when quota or budget already refuse the task).

        Raises:
            ValueError: If ``estimated_cost`` is negative or not finite.
        """
        cost = _validate_amount(estimated_cost, "estimated_cost")
        decision: dict[str, Any] = {
            "timestamp": _utc_now().isoformat(),
            "provider": self.provider,
            "workflow_type": workflow_type,
            "estimated_cost": cost,
            "is_heavy": self.is_heavy_task(workflow_type, cost),
            "quota_status": None,
            "daily_spend": None,
            "daily_budget": self.db.daily_budget,
            "cpu_percent": None,
            "cpu_threshold": self.monitor.cpu_threshold,
            "cpu_idle": None,
            "allowed": False,
            "reason": "",
        }
        failures: list[str] = []

        quota = self.db.get_quota_status(self.provider)
        decision["quota_status"] = quota
        if quota != QUOTA_OK:
            failures.append(f"quota {quota}")
            if short_circuit:
                decision["reason"] = "; ".join(failures)
                return decision

        spend = self.db.get_daily_spend()
        decision["daily_spend"] = spend
        if spend + cost > self.db.daily_budget:
            failures.append(f"budget: {spend:.4f} + {cost:.4f} > {self.db.daily_budget:.4f}")
            if short_circuit:
                decision["reason"] = "; ".join(failures)
                return decision

        cpu = self.monitor.get_cpu_percent()
        idle = self.monitor.is_idle_at(cpu)
        decision["cpu_percent"] = cpu
        decision["cpu_idle"] = idle
        if not idle:
            failures.append(f"cpu busy: {cpu:.1f}% > {self.monitor.cpu_threshold:.1f}%")

        decision["allowed"] = not failures
        decision["reason"] = "; ".join(failures) if failures else "all gates pass"
        return decision


def main(argv: list[str] | None = None) -> int:
    """Continuously print one JSON gate decision per cycle until SIGINT/SIGTERM (or once with --once).

    Returns:
        0 on a clean stop.
    """
    parser = argparse.ArgumentParser(description="CoChem v2 idle sidecar: heavy-task gate monitor.")
    parser.add_argument("--db", required=True, help="SQLite database holding quota_state / credit_ledger_v2.")
    parser.add_argument("--cpu-threshold", type=float, default=DEFAULT_IDLE_CPU_THRESHOLD)
    parser.add_argument("--budget", type=float, default=DEFAULT_DAILY_CREDIT_BUDGET, help="Daily credit budget.")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between decisions.")
    parser.add_argument("--once", action="store_true", help="Print a single decision and exit.")
    parser.add_argument("--cost", type=float, default=0.0, help="Estimated cost of the candidate task.")
    parser.add_argument("--workflow-type", default=None, help="Workflow type of the candidate task.")
    parser.add_argument("--provider", default="default", help="Quota provider row to check.")
    args = parser.parse_args(argv)

    try:
        db = IdleSidecarDB(args.db, daily_budget=args.budget)
        _validate_amount(args.cost, "--cost")
        interval = _validate_amount(args.interval, "--interval")
    except ValueError as exc:
        parser.error(str(exc))
    db.init_schema()
    engine = HeavyTaskPolicyEngine(db, HardwareMonitor(args.cpu_threshold), provider=args.provider)

    stop = threading.Event()

    def _on_signal(_signum: int, _frame: object) -> None:
        stop.set()

    previous: dict[int, object] = {}
    if threading.current_thread() is threading.main_thread():
        stop_signals = [signal.SIGTERM, signal.SIGINT]
        if hasattr(signal, "SIGBREAK"):
            stop_signals.append(signal.SIGBREAK)
        for sig in stop_signals:
            previous[sig] = signal.signal(sig, _on_signal)
    try:
        while not stop.is_set():
            decision = engine.evaluate(args.cost, args.workflow_type)
            sys.stdout.write(json.dumps(decision) + "\n")
            sys.stdout.flush()
            if args.once:
                break
            stop.wait(interval)
    except KeyboardInterrupt:
        stop.set()
        return 130  # Ctrl+C; matches watchdog.EXIT_INTERRUPTED
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    sys.exit(main())