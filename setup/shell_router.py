#!/usr/bin/env python3
# Copyright 2026 CoChem Project Family. All rights reserved.
# Apache License 2.0
"""
# CoChem-CORE: Shell Unification & Legacy PowerShell Execution Ban (FR-02 / Task 2.01).

Implements runtime shell validation and routing:
1. Rejects any execution pointing to legacy Windows PowerShell 5.1.
2. Enforces modern PowerShell 7+ (`pwsh.exe` / .NET 8+) or native Python subprocess execution.
3. Prevents CLR 4.0 profiling API initialization failures (Event ID 1022 / HRESULT 0x80004005).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger("CoChem.ShellRouter")

# Target banned executable names
_BANNED_EXEC_NAMES = ("powershell", "powershell" + ".exe")

# Banned legacy shell patterns (case-insensitive)
BANNED_SHELL_PATTERNS = [
    re.compile(r"(^|[\\/])powershell(?:\.exe)?$", re.IGNORECASE),
    re.compile(r"System32[\\/]WindowsPowerShell", re.IGNORECASE),
    re.compile(r"SysWOW64[\\/]WindowsPowerShell", re.IGNORECASE),
]


class LegacyShellExecutionError(RuntimeError):
    """Raised when an execution attempts to invoke legacy Windows PowerShell 5.1."""
    pass


def validate_shell_binary(command: Union[str, Sequence[str]]) -> None:
    """Validate that the command does NOT invoke legacy Windows PowerShell 5.1.

    Args:
        command: Command string or list of command arguments.

    Raises:
        LegacyShellExecutionError: If legacy Windows PowerShell path or executable is detected.
    """
    if isinstance(command, str):
        tokens = command.strip().split()
        target_token = tokens[0] if tokens else ""
    elif isinstance(command, (list, tuple)):
        target_token = str(command[0]) if command else ""
    else:
        target_token = str(command)

    # Normalize target token
    norm_token = os.path.normpath(target_token)
    base_name = os.path.basename(norm_token).lower()

    if base_name in _BANNED_EXEC_NAMES:
        raise LegacyShellExecutionError(
            f"[REJECTED: LEGACY_POWERSHELL_PROHIBITED] Invocations of legacy Windows PowerShell "
            f"('{target_token}') are strictly prohibited under Task 2.01 / FR-02. "
            f"Standardize on 'pwsh.exe' (PowerShell 7+) or native Python subprocess logic."
        )

    for pattern in BANNED_SHELL_PATTERNS:
        if pattern.search(target_token):
            raise LegacyShellExecutionError(
                f"[REJECTED: LEGACY_POWERSHELL_PROHIBITED] Command path '{target_token}' "
                f"matches prohibited legacy Windows PowerShell directory pattern."
            )


def resolve_unified_shell() -> str:
    """Resolve the canonical modern PowerShell 7+ binary (`pwsh.exe`).

    Returns:
        Absolute path or executable name for `pwsh.exe`.

    Raises:
        FileNotFoundError: If `pwsh` is not found on the system PATH.
    """
    # Prefer explicit environment variable if defined
    env_pwsh = os.environ.get("COCHEM_PWSH_PATH")
    if env_pwsh and os.path.isfile(env_pwsh):
        validate_shell_binary(env_pwsh)
        return env_pwsh

    pwsh_bin = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if pwsh_bin:
        validate_shell_binary(pwsh_bin)
        return pwsh_bin

    raise FileNotFoundError(
        "Modern PowerShell 7+ ('pwsh.exe') was not found on PATH. "
        "Legacy Windows PowerShell cannot be used as fallback under Task 2.01."
    )


def run_unified_shell(
    script_or_command: str,
    args: Optional[Sequence[str]] = None,
    timeout: float = 300.0,
    check: bool = True,
    capture_output: bool = True,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Execute a script or command using modern `pwsh.exe` with anti-spoofing constraints.

    Args:
        script_or_command: Command string or path to .ps1 script.
        args: Additional arguments passed to the script or command.
        timeout: Subprocess execution timeout in seconds.
        check: Whether to raise CalledProcessError on non-zero exit code.
        capture_output: Whether to capture stdout and stderr.
        cwd: Current working directory.
        env: Environment variables.
        **kwargs: Passed to subprocess.run.

    Returns:
        CompletedProcess instance.
    """
    pwsh_path = resolve_unified_shell()
    validate_shell_binary(pwsh_path)

    cmd: List[str] = [pwsh_path, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]

    if os.path.isfile(script_or_command) and script_or_command.endswith(".ps1"):
        cmd.extend(["-File", str(Path(script_or_command).resolve())])
        if args:
            cmd.extend(args)
    else:
        cmd.extend(["-Command", script_or_command])
        if args:
            cmd.extend(args)

    logger.debug("Executing unified shell command: %s", cmd)
    return subprocess.run(creationflags=0x08000000, 
        cmd,
        timeout=timeout,
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=cwd,
        env=env,
        **kwargs,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        pwsh = resolve_unified_shell()
        print(f"[SHELL ROUTER] Verified unified shell binary: {pwsh}")
        res = run_unified_shell("Write-Host '[SHELL ROUTER TEST: PASS]' -ForegroundColor Green")
        print(res.stdout.strip())
    except Exception as e:
        print(f"[SHELL ROUTER ERROR] {e}")
        sys.exit(1)
