"""Stage 3 & 3.5: Micro-Task Queuing & Batch Manifest Generator.

Milestone: M4
Covers:
- Feature 10: Stage 3 JSON Payload Formatting (schema: 4.1.1-wbs-node/1)
- Feature 11: Stage 3 Blackboard Injection (raw SQLite INSERT into job_board.db)
- Feature 12: Stage 3.5 Batch Manifest Generation (B07..B10 manifests <=20 tasks, mirrored to WBS_Micro_Prompts)

Maintains real SQLite WAL state, enforces unique task_id constraint, verifies ZD-8 invariant.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

CANONICAL_SCHEMA_VERSION = "4.1.1-wbs-node/1"
BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
LEAF_NODES_DIR = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"
BATCHES_DIR = BASE_DIR / "wiki" / "wbs" / "batches"
MICRO_PROMPTS_DIR = BASE_DIR / "WBS_Micro_Prompts"
DB_PATH = BASE_DIR / "job_board.db"


def load_canonical_leaf_nodes() -> list[dict[str, Any]]:
    """Loads and validates all 62 leaf node manifests from wiki/wbs/leaf_nodes/."""
    json_files = sorted(list(LEAF_NODES_DIR.glob("*.json")))
    if len(json_files) != 62:
        raise RuntimeError(f"Expected 62 leaf nodes in {LEAF_NODES_DIR}, found {len(json_files)}")
    
    nodes: list[dict[str, Any]] = []
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        if data.get("schema_version") != CANONICAL_SCHEMA_VERSION:
            raise ValueError(f"Invalid schema version in {jf.name}: {data.get('schema_version')}")
        nodes.append(data)
    return nodes


def inject_blackboard_tasks(nodes: list[dict[str, Any]]) -> int:
    """Injects 62 PENDING tasks into job_board.db using raw SQLite transactions."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    cur = conn.cursor()
    
    # Check baseline count
    cur.execute("SELECT count(*) FROM jobs")
    initial_count = cur.fetchone()[0]
    
    cur.execute("BEGIN IMMEDIATE;")
    try:
        inserted = 0
        for node in nodes:
            task_id = node["task_id"]
            payload_str = json.dumps(node, indent=2)
            
            # Check if task already exists
            cur.execute("SELECT id FROM jobs WHERE task_id = ?", (task_id,))
            existing = cur.fetchone()
            if existing:
                continue
                
            cur.execute(
                """
                INSERT INTO jobs (
                    task_id, job_type, status, payload_json, priority,
                    attempts, max_attempts, lease_owner, lease_expires_at,
                    created_at, updated_at
                ) VALUES (
                    ?, 'micro_code', 'PENDING', ?, 1,
                    0, 10, NULL, NULL,
                    strftime('%s', 'now'), strftime('%s', 'now')
                )
                """,
                (task_id, payload_str)
            )
            inserted += 1
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise RuntimeError(f"Blackboard injection failed: {e}") from e
        
    # Verify post-injection state
    cur.execute("SELECT count(*) FROM jobs")
    final_count = cur.fetchone()[0]
    
    cur.execute("SELECT count(*) FROM jobs WHERE status = 'PENDING'")
    pending_count = cur.fetchone()[0]
    
    # Assert Signal ZD-8 invariant is 0
    zd8_query = """
    SELECT COUNT(*) FROM jobs
    WHERE (status != 'RUNNING' AND lease_owner IS NOT NULL)
       OR status = 'FAILED'
       OR (status = 'PENDING' AND attempts >= max_attempts)
    """
    cur.execute(zd8_query)
    zd8_count = cur.fetchone()[0]
    if zd8_count != 0:
        conn.close()
        raise RuntimeError(f"Signal ZD-8 violation after injection: count={zd8_count}")
        
    conn.close()
    return inserted


def generate_batch_manifests(nodes: list[dict[str, Any]]) -> dict[str, Path]:
    """Clusters 62 leaf nodes into Batches 7, 8, 9, 10 (<=20 tasks each) and mirrors manifests."""
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    MICRO_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Define batch assignments based on canonical task specification
    node_map = {n["task_id"]: n for n in nodes}
    
    b7_ids = [f"MC-HW-{i:02d}" for i in range(42, 61)] # 19 tasks
    b8_ids = [f"MC-HW-{i:02d}" for i in range(61, 70)] + [f"MC-DSP-{i:02d}" for i in range(1, 12)] # 20 tasks
    b9_ids = [f"MC-DSP-{i:02d}" for i in range(12, 32)] # 20 tasks
    b10_ids = [f"MC-DSP-{i:02d}" for i in range(32, 35)] # 3 tasks
    
    batches = [
        ("B07", b7_ids),
        ("B08", b8_ids),
        ("B09", b9_ids),
        ("B10", b10_ids),
    ]
    
    results: dict[str, Path] = {}
    for batch_id, task_ids in batches:
        batch_tasks = [node_map[tid] for tid in task_ids if tid in node_map]
        if len(batch_tasks) > 20:
            raise ValueError(f"Batch {batch_id} exceeds limit of 20 tasks: {len(batch_tasks)}")
            
        affected_files = sorted(list({t["target_file"] for t in batch_tasks}))
        
        manifest_data = {
            "batch_id": batch_id,
            "task_count": len(batch_tasks),
            "tasks": batch_tasks,
            "affected_files": affected_files
        }
        
        manifest_json = json.dumps(manifest_data, indent=2)
        
        # Write to wiki/wbs/batches/ atomically
        batch_file = BATCHES_DIR / f"{batch_id}.json"
        tmp_batch = BATCHES_DIR / f"{batch_id}.json.tmp"
        tmp_batch.write_text(manifest_json, encoding="utf-8")
        tmp_batch.replace(batch_file)
        
        # Mirror to WBS_Micro_Prompts/ atomically
        mirror_file = MICRO_PROMPTS_DIR / f"{batch_id}.json"
        tmp_mirror = MICRO_PROMPTS_DIR / f"{batch_id}.json.tmp"
        tmp_mirror.write_text(manifest_json, encoding="utf-8")
        tmp_mirror.replace(mirror_file)
        
        results[batch_id] = batch_file
        
    return results


if __name__ == "__main__":
    print("Executing Stage 3 & 3.5: Micro-Task Queuing & Batch Manifests...")
    nodes = load_canonical_leaf_nodes()
    print(f"Loaded {len(nodes)} canonical leaf nodes.")
    
    inserted = inject_blackboard_tasks(nodes)
    print(f"Injected {inserted} tasks into job_board.db.")
    
    manifests = generate_batch_manifests(nodes)
    print(f"Generated {len(manifests)} batch manifests in {BATCHES_DIR} and mirrored to {MICRO_PROMPTS_DIR}.")
