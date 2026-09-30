"""Remediate dirty lease metadata in v4.1.1 and v4.1.2 job_board.db.

Clears lease_owner and lease_expires_at on COMPLETED rows to satisfy
Signal ZD-8 in watchdog_sre.py.
"""

from __future__ import annotations

import sqlite3
import sys

TARGET_DBS = [
    r"D:\__CoChem\__agentic\v4.1.1\job_board.db",
    r"D:\__CoChem\__agentic\v4.1.2\job_board.db",
]


def remediate_leases() -> None:
    for db_path in TARGET_DBS:
        print(f"=== Processing {db_path} ===")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Check before
        cur.execute("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL)")
        zd8_before = cur.fetchone()[0]
        print(f"ZD-8 violations before: {zd8_before}")

        # Atomic update
        cur.execute("BEGIN IMMEDIATE;")
        cur.execute("UPDATE jobs SET lease_owner = NULL, lease_expires_at = NULL WHERE status = 'COMPLETED';")
        rows_updated = cur.rowcount
        conn.commit()
        print(f"Rows updated: {rows_updated}")

        # Checkpoint WAL
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        cp = cur.fetchall()
        print(f"wal_checkpoint(TRUNCATE): {cp}")

        # Check after
        cur.execute("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL)")
        zd8_after = cur.fetchone()[0]
        print(f"ZD-8 violations after: {zd8_after}")
        assert zd8_after == 0, f"Expected 0 ZD-8 violations, got {zd8_after}"

        # Verify completed count
        cur.execute("SELECT COUNT(*) FROM jobs WHERE status = 'COMPLETED'")
        completed_count = cur.fetchone()[0]
        print(f"Total completed rows: {completed_count}")
        assert completed_count == 121, f"Expected 121 completed rows, got {completed_count}"

        conn.close()
        print(f"PASS: {db_path} successfully cleaned and verified.\n")


if __name__ == "__main__":
    remediate_leases()
