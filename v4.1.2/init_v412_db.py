"""
Initialize D:\\__CoChem\\__agentic\\v4.1.2\\job_board.db with the canonical schema
and migrate the 121 verified completed baseline tasks from v4.1.1.
"""
import sqlite3
import os

V411_DB = r"D:\__CoChem\__agentic\v4.1.1\job_board.db"
V412_DB = r"D:\__CoChem\__agentic\v4.1.2\job_board.db"

def main():
    print(f"Initializing {V412_DB}...")
    os.makedirs(os.path.dirname(V412_DB), exist_ok=True)

    conn = sqlite3.connect(V412_DB)
    cursor = conn.cursor()

    # 1. Pragmas
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA synchronous=NORMAL;")

    # 2. Canonical Schema
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT UNIQUE NOT NULL,
        job_type TEXT NOT NULL CHECK (job_type IN ('micro_code', 'macro_audit', 'research')),
        status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'BLOCKED')),
        payload_json TEXT NOT NULL,
        priority INTEGER DEFAULT 1,
        attempts INTEGER DEFAULT 0,
        max_attempts INTEGER DEFAULT 10,
        lease_owner TEXT,
        lease_expires_at INTEGER,
        not_before INTEGER,
        result_path TEXT,
        created_at INTEGER DEFAULT (strftime('%s', 'now')),
        updated_at INTEGER DEFAULT (strftime('%s', 'now')),
        error_log TEXT
    );
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_jobs_claim 
    ON jobs (status, attempts, max_attempts, priority, created_at);
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_jobs_lease_expiry 
    ON jobs (status, lease_expires_at);
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_jobs_reap 
    ON jobs (status, lease_expires_at);
    """)

    # 3. Migrate 121 completed rows from v4.1.1
    print(f"Migrating completed rows from {V411_DB}...")
    cursor.execute(f"ATTACH DATABASE '{V411_DB.replace(chr(92), '/')}' AS v411;")
    cursor.execute("INSERT INTO jobs SELECT * FROM v411.jobs WHERE status = 'COMPLETED';")
    migrated_count = cursor.rowcount
    conn.commit()
    cursor.execute("DETACH DATABASE v411;")
    print(f"Migrated {migrated_count} rows.")

    # 4. Set autoincrement sequence
    cursor.execute("UPDATE sqlite_sequence SET seq = 121 WHERE name = 'jobs';")
    conn.commit()

    # 5. Checkpoint & Vacuum
    cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    cursor.execute("VACUUM;")

    # 6. Verification
    cursor.execute("SELECT count(*) FROM jobs;")
    total_count = cursor.fetchone()[0]
    cursor.execute("SELECT status, count(*) FROM jobs GROUP BY status;")
    status_breakdown = cursor.fetchall()
    cursor.execute("SELECT MIN(id), MAX(id) FROM jobs;")
    id_range = cursor.fetchone()
    cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = 'jobs';")
    seq_val = cursor.fetchone()[0]

    print(f"v4.1.2 Total rows: {total_count}")
    print(f"v4.1.2 Status breakdown: {status_breakdown}")
    print(f"v4.1.2 ID range: min={id_range[0]}, max={id_range[1]}")
    print(f"v4.1.2 sqlite_sequence jobs seq: {seq_val}")

    conn.close()

    assert total_count == 121, f"Expected 121 rows, got {total_count}"
    assert status_breakdown == [('COMPLETED', 121)], f"Expected only COMPLETED rows, got {status_breakdown}"
    assert id_range == (1, 121), f"Expected ID range (1, 121), got {id_range}"
    assert seq_val == 121, f"Expected seq=121, got {seq_val}"
    print("SUCCESS: v4.1.2 job_board.db initialized and validated successfully.")

if __name__ == "__main__":
    main()
