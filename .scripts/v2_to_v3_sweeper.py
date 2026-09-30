import sqlite3
import json
import os
import shutil
from pathlib import Path
from datetime import datetime

v2_db = Path(r"D:\__CoChem\__agentic\v2\pipeline_v2.sqlite")
runs_dir = Path(r"D:\__CoChem\__agentic\v2\runs")
v3_prompts_dir = Path(r"D:\__CoChem\__agentic\.scripts\prompts")

def sweep():
    if not v2_db.exists():
        print(f"DB not found: {v2_db}")
        return

    conn = sqlite3.connect(v2_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT pipeline_id, status FROM pipeline_runs WHERE status != 'COMPLETED'")
    rows = cursor.fetchall()
    
    migrated_count = 0
    
    for row in rows:
        pid = row["pipeline_id"]
        status = row["status"]
        
        input_json = runs_dir / pid / "00_input.json"
        if not input_json.exists():
            continue
            
        try:
            with open(input_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Failed to read {input_json}: {e}")
            continue
            
        task_desc = data.get("task", "")
        target = data.get("target", "D:\\__CoChem")
        
        if not task_desc:
            continue
            
        # Write V3 payload
        v3_task_id = f"MIGRATED_{pid.replace('pv2-', '')}"
        
        # Don't migrate if it's already in the prompt dir
        v3_filename = v3_prompts_dir / f"{v3_task_id}.json"
        if v3_filename.exists():
            continue
            
        payload = {
            "task_id": v3_task_id,
            "priority": 5,  # Regular priority
            "description": task_desc,
            "acceptance_criteria": [
                f"Migrated from V2 pipeline (Original Status: {status}).",
                "Execute the described task and ensure it passes the v3.0.0 TDD asymmetric audit."
            ],
            "pipeline_v3": {
                "target": target,
                "mode": "docker",
                "plan_only": False
            }
        }
        
        with open(v3_filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            
        print(f"Migrated {pid} -> {v3_filename.name}")
        migrated_count += 1
        
        # Mark as completed in V2 so it doesn't get picked up again
        cursor.execute("UPDATE pipeline_runs SET status = 'COMPLETED', halt_reason = 'MIGRATED_TO_V3' WHERE pipeline_id = ?", (pid,))

    conn.commit()
    conn.close()
    print(f"\nSwept {migrated_count} stranded tasks into the V3 queue.")

if __name__ == "__main__":
    sweep()
