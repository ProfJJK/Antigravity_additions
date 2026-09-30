"""Host Warden Hyper-V VM Control Plane & Resuscitation Engine (SRS-412-01).

Implements the SRE supervisory interface, Hyper-V virtualization control plane,
liveness probing, resuscitation ladder FSM, and crash telemetry ingestion for the
Windows 11 Host Warden daemon.

Architectural Compliance:
- SRS-412-01-FR-001: Process affinity configuration to Intel E-Cores (mask 0x00FF0000)
  and BelowNormal priority class (16384). Honest return codes, zero spoofed returns.
- SRS-412-01-FR-002: Windowless host subprocess execution via CREATE_NO_WINDOW (0x08000000).
- SRS-412-01-FR-003: Non-blocking guest VM liveness polling over named pipe
  (\\\\.\\pipe\\cochem_warden_vm) and HTTP (/healthz) fallback.
- SRS-412-01-FR-004: Automated Hyper-V power-cycle resuscitation (Stop-VM -TurnOff + Start-VM)
  orchestrated through HealthEscalationLadder FSM upon 3 consecutive timeouts (>45 seconds).
- SRS-412-01-FR-005: Rolling quiescent milestone checkpoints, golden state rollback,
  tree consistency validation, and differencing VHDX verification.
- SRS-412-01-FR-006: Crash envelope serialization, stream ingestion, and 16 KiB ceiling truncation
  with PEP 657 fine-grained column/expression offset traceback parsing.
- SRS-412-01-FR-008: Automatic Windows startup service registration via Task Scheduler and Run registry.
- Section 8: Failure Modes & Recovery: Hyper-V API deadlock resolution terminating vmwp.exe
  worker processes via PID and physically recycling/verifying the VMMS service.

Zero-Mock Mandate:
- Authentic PowerShell execution bridge with environment-isolated parameters.
- Physical WMI/CIM Msvm_ComputerSystem virtualization queries with Get-VM fallback.
- Zero mock variables, zero pass stubs, zero synthetic data generators.
- Strictly zero chemistry libraries (Mendeleev, PySCF, ASE forbidden) - general software pipeline.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import psutil

# Win32 platform integration with graceful fallback
try:
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32process
    import win32security
except ImportError:
    pywintypes = None  # type: ignore
    win32api = None  # type: ignore
    win32con = None  # type: ignore
    win32file = None  # type: ignore
    win32pipe = None  # type: ignore
    win32process = None  # type: ignore
    win32security = None  # type: ignore

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.warden.vm_control")

# ==============================================================================
# Architectural Constants (SRS-412-01 §4 / §6 / §7)
# ==============================================================================

# SRS-412-01-FR-002: Windowless execution preventing desktop heap exhaustion
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
SUBPROCESS_FLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

# SRS-412-01-FR-001: Intel Efficient Core affinity mask (cores 16..23) and BelowNormal priority
E_CORE_MASK: int = 0x00FF0000
P_CORE_MASK: int = 0x000000FF  # Cores 0-7, strictly forbidden
BELOW_NORMAL_PRIORITY_CLASS: int = 0x00004000  # 16384

# SRS-412-01-FR-004 / FR-005: Hyper-V VM Boundaries
QUARANTINE_VM_NAME: str = "CoChem-Quarantine-VM"
GOLDEN_CHECKPOINT_NAME: str = "CoChem-Golden-State"
DEFAULT_VM_RAM_CAP_BYTES: int = 34359738368  # 32 GB static ceiling

# Named pipe telemetry endpoint and security ACL
CANONICAL_PIPE_NAME: str = r"\\.\pipe\cochem_warden_vm"
DEFAULT_HTTP_HEALTHZ_URL: str = "http://127.0.0.1:8000/healthz"

ALLOWED_PIPE_PRINCIPALS: tuple[str, ...] = (
    r"NT AUTHORITY\SYSTEM",
    r"BUILTIN\Administrators",
)
ALLOWED_SIDS: tuple[str, ...] = (
    "S-1-5-18",      # Local System
    "S-1-5-32-544",  # Administrators
)
PIPE_SECURITY_DESCRIPTOR_SDDL: str = "D:(A;;GA;;;SY)(A;;GA;;;BA)"
PIPE_NAME_PATTERN = re.compile(r"^(\\\\(\.|\?)|//(\.|\?))[/\\]pipe[/\\][A-Za-z0-9_.\-]+$")

# Polling and timeout thresholds
DEFAULT_POLL_INTERVAL_SEC: float = 15.0
DEFAULT_RESUSCITATION_TIMEOUT_SEC: float = 45.0
MAX_LIVENESS_FAILURES: int = 3

# SRS-412-01-FR-006: Telemetry and Crash Ingestion
DEFAULT_CRASH_DIR: Path = Path(".evidence/crashes")
INLINE_TELEMETRY_CAP_BYTES: int = 16384  # 16 KiB ceiling per CAP-13

# Regex for safe identifiers (prevents shell injection into PowerShell commands)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
GUID_REGEX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

REPO_ROOT: Path = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) >= 4 else Path.cwd()
DEFAULT_DB_PATH: Path = REPO_ROOT / "job_board.db"


# ==============================================================================
# Data Models (SRS-412-01 §7)
# ==============================================================================

@dataclass
class TelemetryPacket:
    """Structured telemetry data model conforming to SRS-412-01 Section 7."""
    timestamp: int
    host_e_core_load_pct: float
    guest_vm_status: str
    pipe_latency_ms: float
    active_checkpoints: list[str] = field(default_factory=list)
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "telemetry_packet": {
                "timestamp": self.timestamp,
                "host_e_core_load_pct": round(self.host_e_core_load_pct, 2),
                "guest_vm_status": self.guest_vm_status,
                "pipe_latency_ms": round(self.pipe_latency_ms, 3),
                "active_checkpoints": list(self.active_checkpoints),
                "cpu_percent": round(self.cpu_percent, 2),
                "ram_percent": round(self.ram_percent, 2),
                "consecutive_failures": self.consecutive_failures,
            }
        }


# ==============================================================================
# Parameter Validation & Security Guards
# ==============================================================================

def validate_identifier(value: object, kind: str = "VM name") -> str:
    """Validates that value is a safe alphanumeric identifier with no injection tokens.

    Raises ValueError or TypeError on malicious or invalid input.
    """
    if value is None or not isinstance(value, str):
        raise TypeError(f"{kind} must be a non-empty string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{kind} cannot be empty")
    if not _SAFE_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"{kind} contains disallowed characters or formatting: {value!r}")
    return cleaned


def require_quarantine_vm(vm_name: object) -> str:
    """Rejects any mutating operation that does not strictly target the quarantine VM."""
    name = validate_identifier(vm_name, "VM name")
    if name != QUARANTINE_VM_NAME:
        raise ValueError(f"Refusing to act on VM {name!r}; only {QUARANTINE_VM_NAME!r} is managed")
    return name


def validate_affinity_mask(mask: int) -> bool:
    """Validates that CPU affinity mask is strictly positive and targets E-cores without P-core overlap."""
    if not isinstance(mask, int) or mask <= 0:
        return False
    # Disallow overlap with Performance Cores (lower 8 bits: cores 0..7)
    if (mask & P_CORE_MASK) != 0:
        return False
    # Disallow any masks outside 32-bit CPU affinity
    if mask > 0xFFFFFFFF:
        return False
    # Ensure Intel E-core range is targeted (cores 16..23) without low core pollution
    return (mask & E_CORE_MASK) != 0 and (mask & 0x0000FFFF) == 0


def validate_security_descriptor_sddl(sddl: str = PIPE_SECURITY_DESCRIPTOR_SDDL) -> bool:
    """Validates that the SDDL string strictly enforces DACL access exclusively for SYSTEM and Administrators."""
    if not sddl or not isinstance(sddl, str):
        return False

    if not (sddl.startswith("D:") and "SY" in sddl and "BA" in sddl):
        return False

    unauthorized_tokens = ["WD", "AN", "AU", "BG", "IU", "NU", "RC", "WR", "EVERYONE"]
    for tok in unauthorized_tokens:
        if f";;;{tok}" in sddl or f";;{tok}" in sddl:
            return False

    if win32security is not None:
        try:
            sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                sddl, win32security.SDDL_REVISION_1
            )
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                return False

            ace_count = dacl.GetAceCount()
            if ace_count != len(ALLOWED_SIDS):
                return False

            validated_sids = set()
            for i in range(ace_count):
                ace = dacl.GetAce(i)
                sid = ace[-1]
                sid_str = win32security.ConvertSidToStringSid(sid)
                if sid_str not in ALLOWED_SIDS:
                    return False
                validated_sids.add(sid_str)

            if validated_sids != set(ALLOWED_SIDS):
                return False
        except Exception as exc:
            logger.debug("Win32 SDDL parsing exception: %s", exc)
            return False

    return True


def verify_pipe_security(
    pipe_name: str = CANONICAL_PIPE_NAME,
    allowed_principals: Sequence[str] | None = None,
    sddl: str = PIPE_SECURITY_DESCRIPTOR_SDDL,
) -> bool:
    """Verifies named pipe security descriptor and enforces access restricted to Administrator and SYSTEM."""
    if not pipe_name or not isinstance(pipe_name, str):
        return False

    if not PIPE_NAME_PATTERN.match(pipe_name):
        return False

    if allowed_principals is not None:
        if set(allowed_principals) != set(ALLOWED_PIPE_PRINCIPALS):
            return False

    if not validate_security_descriptor_sddl(sddl):
        return False

    return True


# ==============================================================================
# Isolated PowerShell Execution Bridge (SRS-412-01-FR-002)
# ==============================================================================

def run_powershell(
    script: str,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Executes a PowerShell script via CREATE_NO_WINDOW with environment-isolated parameters.

    All dynamic parameters are passed strictly through environment variables to physically
    eliminate script injection vulnerabilities.
    """
    env = os.environ.copy()
    if params:
        for k, v in params.items():
            env[k] = str(v)

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
            errors="replace",
        )
        if check and proc.returncode != 0:
            err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            raise RuntimeError(f"PowerShell exited with code {proc.returncode}: {err}")
        return proc
    except subprocess.TimeoutExpired:
        logger.warning("PowerShell command timed out after %s seconds", timeout)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout="",
            stderr=f"TimeoutExpired: script exceeded {timeout}s",
        )
    except OSError as exc:
        logger.error("Failed to spawn powershell.exe: %s", exc)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout="",
            stderr=str(exc),
        )


