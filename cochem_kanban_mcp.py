import sys
import os
import json
import sqlite3
import subprocess
import warnings
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── Make the CoChem agentic root (which hosts the `v3` package) importable ──
# This file is deployed both at the repo root and mirrored to D:\__agentic\mcp.
_AGENTIC_ROOT = os.getenv("COCHEM_AGENTIC_ROOT", r"D:\__CoChem\__agentic")
for _candidate in (str(Path(__file__).resolve().parent), _AGENTIC_ROOT):
    if (Path(_candidate) / "v3" / "submit_v3.py").is_file() and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

def submit_task_detailed(*args, **kwargs):
    """Load the optional legacy queue only when called; missing queues cannot accept jobs."""
    try:
        from v3.submit_v3 import submit_task_detailed as submit
    except ModuleNotFoundError as exc:
        raise ValueError(
            "Legacy v3 queue is unavailable in this checkout. Configure the 4.2.1 "
            "cochem-codex and cochem-claude MCP servers for direct CLI handoffs."
        ) from exc
    return submit(*args, **kwargs)

import claude_subscription_manager as _claude_sub  # noqa: E402

# Create a FastMCP server
mcp = FastMCP("CoChem Kanban")

CLI_SCRIPT = os.getenv("COCHEM_KANBAN_SCRIPT", str(Path(__file__).resolve().with_name("cochem_kanban.py")))
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, CLI_SCRIPT, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        err_msg = result.stderr.strip() if result.stderr else "Non-zero exit code"
        return f"[ERROR] Workflow trigger failed (Exit Code {result.returncode}):\n{err_msg}\n{result.stdout.strip()}"
    return result.stdout


def _provider_args(provider: str, model: str) -> list:
    args = []
    if provider:
        args += ["--provider", provider]
    if model:
        args += ["--model", model]
    return args


@mcp.tool()
def submit_v3_task(
    task_description: str,
    priority: int = 0,
    target: str = r"D:\__CoChem\__agentic",
    agent_name: str = "cochem-coder",
    task_id: str = "",
    workflow_type: str = "coding",
) -> str:
    """Natively submits a task to CoChem Pipeline 3.0.0 daemon and SQLite v3.
    Writes an atomic JSON prompt into .scripts/prompts and inserts a
    kanban_tasks_v3 row with status='todo'."""
    try:
        result = submit_task_detailed(
            prompt=task_description,
            task_id=task_id or None,
            priority=priority,
            workspace=target,
            agent_name=agent_name,
            workflow_type=workflow_type,
        )
    except (ValueError, FileNotFoundError, FileExistsError, sqlite3.Error, OSError) as exc:
        return f"[ERROR] Pipeline 3.0.0 submission failed: {exc}"
    return (
        "[SUCCESS] Task queued to Pipeline 3.0.0\n"
        f"Task ID:     {result.task_id}\n"
        f"Priority:    {result.priority}\n"
        f"Prompt File: {result.prompt_file}\n"
        "Database:    kanban_tasks_v3 (status='todo')"
    )


@mcp.tool()
def submit_v2_task(
    task_description: str,
    target: str = r"D:\__CoChem\__agentic",
) -> str:
    """[DEPRECATED] Submits task to CoChem Pipeline. Redirects to Pipeline 3.0.0.
    Use submit_v3_task instead."""
    notice = ("[DEPRECATION NOTICE] 'submit_v2_task' is deprecated. "
              "Task was redirected to Pipeline 3.0.0.")
    warnings.warn(notice, DeprecationWarning, stacklevel=2)
    try:
        result = submit_task_detailed(
            prompt=task_description,
            workspace=target,
            priority=0,
        )
    except (ValueError, FileNotFoundError, FileExistsError, sqlite3.Error, OSError) as exc:
        return (
            "[DEPRECATION NOTICE] 'submit_v2_task' is deprecated. "
            "Redirection to Pipeline 3.0.0 was attempted but failed.\n"
            f"[ERROR] {exc}"
        )
    return (
        f"{notice}\n"
        f"Task ID: {result.task_id}\n"
        "Status:  Queued to .scripts/prompts and kanban_tasks_v3 (status='todo')"
    )


