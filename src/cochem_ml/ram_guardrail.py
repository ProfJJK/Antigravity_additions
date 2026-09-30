"""Dynamic RAM polling guardrail enforcing the strict < 2.0 GB heap ceiling.

The guardrail reads real process memory through ``psutil``. By default it sums
the resident set size (RSS) and virtual memory size (VMS) of the current
process and all of its descendants. It compares the RSS against a configured
ceiling (``RAM_CEILING_BYTES`` = 2 GiB by default) and applies this policy:

* ``utilization_ratio >= warning_ratio``: run registered flush/eviction
  callbacks first, then ``gc.collect()`` when ``auto_gc`` is enabled, then
  measure again.
* RSS still ``>= ceiling_bytes`` after remediation: raise
  :class:`RAMCeilingExceededError` carrying the diagnostic :class:`RAMSnapshot`.

It can be used in three ways:

* Synchronously, via :meth:`RAMGuardrail.poll` and :meth:`RAMGuardrail.check`.
* Asynchronously, via a daemon polling thread (:meth:`RAMGuardrail.start` and
  :meth:`RAMGuardrail.stop`). A breach found by the thread is recorded under a
  lock and re-raised in the caller on the next :meth:`check` or when the
  context manager exits.
* Scoped, via ``with RAMGuardrail(...)`` or ``@guardrail.protect``. The daemon
  is always torn down when the scope ends.
"""
from __future__ import annotations

import functools
import gc
import inspect
import os
import threading
import typing
from dataclasses import dataclass
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

import psutil

__all__ = [
    "RAM_CEILING_BYTES",
    "DEFAULT_WARNING_RATIO",
    "DEFAULT_POLL_INTERVAL_S",
    "RAMSnapshot",
    "RAMGuardrailConfig",
    "RAMCeilingExceededError",
    "RAMGuardrail",
]

# --------------------------------------------------------------------------- #
# Statutory constants
# --------------------------------------------------------------------------- #
RAM_CEILING_BYTES: int = 2 * 1024 * 1024 * 1024  # 2,147,483,648 bytes (2.0 GiB)
DEFAULT_WARNING_RATIO: float = 0.8
DEFAULT_POLL_INTERVAL_S: float = 0.1

_BYTES_PER_GIB: int = 1024 ** 3
_DEFAULT_STOP_JOIN_TIMEOUT_S: float = 5.0


def _utc_now_iso() -> str:
    """UTC ISO 8601 timestamp with an explicit ``+00:00`` offset."""
    return datetime.now(timezone.utc).isoformat()


def _closure_namespace(func: Callable[..., Any]) -> Dict[str, Any]:
    """Map a function's free-variable names to their current cell contents."""
    namespace: Dict[str, Any] = {}
    code = getattr(func, "__code__", None)
    closure = getattr(func, "__closure__", None)
    if code is None or not closure:
        return namespace
    for name, cell in zip(code.co_freevars, closure):
        try:
            namespace[name] = cell.cell_contents
        except ValueError:  # empty cell (variable not yet bound)
            continue
    return namespace


def _resolved_annotations(func: Callable[..., Any]) -> Dict[str, Any]:
    """Return ``func``'s annotations with stringified entries evaluated.

    Callables defined under ``from __future__ import annotations`` carry string
    annotations. Those are resolved to real objects here so a wrapper exposes the
    same annotation values a directly defined function would. Resolution is
    attempted for the whole mapping first and then entry by entry (using the
    function's globals and closure), so one unresolvable forward reference does
    not leave the other entries stringified. Entries that cannot be resolved keep
    their original value.
    """
    raw: Dict[str, Any] = dict(getattr(func, "__annotations__", None) or {})
    if not any(isinstance(value, str) for value in raw.values()):
        return raw

    if hasattr(inspect, "get_annotations"):
        try:
            return dict(inspect.get_annotations(func, eval_str=True))
        except Exception:
            pass
    else:
        try:
            return dict(typing.get_type_hints(func, include_extras=True))
        except Exception:
            pass

    func_globals: Dict[str, Any] = getattr(func, "__globals__", {})
    local_ns = _closure_namespace(func)
    resolved: Dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            try:
                resolved[key] = eval(value, func_globals, local_ns)  # noqa: S307 - annotation text
            except Exception:
                resolved[key] = value
        else:
            resolved[key] = value
    return resolved


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RAMSnapshot:
    """Immutable, instantaneous observation of physical memory usage."""

    rss_bytes: int
    vms_bytes: int
    rss_gb: float
    utilization_ratio: float
    is_warning: bool
    is_breached: bool
    timestamp_iso: str


@dataclass(frozen=True)
class RAMGuardrailConfig:
    """Immutable guardrail configuration."""

    ceiling_bytes: int = RAM_CEILING_BYTES
    warning_ratio: float = DEFAULT_WARNING_RATIO
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S
    auto_gc: bool = True
    include_children: bool = True
    callbacks: Tuple[Callable[[RAMSnapshot], None], ...] = ()

    def __post_init__(self) -> None:
        if self.ceiling_bytes <= 0:
            raise ValueError(f"ceiling_bytes must be positive: {self.ceiling_bytes}")
        if not (0.0 < self.warning_ratio <= 1.0):
            raise ValueError(f"warning_ratio must be in (0.0, 1.0]: {self.warning_ratio}")
        if self.poll_interval_s <= 0.0:
            raise ValueError(f"poll_interval_s must be positive: {self.poll_interval_s}")
        for cb in self.callbacks:
            if not callable(cb):
                raise TypeError(f"callback is not callable: {cb!r}")


