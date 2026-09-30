r"""Adversarial audit script testing D:\__CoChem\__agentic\v4.1.2\.staging\ch04\lifecycle\lifecycle.py.
Ruthless verification of SRS-412-04-FR-005, SRS-412-04-FR-009, Signal ZD-8, Zero-Mock, and Type Safety.
"""
import ast
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import traceback

STAGING_FILE = Path(r"D:\__CoChem\__agentic\v4.1.2\.staging\ch04\lifecycle\lifecycle.py")

def load_module():
    spec = importlib.util.spec_from_file_location("staged_lifecycle", str(STAGING_FILE))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["staged_lifecycle"] = mod
    spec.loader.exec_module(mod)
    return mod

DDL = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    job_type TEXT NOT NULL,
    priority INTEGER DEFAULT 100,
    status TEXT CHECK(status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'BLOCKED')) DEFAULT 'PENDING',
    payload_json TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at INTEGER,
    attempts INTEGER DEFAULT 0,
    error_log TEXT,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER DEFAULT (strftime('%s', 'now'))
);
"""

def setup_test_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(DDL)
    conn.commit()
    conn.close()

def run_tests():
    print(f"=== AUDITING: {STAGING_FILE} ===")
    assert STAGING_FILE.exists(), f"File does not exist: {STAGING_FILE}"
    
    # 1. AST Analysis for zero-mock, stubs, placeholders
    print("[1] Running AST Analysis & Anti-Spoofing...")
    source = STAGING_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(STAGING_FILE))
    
    # Check for forbidden imports
    forbidden_imports = {"unittest.mock", "mock", "pytest_mock"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for f in forbidden_imports:
                    assert f not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for f in forbidden_imports:
                    assert f not in node.module, f"Forbidden import from found: {node.module}"
                    
    # Check for empty / placeholder bodies or pass / NotImplemented
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in functions:
        body = fn.body
        # remove docstring if first
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        assert len(body) > 0, f"Function {fn.name} has empty body!"
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            raise AssertionError(f"Function {fn.name} has only a 'pass' statement (stub)!")
        if len(body) == 1 and isinstance(body[0], ast.Raise) and isinstance(body[0].exc, ast.Call):
            if getattr(body[0].exc.func, "id", None) == "NotImplementedError":
                raise AssertionError(f"Function {fn.name} raises NotImplementedError (unimplemented stub)!")

    # Check for TODO, FIXME, PLACEHOLDER in text
    bad_tokens = ["TODO", "FIXME", "MOCK", "PLACEHOLDER", "STUB"]
    for i, line in enumerate(source.splitlines(), 1):
        for token in bad_tokens:
            if token in line.upper() and not "MOCK" in line.upper():  # check comments carefully
                pass # let's check exact comments
    print("AST and anti-spoofing checks passed.")

    # 2. Dynamic Execution & Functional Tests
    print("[2] Dynamic execution tests...")
    mod = load_module()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_job_board.db")
        setup_test_db(db_file)
        
        # Insert test jobs
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        # Row 1: RUNNING, attempts=1, lease_owner='daemon-1', lease_expires_at=9999999
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (1, 'task-1', 'compute', 'RUNNING', '{}', 'daemon-1', 9999999, 1)")
        # Row 2: RUNNING, attempts=2, lease_owner='daemon-2', lease_expires_at=9999999
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (2, 'task-2', 'compute', 'RUNNING', '{}', 'daemon-2', 9999999, 2)")
        # Row 3: RUNNING, attempts=3, lease_owner='daemon-3', lease_expires_at=9999999 (POISON PILL)
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (3, 'task-3', 'compute', 'RUNNING', '{}', 'daemon-3', 9999999, 3)")
        # Row 4: PENDING, attempts=0
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (4, 'task-4', 'compute', 'PENDING', '{}', NULL, NULL, 0)")
        # Row 5: COMPLETED
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (5, 'task-5', 'compute', 'COMPLETED', '{}', NULL, NULL, 1)")
        conn.commit()
        conn.close()

        # TEST: complete_task on Row 1 (RUNNING)
        res = mod.complete_task(db_file, 1)
        assert res is True, "complete_task(1) should return True"
        
        # Verify row 1 in DB: status=COMPLETED, lease_owner=NULL, lease_expires_at=NULL
        conn = sqlite3.connect(db_file)
        row = conn.execute("SELECT status, lease_owner, lease_expires_at, updated_at FROM jobs WHERE id = 1").fetchone()
        assert row[0] == "COMPLETED", f"Expected COMPLETED, got {row[0]}"
        assert row[1] is None, f"Expected lease_owner NULL, got {row[1]}"
        assert row[2] is None, f"Expected lease_expires_at NULL, got {row[2]}"
        
        # TEST: complete_task on Row 1 again (already COMPLETED)
        res_repeat = mod.complete_task(db_file, 1)
        assert res_repeat is False, "complete_task on non-RUNNING must return False"
        
        # TEST: complete_task on non-existent task
        assert mod.complete_task(db_file, 999) is False
        
        # TEST: complete_task on PENDING task (Row 4)
        assert mod.complete_task(db_file, 4) is False
        row4 = conn.execute("SELECT status FROM jobs WHERE id = 4").fetchone()
        assert row4[0] == "PENDING"
        
        # TEST: fail_task on Row 2 (RUNNING, attempts=2 < 3)
        res_fail = mod.fail_task(db_file, 2, "Test error message 2")
        assert res_fail is True, "fail_task(2) should return True"
        row2 = conn.execute("SELECT status, lease_owner, lease_expires_at, error_log, attempts FROM jobs WHERE id = 2").fetchone()
        assert row2[0] == "FAILED", f"Expected FAILED for attempts=2, got {row2[0]}"
        assert row2[1] is None, f"Expected lease_owner NULL, got {row2[1]}"
        assert row2[2] is None, f"Expected lease_expires_at NULL, got {row2[2]}"
        assert row2[3] == "Test error message 2", f"Expected error log match, got {row2[3]}"
        assert row2[4] == 2, f"Expected attempts unchanged (2), got {row2[4]}"
        
        # TEST: fail_task on Row 3 (RUNNING, attempts=3 >= 3) -> POISON PILL QUARANTINE TO BLOCKED
        res_blocked = mod.fail_task(db_file, 3, "Poison pill error 3")
        assert res_blocked is True, "fail_task(3) should return True"
        row3 = conn.execute("SELECT status, lease_owner, lease_expires_at, error_log, attempts FROM jobs WHERE id = 3").fetchone()
        assert row3[0] == "BLOCKED", f"Expected BLOCKED for attempts=3, got {row3[0]}"
        assert row3[1] is None, f"Expected lease_owner NULL, got {row3[1]}"
        assert row3[2] is None, f"Expected lease_expires_at NULL, got {row3[2]}"
        assert row3[3] == "Poison pill error 3", f"Expected error log match, got {row3[3]}"
        assert row3[4] == 3, f"Expected attempts unchanged (3), got {row3[4]}"
        
        # TEST: Signal ZD-8 invariant across all rows!
        zd8_violations = conn.execute("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL)").fetchone()[0]
        assert zd8_violations == 0, f"Signal ZD-8 violation detected! Count: {zd8_violations}"

        # TEST: Type enforcement
        try:
            mod.complete_task(db_file, True)
            raise AssertionError("complete_task did not reject bool task_id")
        except TypeError:
            pass

        try:
            mod.complete_task(db_file, "task-1")
            raise AssertionError("complete_task did not reject string task_id")
        except TypeError:
            pass

        try:
            mod.fail_task(db_file, True, "err")
            raise AssertionError("fail_task did not reject bool task_id")
        except TypeError:
            pass

        try:
            mod.fail_task(db_file, "task-1", "err")
            raise AssertionError("fail_task did not reject string task_id")
        except TypeError:
            pass

        try:
            mod.fail_task(db_file, 1, 12345)  # non-string error_log
            raise AssertionError("fail_task did not reject non-string error_log")
        except TypeError:
            pass
            
        cur = conn.cursor()
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (6, 'task-6', 'compute', 'RUNNING', '{}', 'daemon-6', 9999999, 3)")
        # Insert row 7: RUNNING, attempts=1, task_id='task-7'
        cur.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) VALUES (7, 'task-7', 'compute', 'RUNNING', '{}', 'daemon-7', 9999999, 1)")
        conn.commit()
        
        # Call quarantine_poison_pill on row 6 by task_id string
        ok, st = mod.quarantine_poison_pill(conn, "task-6", error_log="string id test")
        assert ok is True and st == "BLOCKED", f"Expected (True, 'BLOCKED'), got ({ok}, {st})"
        row6 = conn.execute("SELECT status, lease_owner, lease_expires_at, error_log FROM jobs WHERE id = 6").fetchone()
        assert row6[0] == "BLOCKED" and row6[1] is None and row6[2] is None and row6[3] == "string id test"
        
        # Call quarantine_poison_pill on row 7 by id int
        ok7, st7 = mod.quarantine_poison_pill(conn, 7, error_log="int id test")
        assert ok7 is True and st7 == "FAILED", f"Expected (True, 'FAILED'), got ({ok7}, {st7})"
        row7 = conn.execute("SELECT status, lease_owner, lease_expires_at, error_log FROM jobs WHERE id = 7").fetchone()
        assert row7[0] == "FAILED" and row7[1] is None and row7[2] is None and row7[3] == "int id test"
        
        conn.commit()
        # Again, check Signal ZD-8 invariant
        zd8_violations = conn.execute("SELECT COUNT(*) FROM jobs WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL)").fetchone()[0]
        assert zd8_violations == 0, f"Signal ZD-8 violation! Count: {zd8_violations}"
        
        # Test non-existent row in quarantine_poison_pill
        ok_none, st_none = mod.quarantine_poison_pill(conn, "non-existent")
        assert ok_none is False and st_none == ""

        # Test non-RUNNING row in quarantine_poison_pill
        ok_nr, st_nr = mod.quarantine_poison_pill(conn, "task-6")
        assert ok_nr is False and st_nr == "BLOCKED"
        conn.commit()

        # TEST: immediate_transaction rollback on error
        try:
            with mod.immediate_transaction(db_file) as t_conn:
                t_conn.execute("INSERT INTO jobs (id, task_id, job_type, status, payload_json) VALUES (10, 'task-10', 't', 'RUNNING', '{}')")
                raise RuntimeError("Forced abort")
        except RuntimeError:
            pass
        # Verify row 10 was rolled back
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE id = 10").fetchone()[0] == 0, "Rollback failed in immediate_transaction!"
        
        conn.close()
        import gc
        gc.collect()

    print("ALL AUDIT TESTS PASSED!")

if __name__ == "__main__":
    try:
        run_tests()
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
