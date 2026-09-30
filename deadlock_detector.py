"""Repository-root facade for ``cochem_ml.deadlock_detector`` (Task 2.12)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from cochem_ml import deadlock_detector as _core  # noqa: E402
from cochem_ml.deadlock_detector import (  # noqa: E402
    COUNCIL_IMMUNE_ROLES,
    DEFAULT_GUARD_TIMEOUT_SEC,
    DEFAULT_INITIAL_SETTLE_SEC,
    AtomicFileLock,
    LockAcquisitionError,
    compute_file_sha256,
    exclusive_os_file_guard,
    iso_from_epoch,
    is_council_immune_role,
    read_lock_payload,
    utc_now_iso,
)

__all__ = list(_core.__all__)
