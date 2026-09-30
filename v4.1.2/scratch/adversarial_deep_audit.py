r"""Adversarial Deep Audit for D:\__CoChem\__agentic\v4.1.2\.staging\ch04\lifecycle\lifecycle.py.
Torture testing concurrency, edge cases, SQL injections, boundary values, and Signal ZD-8.
"""
from __future__ import annotations

import ast
import concurrent.futures
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

STAGED_PATH = Path(r"D:\__CoChem\__agentic\v4.1.2\.staging\ch04\lifecycle\lifecycle.py")

def load_staged_module():
    spec = importlib.util.spec_from_file_location("cochem.blackboard.lifecycle", str(STAGED_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cochem.blackboard.lifecycle"] = mod
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

def create_db(path: Path) -> str:
    db_file = str(path / "job_board.db")
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute(DDL)
    conn.commit()
    conn.close()
    return db_file

def audit_ast():
    print("[AST Audit] Checking code structure...")
    source = STAGED_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod_name = getattr(node, "module", "") or ""
            names = [alias.name for alias in node.names]
            for n in [mod_name] + names:
                assert "mock" not in n.lower(), f"Forbidden mock library imported: {n}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            non_doc_body = [
                s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
            ]
            assert non_doc_body, f"Function {node.name} is completely empty!"
            if len(non_doc_body) == 1:
                stmt = non_doc_body[0]
                if isinstance(stmt, ast.Pass):
                    raise AssertionError(f"Function {node.name} is a dummy pass stub!")
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
                    raise AssertionError(f"Function {node.name} is an ellipsis stub (...)!")
                if isinstance(stmt, ast.Raise):
                    if isinstance(stmt.exc, ast.Call) and getattr(stmt.exc.func, "id", None) == "NotImplementedError":
                        raise AssertionError(f"Function {node.name} raises NotImplementedError!")

    print("[AST Audit] AST analysis verified: 0 mocks, 0 stubs, 0 NotImplemented.")

def test_sql_injection_and_boundaries(mod, db_path: str):
    print("[Security & Boundaries] Testing SQL injection and edge cases...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Insert test task
    cur.execute(
        "INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) "
        "VALUES (100, 'task-sqli', 'test', 'RUNNING', '{}', 'daemon-sql', 9999999, 1)"
    )
    conn.commit()
    conn.close()

    # 1. SQL injection in error_log
    malicious_log = "'); DROP TABLE jobs; -- ' OR '1'='1"
    ok = mod.fail_task(db_path, 100, malicious_log)
    assert ok is True

    conn = sqlite3.connect(db_path)
    # Ensure jobs table still exists!
    tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    assert tbl is not None, "SQL Injection vulnerability! jobs table dropped!"
    row = conn.execute("SELECT status, error_log, lease_owner, lease_expires_at FROM jobs WHERE id = 100").fetchone()
    assert row[0] == "FAILED"
    assert row[1] == malicious_log
    assert row[2] is None and row[3] is None
    conn.close()

    # 2. Pathlib.Path db_path
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) "
        "VALUES (101, 'task-path', 'test', 'RUNNING', '{}', 'daemon-path', 9999999, 1)"
    )
    conn.commit()
    conn.close()
    ok_path = mod.complete_task(Path(db_path), 101)
    assert ok_path is True

    # 3. Unicode and huge error log (100KB)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) "
        "VALUES (102, 'task-unicode', 'test', 'RUNNING', '{}', 'daemon-u', 9999999, 1)"
    )
    conn.commit()
    conn.close()
    huge_log = "🔥" * 20000 + " 🚨 Stacktrace: \u0000 harmless null escaped \n\t"
    ok_huge = mod.fail_task(Path(db_path), 102, huge_log)
    assert ok_huge is True
    conn = sqlite3.connect(db_path)
    r = conn.execute("SELECT error_log FROM jobs WHERE id = 102").fetchone()[0]
    assert r == huge_log
    conn.close()

    print("[Security & Boundaries] Passed cleanly.")

def test_concurrency_race_conditions(mod, db_path: str):
    print("[Concurrency Audit] Testing concurrent completion/failure contention...")
    task_count = 50
    conn = sqlite3.connect(db_path)
    for i in range(200, 200 + task_count):
        conn.execute(
            "INSERT INTO jobs (id, task_id, job_type, status, payload_json, lease_owner, lease_expires_at, attempts) "
            f"VALUES ({i}, 'task-{i}', 'stress', 'RUNNING', '{{}}', 'daemon-w', 9999999, 2)"
        )
    conn.commit()
    conn.close()

    # 50 threads trying to complete and 50 threads trying to fail the SAME tasks concurrently
    def try_complete(tid):
        return mod.complete_task(db_path, tid)

    def try_fail(tid):
        return mod.fail_task(db_path, tid, f"concurrent fail {tid}")

    results_complete = []
    results_fail = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures_complete = {executor.submit(try_complete, tid): tid for tid in range(200, 200 + task_count)}
        futures_fail = {executor.submit(try_fail, tid): tid for tid in range(200, 200 + task_count)}

        for f in concurrent.futures.as_completed(futures_complete):
            results_complete.append((futures_complete[f], f.result()))
        for f in concurrent.futures.as_completed(futures_fail):
            results_fail.append((futures_fail[f], f.result()))

    # Verify that for each task, EXACTLY ONE operation succeeded
    success_map = {tid: 0 for tid in range(200, 200 + task_count)}
    for tid, res in results_complete:
        if res:
            success_map[tid] += 1
    for tid, res in results_fail:
        if res:
            success_map[tid] += 1

    for tid, count in success_map.items():
        assert count == 1, f"Task {tid} had {count} winning transitions! Expected strictly 1."

    # Verify Signal ZD-8 invariant for all tasks
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, status, lease_owner, lease_expires_at FROM jobs WHERE id >= 200 AND id < 250").fetchall()
    for row in rows:
        assert row[1] in ("COMPLETED", "FAILED", "BLOCKED"), f"Invalid terminal status: {row[1]}"
        assert row[2] is None, f"Signal ZD-8 violation on task {row[0]}: lease_owner is {row[2]}"
        assert row[3] is None, f"Signal ZD-8 violation on task {row[0]}: lease_expires_at is {row[3]}"
    conn.close()
    print("[Concurrency Audit] Concurrency passed: zero double-transitions, zero ZD-8 violations.")

def main():
    print("=== STARTING ADVERSARIAL DEEP AUDIT ===")
    audit_ast()
    mod = load_staged_module()
    with tempfile.TemporaryDirectory() as td:
        db_path = create_db(Path(td))
        test_sql_injection_and_boundaries(mod, db_path)
        test_concurrency_race_conditions(mod, db_path)
    print("=== ADVERSARIAL DEEP AUDIT COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()
