from __future__ import annotations

import asyncio
import os
import msvcrt
import json
import logging
import time
import hashlib
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager, contextmanager

from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator
import psutil

if TYPE_CHECKING:
    from psutil._common import sdiskio, sdiskusage

logger = logging.getLogger(__name__)


class LedgerError(Exception):
    """Raised when an audit ledger operation encounters an error."""


class DiskIOSaturatedError(RuntimeError):
    """Raised when an execution slot is requested while the storage subsystem is saturated."""

    def __init__(self, message: str, metrics: dict[str, Any]) -> None:
        super().__init__(message)
        self.metrics = metrics


# Module-level cumulative-counter snapshot: (monotonic_time, psutil sdiskio | None).
# Guarded by a lock because the hardware monitor and slot-acquisition paths may run
# in different threads. The lock is only held for the brief read/write of the snapshot
# tuple; it is NEVER held across a sleep or a psutil call.
_LAST_IO_SNAPSHOT: tuple[float, sdiskio | None] | None = None
_SNAPSHOT_LOCK = threading.Lock()

# Minimum elapsed window for a meaningful rate from cumulative counters.
_MIN_RATE_WINDOW_S = 0.005
# When a snapshot is older than _MAX_STALE_WINDOW_S it is not used for rates (a stale
# snapshot would yield a long-run average that hides short I/O bursts). The non-blocking
# path simply re-baselines; check_disk_io_headroom takes an explicit fresh sample of
# _FRESH_SAMPLE_WINDOW_S when it needs a trustworthy rate.
_FRESH_SAMPLE_WINDOW_S = 0.02
_MAX_STALE_WINDOW_S = 5.0
_BYTES_PER_MB = 1024.0 * 1024.0
_BYTES_PER_GB = 1024.0 ** 3


