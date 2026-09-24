"""Idle-hardware and credit-budget gating for heavy tasks in CoChem Pipeline v2.

A task is "heavy" when its estimated cost is at least
``DEFAULT_HEAVY_COST_THRESHOLD`` credits. The :class:`HeavyTaskPolicyEngine`
allows a heavy task only when all three gates pass:

1. provider quota state is ``QUOTA_OK`` (``quota_state`` table);
2. today's spend plus the estimated cost stays within the daily budget
   (``credit_ledger_v2`` table);
3. system CPU utilisation is at or below the idle threshold (psutil).

State lives in a local SQLite database in WAL mode. The module is fully
offline: it never opens a network connection and never talks to Docker. It
spawns no subprocesses; ``CREATE_NO_WINDOW`` is exported so callers share a
single definition of the flag.

Routing constants (``HALT_SENTINEL``, ``FORBIDDEN_PROVIDERS`` and
``DEFAULT_ROUTING_FALLBACK_CHAIN``) match the rest of the v2 pipeline: every
fallback chain must terminate in ``HALT_SENTINEL`` and must never route to a
forbidden (local) provider.
"""

from __future__ import annotations

import math
from pathlib import Path
import sqlite3
import subprocess
from typing import Optional

import psutil

__all__ = [
    "IdleSidecarDB",
    "HardwareMonitor",
    "HeavyTaskPolicyEngine",
    "DEFAULT_IDLE_CPU_THRESHOLD",
    "DEFAULT_DAILY_CREDIT_BUDGET",
    "DEFAULT_HEAVY_COST_THRESHOLD",
    "CREATE_NO_WINDOW",
    "HALT_SENTINEL",
    "FORBIDDEN_PROVIDERS",
    "DEFAULT_ROUTING_FALLBACK_CHAIN",
]

#: Windows process-creation flag that suppresses the console window.
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
#: Terminal element of every fallback chain: stop gracefully instead of routing further.
HALT_SENTINEL: tuple[str, str] = ("halt", "graceful")
#: Providers that must never appear in any routing or fallback chain.
FORBIDDEN_PROVIDERS: tuple[str, ...] = ("ollama",)
#: When a heavy task is refused, the sidecar never reroutes it; it halts gracefully.
DEFAULT_ROUTING_FALLBACK_CHAIN: list[tuple[str, str]] = [HALT_SENTINEL]

#: CPU utilisation (percent) at or below which the machine counts as idle.
DEFAULT_IDLE_CPU_THRESHOLD: float = 25.0
#: Maximum credits that may be spent per UTC day.
DEFAULT_DAILY_CREDIT_BUDGET: float = 25.00
#: Estimated cost (credits) at or above which a task is considered heavy.
DEFAULT_HEAVY_COST_THRESHOLD: float = 0.05

QUOTA_OK = "QUOTA_OK"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
QUOTA_RECOVERING = "QUOTA_RECOVERING"
QUOTA_STATES: tuple[str, ...] = (QUOTA_OK, QUOTA_EXHAUSTED, QUOTA_RECOVERING)

