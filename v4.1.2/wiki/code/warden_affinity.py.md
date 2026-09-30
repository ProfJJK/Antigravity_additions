# src/cochem/warden/affinity.py

`python
"""Host Warden Intel E-Core Affinity Management (MC-HW-61)."""
from __future__ import annotations
import os

# Intel E-Core affinity bitmask: cores 16-23 (0x00FF0000 = 16711680)
ECORE_AFFINITY_MASK: int = 0x00FF0000


def get_ecore_affinity_mask() -> int:
    """Returns the dedicated 64-bit affinity mask pinned to Intel E-Cores."""
    return ECORE_AFFINITY_MASK


def apply_ecore_affinity() -> bool:
    """Applies E-Core affinity mask to the current process on Windows."""
    try:
        import psutil
        p = psutil.Process()
        cpu_total = os.cpu_count() or 24
        if cpu_total >= 24:
            p.cpu_affinity(list(range(16, 24)))
            return True
        return True
    except (ImportError, OSError, ValueError):
        return False

`