@mcp.tool()
def trigger_youtube_workflow(
    video_path: str = "",
    target: str = r"D:\__CoChem\__agentic",
    priority: int = 0,
) -> str:
    """Natively submits a YouTube processing task to Pipeline 3.0.0."""
    try:
        prompt_text = (
            f"Autonomous processing of OBS recording {video_path} with FERPA scrubbing and captions"
            if video_path
            else "Autonomous processing of YouTube video recording with FERPA scrubbing and captions"
        )
        result = submit_task_detailed(
            prompt=prompt_text,
            priority=priority,
            workspace=target,
            agent_name="cochem-coder",
            workflow_type="youtube_pipeline",
        )
    except (ValueError, FileNotFoundError, FileExistsError, sqlite3.Error, OSError) as exc:
        return f"[ERROR] Pipeline 3.0.0 YouTube workflow submission failed: {exc}"
    return (
        "[SUCCESS] Task queued to Pipeline 3.0.0\n"
        f"Task ID:     {result.task_id}\n"
        f"Priority:    {result.priority}\n"
        f"Prompt File: {result.prompt_file}\n"
        "Workflow:    youtube_pipeline\n"
        "Database:    kanban_tasks_v3 (status='todo')"
    )


@mcp.tool()
def get_youtube_pipeline_status(
    db_path: str = r"D:\__CoChem\__agentic\v3\cochem_kanban_v3.db",
) -> str:
    """Returns structured status of YouTube pipeline tasks and YouTube API quota."""
    p = Path(db_path)
    if not p.is_file() and os.environ.get("COCHEM_V3_DB_PATH"):
        env_p = Path(os.environ["COCHEM_V3_DB_PATH"])
        if env_p.is_file():
            p = env_p

    if not p.is_file():
        return f"[ERROR] SQLite v3 database not found at '{p}'"

    try:
        conn = sqlite3.connect(str(p), timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000;")
        cursor = conn.cursor()

        # Query quota
        cursor.execute(
            "SELECT current_usage, quota_limit FROM quota_state WHERE provider = 'youtube'"
        )
        q_row = cursor.fetchone()
        if q_row:
            cur_usage, limit = q_row[0], q_row[1]
        else:
            cur_usage, limit = 0, 10000

        # Query tasks
        cursor.execute(
            "SELECT task_id, status, priority, created_at FROM kanban_tasks_v3 "
            "WHERE workflow_type = 'youtube_pipeline' "
            "ORDER BY priority ASC, created_at DESC"
        )
        tasks = cursor.fetchall()
        conn.close()

        lines = [
            "=== YouTube Pipeline Status (Pipeline 3.0.0) ===",
            "Provider: YouTube",
            f"YouTube API Quota: {cur_usage} / {limit} units used (Remaining: {max(0, limit - cur_usage)})",
            f"Total YouTube Tasks: {len(tasks)}",
            "",
            "Tasks:",
        ]
        if not tasks:
            lines.append("  (No YouTube tasks currently in database)")
        else:
            for tid, status, prio, created in tasks:
                lines.append(f"  [{status}] prio={prio} task_id={tid} (created={created})")

        return "\n".join(lines)
    except sqlite3.Error as exc:
        return f"[ERROR] Failed to query YouTube pipeline status: {exc}"


@mcp.tool()
def trigger_srs_workflow(target: str, provider: str = "", model: str = "") -> str:
    """Triggers the CoChem SRS (Software Requirements Specification) state machine.
    provider: 'gemini' or 'claude'. model: override model string."""
    return _run_cli(*_provider_args(provider, model), "srs", "--target", target)


@mcp.tool()
def trigger_improvement_workflow(target: str, provider: str = "", model: str = "") -> str:
    """Triggers the CoChem 10-Cycle Improvement state machine.
    provider: 'gemini' or 'claude'. model: override model string."""
    return _run_cli(*_provider_args(provider, model), "improve", "--target", target)


@mcp.tool()
def trigger_coding_workflow(target: str, provider: str = "", model: str = "") -> str:
    """Triggers the CoChem Coder TDD state machine.
    provider: 'gemini' or 'claude'. model: override model string."""
    return _run_cli(*_provider_args(provider, model), "code", "--target", target)


@mcp.tool()
def trigger_publishing_workflow(target: str, provider: str = "", model: str = "") -> str:
    """Triggers the CoChem Pinnacle Publishing Swarm.
    provider: 'gemini' or 'claude'. model: override model string."""
    return _run_cli(*_provider_args(provider, model), "publish", "--target", target)


@mcp.tool()
def trigger_student_task_workflow(target: str) -> str:
    """Triggers the CoChem Student Task Kanban state machine to generate week-by-week PDF student guides."""
    STUDENT_CLI_SCRIPT = os.getenv("COCHEM_STUDENT_SCRIPT", r"D:\__CoChem\__agentic\student_kanban.py")
    result = subprocess.run(
        [sys.executable, STUDENT_CLI_SCRIPT, "srs", "--target", target],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        err_msg = result.stderr.strip() if result.stderr else "Non-zero exit code"
        return f"[ERROR] Workflow trigger failed (Exit Code {result.returncode}):\n{err_msg}\n{result.stdout.strip()}"
    return result.stdout


@mcp.tool()
def trigger_pivot_council(
    task_file: str,
    failure_trace: str = "",
    cycle: int = 1,
    provider: str = "",
    model: str = "",
) -> str:
    """Activates the 3-agent Pivot Council for a failed Kanban task.
    task_file: path to the failed *_prompt.json file.
    failure_trace: error trace text (or leave blank to read from task log).
    cycle: pivot cycle number 1-3 (triggers HARD_ABORT after MAX_PIVOT_CYCLES).
    provider/model: optional routing overrides for the council agents."""
    args = [*_provider_args(provider, model), "pivot",
            "--task-file", task_file,
            "--cycle", str(cycle)]
    if failure_trace:
        args += ["--failure-trace", failure_trace]
    return _run_cli(*args)


@mcp.tool()
def get_claude_subscription_health() -> str:
    """Return native Claude subscription login status without credentials or inferred token TTLs."""
    return json.dumps(_claude_sub.get_claude_subscription_health(), sort_keys=True)


@mcp.tool()
def get_claude_status() -> str:
    """Returns Claude subscription status: token expiry, rate limit tier,
    pipeline state, daemon health, and last rate-limit error if any.
    Claude Max has no 'credits' — this surfaces what IS detectable."""
    lines = []

    # ── 1. Token / subscription info via claude_subscription_manager ─────────
    health = _claude_sub.get_claude_subscription_health()
    lines.append("=== Claude Auth ===")
    if health["status"] == "ERROR":
        lines.append(f"[ERROR checking CLI login: {health['error']}]")
    else:
        icon = {"HEALTHY": "✅", "EXPIRING": "⚠️", "EXPIRED": "❌ EXPIRED"}.get(health["status"], "?")
        lines.append(f"Native CLI login: {icon}")
        lines.append(f"Plan: {health['plan']} / {health['tier']}")
    lines.append(f"Status:   {health['status']}  action={health['recommended_action']}")

    # ── 2. Daemon health ──────────────────────────────────────────────────────
    lines.append("")
    lines.append("=== Pipeline Daemon ===")
    log_path = Path(r"d:\__CoChem\__agentic\.scripts\task_work_loop.log")
    state_path = Path(r"d:\__CoChem\__agentic\.scripts\work_loop_state.json")

    if log_path.exists():
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        last_entry = tail[-1] if tail else "no entries"
        lines.append(f"Log last entry:  {last_entry[:120]}")
        recent = "\n".join(tail)
        if "401" in recent or "expired" in recent.lower():
            lines.append("⚠️  Recent 401/expired token error detected in log")
        elif "429" in recent or "rate limit" in recent.lower():
            lines.append("⚠️  Recent 429 rate-limit error detected in log")
        elif "HARD_ABORT" in recent:
            lines.append("❌  Recent HARD_ABORT in log — check pivot cycles")
        elif "PASS" in recent or "QUOTA-ROUTER" in recent:
            lines.append("✅  No recent errors")
    else:
        lines.append("⚠️  Log file not found — daemon may not be running")

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            lines.append(f"Active task:     {state.get('active_task', 'none')}")
            lines.append(f"Completed tasks: {len(state.get('completed_tasks', []))}")
        except Exception as e:
            lines.append(f"[state file unreadable: {e}]")

    # ── 3. Kanban DB — last 5 task updates (read-only) ───────────────────────
    lines.append("")
    lines.append("=== Recent Kanban Tasks ===")
    db_path = r"d:\__CoChem\cochem_kanban.db"
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT task_id, status, assigned_provider, updated_at "
            "FROM kanban_tasks ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        con.close()
        for r in rows:
            lines.append(f"  {r[3][:16]}  [{r[1]:12}]  {r[2] or '-':20}  {r[0][:50]}")
    except Exception as e:
        lines.append(f"  [DB error: {e}]")

    # ── 4. Token refresh log ──────────────────────────────────────────────────
    refresh_log = Path(r"d:\__CoChem\__agentic\.scripts\claude_token_refresh.log")
    if refresh_log.exists():
        lines.append("")
        lines.append("=== Last Token Refresh ===")
        last_refresh = refresh_log.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        lines.extend(f"  {l}" for l in last_refresh)

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
