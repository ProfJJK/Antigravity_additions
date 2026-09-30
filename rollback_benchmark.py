"""Root mirror shim for :mod:`cochem_ml.rollback_benchmark` (Task 2.19)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from cochem_ml import rollback_benchmark as _impl  # noqa: E402
from cochem_ml.rollback_benchmark import *  # noqa: F401,F403,E402

__all__ = list(_impl.__all__)
