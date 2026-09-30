import sqlite3
import json
import uuid

def ingest_wbs(json_path: str):
    with open(json_path, 'r') as f:
        data = json.load(f)
    conn = sqlite3.connect(r'D:\__CoChem\__agentic\v4.1.2\job_board.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO jobs (task_id, priority, target, description, status, payload)
        VALUES (?, ?, ?, ?, 'PENDING', ?)
    ''', (data['task_id'], data['priority'], data['target'], data['description'], json.dumps(data)))
    conn.commit()
    conn.close()
