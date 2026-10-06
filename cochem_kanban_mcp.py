import json
import sqlite3
import uuid
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("cochem-kanban")

@mcp.tool()
def submit_v4_task(
    task_description: str,
    priority: int = 1,
    target: str = r"D:\__CoChem\__agentic",
    agent_name: str = "cochem-coder",
    task_id: str = "",
    workflow_type: str = "micro_code",
    parent_job_id: str = None,
    fencing_token: str = None,
    dag_node: str = None
) -> str:
    """Natively submits a task to CoChem Pipeline 4.2.0 job_board.db.
    Now supports DAG structures via parent_job_id and dag_node."""
    try:
        t_id = task_id or str(uuid.uuid4())
        data = {
            "task_id": t_id,
            "job_type": workflow_type,
            "priority": priority,
            "target": target,
            "agent_name": agent_name,
            "description": task_description
        }
        if dag_node:
            data["dag_node"] = dag_node
            
        conn = sqlite3.connect(r"D:\__CoChem\__agentic\v4.2.0\job_board.db")
        c = conn.cursor()
        c.execute('''
            INSERT INTO jobs (task_id, job_type, status, payload_json, priority, parent_job_id, fencing_token)
            VALUES (?, ?, 'PENDING', ?, ?, ?, ?)
        ''', (t_id, workflow_type, json.dumps(data), priority, parent_job_id, fencing_token))
        conn.commit()
        conn.close()
        return f"Successfully submitted task {t_id} to v4.2.0 job board."
    except Exception as e:
        return f"[ERROR] Pipeline 4.2.0 submission failed: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
