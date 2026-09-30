import json
from pathlib import Path

tasks = [
    "MC-RAG-08", "MC-RAG-09", "MC-RAG-11", "MC-RAG-12", "MC-RAG-16", 
    "MC-RAG-17", "MC-RAG-18", "MC-RAG-19", "MC-RAG-20", "MC-RAG-21", 
    "MC-SRE-01", "MC-SRE-02", "MC-SRE-03", "MC-SRE-04", "MC-SRE-05", 
    "MC-SRE-06", "MC-SRE-07", "MC-SRE-08", "MC-SRE-09", "MC-SRE-10"
]

print("=== VERIFYING IMPLEMENTATIONS ON DISK ===")
for tid in tasks:
    leaf_p = Path(f"wiki/wbs/leaf_nodes/{tid}.json")
    if not leaf_p.exists():
        print(f"{tid}: LEAF NODE MISSING!")
        continue
    data = json.loads(leaf_p.read_text(encoding="utf-8"))
    target = Path(data["target_file"])
    if not target.exists():
        print(f"{tid}: Target file {target} MISSING!")
        continue
    content = target.read_text(encoding="utf-8")
    has_tag = f"[{tid}]" in content
    # check line range or chunk
    print(f"{tid:10} | Target: {str(target):35} | Tag in file: {has_tag} | Verification: {data.get('verification_command')}")
