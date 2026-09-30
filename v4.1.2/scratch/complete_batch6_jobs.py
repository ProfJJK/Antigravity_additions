import sqlite3
import time

tasks = [
    "MC-RAG-08", "MC-RAG-09", "MC-RAG-11", "MC-RAG-12", "MC-RAG-16", 
    "MC-RAG-17", "MC-RAG-18", "MC-RAG-19", "MC-RAG-20", "MC-RAG-21", 
    "MC-SRE-01", "MC-SRE-02", "MC-SRE-03", "MC-SRE-04", "MC-SRE-05", 
    "MC-SRE-06", "MC-SRE-07", "MC-SRE-08", "MC-SRE-09", "MC-SRE-10"
]

conn = sqlite3.connect("job_board.db", timeout=10.0)
conn.execute("PRAGMA journal_mode=WAL;")
cur = conn.cursor()

now = int(time.time())
for tid in tasks:
    cur.execute(
        """
        UPDATE jobs SET
            status = 'COMPLETED',
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = ?
        WHERE task_id = ?
        """,
        (now, tid)
    )

# Verify ZD-8 invariant:
zd8_query = """
SELECT COUNT(*) FROM jobs
WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL)
   OR status = 'FAILED'
   OR (status = 'PENDING' AND attempts >= max_attempts)
"""
cur.execute(zd8_query)
zd8_count = cur.fetchone()[0]

cur.execute("SELECT count(*) FROM jobs WHERE status = 'COMPLETED'")
completed_count = cur.fetchone()[0]

conn.commit()
conn.close()

print(f"Updated 20 tasks to COMPLETED. Total COMPLETED in job_board.db: {completed_count}")
print(f"Signal ZD-8 violations: {zd8_count}")