class RAMCeilingExceededError(MemoryError):
    """Raised when physical RSS reaches or exceeds the ceiling and remediation fails."""

    def __init__(self, snapshot: RAMSnapshot, message: Optional[str] = None) -> None:
        self.snapshot: RAMSnapshot = snapshot
        if message is None:
            if snapshot.utilization_ratio > 0.0:
                ceiling_gb_text = (
                    f"{snapshot.rss_bytes / snapshot.utilization_ratio / _BYTES_PER_GIB:.3f}"
                )
            else:
                ceiling_gb_text = "unknown"
            message = (
                f"[RAM_CEILING_BREACH] Resident physical memory {snapshot.rss_gb:.3f} GB "
                f"({snapshot.rss_bytes} bytes) reached/exceeded statutory ceiling "
                f"{ceiling_gb_text} GB "
                f"({snapshot.utilization_ratio * 100:.1f}% utilization) at {snapshot.timestamp_iso}."
            )
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Guardrail
# --------------------------------------------------------------------------- #
class RAMGuardrail:
    """psutil-backed RAM guardrail with synchronous and background enforcement."""

    def __init__(self, config: Optional[RAMGuardrailConfig] = None) -> None:
        self._config: RAMGuardrailConfig = config if config is not None else RAMGuardrailConfig()
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[RAMSnapshot], None]] = list(self._config.callbacks)
        self._last_snapshot: Optional[RAMSnapshot] = None
        self._recorded_error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._scope_depth: int = 0

    # ------------------------------------------------------------------ props
    @property
    def config(self) -> RAMGuardrailConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        with self._lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_snapshot(self) -> Optional[RAMSnapshot]:
        with self._lock:
            return self._last_snapshot

    # -------------------------------------------------------------- callbacks
    def register_callback(self, callback: Callable[[RAMSnapshot], None]) -> None:
        """Register a flush/eviction callback invoked at or above the warning threshold."""
        if not callable(callback):
            raise TypeError(f"callback is not callable: {callback!r}")
        with self._lock:
            self._callbacks.append(callback)

    # ------------------------------------------------------------ measurement
    def _measure(self) -> Tuple[int, int]:
        """Return (rss, vms) of this process and, optionally, its descendants."""
        proc = psutil.Process(os.getpid())
        info = proc.memory_info()
        rss = int(info.rss)
        vms = int(info.vms)
        if self._config.include_children:
            try:
                children = proc.children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                children = []
            for child in children:
                try:
                    child_info = child.memory_info()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                rss += int(child_info.rss)
                vms += int(child_info.vms)
        return rss, vms

    def _snapshot(self) -> RAMSnapshot:
        rss, vms = self._measure()
        ratio = rss / self._config.ceiling_bytes
        snap = RAMSnapshot(
            rss_bytes=rss,
            vms_bytes=vms,
            rss_gb=rss / _BYTES_PER_GIB,
            utilization_ratio=ratio,
            is_warning=bool(ratio >= self._config.warning_ratio),
            is_breached=bool(ratio >= 1.0),
            timestamp_iso=_utc_now_iso(),
        )
        with self._lock:
            self._last_snapshot = snap
        return snap

    def _remediate(self, snapshot: RAMSnapshot) -> None:
        """Run callbacks first, then gc.collect() when auto_gc is enabled."""
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            callback(snapshot)
        if self._config.auto_gc:
            gc.collect()

    # ---------------------------------------------------------------- polling
    def poll(self) -> RAMSnapshot:
        """Measure real memory, remediate at the warning threshold, and enforce the ceiling.

        Raises:
            RAMCeilingExceededError: RSS is still at or above the ceiling after
                remediation.
        """
        snap = self._snapshot()
        if snap.is_warning or snap.is_breached:
            self._remediate(snap)
            snap = self._snapshot()
        if snap.is_breached:
            raise RAMCeilingExceededError(snap)
        return snap

    def check(self) -> RAMSnapshot:
        """Re-raise any error recorded by the background daemon, then poll."""
        with self._lock:
            recorded = self._recorded_error
            self._recorded_error = None
        if recorded is not None:
            raise recorded
        return self.poll()

    # ------------------------------------------------------ background daemon
    def _poll_loop(self, stop_event: threading.Event) -> None:
        interval = self._config.poll_interval_s
        while not stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:  # recorded and re-raised in the caller thread
                with self._lock:
                    self._recorded_error = exc
                return
            if stop_event.wait(interval):
                return

    def start(self) -> None:
        """Start the background daemon polling thread (no-op if already running)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._poll_loop,
                args=(stop_event,),
                name="cochem-ram-guardrail",
                daemon=True,
            )
            self._stop_event = stop_event
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = _DEFAULT_STOP_JOIN_TIMEOUT_S) -> None:
        """Signal the daemon to stop and join it. Idempotent."""
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        if thread is None:
            return
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
                self._stop_event = None

    # -------------------------------------------------------- scoped usage
    def __enter__(self) -> "RAMGuardrail":
        with self._lock:
            self._scope_depth += 1
            outermost = self._scope_depth == 1
        if outermost:
            try:
                self.start()
            except BaseException:
                with self._lock:
                    self._scope_depth -= 1
                raise
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        with self._lock:
            self._scope_depth = max(self._scope_depth - 1, 0)
            outermost = self._scope_depth == 0
        if outermost:
            self.stop()
        if exc_type is None:
            # Final boundary check. It also surfaces any breach recorded by the daemon.
            self.check()
        return False

    def protect(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator that runs ``func`` inside this guardrail's monitored scope.

        The wrapper exposes resolved (non-stringified) annotations even when
        ``func`` was defined under ``from __future__ import annotations``.
        """

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self:
                return func(*args, **kwargs)

        wrapper.__annotations__ = _resolved_annotations(func)
        return wrapper
