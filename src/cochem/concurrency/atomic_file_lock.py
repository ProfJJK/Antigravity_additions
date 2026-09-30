"""Cross-process reader-writer file lock.

:class:`RWFileLock` serialises access to a shared resource, such as an HDF5
container, across processes. It holds an operating-system lock on a sidecar
lock file:

* **Windows**: ``LockFileEx`` is called with ``LOCKFILE_EXCLUSIVE_LOCK`` for
  writers or without it for shared readers. The call always uses
  ``LOCKFILE_FAIL_IMMEDIATELY``. The lock covers byte 0 of the lock file.
  Windows allows locking past EOF, so the file can stay empty.
* **POSIX**: ``fcntl.flock`` is called with ``LOCK_EX`` or ``LOCK_SH`` together
  with ``LOCK_NB``.

Both mechanisms attach the lock to the open file handle. As a result:

* Two ``RWFileLock`` instances in the same process conflict just as they would
  across processes.
* The operating system drops the lock when the owning process dies. A crashed
  writer therefore never leaves a stale lock behind.

The lock file itself is never deleted. Deleting it would open an unlink/recreate
race in which two processes could each lock a different inode.

Acquisition polls a non-blocking lock attempt until the timeout expires. On
expiry it raises :class:`RWFileLockTimeoutError`, which is a subclass of
:class:`TimeoutError`.
"""
from __future__ import annotations

import errno
import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

__all__ = ["DEFAULT_LOCK_TIMEOUT_S", "RWFileLock", "RWFileLockTimeoutError"]

DEFAULT_LOCK_TIMEOUT_S = 30.0
_POLL_INTERVAL_S = 0.05


class RWFileLockTimeoutError(TimeoutError):
    """Raised when acquiring an exclusive or shared RWFileLock exceeds its timeout."""


# --------------------------------------------------------------------------- #
# Platform backends
# --------------------------------------------------------------------------- #
if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_NOT_LOCKED = 158
    _ERROR_IO_PENDING = 997

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _LockFileEx = _kernel32.LockFileEx
    _LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _LockFileEx.restype = wintypes.BOOL

    _UnlockFileEx = _kernel32.UnlockFileEx
    _UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _UnlockFileEx.restype = wintypes.BOOL

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)

    def _try_lock(fd: int, shared: bool) -> bool:
        handle = msvcrt.get_osfhandle(fd)
        flags = _LOCKFILE_FAIL_IMMEDIATELY
        if not shared:
            flags |= _LOCKFILE_EXCLUSIVE_LOCK
        overlapped = _OVERLAPPED()
        if _LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
            return True
        err = ctypes.get_last_error()
        if err in (_ERROR_LOCK_VIOLATION, _ERROR_IO_PENDING):
            return False
        raise ctypes.WinError(err)

    def _unlock(fd: int) -> None:
        handle = msvcrt.get_osfhandle(fd)
        overlapped = _OVERLAPPED()
        if not _UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
            err = ctypes.get_last_error()
            if err != _ERROR_NOT_LOCKED:
                raise ctypes.WinError(err)

else:
    import fcntl

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)

    def _try_lock(fd: int, shared: bool) -> bool:
        op = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, op)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _validate_timeout(value: float, label: str) -> float:
    result = float(value)
    if math.isnan(result) or result < 0.0:
        raise ValueError(f"{label} must be a non-negative number of seconds, got {value!r}")
    return result


# --------------------------------------------------------------------------- #
# Public lock
# --------------------------------------------------------------------------- #
class RWFileLock:
    """Cross-process reader-writer lock bound to a sidecar lock file.

    Parameters
    ----------
    lock_path:
        Path of the lock file. The file is created on demand.
    timeout:
        Default acquisition timeout in seconds. The default is 30.0.
    """

    def __init__(self, lock_path: Union[str, Path], timeout: float = DEFAULT_LOCK_TIMEOUT_S) -> None:
        self._path = Path(lock_path)
        self._timeout = _validate_timeout(timeout, "timeout")
        self._fd: Optional[int] = None
        self._shared = False
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    @property
    def path(self) -> Path:
        return self._path

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def is_locked(self) -> bool:
        """True while this instance holds the lock, in either mode."""
        return self._fd is not None

    @property
    def is_shared(self) -> bool:
        """True while this instance holds a shared (reader) lock."""
        return self._fd is not None and self._shared

    # ------------------------------------------------------------------ #
    def acquire(self, shared: bool = False, timeout: Optional[float] = None) -> bool:
        """Acquire the lock. Pass ``shared=False`` (the default) for exclusive access.

        Raises :class:`RWFileLockTimeoutError` if the lock is still unavailable
        after ``timeout`` seconds. When ``timeout`` is omitted, the instance
        default is used.
        """
        budget = self._timeout if timeout is None else _validate_timeout(timeout, "timeout")
        with self._state_lock:
            if self._fd is not None:
                raise RuntimeError(f"RWFileLock on {self._path} is already held by this instance")
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._path), _OPEN_FLAGS, 0o666)
            start = time.monotonic()
            deadline = start + budget
            try:
                while True:
                    if _try_lock(fd, bool(shared)):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        mode = "shared" if shared else "exclusive"
                        err = RWFileLockTimeoutError(
                            f"could not acquire {mode} lock on {self._path} within {budget:.3f} s "
                            f"(waited {time.monotonic() - start:.3f} s)"
                        )
                        err.lock_path = self._path
                        err.timeout = budget
                        err.shared = bool(shared)
                        raise err
                    time.sleep(min(_POLL_INTERVAL_S, remaining))
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
            self._shared = bool(shared)
        return True

    def release(self) -> None:
        """Release the lock. This is idempotent: calling it when not held does nothing."""
        with self._state_lock:
            fd = self._fd
            if fd is None:
                return
            self._fd = None
            self._shared = False
            try:
                _unlock(fd)
            finally:
                os.close(fd)

    # ------------------------------------------------------------------ #
    @contextmanager
    def write_lock(self, timeout: Optional[float] = None) -> Iterator["RWFileLock"]:
        """Hold the exclusive (writer) lock for the duration of the block."""
        self.acquire(shared=False, timeout=timeout)
        try:
            yield self
        finally:
            self.release()

    @contextmanager
    def read_lock(self, timeout: Optional[float] = None) -> Iterator["RWFileLock"]:
        """Hold a shared (reader) lock for the duration of the block."""
        self.acquire(shared=True, timeout=timeout)
        try:
            yield self
        finally:
            self.release()

    def __enter__(self) -> "RWFileLock":
        self.acquire(shared=False)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def __del__(self) -> None:
        # Closing the descriptor drops the OS lock. This covers instances that
        # are garbage-collected while still holding the lock.
        fd = getattr(self, "_fd", None)
        if fd is not None:
            self._fd = None
            try:
                os.close(fd)
            except OSError:
                return

    def __repr__(self) -> str:
        state = "unlocked"
        if self._fd is not None:
            state = "shared" if self._shared else "exclusive"
        return f"RWFileLock(path={str(self._path)!r}, timeout={self._timeout}, state={state})"
