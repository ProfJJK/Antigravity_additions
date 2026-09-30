"""Resilient telemetry event collection daemon for CoChem (Task 1.03).

The module provides:

* :class:`DaemonConfig` - validated daemon configuration.
* :class:`DaemonState` - supervisor lifecycle states.
* :class:`DaemonTelemetryMetrics` - real-time daemon metrics, including
  dynamically resolved Mendeleev atomic masses.
* :class:`TelemetryDaemonWorker` - a non-blocking background ingestion thread
  that normalises :class:`TelemetryRecord`, :class:`FileSystemEventRecord` and
  structured payload dictionaries into a JSONL sink file.
* :class:`TelemetryDaemonSupervisor` - a watchdog that restarts crashed workers
  with bounded exponential backoff, migrates in-flight state between workers,
  enforces a restart ceiling and handles OS termination signals gracefully.
* :func:`run_telemetry_daemon`, :func:`build_arg_parser` and :func:`main` - the
  synchronous entrypoint and command line interface.

Engineering invariants
----------------------
PCA-66 / DEF-DATA-01 (transactional buffer flush)
    ``TelemetryDaemonWorker.flush`` writes a snapshot of the event buffer to
    disk, flushes and fsyncs it, and only then removes exactly the committed
    prefix from the buffer (``del buffer[:n]``). On ``OSError`` the buffer is
    left untouched.

PCA-67 / DEF-QUEUE-01 (in-flight queue migration)
    When a worker dies unexpectedly the supervisor extracts every uncommitted
    inbox item and buffered record through
    ``TelemetryDaemonWorker.extract_uncommitted_state`` and re-injects them into
    the replacement worker before it starts.

PCA-68 / DEF-BYPASS-01 (cross-platform signal parity)
    SIGINT and SIGTERM are trapped everywhere, SIGBREAK on Windows, and Win32
    console close / logoff / shutdown events are trapped through
    ``SetConsoleCtrlHandler``. Signal handlers never block: they hand the
    shutdown to a dedicated thread so no lock held by the interrupted thread
    can deadlock the shutdown sequence.
"""
from __future__ import annotations

import argparse
import dataclasses
import enum
import functools
import inspect
import itertools
import json
import logging
import math
import numbers
import os
import queue
import signal
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# --------------------------------------------------------------------------- #
# Import path bootstrap: ``cochem_ml`` lives in the inbox_code dropzone.       #
# Appended (not prepended) so an explicitly configured PYTHONPATH wins.        #
# --------------------------------------------------------------------------- #
_MODULE_DIR = Path(__file__).resolve().parent
_INBOX_CODE_DIR = _MODULE_DIR / "dropzones" / "inbox_code"
if _INBOX_CODE_DIR.is_dir() and str(_INBOX_CODE_DIR) not in sys.path:
    sys.path.append(str(_INBOX_CODE_DIR))

from mendeleev import element  # noqa: E402

from cochem_ml.filesystem_listener import (  # noqa: E402
    FileSystemEventListener,
    FileSystemEventRecord,
    verify_mendeleev_integrity,
)
from cochem_ml.telemetry_record import (  # noqa: E402
    TelemetryRecord,
    get_current_iso_timestamp,
)

__all__ = [
    "DaemonConfig",
    "DaemonState",
    "DaemonTelemetryMetrics",
    "TelemetryDaemonSupervisor",
    "TelemetryDaemonWorker",
    "WorkerClosedError",
    "build_arg_parser",
    "main",
    "resolve_atomic_masses",
    "resolve_sink_path",
    "run_telemetry_daemon",
]

logger = logging.getLogger("cochem.telemetry_daemon")

DEFAULT_SINK_RELATIVE_PATH = Path("telemetry") / "cochem_telemetry_daemon.jsonl"

# Minimum time a worker thread join may take before being reported as stuck.
_THREAD_JOIN_FLOOR_SECONDS = 5.0
# Granularity of the main-thread wait loop; keeps signal delivery responsive
# on platforms where blocking lock acquisition is not interruptible.
_MAIN_WAIT_SLICE_SECONDS = 0.1

# Win32 console control event codes (wincon.h).
_WIN_CTRL_C_EVENT = 0
_WIN_CTRL_BREAK_EVENT = 1
_WIN_CTRL_CLOSE_EVENT = 2
_WIN_CTRL_LOGOFF_EVENT = 5
_WIN_CTRL_SHUTDOWN_EVENT = 6
_WIN_CONSOLE_EVENT_NAMES = {
    _WIN_CTRL_CLOSE_EVENT: "CTRL_CLOSE_EVENT",
    _WIN_CTRL_LOGOFF_EVENT: "CTRL_LOGOFF_EVENT",
    _WIN_CTRL_SHUTDOWN_EVENT: "CTRL_SHUTDOWN_EVENT",
}
# Windows grants roughly five seconds to console close handlers.
_WIN_CONSOLE_HANDLER_GRACE_SECONDS = 4.5


# --------------------------------------------------------------------------- #
# Chemistry: dynamic Mendeleev atomic mass resolution                          #
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def resolve_atomic_masses() -> Tuple[float, float]:
    """Return ``(carbon_mass, silicon_mass)`` queried from the Mendeleev database.

    Values are never hardcoded. The result is validated for physical
    consistency (finite, positive, silicon heavier than carbon since Z=14 > Z=6)
    and cross-checked with the canonical ``verify_mendeleev_integrity`` helper.
    """
    carbon = float(element("C").mass)
    silicon = float(element("Si").mass)
    if not (math.isfinite(carbon) and math.isfinite(silicon)):
        raise RuntimeError(f"Mendeleev returned non-finite atomic masses: C={carbon!r}, Si={silicon!r}")
    if carbon <= 0.0 or silicon <= carbon:
        raise RuntimeError(
            f"Mendeleev returned physically inconsistent atomic masses: C={carbon!r}, Si={silicon!r}"
        )
    if verify_mendeleev_integrity() is not True:
        raise RuntimeError("verify_mendeleev_integrity() rejected the Mendeleev element database")
    return carbon, silicon


# --------------------------------------------------------------------------- #
# Serialisation helpers                                                        #
# --------------------------------------------------------------------------- #
def _json_default(obj: Any) -> Any:
    """``json.dumps`` fallback for the non-native types that appear in telemetry."""
    if isinstance(obj, PurePath):
        return str(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).decode("utf-8", errors="replace")
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return _safe_repr(obj)


