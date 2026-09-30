import sqlite3
import json
import os
import re

db_path = r'D:\__CoChem\cochem_kanban.db'
dropzone = r'D:\__CoChem\__agentic\.scripts\prompts'
os.makedirs(dropzone, exist_ok=True)

conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT task_id, prompt FROM kanban_tasks_v3 WHERE status='todo'")
rows = c.fetchall()

count = 0
for task_id, prompt in rows:
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', task_id)
    json_path = os.path.join(dropzone, f'{safe_id}.json')
    if not os.path.exists(json_path):
        payload = {'task_description': prompt}
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        count += 1
print(f'Migrated {count} missing tasks from DB to dropzone!')
