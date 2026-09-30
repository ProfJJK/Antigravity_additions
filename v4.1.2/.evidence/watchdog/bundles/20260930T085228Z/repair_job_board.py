"""Resuscitation 2026-09-30 bundle 20260930T085228Z (session v4-1-2-d3): quarantine MC-CONCURRENCY-FIX.

One BEGIN IMMEDIATE transaction on the live job_board.db. The row is the only ready PENDING job and there is
no DSP consumer deployed (no worker process, no CoChemDspWorker_V412_W* task registered) and the code_forge
bridge is known to crash deterministically (..\v2 deleted -> llm_router import fails -> agy legacy path ->
WinError 206 argv limit at P4). PENDING -> BLOCKED with the reason appended to error_log; not_before cleared.
"""
import json, sqlite3, sys, time

DB = r"D:\__CoChem\__agentic\v4.1.2\job_board.db"
TASK = "MC-CONCURRENCY-FIX"
ZD8 = ("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL) OR status = 'FAILED' "
       "OR (status = 'PENDING' AND attempts >= max_attempts)")
NOTE = ("\n[RESUSCITATION 2026-09-30 bundle 20260930T085228Z session v4-1-2-d3] PENDING -> BLOCKED (operator "
        "quarantine, not a verdict): retry window (not_before 1790758043) expired with no DSP consumer deployed "
        "(no worker_daemon process since 02:39:55 CDT, no CoChemDspWorker_V412_W* scheduled task) and the "
        "code_forge bridge still crashes deterministically (..\v2 deleted -> llm_router import fails -> agy legacy "
        "path -> WinError 206 argv limit at P4). Re-queue with: UPDATE jobs SET status='PENDING', not_before=NULL "
        "WHERE task_id='MC-CONCURRENCY-FIX' once workers are registered and the bridge environment is repaired.")
now = int(time.time())
conn = sqlite3.connect(DB, timeout=30, isolation_level=None)
conn.execute("PRAGMA busy_timeout=30000")
try:
    conn.execute("BEGIN IMMEDIATE")
    before = conn.execute("SELECT id, status, attempts, max_attempts, lease_owner, not_before, updated_at FROM jobs "
                          "WHERE task_id=?", (TASK,)).fetchone()
    assert before == (399, "PENDING", 1, 10, None, 1790758043, 1790754443), before
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    cur = conn.execute("UPDATE jobs SET status='BLOCKED', lease_owner=NULL, lease_expires_at=NULL, not_before=NULL, "
                       "error_log = COALESCE(error_log, '') || ?, updated_at=? "
                       "WHERE task_id=? AND status='PENDING' AND attempts=1 AND updated_at=1790754443",
                       (NOTE, now, TASK))
    assert cur.rowcount == 1, cur.rowcount
    conn.execute("INSERT INTO recovery_telemetry (tier, action, details) VALUES (3, 'fable_resuscitation', ?)",
                 (json.dumps({"bundle": "20260930T085228Z", "session": "v4-1-2-d3", "rows_changed": 1,
                              "task_id": TASK, "transition": "PENDING->BLOCKED", "reason": "no DSP consumer deployed; "
                              "bridge environment broken", "ts": now}),))
    zd8 = conn.execute(ZD8).fetchone()[0]
    ready = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='PENDING' AND attempts < max_attempts AND "
                         "(not_before IS NULL OR not_before <= ?)", (now,)).fetchone()[0]
    total_after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert zd8 == 0 and ready == 0 and total_after == total_before == 262, (zd8, ready, total_before, total_after)
    conn.execute("COMMIT")
    print("COMMITTED: ZD8=%d ready_pending=%d total=%d updated_at=%d" % (zd8, ready, total_after, now))
except Exception as exc:
    conn.execute("ROLLBACK"); print("ROLLED BACK:", repr(exc)); sys.exit(1)
finally:
    conn.close()
ro = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
print("post-commit (ro): ZD8=%d counts=%s" % (ro.execute(ZD8).fetchone()[0],
      ro.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()))
print(ro.execute("SELECT id,status,attempts,not_before,updated_at FROM jobs WHERE task_id=?", (TASK,)).fetchone())
