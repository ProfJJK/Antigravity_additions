"""Root facade re-exporting ``cochem_ml.self_healing_fsm`` (Task 2.18)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from cochem_ml import self_healing_fsm as _impl  # noqa: E402

globals().update({_name: getattr(_impl, _name) for _name in _impl.__all__})

__all__ = list(_impl.__all__)
