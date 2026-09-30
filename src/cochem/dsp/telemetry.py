"""Domain execution telemetry and lifecycle hooks for Domain-Specific Pipelines.

Implements SRS-412-07 section 4 (FR-005) / WBS leaf node MC-DSP-05.

``PipelineTelemetry`` tracks, for a single pipeline run:

* wall-clock start timestamp (``start_time``, epoch seconds) and a
  high-resolution elapsed duration (``duration_sec``, measured with
  ``time.perf_counter`` so short runs still report a non-zero duration);
* physical memory consumption of the current process, sampled from the
  operating system via ``psutil`` as Resident Set Size (RSS). Raw values are
  kept in bytes internally and converted to megabytes only at output
  boundaries;
* the final lifecycle status (``PENDING`` -> ``COMPLETED`` / ``FAILED`` /
  caller-supplied);
* a FIFO list of completion hooks, each invoked exactly once with the
  finalized metrics dictionary.

It can be used explicitly (``finish()``) or as a context manager, in which
case a clean exit finalizes with ``COMPLETED`` and an exception finalizes with
``FAILED`` while still propagating the exception to the caller.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import psutil

_LOGGER = logging.getLogger(__name__)

_BYTES_PER_MB: int = 1024 * 1024

STATUS_PENDING: str = "PENDING"
STATUS_COMPLETED: str = "COMPLETED"
STATUS_FAILED: str = "FAILED"

MetricsHook = Callable[[dict[str, Any]], None]


def _bytes_to_mb(value: int) -> float:
    """Convert a byte count to megabytes (MiB) as a float."""
    return float(value) / float(_BYTES_PER_MB)


class PipelineTelemetry:
    """Execution telemetry for one domain pipeline run."""

    def __init__(self, task_id: str) -> None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")

        self.task_id: str = task_id
        self.start_time: float = time.time()
        self._start_perf: float = time.perf_counter()
        self.duration_sec: float = 0.0
        self.status: str = STATUS_PENDING

        # Cache the process handle once; memory_info() is then a cheap
        # per-sample OS query rather than a fresh PID lookup each time.
        self._process: psutil.Process = psutil.Process()
        self.start_rss_bytes: int = int(self._process.memory_info().rss)
        self.peak_rss_bytes: int = self.start_rss_bytes
        self.end_rss_bytes: int = self.start_rss_bytes

        self._hooks: list[MetricsHook] = []
        self._finished: bool = False
        self._metrics: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Memory sampling
    # ------------------------------------------------------------------
    def _sample_rss_bytes(self) -> int:
        current = int(self._process.memory_info().rss)
        if current > self.peak_rss_bytes:
            self.peak_rss_bytes = current
        return current

    def record_memory(self) -> float:
        """Sample current process RSS; update the monotonic peak.

        Returns the current RSS in megabytes.
        """
        return _bytes_to_mb(self._sample_rss_bytes())

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------
    def register_hook(self, hook: MetricsHook) -> None:
        """Register a callable invoked with the metrics dict on completion.

        Hooks are dispatched in registration (FIFO) order, exactly once.
        """
        if not callable(hook):
            raise TypeError(
                f"hook must be a callable, got {type(hook).__name__}"
            )
        self._hooks.append(hook)

    def _dispatch_hooks(self, metrics: dict[str, Any]) -> None:
        for hook in list(self._hooks):
            try:
                # Each hook receives its own copy so one listener mutating the
                # payload cannot corrupt what later listeners or the caller see.
                hook(dict(metrics))
            except Exception:  # noqa: BLE001 - hook isolation is deliberate
                _LOGGER.exception(
                    "Telemetry hook %r failed for task %s",
                    getattr(hook, "__qualname__", hook),
                    self.task_id,
                )

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------
    def _build_metrics(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "duration_sec": float(self.duration_sec),
            "memory_start_mb": _bytes_to_mb(self.start_rss_bytes),
            "memory_end_mb": _bytes_to_mb(self.end_rss_bytes),
            "memory_peak_mb": _bytes_to_mb(self.peak_rss_bytes),
        }

    @property
    def finished(self) -> bool:
        """True once finish() has finalized this run."""
        return self._finished

    def finish(self, status: str = STATUS_COMPLETED) -> dict[str, Any]:
        """Finalize duration, memory and status; fire hooks once.

        Idempotent: subsequent calls return the originally finalized metrics
        without re-running hooks or altering duration/status.
        """
        if self._finished and self._metrics is not None:
            return dict(self._metrics)

        self.duration_sec = time.perf_counter() - self._start_perf
        self.end_rss_bytes = self._sample_rss_bytes()
        self.peak_rss_bytes = max(self.peak_rss_bytes, self.end_rss_bytes)
        self.status = str(status)

        metrics = self._build_metrics()
        self._metrics = metrics
        self._finished = True

        self._dispatch_hooks(metrics)
        return dict(metrics)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> PipelineTelemetry:
        self.start_time = time.time()
        self._start_perf = time.perf_counter()
        self.record_memory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        final_status = STATUS_FAILED if exc_type is not None else STATUS_COMPLETED
        self.finish(status=final_status)
        # Returning None never suppresses an exception raised in the block.

    def __repr__(self) -> str:
        return (
            f"PipelineTelemetry(task_id={self.task_id!r}, status={self.status!r}, "
            f"duration_sec={self.duration_sec:.6f}, "
            f"peak_rss_mb={_bytes_to_mb(self.peak_rss_bytes):.2f})"
        )
