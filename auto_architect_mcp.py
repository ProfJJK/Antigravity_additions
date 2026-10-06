import os
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict
import sqlite3
import uuid

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("CoChem-Auto-Architect", version="4.0.0")

KANBAN_DB_PATH = Path("D:/__CoChem/__agentic/v4.2.0/job_board.db")

WIKI_DB_PATH = Path("D:/__CoChem/__agentic/v4.2.0/db/wikirag.db")

def _get_wikirag_rules() -> str:
    if not WIKI_DB_PATH.exists():
        return ""
    try:
        conn = sqlite3.connect(WIKI_DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category, rule_text FROM rules")
        rules = cur.fetchall()
        conn.close()
        if not rules:
            return ""
        formatted = "\n\n### CoChem Dynamic WikiRAG Rules:\n"
        for cat, text in rules:
            formatted += f"- **{cat}**: {text}\n"
        return formatted
    except Exception as e:
        logger.error(f"Failed to fetch WikiRAG rules: {e}")
        return ""

@mcp.tool()
def query_wikirag() -> str:
    """
    Retrieves the legacy CoChem domain-specific rules and infrastructure directives 
    from the WikiRAG database. Use this tool whenever you are assigned a CoChem task.
    """
    return _get_wikirag_rules()

def _init_blackboard_db():
    KANBAN_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(KANBAN_DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
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
            parent_job_id TEXT,
            fencing_token TEXT,
            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            updated_at INTEGER DEFAULT (strftime('%s', 'now'))
        )
    """)
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT")
        conn.execute("ALTER TABLE jobs ADD COLUMN fencing_token TEXT")
    except sqlite3.OperationalError:
        pass
    conn.close()

def _submit_job(job_type: str, payload: dict, priority: int = 100) -> str:
    _init_blackboard_db()
    task_id = f"{job_type}-{uuid.uuid4().hex[:8].upper()}"
    conn = sqlite3.connect(KANBAN_DB_PATH, timeout=30.0, isolation_level=None)
    try:
        conn.execute(
            """
            INSERT INTO jobs (task_id, job_type, priority, status, payload_json, parent_job_id, fencing_token, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, job_type, priority, "PENDING", json.dumps(payload), payload.get("parent_job_id"), payload.get("fencing_token"), int(time.time()), int(time.time()))
        )
    except Exception as e:
        logger.error(f"Failed to submit to Task Matrix: {e}")
        raise e
    finally:
        conn.close()
    return task_id

@mcp.tool()
def trigger_srs_drafting(
    target_repo_path: str,
    initial_objective: str,
    parent_job_id: str = None,
    fencing_token: str = None,
    dag_node: str = None
) -> Dict[str, Any]:
    """
    Phase 1: Generates exhaustive suggestions. Agents will deduplicate, correlate, 
    compute Risk vs Reward & Pro vs Con, and provide plain-English summaries.
    Outputs a Markdown artifact for the user to natively comment on.
    """
    if not os.path.exists(target_repo_path):
        return {"status": "ERROR", "message": f"Pre-flight validation failed: Target repository path does not exist: {target_repo_path}"}
        
    payload = {
        "objective": initial_objective,
        "target_repo_path": target_repo_path,
        "processing_rules": {
            "exhaustion_threshold": 3,
            "group_correlated_suggestions": True,
            "compute_risk_reward_pro_con": True,
            "jargon_simplification_summaries": True,
            "parent_job_id": parent_job_id,
            "fencing_token": fencing_token,
            "dag_node": dag_node
        }
    }
    try:
        task_id = _submit_job("SRS_DRAFTING_EXHAUSTIVE", payload)
        return {"status": "SUCCESS", "task_id": task_id}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@mcp.tool()
def trigger_implementation_swarm(
    target_repo_path: str,
    srs_document_path: str,
    parent_job_id: str = None,
    fencing_token: str = None,
    dag_node: str = None
) -> Dict[str, Any]:
    """
    Phase 2: Ingests the Human-approved SRS into the Wiki RAG and initiates the Flawless Engine.
    Code is generated in strict chunks (max 200 lines).
    """
    if not os.path.exists(target_repo_path):
        return {"status": "ERROR", "message": f"Pre-flight validation failed: Target repository path does not exist: {target_repo_path}"}
    if not os.path.exists(srs_document_path):
        return {"status": "ERROR", "message": f"Pre-flight validation failed: SRS document path does not exist: {srs_document_path}"}

    payload = {
        "target_repo_path": target_repo_path,
        "srs_document_path": srs_document_path,
        "wiki_rag_ingestion": True,
        "flawless_engine_inputs": {
            "wbs_line_limit": 200,
            "max_pivot_cycles": 3
        },
        "parent_job_id": parent_job_id,
        "fencing_token": fencing_token,
        "dag_node": dag_node
    }
    try:
        task_id = _submit_job("FLAWLESS_MCTS_ROOT", payload, priority=90)
        return {"status": "SUCCESS", "master_task_id": task_id}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@mcp.tool()
def trigger_diagnostic_audit(
    target_repo_path: str,
    srs_document_path: str,
    parent_job_id: str = None,
    fencing_token: str = None,
    dag_node: str = None
) -> Dict[str, Any]:
    """
    Phase 3: The strict 50-line Diagnostic Audit Loop.
    Assigns sub-agents to read <=50 lines of code at a time to find divergence.
    Gemini 3.1 Pro synthesizes into a Chaptered Failings Document, which is split 
    and fed directly back into the Flawless Engine to fix.
    """
    if not os.path.exists(target_repo_path):
        return {"status": "ERROR", "message": f"Pre-flight validation failed: Target repository path does not exist: {target_repo_path}"}
    if not os.path.exists(srs_document_path):
        return {"status": "ERROR", "message": f"Pre-flight validation failed: SRS document path does not exist: {srs_document_path}"}

    payload = {
        "target_repo_path": target_repo_path,
        "srs_document_path": srs_document_path,
        "audit_rules": {
            "max_lines_per_subagent_chunk": 50,
            "synthesis_agent_model": "gemini-3.1-pro",
            "cross_reference_wiki_rag": True,
            "auto_queue_repairs": True
        },
        "parent_job_id": parent_job_id,
        "fencing_token": fencing_token,
        "dag_node": dag_node
    }
    try:
        task_id = _submit_job("DIAGNOSTIC_AUDIT_SWARM", payload, priority=80)
        return {"status": "SUCCESS", "master_task_id": task_id}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

def main():
    _init_blackboard_db()
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting CoChem Flawless Engine Auto-Architect MCP Server...")
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
