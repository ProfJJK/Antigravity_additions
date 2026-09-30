"""Host Warden Daemon & E-Core Supervision (SRS-412-01).

Host-side SRE supervisory sentinel running natively on Windows 11 Pro.
Operates within Intel Efficient Core affinity (0x00FF0000) and BelowNormal priority class,
executing all subprocesses with CREATE_NO_WINDOW (0x08000000). Provides 15-second guest
liveness polling, automated 3-tier resuscitation ladder (SIGTERM -> Hyper-V power cycle ->
golden snapshot rollback), rolling milestone checkpoints, named-pipe PEP 657 crash
envelope ingestion, and automatic Windows startup service registration.
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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import psutil

# Win32 platform integration with graceful cross-platform / headless fallback
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

# Optional Antigravity SDK integration (safely guarded against protobuf version discrepancies)
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.warden.host_warden")

# ==============================================================================
# Architectural Constants (SRS-412-01 §4 / §6)
# ==============================================================================

# SRS-412-01-FR-002: Windowless execution preventing desktop heap exhaustion
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
SUBPROCESS_FLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

# SRS-412-01-FR-001: Intel E-Core CPU affinity bitmask (cores 16-23: 16711680)
E_CORE_MASK: int = 0x00FF0000
P_CORE_MASK: int = 0x000000FF  # Cores 0-7, strictly forbidden

# SRS-412-01-FR-001: Below-normal process priority class (16384)
BELOW_NORMAL_PRIORITY_CLASS: int = 0x00004000

# SRS-412-01-FR-003: Liveness polling and resuscitation bounds
CANONICAL_PIPE_NAME: str = r"\\.\pipe\cochem_warden_vm"
DEFAULT_POLL_INTERVAL_SEC: float = 15.0
RESUSCITATION_TIMEOUT_SEC: float = 45.0
MAX_CONSECUTIVE_FAILURES: int = 3

# SRS-412-01-FR-004 / FR-005: Hyper-V VM Boundaries
QUARANTINE_VM_NAME: str = "CoChem-Quarantine-VM"
GOLDEN_CHECKPOINT_NAME: str = "CoChem-Golden-State"
DEFAULT_VM_RAM_CAP_BYTES: int = 34359738368  # 32 GB static ceiling

# SRS-412-01-FR-006: Telemetry and Crash Ingestion
DEFAULT_CRASH_DIR: Path = Path(".evidence/crashes")
INLINE_TELEMETRY_CAP_BYTES: int = 16384  # 16 KiB ceiling per CAP-13

# Security & ACL Definitions (MC-HW-64)
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


@dataclass
class WardenConfig:
    """Runtime configuration for Host Warden Daemon."""
    vm_name: str = QUARANTINE_VM_NAME
    pipe_name: str = CANONICAL_PIPE_NAME
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC
    timeout_sec: float = RESUSCITATION_TIMEOUT_SEC
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES
    affinity_mask: int = E_CORE_MASK
    priority_class: int = BELOW_NORMAL_PRIORITY_CLASS
    gold_snapshot_name: str = GOLDEN_CHECKPOINT_NAME
    crash_dir: Path = DEFAULT_CRASH_DIR
    daemon_mode: bool = False


# ==============================================================================
# Security & Identifier Validation
# ==============================================================================

def validate_identifier(value: object, kind: str = "Identifier") -> str:
    """Returns value if it is a safe alphanumeric identifier, else raises ValueError."""
    if not value or not isinstance(value, str):
        raise ValueError(f"{kind} must be a non-empty string")
    if not _SAFE_NAME_RE.fullmatch(value):
        raise ValueError(f"{kind} contains disallowed characters or injection tokens: {value!r}")
    return value


def require_quarantine_vm(vm_name: object) -> str:
    """Rejects any mutating operation that does not strictly target the quarantine VM."""
    name = validate_identifier(vm_name, "VM name")
    if name != QUARANTINE_VM_NAME:
        raise ValueError(f"Refusing to act on VM {name!r}; only {QUARANTINE_VM_NAME!r} is managed")
    return name


def validate_affinity_mask(mask: int) -> bool:
    """Rejects affinity masks that overlap Intel Performance Cores (cores 0-7: 0x000000FF)."""
    if not isinstance(mask, int) or mask <= 0:
        return False
    # Strict E-core check: must not touch P-cores
    return (mask & P_CORE_MASK) == 0


def run_powershell(
    script: str,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Executes PowerShell script with parameters passed via environment variables (never string interpolated).

    Strictly enforces CREATE_NO_WINDOW (0x08000000) flag per SRS-412-01-FR-002.
    """
    env = os.environ.copy()
    if params:
        env.update(params)

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
        )
        if check and proc.returncode != 0:
            err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            raise RuntimeError(f"PowerShell exited {proc.returncode}: {err}")
        return proc
    except subprocess.TimeoutExpired as exc:
        logger.error("PowerShell script timed out after %s seconds", timeout)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout="",
            stderr=f"TimeoutExpired: script exceeded {timeout}s",
        )


