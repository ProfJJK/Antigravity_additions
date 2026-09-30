# src/cochem/warden/ladder.py

`python
"""Host Warden Health Recovery Escalation Ladder FSM (MC-HW-54, MC-HW-55)."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH: Path = REPO_ROOT / "job_board.db"


class HealthEscalationLadder:
    """FSM coordinating Tier 1 -> Tier 2 -> Tier 3 recovery escalation."""

    def __init__(self, vm_name: str = "CoChem-Quarantine-VM") -> None:
        self.vm_name = vm_name
        self.tier = 1

    TIER_ACTIONS: dict[int, str] = {
        1: "service_restart",
        2: "vm_reboot",
        3: "golden_rollback",
    }

    def escalate(self) -> int:
        """Advances escalation tier: 1 (service restart) -> 2 (VM reboot) -> 3 (rollback)."""
        if self.tier < 3:
            self.tier += 1
        return self.tier

    def reset(self) -> None:
        """Resets escalation tier upon healthy recovery."""
        self.tier = 1

    def execute_current_tier(self) -> dict[str, Any]:
        """Executes the recovery action for the current tier via physical host modules."""
        from .restart_service import trigger_graceful_restart
        from .rollback import restore_golden_checkpoint
        from .vm_control import reboot_quarantine_vm

        action = self.TIER_ACTIONS.get(self.tier, "unknown")
        success: bool

        if self.tier == 1:
            success = trigger_graceful_restart("cochem-guest-daemon")
        elif self.tier == 2:
            success = reboot_quarantine_vm(self.vm_name)
        elif self.tier == 3:
            success = restore_golden_checkpoint(self.vm_name, "CoChem-Golden-State")
        else:
            raise ValueError(f"Invalid recovery tier: {self.tier}")

        return {
            "status": "DISPATCHED",
            "tier": self.tier,
            "action": action,
            "success": bool(success),
        }

    def step(self) -> dict[str, Any]:
        """Executes current recovery tier; automatically escalates if recovery fails."""
        result = self.execute_current_tier()
        if not result.get("success", False) and self.tier < 3:
            self.escalate()
        return result


def emit_recovery_event(
    conn: sqlite3.Connection | str | Path | None = None,
    event_data: dict[str, Any] | None = None,
) -> int:
    """Emits escalation audit record to telemetry log table in job_board.db (or provided SQLite conn)."""
    if isinstance(conn, dict) and event_data is None:
        event_data = conn
        conn = None

    if event_data is None:
        event_data = {}

    close_conn = False
    active_conn: sqlite3.Connection
    if isinstance(conn, sqlite3.Connection):
        active_conn = conn
    elif isinstance(conn, (str, Path)):
        active_conn = sqlite3.connect(str(conn))
        close_conn = True
    else:
        active_conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        close_conn = True

    try:
        active_conn.execute(
            """CREATE TABLE IF NOT EXISTS recovery_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                tier INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL
            )"""
        )
        tier = int(event_data.get("tier", 1))
        action = str(event_data.get("action", "unspecified_recovery"))
        details = json.dumps(event_data)

        cur = active_conn.execute(
            "INSERT INTO recovery_telemetry (tier, action, details) VALUES (?, ?, ?)",
            (tier, action, details),
        )
        active_conn.commit()
        return cur.lastrowid or 0
    finally:
        if close_conn:
            active_conn.close()

`
