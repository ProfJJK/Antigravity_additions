import os
import subprocess
from pathlib import Path

BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
CLAUDE_EXE = r"C:\Users\ansac\.local\bin\claude.exe"

TASKS = [
    {
        "chapter": "ch02_quarantine_vm.md",
        "part": "Part 1 (FR-001 to FR-004)",
        "focus": "ONLY generate tasks for FR-001, FR-002, FR-003, and FR-004."
    },
    {
        "chapter": "ch02_quarantine_vm.md",
        "part": "Part 2 (FR-007 to FR-008 & NFRs)",
        "focus": "ONLY generate tasks for FR-007, FR-008, and the Non-Functional Requirements (NFR-VM-01 to 03). REMEMBER: FR-005 and FR-006 were explicitly stripped."
    },
    {
        "chapter": "ch03_concurrency_layers.md",
        "part": "Part 1 (FR-001 to FR-004)",
        "focus": "ONLY generate tasks for FR-001 through FR-004. You MUST use the exact test names specified in Section 10 Traceability."
    },
    {
        "chapter": "ch03_concurrency_layers.md",
        "part": "Part 2 (FR-005 to FR-008 & Failure Modes)",
        "focus": "ONLY generate tasks for FR-005 through FR-008, plus the Failure Modes (e.g., Subprocess Hang 1740s recovery). You MUST use the exact test names specified in Section 10 Traceability."
    },
    {
        "chapter": "ch08_watchdog_sre.md",
        "part": "Part 1 (Matrices 1 & 2)",
        "focus": "ONLY generate tasks for FR-001, FR-002, FR-003 (Matrix 1: Process Death), and FR-004 (Matrix 2: Zombie Deadlocks). You MUST write the end-to-end tests mandated in Section 10."
    },
    {
        "chapter": "ch08_watchdog_sre.md",
        "part": "Part 2 (Matrix 3 & Signal ZD-8)",
        "focus": "ONLY generate tasks for FR-005 (Matrix 3: Data Corruption), FR-006, FR-007, FR-008, and FR-009 (Signal ZD-8 heuristic). You MUST write the end-to-end tests mandated in Section 10."
    }
]

def run_fable_fracturer(task):
    chapter_file = task["chapter"]
    part = task["part"]
    focus = task["focus"]
    
    print(f"\n--- FRACTURING {chapter_file} - {part} ---")
    
    prompt = f"""Act as the Fable 5.1 Director managing 20 Opus 5.5 sub-agents.
Read the SRS chapter D:\\__CoChem\\__agentic\\v4.1.2\\wiki\\srs\\{chapter_file}.

CRITICAL: To avoid output token limits, you are fracturing this document in PARTS.
{focus}

CRITICAL INSTRUCTION: This pipeline is a GENERAL SOFTWARE SYSTEM. It is NOT a physical chemistry calculation. DO NOT generate tasks, tests, or code that involve PySCF, quantum chemistry, or physical chemistry libraries.
Ensure you strictly adhere to the Rule 18 Whole-File Rewrite Ban (chunk_start and chunk_end bounds must be between 20-100 lines).
Output the fractured nodes into individual JSON files in D:\\__CoChem\\__agentic\\v4.1.2\\wiki\\wbs\\leaf_nodes\\ using schema version '4.1.1-wbs-node/1'.
Use --dangerously-skip-permissions to write the files directly.
"""
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--max-old-space-size=4096"
    cmd = [CLAUDE_EXE, "--dangerously-skip-permissions", "-p", prompt]
    
    res = subprocess.run(cmd, stdin=subprocess.DEVNULL, env=env, creationflags=0x08000000, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Failed to fracture {chapter_file} {part}: {res.stderr}")
    else:
        print(f"Successfully fractured {chapter_file} {part}")

if __name__ == "__main__":
    for task in TASKS:
        run_fable_fracturer(task)
    print("\nSplit fracture regeneration complete!")
