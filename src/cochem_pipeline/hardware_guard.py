"""Admission limits measured from the host running the pipeline.

This module does not issue leases or count workers. The SQLite job board must
atomically claim each job with ``max_workers=guard.capacity()`` and issue its
fencing token in the same transaction. A process-local semaphore cannot enforce
the four-agent ceiling across multiple daemon processes.

Memory and disk settings named ``mb`` use binary megabytes (MiB). These are
admission reserves, not OS-enforced limits on a provider CLI's later growth.
"""

from __future__ import annotations

import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

_MIB = 1024 * 1024
_HARD_MAX_AGENTS = 4


@dataclass(frozen=True)
class HardwareGuard:
    """Compute a conservative global worker ceiling using fresh OS telemetry.

    ``max_agents`` above four is accepted but capped at four. A ceiling of zero
    pauses new work. ``workspace`` must exist so its actual filesystem can be
    measured; failed probes deny admission rather than inventing telemetry.
    """

    max_agents: int = 4
    min_free_memory_mb: float = 1024
    min_free_disk_mb: float = 512
    workspace: Path = field(default_factory=Path.cwd)
    per_agent_memory_mb: float = 256
    max_cpu_percent: float = 95

    def __post_init__(self) -> None:
        if isinstance(self.max_agents, bool) or not isinstance(self.max_agents, int):
            raise ValueError("max_agents must be a nonnegative integer")
        if self.max_agents < 0:
            raise ValueError("max_agents must be a nonnegative integer")
        for name in ("min_free_memory_mb", "min_free_disk_mb", "per_agent_memory_mb", "max_cpu_percent"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")
        if self.per_agent_memory_mb <= 0:
            raise ValueError("per_agent_memory_mb must be greater than zero")
        if self.max_cpu_percent > 100:
            raise ValueError("max_cpu_percent must not exceed 100")
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())

    def snapshot(self) -> dict[str, Any]:
        """Return measurements, derived capacity, and any admission blockers.

        CPU usage is sampled over 50 ms. Using a timed sample avoids psutil's
        meaningless first nonblocking CPU reading of zero. Affinity restricts
        the CPU count when supported by the operating system.
        """
        result: dict[str, Any] = {
            "measured_at": time.time(),
            "workspace": str(self.workspace),
            "max_agents": self.max_agents,
            "hard_max_agents": _HARD_MAX_AGENTS,
            "min_free_memory_mb": self.min_free_memory_mb,
            "min_free_disk_mb": self.min_free_disk_mb,
            "per_agent_memory_mb": self.per_agent_memory_mb,
            "max_cpu_percent": self.max_cpu_percent,
            "cpu_count": None,
            "cpu_percent": None,
            "memory_total_mb": None,
            "memory_available_mb": None,
            "disk_total_mb": None,
            "disk_free_mb": None,
            "capacity": 0,
            "reasons": [],
        }
        reasons: list[str] = result["reasons"]
        try:
            cpu_count = psutil.cpu_count(logical=True)
            if cpu_count is None or cpu_count < 1:
                raise OSError("OS did not report a usable CPU count")
            process = psutil.Process()
            if hasattr(process, "cpu_affinity"):
                affinity = process.cpu_affinity()
                if not affinity:
                    raise OSError("OS reported no CPUs in process affinity")
                cpu_count = min(cpu_count, len(affinity))
            result["cpu_count"] = cpu_count
            result["cpu_percent"] = psutil.cpu_percent(interval=0.05)
        except (OSError, psutil.Error, ValueError) as exc:
            reasons.append(f"CPU measurement failed: {exc}")

        try:
            memory = psutil.virtual_memory()
            result["memory_total_mb"] = memory.total / _MIB
            result["memory_available_mb"] = memory.available / _MIB
        except (OSError, psutil.Error, ValueError) as exc:
            reasons.append(f"Memory measurement failed: {exc}")

        try:
            disk = shutil.disk_usage(self.workspace)
            result["disk_total_mb"] = disk.total / _MIB
            result["disk_free_mb"] = disk.free / _MIB
        except (OSError, ValueError) as exc:
            reasons.append(f"Disk measurement failed: {exc}")

        if reasons:
            return result
        if self.max_agents == 0:
            reasons.append("Worker admission is disabled by max_agents=0")
        if result["cpu_percent"] > self.max_cpu_percent:
            reasons.append("CPU utilization exceeds the configured limit")
        if result["disk_free_mb"] < self.min_free_disk_mb:
            reasons.append("Free disk space is below the configured reserve")
        memory_slots = max(
            0,
            math.floor((result["memory_available_mb"] - self.min_free_memory_mb) / self.per_agent_memory_mb),
        )
        if memory_slots == 0:
            reasons.append("Available memory cannot accommodate a worker and the configured reserve")
        if not reasons:
            result["capacity"] = min(_HARD_MAX_AGENTS, self.max_agents, result["cpu_count"], memory_slots)
        return result

    def capacity(self) -> int:
        """Return the current total worker ceiling for an atomic board claim."""
        return int(self.snapshot()["capacity"])
