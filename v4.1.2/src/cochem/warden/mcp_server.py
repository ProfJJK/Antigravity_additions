"""Host Warden FastMCP Server & SRE Supervisory Interface (SRS-412-01).

Implements the Host Warden FastMCP supervisory server running natively on Windows 11 Pro
for the ephemeral Hyper-V quarantine VM (CoChem-Quarantine-VM) (SRS-412-01, MC-HW-56..60).

Architectural Compliance:
- SRS-412-01-FR-001: Pinned to Intel Efficient Cores via CPU affinity mask 0x00FF0000
  and BelowNormal process priority class (16384). Validates P_CORE_MASK (0x000000FF).
- SRS-412-01-FR-002: Windowless execution enforcing CREATE_NO_WINDOW (0x08000000)
  on all host-side subprocesses to prevent desktop heap exhaustion.
- SRS-412-01-FR-003: Non-blocking guest VM liveness polling over named pipe
  (\\\\.\\pipe\\cochem_warden_vm) and telemetry ingestion at 15-second intervals.
- SRS-412-01-FR-004: Three-tier automated resuscitation ladder (Tier 1: service restart,
  Tier 2: VM reboot, Tier 3: golden checkpoint rollback) with SQLite telemetry persistence.
- SRS-412-01-FR-005: Rolling quiescent milestone checkpoints and golden checkpoint restoration
  via Restore-VMSnapshot upon filesystem corruption or guest deadlock.
- SRS-412-01-FR-006: Crash envelope serialization, stream ingestion, and PEP 657 structured
  crash envelope parsing capped to 16 KiB inline telemetry.
- SRS-412-01-FR-008: Automatic Windows startup service registration and background daemon mode.
- Non-Functional Requirements: NFR-HW-01 (RAM <= 50 MB), NFR-HW-02 (E-Core CPU < 5%),
  NFR-HW-03 (Resuscitation sequence initiates within 3 seconds of 45-second timeout).

Zero-Mock Mandate:
- Zero mock variables, zero pass stubs, zero synthetic loop data generators.
- Strictly zero chemistry libraries (Mendeleev, PySCF, ASE forbidden) - general software pipeline.
- FastMCP tool surface strictly exposes exactly three tools: get_vm_health_status,
  trigger_vm_resuscitation, and get_crash_envelopes without vm_name or evidence_dir arguments.
- Loopback interface binding (127.0.0.1) over HTTP or SSE transport; stdio is forbidden.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
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

# Optional Antigravity SDK integration (safely guarded against protobuf version discrepancies)
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.warden.mcp_server")

# ==============================================================================
# Architectural Constants (SRS-412-01 §4 / §6 / §8)
# ==============================================================================

# SRS-412-01-FR-002: Windowless subprocess execution flag
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
SUBPROCESS_FLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

# SRS-412-01-FR-001: Intel Efficient Core CPU affinity bitmask (cores 16-23: 16711680)
E_CORE_MASK: int = 0x00FF0000
P_CORE_MASK: int = 0x000000FF  # Cores 0-7, strictly forbidden Performance Cores

# SRS-412-01-FR-001: Below-normal process priority class (16384)
BELOW_NORMAL_PRIORITY_CLASS: int = 0x00004000

# SRS-412-01-FR-003: Liveness polling endpoints
CANONICAL_PIPE_NAME: str = r"\\.\pipe\cochem_warden_vm"

# SRS-412-01-FR-004 / FR-005: Hyper-V VM Boundaries
QUARANTINE_VM_NAME: str = "CoChem-Quarantine-VM"
GOLDEN_CHECKPOINT_NAME: str = "CoChem-Golden-State"
DEFAULT_VM_RAM_CAP_BYTES: int = 34359738368  # 32 GB static ceiling

# SRS-412-01-FR-006: Telemetry and Crash Ingestion
MAX_CRASH_ENVELOPES: int = 100
INLINE_TELEMETRY_CAP_BYTES: int = 16384  # 16 KiB ceiling per CAP-13

# Host Warden MCP Network Configuration (MC-HW-56..58)
BIND_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 47821
PORT_ENV_VAR: str = "COCHEM_WARDEN_MCP_PORT"
TRANSPORTS: tuple[str, ...] = ("http", "sse")

# Dynamic Resolution of Repository Root and Evidence Directory
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3] if len(_THIS_FILE.parents) >= 4 else _THIS_FILE.parent
CRASH_EVIDENCE_DIR: Path = _REPO_ROOT / ".evidence" / "crashes"

# Ensure repo src is accessible on sys.path for authentic module imports
_SRC_DIR = _REPO_ROOT / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ==============================================================================
# Authentic Warden Module Imports (Strict Zero-Mock Enforcement)
# ==============================================================================

try:
    from cochem.warden.affinity import apply_ecore_affinity, get_ecore_affinity_mask
    from cochem.warden.ipc import connect_hv_socket, listen_warden_pipe
    from cochem.warden.ladder import HealthEscalationLadder, emit_recovery_event
    from cochem.warden.priority import get_host_warden_priority, get_priority_class_value
    from cochem.warden.restart_service import trigger_graceful_restart, wait_for_service_recovery
    from cochem.warden.rollback import (
        prune_stale_checkpoints,
        restore_golden_checkpoint,
        validate_checkpoint_tree,
    )
    from cochem.warden.vm_control import get_vm_state, reboot_quarantine_vm
except ImportError:
    from .affinity import apply_ecore_affinity, get_ecore_affinity_mask  # type: ignore
    from .ipc import connect_hv_socket, listen_warden_pipe  # type: ignore
    from .ladder import HealthEscalationLadder, emit_recovery_event  # type: ignore
    from .priority import get_host_warden_priority, get_priority_class_value  # type: ignore
    from .restart_service import trigger_graceful_restart, wait_for_service_recovery  # type: ignore
    from .rollback import (  # type: ignore
        prune_stale_checkpoints,
        restore_golden_checkpoint,
        validate_checkpoint_tree,
    )
    from .vm_control import get_vm_state, reboot_quarantine_vm  # type: ignore


# ==============================================================================
# Validation Guards (SRS-412-01 / MC-HW-60)
# ==============================================================================

def validate_tier_guard(tier: Any) -> int:
    """Guards trigger_vm_resuscitation against non-integer, boolean, or out-of-bounds tiers."""
    if isinstance(tier, bool) or tier not in (1, 2, 3):
        raise ValueError(f"Tier must be 1, 2, or 3; got: {tier}")
    return int(tier)


def validate_limit_guard(limit: Any, max_limit: int = MAX_CRASH_ENVELOPES) -> int:
    """Guards crash envelope retrieval against non-integer, boolean, or non-positive limits."""
    if isinstance(limit, bool):
        raise TypeError(f"Limit must be an integer, not bool; got: {limit}")
    try:
        val = int(limit)
    except (ValueError, TypeError) as err:
        raise TypeError(f"Limit must be an integer; got: {type(limit).__name__}") from err
    if val <= 0:
        raise ValueError(f"Limit must be a positive integer >= 1; got: {val}")
    return min(val, max_limit)


def validate_affinity_mask_guard(mask: int) -> bool:
    """SRS-412-01-FR-001: Verifies that CPU affinity mask avoids P-Core overlap."""
    if not isinstance(mask, int) or mask <= 0:
        return False
    if (mask & P_CORE_MASK) != 0:
        return False
    return (mask & E_CORE_MASK) != 0


# ==============================================================================
# Process Governance Configuration (SRS-412-01-FR-001)
# ==============================================================================

def configure_process_governance() -> bool:
    """Enforces physical Intel E-Core affinity and BelowNormal priority (SRS-412-01-FR-001)."""
    if not validate_affinity_mask_guard(E_CORE_MASK):
        raise RuntimeError("Configured E_CORE_MASK overlaps Performance Cores")

    affinity_ok = apply_ecore_affinity()
    priority_ok = False

    if win32api is not None and win32process is not None:
        try:
            handle = win32api.GetCurrentProcess()
            win32process.SetPriorityClass(handle, win32process.BELOW_NORMAL_PRIORITY_CLASS)
            priority_ok = True
        except Exception as exc:
            logger.warning("Win32 SetPriorityClass failed: %s", exc)

    if not priority_ok:
        try:
            p = psutil.Process()
            if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                priority_ok = True
            elif hasattr(os, "nice"):
                os.nice(10)
                priority_ok = True
        except Exception as exc:
            logger.warning("psutil SetPriorityClass failed: %s", exc)

    logger.info("Host Warden process governance applied: affinity=%s, priority=%s", affinity_ok, priority_ok)
    return affinity_ok and priority_ok


# ==============================================================================
# FastMCP Server Instance Definition
# ==============================================================================

mcp = FastMCP(
    name="cochem-host-warden",
    instructions=(
        f"Host Warden for the ephemeral Hyper-V quarantine VM '{QUARANTINE_VM_NAME}'. "
        "Read health with get_vm_health_status and recent crashes with get_crash_envelopes; "
        "escalate recovery with trigger_vm_resuscitation (1=service restart, 2=VM reboot, 3=golden rollback)."
    ),
)


# ==============================================================================
# MCP Tool Surface (SRS-412-01 / MC-HW-56..60)
# ==============================================================================

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_vm_health_status() -> dict[str, Any]:
    """Exposes VM CPU, RAM, uptime, and named-pipe telemetry via MCP tool."""
    vm_state = get_vm_state(QUARANTINE_VM_NAME)
    affinity_mask = hex(get_ecore_affinity_mask())
    priority = get_host_warden_priority()
    ipc_state = listen_warden_pipe()

    # SRS-412-01-FR-003 & FR-004: Health requires VM strictly running AND active IPC pipe
    is_running = (vm_state.get("state") == "Running")
    is_connected = bool(ipc_state.get("connected", False))
    is_healthy = is_running and is_connected

    # Authentic guest VM uptime (0 if VM inactive or uninitialized; not host OS boot time)
    guest_uptime = 0
    if is_healthy:
        guest_uptime = int(
            ipc_state.get("uptime")
            or ipc_state.get("uptime_seconds")
            or vm_state.get("uptime")
            or 0
        )

    return {
        "status": "HEALTHY" if is_healthy else "DEGRADED",
        "vm_state": vm_state.get("state", "Offline"),
        "vm_health": vm_state.get("health", "Unknown"),
        "cpu_usage_pct": vm_state.get("cpu_usage", 0),
        "memory_assigned_bytes": vm_state.get("memory_assigned_bytes", 0),
        "uptime": guest_uptime,
        "uptime_seconds": guest_uptime,
        "ipc_status": ipc_state.get("status", "DISCONNECTED"),
        "e_core_mask": affinity_mask,
        "priority_class": priority,
        "telemetry_timestamp": int(time.time()),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
def trigger_vm_resuscitation(tier: int = 1) -> dict[str, Any]:
    """Dispatches escalation recovery commands based on tier (1: restart, 2: reboot, 3: rollback)."""
    tier = validate_tier_guard(tier)

    ladder = HealthEscalationLadder(vm_name=QUARANTINE_VM_NAME)
    ladder.tier = tier

    result = ladder.execute_current_tier()
    if "timestamp" not in result:
        result["timestamp"] = int(time.time())

    # SRS-412-01-FR-004 / MC-HW-55: Persist recovery event to SQLite recovery_telemetry in job_board.db
    try:
        emit_recovery_event(event_data=result)
    except Exception as exc:
        logger.warning("Failed emitting recovery telemetry event: %s", exc)

    logger.warning(
        "Resuscitation tier %d (%s) dispatched: success=%s",
        result.get("tier"),
        result.get("action"),
        result.get("success"),
    )
    return result


def _sanitize_record(record: dict[str, Any], max_bytes: int = INLINE_TELEMETRY_CAP_BYTES) -> dict[str, Any]:
    """Applies 16 KiB ceiling truncation to inline crash telemetry fields (SRS-412-01-FR-006)."""
    sanitized: dict[str, Any] = {}
    for key, val in record.items():
        if isinstance(val, str):
            encoded = val.encode("utf-8")
            if len(encoded) > max_bytes:
                marker = "\n\n[... TELEMETRY TRUNCATED BY HOST WARDEN ...]\n\n"
                head_len = (max_bytes // 2) - 100
                tail_len = (max_bytes // 2) - 100
                head = encoded[:head_len].decode("utf-8", errors="ignore")
                tail = encoded[-tail_len:].decode("utf-8", errors="ignore")
                sanitized[key] = f"{head}{marker}{tail}"
            else:
                sanitized[key] = val
        elif isinstance(val, dict):
            sanitized[key] = _sanitize_record(val, max_bytes=max_bytes)
        elif isinstance(val, list):
            sanitized[key] = [
                _sanitize_record(item, max_bytes=max_bytes) if isinstance(item, dict)
                else (
                    item if not isinstance(item, str) or len(item.encode("utf-8")) <= max_bytes
                    else item.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore") + "... [TRUNCATED]"
                )
                for item in val
            ]
        else:
            sanitized[key] = val
    return sanitized


def read_crash_envelopes(limit: int = 10, evidence_dir: Path | None = None) -> list[dict[str, Any]]:
    """Reads recent crash telemetry packets; evidence_dir is for in-process callers only."""
    validated_limit = validate_limit_guard(limit)
    target_dir = Path(evidence_dir) if evidence_dir else CRASH_EVIDENCE_DIR
    if not target_dir.exists() or not target_dir.is_dir():
        return []

    envelopes: list[dict[str, Any]] = []
    files = sorted(
        list(target_dir.glob("*.json")) + list(target_dir.glob("*.jsonl")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    for jf in files:
        if len(envelopes) >= validated_limit:
            break
        try:
            content = jf.read_text(encoding="utf-8").strip()
            if not content:
                continue
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    sanitized_data = _sanitize_record(data, max_bytes=INLINE_TELEMETRY_CAP_BYTES)
                    sanitized_data["_file"] = jf.name
                    envelopes.append(sanitized_data)
                    continue
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            sanitized_item = _sanitize_record(item, max_bytes=INLINE_TELEMETRY_CAP_BYTES)
                            sanitized_item["_file"] = jf.name
                            envelopes.append(sanitized_item)
                            if len(envelopes) >= validated_limit:
                                break
                    continue
            except json.JSONDecodeError:
                logger.debug("Telemetry payload in %s is not monolithic JSON; attempting JSONL streaming parse", jf.name)

            for line in content.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                    if isinstance(record, dict):
                        sanitized_record = _sanitize_record(record, max_bytes=INLINE_TELEMETRY_CAP_BYTES)
                        sanitized_record["_file"] = jf.name
                        envelopes.append(sanitized_record)
                        if len(envelopes) >= validated_limit:
                            break
                except json.JSONDecodeError:
                    continue
        except (OSError, UnicodeDecodeError):
            continue

    return envelopes


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_crash_envelopes(limit: int = 10) -> list[dict[str, Any]]:
    """Returns recent crash telemetry packets from .evidence/crashes/ (at most 100)."""
    return read_crash_envelopes(limit)


# ==============================================================================
# Daemon Server Entrypoint (SRS-412-01-FR-008)
# ==============================================================================

def main(argv: list[str] | None = None) -> None:
    """Serves the warden tools over loopback HTTP/SSE until the process is stopped."""
    parser = argparse.ArgumentParser(prog="python -m cochem.warden.mcp_server")
    parser.add_argument("--transport", choices=TRANSPORTS, default="http")
    parser.add_argument("--port", type=int, default=int(os.environ.get(PORT_ENV_VAR, DEFAULT_PORT)))
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error(f"--port must be in 1024..65535; got {args.port}")

    # SRS-412-01-FR-001: Enforce physical E-Core affinity and BelowNormal priority prior to server execution
    configure_process_governance()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("Starting Host Warden MCP (%s) on %s:%d", args.transport, BIND_HOST, args.port)
    mcp.run(
        transport=args.transport,
        host=BIND_HOST,
        port=args.port,
        show_banner=False,
        host_origin_protection=True,
    )


if __name__ == "__main__":
    main()
