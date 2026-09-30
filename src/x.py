"""Host Warden diagnostic probe utilities.

Provides:
  * HostWardenStatus: an immutable snapshot of host health and telemetry.
  * get_warden_status(): samples live host telemetry (CPU, memory, pid, clock).
  * validate_warden_payload(): defensive validation of warden payload dicts.
  * HostWardenProbe: readiness flag plus heartbeat tracking.

Telemetry is read from the running host every call. ``psutil`` is used when
installed. If it is not installed, the module falls back to native OS
interfaces: Win32 ``GetSystemTimes`` / ``GlobalMemoryStatusEx`` through
ctypes on Windows, and ``/proc/stat`` / ``/proc/meminfo`` on Linux. No
subprocesses, sockets or hypervisor APIs are used.

The status is ``"HEALTHY"`` when every telemetry probe returned a valid
reading. It is ``"DEGRADED"`` when any probe failed, and the failures are
listed under ``metadata["errors"]``. Resource-pressure thresholds are
reported as separate indicators and do not change the status. That keeps
the status meaning "the warden can observe the host". It does not mean
"the host is idle".
"""
from __future__ import annotations

import math
import os
import platform
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # psutil is the preferred telemetry source (used by cochem.warden.health)
    import psutil as _psutil  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on host environment
    _psutil = None

__all__ = [
    "HostWardenStatus",
    "get_warden_status",
    "validate_warden_payload",
    "HostWardenProbe",
    "STATUS_HEALTHY",
    "STATUS_DEGRADED",
    "REQUIRED_PAYLOAD_FIELDS",
]

STATUS_HEALTHY = "HEALTHY"
STATUS_DEGRADED = "DEGRADED"

REQUIRED_PAYLOAD_FIELDS: Tuple[str, ...] = ("task_id", "domain", "timestamp")

# Pressure indicator thresholds (percent). Reported, not used to flip status.
CPU_PRESSURE_THRESHOLD = 90.0
MEMORY_PRESSURE_THRESHOLD = 90.0


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HostWardenStatus:
    """Immutable snapshot of Host Warden health and host telemetry."""

    status: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        return self.status == STATUS_HEALTHY


# ---------------------------------------------------------------------------
# Native fallback telemetry (used only when psutil is unavailable)
# ---------------------------------------------------------------------------
_cpu_state_lock = threading.Lock()
_last_cpu_times: Optional[Tuple[float, float]] = None  # (idle, total)


