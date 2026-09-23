import sys
import os
import subprocess
from mcp.server.fastmcp import FastMCP

# Create a FastMCP server
mcp = FastMCP("CoChem Kanban")

CLI_SCRIPT = os.getenv("COCHEM_KANBAN_SCRIPT", r"D:\__CoChem\__agentic\cochem_kanban.py")


def _run_cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, CLI_SCRIPT, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        creationflags=0x08000000,
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
        creationflags=0x08000000,
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
def get_claude_status() -> str:
    """Returns Claude subscription status: token expiry, rate limit tier,
    pipeline state, daemon health, and last rate-limit error if any.
    Claude Max has no 'credits' — this surfaces what IS detectable."""
    import json, pathlib, datetime, sqlite3

    lines = []

    # ── 1. Token / subscription info from .credentials.json ──────────────────
    creds_path = pathlib.Path.home() / ".claude" / ".credentials.json"
    try:
        oauth = json.loads(creds_path.read_text(encoding="utf-8")).get("claudeAiOauth", {})
        exp_ms = oauth.get("expiresAt", 0)
        exp_dt = datetime.datetime.fromtimestamp(exp_ms / 1000)
        secs_left = (exp_dt - datetime.datetime.now()).total_seconds()
        hours_left = secs_left / 3600
        token_prefix = oauth.get("accessToken", "")[:20]
        tier = oauth.get("rateLimitTier", "unknown")
        sub = oauth.get("subscriptionType", "unknown")
        status_icon = "✅" if secs_left > 600 else ("⚠️" if secs_left > 0 else "❌ EXPIRED")
        lines.append(f"=== Claude Auth ===")
        lines.append(f"Token:    {token_prefix}...  {status_icon}")
        lines.append(f"Expires:  {exp_dt.strftime('%Y-%m-%d %H:%M')} ({hours_left:.1f}h remaining)")
        lines.append(f"Plan:     {sub} / {tier}")
    except Exception as e:
        lines.append(f"[ERROR reading credentials: {e}]")

    # ── 2. Daemon health ──────────────────────────────────────────────────────
    lines.append("")
    lines.append("=== Pipeline Daemon ===")
    log_path = pathlib.Path(r"d:\__CoChem\__agentic\.scripts\task_work_loop.log")
    state_path = pathlib.Path(r"d:\__CoChem\__agentic\.scripts\work_loop_state.json")

    if log_path.exists():
        # Read last 20 lines for recent activity
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        last_entry = tail[-1] if tail else "no entries"
        lines.append(f"Log last entry:  {last_entry[:120]}")
        # Detect rate-limit / auth errors in recent log
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
        except Exception:
            pass

    # ── 3. Kanban DB — last 5 task updates ───────────────────────────────────
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
    refresh_log = pathlib.Path(r"d:\__CoChem\__agentic\.scripts\claude_token_refresh.log")
    if refresh_log.exists():
        lines.append("")
        lines.append("=== Last Token Refresh ===")
        last_refresh = refresh_log.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        lines.extend(f"  {l}" for l in last_refresh)

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
