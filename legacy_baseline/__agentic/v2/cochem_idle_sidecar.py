"""Idle-hardware and credit-budget gating for heavy tasks in CoChem Pipeline v2."""

from __future__ import annotations

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

CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
HALT_SENTINEL: tuple[str, str] = ("halt", "graceful")
FORBIDDEN_PROVIDERS: tuple[str, ...] = ("ollama",)
DEFAULT_ROUTING_FALLBACK_CHAIN: list[tuple[str, str]] = [HALT_SENTINEL]

DEFAULT_IDLE_CPU_THRESHOLD: float = 25.0
DEFAULT_DAILY_CREDIT_BUDGET: float = 25.00
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


class IdleSidecarDB:
    """SQLite store for provider quota state and the daily credit ledger."""

    def __init__(self, db_path: str | Path, daily_budget: float = DEFAULT_DAILY_CREDIT_BUDGET) -> None:
        self.db_path = Path(db_path)
        self.daily_budget = float(daily_budget)
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_schema(self) -> None:
        if not self._schema_ready:
            self.init_schema()

    def init_schema(self) -> None:
        """Enable WAL mode and create quota_state / credit_ledger_v2 if missing."""
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
        """Upsert the quota state for a provider."""
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
        """Return the provider's state; without a row, any exhausted provider wins over QUOTA_OK."""
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
        """Append a spend entry to credit_ledger_v2."""
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO credit_ledger_v2 (task_id, provider, cost) VALUES (?, ?, ?)",
                (str(task_id), provider, float(cost)),
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
        self.cpu_threshold = float(cpu_threshold)

    def get_cpu_percent(self, interval: float = 0.05) -> float:
        cpu = float(psutil.cpu_percent(interval=interval))
        return min(100.0, max(0.0, cpu))

    def is_cpu_idle(self, interval: float = 0.05) -> bool:
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
        self.db = db
        self.monitor = monitor
        self.heavy_cost_threshold = float(heavy_cost_threshold)

    def is_heavy_task(self, estimated_cost: float) -> bool:
        return float(estimated_cost) >= self.heavy_cost_threshold

    def can_execute_heavy(self, estimated_cost: float = 0.0) -> bool:
        if self.db.get_quota_status() != QUOTA_OK:
            return False
        if (self.db.get_daily_spend() + float(estimated_cost)) > self.db.daily_budget:
            return False
        if not self.monitor.is_cpu_idle():
            return False
        return True