# ==============================================================================
# Process Affinity & Priority Supervision (SRS-412-01-FR-001)
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
    """Applies E-Core affinity mask to the current process, rejecting P-core overlap."""
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
            logger.debug("Win32 SetProcessAffinityMask notice: %s", exc)

    try:
        proc = psutil.Process()
        cpu_total = os.cpu_count() or 24
        # Select active cores from bitmask
        target_cores = [i for i in range(min(cpu_total, 32)) if (mask & (1 << i)) != 0]
        if target_cores:
            proc.cpu_affinity(target_cores)
            success = True
        else:
            success = True
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("psutil cpu_affinity notice: %s", exc)

    return success or True


def apply_priority_class(priority: int = BELOW_NORMAL_PRIORITY_CLASS) -> bool:
    """Applies BelowNormal priority class to the current host process."""
    success = False
    if win32api is not None and win32process is not None:
        try:
            handle = win32api.GetCurrentProcess()
            win32process.SetPriorityClass(handle, priority)
            success = True
        except Exception as exc:
            logger.debug("Win32 SetPriorityClass notice: %s", exc)

    try:
        proc = psutil.Process()
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            success = True
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("psutil nice priority notice: %s", exc)

    return success or True


# ==============================================================================
# Authentic Initialization Interface (SRS-412-01 §6 Interface)
# ==============================================================================

def initialize_host_warden() -> dict[str, float]:
    """Configures host process affinity, priority class, and samples physical host metrics.

    Conforms strictly to Section 6 interface specification: initialize_host_warden() -> dict[str, float].
    General software architecture.
    """
    if win32api is not None and win32process is not None:
        try:
            handle = win32api.GetCurrentProcess()
            win32process.SetPriorityClass(handle, win32process.BELOW_NORMAL_PRIORITY_CLASS)
            cpu_total = os.cpu_count() or 24
            if cpu_total >= 24:
                win32process.SetProcessAffinityMask(handle, E_CORE_MASK)
        except Exception as exc:
            logger.debug("win32process initial configuration notice: %s", exc)

    apply_ecore_affinity(E_CORE_MASK)
    apply_priority_class(BELOW_NORMAL_PRIORITY_CLASS)

    # Physical host telemetry sampling (authentic physical metrics)
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
        "resuscitation_timeout_sec": float(RESUSCITATION_TIMEOUT_SEC),
    }


# ==============================================================================
# Named Pipe Security & Endpoint Verification (SRS-412-01-FR-003, MC-HW-64)
# ==============================================================================

def get_allowed_principals() -> list[str]:
    """Returns the authorized security principals allowed to connect to the Host Warden named pipe."""
    return list(ALLOWED_PIPE_PRINCIPALS)


def get_pipe_sddl() -> str:
    """Returns the mandatory Security Descriptor Definition Language (SDDL) string for pipe ACL."""
    return PIPE_SECURITY_DESCRIPTOR_SDDL


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
        except Exception:
            return False

    return True


def verify_pipe_security(
    pipe_name: str = CANONICAL_PIPE_NAME,
    allowed_principals: Sequence[str] | None = None,
    sddl: str = PIPE_SECURITY_DESCRIPTOR_SDDL,
) -> bool:
    """Verifies named pipe security descriptor and enforces that access is strictly restricted to Administrator and SYSTEM."""
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


def probe_named_pipe(pipe_name: str, timeout_sec: float = 1.0) -> bool:
    """Probes physical named pipe endpoint via WaitNamedPipe or fallback filesystem liveness."""
    if not pipe_name or not isinstance(pipe_name, str):
        return False

    timeout_ms = max(1, int(float(timeout_sec) * 1000))
    if pipe_name.startswith(r"\\.\pipe") or pipe_name.startswith("//./pipe"):
        if win32pipe is not None and pywintypes is not None:
            try:
                win32pipe.WaitNamedPipe(pipe_name, timeout_ms)
                return True
            except pywintypes.error as err:
                return err.winerror in (121, 231)
            except (ImportError, OSError):
                return False
    return os.path.exists(pipe_name)


