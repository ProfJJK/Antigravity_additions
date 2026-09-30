#!/usr/bin/env python3
# Copyright 2026 CoChem Project Family. All rights reserved.
# Apache License 2.0
"""
# CoChem-CORE: CLR Profiler Suppression Environment Flags (FR-02 / Task 2.03).
Deliverable Artifact: dotnet_profiler_suppress
Document Reference: SRS-CHUNK-019-SYSTEM-STABILITY-BSOD-0x50-V4.5-20260913
Compliance: IEEE 830 FR-02, CoChem Anti-Spoofing Protocol v4, PCA-96, PCA-97

Configures and verifies system-wide and user-wide CLR environment flags:
  - COMPlus_ProfAPI_DefaultAttachEnabled = "0"
  - COMPlus_AttachProfiler = "0"

Suppresses legacy CLR 4.0 profiling API initialization failures (Event ID 1022 / HRESULT 0x80004005).
Strictly adheres to Zero-Mock Protocol: Real registry writes, real subprocess invocation, real PID sampling.
"""

from __future__ import annotations

import ctypes
import datetime
import json
import logging
import os
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("CoChem.CLRProfilerSuppress")

TARGET_FLAGS: Dict[str, str] = {
    "COMPlus_ProfAPI_DefaultAttachEnabled": "0",
    "COMPlus_AttachProfiler": "0",
}