def _safe_repr(obj: Any) -> str:
    try:
        return repr(obj)
    except Exception as exc:  # repr() of arbitrary objects can raise anything
        return f"<unrepresentable {type(obj).__qualname__}: {type(exc).__name__}: {exc}>"


def _json_safe(obj: Any) -> Any:
    """Convert ``obj`` into plain JSON types (dict/list/str/int/float/bool/None)."""
    return json.loads(json.dumps(obj, default=_json_default, ensure_ascii=False))


def _object_to_dict(obj: Any) -> Dict[str, Any]:
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
        raise TypeError(f"{type(obj).__qualname__}.to_dict() returned {type(result).__qualname__}")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return dict(vars(obj))


def _append_jsonl(path: Path, records: Sequence[Mapping[str, Any]], create_dirs: bool) -> None:
    """Append ``records`` as JSON lines to ``path`` and force them to stable storage.

    Raises ``OSError`` if the sink cannot be opened or written.
    """
    if not records:
        return
    if create_dirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(record, default=_json_default, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    with open(path, "a", encoding="utf-8", newline="\n") as sink:
        sink.write(payload)
        sink.flush()
        os.fsync(sink.fileno())


def _signal_name(signum: Optional[int]) -> str:
    if signum is None:
        return "request_shutdown"
    try:
        return signal.Signals(signum).name
    except (ValueError, TypeError):
        return f"signal_{signum}"


# --------------------------------------------------------------------------- #
# Configuration and state                                                      #
# --------------------------------------------------------------------------- #
class DaemonState(str, enum.Enum):
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    RESTARTING = "RESTARTING"
    STOPPING = "STOPPING"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


def _coerce_path(value: Any, name: str) -> Path:
    if isinstance(value, Path):
        return value
    try:
        return Path(os.fspath(value))
    except TypeError as exc:
        raise TypeError(f"{name} must be a path-like value, got {type(value).__qualname__}") from exc


def _coerce_path_list(value: Any, name: str) -> List[Path]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, os.PathLike)):
        return [_coerce_path(value, name)]
    if not isinstance(value, Iterable):
        raise TypeError(f"{name} must be a path or an iterable of paths, got {type(value).__qualname__}")
    return [_coerce_path(item, name) for item in value]


