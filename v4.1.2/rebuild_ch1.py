import os
import sys
import subprocess
import json
from pathlib import Path

CLAUDE_EXE = r"C:\Users\ansac\.local\bin\claude.exe"

def run_fable(prompt, staging_dir):
    staging_dir.mkdir(parents=True, exist_ok=True)
    fable_prompt = f'''[SYSTEM ARCHITECTURE MANDATE]
You are Fable 5.1 acting as the MASTER ORCHESTRATOR.
You must use a Git Differential workflow in the directory {staging_dir}.
Write the requested Python script chunk by chunk using Opus 5.5 subagents.
Commit your progress. Do not use mocked variables.

{prompt}
'''
    prompt_file = staging_dir / "fable_prompt.txt"
    prompt_file.write_text(fable_prompt, encoding="utf-8")
    
    subprocess.run(["git", "init"], cwd=staging_dir, capture_output=True)
    
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--max-old-space-size=4096"
    cmd = [CLAUDE_EXE, "--dangerously-skip-permissions", "-p", fable_prompt]
    print(f"Running Fable 5.1 for {staging_dir.name}...")
    res = subprocess.run(cmd, cwd=staging_dir, env=env, creationflags=0x08000000, capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        print(f"Fable failed: {res.stderr}")
        return False
    return True

def run_gemini_audit(script_path, srs_context):
    print(f"Running Gemini 3.1 Pro Asymmetric Audit on {script_path.name}...")
    prompt = f'''Audit the physical file {script_path} against the following SRS context:
{srs_context}

Check for absolutely zero mocked data, no spoofed states, and strict adherence.
If it passes, end your response with exactly: [AUDIT: PASS]
If it fails, end your response with exactly: [AUDIT: FAIL]
'''
    cmd = ["agy", "--agent", "cochem-audit", "-p", prompt]
    res = subprocess.run(cmd, creationflags=0x08000000, capture_output=True, text=True, errors="replace")
    print(res.stdout)
    if "[AUDIT: PASS]" in res.stdout:
        return True
    return False

def process_chapter(chapter_num, scripts_to_build, srs_file):
    srs_content = Path(srs_file).read_text(encoding="utf-8")
    for script_name in scripts_to_build:
        staging_dir = Path(f"D:\\__CoChem\\__agentic\\v4.1.2\\.staging\\ch{chapter_num:02d}\\{script_name.replace('.py', '')}")
        script_path = staging_dir / script_name
        
        passed = False
        attempts = 0
        while not passed and attempts < 3:
            attempts += 1
            print(f"\n--- Building {script_name} (Attempt {attempts}) ---")
            prompt = f"Write the python script {script_name} based on this SRS Chapter Context:\n\n{srs_content}"
            if run_fable(prompt, staging_dir):
                if script_path.exists():
                    passed = run_gemini_audit(script_path, srs_content)
                else:
                    print(f"File {script_path} was not created by Fable.")
            if not passed:
                print("Failed audit or generation. Retrying...")
        
        if not passed:
            print(f"HARD ABORT: Failed to build {script_name} after 3 attempts.")
            sys.exit(1)
        
        print(f"SUCCESS: {script_name} verified.")

if __name__ == "__main__":
    ch1_scripts = ["host_warden.py", "vm_control.py", "restart_service.py", "rollback.py"]
    process_chapter(1, ch1_scripts, r"D:\__CoChem\__agentic\v4.1.2\wiki\srs\ch01_host_warden.md")
