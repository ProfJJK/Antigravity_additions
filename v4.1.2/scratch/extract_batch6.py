import sqlite3
import json

tasks = [
    "MC-RAG-08", "MC-RAG-09", "MC-RAG-11", "MC-RAG-12", "MC-RAG-16", 
    "MC-RAG-17", "MC-RAG-18", "MC-RAG-19", "MC-RAG-20", "MC-RAG-21", 
    "MC-SRE-01", "MC-SRE-02", "MC-SRE-03", "MC-SRE-04", "MC-SRE-05", 
    "MC-SRE-06", "MC-SRE-07", "MC-SRE-08", "MC-SRE-09", "MC-SRE-10"
]

print("=== JOB_BOARD.DB ===")
conn = sqlite3.connect("job_board.db")
c = conn.cursor()
rows = c.execute(f"SELECT task_id, status, payload_json FROM jobs WHERE task_id IN ({','.join(['?']*len(tasks))})", tasks).fetchall()
print(f"Found {len(rows)} tasks:")
task_details = {}
for tid, status, payload in rows:
    p = json.loads(payload) if payload else {}
    task_details[tid] = {
        "status": status,
        "target_file": p.get("target_file"),
        "title": p.get("title") or p.get("name"),
        "keys": list(p.keys()),
        "payload": p
    }
    print(f"  {tid} [{status}]: {p.get('target_file')} - {p.get('title') or p.get('name') or p.get('task_name')}")
conn.close()

print("\n=== KNOWLEDGE_INDEX.DB ===")
conn_k = sqlite3.connect("knowledge_index.db")
c_k = conn_k.cursor()
for tid in tasks[:5]:
    docs = c_k.execute("SELECT document_id, title, filepath, length(body) FROM fts_documents WHERE document_id = ? OR body LIKE ?", (tid, f"%{tid}%")).fetchall()
    print(f"  Doc search for {tid}: {len(docs)} matches")
    for d in docs:
        print(f"    id={d[0]}, title={d[1]}, filepath={d[2]}, body_len={d[3]}")
conn_k.close()

with open("scratch/task_details.json", "w", encoding="utf-8") as f:
    json.dump(task_details, f, indent=2)
print("\nWrote scratch/task_details.json")
