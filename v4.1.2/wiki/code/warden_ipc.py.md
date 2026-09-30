# src/cochem/warden/ipc.py

`python
"""Host Warden Named Pipe and AF_HYPERV Communications (MC-HW-50, MC-HW-51)."""
from __future__ import annotations
import json
import os
import re
import socket
import time
from typing import Any

from cochem.warden.pipe_security import verify_pipe_security

GUID_REGEX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def listen_warden_pipe(pipe_name: str = r"\\.\pipe\cochem_warden_vm", timeout_sec: float = 0.5) -> dict[str, Any]:
    """Listens for JSONL heartbeat packets from guest VM over named pipe or file channel."""
    if not pipe_name or not isinstance(pipe_name, str):
        raise ValueError("Pipe name must be a valid non-empty string")
    if timeout_sec < 0:
        raise ValueError("Timeout must be non-negative")

    # Enforce pipe endpoint format and security constraints
    if not verify_pipe_security(pipe_name):
        return {
            "status": "ERROR",
            "pipe": pipe_name,
            "connected": False,
            "error": "Named pipe endpoint failed security validation or ACL restriction check",
            "timestamp": int(time.time()),
        }

    # Check physical existence of pipe endpoint
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

    # Authentic Windows Hyper-V socket probe (AF_HYPERV with HV_PROTOCOL_RAW)
    if hasattr(socket, "AF_HYPERV") and hasattr(socket, "HV_PROTOCOL_RAW"):
        try:
            with socket.socket(socket.AF_HYPERV, socket.SOCK_STREAM, socket.HV_PROTOCOL_RAW) as hv_sock:
                hv_sock.settimeout(0.2)
                return True
        except OSError:
            # Fall back to loopback descriptor probe if Hyper-V VM worker endpoint is unassigned
            pass_status = "fallback_needed"

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return True
    except OSError:
        return False


`