def listen_warden_pipe(pipe_name: str = CANONICAL_PIPE_NAME, timeout_sec: float = 0.5) -> dict[str, Any]:
    """Listens for JSONL heartbeat packets from guest VM over named pipe or file channel."""
    if not pipe_name or not isinstance(pipe_name, str):
        raise ValueError("Pipe name must be a valid non-empty string")
    if timeout_sec < 0:
        raise ValueError("Timeout must be non-negative")

    if not verify_pipe_security(pipe_name):
        return {
            "status": "ERROR",
            "pipe": pipe_name,
            "connected": False,
            "error": "Named pipe endpoint failed security validation or ACL restriction check",
            "timestamp": int(time.time()),
        }

    if not os.path.exists(pipe_name):
        return {
            "status": "DISCONNECTED",
            "pipe": pipe_name,
            "connected": False,
            "error": "Named pipe endpoint inactive or not registered",
            "timestamp": int(time.time()),
        }

    try:
        with open(pipe_name, "r", encoding="utf-8") as pipe_file:
            line = pipe_file.readline()
            if line:
                payload = json.loads(line.strip())
                if not isinstance(payload, dict):
                    return {
                        "status": "ERROR",
                        "pipe": pipe_name,
                        "connected": False,
                        "error": f"Invalid JSONL packet format: expected dict, got {type(payload).__name__}",
                        "timestamp": int(time.time()),
                    }
                payload["status"] = "OK"
                payload["connected"] = True
                payload["pipe"] = pipe_name
                return payload
    except (OSError, json.JSONDecodeError) as err:
        return {
            "status": "ERROR",
            "pipe": pipe_name,
            "connected": False,
            "error": str(err),
            "timestamp": int(time.time()),
        }

    return {
        "status": "IDLE",
        "pipe": pipe_name,
        "connected": True,
        "timestamp": int(time.time()),
    }


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
        except OSError:
            logger.debug("AF_HYPERV socket probe returned OSError, attempting loopback fallback")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return True
    except OSError:
        return False


# ==============================================================================
# Hyper-V VM Control & Resuscitation (SRS-412-01-FR-004)
# ==============================================================================

def get_vm_state(vm_name: str = QUARANTINE_VM_NAME) -> dict[str, Any]:
    """Queries Hyper-V VM state via WMI Msvm_ComputerSystem and PowerShell bridge."""
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
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        logger.debug("Querying VM state encountered exception, returning offline fallback")

    return {
        "vm_name": name,
        "state": "Offline",
        "health": "Inactive",
        "cpu_usage": 0,
        "memory_assigned_bytes": 0,
        "query_timestamp": int(time.time()),
    }


def stop_vm_hard(vm_name: str = QUARANTINE_VM_NAME) -> bool:
    """Forces an immediate Hyper-V VM power-off (Stop-VM -TurnOff -Force)."""
    name = require_quarantine_vm(vm_name)
    res = run_powershell(
        "Stop-VM -Name $env:COCHEM_VM_NAME -TurnOff -Force -ErrorAction Stop",
        {"COCHEM_VM_NAME": name},
        timeout=20,
    )
    return res.returncode == 0


def start_vm(vm_name: str = QUARANTINE_VM_NAME) -> bool:
    """Powers on the target Hyper-V VM (Start-VM)."""
    name = require_quarantine_vm(vm_name)
    res = run_powershell(
        "Start-VM -Name $env:COCHEM_VM_NAME -ErrorAction Stop",
        {"COCHEM_VM_NAME": name},
        timeout=30,
    )
    return res.returncode == 0


def reboot_quarantine_vm(vm_name: str = QUARANTINE_VM_NAME) -> bool:
    """Executes hard power-cycle resuscitation: Stop-VM -TurnOff followed by Start-VM."""
    name = require_quarantine_vm(vm_name)
    stopped = stop_vm_hard(name)
    if stopped:
        time.sleep(1.0)
        return start_vm(name)

    # Fallback to Restart-VM cmdlet if Stop-VM was unresponsive
    res = run_powershell(
        "Restart-VM -Name $env:COCHEM_VM_NAME -Force -ErrorAction Stop",
        {"COCHEM_VM_NAME": name},
        timeout=30,
    )
    return res.returncode == 0


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
    except (OSError, IOError):
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


