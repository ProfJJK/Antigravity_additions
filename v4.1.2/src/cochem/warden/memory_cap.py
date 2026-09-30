"""Host Warden Guest VM Memory Allocation Cap (MC-HW-63)."""
from __future__ import annotations

# Guest VM static RAM ceiling: 32 GB (34,359,738,368 bytes)
VM_STATIC_RAM_CAP_BYTES: int = 34359738368


def get_vm_ram_cap_bytes() -> int:
    """Returns guest VM static RAM ceiling in bytes (32 GB)."""
    return VM_STATIC_RAM_CAP_BYTES


def get_vm_ram_cap_gb() -> int:
    """Returns guest VM static RAM ceiling in gigabytes."""
    return 32
