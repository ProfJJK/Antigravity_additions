"""Host Warden Process Priority Configuration (MC-HW-62)."""
from __future__ import annotations

# Windows Process Priority Class: BELOW_NORMAL_PRIORITY_CLASS (0x00004000 = 16384)
BELOW_NORMAL_PRIORITY_CLASS: int = 0x00004000


def get_host_warden_priority() -> str:
    """Returns the configured process priority class string for Host Warden."""
    return "BelowNormal"


def get_priority_class_value() -> int:
    """Returns the Windows Win32 API priority class constant value."""
    return BELOW_NORMAL_PRIORITY_CLASS
