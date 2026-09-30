import os
import sys

file_path = r'D:\__CoChem\__agentic\.scripts\task_work_loop.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

if '_GIT_MERGE_LOCK' not in content:
    content = content.replace('import asyncio\n', 'import asyncio\n_GIT_MERGE_LOCK = None\n')

if 'global _GIT_MERGE_LOCK' not in content:
    content = content.replace('async def daemon_loop(', 'async def daemon_loop(\n    global _GIT_MERGE_LOCK\n    if _GIT_MERGE_LOCK is None:\n        _GIT_MERGE_LOCK = asyncio.Lock()\n')

old_ws_code = '''        ws_raw = raw.get("workspace")
        workspace = (Path(ws_raw) if ws_raw and Path(ws_raw).is_dir() else TDD_WORKSPACE_ROOT).resolve()
        sid = _safe_id(task_id)'''

new_ws_code = '''        ws_raw = raw.get("workspace")
        sid = _safe_id(task_id)
        runs_dir = Path(os.environ.get("COCHEM_TDD_RUNS_DIR", str(TDD_RUNS_DIR))).resolve()
        
        # FABLE 5.1 ISOLATED WORKSPACE ARCHITECTURE
        if ws_raw and Path(ws_raw).is_dir():
            workspace = Path(ws_raw).resolve()
        else:
            workspace = (runs_dir / sid / "workspace").resolve()
            if not workspace.exists():
                import subprocess
                workspace.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "clone", "--local", str(TDD_WORKSPACE_ROOT), str(workspace)], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.name", "cochem-coder"], cwd=workspace)
                subprocess.run(["git", "config", "user.email", "cochem@localhost"], cwd=workspace)'''

content = content.replace(old_ws_code, new_ws_code)

old_finalize_acc = '''    if outcome == "ACCEPTED":
        # Leave artifact in place; user/orchestrator moves task to completed
        return outcome'''

new_finalize_acc = '''    if outcome == "ACCEPTED":
        # FABLE 5.1 ISOLATED WORKSPACE MERGE
        import subprocess
        if ctx.workspace != TDD_WORKSPACE_ROOT and (ctx.workspace / ".git").exists():
            subprocess.run(["git", "add", "."], cwd=ctx.workspace)
            proc = subprocess.run(["git", "commit", "-m", f"[TDD] Task {ctx.task_id} completed\\n\\n{ctx.outcome_reason}"], cwd=ctx.workspace, capture_output=True)
            if proc.returncode == 0:
                async with _GIT_MERGE_LOCK:
                    safe_sid = _safe_id(ctx.task_id)
                    subprocess.run(["git", "remote", "add", safe_sid, str(ctx.workspace)], cwd=TDD_WORKSPACE_ROOT)
                    subprocess.run(["git", "fetch", safe_sid], cwd=TDD_WORKSPACE_ROOT)
                    merge_proc = subprocess.run(["git", "merge", "--no-ff", "-m", f"Merge task {ctx.task_id}", f"{safe_sid}/master"], cwd=TDD_WORKSPACE_ROOT)
                    subprocess.run(["git", "remote", "rm", safe_sid], cwd=TDD_WORKSPACE_ROOT)
                    if merge_proc.returncode != 0:
                        subprocess.run(["git", "merge", "--abort"], cwd=TDD_WORKSPACE_ROOT)
                        ctx.outcome = "MERGE_CONFLICT"
                        ctx.outcome_reason = "Isolated workspace succeeded, but merging back to main branch produced conflicts."
                        ctx.log("Merge conflict when synchronizing isolated workspace to TDD_WORKSPACE_ROOT.", logging.ERROR)
                        return ctx.outcome
        return outcome'''

content = content.replace(old_finalize_acc, new_finalize_acc)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('task_work_loop.py patched successfully.')
