import os
import json
import time
import subprocess
import sys

STATE_FILE = r"D:\__CoChem\__agentic\.scripts\work_loop_state.json"

def is_process_running(process_name_or_cmd):
    try:
        import psutil
        for p in psutil.process_iter(['cmdline']):
            try:
                cmdline = " ".join(p.info.get('cmdline') or [])
                if process_name_or_cmd in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False
    except Exception:
        try:
            cmd = [
                "pwsh", "-NoProfile", "-Command",
                f'Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like "*{process_name_or_cmd}*" }} | Select-Object -ExpandProperty ProcessId'
            ]
            output = subprocess.check_output(creationflags=0x08000000, cmd, text=True)
            lines = [line.strip() for line in output.splitlines() if line.strip() and line.strip().isdigit()]
            return len(lines) > 0
        except Exception:
            return False

def kill_stale_agents():
    """Kills any hung Python processes running the kanban loop or Antigravity CLI."""
    print("[Meta Auditor] Executing process sweep for hung agents...")
    try:
        # Kill Antigravity instances forcefully
        subprocess.run(creationflags=0x08000000, "taskkill /F /IM agy.exe", shell=True, capture_output=True)
        # Kill the task_work_loop specifically using native psutil or pwsh
        killed = False
        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if p.info.get('name') == 'python.exe':
                        cmdline = " ".join(p.info.get('cmdline') or [])
                        if 'task_work_loop' in cmdline:
                            p.kill()
                            killed = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        if not killed:
            subprocess.run(creationflags=0x08000000, 
                [
                    "pwsh", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*task_work_loop*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
                ],
                capture_output=True
            )
    except Exception as e:
        print(f"[Meta Auditor] Cleanup error: {e}")

def trigger_council(reason: str):
    """Invokes the Agent Council via agy when spoofing or hangs are detected."""
    print(f"[Meta Auditor] Summoning Agent Council. Reason: {reason}")
    subprocess.Popen(creationflags=0x08000000, [
        "agy", 
        "--agent", "0rchestrator", 
        "-p", f"CRITICAL INCIDENT IN KANBAN LOOP: {reason}. Convene the Agent Council immediately to resolve this."
    ], creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)

def audit_state():
    print("[Meta Auditor] Native Cron Audit Initiated...")
    if not os.path.exists(STATE_FILE):
        print("[Meta Auditor] No active state file found. Exiting cleanly.")
        sys.exit(0)
        
    mtime = os.path.getmtime(STATE_FILE)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        print(f"[Meta Auditor] Failed to read state file: {e}")
        sys.exit(1)
        
    # Check for spoofing escalation
    if state.get("status") == "SPOOFING_DETECTED":
        trigger_council(state.get("critique", "Spoofing detected by local verification loop."))
        
    # Check if presentation loop is running. Presentations require massive contexts and often take > 30 minutes.
    is_presentation_active = is_process_running("presentation_work_loop")
    timeout = 3600 if is_presentation_active else 600
    
    # Check for hung agents
    if time.time() - mtime > timeout:
        print(f"[Meta Auditor] State frozen for {timeout//60}+ minutes. Killing hung agents and resetting lock.")
        kill_stale_agents()
        trigger_council(f"State frozen for {timeout//60} minutes. Agents hung or crashed.")

if __name__ == "__main__":
    audit_state()