SIDECAR_DDL = """
CREATE TABLE IF NOT EXISTS quota_state (
    provider TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('QUOTA_OK', 'QUOTA_EXHAUSTED', 'QUOTA_RECOVERING')),
    backoff_until TEXT DEFAULT NULL,
    last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS credit_ledger_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT DEFAULT NULL,
    provider TEXT DEFAULT NULL,
    cost REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


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


class IdleSidecarDB:
    """SQLite store for provider quota state and the daily credit ledger.

    A fresh connection is opened per operation. The schema is created lazily
    on first use if :meth:`init_schema` was not called explicitly.
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
        """Enable WAL mode and create ``quota_state`` / ``credit_ledger_v2`` if missing. Idempotent."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SIDECAR_DDL)
            conn.commit()
        finally:
            conn.close()
        self._schema_ready = True

    def set_quota_status(self, status: str, provider: str = "default", details: Optional[str] = None) -> None:
        """Upsert the quota state for ``provider``.

        Raises:
            ValueError: If ``status`` is not one of ``QUOTA_STATES``.
        """
        if status not in QUOTA_STATES:
            raise ValueError(f"invalid quota status {status!r}; expected one of {QUOTA_STATES}")
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO quota_state (provider, state, details, last_checked_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(provider) DO UPDATE SET state = excluded.state, "
                "details = excluded.details, last_checked_at = excluded.last_checked_at",
                (provider, status, details),
            )
            conn.commit()
        finally:
            conn.close()

    def get_quota_status(self, provider: str = "default") -> str:
        """Return the provider's quota state.

        Without a row for ``provider`` the answer is conservative: any
        exhausted provider yields ``QUOTA_EXHAUSTED``, otherwise ``QUOTA_OK``.
        """
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute("SELECT state FROM quota_state WHERE provider = ?", (provider,)).fetchone()
            if row is not None:
                return str(row[0])
            exhausted = conn.execute(
                "SELECT 1 FROM quota_state WHERE state = ? LIMIT 1", (QUOTA_EXHAUSTED,)
            ).fetchone()
        finally:
            conn.close()
        return QUOTA_EXHAUSTED if exhausted is not None else QUOTA_OK

    def record_spend(self, task_id: str | int, provider: str, cost: float) -> None:
        """Append a spend entry to ``credit_ledger_v2``.

        Raises:
            ValueError: If ``cost`` is negative or not finite.
        """
        amount = _validate_amount(cost, "cost")
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO credit_ledger_v2 (task_id, provider, cost) VALUES (?, ?, ?)",
                (str(task_id), provider, amount),
            )
            conn.commit()
        finally:
            conn.close()

    def get_daily_spend(self, provider: Optional[str] = None) -> float:
        """Sum today's (UTC) spend, optionally for a single provider."""
        self._ensure_schema()
        sql = "SELECT COALESCE(SUM(cost), 0.0) FROM credit_ledger_v2 WHERE DATE(created_at) = DATE('now')"
        params: tuple[str, ...] = ()
        if provider is not None:
            sql += " AND provider = ?"
            params = (provider,)
        conn = self._connect()
        try:
            return float(conn.execute(sql, params).fetchone()[0])
        finally:
            conn.close()


class HardwareMonitor:
    """Samples system CPU utilisation to decide whether the machine is idle."""

    def __init__(self, cpu_threshold: float = DEFAULT_IDLE_CPU_THRESHOLD) -> None:
        """Set the idle threshold in percent; values <= 0 mean "never idle"."""
        self.cpu_threshold = float(cpu_threshold)

    def get_cpu_percent(self, interval: float = 0.05) -> float:
        """Return system-wide CPU utilisation over ``interval`` seconds, clamped to [0, 100].

        Negative intervals are treated as 0 (non-blocking sample since the last call).
        """
        cpu = float(psutil.cpu_percent(interval=max(0.0, float(interval))))
        return min(100.0, max(0.0, cpu))

    def is_cpu_idle(self, interval: float = 0.05) -> bool:
        """Return True if CPU utilisation is at or below the idle threshold."""
        # A non-positive threshold means "never idle" rather than "idle only at exactly 0%".
        if self.cpu_threshold <= 0.0:
            return False
        cpu = self.get_cpu_percent(interval=interval)
        return cpu <= self.cpu_threshold


class HeavyTaskPolicyEngine:
    """Allows heavy tasks only when quota is OK, budget remains, and the CPU is idle."""

    def __init__(
        self,
        db: IdleSidecarDB,
        monitor: HardwareMonitor,
        heavy_cost_threshold: float = DEFAULT_HEAVY_COST_THRESHOLD,
    ) -> None:
        """Combine a quota/ledger store with a hardware monitor.

        Raises:
            ValueError: If ``heavy_cost_threshold`` is negative or not finite.
        """
        self.db = db
        self.monitor = monitor
        self.heavy_cost_threshold = _validate_amount(heavy_cost_threshold, "heavy_cost_threshold")

    def is_heavy_task(self, estimated_cost: float) -> bool:
        """Return True if ``estimated_cost`` reaches the heavy-task threshold.

        Raises:
            ValueError: If ``estimated_cost`` is negative or not finite.
        """
        return _validate_amount(estimated_cost, "estimated_cost") >= self.heavy_cost_threshold

    def can_execute_heavy(self, estimated_cost: float = 0.0) -> bool:
        """Return True only if quota, daily budget and CPU idleness all permit the task.

        Gates are checked cheapest first; the CPU sample (which blocks briefly)
        runs only when quota and budget already pass.

        Raises:
            ValueError: If ``estimated_cost`` is negative or not finite.
        """
        cost = _validate_amount(estimated_cost, "estimated_cost")
        if self.db.get_quota_status() != QUOTA_OK:
            return False
        if self.db.get_daily_spend() + cost > self.db.daily_budget:
            return False
        return self.monitor.is_cpu_idle()
