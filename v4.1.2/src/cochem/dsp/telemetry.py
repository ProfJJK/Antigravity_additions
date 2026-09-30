"""DSP Execution Telemetry and Audit Hooks (MC-DSP-05)."""
from __future__ import annotations

import time
from typing import Any, Callable
import psutil


class PipelineTelemetry:
    """Records duration, memory, and status metrics for pipeline runs with execution hooks."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.start_time = time.time()
        self.duration_sec = 0.0
        self.start_rss_bytes = psutil.Process().memory_info().rss
        self.peak_rss_bytes = self.start_rss_bytes
        self.end_rss_bytes = self.start_rss_bytes
        self.status = "PENDING"
        self._hooks: list[Callable[[dict[str, Any]], None]] = []

    def record_memory(self) -> float:
        """Samples current process RSS memory in megabytes."""
        rss = psutil.Process().memory_info().rss
        if rss > self.peak_rss_bytes:
            self.peak_rss_bytes = rss
        return rss / (1024 * 1024)

    def register_hook(self, hook: Callable[[dict[str, Any]], None]) -> None:
        """Registers a callback hook invoked upon pipeline completion."""
        self._hooks.append(hook)

    def finish(self, status: str = "COMPLETED") -> dict[str, Any]:
        """Finalizes telemetry recording, updates duration and memory, and triggers hooks."""
        self.status = status
        self.duration_sec = time.time() - self.start_time
        self.end_rss_bytes = psutil.Process().memory_info().rss
        if self.end_rss_bytes > self.peak_rss_bytes:
            self.peak_rss_bytes = self.end_rss_bytes

        metrics: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "duration_sec": self.duration_sec,
            "memory_start_mb": self.start_rss_bytes / (1024 * 1024),
            "memory_end_mb": self.end_rss_bytes / (1024 * 1024),
            "memory_peak_mb": self.peak_rss_bytes / (1024 * 1024),
        }

        for hook in self._hooks:
            hook(metrics)

        return metrics

    def __enter__(self) -> PipelineTelemetry:
        self.start_time = time.time()
        self.record_memory()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        final_status = "FAILED" if exc_type is not None else "COMPLETED"
        self.finish(status=final_status)