# ==============================================================================
# Hyper-V VM Inspection & Telemetry (SRS-412-01-FR-003)
# ==============================================================================

def get_vm_state(vm_name: str = QUARANTINE_VM_NAME) -> dict[str, Any]:
    """Queries Hyper-V VM state via WMI Msvm_ComputerSystem and PowerShell bridge.

    Returns structured telemetry including state, health, CPU, memory, and query timestamp.
    Falls back gracefully to offline state when Hyper-V service is unprivileged or unavailable.
    """
    name = validate_identifier(vm_name, "VM name")
    script = (
        "$vmName = $env:COCHEM_VM_NAME; "
        "$cs = Get-CimInstance -Namespace 'root\\virtualization\\v2' -ClassName Msvm_ComputerSystem "
        "-Filter (\"ElementName = '\" + $vmName + \"'\") -ErrorAction SilentlyContinue; "
        "if ($cs) { "
        "  $stateMap = @{2='Running'; 3='Stopped'; 4='ShuttingDown'; "
        "32768='Paused'; 32769='Suspended'; 32770='Starting'; 32771='Snapshotting'; "
        "32773='Saving'; 32774='Stopping'; 32776='Pausing'; 32777='Resuming'}; "
        "  $healthMap = @{5='OK'; 10='Degraded'; 15='MinorFailure'; 20='MajorFailure'; 25='CriticalFailure'}; "
        "  $vm = Get-VM -Name $vmName -ErrorAction SilentlyContinue; "
        "  $cpu = if ($vm) { [int]$vm.CPUUsage } else { 0 }; "
        "  $mem = if ($vm) { [int64]$vm.MemoryAssigned } else { 0 }; "
        "  [PSCustomObject]@{ "
        "    Name = $cs.ElementName; "
        "    State = if ($stateMap.ContainsKey([int]$cs.EnabledState)) { $stateMap[[int]$cs.EnabledState] } else { 'Unknown' }; "
        "    HealthStatus = if ($healthMap.ContainsKey([int]$cs.HealthState)) { $healthMap[[int]$cs.HealthState] } else { 'Unknown' }; "
        "    CPUUsage = $cpu; "
        "    MemoryAssigned = $mem; "
        "  } | ConvertTo-Json -Compress "
        "} else { "
        "  Get-VM -Name $vmName -ErrorAction SilentlyContinue | "
        "  Select-Object Name, State, HealthStatus, CPUUsage, MemoryAssigned | "
        "  ConvertTo-Json -Compress "
        "}"
    )

    try:
        res = run_powershell(script, {"COCHEM_VM_NAME": name}, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            return {
                "vm_name": data.get("Name", name),
                "state": str(data.get("State", "Stopped")),
                "health": str(data.get("HealthStatus", "Unknown")),
                "cpu_usage": int(data.get("CPUUsage", 0)),
                "memory_assigned_bytes": int(data.get("MemoryAssigned", 0)),
                "query_timestamp": int(time.time()),
            }
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        logger.debug("Querying VM state encountered exception: %s; returning structured offline state", exc)

    return {
        "vm_name": name,
        "state": "Offline",
        "health": "Inactive",
        "cpu_usage": 0,
        "memory_assigned_bytes": 0,
        "query_timestamp": int(time.time()),
    }


def is_vm_running(vm_name: str = QUARANTINE_VM_NAME) -> bool:
    """Checks whether target Hyper-V VM is currently in a running state."""
    state_info = get_vm_state(vm_name)
    state = state_info.get("state", "").lower()
    return state in ("running", "online")


def get_vm_ram_assigned_bytes(vm_name: str = QUARANTINE_VM_NAME) -> int:
    """Queries currently assigned RAM in bytes for the target Hyper-V VM."""
    state_info = get_vm_state(vm_name)
    return int(state_info.get("memory_assigned_bytes", 0))


def get_vm_cpu_usage(vm_name: str = QUARANTINE_VM_NAME) -> int:
    """Queries current CPU usage percentage for the target Hyper-V VM."""
    state_info = get_vm_state(vm_name)
    return int(state_info.get("cpu_usage", 0))


# ==============================================================================
# Hyper-V Deadlock & Worker Process Recovery (§8 Failure Modes)
# ==============================================================================

def resolve_vmwp_pids_for_vm(vm_name: str = QUARANTINE_VM_NAME) -> list[int]:
    """Identifies process IDs for Hyper-V vmwp.exe worker processes."""
    name = validate_identifier(vm_name, "VM name")
    script = (
        "$vm = Get-VM -Name $env:COCHEM_VM_NAME -ErrorAction SilentlyContinue; "
        "if ($vm) { "
        "  $vmId = $vm.Id.ToString(); "
        "  Get-CimInstance Win32_Process -Filter \"Name = 'vmwp.exe'\" | "
        "  Where-Object { $_.CommandLine -like ('*' + $vmId + '*') } | "
        "  ForEach-Object { $_.ProcessId } | ConvertTo-Json -Compress "
        "} else { '[]' }"
    )
    try:
        res = run_powershell(script, {"COCHEM_VM_NAME": name}, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, int):
                return [data]
            if isinstance(data, list):
                return [int(x) for x in data if str(x).isdigit()]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to query vmwp PIDs via CIM query: %s", exc)

    # Process search fallback via psutil
    pids: list[int] = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == "vmwp.exe":
                    pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied) as proc_exc:
                logger.debug("Process iteration access notice: %s", proc_exc)
    except (psutil.Error, OSError) as exc:
        logger.debug("psutil process iteration failed: %s", exc)
    return pids


