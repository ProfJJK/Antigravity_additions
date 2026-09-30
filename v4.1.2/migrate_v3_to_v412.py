import sqlite3
import json
import os
import sys

conn_v3 = sqlite3.connect('D:/__CoChem/__agentic/v3/cochem_kanban_v3.db')
conn_v3.row_factory = sqlite3.Row
tasks_v3 = conn_v3.execute("SELECT * FROM kanban_tasks_v3 WHERE status = 'todo'").fetchall()

conn_v4 = sqlite3.connect('D:/__CoChem/__agentic/v4.1.2/job_board.db')
# Resuscitation 2026-09-30: the former `DELETE FROM jobs WHERE id > 291` wiped ids 292-365
# (batch 07-10 rows). Migration must be additive (SRS-412-04-FR-008: INSERT OR IGNORE).

for task in tasks_v3:
    task_id = task['task_id']
    payload_uri = task['payload_uri']
    
    payload_str = "{}"
    if payload_uri and os.path.exists(payload_uri):
        with open(payload_uri, "r", encoding="utf-8") as f:
            payload_str = f.read()
    
    try:
        p_data = json.loads(payload_str)
        if not isinstance(p_data, dict):
            raise ValueError("payload is not a JSON object")
    except ValueError:
        # v3 payload_uri files are often raw Markdown prompts: wrap them instead of storing
        # non-JSON text that the DSP worker can only reject ("dispatch rejected: Expecting value").
        p_data = {"prompt": payload_str, "target": "D:/__CoChem/__agentic/v3",
                  "payload_provenance": f"v3 kanban payload_uri {payload_uri}"}
    p_data["task_id"] = task_id
    if "target_file" not in p_data:
        p_data["target_file"] = p_data.get("target", "D:/__CoChem/__agentic/v3")
    payload_str = json.dumps(p_data)

    try:
        conn_v4.execute('''
            INSERT OR IGNORE INTO jobs (task_id, job_type, status, payload_json, priority)
            VALUES (?, ?, ?, ?, ?)
        ''', (task_id, 'micro_code', 'PENDING', payload_str, task['priority'] or 1))
        print(f"Migrated {task_id} as micro_code")
    except sqlite3.Error as e:
        print(f"Error {task_id}: {e}")

conn_v4.commit()
