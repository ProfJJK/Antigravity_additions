"""Ephemeral Docker Sandbox Execution Wrapper (MC-DSP-15)."""
from __future__ import annotations
from typing import Any

MANDATORY_SANDBOX_FLAGS: list[str] = [
    "--network", "none",
    "--read-only",
    "--tmpfs", "/tmp:rw,size=2g",
    "--memory", "4g",
    "--cpus", "2",
    "--pids-limit", "512",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges"
]

def run_in_docker_sandbox(cmd: list[str], image: str = "cochem-executor:latest") -> list[str]:
    """Constructs compliant docker run command with isolation and resource constraints."""
    full_cmd = ["docker", "run", "--rm"]
    full_cmd.extend(MANDATORY_SANDBOX_FLAGS)
    full_cmd.append(image)
    full_cmd.extend(cmd)
    return full_cmd
