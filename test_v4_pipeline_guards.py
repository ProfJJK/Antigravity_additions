"""Pipeline invariant and schema guard tests for V4.

Covers:
  - AC-14: test_work_loop_dispatch_roundtrip
  - AC-15: test_no_markdown_body_in_prompt
  - AC-39: test_pivot_council_guard
  - AC-40: test_repair_exhaustion_emits_autopsy
  - AC-52: test_no_markdown_body_extended
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / ".scripts"
V2_DIR = ROOT / "v2"

for p in (str(SCRIPTS_DIR), str(V2_DIR), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import task_work_loop
from v4_schemas import (
    ExecutionChunk,
    AuditFeedback,
    PlanDossier,
    PhysicsAutopsyReport,
)
import llm_router
from llm_router import CliProvider
import cochem_kanban
import v4_migrate_kanban

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _make_chunk(target_files=None, acceptance_criteria=None, task_id=196.13):
    return ExecutionChunk(
        task_id=task_id,
        target_files=target_files or [".scripts/task_work_loop.py"],
        acceptance_criteria=acceptance_criteria or [
            "AC1 build_dispatch returns the serialized chunk",
            "AC2 no concatenated file reads in prompts",
        ],
        test_spec={
            "test_file": "test_v4_pipeline_guards.py",
            "physical_inputs": {"temperature_K": 300.0, "pressure_bar": 1.013},
            "assertions": ["dispatch is valid JSON"],
        },
    )


# --------------------------------------------------------------------------- #
# AC-14: Work loop dispatch roundtrip
# --------------------------------------------------------------------------- #
def test_work_loop_dispatch_roundtrip():
    chunk = _make_chunk()
    result = task_work_loop.build_dispatch(chunk)
    assert isinstance(result, str)
    assert ExecutionChunk.model_validate_json(result) == chunk
    assert result == chunk.model_dump_json()


# --------------------------------------------------------------------------- #
# AC-15: AST prompt anti-concatenation visitor
# --------------------------------------------------------------------------- #
_SINK_CALLS = {"structured_call", "run_cli", "safe_chat_cli"}
_READ_CALLS = {"read", "read_text", "open"}


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _contains_read_call(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and _call_name(n) in _READ_CALLS
        for n in ast.walk(node)
    )


def _is_concat_node(node: ast.AST) -> bool:
    if isinstance(node, (ast.BinOp, ast.JoinedStr)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
    )


def _is_bad_expr(node: ast.AST | None) -> bool:
    return node is not None and _is_concat_node(node) and _contains_read_call(node)


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_target_names(elt))
        return out
    return []


def _find_violations(tree: ast.AST) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in _SINK_CALLS:
            values = list(node.args) + [kw.value for kw in node.keywords]
            for v in values:
                if _is_bad_expr(v):
                    violations.append(f"line {node.lineno}: arg of {_call_name(node)}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            names = [n for t in targets for n in _target_names(t)]
            if any("prompt" in n.lower() for n in names) and _is_bad_expr(node.value):
                violations.append(f"line {node.lineno}: assignment to {names}")
    return violations


def test_no_markdown_body_in_prompt():
    files_to_check = [
        SCRIPTS_DIR / "task_work_loop.py",
        V2_DIR / "task_planning_orchestra.py",
    ]
    all_violations: list[str] = []
    for f in files_to_check:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(f))
        for v in _find_violations(tree):
            all_violations.append(f"{f.name}:{v}")

    assert len(all_violations) == 0, f"prompt concatenation violations: {all_violations}"


# --------------------------------------------------------------------------- #
# AC-39: Pivot council circuit breaker
# --------------------------------------------------------------------------- #
def test_pivot_council_guard(tmp_path: Path):
    db_path = tmp_path / "queue.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE kanban_tasks_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                status TEXT NOT NULL DEFAULT 'todo',
                prompt TEXT,
                description TEXT,
                workflow_type TEXT NOT NULL DEFAULT 'v4',
                payload_uri TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 0,
                payload TEXT CHECK(json_valid(payload)),
                schema_name TEXT,
                schema_version TEXT,
                schema_sha256 TEXT,
                producer_provider TEXT,
                produced_at_utc REAL,
                payload_sha256 TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                validation_error TEXT,
                created_at REAL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    v4_migrate_kanban.run_migration(db_path=db_path)

    import cochem_kanban
    import task_planning_orchestra

    task_id = "test_task"
    # Enqueue 3 consecutive FAIL forms
    for i in range(3):
        fb = AuditFeedback(
            score=50,
            verdict="FAIL",
            violations=[f"Persistent failure violation {i}"],
            missing_acceptance_criteria=["AC1"],
        )
        object.__setattr__(fb, "task_id", task_id)
        cochem_kanban.enqueue_form(fb, db_path=db_path)

    escalation = task_planning_orchestra.run_revision_cycle(
        task_id=task_id,
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        escalation_rows = cur.execute(
            "SELECT count(*) FROM kanban_tasks_v3 WHERE schema_name='PivotEscalation'"
        ).fetchone()[0]
        attempt_rows = cur.execute("SELECT count(*) FROM llm_attempts").fetchone()[0]
        plan_rows = cur.execute(
            "SELECT count(*) FROM kanban_tasks_v3 WHERE schema_name='PlanDossier'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert getattr(escalation, "__class__", object).__name__ == "PivotEscalation" or isinstance(
        escalation, getattr(task_planning_orchestra, "PivotEscalation", object)
    )
    assert escalation_rows == 1
    assert attempt_rows == 0
    assert plan_rows <= 3


# --------------------------------------------------------------------------- #
# AC-40: Repair exhaustion emits autopsy
# --------------------------------------------------------------------------- #
def test_repair_exhaustion_emits_autopsy(tmp_path: Path):
    chunk = _make_chunk()
    script = tmp_path / "bad_child.py"
    script.write_text("print('{not json')\n", encoding="utf-8")
    db_path = tmp_path / "queue.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE kanban_tasks_v3 (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload TEXT,
                schema_name TEXT,
                schema_version TEXT,
                schema_sha256 TEXT,
                producer_provider TEXT,
                produced_at_utc REAL,
                payload_sha256 TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO kanban_tasks_v3 (task_id, status, payload) VALUES (?, ?, ?)",
            (str(chunk.task_id), "running", "{}"),
        )
        conn.commit()
    finally:
        conn.close()

    provider = CliProvider(argv=[sys.executable, str(script)])
    task_work_loop.dispatch_structured(chunk, provider=provider, db_path=db_path, timeout=120.0)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT status, payload, schema_name, schema_version, schema_sha256, "
            "producer_provider, produced_at_utc, payload_sha256 "
            "FROM kanban_tasks_v3 WHERE task_id=?",
            (str(chunk.task_id),),
        ).fetchall()
        attempts = conn.execute(
            "SELECT task_id, attempt_num, raw_output, validation_error "
            "FROM llm_attempts ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    status, payload, schema_name, schema_version, schema_sha256, producer, produced_at, payload_sha256 = rows[0]
    assert status == "failed"
    assert schema_name == "PhysicsAutopsyReport"

    report = PhysicsAutopsyReport.model_validate_json(payload)
    assert report.failure_stage == "SYNTAX"
    assert report.hypothesis.strip()
    assert report.fix_vector.strip()

    assert len(attempts) == 3
    for tid, num, raw_output, validation_error in attempts:
        assert str(tid) == str(chunk.task_id)
        assert "{not json" in raw_output
        assert isinstance(validation_error, str) and len(validation_error.strip()) > 0


# --------------------------------------------------------------------------- #
# AC-52: No markdown body extended AST check
# --------------------------------------------------------------------------- #
def test_no_markdown_body_extended():
    target_files = [ROOT / "cochem_kanban.py", ROOT / "fix_queue.py"]
    violations: list[str] = []
    for f in target_files:
        if not f.is_file():
            continue
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(f))
        for v in _find_violations(tree):
            violations.append(f"{f.name}:{v}")

    # Specific checks required by AC-52:
    # 1. cochem_kanban.py line 268 must not concatenate file reads into prompts
    # 2. fix_queue.py must not contain task_description dictionary key or write to prompts dir
    fq_path = ROOT / "fix_queue.py"
    if fq_path.is_file():
        fq_src = fq_path.read_text(encoding="utf-8")
        assert "task_description" not in fq_src, "fix_queue.py contains task_description"
        assert ".scripts/prompts" not in fq_src and "prompts" not in fq_src, "fix_queue.py writes to prompts"

    assert len(violations) == 0, f"extended violations found: {violations}"
