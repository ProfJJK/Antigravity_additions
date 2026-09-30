import os
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
LEAF_DIR = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"
DB_PATH = BASE_DIR / "job_board.db"

def inject_tasks():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    count = 0
    for file_path in LEAF_DIR.glob("*.json"):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        task_id = data["task_id"]
        # Only inject the ones we just generated for the missing chapters (or skip ones already in DB)
        # We can just insert OR IGNORE.
        payload = json.dumps(data)
        
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO jobs (task_id, job_type, payload_json, status, priority, attempts, max_attempts) "
                "VALUES (?, ?, ?, 'PENDING', 1, 0, 10)",
                (task_id, "micro_code", payload)
            )
            if cursor.rowcount > 0:
                count += 1
        except Exception as e:
            print(f"Failed to inject {task_id}: {e}")

    conn.commit()
    conn.close()
    print(f"Successfully injected {count} new tasks into job_board.db")

if __name__ == "__main__":
    inject_tasks()