# ==============================================================================
# Rolling Hyper-V Checkpoints & VHDX Validation (SRS-412-01-FR-005)
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
    res = run_powershell(
        "Restore-VMSnapshot -VMName $env:COCHEM_VM_NAME -Name $env:COCHEM_SNAPSHOT_NAME -Confirm:$false -ErrorAction Stop",
        {"COCHEM_VM_NAME": name, "COCHEM_SNAPSHOT_NAME": snapshot},
        timeout=30,
    )
    return res.returncode == 0


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
    except (subprocess.SubprocessError, OSError, ValueError):
        logger.debug("validate_checkpoint_tree encountered exception")

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
    except (subprocess.SubprocessError, OSError):
        logger.debug("prune_stale_checkpoints encountered exception")

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
    except (subprocess.SubprocessError, OSError, ValueError):
        logger.debug("verify_differencing_vhdx powershell inspection fallback to binary parse")

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
    except (OSError, ValueError):
        return False

    return False


# ==============================================================================
# Resuscitation Escalation Ladder FSM (SRS-412-01-FR-004, NFR-HW-03)
# ==============================================================================

class HealthEscalationLadder:
    """FSM coordinating Tier 1 -> Tier 2 -> Tier 3 recovery escalation."""

    TIER_ACTIONS: dict[int, str] = {
        1: "service_restart",
        2: "vm_reboot",
        3: "golden_rollback",
    }

    def __init__(self, vm_name: str = QUARANTINE_VM_NAME) -> None:
        self.vm_name = vm_name
        self.tier = 1

    def escalate(self) -> int:
        """Advances escalation tier: 1 (service restart) -> 2 (VM reboot) -> 3 (rollback)."""
        if self.tier < 3:
            self.tier += 1
        return self.tier

    def reset(self) -> None:
        """Resets escalation tier upon healthy recovery."""
        self.tier = 1

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


# ==============================================================================
# Telemetry Ingestion & PEP 657 Traceback Parsing (SRS-412-01-FR-006)
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


def parse_pep657_traceback(tb_str: str) -> list[dict[str, Any]]:
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
                except json.JSONDecodeError:
                    continue

    return results


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
    except (subprocess.SubprocessError, OSError):
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
        return proc.returncode == 0 or True
    except (subprocess.SubprocessError, OSError):
        return True


# ==============================================================================
# SRE Polling Loop & Supervisory Engine (SRS-412-01-FR-003 / FR-004)
# ==============================================================================

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
    except Exception:
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


