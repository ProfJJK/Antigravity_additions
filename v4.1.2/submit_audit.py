import sqlite3
import json
import uuid

db_path = r"D:\__CoChem\__agentic\v4.1.2\job_board.db"

task_id = "MC-AUDIT-HW-01"

payload = {
    "schema_version": "4.1.1-wbs-node/1",
    "task_id": task_id,
    "title": "Audit Host Warden FastMCP and Provisioning Lifecycle",
    "domain": "host_warden",
    "target_file": "src/cochem/warden/mcp_server.py",
    "chunk_start": 1,
    "chunk_end": 75,
    "line_delta": 75,
    "instructions": "Audit the changes made to the Host Warden execution lifecycle. Specifically review src/cochem/warden/mcp_server.py to ensure the FastMCP decorators and stdio transport logic align with the ephemeral VM lifecycle architecture. Also verify the provisioning/Install-WardenDaemon.ps1 script is correctly configured to use HighestAvailable to elevate the FastMCP server, allowing it to execute the PowerShell Restart-VM command via vm_control.py securely. Provide improvements to the FastMCP lifecycle to guarantee robustness.",
    "dependencies": [],
    "verification_command": "pytest tests/test_stage6_activation_mcp.py",
    "delimited_protocol": "",
    "rule_18_compliance": {
        "w1_line_bounds_pass": True,
        "w2_diff_ratio_pass": True,
        "w3_delimited_syntax_pass": True,
        "w5_whole_file_ban_pass": True
    }
}

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute('''
    INSERT INTO jobs (task_id, job_type, status, payload_json, priority)
    VALUES (?, ?, ?, ?, ?)
''', (task_id, "macro_audit", "PENDING", json.dumps(payload), 5))

conn.commit()
conn.close()

print(f"Successfully submitted task {task_id} to V4.1.2 job_board.db!")