def _read_cpu_times_native() -> Tuple[float, float]:
    """Return cumulative (idle, total) CPU time from the OS."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        idle = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            raise OSError("GetSystemTimes failed")

        def _ft(ft: "wintypes.FILETIME") -> float:
            return float((ft.dwHighDateTime << 32) | ft.dwLowDateTime)

        idle_t = _ft(idle)
        # Kernel time includes idle time on Windows.
        total_t = _ft(kernel) + _ft(user)
        return idle_t, total_t

    with open("/proc/stat", "r", encoding="utf-8") as fh:
        first = fh.readline().split()
    if not first or first[0] != "cpu":
        raise OSError("unexpected /proc/stat format")
    values = [float(v) for v in first[1:]]
    idle_t = values[3] + (values[4] if len(values) > 4 else 0.0)  # idle + iowait
    return idle_t, sum(values)


def _cpu_percent_native() -> float:
    """Non-blocking CPU utilisation since the previous call.

    The first call returns 0.0. psutil.cpu_percent(interval=None) behaves the
    same way, because there is no earlier sample to compare against.
    """
    global _last_cpu_times
    idle_t, total_t = _read_cpu_times_native()
    with _cpu_state_lock:
        previous = _last_cpu_times
        _last_cpu_times = (idle_t, total_t)
    if previous is None:
        return 0.0
    d_idle = idle_t - previous[0]
    d_total = total_t - previous[1]
    if d_total <= 0:
        return 0.0
    busy = 100.0 * (1.0 - d_idle / d_total)
    return max(0.0, min(100.0, busy))


def _memory_percent_native() -> float:
    if sys.platform == "win32":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            raise OSError("GlobalMemoryStatusEx failed")
        if stat.ullTotalPhys == 0:
            raise OSError("GlobalMemoryStatusEx reported zero physical memory")
        used = stat.ullTotalPhys - stat.ullAvailPhys
        return 100.0 * used / stat.ullTotalPhys

    info: Dict[str, float] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = float(parts[1])
    total = info.get("MemTotal", 0.0)
    avail = info.get("MemAvailable", info.get("MemFree", 0.0))
    if total <= 0:
        raise OSError("/proc/meminfo reported zero MemTotal")
    return 100.0 * (total - avail) / total


def _sample_cpu_percent() -> Tuple[float, str]:
    if _psutil is not None:
        return float(_psutil.cpu_percent(interval=None)), "psutil"
    return float(_cpu_percent_native()), "native"


def _sample_memory_percent() -> Tuple[float, str]:
    if _psutil is not None:
        return float(_psutil.virtual_memory().percent), "psutil"
    return float(_memory_percent_native()), "native"


def _is_valid_percent(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= 100.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_warden_status() -> HostWardenStatus:
    """Sample live host telemetry and return a HostWardenStatus snapshot."""
    errors: List[str] = []
    metadata: Dict[str, Any] = {
        "pid": os.getpid(),
        "timestamp": time.time(),
        "platform": platform.system() or sys.platform,
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
    }

    try:
        cpu, cpu_source = _sample_cpu_percent()
        if not _is_valid_percent(cpu):
            raise ValueError(f"cpu_percent out of range: {cpu!r}")
        metadata["cpu_percent"] = cpu
        metadata["cpu_source"] = cpu_source
        metadata["cpu_pressure"] = cpu >= CPU_PRESSURE_THRESHOLD
    except Exception as exc:  # noqa: BLE001 - reported as degraded telemetry
        errors.append(f"cpu_percent: {type(exc).__name__}: {exc}")

    try:
        mem, mem_source = _sample_memory_percent()
        if not _is_valid_percent(mem):
            raise ValueError(f"memory_percent out of range: {mem!r}")
        metadata["memory_percent"] = mem
        metadata["memory_source"] = mem_source
        metadata["memory_pressure"] = mem >= MEMORY_PRESSURE_THRESHOLD
    except Exception as exc:  # noqa: BLE001 - reported as degraded telemetry
        errors.append(f"memory_percent: {type(exc).__name__}: {exc}")

    if errors:
        metadata["errors"] = errors
        return HostWardenStatus(status=STATUS_DEGRADED, metadata=metadata)
    return HostWardenStatus(status=STATUS_HEALTHY, metadata=metadata)


def _is_positive_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        if not math.isfinite(value):
            return False
    except OverflowError:  # int too large to convert to float
        return False
    return value > 0


def _is_non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) > 0


def validate_warden_payload(payload: Any) -> bool:
    """Return True only for a dict with valid task_id, domain and timestamp.

    task_id and domain must be non-blank strings. timestamp must be a finite,
    strictly positive int or float, and bool is rejected. The function never
    raises: any malformed input returns False.
    """
    if not isinstance(payload, dict):
        return False
    for key in REQUIRED_PAYLOAD_FIELDS:
        if key not in payload:
            return False
    if not _is_non_blank_str(payload["task_id"]):
        return False
    if not _is_non_blank_str(payload["domain"]):
        return False
    if not _is_positive_finite_number(payload["timestamp"]):
        return False
    return True


class HostWardenProbe:
    """Tracks probe readiness and the most recent heartbeat timestamp."""

    def __init__(self, ready: bool = True) -> None:
        self._lock = threading.Lock()
        self._ready: bool = bool(ready)
        self.last_heartbeat: Optional[float] = None

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def set_ready(self, ready: bool) -> None:
        with self._lock:
            self._ready = bool(ready)

    def record_heartbeat(self, timestamp: float) -> None:
        """Record a heartbeat. Raises ValueError for invalid timestamps.

        On rejection, last_heartbeat is left unchanged.
        """
        if not _is_positive_finite_number(timestamp):
            raise ValueError(
                f"heartbeat timestamp must be a finite positive number, got {timestamp!r}"
            )
        with self._lock:
            self.last_heartbeat = float(timestamp)

    def seconds_since_heartbeat(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds since the last heartbeat, or None if none has been recorded."""
        with self._lock:
            last = self.last_heartbeat
        if last is None:
            return None
        current = time.time() if now is None else float(now)
        return current - last
