"""Resuscitation repair for bundle 20260930T073958Z_1 (Signal ZD-8 -> 0). One BEGIN IMMEDIATE transaction."""
import json, sqlite3, time, sys
DB = r"D:\__CoChem\__agentic\v4.1.2\job_board.db"
TAG = "[RESUSCITATION 2026-09-30 bundle 20260930T073958Z_1 session v4-1-2-e6]"
ZD8 = ("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL) "
       "OR status = 'FAILED' OR (status = 'PENDING' AND attempts >= max_attempts)")
MD_ROWS = ["status_mcp_v3_upgrade", "audit_youtube_pipeline_v3", "implement_spycfit_gpu",
           "implement_spycfit_integration", "audit_spycfit_spoofing_resolution", "audit_spycfit_physics_autopsy",
           "kanban_improve_BASE_Student_Flawless_UI_Phase1_62388135",
           "kanban_improve_BASE_Fable_Production_Ready_Phase2_14de0654",
           "kanban_srs_hybrid_rag_upgrade_31227497", "migrate_mcp_to_v3"]
STUB_ROWS = [f"MC-SRE-{n}" for n in range(11, 24)] + [f"MC-TDD-{n:02d}" for n in (5, 6, 7, 8, 9, 10, 12)]
now = int(time.time())
conn = sqlite3.connect(DB, timeout=30.0, isolation_level=None)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout = 30000")
conn.execute("BEGIN IMMEDIATE")
try:
    changes = []
    def upd(sql, params, expect=1, label=""):
        cur = conn.execute(sql, params)
        assert cur.rowcount == expect, f"{label}: expected {expect} row(s), got {cur.rowcount}"
        changes.append((label, cur.rowcount))
    print("ZD-8 before:", conn.execute(ZD8).fetchone()[0])
    # (c) crash rows: MC-DSP-15 attempts 10/10 and MC-HW-55 9/10 -> BLOCKED (ch04 FR-005 poison-pill quarantine)
    for tid in ("MC-DSP-15", "MC-HW-55"):
        note = (f"\n{TAG} FAILED -> BLOCKED: attempts exhausted past the FR-005 quarantine threshold; the last attempt "
                "was an infrastructure crash of .scripts/task_work_loop.py at phase P4 (agy legacy path, llm_router "
                "cannot import v2.MODEL_REGISTRY_V2), not a TDD verdict. Re-queue manually once llm_router imports.")
        upd("UPDATE jobs SET status='BLOCKED', lease_owner=NULL, lease_expires_at=NULL, not_before=NULL, "
            "error_log = COALESCE(error_log,'') || ?, updated_at=? WHERE task_id=? AND status='FAILED'",
            (note, now, tid), label=f"{tid} FAILED->BLOCKED")
    # MC-CONCURRENCY-FIX attempts 1/10: transient infrastructure fault -> PENDING with 1 h backoff
    note = (f"\n{TAG} FAILED -> PENDING (not_before +3600 s): attempt 1 crashed in task_work_loop.py P4 "
            "(infrastructure fault, same root cause as MC-DSP-15); worker retry policy applies after the fix.")
    upd("UPDATE jobs SET status='PENDING', lease_owner=NULL, lease_expires_at=NULL, not_before=?, "
        "error_log = COALESCE(error_log,'') || ?, updated_at=? WHERE task_id=? AND status='FAILED' AND attempts < max_attempts",
        (now + 3600, note, now, "MC-CONCURRENCY-FIX"), label="MC-CONCURRENCY-FIX FAILED->PENDING")
    # (a) markdown payloads from migrate_v3_to_v412.py: wrap into the JSON object the migration intended, quarantine
    for tid in MD_ROWS:
        r = conn.execute("SELECT payload_json FROM jobs WHERE task_id=? AND status='FAILED'", (tid,)).fetchone()
        assert r is not None, tid
        raw = r["payload_json"]
        try:
            json.loads(raw); raise AssertionError(f"{tid}: payload already JSON, refusing to wrap")
        except ValueError:
            pass
        wrapped = json.dumps({"task_id": tid, "prompt": raw, "target_file": "D:/__CoChem/__agentic/v3",
                              "target": "D:/__CoChem/__agentic/v3",
                              "payload_provenance": "v3 kanban payload_uri markdown wrapped by resuscitation 2026-09-30"})
        note = (f"\n{TAG} FAILED -> BLOCKED: payload_json was raw Markdown (migrate_v3_to_v412.py swallowed the "
                "JSON error); wrapped into a JSON object {task_id, prompt, target_file}. Quarantined pending operator "
                "triage; set status='PENDING', attempts=0 to run.")
        upd("UPDATE jobs SET status='BLOCKED', payload_json=?, lease_owner=NULL, lease_expires_at=NULL, "
            "error_log = COALESCE(error_log,'') || ?, updated_at=? WHERE task_id=? AND status='FAILED'",
            (wrapped, note, now, tid), label=f"{tid} FAILED->BLOCKED (payload wrapped)")
    # (b) stub rows without target_file: quarantine, canonical payload lives in wiki/wbs/leaf_nodes/<id>.json
    for tid in STUB_ROWS:
        note = (f"\n{TAG} FAILED -> BLOCKED: stub payload (no target_file) re-inserted after migrate_v3_to_v412.py "
                "deleted ids 292-365; canonical WBS payload is wiki/wbs/leaf_nodes/" + tid + ".json; batch 07 work "
                "was already deployed live on 2026-09-29. Operator: restore payload + PENDING, or mark COMPLETED "
                "with a real result_path after running its verification_command.")
        upd("UPDATE jobs SET status='BLOCKED', lease_owner=NULL, lease_expires_at=NULL, "
            "error_log = COALESCE(error_log,'') || ?, updated_at=? WHERE task_id=? AND status='FAILED'",
            (note, now, tid), label=f"{tid} FAILED->BLOCKED")
    conn.execute("INSERT INTO recovery_telemetry (tier, action, details) VALUES (3, 'fable_resuscitation', ?)",
                 (json.dumps({"bundle": "20260930T073958Z_1", "session": "v4-1-2-e6", "rows_changed": len(changes),
                              "ts": now}),))
    after = conn.execute(ZD8).fetchone()[0]
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    print("ZD-8 inside txn:", after, counts)
    assert after == 0, "ZD-8 still non-zero, rolling back"
    assert sum(counts.values()) == 262, counts
    conn.execute("COMMIT")
    print("COMMITTED", len(changes), "row updates")
    for c in changes: print("  ", c)
except BaseException as exc:
    conn.execute("ROLLBACK"); print("ROLLED BACK:", repr(exc)); sys.exit(1)
finally:
    conn.close()