def _coerce_real(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{name} must be a real number, got {type(value).__qualname__}")
    return float(value)


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an integer, got {type(value).__qualname__}")
    return int(value)


@dataclass
class DaemonConfig:
    dropzone_roots: List[Path] = field(default_factory=list)
    scratch_roots: List[Path] = field(default_factory=list)
    telemetry_sink_path: Optional[Path] = None
    poll_interval: float = 0.05
    max_restarts: int = 5
    restart_backoff_seconds: float = 0.2
    max_restart_backoff_seconds: float = 5.0
    health_check_interval: float = 0.5
    flush_batch_size: int = 100
    auto_create_dirs: bool = True
    enable_fs_listener: bool = True

    def __post_init__(self) -> None:
        self.dropzone_roots = _coerce_path_list(self.dropzone_roots, "dropzone_roots")
        self.scratch_roots = _coerce_path_list(self.scratch_roots, "scratch_roots")
        if self.telemetry_sink_path is not None:
            self.telemetry_sink_path = _coerce_path(self.telemetry_sink_path, "telemetry_sink_path")

        self.poll_interval = _coerce_real(self.poll_interval, "poll_interval")
        if not (math.isfinite(self.poll_interval) and self.poll_interval > 0):
            raise ValueError(f"poll_interval must be positive (got {self.poll_interval!r})")

        self.max_restarts = _coerce_int(self.max_restarts, "max_restarts")
        if self.max_restarts < 0:
            raise ValueError(f"max_restarts must be non-negative (got {self.max_restarts!r})")

        self.restart_backoff_seconds = _coerce_real(self.restart_backoff_seconds, "restart_backoff_seconds")
        if not (math.isfinite(self.restart_backoff_seconds) and self.restart_backoff_seconds >= 0):
            raise ValueError(
                f"restart_backoff_seconds must be non-negative (got {self.restart_backoff_seconds!r})"
            )

        self.max_restart_backoff_seconds = _coerce_real(
            self.max_restart_backoff_seconds, "max_restart_backoff_seconds"
        )
        if not (
            math.isfinite(self.max_restart_backoff_seconds)
            and self.max_restart_backoff_seconds >= self.restart_backoff_seconds
        ):
            raise ValueError(
                "max_restart_backoff_seconds must be finite and >= restart_backoff_seconds "
                f"(got {self.max_restart_backoff_seconds!r} < {self.restart_backoff_seconds!r})"
            )

        self.health_check_interval = _coerce_real(self.health_check_interval, "health_check_interval")
        if not (math.isfinite(self.health_check_interval) and self.health_check_interval > 0):
            raise ValueError(f"health_check_interval must be positive (got {self.health_check_interval!r})")

        self.flush_batch_size = _coerce_int(self.flush_batch_size, "flush_batch_size")
        if self.flush_batch_size <= 0:
            raise ValueError(f"flush_batch_size must be positive (got {self.flush_batch_size!r})")

        self.auto_create_dirs = bool(self.auto_create_dirs)
        self.enable_fs_listener = bool(self.enable_fs_listener)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(dataclasses.asdict(self))


def resolve_sink_path(config: DaemonConfig) -> Path:
    """Return the configured sink path, or the default path under the current directory."""
    if config.telemetry_sink_path is not None:
        return config.telemetry_sink_path
    return Path.cwd() / DEFAULT_SINK_RELATIVE_PATH


@dataclass
class DaemonTelemetryMetrics:
    pid: int = field(default_factory=os.getpid)
    uptime_seconds: float = 0.0
    total_events_collected: int = 0
    total_records_flushed: int = 0
    restart_count: int = 0
    crash_count: int = 0
    last_error: Optional[str] = None
    last_restart_timestamp: Optional[str] = None
    last_heartbeat_timestamp: Optional[str] = None
    carbon_atomic_mass: float = 0.0
    silicon_atomic_mass: float = 0.0
    daemon_state: str = DaemonState.INITIALIZING.value
    active_worker_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------- #
# Worker                                                                       #
# --------------------------------------------------------------------------- #
class WorkerClosedError(RuntimeError):
    """Raised when telemetry is submitted to a worker whose intake is closed."""


@dataclass(frozen=True)
class _LifecycleEvent:
    """Daemon lifecycle marker routed through a worker like any other item."""

    event: str
    details: Dict[str, Any] = field(default_factory=dict)


IngestItem = Union[TelemetryRecord, FileSystemEventRecord, Dict[str, Any], _LifecycleEvent]


def _construct_fs_listener(config: DaemonConfig) -> Any:
    """Instantiate ``FileSystemEventListener`` by binding its declared parameters.

    Parameters whose names reference dropzones, scratch roots or polling are
    bound to the matching configuration values. A required parameter that
    cannot be bound raises ``TypeError`` so the caller can report it.
    """
    parameters = inspect.signature(FileSystemEventListener).parameters
    kwargs: Dict[str, Any] = {}
    for name, param in parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        lname = name.lower()
        if param.kind is inspect.Parameter.POSITIONAL_ONLY:
            if param.default is inspect.Parameter.empty:
                raise TypeError(f"FileSystemEventListener positional-only parameter {name!r} is unsupported")
            continue
        if "dropzone" in lname:
            kwargs[name] = list(config.dropzone_roots)
        elif "scratch" in lname:
            kwargs[name] = list(config.scratch_roots)
        elif "poll" in lname:
            kwargs[name] = config.poll_interval
        elif param.default is inspect.Parameter.empty:
            raise TypeError(f"FileSystemEventListener requires unsupported parameter {name!r}")
    return FileSystemEventListener(**kwargs)


class TelemetryDaemonWorker:
    """Background ingestion thread flushing telemetry into a JSONL sink."""

    _instance_counter = itertools.count(1)

    def __init__(self, config: DaemonConfig, worker_id: Optional[str] = None) -> None:
        if not isinstance(config, DaemonConfig):
            raise TypeError(f"config must be a DaemonConfig, got {type(config).__qualname__}")
        self._config = config
        self.worker_id = worker_id or (
            f"telemetry-worker-{next(self._instance_counter)}-{uuid.uuid4().hex[:8]}"
        )
        self._sink_path = resolve_sink_path(config)
        self._inbox_queue: "queue.Queue[IngestItem]" = queue.Queue()
        self._event_buffer: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._flush_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._accepting = True
        self._started = False
        self._stop_requested = False
        self._events_collected = 0
        self._records_flushed = 0
        self._worker_exception: Optional[Exception] = None
        self._last_flush_error: Optional[OSError] = None
        self._consecutive_flush_failures = 0
        self._last_flush_attempt = time.monotonic()
        self._fs_listener: Any = None
        self._fs_listener_error: Optional[BaseException] = None

    # ------------------------------------------------------------------ props
    @property
    def sink_path(self) -> Path:
        return self._sink_path

    @property
    def events_collected(self) -> int:
        with self._lock:
            return self._events_collected

    @property
    def records_flushed(self) -> int:
        with self._lock:
            return self._records_flushed

    @property
    def worker_exception(self) -> Optional[Exception]:
        return self._worker_exception

    @property
    def last_flush_error(self) -> Optional[OSError]:
        return self._last_flush_error

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    @property
    def pending_buffer_size(self) -> int:
        with self._lock:
            return len(self._event_buffer)

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def is_running(self) -> bool:
        return bool(self._running) and self.is_alive()

    def _join_timeout(self) -> float:
        return max(_THREAD_JOIN_FLOOR_SECONDS, self._config.poll_interval * 10)

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError(f"worker {self.worker_id} has already been started")
            if self._stop_requested or not self._accepting:
                raise RuntimeError(f"worker {self.worker_id} is closed and cannot be started")
            self._started = True
        self._start_fs_listener()
        self._running = True
        thread = threading.Thread(target=self._run, name=self.worker_id, daemon=True)
        self._thread = thread
        try:
            thread.start()
        except BaseException:
            self._running = False
            self._shutdown_fs_listener(collect=True)
            raise

    def _run(self) -> None:
        poll_interval = self._config.poll_interval
        try:
            while self._running and not self._stop_event.is_set():
                self._pump_inbox(poll_interval)
                if not self._running:
                    break
                self._collect_fs_events()
                if self._flush_due():
                    try:
                        self.flush()
                    except OSError as exc:
                        self._note_flush_failure(exc)
        except Exception as exc:
            self._worker_exception = exc
            logger.exception("telemetry worker %s crashed", self.worker_id)
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop the worker, draining the inbox exhaustively and flushing to disk.

        Raises ``OSError`` if the remaining records cannot be persisted; they
        then remain in the event buffer (PCA-66).
        """
        with self._lock:
            self._accepting = False
            self._stop_requested = True
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout())
            if thread.is_alive():
                logger.warning(
                    "telemetry worker %s thread did not exit within %.1fs; draining concurrently",
                    self.worker_id,
                    self._join_timeout(),
                )
        self._running = False
        self._shutdown_fs_listener(collect=True)
        while True:
            self._drain_inbox_nowait()
            with self._lock:
                pending = len(self._event_buffer)
            if pending == 0 and self._inbox_queue.empty():
                break
            if pending:
                self.flush()

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    # -------------------------------------------------------------- ingestion
    @staticmethod
    def _validate_item(item: Any) -> None:
        if not isinstance(item, (TelemetryRecord, FileSystemEventRecord, Mapping, _LifecycleEvent)):
            raise TypeError(f"unsupported telemetry item type: {type(item).__qualname__}")

    def _enqueue(self, item: IngestItem) -> None:
        with self._lock:
            if not self._accepting:
                raise WorkerClosedError(f"worker {self.worker_id} is no longer accepting telemetry")
            self._inbox_queue.put_nowait(item)

    def submit_record(self, record: TelemetryRecord) -> None:
        if not isinstance(record, TelemetryRecord):
            raise TypeError(f"submit_record expects TelemetryRecord, got {type(record).__qualname__}")
        self._enqueue(record)

    def submit_event(self, event: FileSystemEventRecord) -> None:
        if not isinstance(event, FileSystemEventRecord):
            raise TypeError(f"submit_event expects FileSystemEventRecord, got {type(event).__qualname__}")
        self._enqueue(event)

    def submit_payload(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError(f"submit_payload expects a mapping, got {type(payload).__qualname__}")
        self._enqueue(dict(payload))

    def submit_lifecycle_event(self, event: str, details: Optional[Dict[str, Any]] = None) -> None:
        self._enqueue(_LifecycleEvent(event=str(event), details=dict(details or {})))

    def _normalise(self, item: Any) -> Dict[str, Any]:
        try:
            if isinstance(item, _LifecycleEvent):
                kind = "lifecycle"
                body: Dict[str, Any] = {"lifecycle_event": item.event, **item.details}
            elif isinstance(item, TelemetryRecord):
                kind = "telemetry_record"
                body = _object_to_dict(item)
            elif isinstance(item, FileSystemEventRecord):
                kind = "filesystem_event"
                body = _object_to_dict(item)
            elif isinstance(item, Mapping):
                kind = "payload"
                body = dict(item)
            else:
                kind = "unrecognised"
                body = {"type": type(item).__qualname__, "repr": _safe_repr(item)}
            return _json_safe(self._envelope(kind, body))
        except (TypeError, ValueError, RecursionError) as exc:
            logger.error(
                "telemetry worker %s could not serialise %s: %s", self.worker_id, type(item).__qualname__, exc
            )
            return _json_safe(
                self._envelope(
                    "serialisation_error",
                    {
                        "type": type(item).__qualname__,
                        "repr": _safe_repr(item),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            )

    def _envelope(self, kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "record_type": kind,
            "worker_id": self.worker_id,
            "daemon_pid": os.getpid(),
            "ingested_at": get_current_iso_timestamp(),
            "data": body,
        }

    def _ingest(self, item: Any) -> None:
        record = self._normalise(item)
        with self._lock:
            self._event_buffer.append(record)
            self._events_collected += 1

    def _pump_inbox(self, timeout: float) -> int:
        try:
            first = self._inbox_queue.get(timeout=timeout)
        except queue.Empty:
            return 0
        self._ingest(first)
        count = 1
        limit = self._config.flush_batch_size
        while count < limit:
            try:
                item = self._inbox_queue.get_nowait()
            except queue.Empty:
                break
            self._ingest(item)
            count += 1
        return count

    def _drain_inbox_nowait(self) -> int:
        count = 0
        while True:
            try:
                item = self._inbox_queue.get_nowait()
            except queue.Empty:
                return count
            self._ingest(item)
            count += 1

    # ------------------------------------------------------ filesystem events
    def _start_fs_listener(self) -> None:
        cfg = self._config
        if not cfg.enable_fs_listener:
            return
        roots = list(cfg.dropzone_roots) + list(cfg.scratch_roots)
        if not roots:
            return
        try:
            if cfg.auto_create_dirs:
                for root in roots:
                    root.mkdir(parents=True, exist_ok=True)
            listener = _construct_fs_listener(cfg)
            listener.start()
        except Exception as exc:
            self._fs_listener_error = exc
            logger.warning(
                "telemetry worker %s: filesystem listener unavailable (%s: %s); continuing without it",
                self.worker_id,
                type(exc).__name__,
                exc,
            )
            self._ingest(
                _LifecycleEvent(
                    event="FS_LISTENER_UNAVAILABLE",
                    details={
                        "error": f"{type(exc).__name__}: {exc}",
                        "dropzone_roots": [str(p) for p in cfg.dropzone_roots],
                        "scratch_roots": [str(p) for p in cfg.scratch_roots],
                    },
                )
            )
            return
        with self._lock:
            self._fs_listener = listener

    def _collect_fs_events(self) -> None:
        listener = self._fs_listener
        if listener is None:
            return
        events = listener.drain_events()
        for event in events or ():
            self._ingest(event)

    def _shutdown_fs_listener(self, collect: bool) -> None:
        with self._lock:
            listener = self._fs_listener
            self._fs_listener = None
        if listener is None:
            return

        def _drain() -> None:
            if not collect:
                return
            try:
                for event in listener.drain_events() or ():
                    self._ingest(event)
            except Exception as exc:
                logger.error("telemetry worker %s failed to drain filesystem events: %s", self.worker_id, exc)

        _drain()
        try:
            listener.stop()
        except Exception as exc:
            logger.error("telemetry worker %s failed to stop filesystem listener: %s", self.worker_id, exc)
        join = getattr(listener, "join", None)
        if callable(join):
            try:
                join(timeout=_THREAD_JOIN_FLOOR_SECONDS)
            except TypeError:
                join()
            except Exception as exc:
                logger.error("telemetry worker %s failed to join filesystem listener: %s", self.worker_id, exc)
        _drain()

    # ---------------------------------------------------------------- flushing
    def _flush_due(self) -> bool:
        with self._lock:
            pending = len(self._event_buffer)
        if pending == 0:
            return False
        if pending >= self._config.flush_batch_size:
            return True
        return (time.monotonic() - self._last_flush_attempt) >= self._config.poll_interval

    def _note_flush_failure(self, exc: OSError) -> None:
        self._last_flush_error = exc
        self._consecutive_flush_failures += 1
        if self._consecutive_flush_failures == 1:
            logger.warning(
                "telemetry worker %s cannot write sink %s (%s); records retained in buffer",
                self.worker_id,
                self._sink_path,
                exc,
            )
        else:
            logger.debug(
                "telemetry worker %s flush retry %d failed: %s",
                self.worker_id,
                self._consecutive_flush_failures,
                exc,
            )

    def flush(self) -> int:
        """Persist the event buffer to the sink (PCA-66 / DEF-DATA-01).

        The buffer is truncated only after the write, flush and fsync have all
        succeeded, and only by the number of records actually written. On
        ``OSError`` the buffer is left intact and the error propagates.
        """
        with self._flush_lock:
            with self._lock:
                pending = list(self._event_buffer)
            self._last_flush_attempt = time.monotonic()
            if not pending:
                return 0
            _append_jsonl(self._sink_path, pending, create_dirs=self._config.auto_create_dirs)
            flushed_count = len(pending)
            with self._lock:
                del self._event_buffer[:flushed_count]
                self._records_flushed += flushed_count
            if self._consecutive_flush_failures:
                logger.info(
                    "telemetry worker %s sink recovered after %d failed attempts",
                    self.worker_id,
                    self._consecutive_flush_failures,
                )
                self._consecutive_flush_failures = 0
            return flushed_count

    # -------------------------------------------------------------- migration
    def extract_uncommitted_state(
        self,
    ) -> Tuple[List[IngestItem], List[Dict[str, Any]]]:
        """Remove and return every uncommitted item (PCA-67 / DEF-QUEUE-01).

        Returns ``(queue_items, buffer_items)``: raw items still in the inbox
        and normalised records not yet flushed. Intake is closed and the thread
        is stopped first so nothing can be stranded after extraction.
        """
        with self._lock:
            self._accepting = False
        self._running = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(self._join_timeout())
            if thread.is_alive():
                logger.warning("telemetry worker %s thread still alive during state extraction", self.worker_id)
        self._shutdown_fs_listener(collect=True)
        with self._flush_lock:
            with self._lock:
                queue_items: List[IngestItem] = []
                while True:
                    try:
                        queue_items.append(self._inbox_queue.get_nowait())
                    except queue.Empty:
                        break
                buffer_items = list(self._event_buffer)
                del self._event_buffer[:]
        return queue_items, buffer_items

    def restore_uncommitted_state(
        self,
        queue_items: Sequence[IngestItem],
        buffer_items: Sequence[Dict[str, Any]],
    ) -> None:
        """Inject state extracted from another worker (all-or-nothing)."""
        for item in queue_items:
            self._validate_item(item)
        for record in buffer_items:
            if not isinstance(record, dict):
                raise TypeError(f"buffer items must be dicts, got {type(record).__qualname__}")
        with self._lock:
            if not self._accepting:
                raise WorkerClosedError(f"worker {self.worker_id} is closed; cannot restore state")
            self._event_buffer.extend(buffer_items)
            for item in queue_items:
                self._inbox_queue.put_nowait(item)


# --------------------------------------------------------------------------- #
# Supervisor                                                                   #
# --------------------------------------------------------------------------- #
class TelemetryDaemonSupervisor:
    """Watchdog supervising a :class:`TelemetryDaemonWorker` with restart and signal handling."""

    def __init__(self, config: DaemonConfig) -> None:
        if not isinstance(config, DaemonConfig):
            raise TypeError(f"config must be a DaemonConfig, got {type(config).__qualname__}")
        # Pin the sink path once so every worker (including replacements) writes to the same file.
        self._config = dataclasses.replace(config, telemetry_sink_path=resolve_sink_path(config))
        self._sink_path: Path = self._config.telemetry_sink_path  # type: ignore[assignment]
        self._lock = threading.RLock()
        self._state = DaemonState.INITIALIZING
        self._worker: Optional[TelemetryDaemonWorker] = None
        self._workers: List[TelemetryDaemonWorker] = []
        self._orphans: List[TelemetryDaemonWorker] = []
        self._holding: List[IngestItem] = []
        self._crash_history: List[Dict[str, Any]] = []
        self._metrics = DaemonTelemetryMetrics()
        self._failed = False
        self._started = False
        self._stop_started = False
        self._stop_owner: Optional[threading.Thread] = None
        self._intake_closed = False
        self._start_monotonic: Optional[float] = None
        self._end_monotonic: Optional[float] = None
        self._shutdown_event = threading.Event()
        self._stopped_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._shutdown_thread: Optional[threading.Thread] = None
        self._shutdown_trigger: Optional[str] = None
        self._original_handlers: Dict[int, Any] = {}
        self._console_ctrl_callback: Any = None
        self._console_ctrl_kernel32: Any = None
        self._signal_handler_ref = self._handle_signal
        self._worker_serial = itertools.count(1)
        self._instance_tag = uuid.uuid4().hex[:8]

    # ------------------------------------------------------------------ props
    @property
    def config(self) -> DaemonConfig:
        return self._config

    @property
    def state(self) -> DaemonState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state in (DaemonState.RUNNING, DaemonState.RESTARTING)

    @property
    def worker(self) -> Optional[TelemetryDaemonWorker]:
        with self._lock:
            return self._worker

    @property
    def crash_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._crash_history]

    def wait_for_termination(self, timeout: Optional[float] = None) -> bool:
        return self._stopped_event.wait(timeout)

    def _stop_budget(self) -> float:
        cfg = self._config
        return cfg.max_restart_backoff_seconds + cfg.health_check_interval + cfg.poll_interval * 10 + 30.0

    def _set_shutdown_trigger(self, trigger: str) -> None:
        with self._lock:
            if self._shutdown_trigger is None:
                self._shutdown_trigger = trigger

    # --------------------------------------------------------------- signals
    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.request_shutdown(signum)

    def register_signal_handlers(self) -> bool:
        """Install handlers for SIGINT/SIGTERM/SIGBREAK and Win32 console events.

        Returns True if at least one handler was installed. Python only allows
        signal registration from the main thread; elsewhere this returns False.
        """
        if threading.current_thread() is not threading.main_thread():
            logger.warning("signal handlers can only be registered from the main thread; skipping")
            return False
        installed = False
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, self._signal_handler_ref)
            except (OSError, ValueError, RuntimeError) as exc:
                logger.warning("could not install handler for %s: %s", name, exc)
                continue
            if signum not in self._original_handlers:
                self._original_handlers[signum] = previous
            installed = True
        if self._install_console_ctrl_handler():
            installed = True
        return installed

    def _install_console_ctrl_handler(self) -> bool:
        if os.name != "nt" or self._console_ctrl_callback is not None:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
            kernel32.SetConsoleCtrlHandler.argtypes = (handler_type, wintypes.BOOL)
            kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        except (ImportError, OSError, AttributeError) as exc:
            logger.warning("Win32 console control handler unavailable: %s", exc)
            return False

        def _console_handler(ctrl_type: int) -> bool:
            # Ctrl+C / Ctrl+Break are delivered to Python as SIGINT / SIGBREAK
            # and handled by the Python-level handlers in the main thread.
            if ctrl_type in (_WIN_CTRL_C_EVENT, _WIN_CTRL_BREAK_EVENT):
                return False
            name = _WIN_CONSOLE_EVENT_NAMES.get(ctrl_type)
            if name is None:
                return False
            self._request_shutdown(name)
            self._stopped_event.wait(_WIN_CONSOLE_HANDLER_GRACE_SECONDS)
            return True

        callback = handler_type(_console_handler)
        if not kernel32.SetConsoleCtrlHandler(callback, True):
            logger.warning("SetConsoleCtrlHandler failed (winerror %d)", ctypes.get_last_error())
            return False
        self._console_ctrl_callback = callback
        self._console_ctrl_kernel32 = kernel32
        return True

    def _uninstall_console_ctrl_handler(self) -> None:
        callback = self._console_ctrl_callback
        kernel32 = self._console_ctrl_kernel32
        if callback is None or kernel32 is None:
            return
        if not kernel32.SetConsoleCtrlHandler(callback, False):
            logger.warning("failed to remove Win32 console control handler")
        self._console_ctrl_callback = None
        self._console_ctrl_kernel32 = None

    def restore_signal_handlers(self) -> None:
        self._uninstall_console_ctrl_handler()
        if not self._original_handlers:
            return
        if threading.current_thread() is not threading.main_thread():
            logger.debug("signal handlers can only be restored from the main thread; deferring")
            return
        for signum, previous in list(self._original_handlers.items()):
            if previous is None:
                logger.warning(
                    "original handler for %s was not installed from Python and cannot be restored",
                    _signal_name(signum),
                )
            else:
                try:
                    signal.signal(signum, previous)
                except (OSError, ValueError, RuntimeError) as exc:
                    logger.warning("could not restore handler for %s: %s", _signal_name(signum), exc)
                    continue
            del self._original_handlers[signum]

    def request_shutdown(self, signum: Optional[int] = None) -> None:
        """Request an asynchronous graceful shutdown; safe to call from a signal handler."""
        self._request_shutdown(_signal_name(signum))

    def _request_shutdown(self, trigger: str) -> None:
        with self._lock:
            if self._shutdown_thread is not None or self._stop_started:
                return
            if self._shutdown_trigger is None:
                self._shutdown_trigger = trigger
            thread = threading.Thread(
                target=self._run_requested_shutdown,
                name=f"telemetry-daemon-shutdown-{self._instance_tag}",
                daemon=False,
            )
            self._shutdown_thread = thread
        thread.start()

    def _run_requested_shutdown(self) -> None:
        try:
            self.stop()
        except Exception:
            logger.exception("telemetry daemon shutdown raised")

    # -------------------------------------------------------------- lifecycle
    def _spawn_worker(
        self,
        migrated_items: Optional[Sequence[IngestItem]] = None,
        migrated_buffer: Optional[Sequence[Dict[str, Any]]] = None,
        start: bool = True,
        role: str = "worker",
    ) -> TelemetryDaemonWorker:
        worker = TelemetryDaemonWorker(
            self._config,
            worker_id=f"telemetry-{role}-{self._instance_tag}-{next(self._worker_serial)}",
        )
        if migrated_items or migrated_buffer:
            worker.restore_uncommitted_state(list(migrated_items or []), list(migrated_buffer or []))
        with self._lock:
            self._workers.append(worker)
        if start:
            worker.start()
        return worker

    def start(self, install_signals: bool = True) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("telemetry daemon supervisor has already been started")
            self._started = True
        worker: Optional[TelemetryDaemonWorker] = None
        try:
            carbon, silicon = resolve_atomic_masses()
            with self._lock:
                self._metrics.carbon_atomic_mass = carbon
                self._metrics.silicon_atomic_mass = silicon
                self._metrics.pid = os.getpid()
                self._start_monotonic = time.monotonic()
            worker = self._spawn_worker(start=False)
            worker.submit_lifecycle_event(
                "DAEMON_STARTUP",
                {
                    "timestamp": get_current_iso_timestamp(),
                    "daemon_pid": os.getpid(),
                    "sink": str(self._sink_path),
                    "config": self._config.to_dict(),
                    "carbon_atomic_mass": carbon,
                    "silicon_atomic_mass": silicon,
                },
            )
            worker.start()
            with self._lock:
                self._worker = worker
                self._state = DaemonState.RUNNING
                self._metrics.last_heartbeat_timestamp = get_current_iso_timestamp()
            watchdog = threading.Thread(
                target=self._watchdog_loop,
                name=f"telemetry-daemon-watchdog-{self._instance_tag}",
                daemon=True,
            )
            self._watchdog_thread = watchdog
            watchdog.start()
        except BaseException as exc:
            with self._lock:
                self._state = DaemonState.FAILED
                self._failed = True
                self._metrics.last_error = f"startup failed: {type(exc).__name__}: {exc}"
                if worker is not None and self._worker is None:
                    self._worker = worker
            logger.error("telemetry daemon failed to start: %s", exc)
            raise
        if install_signals:
            self.register_signal_handlers()

    def _watchdog_loop(self) -> None:
        interval = self._config.health_check_interval
        while not self._shutdown_event.wait(interval):
            try:
                self._check_worker_health()
            except Exception as exc:
                logger.exception("telemetry watchdog health check failed")
                with self._lock:
                    self._metrics.last_error = f"watchdog: {type(exc).__name__}: {exc}"
            with self._lock:
                if self._state == DaemonState.FAILED:
                    return

    def _check_worker_health(self) -> None:
        with self._lock:
            if self._state != DaemonState.RUNNING or self._shutdown_event.is_set():
                return
            worker = self._worker
        if worker is None:
            return
        if worker.is_alive():
            with self._lock:
                self._metrics.last_heartbeat_timestamp = get_current_iso_timestamp()
            return
        if worker.stop_requested:
            return
        self._handle_worker_crash(worker)

    def _handle_worker_crash(self, worker: TelemetryDaemonWorker) -> None:
        exc = worker.worker_exception
        reason = f"{type(exc).__name__}: {exc}" if exc is not None else "worker thread exited unexpectedly"
        # PCA-67: pull every uncommitted item off the dead worker before anything else.
        queue_items, buffer_items = worker.extract_uncommitted_state()
        now_iso = get_current_iso_timestamp()
        held: List[IngestItem] = []
        with self._lock:
            if self._worker is not worker:
                # The worker was already replaced; persist what was extracted rather than drop it.
                superseded = True
            else:
                superseded = False
                self._worker = None
                self._metrics.crash_count += 1
                self._metrics.last_error = reason
                limit_reached = self._metrics.restart_count >= self._config.max_restarts
                crash_info: Dict[str, Any] = {
                    "crash_index": self._metrics.crash_count,
                    "worker_id": worker.worker_id,
                    "timestamp": now_iso,
                    "reason": reason,
                    "exception_type": type(exc).__name__ if exc is not None else None,
                    "migrated_queue_items": len(queue_items),
                    "migrated_buffer_items": len(buffer_items),
                    "restart_scheduled": not limit_reached,
                }
                self._crash_history.append(crash_info)
                attempt = self._metrics.restart_count
                if limit_reached:
                    self._failed = True
                    if self._state == DaemonState.RUNNING:
                        self._state = DaemonState.FAILED
                    held = self._holding
                    self._holding = []
                elif self._state == DaemonState.RUNNING:
                    self._state = DaemonState.RESTARTING
        if superseded:
            self._salvage(queue_items, buffer_items)
            return
        logger.error("telemetry worker %s crashed: %s", worker.worker_id, reason)

        if limit_reached:
            logger.error(
                "telemetry daemon exceeded max_restarts=%d; halting restarts", self._config.max_restarts
            )
            failure_marker = _LifecycleEvent(
                event="DAEMON_RESTART_LIMIT_EXCEEDED",
                details={"crash": dict(crash_info), "max_restarts": self._config.max_restarts},
            )
            self._salvage(list(queue_items) + list(held) + [failure_marker], buffer_items)
            return

        backoff = min(
            self._config.restart_backoff_seconds * (2 ** attempt),
            self._config.max_restart_backoff_seconds,
        )
        self._shutdown_event.wait(backoff)

        with self._lock:
            held = self._holding
            self._holding = []
        restart_marker = _LifecycleEvent(
            event="DAEMON_WORKER_RESTARTED",
            details={"crash": dict(crash_info), "backoff_seconds": backoff, "restart_attempt": attempt + 1},
        )
        replacement = self._spawn_worker(
            list(queue_items) + list(held) + [restart_marker], buffer_items, start=False
        )
        with self._lock:
            self._worker = replacement
            if self._shutdown_event.is_set() or self._stop_started:
                # stop() drains and flushes the unstarted replacement.
                return
            self._metrics.restart_count += 1
            self._metrics.last_restart_timestamp = get_current_iso_timestamp()
        try:
            replacement.start()
        except Exception as start_exc:
            logger.error("replacement worker %s failed to start: %s", replacement.worker_id, start_exc)
            with self._lock:
                self._metrics.last_error = f"restart failed: {type(start_exc).__name__}: {start_exc}"
        with self._lock:
            if self._state == DaemonState.RESTARTING:
                # A replacement that failed to start is detected as a crash on the next health check.
                self._state = DaemonState.RUNNING

    def _stop_worker(self, worker: TelemetryDaemonWorker) -> bool:
        try:
            worker.stop()
        except OSError as exc:
            logger.error(
                "telemetry worker %s could not persist %d buffered records: %s",
                worker.worker_id,
                worker.pending_buffer_size,
                exc,
            )
            with self._lock:
                self._metrics.last_error = f"flush failed: {type(exc).__name__}: {exc}"
                if worker not in self._orphans:
                    self._orphans.append(worker)
            return False
        worker.join(timeout=_THREAD_JOIN_FLOOR_SECONDS)
        return True

    def _salvage(self, queue_items: Sequence[IngestItem], buffer_items: Sequence[Dict[str, Any]]) -> bool:
        if not queue_items and not buffer_items:
            return True
        salvage = self._spawn_worker(queue_items, buffer_items, start=False, role="salvage")
        return self._stop_worker(salvage)

    def stop(self, timeout: Optional[float] = None) -> None:
        budget = self._stop_budget() if timeout is None else max(0.0, float(timeout))
        current = threading.current_thread()
        with self._lock:
            if not self._started:
                self._stop_started = True
                self._intake_closed = True
                self._state = DaemonState.TERMINATED
                self._metrics.daemon_state = self._state.value
                self._stopped_event.set()
                return
            if self._stop_started:
                must_wait: Optional[bool] = self._stop_owner is not current
            else:
                must_wait = None
                self._stop_started = True
                self._stop_owner = current
                if self._shutdown_trigger is None:
                    self._shutdown_trigger = "stop"
                if self._state == DaemonState.FAILED:
                    self._failed = True
                self._state = DaemonState.STOPPING
        if must_wait is not None:
            if must_wait and not self._stopped_event.wait(budget):
                logger.warning("timed out after %.1fs waiting for in-progress shutdown", budget)
            return
        self._shutdown_event.set()
        self._perform_shutdown(budget)

    def _perform_shutdown(self, budget: float) -> None:
        failed = False
        try:
            watchdog = self._watchdog_thread
            if watchdog is not None and watchdog is not threading.current_thread():
                watchdog.join(budget)
                if watchdog.is_alive():
                    logger.error("telemetry watchdog did not exit within %.1fs", budget)
            with self._lock:
                worker = self._worker
                orphans = list(self._orphans)
                self._orphans.clear()
            persisted_ok = True
            if worker is not None and not self._stop_worker(worker):
                persisted_ok = False
            for orphan in orphans:
                if orphan is not worker and not self._stop_worker(orphan):
                    persisted_ok = False
            with self._lock:
                held = self._holding
                self._holding = []
                self._intake_closed = True
            if held and not self._salvage(held, []):
                persisted_ok = False
            with self._lock:
                self._end_monotonic = time.monotonic()
                failed = self._failed or not persisted_ok
            final_state = DaemonState.FAILED if failed else DaemonState.TERMINATED
            record = self._build_shutdown_record(final_state)
            try:
                _append_jsonl(self._sink_path, [record], create_dirs=self._config.auto_create_dirs)
            except OSError as exc:
                failed = True
                logger.error("could not write DAEMON_SHUTDOWN record to %s: %s", self._sink_path, exc)
                with self._lock:
                    self._metrics.last_error = f"shutdown record failed: {type(exc).__name__}: {exc}"
        except BaseException:
            failed = True
            logger.exception("telemetry daemon shutdown sequence failed")
            raise
        finally:
            with self._lock:
                if self._end_monotonic is None:
                    self._end_monotonic = time.monotonic()
                self._intake_closed = True
                self._state = DaemonState.FAILED if failed else DaemonState.TERMINATED
                self._metrics.daemon_state = self._state.value
            if threading.current_thread() is threading.main_thread():
                self.restore_signal_handlers()
            self._stopped_event.set()

    def _build_shutdown_record(self, final_state: DaemonState) -> Dict[str, Any]:
        metrics = self.poll_metrics()
        metrics.daemon_state = final_state.value
        with self._lock:
            trigger = self._shutdown_trigger
            crashes = len(self._crash_history)
        return _json_safe(
            {
                "record_type": "lifecycle",
                "worker_id": None,
                "daemon_pid": os.getpid(),
                "ingested_at": get_current_iso_timestamp(),
                "data": {
                    "lifecycle_event": "DAEMON_SHUTDOWN",
                    "trigger": trigger,
                    "final_state": final_state.value,
                    "crash_count": crashes,
                    "metrics": metrics.to_dict(),
                },
            }
        )

    # -------------------------------------------------------------- ingestion
    def _dispatch(self, item: IngestItem, submit_name: str) -> None:
        while True:
            with self._lock:
                if not self._started:
                    raise RuntimeError("telemetry daemon has not been started")
                if self._intake_closed or self._state in (DaemonState.TERMINATED, DaemonState.FAILED):
                    raise RuntimeError(f"telemetry daemon is {self._state.value}; not accepting telemetry")
                worker = self._worker
                if worker is None:
                    # Between a crash and its replacement: held and migrated on restart.
                    self._holding.append(item)
                    return
            try:
                getattr(worker, submit_name)(item)
                return
            except WorkerClosedError:
                with self._lock:
                    if self._worker is worker and not self._intake_closed:
                        self._holding.append(item)
                        return
                # The worker was swapped; retry against the current one.

    def submit_record(self, record: TelemetryRecord) -> None:
        if not isinstance(record, TelemetryRecord):
            raise TypeError(f"submit_record expects TelemetryRecord, got {type(record).__qualname__}")
        self._dispatch(record, "submit_record")

    def submit_event(self, event: FileSystemEventRecord) -> None:
        if not isinstance(event, FileSystemEventRecord):
            raise TypeError(f"submit_event expects FileSystemEventRecord, got {type(event).__qualname__}")
        self._dispatch(event, "submit_event")

    def submit_payload(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError(f"submit_payload expects a mapping, got {type(payload).__qualname__}")
        self._dispatch(dict(payload), "submit_payload")

    # ---------------------------------------------------------------- metrics
    def poll_metrics(self) -> DaemonTelemetryMetrics:
        with self._lock:
            snapshot = dataclasses.replace(self._metrics)
            workers = list(self._workers)
            started_at = self._start_monotonic
            ended_at = self._end_monotonic
            state = self._state
            worker = self._worker
        snapshot.pid = os.getpid()
        if started_at is not None:
            end = ended_at if ended_at is not None else time.monotonic()
            snapshot.uptime_seconds = max(0.0, end - started_at)
        snapshot.total_events_collected = sum(w.events_collected for w in workers)
        snapshot.total_records_flushed = sum(w.records_flushed for w in workers)
        snapshot.daemon_state = state.value
        snapshot.active_worker_id = worker.worker_id if worker is not None else None
        return snapshot

    # -------------------------------------------------------- context manager
    def __enter__(self) -> "TelemetryDaemonSupervisor":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._set_shutdown_trigger("context_exit" if exc_type is None else f"context_exit:{exc_type.__name__}")
        try:
            self.stop()
        finally:
            self.restore_signal_handlers()
        return None


# --------------------------------------------------------------------------- #
# Entrypoints                                                                  #
# --------------------------------------------------------------------------- #
def run_telemetry_daemon(
    config: DaemonConfig, duration_seconds: Optional[float] = None
) -> DaemonTelemetryMetrics:
    """Run the daemon synchronously until ``duration_seconds`` elapse or a signal arrives."""
    if duration_seconds is not None:
        duration_seconds = _coerce_real(duration_seconds, "duration_seconds")
        if not (math.isfinite(duration_seconds) and duration_seconds > 0):
            raise ValueError(f"duration_seconds must be positive (got {duration_seconds!r})")
    supervisor = TelemetryDaemonSupervisor(config)
    install_signals = threading.current_thread() is threading.main_thread()
    supervisor.start(install_signals=install_signals)
    deadline = None if duration_seconds is None else time.monotonic() + duration_seconds
    try:
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    supervisor._set_shutdown_trigger("duration_elapsed")
                    break
                wait_slice = min(_MAIN_WAIT_SLICE_SECONDS, remaining)
            else:
                wait_slice = _MAIN_WAIT_SLICE_SECONDS
            if supervisor.wait_for_termination(wait_slice):
                break
            if supervisor.state == DaemonState.FAILED:
                supervisor._set_shutdown_trigger("restart_limit_exceeded")
                break
    finally:
        supervisor.stop()
        supervisor.restore_signal_handlers()
    return supervisor.poll_metrics()


def _config_default(name: str) -> Any:
    for f in dataclasses.fields(DaemonConfig):
        if f.name == name:
            if f.default is not dataclasses.MISSING:
                return f.default
            if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                return f.default_factory()  # type: ignore[misc]
    raise KeyError(name)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cochem_telemetry_daemon",
        description="CoChem resilient telemetry event collection daemon.",
    )
    parser.add_argument("--dropzones", nargs="*", type=Path, default=[], metavar="DIR",
                        help="dropzone roots watched by the filesystem listener")
    parser.add_argument("--scratch", nargs="*", type=Path, default=[], metavar="DIR",
                        help="scratch roots watched by the filesystem listener")
    parser.add_argument("--sink", type=Path, default=None, metavar="FILE",
                        help=f"JSONL telemetry sink (default: ./{DEFAULT_SINK_RELATIVE_PATH.as_posix()})")
    parser.add_argument("--poll-interval", type=float, default=_config_default("poll_interval"))
    parser.add_argument("--max-restarts", type=int, default=_config_default("max_restarts"))
    parser.add_argument("--restart-backoff", type=float, default=_config_default("restart_backoff_seconds"))
    parser.add_argument("--max-restart-backoff", type=float,
                        default=_config_default("max_restart_backoff_seconds"))
    parser.add_argument("--health-check-interval", type=float, default=_config_default("health_check_interval"))
    parser.add_argument("--flush-batch-size", type=int, default=_config_default("flush_batch_size"))
    parser.add_argument("--duration", type=float, default=None, metavar="SECONDS",
                        help="run for a bounded duration (default: until signalled)")
    parser.add_argument("--no-fs-listener", action="store_true", help="disable the filesystem listener")
    parser.add_argument("--no-create-dirs", action="store_true", help="do not create missing directories")
    parser.add_argument("--log-level", default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, args.log_level),
            stream=sys.stderr,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    else:
        logger.setLevel(getattr(logging, args.log_level))
    try:
        config = DaemonConfig(
            dropzone_roots=list(args.dropzones or []),
            scratch_roots=list(args.scratch or []),
            telemetry_sink_path=args.sink,
            poll_interval=args.poll_interval,
            max_restarts=args.max_restarts,
            restart_backoff_seconds=args.restart_backoff,
            max_restart_backoff_seconds=args.max_restart_backoff,
            health_check_interval=args.health_check_interval,
            flush_batch_size=args.flush_batch_size,
            auto_create_dirs=not args.no_create_dirs,
            enable_fs_listener=not args.no_fs_listener,
        )
        if args.duration is not None and not (math.isfinite(args.duration) and args.duration > 0):
            raise ValueError(f"--duration must be positive (got {args.duration!r})")
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    metrics = run_telemetry_daemon(config, duration_seconds=args.duration)
    sys.stdout.write(json.dumps(metrics.to_dict(), sort_keys=True, default=_json_default) + "\n")
    sys.stdout.flush()
    return 0 if metrics.daemon_state == DaemonState.TERMINATED.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
