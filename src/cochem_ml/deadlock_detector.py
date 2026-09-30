"""Lock primitives shared by the CoChem lock reclamation subsystem (Task 2.12).

Public surface:

* ``COUNCIL_IMMUNE_ROLES``: RBAC Invariant 9 roles whose locks may never be
  reaped unilaterally.
* ``compute_file_sha256``: streaming byte-level SHA-256 used for forensic
  digests and TOCTOU verification.
* ``exclusive_os_file_guard``: kernel-released advisory guard (``msvcrt`` on
  Windows, ``fcntl`` on POSIX). The OS releases it automatically when the
  holder dies, so a crashed writer can never wedge the guard.
* ``AtomicFileLock``: ``O_CREAT | O_EXCL`` lock file carrying JSON metadata
  (pid, role, acquisition time, heartbeat). This is the wire format that
  ``cochem_ml.lock_reclamation`` diagnoses.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple, Union

PathLike = Union[str, "os.PathLike[str]"]

COUNCIL_IMMUNE_ROLES: Tuple[str, ...] = (
    "0rchestrator",
    "cochem-audit",
    "cochem-improve",
    "cochem-sdp-manager",
    "cochem-debug",
    "adversary",
    "cochem-scribe",
    "cochem-coder",
    "cochem-tester",
)

# A lock file is created empty (O_CREAT|O_EXCL) and its metadata written
# immediately afterwards. Files younger than this window may be mid-write.
DEFAULT_INITIAL_SETTLE_SEC: float = 2.0
DEFAULT_GUARD_TIMEOUT_SEC: float = 10.0

_GUARD_POLL_SEC = 0.005
_HASH_CHUNK_BYTES = 1 << 16

__all__ = [
    "AtomicFileLock",
    "COUNCIL_IMMUNE_ROLES",
    "DEFAULT_GUARD_TIMEOUT_SEC",
    "DEFAULT_INITIAL_SETTLE_SEC",
    "LockAcquisitionError",
    "compute_file_sha256",
    "exclusive_os_file_guard",
    "iso_from_epoch",
    "is_council_immune_role",
    "read_lock_payload",
    "utc_now_iso",
]


class LockAcquisitionError(RuntimeError):
    """Raised when an AtomicFileLock cannot be acquired or its ownership was lost."""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with explicit offset."""
    return datetime.now(timezone.utc).isoformat()


def iso_from_epoch(timestamp: float) -> str:
    """Convert epoch seconds to an ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def compute_file_sha256(path: PathLike, chunk_size: int = _HASH_CHUNK_BYTES) -> str:
    """Stream the file at ``path`` and return its lowercase hex SHA-256 digest."""
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_council_immune_role(
    role: Optional[str], immune_roles: Iterable[str] = COUNCIL_IMMUNE_ROLES
) -> bool:
    """Case-insensitive membership test against the Council immunity roster.

    Case folding prevents trivially bypassing immunity with e.g. ``Cochem-Audit``.
    """
    if not isinstance(role, str):
        return False
    normalized = role.strip().lower()
    if not normalized:
        return False
    return normalized in {str(r).strip().lower() for r in immune_roles}


@contextlib.contextmanager
def exclusive_os_file_guard(
    guard_path: PathLike, timeout_sec: float = DEFAULT_GUARD_TIMEOUT_SEC
) -> Iterator[None]:
    """Hold an exclusive OS advisory lock on ``guard_path`` for the ``with`` body.

    Raises ``TimeoutError`` when the guard cannot be obtained within
    ``timeout_sec``. The lock is released by the kernel if this process dies.
    """
    target = Path(guard_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    deadline = time.monotonic() + max(0.0, float(timeout_sec))
    try:
        if os.name == "nt":
            import msvcrt

            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"could not acquire OS guard {target} within {timeout_sec}s"
                        ) from exc
                    time.sleep(_GUARD_POLL_SEC)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"could not acquire OS guard {target} within {timeout_sec}s"
                        ) from exc
                    time.sleep(_GUARD_POLL_SEC)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_lock_payload(lock_path: PathLike) -> Optional[Dict[str, Any]]:
    """Return the decoded JSON object stored in a lock file, or None if absent/unparseable."""
    try:
        raw = Path(lock_path).read_bytes()
    except FileNotFoundError:
        return None
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


class AtomicFileLock:
    """Exclusive lock file created with ``O_CREAT | O_EXCL``.

    The file body is a UTF-8 JSON object describing the holder. Heartbeats
    rewrite the body atomically (temp file + ``os.replace``) so readers never
    observe a torn write.
    """

    def __init__(
        self,
        lock_path: PathLike,
        role: str,
        *,
        acquire_timeout_sec: float = 0.0,
        poll_interval_sec: float = 0.05,
    ) -> None:
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role must be a non-empty string")
        if poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec must be positive")
        self.lock_path = Path(lock_path)
        self.role = role.strip()
        self._acquire_timeout_sec = max(0.0, float(acquire_timeout_sec))
        self._poll_interval_sec = float(poll_interval_sec)
        self._token: Optional[str] = None
        self._acquired_at: Optional[float] = None

    def _payload(self, acquired_at: float, heartbeat_at: float) -> Dict[str, Any]:
        return {
            "pid": os.getpid(),
            "role": self.role,
            "lock_token": self._token,
            "acquired_at": acquired_at,
            "acquired_at_iso": iso_from_epoch(acquired_at),
            "heartbeat_ts": heartbeat_at,
            "heartbeat": iso_from_epoch(heartbeat_at),
        }

    def try_acquire(self) -> bool:
        """Single non-blocking acquisition attempt."""
        if self._token is not None:
            raise LockAcquisitionError(f"{self.lock_path} already held by this instance")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(str(self.lock_path), flags, 0o644)
        except FileExistsError:
            return False
        token = uuid.uuid4().hex
        acquired = time.time()
        self._token = token
        data = json.dumps(self._payload(acquired, acquired)).encode("utf-8")
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            self._token = None
            with contextlib.suppress(OSError):
                self.lock_path.unlink()
            raise
        os.close(fd)
        self._acquired_at = acquired
        return True

    def acquire(self, timeout_sec: Optional[float] = None) -> bool:
        """Poll for the lock until acquired or ``timeout_sec`` elapses."""
        budget = self._acquire_timeout_sec if timeout_sec is None else max(0.0, float(timeout_sec))
        deadline = time.monotonic() + budget
        while True:
            if self.try_acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll_interval_sec)

    def owns_lock(self) -> bool:
        """True when the on-disk lock still carries this instance's pid and token."""
        if self._token is None:
            return False
        payload = read_lock_payload(self.lock_path)
        if payload is None:
            return False
        return payload.get("pid") == os.getpid() and payload.get("lock_token") == self._token

    @property
    def is_held(self) -> bool:
        return self.owns_lock()

    def heartbeat(self) -> float:
        """Refresh the heartbeat timestamp; returns the new heartbeat epoch."""
        if not self.owns_lock() or self._acquired_at is None:
            raise LockAcquisitionError(f"ownership of {self.lock_path} was lost")
        now = time.time()
        temp_path = self.lock_path.with_name(f".{self.lock_path.name}.{self._token}.hb")
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(self._payload(self._acquired_at, now)))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.lock_path)
        finally:
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()
        return now

    def release(self) -> bool:
        """Remove the lock file if still owned; returns True when removed."""
        owned = self.owns_lock()
        self._token = None
        self._acquired_at = None
        if not owned:
            return False
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return False
        return True

    def __enter__(self) -> "AtomicFileLock":
        if not self.acquire():
            raise LockAcquisitionError(f"could not acquire {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