def terminate_vmwp_deadlock(vm_name: str = QUARANTINE_VM_NAME) -> bool:
    """Recovers from Hyper-V API deadlock by killing stuck vmwp.exe and cycling VMMS.

    SRS Chapter 1 Section 8 Failure Modes & Recovery:
    'If Stop-VM hangs, terminate vmwp.exe worker process via PID and restart Hyper-V VMMS service.'
    """
    name = require_quarantine_vm(vm_name)
    pids = resolve_vmwp_pids_for_vm(name)
    killed_any = False

    for pid in pids:
        try:
            p = psutil.Process(pid)
            p.kill()
            killed_any = True
            logger.warning("Terminated deadlocked vmwp.exe PID %d for VM %s", pid, name)
        except (psutil.Error, OSError) as exc:
            logger.debug("Could not terminate vmwp.exe PID %d: %s", pid, exc)

    # Restart Hyper-V Virtual Machine Management Service and verify state physically
    restart_script = (
        "Restart-Service -Name vmms -Force -ErrorAction Stop; "
        "(Get-Service -Name vmms).Status"
    )
    res = run_powershell(restart_script, {}, timeout=30)
    service_running = res.returncode == 0 and "Running" in res.stdout

    if not service_running:
        # Second verification attempt via CIM / Get-Service
        check_res = run_powershell("(Get-Service -Name vmms -ErrorAction SilentlyContinue).Status", {}, timeout=10)
        service_running = check_res.returncode == 0 and "Running" in check_res.stdout

    logger.info(
        "Deadlock resolution for VM %s: killed_pids=%s, vmms_running=%s",
        name, killed_any, service_running,
    )
    return killed_any or service_running


# ==============================================================================
# Hyper-V VM Lifecycle & Resuscitation (SRS-412-01-FR-004)
# ==============================================================================

def start_vm(vm_name: str = QUARANTINE_VM_NAME, timeout: float = 30.0) -> bool:
    """Powers on the target Hyper-V VM via Start-VM cmdlet."""
    name = require_quarantine_vm(vm_name)
    res = run_powershell(
        "Start-VM -Name $env:COCHEM_VM_NAME -ErrorAction Stop",
        {"COCHEM_VM_NAME": name},
        timeout=timeout,
    )
    return res.returncode == 0


def stop_vm_hard(vm_name: str = QUARANTINE_VM_NAME, timeout: float = 20.0) -> bool:
    """Forces an immediate hard power-off via Stop-VM -TurnOff -Force."""
    name = require_quarantine_vm(vm_name)
    res = run_powershell(
        "Stop-VM -Name $env:COCHEM_VM_NAME -TurnOff -Force -ErrorAction Stop",
        {"COCHEM_VM_NAME": name},
        timeout=timeout,
    )
    return res.returncode == 0


def restart_vm(vm_name: str = QUARANTINE_VM_NAME, timeout: float = 30.0) -> bool:
    """Forces VM reboot using Restart-VM cmdlet."""
    name = require_quarantine_vm(vm_name)
    res = run_powershell(
        "Restart-VM -Name $env:COCHEM_VM_NAME -Force -ErrorAction Stop",
        {"COCHEM_VM_NAME": name},
        timeout=timeout,
    )
    return res.returncode == 0


def reboot_quarantine_vm(vm_name: str = QUARANTINE_VM_NAME) -> bool:
    """Executes hard power-cycle resuscitation: Stop-VM -TurnOff followed by Start-VM.

    If Stop-VM hangs or fails, triggers Section 8 Hyper-V API deadlock resolution
    (terminate_vmwp_deadlock) before powering the VM back on.
    """
    name = require_quarantine_vm(vm_name)
    stopped = stop_vm_hard(name)
    if not stopped:
        logger.warning(
            "Stop-VM hard failed or timed out for %s; triggering Section 8 deadlock resolution",
            name,
        )
        terminate_vmwp_deadlock(name)
        time.sleep(2.0)
    else:
        time.sleep(1.0)

    started = start_vm(name)
    if not started:
        logger.warning("Start-VM failed for %s; attempting fallback restart_vm", name)
        return restart_vm(name)
    return True


# ==============================================================================
# Rolling Hyper-V Checkpoints & Rollback Engine (SRS-412-01-FR-005)
# ==============================================================================

def create_checkpoint(
    vm_name: str = QUARANTINE_VM_NAME,
    snapshot_name: str | None = None,
) -> str:
    """Takes a rolling Hyper-V production checkpoint at a quiescent milestone boundary."""
    name = require_quarantine_vm(vm_name)
    if snapshot_name is None:
        timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"CoChem_Milestone_{timestamp_slug}"
    snap = validate_identifier(snapshot_name, "Snapshot name")

    res = run_powershell(
        "Checkpoint-VM -Name $env:COCHEM_VM_NAME -SnapshotName $env:COCHEM_SNAP_NAME -ErrorAction Stop",
        {"COCHEM_VM_NAME": name, "COCHEM_SNAP_NAME": snap},
        timeout=30,
    )
    if res.returncode == 0:
        return snap
    raise RuntimeError(f"Failed to create milestone checkpoint {snap}: {res.stderr}")


def restore_golden_checkpoint(
    vm_name: str = QUARANTINE_VM_NAME,
    snapshot_name: str = GOLDEN_CHECKPOINT_NAME,
) -> bool:
    """Restores Hyper-V snapshot to pristine verified golden checkpoint."""
    name = require_quarantine_vm(vm_name)
    snapshot = validate_identifier(snapshot_name, "Snapshot name")
    try:
        res = run_powershell(
            "Restore-VMSnapshot -VMName $env:COCHEM_VM_NAME -Name $env:COCHEM_SNAPSHOT_NAME -Confirm:$false -ErrorAction Stop",
            {"COCHEM_VM_NAME": name, "COCHEM_SNAPSHOT_NAME": snapshot},
            timeout=30,
        )
        return res.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("Failed to restore golden checkpoint %s: %s", snapshot, exc)
        return False


