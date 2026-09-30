"""Cross-process concurrency primitives for CoChem.

The package exposes :class:`RWFileLock`, a reader-writer lock backed by an
operating-system byte-range lock on a sidecar lock file. On Windows it uses
``LockFileEx`` and ``UnlockFileEx``. On POSIX it uses ``flock``. The package
also exposes :class:`RWFileLockTimeoutError`, raised when a lock cannot be
acquired within the configured timeout.
"""
from __future__ import annotations

from cochem.concurrency.atomic_file_lock import (
    DEFAULT_LOCK_TIMEOUT_S,
    RWFileLock,
    RWFileLockTimeoutError,
)

__all__ = ["DEFAULT_LOCK_TIMEOUT_S", "RWFileLock", "RWFileLockTimeoutError"]
