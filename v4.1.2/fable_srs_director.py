"""Fable Director Driver for Stage 1: Multi-Part SRS and Dependency Graph Generation.

Invokes claude.exe (Claude Fable 5.1 Director) using authenticated Max subscription,
strictly enforcing Windows CREATE_NO_WINDOW (0x08000000) and NODE_OPTIONS memory cap.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW: int = 0x08000000
CLAUDE_EXE: str = r"C:\Users\ansac\.local\bin\claude.exe"

FABLE_ORCHESTRATOR_INSTRUCTION = """
[SYSTEM ARCHITECTURE MANDATE]
You are Fable 5.1 acting as the MASTER ORCHESTRATOR. 
When executing tasks, planning, or generating code, you MUST adhere to the following Swarm topology:
1. Fable (You) acts as the high-level Orchestrator and Director.
2. Opus 5.5 subagents MUST be delegated to write the easy/boilerplate parts of the code.
3. Fable (You) steps in to personally implement the difficult, complex, and high-risk architectural parts.
4. Fable (You) audits the Opus outputs before coming back out.
5. Upon returning to the main loop, Gemini Pro 3.1 will act as the final Asymmetric Verifier to further audit the output.
"""

def invoke_fable_director(prompt: str, timeout_sec: int = 120) -> str:
    """Invokes claude.exe non-interactively with memory safeguards and no window popup."""
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--max-old-space-size=4096"
    
    full_prompt = FABLE_ORCHESTRATOR_INSTRUCTION + "\n\n" + prompt

    cmd = [CLAUDE_EXE, "--dangerously-skip-permissions", "-p", full_prompt]
    
    res = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout_sec
    )
    if res.returncode != 0:
        raise RuntimeError(f"Claude invocation failed (code {res.returncode}): {res.stderr}")
    return res.stdout.strip()


if __name__ == "__main__":
    print("Testing Fable Director invocation...")
    response = invoke_fable_director("Respond with: FABLE_DIRECTOR_READY")
    print("Fable Director Response:", response)