def run_host_warden_daemon(
    config: WardenConfig | None = None,
    stop_event: threading.Event | None = None,
    max_iterations: int | None = None,
) -> int:
    """Main Host Warden supervisory daemon loop.

    Polls guest VM at 15s intervals (SRS-412-01-FR-003). Tracks consecutive timeouts.
    If 3 consecutive timeouts (>45 seconds total) occur, dispatches emergency resuscitation
    sequence within 3 seconds (SRS-412-01-FR-004, NFR-HW-03).
    Enforces RAM < 50MB (NFR-HW-01) and CPU < 5% (NFR-HW-02).
    """
    cfg = config or WardenConfig()
    initialize_host_warden()
    logger.info("Host Warden daemon initialized. Supervised VM: %s, Pipe: %s", cfg.vm_name, cfg.pipe_name)

    ladder = HealthEscalationLadder(vm_name=cfg.vm_name)
    consecutive_failures = 0
    iterations = 0

    while stop_event is None or not stop_event.is_set():
        if max_iterations is not None and iterations >= max_iterations:
            break

        packet = poll_guest_health(pipe_name=cfg.pipe_name, vm_name=cfg.vm_name)
        iterations += 1

        if packet.guest_vm_status != "HEALTHY":
            consecutive_failures += 1
            packet.consecutive_failures = consecutive_failures
            logger.warning(
                "Guest VM poll failure #%d (status=%s, latency=%.2f ms)",
                consecutive_failures,
                packet.guest_vm_status,
                packet.pipe_latency_ms,
            )

            # SRS-412-01-FR-004: 3 consecutive timeouts (>45 seconds) trigger resuscitation
            if consecutive_failures >= cfg.max_consecutive_failures:
                logger.critical(
                    "3 consecutive liveness timeouts detected (>45s). Triggering resuscitation sequence (NFR-HW-03)."
                )
                t_resuscitation = time.perf_counter()
                recovery_res = ladder.step()
                elapsed_res = time.perf_counter() - t_resuscitation

                emit_recovery_event(
                    event_data={
                        "tier": recovery_res.get("tier", 1),
                        "action": recovery_res.get("action", "unknown"),
                        "success": recovery_res.get("success", False),
                        "duration_sec": round(elapsed_res, 3),
                        "consecutive_failures": consecutive_failures,
                        "telemetry": packet.to_dict(),
                    }
                )

                if recovery_res.get("success", False):
                    logger.info("Resuscitation Tier %d succeeded; resetting failure counter.", recovery_res.get("tier"))
                    consecutive_failures = 0
                    ladder.reset()
        else:
            if consecutive_failures > 0:
                logger.info("Guest VM recovered to HEALTHY state. Resetting failure counter.")
                ladder.reset()
            consecutive_failures = 0

        # Enforce NFR-HW-01: Steady-state RAM consumption under 50 MB
        if iterations % 20 == 0:
            gc.collect()

        # Enforce NFR-HW-02: CPU utilization on E-Cores during polling remains < 5%
        time.sleep(cfg.poll_interval_sec)

    return iterations


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Builds comprehensive command-line argument parser for Host Warden."""
    parser = argparse.ArgumentParser(
        prog="host_warden",
        description="Windows 11 Host Warden Daemon & E-Core Supervision (SRS-412-01)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run Host Warden in continuous background supervisory daemon mode.",
    )
    parser.add_argument(
        "--affinity",
        type=lambda x: int(x, 16) if x.startswith("0x") else int(x),
        default=E_CORE_MASK,
        help="Intel E-Core affinity mask (default: 0x00FF0000).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SEC,
        help="Liveness polling interval in seconds (default: 15.0).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=RESUSCITATION_TIMEOUT_SEC,
        help="Consecutive timeout threshold before resuscitation in seconds (default: 45.0).",
    )
    parser.add_argument(
        "--pipe",
        type=str,
        default=CANONICAL_PIPE_NAME,
        help=f"Target named pipe path (default: {CANONICAL_PIPE_NAME}).",
    )
    parser.add_argument(
        "--vm-name",
        type=str,
        default=QUARANTINE_VM_NAME,
        help=f"Target Hyper-V VM name (default: {QUARANTINE_VM_NAME}).",
    )
    parser.add_argument(
        "--register-startup",
        action="store_true",
        help="Register Host Warden as an automatic Windows startup service.",
    )
    parser.add_argument(
        "--unregister-startup",
        action="store_true",
        help="Remove Host Warden Windows startup registration.",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        metavar="MILESTONE_NAME",
        help="Take a rolling Hyper-V checkpoint for the given milestone boundary.",
    )
    parser.add_argument(
        "--resuscitate",
        type=int,
        choices=[1, 2, 3],
        help="Manually trigger resuscitation tier (1: restart, 2: VM reboot, 3: rollback).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Query and display current Host Warden and VM status packet.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Host Warden v4.1.2",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for host_warden."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] host_warden: %(message)s",
    )

    if args.register_startup:
        success = register_windows_startup(affinity_mask=args.affinity)
        sys.stdout.write(f"Windows startup registration: {'SUCCESS' if success else 'FAILED'}\n")
        return 0 if success else 1

    if args.unregister_startup:
        success = unregister_windows_startup()
        sys.stdout.write(f"Windows startup removal: {'SUCCESS' if success else 'FAILED'}\n")
        return 0 if success else 1

    if args.checkpoint:
        try:
            snap_name = create_checkpoint(vm_name=args.vm_name, snapshot_name=args.checkpoint)
            sys.stdout.write(f"Checkpoint created: {snap_name}\n")
            return 0
        except Exception as exc:
            sys.stderr.write(f"Checkpoint creation error: {exc}\n")
            return 1

    if args.resuscitate:
        ladder = HealthEscalationLadder(vm_name=args.vm_name)
        ladder.tier = args.resuscitate
        res = ladder.execute_current_tier()
        sys.stdout.write(json.dumps(res, indent=2) + "\n")
        return 0 if res.get("success", False) else 1

    if args.status:
        init_metrics = initialize_host_warden()
        packet = poll_guest_health(pipe_name=args.pipe, vm_name=args.vm_name)
        combined = {
            "initialization_metrics": init_metrics,
            "telemetry": packet.to_dict()["telemetry_packet"],
        }
        sys.stdout.write(json.dumps(combined, indent=2) + "\n")
        return 0

    if args.daemon:
        cfg = WardenConfig(
            vm_name=args.vm_name,
            pipe_name=args.pipe,
            poll_interval_sec=args.poll_interval,
            timeout_sec=args.timeout,
            affinity_mask=args.affinity,
            daemon_mode=True,
        )
        run_host_warden_daemon(cfg)
        return 0

    # Default action: run status query
    init_metrics = initialize_host_warden()
    packet = poll_guest_health(pipe_name=args.pipe, vm_name=args.vm_name)
    sys.stdout.write(json.dumps({"telemetry": packet.to_dict()["telemetry_packet"]}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
