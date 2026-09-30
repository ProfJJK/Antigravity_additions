# src/cochem/warden/health.py

`python
"""Host Warden SRE Health Polling Loop (MC-HW-42)."""
from __future__ import annotations
import os
import psutil
from typing import Any

def poll_guest_health(pipe_name: str = r"\\.\pipe\cochem_warden_vm", timeout_sec: int = 5) -> dict[str, Any]:
    """Polls VM and guest process health using system process sampling and pipe probes."""
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    pipe_responsive = probe_named_pipe(pipe_name, timeout_sec=float(timeout_sec))
    is_healthy = pipe_responsive and (cpu < 95.0) and (mem.percent < 95.0)
    active_pids: list[int] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            pid = proc.info.get("pid")
            if isinstance(pid, int):
                active_pids.append(pid)
                if len(active_pids) >= 5:
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return {
        "status": "HEALTHY" if is_healthy else "DEGRADED",
        "cpu_percent": cpu,
        "ram_used_bytes": mem.used,
        "ram_total_bytes": mem.total,
        "ram_percent": mem.percent,
        "pipe_target": pipe_name,
        "pipe_responsive": pipe_responsive,
        "active_pids": active_pids,
    }


def probe_named_pipe(pipe_name: str, timeout_sec: float = 1.0) -> bool:
    """Probes physical named pipe endpoint via WaitNamedPipe or fallback filesystem liveness."""
    if not pipe_name or not isinstance(pipe_name, str):
        return False
    timeout_ms = max(1, int(float(timeout_sec) * 1000))
    if pipe_name.startswith(r"\\.\pipe") or pipe_name.startswith("//./pipe"):
        try:
            import pywintypes
            import win32pipe
            win32pipe.WaitNamedPipe(pipe_name, timeout_ms)
            return True
        except pywintypes.error as err:
            return err.winerror in (121, 231)
        except (ImportError, OSError):
            return False
    return os.path.exists(pipe_name)


def run_health_polling_loop(
    pipe_name: str = r"\\.\pipe\cochem_warden_vm",
    interval_sec: float = 15.0,
    max_consecutive_failures: int = 3,
    on_failure_action: Any = None,
    max_iterations: int | None = None,
) -> int:
    """SRE polling loop monitoring guest VM health and tracking consecutive failures (SRS-412-01-FR-003/004)."""
    import time
    consecutive_failures = 0
    iterations = 0

    while max_iterations is None or iterations < max_iterations:
        sample = poll_guest_health(pipe_name=pipe_name)
        iterations += 1

        if sample.get("status") != "HEALTHY":
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures and on_failure_action is not None:
                on_failure_action(consecutive_failures, sample)
        else:
            consecutive_failures = 0

        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(interval_sec)

    return iterations

`
