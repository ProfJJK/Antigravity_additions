import sqlite3
import json

conn = sqlite3.connect(r'D:\__CoChem\__agentic\v4.1.2\job_board.db')
c = conn.cursor()

# Find all failed Batch 7 tasks
c.execute('SELECT task_id, error_log, payload_json FROM jobs WHERE (task_id LIKE ''MC-SRE-1%'' OR task_id LIKE ''MC-TDD-0%'') AND status = ''FAILED''')
failed_tasks = c.fetchall()

for row in failed_tasks:
    t_id, error_log, payload = row
    data = json.loads(payload)
    
    # Append the failure context to the description
    base_desc = data.get('description', '')
    new_desc = f"{base_desc}\n\n[PREVIOUS FAILURE RECORD]\nThis task previously FAILED asymmetric verification. The automated Anti-Spoofing code_forge.validate() rejected the payload because previous agents attempted to spoof completion with mocked data due to hardware bottlenecks. DO NOT SPOOF. You must execute this physically. Orchestrator: Fable 5.1. Workers: Opus 5.5."
    
    data['description'] = new_desc
    data['agent_name'] = 'pivot-strategist' # Route to Fable
    
    # Re-insert as PENDING
    c.execute('''
        UPDATE jobs 
        SET status = 'PENDING', attempts = 0, payload_json = ? 
        WHERE task_id = ?
    ''', (json.dumps(data), t_id))

conn.commit()
conn.close()
print(f"Successfully re-ingested {len(failed_tasks)} Batch 7 tasks with failure records.")