def _in_running_event_loop() -> bool:
    """True when the calling thread is currently running an asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _resolve_usage(target_path: str) -> sdiskusage | None:
    """Return psutil.disk_usage (an ``sdiskusage``) for target_path, or None.

    On Windows, disk_usage raises on empty strings or non-existent paths, so the path is
    normalised with abspath and, on failure, resolved to its volume root (e.g. 'D:\\').
    Returns None only if neither the path nor its anchor can be queried.
    """
    abs_path = os.path.abspath(target_path or ".")
    candidates = [abs_path]
    anchor = Path(abs_path).anchor
    if not anchor:
        drive = os.path.splitdrive(abs_path)[0]
        anchor = drive + os.sep if drive else ""
    if anchor and anchor not in candidates:
        candidates.append(anchor)
    for candidate in candidates:
        try:
            return psutil.disk_usage(candidate)
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return None


def _read_io_counters() -> sdiskio | None:
    """Read system-wide cumulative disk I/O counters (``sdiskio``); None if unavailable."""
    try:
        return psutil.disk_io_counters()
    except (RuntimeError, OSError):
        return None


def get_disk_io_metrics(
    target_path: str = ".",
    sample_duration: float = 0.0
) -> dict[str, Any]:
    """
    Measures disk I/O throughput, IOPS, and volume capacity.

    Throughput/IOPS are computed as deltas of psutil.disk_io_counters() (system-wide
    cumulative counters since boot).

    * sample_duration > 0: the function blocks (time.sleep) for that window and reports
      the rate over it. Only call this from a worker thread when inside an event loop.
    * sample_duration == 0 (default): NEVER sleeps. The rate is computed against the
      snapshot left by the previous call. If there is no usable snapshot, it is older than
      _MAX_STALE_WINDOW_S, or fewer than _MIN_RATE_WINDOW_S have elapsed, the rates are
      reported as 0.0 with "rates_valid" False (the stale/absent baseline is refreshed so
      the next call has a real window). Callers that need a trustworthy rate must check
      "rates_valid" (check_disk_io_headroom does so).

    Returns structured dict:
    {
        "read_mb_s": float,
        "write_mb_s": float,
        "total_mb_s": float,
        "read_iops": float,
        "write_iops": float,
        "total_iops": float,
        "free_gb": float,
        "percent_used": float,
        "timestamp": float,
        "io_counters_available": bool,
        "usage_available": bool,
        "rates_valid": bool,
    }

    If the volume cannot be queried, free_gb is reported as 0.0 so any headroom check
    fails closed (the scheduler collapses to its floor rather than assuming free space).
    """
    global _LAST_IO_SNAPSHOT

    usage = _resolve_usage(target_path)
    if usage is not None:
        free_gb = float(usage.free) / _BYTES_PER_GB
        percent_used = float(usage.percent)
    else:
        free_gb = 0.0
        percent_used = 100.0

    c1: sdiskio | None
    c2: sdiskio | None
    update_snapshot = True
    if sample_duration and sample_duration > 0.0:
        # Explicitly requested blocking sample. Sleep happens outside _SNAPSHOT_LOCK.
        window = float(sample_duration)
        t1 = time.monotonic()
        c1 = _read_io_counters()
        time.sleep(window)
        c2 = _read_io_counters()
        t2 = time.monotonic()
    else:
        # Non-blocking path: never sleeps.
        with _SNAPSHOT_LOCK:
            prev = _LAST_IO_SNAPSHOT
        c2 = _read_io_counters()
        t2 = time.monotonic()
        c1 = None
        t1 = t2
        if prev is not None and prev[1] is not None:
            elapsed = t2 - prev[0]
            if elapsed > _MAX_STALE_WINDOW_S:
                pass  # stale baseline: re-baseline below, report no rate
            elif elapsed < _MIN_RATE_WINDOW_S:
                # Too close to the baseline for a meaningful rate: keep the older
                # baseline so a later call measures a real interval.
                update_snapshot = False
            else:
                t1, c1 = prev

    if update_snapshot:
        with _SNAPSHOT_LOCK:
            # Never move the shared snapshot backwards in time (concurrent callers).
            if _LAST_IO_SNAPSHOT is None or _LAST_IO_SNAPSHOT[0] < t2:
                _LAST_IO_SNAPSHOT = (t2, c2)

    dt = t2 - t1
    io_available = c2 is not None
    rates_valid = c1 is not None and c2 is not None and dt > 0.001
    if rates_valid and c1 is not None and c2 is not None:
        # Counters can go backwards after drive re-enumeration / bus reset: clamp at zero.
        d_read_bytes = max(0, c2.read_bytes - c1.read_bytes)
        d_write_bytes = max(0, c2.write_bytes - c1.write_bytes)
        d_read_count = max(0, c2.read_count - c1.read_count)
        d_write_count = max(0, c2.write_count - c1.write_count)

        read_mb_s = (d_read_bytes / _BYTES_PER_MB) / dt
        write_mb_s = (d_write_bytes / _BYTES_PER_MB) / dt
        read_iops = d_read_count / dt
        write_iops = d_write_count / dt
    else:
        read_mb_s = write_mb_s = read_iops = write_iops = 0.0

    return {
        "read_mb_s": float(read_mb_s),
        "write_mb_s": float(write_mb_s),
        "total_mb_s": float(read_mb_s + write_mb_s),
        "read_iops": float(read_iops),
        "write_iops": float(write_iops),
        "total_iops": float(read_iops + write_iops),
        "free_gb": float(free_gb),
        "percent_used": float(percent_used),
        "timestamp": time.time(),
        "io_counters_available": bool(io_available),
        "usage_available": usage is not None,
        "rates_valid": bool(rates_valid),
    }


def check_disk_io_headroom(
    target_path: str = ".",
    max_mb_s: float = 120.0,
    max_iops: float = 4000.0,
    min_free_gb: float = 5.0
) -> tuple[bool, dict[str, Any]]:
    """
    Validates storage subsystem health against saturation thresholds.

    Uses the non-blocking metrics read first; if the platform exposes I/O counters but no
    valid rate is available yet (first call, stale or too-recent baseline), a single short
    explicit sample (_FRESH_SAMPLE_WINDOW_S) is taken so the check never passes on rates
    that were never measured.

    Event-loop safety: this synchronous function NEVER blocks an event loop. When invoked
    on a thread that is running an asyncio event loop, the blocking sample is NOT taken.
    The baseline has already been refreshed by the non-blocking read, so the next call
    gets a real window; the returned metrics carry ``"rate_sample_deferred": True`` and
    ``"rates_valid": False`` so callers know rates were not measured this time. Capacity
    (free space) is still enforced. Async callers that want a measured rate every time
    must use async_check_disk_io_headroom / async_guarded_execution_slot, which run this
    in a worker thread.

    Returns:
        (is_healthy: bool, metrics: dict[str, Any])
        Where is_healthy is False if total_mb_s > max_mb_s,
        total_iops > max_iops, or free_gb < min_free_gb.
        metrics additionally carries "saturation_reasons": list[str] and
        "rate_sample_deferred": bool.
    """
    metrics: dict[str, Any] = get_disk_io_metrics(target_path=target_path, sample_duration=0.0)
    deferred = False
    if metrics.get("io_counters_available") and not metrics.get("rates_valid"):
        if _in_running_event_loop():
            deferred = True
            logger.debug(
                "check_disk_io_headroom called on an event-loop thread; "
                "skipping blocking rate sample (use async_check_disk_io_headroom)."
            )
        else:
            metrics = get_disk_io_metrics(
                target_path=target_path, sample_duration=_FRESH_SAMPLE_WINDOW_S
            )
    reasons: list[str] = []
    if metrics["total_mb_s"] > max_mb_s:
        reasons.append(f"throughput {metrics['total_mb_s']:.2f} MB/s > {max_mb_s} MB/s")
    if metrics["total_iops"] > max_iops:
        reasons.append(f"IOPS {metrics['total_iops']:.1f} > {max_iops}")
    if metrics["free_gb"] < min_free_gb:
        reasons.append(f"free space {metrics['free_gb']:.2f} GB < {min_free_gb} GB")
    metrics["saturation_reasons"] = reasons
    metrics["rate_sample_deferred"] = deferred
    return (not reasons), metrics


async def async_check_disk_io_headroom(
    target_path: str = ".",
    max_mb_s: float = 120.0,
    max_iops: float = 4000.0,
    min_free_gb: float = 5.0
) -> tuple[bool, dict[str, Any]]:
    """Event-loop-safe variant of check_disk_io_headroom.

    The (briefly sleeping) sampling runs in a worker thread via ``asyncio.to_thread`` so
    the calling event loop is never blocked. Returns the same (is_healthy, metrics) tuple.
    """
    return await asyncio.to_thread(
        check_disk_io_headroom,
        target_path=target_path,
        max_mb_s=max_mb_s,
        max_iops=max_iops,
        min_free_gb=min_free_gb,
    )


def require_disk_io_headroom(
    target_path: str = ".",
    max_mb_s: float = 120.0,
    max_iops: float = 4000.0,
    min_free_gb: float = 5.0
) -> dict[str, Any]:
    """Gatekeeper for execution-slot acquisition.

    Returns the metrics when storage is healthy; raises DiskIOSaturatedError otherwise so
    callers reject the slot instead of piling more I/O onto a saturated controller.
    """
    healthy, metrics = check_disk_io_headroom(
        target_path=target_path, max_mb_s=max_mb_s, max_iops=max_iops, min_free_gb=min_free_gb
    )
    if not healthy:
        raise DiskIOSaturatedError(
            "Disk I/O saturated: " + "; ".join(metrics.get("saturation_reasons", [])), metrics
        )
    return metrics


@contextmanager
def guarded_execution_slot(
    target_path: str = ".",
    max_mb_s: float = 120.0,
    max_iops: float = 4000.0,
    min_free_gb: float = 5.0
) -> Iterator[dict[str, Any]]:
    """Execution-slot acquisition path with disk I/O enforcement built in.

    Enter the context to acquire a slot: require_disk_io_headroom() is evaluated first and
    DiskIOSaturatedError is raised (slot rejected, body never runs) when the storage
    subsystem is saturated. On success the healthy-state metrics are yielded. Wrap any
    process/agent launch in this context so the AC5 rejection is enforced at the point of
    acquisition rather than being an optional API.
    """
    metrics = require_disk_io_headroom(
        target_path=target_path, max_mb_s=max_mb_s, max_iops=max_iops, min_free_gb=min_free_gb
    )
    yield metrics


@asynccontextmanager
async def async_guarded_execution_slot(
    target_path: str = ".",
    max_mb_s: float = 120.0,
    max_iops: float = 4000.0,
    min_free_gb: float = 5.0
) -> AsyncIterator[dict[str, Any]]:
    """Async variant of guarded_execution_slot for asyncio callers.

    The headroom check (which may sleep a few milliseconds while sampling the disk
    counters) runs in a worker thread via ``asyncio.to_thread`` so the event loop is never
    blocked. Semantics are identical: DiskIOSaturatedError is raised on entry, and the body
    never runs, when the storage subsystem is saturated.
    """
    metrics = await asyncio.to_thread(
        require_disk_io_headroom,
        target_path=target_path,
        max_mb_s=max_mb_s,
        max_iops=max_iops,
        min_free_gb=min_free_gb,
    )
    yield metrics


@contextmanager
def acquire_file_lock(filepath: str, timeout: float = 10.0) -> Iterator[int]:
    """
    Acquires an exclusive OS-level lock on a file using msvcrt (Windows).
    Creates the file if it does not exist. The lock is always released and the
    descriptor closed, even if the body of the with-block raises.
    (Covered by tests/tdd/test_concurrency_guard_slot_and_lock.py.)
    """
    start_time = time.time()
    fd = None

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

    while True:
        try:
            fd = os.open(filepath, os.O_RDWR | os.O_CREAT)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if fd is not None:
                os.close(fd)
                fd = None
            if time.time() - start_time >= timeout:
                raise TimeoutError(
                    f"Could not acquire lock on {filepath} within {timeout} seconds."
                )
            time.sleep(0.1)

    try:
        yield fd
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError as exc:
            # Unlock can only fail if the region is already unlocked; closing the
            # descriptor below releases any remaining lock regardless.
            logger.warning("unlock warning for %s: %s", filepath, exc)
        os.close(fd)


def append_to_ledger(ledger_path: str, action: str, data: dict[str, Any]) -> None:
    """
    Safely appends a cryptographic transition state to the audit ledger.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    payload_str = json.dumps(data, sort_keys=True)
    content_hash = hashlib.sha256(f"{timestamp}{action}{payload_str}".encode('utf-8')).hexdigest()

    entry = {
        "timestamp": timestamp,
        "action": action,
        "hash": content_hash,
        "payload": data
    }

    with acquire_file_lock(ledger_path) as fd:
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, (json.dumps(entry) + "\n").encode('utf-8'))


def safe_json_read(filepath: str) -> dict[str, Any]:
    if not os.path.exists(filepath):
        return {}
    with acquire_file_lock(filepath) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        size = os.path.getsize(filepath)
        if size == 0:
            return {}
        content = os.read(fd, size).decode('utf-8')
        if not content.strip():
            return {}
        return json.loads(content)


def safe_json_write(filepath: str, data: dict[str, Any]) -> None:
    with acquire_file_lock(filepath) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(data, indent=2).encode('utf-8'))
