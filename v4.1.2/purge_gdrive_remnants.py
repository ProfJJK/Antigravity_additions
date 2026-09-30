"""Remediate surviving pre-ratification remnants in Gdrive pipeline_plans 4.1.1.

1. Deletes the 7 surviving pre-ratification files:
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts\01_Matrix_Task_Queue_WBS.json
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts\02_Host_Warden_WBS.json
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts\03_Split_Domain_WBS.json
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\SRS_Impl_01_Host_Warden.md
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\SRS_Impl_02_Split_Domain.md
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\SRS_Impl_03_Task_Queue.md
   - D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\INDEX_Coding.md
2. Removes empty directory D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts
3. Purges all 183 unratified tasks from D:\Gdrive\__agentic\pipeline_plans\4.1.1\job_board.db
"""

from __future__ import annotations

import os
import sqlite3

TARGET_FILES = [
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts\01_Matrix_Task_Queue_WBS.json",
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts\02_Host_Warden_WBS.json",
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts\03_Split_Domain_WBS.json",
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\SRS_Impl_01_Host_Warden.md",
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\SRS_Impl_02_Split_Domain.md",
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\SRS_Impl_03_Task_Queue.md",
    r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\wiki\INDEX_Coding.md",
]

TARGET_DIR = r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\WBS_Micro_Prompts"
GDRIVE_DB = r"D:\Gdrive\__agentic\pipeline_plans\4.1.1\job_board.db"


def purge_gdrive_remnants() -> None:
    print("=== Step 2A: Deleting 7 Surviving Pre-Ratification Files ===")
    deleted_files = []
    for f in TARGET_FILES:
        if os.path.exists(f):
            size = os.path.getsize(f)
            os.remove(f)
            print(f"DELETED: {f} ({size} bytes)")
            deleted_files.append(f)
        else:
            print(f"ALREADY ABSENT: {f}")

    # Verify all 7 files are gone
    remaining = [f for f in TARGET_FILES if os.path.exists(f)]
    assert len(remaining) == 0, f"Error: files still remain: {remaining}"
    print(f"Verification: 0 of 7 files remain. ({len(deleted_files)} files deleted)\n")

    # Remove empty WBS_Micro_Prompts directory if empty
    if os.path.exists(TARGET_DIR):
        try:
            os.rmdir(TARGET_DIR)
            print(f"REMOVED EMPTY DIR: {TARGET_DIR}\n")
        except OSError as e:
            print(f"Could not remove {TARGET_DIR}: {e}\n")

    print("=== Step 2B: Purging Unratified Tasks from Gdrive job_board.db ===")
    assert os.path.exists(GDRIVE_DB), f"Database not found: {GDRIVE_DB}"
    conn = sqlite3.connect(GDRIVE_DB)
    cur = conn.cursor()

    cur.execute("SELECT status, count(*) FROM jobs GROUP BY status")
    counts_before = cur.fetchall()
    print(f"Counts before purge: {counts_before}")

    cur.execute("BEGIN IMMEDIATE;")
    cur.execute("DELETE FROM jobs WHERE status != 'COMPLETED';")
    rows_deleted = cur.rowcount
    print(f"Rows deleted: {rows_deleted}")
    conn.commit()

    cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    cp = cur.fetchall()
    print(f"wal_checkpoint(TRUNCATE): {cp}")

    cur.execute("VACUUM;")
    print("VACUUM executed.")

    cur.execute("SELECT count(*) FROM jobs")
    total_remaining = cur.fetchone()[0]
    print(f"Total jobs remaining in Gdrive job_board.db: {total_remaining}")
    assert total_remaining == 0, f"Expected 0 remaining jobs, got {total_remaining}"

    conn.close()
    print("PASS: Gdrive job_board.db successfully purged and vacuumed.\n")


if __name__ == "__main__":
    purge_gdrive_remnants()