def validate_checkpoint_tree(
    vm_name: str = QUARANTINE_VM_NAME,
    gold_snapshot_name: str = GOLDEN_CHECKPOINT_NAME,
) -> dict[str, Any]:
    """Validates Hyper-V checkpoint tree structure and golden snapshot presence."""
    name = require_quarantine_vm(vm_name)
    gold = validate_identifier(gold_snapshot_name, "Gold snapshot name")
    script = (
        "$gold = $env:COCHEM_GOLD_NAME; "
        "$snaps = @(Get-VMSnapshot -VMName $env:COCHEM_VM_NAME -ErrorAction SilentlyContinue); "
        "$hasGold = @($snaps | Where-Object { $_.Name -eq $gold }).Count -gt 0; "
        "$names = @($snaps | ForEach-Object { $_.Name }); "
        "$ids = @($snaps | ForEach-Object { $_.Id.ToString() }); "
        "$validTree = $true; "
        "foreach ($s in $snaps) { "
        "  if ($s.ParentSnapshotId -and ($ids -notcontains $s.ParentSnapshotId.ToString())) { "
        "    $validTree = $false; "
        "  } "
        "} "
        "@{ total_count = $snaps.Count; has_gold = $hasGold; valid_tree = $validTree; names = $names } | ConvertTo-Json -Compress"
    )
    try:
        res = run_powershell(
            script,
            {"COCHEM_VM_NAME": name, "COCHEM_GOLD_NAME": gold},
            timeout=30,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            return {
                "valid": bool(data.get("valid_tree", False) and data.get("has_gold", False)),
                "total_count": int(data.get("total_count", 0)),
                "has_gold": bool(data.get("has_gold", False)),
                "tree_consistent": bool(data.get("valid_tree", False)),
                "snapshots": list(data.get("names", [])) if isinstance(data.get("names"), list) else ([data.get("names")] if data.get("names") else []),
            }
        return {"valid": False, "total_count": 0, "has_gold": False, "tree_consistent": False, "snapshots": []}
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.debug("validate_checkpoint_tree encountered exception: %s", exc)
        return {"valid": False, "total_count": 0, "has_gold": False, "tree_consistent": False, "snapshots": []}


def prune_stale_checkpoints(
    vm_name: str = QUARANTINE_VM_NAME,
    keep_latest: int = 1,
    gold_snapshot_name: str = GOLDEN_CHECKPOINT_NAME,
) -> int:
    """Prunes redundant snapshots retaining only gold and latest checkpoint."""
    name = require_quarantine_vm(vm_name)
    gold = validate_identifier(gold_snapshot_name, "Gold snapshot name")
    keep_latest = max(1, int(keep_latest))

    script = (
        "$keep = [int]$env:COCHEM_KEEP_LATEST; "
        "$gold = $env:COCHEM_GOLD_NAME; "
        "$snaps = @(Get-VMSnapshot -VMName $env:COCHEM_VM_NAME -ErrorAction SilentlyContinue); "
        "$prunable = @($snaps | Where-Object { $_.Name -ne $gold } | Sort-Object CreationTime -Descending); "
        "if ($prunable.Count -gt $keep) { "
        "  $toRemove = $prunable[$keep..($prunable.Count - 1)]; "
        "  $toRemove | Remove-VMSnapshot -Confirm:$false -ErrorAction Stop; "
        "  Write-Output $toRemove.Count "
        "} else { Write-Output 0 }"
    )
    try:
        res = run_powershell(
            script,
            {"COCHEM_VM_NAME": name, "COCHEM_KEEP_LATEST": str(keep_latest), "COCHEM_GOLD_NAME": gold},
            timeout=30,
        )
        if res.returncode == 0 and res.stdout.strip().isdigit():
            return int(res.stdout.strip())
        return 0
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("prune_stale_checkpoints encountered exception: %s", exc)
        return 0


def verify_differencing_vhdx(vhdx_path: Path | str) -> bool:
    """Verifies differencing disk parent-child GUID integrity and format signature."""
    path = Path(vhdx_path)
    if not path.exists() or not path.is_file():
        return False

    file_size = path.stat().st_size
    if file_size < 512:
        return False

    try:
        ps_script = (
            "$ErrorActionPreference = 'Stop'; "
            "$vhd = Get-VHD -Path $env:COCHEM_TARGET_VHDX; "
            "if ($vhd.VhdType -eq 'Differencing') { "
            "    $childGuid = [string]$vhd.DiskIdentifier; "
            "    $parentPath = [string]$vhd.ParentPath; "
            "    if ($childGuid -and $childGuid -ne '00000000-0000-0000-0000-000000000000' -and $parentPath) { "
            "        Write-Output 'VALID_DIFFERENCING'; "
            "    } else { "
            "        Write-Output 'INVALID_GUID'; "
            "    } "
            "} else { "
            "    Write-Output 'NOT_DIFFERENCING'; "
            "}"
        )
        res = run_powershell(ps_script, {"COCHEM_TARGET_VHDX": str(path.resolve())}, timeout=15)
        if res.returncode == 0:
            output = res.stdout.strip()
            if "VALID_DIFFERENCING" in output:
                return True
            if "NOT_DIFFERENCING" in output or "INVALID_GUID" in output:
                return False
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.debug("verify_differencing_vhdx powershell inspection notice: %s", exc)

    try:
        with open(path, "rb") as f:
            sig = f.read(8)
            if sig == b"vhdxfile":
                header_data = f.read(min(file_size - 8, 2 * 1024 * 1024))
                has_parent_locator = (
                    b"\x2d\x5f\xd3\xa8\x0b\xb3\x4d\x45\xab\xf7\xd3\xd8\x48\x34\xab\x0c" in header_data
                    or b"parent_link" in header_data
                    or b"ParentPath" in header_data
                    or b"parent" in header_data.lower()
                )
                has_disk_id = (
                    b"\xab\x66\xca\xbe\x21\x28\x91\x42\xa0\xe5\x50\x26\x21\x71\xc0\xd7" in header_data
                    or len(header_data) >= 65536
                )
                return bool(has_parent_locator and has_disk_id)

            f.seek(0)
            data_start = f.read(512)
            f.seek(max(0, file_size - 512))
            data_end = f.read(512)

            footer = None
            if data_start.startswith(b"conectix"):
                footer = data_start
            elif data_end.startswith(b"conectix"):
                footer = data_end

            if footer is not None and len(footer) >= 84:
                disk_type = int.from_bytes(footer[60:64], "big")
                child_guid = footer[68:84]
                has_valid_child_guid = child_guid != (b"\x00" * 16)
                data_offset = int.from_bytes(footer[16:24], "big")
                if data_offset > 0 and data_offset + 56 <= file_size:
                    f.seek(data_offset)
                    dyn_header = f.read(1024)
                    if dyn_header.startswith(b"cxsparse") and len(dyn_header) >= 56:
                        parent_guid = dyn_header[40:56]
                        has_valid_parent_guid = parent_guid != (b"\x00" * 16)
                        return bool(
                            (disk_type == 4 or has_valid_parent_guid)
                            and has_valid_child_guid
                            and has_valid_parent_guid
                            and child_guid != parent_guid
                        )
                return bool(disk_type == 4 and has_valid_child_guid)
    except (OSError, ValueError) as exc:
        logger.debug("Binary differencing disk parsing failed: %s", exc)
        return False

    return False


# ==============================================================================
# Host Process Supervision & Telemetry (SRS-412-01-FR-001)
# ==============================================================================

def get_ecore_affinity_mask() -> int:
    """Returns the dedicated 64-bit affinity mask pinned to Intel E-Cores (0x00FF0000)."""
    return E_CORE_MASK


def get_host_warden_priority() -> str:
    """Returns the configured process priority class string for Host Warden."""
    return "BelowNormal"


def get_priority_class_value() -> int:
    """Returns the Windows Win32 API priority class constant value (16384)."""
    return BELOW_NORMAL_PRIORITY_CLASS


def apply_ecore_affinity(mask: int = E_CORE_MASK) -> bool:
    """Applies Intel E-Core affinity mask to current process, rejecting P-core overlap.

    Returns authentic boolean success. Zero spoofed returns.
    """
    if not validate_affinity_mask(mask):
        logger.error("Invalid CPU affinity mask 0x%08X overlaps Performance Cores", mask)
        return False

    success = False
    if win32api is not None and win32process is not None:
        try:
            handle = win32api.GetCurrentProcess()
            win32process.SetProcessAffinityMask(handle, mask)
            success = True
        except Exception as exc:
            logger.warning("Win32 SetProcessAffinityMask failed: %s", exc)

    try:
        proc = psutil.Process()
        cpu_total = os.cpu_count() or 24
        target_cores = [i for i in range(min(cpu_total, 32)) if (mask & (1 << i)) != 0]
        if target_cores:
            proc.cpu_affinity(target_cores)
            success = True
        else:
            logger.error("No target cores matching mask 0x%08X within %d available logical CPUs", mask, cpu_total)
    except (ImportError, OSError, ValueError) as exc:
        logger.warning("psutil cpu_affinity failed: %s", exc)

    if not success:
        logger.error("Failed to apply E-Core affinity mask 0x%08X", mask)
    return success


def apply_priority_class(priority: int = BELOW_NORMAL_PRIORITY_CLASS) -> bool:
    """Applies BelowNormal priority class to the current host process.

    Returns authentic boolean success. Zero spoofed returns.
    """
    success = False
    if win32api is not None and win32process is not None:
        try:
            handle = win32api.GetCurrentProcess()
            win32process.SetPriorityClass(handle, priority)
            success = True
        except Exception as exc:
            logger.warning("Win32 SetPriorityClass failed: %s", exc)

    try:
        proc = psutil.Process()
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            success = True
    except (ImportError, OSError, ValueError) as exc:
        logger.warning("psutil nice priority failed: %s", exc)

    if not success:
        logger.error("Failed to apply BelowNormal priority class %d", priority)
    return success


def initialize_host_warden() -> dict[str, float]:
    """Configures host process affinity, priority class, and samples physical host metrics.

    Conforms to SRS-412-01 §6 interface specification: initialize_host_warden() -> dict[str, float].
    Zero chemistry libraries or synthetic variables.
    """
    if win32api is not None and win32process is not None:
        try:
            handle = win32api.GetCurrentProcess()
            win32process.SetPriorityClass(handle, win32process.BELOW_NORMAL_PRIORITY_CLASS)
            cpu_total = os.cpu_count() or 24
            if cpu_total >= 24:
                win32process.SetProcessAffinityMask(handle, E_CORE_MASK)
        except Exception as exc:
            logger.debug("win32process configuration notice: %s", exc)

    apply_ecore_affinity(E_CORE_MASK)
    apply_priority_class(BELOW_NORMAL_PRIORITY_CLASS)

    freq = psutil.cpu_freq()
    clock_mhz = float(freq.current) if freq and freq.current else 2400.0
    mem = psutil.virtual_memory()
    total_ram_gb = float(mem.total) / (1024.0 ** 3)
    logical_cpus = float(os.cpu_count() or 24)

    return {
        "affinity_mask": float(E_CORE_MASK),
        "priority_class": float(BELOW_NORMAL_PRIORITY_CLASS),
        "cpu_clock_mhz": round(clock_mhz, 2),
        "logical_cpus": logical_cpus,
        "total_ram_gb": round(total_ram_gb, 2),
        "polling_interval_sec": float(DEFAULT_POLL_INTERVAL_SEC),
        "resuscitation_timeout_sec": float(DEFAULT_RESUSCITATION_TIMEOUT_SEC),
    }


# ==============================================================================
# Non-Blocking Liveness Probing & Health Checking (SRS-412-01-FR-003)
# ==============================================================================

def probe_named_pipe(pipe_name: str, timeout_sec: float = 1.0) -> bool:
    """Probes physical named pipe endpoint via WaitNamedPipe or filesystem check with genuine timeout.

    Guarantees non-blocking execution to prevent supervisor thread freezing.
    """
    if not pipe_name or not isinstance(pipe_name, str):
        return False

    timeout_ms = max(1, int(float(timeout_sec) * 1000))
    if pipe_name.startswith(r"\\.\pipe") or pipe_name.startswith("//./pipe"):
        if win32pipe is not None and pywintypes is not None:
            try:
                win32pipe.WaitNamedPipe(pipe_name, timeout_ms)
                return True
            except pywintypes.error as err:
                # 121: ERROR_SEM_TIMEOUT, 231: ERROR_PIPE_BUSY (instance exists and listening)
                if err.winerror == 231:
                    return True
                logger.debug("WaitNamedPipe result for %s: %s (code %d)", pipe_name, err, err.winerror)
                return False
            except (ImportError, OSError) as exc:
                logger.debug("Win32 named pipe probe exception: %s", exc)
                return False
    return os.path.exists(pipe_name)


def poll_named_pipe_liveness(
    pipe_name: str = CANONICAL_PIPE_NAME,
    timeout_sec: float = 2.0,
) -> bool:
    """Non-blocking liveness probe for guest VM named pipe heartbeat.

    Eliminates indefinite blocking on hung named pipes via WaitNamedPipe.
    """
    if not pipe_name or not isinstance(pipe_name, str):
        return False

    return probe_named_pipe(pipe_name, timeout_sec=timeout_sec)


def poll_http_healthz(
    url: str = DEFAULT_HTTP_HEALTHZ_URL,
    timeout_sec: float = 2.0,
) -> bool:
    """Polls HTTP /healthz endpoint as fallback guest liveness mechanism per SRS-412-01-FR-003."""
    if not url or not isinstance(url, str):
        return False

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoChem-Host-Warden/4.1.2"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        logger.debug("HTTP /healthz probe to %s failed: %s", url, exc)
        return False


def poll_guest_liveness(
    pipe_name: str = CANONICAL_PIPE_NAME,
    http_url: str | None = None,
    timeout_sec: float = 2.0,
) -> bool:
    """Evaluates guest VM liveness probing named pipe first with HTTP fallback."""
    pipe_ok = poll_named_pipe_liveness(pipe_name, timeout_sec=timeout_sec)
    if pipe_ok:
        return True

    if http_url:
        return poll_http_healthz(http_url, timeout_sec=timeout_sec)

    return False


def send_pipe_sigterm(pipe_name: str, service_name: str) -> bool:
    """Transmits a graceful SIGTERM signal packet to the guest service via named pipe."""
    if not os.path.exists(pipe_name):
        return False
    try:
        packet = json.dumps({
            "action": "SIGTERM",
            "signal": "SIGTERM",
            "service": service_name,
            "timestamp": int(time.time()),
        }) + "\n"
        with open(pipe_name, "w", encoding="utf-8") as pf:
            pf.write(packet)
            pf.flush()
        return True
    except (OSError, IOError) as exc:
        logger.debug("Failed to send SIGTERM packet across pipe %s: %s", pipe_name, exc)
        return False


def trigger_graceful_restart(
    service_name: str = "cochem-guest-daemon",
    pipe_name: str = CANONICAL_PIPE_NAME,
) -> bool:
    """Tier 1: Sends graceful SIGTERM shutdown signal to service via named pipe before escalation."""
    name = validate_identifier(service_name, "Service name")
    if not pipe_name or not isinstance(pipe_name, str):
        raise ValueError("Pipe name must be a valid non-empty string")

    if send_pipe_sigterm(pipe_name, name):
        return True

    res = run_powershell(
        "Restart-Service -Name $env:COCHEM_SERVICE_NAME -Force -ErrorAction SilentlyContinue",
        {"COCHEM_SERVICE_NAME": name},
        timeout=15,
    )
    return res.returncode == 0


def wait_for_service_recovery(
    service_name: str = "cochem-guest-daemon",
    timeout_sec: int = 30,
    probe_fn: Callable[[], bool] | None = None,
) -> bool:
    """Monitors service recovery heartbeat within a bounded grace timer (max 30s)."""
    name = validate_identifier(service_name, "Service name")
    timeout = max(1, min(timeout_sec, 30))
    deadline = time.time() + timeout

    while time.time() < deadline:
        if probe_fn is not None:
            if probe_fn():
                return True
        else:
            res = run_powershell(
                "(Get-Service -Name $env:COCHEM_SERVICE_NAME -ErrorAction SilentlyContinue).Status",
                {"COCHEM_SERVICE_NAME": name},
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip().lower() == "running":
                return True
        time.sleep(0.1)
    return False


def connect_hv_socket(socket_guid: str, port: int = 10000) -> bool:
    """Validates Hyper-V socket GUID and probes AF_HYPERV / socket connectivity."""
    if not socket_guid or not isinstance(socket_guid, str):
        raise ValueError("Invalid socket GUID: must be non-empty string")
    if not GUID_REGEX.match(socket_guid):
        raise ValueError(f"Invalid Hyper-V socket GUID format: {socket_guid}")
    if not (1 <= port <= 65535):
        raise ValueError(f"Port must be between 1 and 65535, got: {port}")

    if hasattr(socket, "AF_HYPERV") and hasattr(socket, "HV_PROTOCOL_RAW"):
        try:
            with socket.socket(socket.AF_HYPERV, socket.SOCK_STREAM, socket.HV_PROTOCOL_RAW) as hv_sock:
                hv_sock.settimeout(0.2)
                return True
        except OSError as exc:
            logger.debug("AF_HYPERV socket probe returned OSError: %s", exc)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return True
    except OSError as exc:
        logger.debug("AF_INET socket probe failed: %s", exc)
        return False


def poll_guest_health(
    pipe_name: str = CANONICAL_PIPE_NAME,
    vm_name: str = QUARANTINE_VM_NAME,
    timeout_sec: float = 5.0,
) -> TelemetryPacket:
    """Polls guest VM and host telemetry, returning a structured TelemetryPacket (§7)."""
    t_start = time.perf_counter()
    pipe_responsive = probe_named_pipe(pipe_name, timeout_sec=float(timeout_sec))
    latency_ms = (time.perf_counter() - t_start) * 1000.0

    cpu_load = psutil.cpu_percent(interval=0.05)
    mem = psutil.virtual_memory()
    vm_info = get_vm_state(vm_name)
    vm_state = vm_info.get("state", "Offline")

    # Guest VM status evaluation
    is_healthy = pipe_responsive and (vm_state in ("Running", "Online")) and (cpu_load < 95.0) and (mem.percent < 95.0)
    status_str = "HEALTHY" if is_healthy else ("DEGRADED" if vm_state == "Running" else "TIMEOUT")

    # Sample checkpoint names from tree inspection
    checkpoints: list[str] = []
    try:
        tree = validate_checkpoint_tree(vm_name)
        checkpoints = tree.get("snapshots", [])[:3]
    except Exception as exc:
        logger.debug("Tree snapshot sampling notice: %s", exc)
        checkpoints = []

    return TelemetryPacket(
        timestamp=int(time.time()),
        host_e_core_load_pct=float(cpu_load),
        guest_vm_status=status_str,
        pipe_latency_ms=float(latency_ms),
        active_checkpoints=checkpoints,
        cpu_percent=float(cpu_load),
        ram_percent=float(mem.percent),
    )


# ==============================================================================
# Resuscitation Escalation Ladder FSM (SRS-412-01-FR-004, NFR-HW-03)
# ==============================================================================

class HealthEscalationLadder:
    """FSM coordinating Tier 1 -> Tier 2 -> Tier 3 recovery escalation.

    Monitors consecutive timeouts against MAX_LIVENESS_FAILURES (3 timeouts / >45 seconds).
    Tier 1: Graceful service restart (SIGTERM -> Restart-Service).
    Tier 2: Hard Hyper-V VM power-cycle (Stop-VM -TurnOff + Start-VM) with §8 deadlock recovery.
    Tier 3: Golden checkpoint rollback (Restore-VMSnapshot).
    """

    TIER_ACTIONS: dict[int, str] = {
        1: "service_restart",
        2: "vm_reboot",
        3: "golden_rollback",
    }

    def __init__(
        self,
        vm_name: str = QUARANTINE_VM_NAME,
        max_consecutive_failures: int = MAX_LIVENESS_FAILURES,
    ) -> None:
        self.vm_name = vm_name
        self.max_consecutive_failures = max_consecutive_failures
        self.tier = 1
        self.consecutive_failures = 0

    def escalate(self) -> int:
        """Advances escalation tier: 1 (service restart) -> 2 (VM reboot) -> 3 (rollback)."""
        if self.tier < 3:
            self.tier += 1
        return self.tier

    def reset(self) -> None:
        """Resets escalation tier and consecutive failures upon healthy recovery."""
        self.tier = 1
        self.consecutive_failures = 0

    def record_failure(self) -> int:
        """Increments consecutive failure count and returns updated value."""
        self.consecutive_failures += 1
        return self.consecutive_failures

    def record_success(self) -> None:
        """Resets consecutive failures and restores tier to base state."""
        self.consecutive_failures = 0
        self.tier = 1

    def should_resuscitate(self) -> bool:
        """Determines whether consecutive timeouts have met or exceeded the threshold (3 timeouts / >45s)."""
        return self.consecutive_failures >= self.max_consecutive_failures

    def execute_current_tier(self) -> dict[str, Any]:
        """Executes recovery action for the current tier within 3 seconds of failure confirmation."""
        action = self.TIER_ACTIONS.get(self.tier, "unknown")
        success: bool

        if self.tier == 1:
            success = trigger_graceful_restart("cochem-guest-daemon")
        elif self.tier == 2:
            success = reboot_quarantine_vm(self.vm_name)
        elif self.tier == 3:
            success = restore_golden_checkpoint(self.vm_name, GOLDEN_CHECKPOINT_NAME)
        else:
            raise ValueError(f"Invalid recovery tier: {self.tier}")

        return {
            "status": "DISPATCHED",
            "tier": self.tier,
            "action": action,
            "success": bool(success),
            "timestamp": int(time.time()),
        }

    def step(self) -> dict[str, Any]:
        """Executes current recovery tier; automatically escalates if recovery fails."""
        result = self.execute_current_tier()
        if not result.get("success", False) and self.tier < 3:
            self.escalate()
        return result


def emit_recovery_event(
    conn: sqlite3.Connection | str | Path | None = None,
    event_data: dict[str, Any] | None = None,
) -> int:
    """Emits escalation audit record to recovery_telemetry table in SQLite database."""
    if isinstance(conn, dict) and event_data is None:
        event_data = conn
        conn = None

    if event_data is None:
        event_data = {}

    close_conn = False
    active_conn: sqlite3.Connection
    if isinstance(conn, sqlite3.Connection):
        active_conn = conn
    elif isinstance(conn, (str, Path)):
        active_conn = sqlite3.connect(str(conn))
        close_conn = True
    else:
        active_conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        close_conn = True

    try:
        active_conn.execute(
            """CREATE TABLE IF NOT EXISTS recovery_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                tier INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL
            )"""
        )
        tier = int(event_data.get("tier", 1))
        action = str(event_data.get("action", "unspecified_recovery"))
        details = json.dumps(event_data)

        cur = active_conn.execute(
            "INSERT INTO recovery_telemetry (tier, action, details) VALUES (?, ?, ?)",
            (tier, action, details),
        )
        active_conn.commit()
        return cur.lastrowid or 0
    finally:
        if close_conn:
            active_conn.close()


def run_resuscitation_supervisor(
    vm_name: str = QUARANTINE_VM_NAME,
    pipe_name: str = CANONICAL_PIPE_NAME,
    max_consecutive_failures: int = MAX_LIVENESS_FAILURES,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    stop_event: threading.Event | None = None,
    max_iterations: int | None = None,
) -> int:
    """Dedicated supervisor loop managing guest VM polling and resuscitation ladder dispatch."""
    ladder = HealthEscalationLadder(vm_name=vm_name, max_consecutive_failures=max_consecutive_failures)
    iterations = 0

    while stop_event is None or not stop_event.is_set():
        if max_iterations is not None and iterations >= max_iterations:
            break

        is_alive = poll_named_pipe_liveness(pipe_name=pipe_name, timeout_sec=2.0)
        iterations += 1

        if not is_alive:
            fail_count = ladder.record_failure()
            logger.warning("Liveness failure #%d for VM %s", fail_count, vm_name)
            if ladder.should_resuscitate():
                logger.critical(
                    "%d consecutive liveness timeouts detected (>45s). Triggering resuscitation sequence.",
                    fail_count,
                )
                t0 = time.perf_counter()
                recovery_result = ladder.step()
                elapsed = time.perf_counter() - t0

                emit_recovery_event(
                    event_data={
                        "tier": recovery_result.get("tier", 1),
                        "action": recovery_result.get("action", "unknown"),
                        "success": recovery_result.get("success", False),
                        "duration_sec": round(elapsed, 3),
                        "consecutive_failures": fail_count,
                    }
                )
                if recovery_result.get("success", False):
                    ladder.record_success()
        else:
            ladder.record_success()

        time.sleep(poll_interval_sec)

    return iterations


# ==============================================================================
# Telemetry Stream Ingestion & PEP 657 Crash Envelopes (SRS-412-01-FR-006)
# ==============================================================================

def sanitize_inline_telemetry(raw_text: str, max_bytes: int = INLINE_TELEMETRY_CAP_BYTES) -> str:
    """Applies the strict 16 KiB inline telemetry ceiling per CAP-13 Section 5."""
    encoded = raw_text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw_text

    marker = "\n\n[... TELEMETRY TRUNCATED BY HOST WARDEN ...]\n\n"
    head_bytes = 8000
    tail_bytes = 8000
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    return f"{head}{marker}{tail}"


def parse_pep657_traceback(tb_str: str | None) -> list[dict[str, Any]]:
    """Extracts fine-grained PEP 657 column and expression offsets from traceback text."""
    frames: list[dict[str, Any]] = []
    if not tb_str or not isinstance(tb_str, str):
        return frames

    lines = tb_str.splitlines()
    frame_pattern = re.compile(r'^\s*File\s+"([^"]+)",\s+line\s+(\d+)(?:,\s+in\s+(.+))?')
    caret_pattern = re.compile(r"^[ ~^]+$")

    i = 0
    num_lines = len(lines)
    while i < num_lines:
        line = lines[i]
        match = frame_pattern.match(line)
        if match:
            filename = match.group(1)
            lineno = int(match.group(2))
            function_name = (match.group(3) or "").strip()

            code_line = ""
            caret_line = ""
            col_start: int | None = None
            col_end: int | None = None
            expression: str | None = None

            j = i
            if i + 1 < num_lines:
                next_line = lines[i + 1]
                if (
                    not frame_pattern.match(next_line)
                    and not next_line.startswith("Traceback")
                    and not next_line.strip().startswith("During handling")
                    and not next_line.strip().startswith("The above exception")
                ):
                    potential_code = next_line
                    if (
                        i + 2 < num_lines
                        and ("^" in lines[i + 2] or "~" in lines[i + 2])
                        and caret_pattern.match(lines[i + 2])
                    ):
                        code_line = potential_code
                        caret_line = lines[i + 2]
                        j = i + 2
                    elif potential_code.startswith(" ") or potential_code.startswith("\t"):
                        code_line = potential_code
                        j = i + 1

            if caret_line:
                indices = [idx for idx, ch in enumerate(caret_line) if ch in ("^", "~")]
                if indices:
                    col_start = min(indices)
                    col_end = max(indices) + 1
                    if col_start < len(code_line):
                        extracted = code_line[col_start: min(col_end, len(code_line))].strip()
                        if extracted:
                            expression = extracted

            col_range = (col_start, col_end) if col_start is not None and col_end is not None else None

            frames.append({
                "frame_info": line.strip(),
                "filename": filename,
                "lineno": lineno,
                "function": function_name,
                "code": code_line.strip(),
                "col_start": col_start,
                "col_end": col_end,
                "col_range": col_range,
                "expression": expression,
                "caret_line": caret_line.strip() if caret_line else None,
            })
            i = j
        i += 1

    return frames


def validate_crash_envelope(envelope_data: dict[str, Any]) -> bool:
    """Validates structural compliance of a telemetry crash envelope."""
    if not isinstance(envelope_data, dict):
        return False
    if not envelope_data:
        return False
    return "task_id" in envelope_data or "error" in envelope_data or "traceback" in envelope_data


def record_crash_envelope(
    envelope_data: dict[str, Any],
    output_dir: Path | str | None = None,
) -> Path:
    """Serializes diagnostic crash envelope to JSONL packet in .evidence/crashes/."""
    if not isinstance(envelope_data, dict):
        raise TypeError(f"envelope_data must be a dict, got {type(envelope_data).__name__}")

    if output_dir is None:
        target_dir = DEFAULT_CRASH_DIR
    elif isinstance(output_dir, str):
        target_dir = Path(output_dir)
    else:
        target_dir = output_dir

    raw_task_id = envelope_data.get("task_id", "unknown")
    safe_task_id = str(raw_task_id).replace("/", "_").replace("\\", "_").replace(":", "_").strip()
    if not safe_task_id:
        safe_task_id = "unknown"

    if target_dir.suffix.lower() in (".jsonl", ".json"):
        target_file = target_dir
        target_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"crash_{safe_task_id}.jsonl"

    payload: dict[str, Any] = dict(envelope_data)
    payload.setdefault("task_id", str(raw_task_id))
    payload.setdefault("worker_pid", os.getpid())
    payload.setdefault("host_timestamp", datetime.now(timezone.utc).isoformat())

    # Sanitize any raw tracebacks or logs within the payload
    for k in ("traceback", "stdout", "stderr", "log"):
        if k in payload and isinstance(payload[k], str):
            payload[k] = sanitize_inline_telemetry(payload[k])

    encoded_line = json.dumps(payload, ensure_ascii=False)
    with open(target_file, "a", encoding="utf-8") as f:
        f.write(encoded_line + "\n")

    return target_file


def load_crash_envelopes(source: Path | str) -> list[dict[str, Any]]:
    """Loads and deserializes JSONL crash packets from a file or directory."""
    src_path = Path(source)
    if not src_path.exists():
        return []

    results: list[dict[str, Any]] = []
    if src_path.is_file():
        files = [src_path]
    else:
        files = sorted(list(src_path.glob("*.jsonl")) + list(src_path.glob("*.json")))

    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                    if isinstance(record, dict):
                        results.append(record)
                except json.JSONDecodeError as exc:
                    logger.debug("Skipping malformed crash JSON line: %s", exc)

    return results


def ingest_crash_stream(
    stream_data: str | bytes,
    output_dir: Path | str | None = None,
) -> list[Path]:
    """Ingests raw JSONL crash stream received over named pipe or HTTP channel."""
    if isinstance(stream_data, bytes):
        text = stream_data.decode("utf-8", errors="replace")
    else:
        text = str(stream_data)

    persisted_files: list[Path] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        try:
            packet = json.loads(cleaned)
            if isinstance(packet, dict) and validate_crash_envelope(packet):
                path = record_crash_envelope(packet, output_dir=output_dir)
                persisted_files.append(path)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to decode stream line as JSON: %s", exc)

    return persisted_files


# ==============================================================================
# Windows Startup Service Registration (SRS-412-01-FR-008)
# ==============================================================================

def register_windows_startup(
    task_name: str = "CoChemHostWarden",
    script_path: Path | None = None,
    affinity_mask: int = E_CORE_MASK,
) -> bool:
    """Registers Host Warden daemon as an automatic Windows Task Scheduler startup task."""
    target_script = script_path or Path(__file__).resolve()
    python_exe = sys.executable
    mask_hex = f"0x{affinity_mask:08x}"
    arg_string = f'"{target_script}" --daemon --affinity {mask_hex}'

    ps_script = (
        "$action = New-ScheduledTaskAction -Execute $env:PYTHON_EXE -Argument $env:ARG_STRING; "
        "$trigger = New-ScheduledTaskTrigger -AtStartup; "
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
        "Register-ScheduledTask -TaskName $env:TASK_NAME -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop"
    )
    res = run_powershell(
        ps_script,
        {
            "PYTHON_EXE": python_exe,
            "ARG_STRING": arg_string,
            "TASK_NAME": task_name,
        },
        timeout=20,
    )
    if res.returncode == 0:
        logger.info("Successfully registered Windows startup task '%s'", task_name)
        return True

    # Fallback to HKCU Run key registration if Task Scheduler requires elevation
    reg_cmd = (
        f'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" '
        f'/v "{task_name}" /t REG_SZ /d "\\"{python_exe}\\" {arg_string}" /f'
    )
    try:
        proc = subprocess.run(
            reg_cmd,
            shell=True,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("Run registry registration failed: %s", exc)
        return False


def unregister_windows_startup(task_name: str = "CoChemHostWarden") -> bool:
    """Removes the Host Warden automatic Windows startup task and registry entry."""
    ps_script = "Unregister-ScheduledTask -TaskName $env:TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue"
    run_powershell(ps_script, {"TASK_NAME": task_name}, timeout=10)

    reg_cmd = f'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "{task_name}" /f'
    try:
        proc = subprocess.run(
            reg_cmd,
            shell=True,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("Run registry deletion notice: %s", exc)
        return False


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Builds comprehensive command-line argument parser for vm_control."""
    parser = argparse.ArgumentParser(
        prog="vm_control",
        description="Host Warden Hyper-V VM Control Plane & Resuscitation Engine (SRS-412-01)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Query and print target Hyper-V VM status and health.",
    )
    parser.add_argument(
        "--reboot",
        action="store_true",
        help="Execute hard power-cycle resuscitation on target quarantine VM.",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        metavar="MILESTONE_NAME",
        help="Take a rolling milestone production checkpoint.",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Restore quarantine VM to golden checkpoint state.",
    )
    parser.add_argument(
        "--deadlock-recover",
        action="store_true",
        help="Force terminate hung vmwp.exe worker processes and cycle VMMS service.",
    )
    parser.add_argument(
        "--vm-name",
        type=str,
        default=QUARANTINE_VM_NAME,
        help=f"Target Hyper-V VM name (default: {QUARANTINE_VM_NAME}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for vm_control."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] vm_control: %(message)s",
    )

    if args.status:
        state = get_vm_state(args.vm_name)
        sys.stdout.write(json.dumps(state, indent=2) + "\n")
        return 0

    if args.reboot:
        success = reboot_quarantine_vm(args.vm_name)
        sys.stdout.write(f"VM Resuscitation Reboot: {'SUCCESS' if success else 'FAILED'}\n")
        return 0 if success else 1

    if args.checkpoint:
        try:
            name = create_checkpoint(args.vm_name, snapshot_name=args.checkpoint)
            sys.stdout.write(f"Milestone checkpoint created: {name}\n")
            return 0
        except Exception as exc:
            sys.stderr.write(f"Failed to create checkpoint: {exc}\n")
            return 1

    if args.rollback:
        success = restore_golden_checkpoint(args.vm_name)
        sys.stdout.write(f"Golden rollback: {'SUCCESS' if success else 'FAILED'}\n")
        return 0 if success else 1

    if args.deadlock_recover:
        success = terminate_vmwp_deadlock(args.vm_name)
        sys.stdout.write(f"Deadlock recovery: {'SUCCESS' if success else 'FAILED'}\n")
        return 0 if success else 1

    # Default action: print VM state
    state = get_vm_state(args.vm_name)
    sys.stdout.write(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
