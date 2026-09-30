# src/cochem/quarantine/vm_profile.py

`python
"""Quarantine VM Hypervisor Profile and Resource Enforcement Engine (SRS-412-02).

Implements the Windows 11 Host-Side Hyper-V virtualization profile management,
static 32 GB RAM ceiling enforcement, Dynamic Memory permanent disabling,
12 vCPU allocation with hard 70% Maximum CPU cap, and live verification probes
for the ephemeral Quarantine VM (SRS-412-02, MC-QVM-06..09).

Architectural Compliance:
- SRS-412-02-FR-001: The Quarantine VM shall enforce a static 32 GB RAM ceiling
  (34,359,738,368 bytes) with Hyper-V Dynamic Memory permanently disabled ($false).
- SRS-412-02-FR-002: The VM hypervisor configuration shall allocate 12 virtual CPUs
  with a hard 70% CPU resource allocation cap (Maximum=70, RelativeWeight=100).
- Windows Subprocess Isolation: Windowless host-side execution enforcing
  CREATE_NO_WINDOW (0x08000000) on all PowerShell and hypervisor probes to prevent
  desktop heap exhaustion and focus stealing.
- Traceability: test_ch02_fr_001_*, test_ch02_fr_002_*, and
  test_f05_srs_covers_all_core_domains_vm_warden_db_tdd.

Zero-Mock Mandate:
- Authentic PowerShell execution bridge with environment-isolated parameters.
- Physical Get-VMMemory and Get-VMProcessor hypervisor inspection probes.
- Zero mock variables, zero pass stubs, zero synthetic data generators.
- Strictly zero chemistry libraries (Mendeleev, PySCF, ASE forbidden) - general software pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

# Win32 platform integration with graceful fallback
try:
    import pywintypes  # type: ignore[import-untyped]
    import win32api  # type: ignore[import-untyped]
    import win32con  # type: ignore[import-untyped]
    import win32process  # type: ignore[import-untyped]
except ImportError:
    pywintypes = None  # type: ignore
    win32api = None  # type: ignore
    win32con = None  # type: ignore
    win32process = None  # type: ignore

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

# Host Warden memory cap integration
try:
    from cochem.warden.memory_cap import get_vm_ram_cap_bytes, VM_STATIC_RAM_CAP_BYTES
except ImportError:
    # Canonical Host Warden Guest VM Memory Allocation Cap (MC-HW-63)
    VM_STATIC_RAM_CAP_BYTES: int = 34359738368  # 32 GB static ceiling

    def get_vm_ram_cap_bytes() -> int:
        """Returns guest VM static RAM ceiling in bytes (32 GB)."""
        return VM_STATIC_RAM_CAP_BYTES

logger = logging.getLogger("cochem.quarantine.vm_profile")

# ==============================================================================
# Architectural Constants (SRS-412-02 §2 / §4 / §7)
# ==============================================================================

# Windows subprocess creation flag preventing GUI window popup
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
SUBPROCESS_FLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

# Default Quarantine VM identifier
DEFAULT_VM_NAME: str = "CoChem-Quarantine-VM"

# SRS-412-02-FR-001: Static 32 GB RAM ceiling (34,359,738,368 bytes)
STATIC_RAM_CAP_BYTES: int = get_vm_ram_cap_bytes()

# SRS-412-02-FR-002: 12 vCPUs, hard 70% CPU Maximum cap, 100 relative weight
VCPU_COUNT: int = 12
CPU_CAP_PERCENT: int = 70
CPU_RELATIVE_WEIGHT: int = 100

# Regex for safe VM identifiers (prevents shell injection into PowerShell commands)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


# ==============================================================================
# Parameter Validation & Security Guards
# ==============================================================================

def validate_vm_name(value: object) -> str:
    """Validates that vm_name is a non-empty, safe alphanumeric identifier.

    Raises ValueError or TypeError on empty, invalid, or injection-formatted input.
    """
    if value is None or not isinstance(value, str):
        raise TypeError(f"VM name must be a non-empty string, got {type(value).__name__}")
    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("VM name must not be empty")
    if not _SAFE_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"VM name contains disallowed characters or formatting: {value!r}")
    return cleaned


# ==============================================================================
# [MC-QVM-06] SRS-412-02-FR-001: Static 32 GB RAM Ceiling & Dynamic Memory Ban
# ==============================================================================

def build_set_vm_memory_command(vm_name: str, vm_state: str = "Off") -> list[str]:
    """Builds PowerShell Set-VMMemory argv enforcing static 32 GB RAM ceiling.

    Satisfies SRS-412-02-FR-001: The Quarantine VM shall enforce a static 32 GB RAM
    ceiling with Hyper-V Dynamic Memory permanently disabled.

    Args:
        vm_name: Target Hyper-V virtual machine name.
        vm_state: Current VM state (e.g. "Off", "Running"). Changing memory
                  allocation requires the VM to be powered Off.

    Returns:
        Argv list for execution via subprocess.run (never shell=True).

    Raises:
        ValueError: If vm_name is empty or invalid.
        RuntimeError: If vm_state is not "Off".
    """
    if not vm_name or not vm_name.strip():
        raise ValueError("VM name must not be empty")
    clean_name = vm_name.strip().strip('"').strip("'")
    if not clean_name:
        raise ValueError("VM name must not be empty")

    if vm_state.strip().lower() != "off":
        raise RuntimeError(f"VM must be Off to change memory settings; current state is {vm_state!r}")

    quoted_name = f'"{clean_name}"'
    cap_bytes = str(get_vm_ram_cap_bytes())

    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "Set-VMMemory",
        "-VMName",
        quoted_name,
        "-DynamicMemoryEnabled",
        "$false",
        "-StartupBytes",
        cap_bytes,
    ]


# ==============================================================================
# [MC-QVM-07] SRS-412-02-FR-001/verify: Dynamic Memory Disabled Probe
# ==============================================================================

def verify_dynamic_memory_disabled(
    vm_name: str,
    raw_json: str | dict[str, Any] | None = None,
) -> list[str]:
    """Verifies that Dynamic Memory is permanently disabled and Startup equals 32 GB.

    Satisfies SRS-412-02-FR-001: The Quarantine VM shall enforce a static 32 GB RAM
    ceiling with Hyper-V Dynamic Memory permanently disabled.

    Args:
        vm_name: Target Hyper-V virtual machine name.
        raw_json: Optional pre-queried JSON string or dictionary from Get-VMMemory.
                  If None, physically queries Hyper-V via powershell.exe.

    Returns:
        List of architectural violation messages (empty list indicates full compliance).
    """
    if not vm_name or not vm_name.strip():
        raise ValueError("VM name must not be empty")
    clean_name = vm_name.strip().strip('"').strip("'")
    if not clean_name:
        raise ValueError("VM name must not be empty")

    violations: list[str] = []

    if raw_json is not None:
        data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    else:
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Get-VMMemory -VMName '{clean_name}' | ConvertTo-Json",
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode != 0:
            return [f"Failed to query Get-VMMemory for {vm_name}: {res.stderr.strip()}"]
        stdout_clean = res.stdout.strip()
        if not stdout_clean:
            return [f"Empty response from Get-VMMemory for {vm_name}"]
        data = json.loads(stdout_clean)

    if data.get("DynamicMemoryEnabled") is not False:
        violations.append(f"DynamicMemoryEnabled is True; expected False for VM '{vm_name}'")

    startup = data.get("Startup")
    if startup != STATIC_RAM_CAP_BYTES:
        violations.append(
            f"Startup memory {startup} bytes does not match required static cap {STATIC_RAM_CAP_BYTES}"
        )

    return violations


# ==============================================================================
# [MC-QVM-08] SRS-412-02-FR-002: 12 vCPUs & Hard 70% Maximum Cap Builder
# ==============================================================================

def build_set_vm_processor_command(
    vm_name: str,
    count: int = VCPU_COUNT,
    maximum: int = CPU_CAP_PERCENT,
    relative_weight: int = CPU_RELATIVE_WEIGHT,
) -> list[str]:
    """Builds PowerShell Set-VMProcessor argv enforcing 12 vCPUs and 70% max cap.

    Satisfies SRS-412-02-FR-002: The VM hypervisor configuration shall allocate 12
    virtual CPUs with a hard 70% CPU resource allocation cap.

    Args:
        vm_name: Target Hyper-V virtual machine name.
        count: Virtual processor count (default: 12 vCPUs).
        maximum: Hyper-V Maximum CPU percentage cap (default: 70%).
        relative_weight: CPU scheduling priority weight (default: 100).

    Returns:
        Argv list for execution via subprocess.run (never shell=True).

    Raises:
        ValueError: If vm_name is empty or parameters are out of bounds.
    """
    if not vm_name or not vm_name.strip():
        raise ValueError("VM name must not be empty")
    clean_name = vm_name.strip().strip('"').strip("'")
    if not clean_name:
        raise ValueError("VM name must not be empty")

    if count <= 0:
        raise ValueError(f"vCPU count must be positive, got {count}")
    if not (1 <= maximum <= 100):
        raise ValueError(f"CPU maximum percentage must be between 1 and 100, got {maximum}")
    if relative_weight <= 0:
        raise ValueError(f"Relative weight must be positive, got {relative_weight}")

    quoted_name = f'"{clean_name}"'
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "Set-VMProcessor",
        "-VMName",
        quoted_name,
        "-Count",
        str(count),
        "-Maximum",
        str(maximum),
        "-RelativeWeight",
        str(relative_weight),
    ]


# ==============================================================================
# [MC-QVM-09] SRS-412-02-FR-002/verify: Processor Count & Maximum Cap Probe
# ==============================================================================

def verify_vm_processor_cap(
    vm_name: str,
    raw_json: str | dict[str, Any] | None = None,
) -> list[str]:
    """Parses Get-VMProcessor JSON to verify 12 vCPUs and hard 70% Maximum cap.

    Satisfies SRS-412-02-FR-002: The VM hypervisor configuration shall allocate 12
    virtual CPUs with a hard 70% CPU resource allocation cap.

    Args:
        vm_name: Target Hyper-V virtual machine name.
        raw_json: Optional pre-queried JSON string or dictionary from Get-VMProcessor.
                  If None, physically queries Hyper-V via powershell.exe.

    Returns:
        List of architectural violation messages (empty list indicates full compliance).
    """
    if not vm_name or not vm_name.strip():
        raise ValueError("VM name must not be empty")
    clean_name = vm_name.strip().strip('"').strip("'")
    if not clean_name:
        raise ValueError("VM name must not be empty")

    violations: list[str] = []

    if raw_json is not None:
        data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    else:
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Get-VMProcessor -VMName '{clean_name}' | ConvertTo-Json",
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode != 0:
            return [f"Failed to query Get-VMProcessor for {vm_name}: {res.stderr.strip()}"]
        stdout_clean = res.stdout.strip()
        if not stdout_clean:
            return [f"Empty response from Get-VMProcessor for {vm_name}"]
        data = json.loads(stdout_clean)

    count = data.get("Count")
    if count != VCPU_COUNT:
        violations.append(
            f"VM '{vm_name}' vCPU count mismatch: expected {VCPU_COUNT}, got {count}"
        )

    maximum = data.get("Maximum")
    if maximum != CPU_CAP_PERCENT:
        violations.append(
            f"VM '{vm_name}' CPU Maximum cap mismatch: expected {CPU_CAP_PERCENT}%, got {maximum}%"
        )

    return violations


# ==============================================================================
# Complete VM Profile Specification Data Model
# ==============================================================================

@dataclass(frozen=True)
class VMHardwareProfile:
    """Hyper-V Quarantine VM Hardware Profile (SRS-412-02 §2 / §4)."""

    vm_name: str = DEFAULT_VM_NAME
    ram_cap_bytes: int = STATIC_RAM_CAP_BYTES
    dynamic_memory_enabled: bool = False
    vcpu_count: int = VCPU_COUNT
    cpu_cap_percent: int = CPU_CAP_PERCENT
    cpu_relative_weight: int = CPU_RELATIVE_WEIGHT

    def __post_init__(self) -> None:
        if not self.vm_name or not self.vm_name.strip():
            raise ValueError("vm_name must be non-empty")
        if self.ram_cap_bytes != STATIC_RAM_CAP_BYTES:
            raise ValueError(f"ram_cap_bytes must equal static ceiling {STATIC_RAM_CAP_BYTES}")
        if self.dynamic_memory_enabled is not False:
            raise ValueError("dynamic_memory_enabled must be False")
        if self.vcpu_count != VCPU_COUNT:
            raise ValueError(f"vcpu_count must equal {VCPU_COUNT}")
        if self.cpu_cap_percent != CPU_CAP_PERCENT:
            raise ValueError(f"cpu_cap_percent must equal {CPU_CAP_PERCENT}")

    def to_dict(self) -> dict[str, Any]:
        """Serializes hardware profile to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VMHardwareProfile:
        """Instantiates hardware profile from dictionary."""
        return cls(**data)


# ==============================================================================
# Physical Execution & Audit Operations
# ==============================================================================

def query_vm_state(vm_name: str, timeout: float = 15.0) -> str:
    """Queries physical Hyper-V VM state via PowerShell.

    Returns:
        State string (e.g. "Off", "Running", "Saved", "Paused", "Unknown").
    """
    clean_name = validate_vm_name(vm_name)
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f"(Get-VM -Name '{clean_name}').State.ToString()",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0:
            return res.stdout.strip() or "Unknown"
        logger.warning("Get-VM returned code %d: %s", res.returncode, res.stderr.strip())
        return "Unknown"
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("Failed querying VM state for %s: %s", vm_name, exc)
        return "Unknown"


def apply_vm_profile(
    vm_name: str = DEFAULT_VM_NAME,
    timeout: float = 30.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Applies complete Quarantine VM hypervisor profile (memory + vCPU cap).

    Satisfies SRS-412-02-FR-001 and SRS-412-02-FR-002.
    """
    clean_name = validate_vm_name(vm_name)
    current_state = query_vm_state(clean_name, timeout=timeout)

    mem_cmd = build_set_vm_memory_command(clean_name, vm_state=current_state if current_state != "Unknown" else "Off")
    proc_cmd = build_set_vm_processor_command(clean_name)

    if dry_run:
        return {
            "status": "DRY_RUN",
            "vm_name": clean_name,
            "vm_state": current_state,
            "memory_command": mem_cmd,
            "processor_command": proc_cmd,
            "applied": False,
        }

    # Execute Set-VMMemory
    mem_res = subprocess.run(
        mem_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if mem_res.returncode != 0:
        return {
            "status": "FAILED_MEMORY_CONFIG",
            "vm_name": clean_name,
            "error": mem_res.stderr.strip(),
            "applied": False,
        }

    # Execute Set-VMProcessor
    proc_res = subprocess.run(
        proc_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc_res.returncode != 0:
        return {
            "status": "FAILED_PROCESSOR_CONFIG",
            "vm_name": clean_name,
            "error": proc_res.stderr.strip(),
            "applied": False,
        }

    return {
        "status": "APPLIED",
        "vm_name": clean_name,
        "vm_state": current_state,
        "applied": True,
    }


def audit_full_vm_profile(
    vm_name: str = DEFAULT_VM_NAME,
    memory_json: str | dict[str, Any] | None = None,
    processor_json: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audits VM hypervisor configuration against SRS-412-02-FR-001 and FR-002."""
    clean_name = validate_vm_name(vm_name)
    mem_violations = verify_dynamic_memory_disabled(clean_name, raw_json=memory_json)
    proc_violations = verify_vm_processor_cap(clean_name, raw_json=processor_json)

    all_violations = mem_violations + proc_violations
    compliant = len(all_violations) == 0

    return {
        "vm_name": clean_name,
        "compliant": compliant,
        "violation_count": len(all_violations),
        "memory_violations": mem_violations,
        "processor_violations": proc_violations,
        "all_violations": all_violations,
        "timestamp": time.time(),
    }


# ==============================================================================
# Standalone CLI Entrypoint
# ==============================================================================

def main() -> int:
    """CLI entrypoint for Quarantine VM Hypervisor Profile Management."""
    parser = argparse.ArgumentParser(
        description="Quarantine VM Hypervisor Profile Management (SRS-412-02)",
    )
    parser.add_argument(
        "--vm-name",
        default=DEFAULT_VM_NAME,
        help="Target Hyper-V virtual machine name (default: %(default)s)",
    )
    parser.add_argument(
        "--action",
        choices=["verify", "apply", "show-commands", "profile"],
        default="verify",
        help="Operation to perform (default: %(default)s)",
    )
    parser.add_argument(
        "--state",
        default="Off",
        help="Assumed VM state for command building (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate command application without modifying Hyper-V",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON",
    )

    args = parser.parse_args()

    try:
        clean_name = validate_vm_name(args.vm_name)
    except (ValueError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.action == "show-commands":
        mem_cmd = build_set_vm_memory_command(clean_name, vm_state=args.state)
        proc_cmd = build_set_vm_processor_command(clean_name)
        payload = {
            "vm_name": clean_name,
            "set_memory_command": mem_cmd,
            "set_processor_command": proc_cmd,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("Set-VMMemory command:")
            print("  " + " ".join(mem_cmd))
            print("Set-VMProcessor command:")
            print("  " + " ".join(proc_cmd))
        return 0

    elif args.action == "profile":
        profile = VMHardwareProfile(vm_name=clean_name)
        if args.json:
            print(json.dumps(profile.to_dict(), indent=2))
        else:
            print(f"Quarantine VM Hardware Profile for '{clean_name}':")
            print(f"  RAM Cap: {profile.ram_cap_bytes} bytes ({profile.ram_cap_bytes / (1024**3):.1f} GB)")
            print(f"  Dynamic Memory: {profile.dynamic_memory_enabled}")
            print(f"  vCPU Count: {profile.vcpu_count}")
            print(f"  CPU Maximum Cap: {profile.cpu_cap_percent}%")
            print(f"  Relative Weight: {profile.cpu_relative_weight}")
        return 0

    elif args.action == "verify":
        audit = audit_full_vm_profile(clean_name)
        if args.json:
            print(json.dumps(audit, indent=2))
        else:
            print(f"Quarantine VM Profile Audit for '{clean_name}':")
            print(f"  Compliant: {audit['compliant']}")
            if audit["all_violations"]:
                print("  Violations:")
                for v in audit["all_violations"]:
                    print(f"    - {v}")
            else:
                print("  No architectural violations detected.")
        return 0 if audit["compliant"] else 1

    elif args.action == "apply":
        res = apply_vm_profile(clean_name, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Profile Application Result: {res.get('status')}")
            if "error" in res:
                print(f"  Error: {res['error']}")
        return 0 if res.get("applied", False) or args.dry_run else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

`