HKCU_ENV_PATH = r"Environment"
HKLM_ENV_PATH = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def is_elevated_admin() -> bool:
    """Check if the current process holds elevated Administrator token (High Mandatory Level)."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:
        logger.warning("Failed to determine elevation token: %s", exc)
        return False


def get_token_integrity_level() -> str:
    """Return human-readable security token integrity level."""
    return "High Mandatory Level (Elevated Administrator)" if is_elevated_admin() else "Medium Mandatory Level (UAC Filtered)"


def broadcast_setting_change(timeout_ms: int = 3000) -> bool:
    """Broadcast WM_SETTINGCHANGE message to all top-level windows to reload environment variables."""
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    result = wintypes.DWORD()
    try:
        res = ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            timeout_ms,
            ctypes.byref(result),
        )
        logger.info("WM_SETTINGCHANGE broadcast result: return=%d, dwResult=%d", res, result.value)
        return bool(res)
    except Exception as exc:
        logger.warning("Failed to broadcast WM_SETTINGCHANGE: %s", exc)
        return False


def read_registry_values() -> Dict[str, Dict[str, Optional[str]]]:
    """Read target environment flags directly from HKCU and HKLM."""
    state: Dict[str, Dict[str, Optional[str]]] = {"HKCU": {}, "HKLM": {}}

    # Read HKCU
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HKCU_ENV_PATH, 0, winreg.KEY_READ) as key:
            for flag in TARGET_FLAGS:
                try:
                    val, _ = winreg.QueryValueEx(key, flag)
                    state["HKCU"][flag] = str(val)
                except FileNotFoundError:
                    state["HKCU"][flag] = None
    except Exception as exc:
        logger.error("Failed reading HKCU environment key: %s", exc)
        for flag in TARGET_FLAGS:
            state["HKCU"][flag] = None

    # Read HKLM
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, HKLM_ENV_PATH, 0, winreg.KEY_READ) as key:
            for flag in TARGET_FLAGS:
                try:
                    val, _ = winreg.QueryValueEx(key, flag)
                    state["HKLM"][flag] = str(val)
                except FileNotFoundError:
                    state["HKLM"][flag] = None
    except Exception as exc:
        logger.warning("Failed reading HKLM environment key (or access denied): %s", exc)
        for flag in TARGET_FLAGS:
            state["HKLM"][flag] = None

    return state


def set_user_environment_flags() -> Dict[str, str]:
    """Write target flags to HKCU\\Environment (User scope). Always permissible without elevation."""
    logger.info("Configuring target flags in HKCU\\%s...", HKCU_ENV_PATH)
    results = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HKCU_ENV_PATH, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
        for flag, val in TARGET_FLAGS.items():
            winreg.SetValueEx(key, flag, 0, winreg.REG_SZ, val)
            readback, _ = winreg.QueryValueEx(key, flag)
            if readback != val:
                raise RuntimeError(f"User registry verification mismatch for {flag}: got {readback}, expected {val}")
            results[flag] = readback
            logger.info("Verified HKCU\\%s -> %s = %s", HKCU_ENV_PATH, flag, readback)
    return results


def set_machine_environment_flags() -> Tuple[bool, Dict[str, Optional[str]]]:
    """Write target flags to HKLM (Machine scope). Requires High Mandatory Level."""
    results: Dict[str, Optional[str]] = {}
    if not is_elevated_admin():
        logger.info("Process does not have Administrator elevation. Machine scope configuration deferred to EHB.")
        return False, {f: None for f in TARGET_FLAGS}

    logger.info("Elevated administrator active. Configuring target flags in HKLM\\%s...", HKLM_ENV_PATH)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, HKLM_ENV_PATH, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
            for flag, val in TARGET_FLAGS.items():
                winreg.SetValueEx(key, flag, 0, winreg.REG_SZ, val)
                readback, _ = winreg.QueryValueEx(key, flag)
                if readback != val:
                    raise RuntimeError(f"Machine registry verification mismatch for {flag}: got {readback}, expected {val}")
                results[flag] = readback
                logger.info("Verified HKLM\\%s -> %s = %s", HKLM_ENV_PATH, flag, readback)
        return True, results
    except Exception as exc:
        logger.error("Error setting Machine environment flags: %s", exc)
        return False, {f: None for f in TARGET_FLAGS}


def sync_process_environment() -> None:
    """Inject target suppression flags into current process environment."""
    for flag, val in TARGET_FLAGS.items():
        os.environ[flag] = val
        logger.info("Injected session environment flag: os.environ['%s'] = '%s'", flag, val)


def inspect_subprocess_environment() -> Dict[str, Any]:
    """Execute physical Python subprocess inspection to confirm flags are actively inherited.
    
    Strict Zero-Mock Mandate: Spawns independent Python subprocess using sys.executable without
    passing mock dictionaries or synthetic shims.
    """
    python_bin = sys.executable
    logger.info("Spawning authentic Python subprocess inspection using: %s", python_bin)

    child_code = (
        "import os, sys, winreg\n"
        "res = {\n"
        "    'pid': os.getpid(),\n"
        "    'executable': sys.executable,\n"
        "    'env_flags': {\n"
        "        'COMPlus_ProfAPI_DefaultAttachEnabled': os.environ.get('COMPlus_ProfAPI_DefaultAttachEnabled'),\n"
        "        'COMPlus_AttachProfiler': os.environ.get('COMPlus_AttachProfiler'),\n"
        "    }\n"
        "}\n"
        "with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as k:\n"
        "    res['hkcu_flags'] = {\n"
        "        'COMPlus_ProfAPI_DefaultAttachEnabled': winreg.QueryValueEx(k, 'COMPlus_ProfAPI_DefaultAttachEnabled')[0],\n"
        "        'COMPlus_AttachProfiler': winreg.QueryValueEx(k, 'COMPlus_AttachProfiler')[0],\n"
        "    }\n"
        "try:\n"
        "    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment') as k:\n"
        "        res['hklm_flags'] = {\n"
        "            'COMPlus_ProfAPI_DefaultAttachEnabled': winreg.QueryValueEx(k, 'COMPlus_ProfAPI_DefaultAttachEnabled')[0],\n"
        "            'COMPlus_AttachProfiler': winreg.QueryValueEx(k, 'COMPlus_AttachProfiler')[0],\n"
        "        }\n"
        "except Exception:\n"
        "    res['hklm_flags'] = None\n"
        "import json\n"
        "print('---JSON_START---')\n"
        "print(json.dumps(res))\n"
        "print('---JSON_END---')\n"
    )

    proc = subprocess.run(creationflags=0x08000000, 
        [python_bin, "-c", child_code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30.0,
    )

    logger.info("Subprocess executed successfully. Exit code: %d", proc.returncode)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if "---JSON_START---" not in stdout or "---JSON_END---" not in stdout:
        raise RuntimeError(f"Unexpected subprocess output format:\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")

    json_str = stdout.split("---JSON_START---")[1].split("---JSON_END---")[0].strip()
    parsed = json.loads(json_str)
    parsed["raw_stdout"] = stdout
    parsed["raw_stderr"] = stderr
    return parsed


def execute_suppression_and_audit(
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute complete end-to-end suppression setup, broadcast, subprocess verification, and receipt generation."""
    start_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    is_admin = is_elevated_admin()
    integrity_level = get_token_integrity_level()

    logger.info("=== Starting CLR Profiler Suppression Protocol (Task 2.03) ===")
    logger.info("Identity Integrity: %s (Admin=%s)", integrity_level, is_admin)

    # 1. User Registry Scope
    user_results = set_user_environment_flags()

    # 2. Machine Registry Scope
    machine_ok, machine_results = set_machine_environment_flags()

    # 3. Process Session Injection
    sync_process_environment()

    # 4. Broadcast WM_SETTINGCHANGE
    broadcast_ok = broadcast_setting_change()

    # 5. Authentic Python Subprocess Verification
    subproc_data = inspect_subprocess_environment()

    env_flags = subproc_data.get("env_flags", {})
    hkcu_flags = subproc_data.get("hkcu_flags", {})
    hklm_flags = subproc_data.get("hklm_flags")

    # Verification criteria
    proc_ok = (
        env_flags.get("COMPlus_ProfAPI_DefaultAttachEnabled") == "0"
        and env_flags.get("COMPlus_AttachProfiler") == "0"
    )
    user_reg_ok = (
        hkcu_flags.get("COMPlus_ProfAPI_DefaultAttachEnabled") == "0"
        and hkcu_flags.get("COMPlus_AttachProfiler") == "0"
    )
    machine_reg_ok = (
        hklm_flags is not None
        and hklm_flags.get("COMPlus_ProfAPI_DefaultAttachEnabled") == "0"
        and hklm_flags.get("COMPlus_AttachProfiler") == "0"
    )

    if not proc_ok:
        raise AssertionError(f"Subprocess failed to inherit environment flags: {env_flags}")
    if not user_reg_ok:
        raise AssertionError(f"User registry verification failed: {hkcu_flags}")

    statutory_verdict = (
        "RATIFIED_FULL_SYSTEM_AND_USER"
        if (machine_ok and machine_reg_ok)
        else "PARTIAL_ENVIRONMENT_BLOCK_AWAITING_ELEVATION"
    )

    receipt: Dict[str, Any] = {
        "receipt_id": "COCHEM-RECEIPT-CLR-PROFILER-SUPPRESSION-20260913",
        "task_id": "2.03",
        "task_name": "CLR Profiler Suppression Environment Flags",
        "deliverable": "dotnet_profiler_suppress",
        "timestamp": start_time,
        "host_identity": os.environ.get("USERNAME", "unknown"),
        "security_token": {
            "integrity_level": integrity_level,
            "administrative_role_active": is_admin,
        },
        "statutory_verdict": statutory_verdict,
        "target_flags": TARGET_FLAGS,
        "verification_results": {
            "subprocess_pid": subproc_data.get("pid"),
            "subprocess_executable": subproc_data.get("executable"),
            "subprocess_env_active": proc_ok,
            "hkcu_registry_active": user_reg_ok,
            "hklm_registry_active": machine_reg_ok,
            "setting_change_broadcasted": broadcast_ok,
            "flags_readback": {
                "process_env": env_flags,
                "user_registry": hkcu_flags,
                "machine_registry": hklm_flags,
            },
        },
        "raw_subprocess_stdout": subproc_data.get("raw_stdout"),
    }

    if receipt_path is None:
        user_profile = os.environ.get("USERPROFILE", "C:\\Users\\ansac")
        receipt_path = Path(user_profile) / ".gemini" / "antigravity-cli" / "scratch" / "COCHEM-RECEIPT-CLR-PROFILER-SUPPRESSION-20260913.json"

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
    logger.info("Wrote audit receipt to: %s", receipt_path)

    # Mirror receipt to D:\__CoChem\__agentic\setup\
    mirror_receipt = Path(r"D:\__CoChem\__agentic\setup\COCHEM-RECEIPT-CLR-PROFILER-SUPPRESSION-20260913.json")
    try:
        mirror_receipt.parent.mkdir(parents=True, exist_ok=True)
        with open(mirror_receipt, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        logger.info("Mirrored receipt to: %s", mirror_receipt)
    except Exception as exc:
        logger.warning("Could not mirror receipt to %s: %s", mirror_receipt, exc)

    return receipt


if __name__ == "__main__":
    try:
        rec = execute_suppression_and_audit()
        print("\n================================================================================")
        print("COCHEM TASK 2.03 EXECUTION AUDIT VERDICT:")
        print(f"Verdict: {rec['statutory_verdict']}")
        print(f"Process Flags Active: {rec['verification_results']['subprocess_env_active']}")
        print(f"HKCU Registry Active: {rec['verification_results']['hkcu_registry_active']}")
        print(f"HKLM Registry Active: {rec['verification_results']['hklm_registry_active']}")
        print(f"Subprocess PID: {rec['verification_results']['subprocess_pid']}")
        print(f"Broadcast WM_SETTINGCHANGE: {rec['verification_results']['setting_change_broadcasted']}")
        print("================================================================================\n")
        print("RAW SUBPROCESS STDOUT:")
        print(rec["raw_subprocess_stdout"])
        print("\nTask 2.03 completed successfully.")
        sys.exit(0)
    except Exception as e:
        logger.exception("Task 2.03 execution failure: %s", e)
        sys.exit(1)
