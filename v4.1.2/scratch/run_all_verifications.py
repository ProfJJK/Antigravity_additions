import json
import subprocess
import sys
from pathlib import Path

tasks = [
    "MC-RAG-08", "MC-RAG-09", "MC-RAG-11", "MC-RAG-12", "MC-RAG-16", 
    "MC-RAG-17", "MC-RAG-18", "MC-RAG-19", "MC-RAG-20", "MC-RAG-21", 
    "MC-SRE-01", "MC-SRE-02", "MC-SRE-03", "MC-SRE-04", "MC-SRE-05", 
    "MC-SRE-06", "MC-SRE-07", "MC-SRE-08", "MC-SRE-09", "MC-SRE-10"
]

no_win = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

results = {}
for tid in tasks:
    leaf_p = Path(f"wiki/wbs/leaf_nodes/{tid}.json")
    data = json.loads(leaf_p.read_text(encoding="utf-8"))
    cmd_str = data.get("verification_command")
    print(f"Running verification for {tid}: {cmd_str}")
    res = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, creationflags=no_win)
    results[tid] = {
        "command": cmd_str,
        "returncode": res.returncode,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip()
    }
    status_str = "PASS" if res.returncode == 0 else "FAIL"
    print(f"  -> {status_str} (exit code {res.returncode})")

with open("scratch/verification_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
print("Saved scratch/verification_results.json")
