import json, sqlite3
from pathlib import Path

tasks = [
    "MC-RAG-08", "MC-RAG-09", "MC-RAG-11", "MC-RAG-12", "MC-RAG-16", 
    "MC-RAG-17", "MC-RAG-18", "MC-RAG-19", "MC-RAG-20", "MC-RAG-21", 
    "MC-SRE-01", "MC-SRE-02", "MC-SRE-03", "MC-SRE-04", "MC-SRE-05", 
    "MC-SRE-06", "MC-SRE-07", "MC-SRE-08", "MC-SRE-09", "MC-SRE-10"
]

conn = sqlite3.connect("job_board.db")
c = conn.cursor()

print("TASK_ID | JOB_BOARD TARGET | LEAF_NODE TARGET | MATCH?")
print("-" * 70)
for tid in tasks:
    jb_row = c.execute("SELECT payload_json FROM jobs WHERE task_id = ?", (tid,)).fetchone()
    jb_target = "NONE"
    jb_title = ""
    if jb_row and jb_row[0]:
        jb_p = json.loads(jb_row[0])
        jb_target = jb_p.get("target_file")
        jb_title = jb_p.get("title")
    
    leaf_p = Path(f"wiki/wbs/leaf_nodes/{tid}.json")
    ln_target = "NONE"
    ln_title = ""
    if leaf_p.exists():
        ln_data = json.loads(leaf_p.read_text(encoding="utf-8"))
        ln_target = ln_data.get("target_file")
        ln_title = ln_data.get("title")
        
    match = (jb_target == ln_target)
    print(f"{tid:10} | {jb_target:35} | {ln_target:35} | {match}")
    if not match:
        print(f"  JB Title: {jb_title}")
        print(f"  LN Title: {ln_title}")
conn.close()
