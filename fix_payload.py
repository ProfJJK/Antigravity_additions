import sqlite3
import json

conn = sqlite3.connect(r'D:\__CoChem\__agentic\v4.1.2\job_board.db')
c = conn.cursor()
t_id = 'MC-CONCURRENCY-FIX'

c.execute('SELECT payload_json FROM jobs WHERE task_id = ?', (t_id,))
row = c.fetchone()
if row:
    data = json.loads(row[0])
    data['target_file'] = r'D:\__CoChem\__agentic\v4.1.2\src\cochem\dsp\worker_daemon.py'
    c.execute('UPDATE jobs SET status = ?, payload_json = ?, attempts = 0 WHERE task_id = ?', ('PENDING', json.dumps(data), t_id))
    conn.commit()
    print('Fixed MC-CONCURRENCY-FIX payload and set to PENDING.')
conn.close()
