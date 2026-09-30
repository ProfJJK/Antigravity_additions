import os
import subprocess
from pathlib import Path

BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
LEAF_DIR = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"

CHAPTERS = [
    "ch02_quarantine_vm.md",
    "ch03_concurrency_layers.md",
    "ch08_watchdog_sre.md"
]

CLAUDE_EXE = r"C:\Users\ansac\.local\bin\claude.exe"

def run_fable_fracturer(chapter_file: str):
    print(f"\n--- FRACTURING {chapter_file} ---")
    
    extra_instructions = ""
    if "ch02" in chapter_file:
        extra_instructions = "CRITICAL: The SRS has been scrubbed of hallucinated chemistry. Ensure you generate tasks for the remaining Docker/VM requirements (FR-001, FR-002, FR-003, FR-004, FR-007, FR-008)."
    elif "ch03" in chapter_file:
        extra_instructions = "CRITICAL: You MUST adhere perfectly to the Section 10 Traceability Matrix. Use the exact test names specified in Section 10 (e.g., test_f04_fable_invocation_enforces_create_no_window_flag). Do not use generic test names."
    elif "ch08" in chapter_file:
        extra_instructions = "CRITICAL: You MUST write the end-to-end tests mandated in Section 10 (e.g., test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation). DO NOT hallucinate excuses to skip them. This is a general software system, there are no chemistry fixtures to wait for."

    prompt = f"""Act as the Fable 5.1 Director managing 20 Opus 5.5 sub-agents.
Read the SRS chapter D:\\__CoChem\\__agentic\\v4.1.2\\wiki\\srs\\{chapter_file} and fracture it into strict N=1 JSON leaf nodes.
{extra_instructions}
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
        print(f"Failed to fracture {chapter_file}: {res.stderr}")
    else:
        print(f"Successfully fractured {chapter_file}")

if __name__ == "__main__":
    for chapter in CHAPTERS:
        # First, purge any existing nodes for this chapter by relying on Fable to overwrite or we just leave them.
        # Actually, let's just let Fable generate new ones. Fable generates them with unique IDs, so we might get duplicates. 
        # But we'll just audit the newly generated payload files.
        run_fable_fracturer(chapter)
    print("\nFracture regeneration complete!")
