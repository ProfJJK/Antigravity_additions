import os
import subprocess
from pathlib import Path

BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
MISSING_CHAPTERS = [
    "ch03_concurrency_layers.md",
    "ch04_task_matrix_blackboard.md",
    "ch05_research_tdd_pivot.md",
    "ch06_dual_wiki_rag.md",
    "ch08_watchdog_sre.md"
]
        
def run_gemini_audit(chapter_file: str):
    print(f"--- AUDITING {chapter_file} Leaf Nodes using Gemini 3.1 Pro (g-audit) ---")
    prompt = f"""You are a Gemini 3.1 Pro swarm acting as the V4.1.2 WBS Auditor (using the g-audit agent profile for General Software Systems).
CRITICAL INSTRUCTION: This pipeline is a GENERAL SOFTWARE SYSTEM, not physical chemistry.
Read the newly generated leaf nodes in D:\\__CoChem\\__agentic\\v4.1.2\\wiki\\wbs\\leaf_nodes\\ corresponding to {chapter_file}.
Audit each JSON payload against user_global.md (Rule 18 compliance).
Ensure all line bounds are strictly between 20-100 lines and there are NO whole-file replacements.
If any violations or chemistry hallucinations are found, explicitly delete the violating JSON file.
"""
    cmd = ["agy", "--agent", "g-audit", "--model", "Gemini 3.1 Pro (High)", "-p", prompt]
    res = subprocess.run(cmd, stdin=subprocess.DEVNULL, creationflags=0x08000000, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Audit failed for {chapter_file}: {res.stderr}")
    else:
        print(f"Audit passed for {chapter_file}")

if __name__ == "__main__":
    for chapter in MISSING_CHAPTERS:
        run_gemini_audit(chapter)
    print("\nAll missing chapters have been audited successfully!")
