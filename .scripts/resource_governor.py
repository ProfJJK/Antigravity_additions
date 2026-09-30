"""
Resource governor for the task_work_loop daemon (Windows)
=========================================================
1. memory_worker_cap(): admission control on commit charge (CommitTotal vs CommitLimit).
   psutil "available" ignores commit; when RAM+pagefile are fully committed, allocations
   fail system-wide even though "available" can look healthy.
2. AdaptiveLimiter: resizable asyncio gate. Replaces swapping in a fresh asyncio.Semaphore,
   which let tasks holding the old semaphore plus a full new allotment run at once.
3. install_job_memory_cap(): Windows Job Object with JOB_OBJECT_LIMIT_JOB_MEMORY. Children
   inherit the job, so the whole tree has a hard commit ceiling; at the ceiling the
   allocating child gets an ordinary OOM (failed task). KILL_ON_JOB_CLOSE stops orphans.

Env: COCHEM_TASK_COMMIT_BUDGET_GB (1.5), COCHEM_COMMIT_RESERVE_GB (max(6, 15% of limit)),
     COCHEM_MIN_PHYS_AVAIL_GB (3), COCHEM_JOB_MEMORY_LIMIT_GB (60% of RAM; 0 disables),
     COCHEM_JOB_MAX_PROCESSES (0 = off), COCHEM_MAX_CONCURRENT_PYTEST (4)
"""
from __future__ import annotations

import asyncio
import collections
import ctypes
import logging
import os
from ctypes import wintypes as wt
from typing import Optional

logger = logging.getLogger(__name__)
_GB = 1024 ** 3


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning(f"[GOV] ignoring non-numeric {name}={raw!r}")
        return default


class _PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
                ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                ("HandleCount", wt.DWORD), ("ProcessCount", wt.DWORD), ("ThreadCount", wt.DWORD)]


def memory_snapshot() -> Optional[dict]:
    if os.name != "nt":
        return None
    info = _PERFORMANCE_INFORMATION()
    info.cb = ctypes.sizeof(info)
    try:
        if not ctypes.WinDLL("psapi").GetPerformanceInfo(ctypes.byref(info), info.cb):
            return None
    except (OSError, AttributeError):
        return None
    pg = info.PageSize
    total, limit = info.CommitTotal * pg / _GB, info.CommitLimit * pg / _GB
    return {"commit_total_gb": total, "commit_limit_gb": limit, "commit_free_gb": limit - total,
            "phys_total_gb": info.PhysicalTotal * pg / _GB,
            "phys_avail_gb": info.PhysicalAvailable * pg / _GB,
            "kernel_nonpaged_gb": info.KernelNonpaged * pg / _GB}


def memory_worker_cap(in_flight: int, floor: int = 1) -> Optional[int]:
    """Total concurrent tasks current headroom affords. Running tasks are already in
    CommitTotal, so cap = in_flight + tasks that fit in what is left. A cap below
    in_flight kills nothing; the limiter just admits no new task until some finish."""
    snap = memory_snapshot()
    if snap is None:
        return None
    budget = max(0.25, _env_float("COCHEM_TASK_COMMIT_BUDGET_GB", 1.5))
    reserve = _env_float("COCHEM_COMMIT_RESERVE_GB", max(6.0, 0.15 * snap["commit_limit_gb"]))
    min_phys = _env_float("COCHEM_MIN_PHYS_AVAIL_GB", 3.0)
    if snap["phys_avail_gb"] < min_phys or snap["commit_free_gb"] < reserve:
        cap = floor
    else:
        fit = min((snap["commit_free_gb"] - reserve) // budget, (snap["phys_avail_gb"] - min_phys) // budget)
        cap = in_flight + max(0, int(fit))
    cap = max(floor, min(cap, int(snap["phys_total_gb"] // budget)))
    logger.debug(f"[GOV] commit {snap['commit_total_gb']:.1f}/{snap['commit_limit_gb']:.1f}GB "
                 f"phys_avail={snap['phys_avail_gb']:.1f}GB nonpaged={snap['kernel_nonpaged_gb']:.2f}GB "
                 f"in_flight={in_flight} -> cap {cap}")
    return cap


class AdaptiveLimiter:
    """Resizable concurrency gate; lowering the limit takes effect immediately.
    Event-loop thread only."""

    def __init__(self, limit: int):
        self._limit = max(1, int(limit))
        self._in_flight = 0
        self._waiters: collections.deque[asyncio.Future] = collections.deque()

    limit = property(lambda self: self._limit)
    in_flight = property(lambda self: self._in_flight)
    _value = property(lambda self: max(0, self._limit - self._in_flight))  # Semaphore-compatible

    def set_limit(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._wake()

    def _wake(self) -> None:
        while self._waiters and self._in_flight < self._limit:
            fut = self._waiters.popleft()
            if not fut.done():
                self._in_flight += 1          # slot handed to the waiter
                fut.set_result(None)

    async def acquire(self) -> None:
        if self._in_flight < self._limit and not self._waiters:
            self._in_flight += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()                # handed a slot just as we were cancelled
            elif fut in self._waiters:
                self._waiters.remove(fut)
            raise

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self._wake()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *exc):
        self.release()


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                  "ReadBytes", "WriteBytes", "OtherBytes")]


class _BASIC_LIMITS(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class _EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [("Basic", _BASIC_LIMITS), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_job_handle = None  # must stay open for the daemon's lifetime


def install_job_memory_cap() -> Optional[float]:
    """Put this process and all future children in a memory-capped Job Object. Never raises."""
    global _job_handle
    if os.name != "nt" or _job_handle is not None:
        return None
    snap = memory_snapshot()
    limit_gb = _env_float("COCHEM_JOB_MEMORY_LIMIT_GB", 0.6 * snap["phys_total_gb"] if snap else 0.0)
    if limit_gb <= 0:
        return None
    max_procs = int(_env_float("COCHEM_JOB_MAX_PROCESSES", 0))
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wt.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        logger.warning(f"[GOV] CreateJobObject failed (err={ctypes.get_last_error()})")
        return None
    info = _EXTENDED_LIMITS()
    info.Basic.LimitFlags = 0x200 | 0x2000          # JOB_MEMORY | KILL_ON_JOB_CLOSE
    if max_procs > 0:
        info.Basic.LimitFlags |= 0x8                # ACTIVE_PROCESS
        info.Basic.ActiveProcessLimit = max_procs
    info.JobMemoryLimit = int(limit_gb * _GB)
    if not (k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            and k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())):
        logger.warning(f"[GOV] Job Object setup failed (err={ctypes.get_last_error()}); running without it")
        k32.CloseHandle(job)
        return None
    _job_handle = job
    logger.info(f"[GOV] Job Object active: process-tree commit capped at {limit_gb:.1f} GB")
    return limit_gb


_pytest_gate: Optional[asyncio.Semaphore] = None


def pytest_gate() -> asyncio.Semaphore:
    global _pytest_gate
    if _pytest_gate is None:
        _pytest_gate = asyncio.Semaphore(max(1, int(_env_float("COCHEM_MAX_CONCURRENT_PYTEST", 4))))
    return _pytest_gate