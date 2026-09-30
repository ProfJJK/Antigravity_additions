import os
import sys
import subprocess
import glob
import shutil
from pathlib import Path

PIPELINE_SCRIPTS = {
    1: ["src/cochem/warden/host_warden.py", "src/cochem/warden/vm_control.py", "src/cochem/warden/restart_service.py", "src/cochem/warden/rollback.py", "src/cochem/warden/mcp_server.py"],
    2: ["src/cochem/quarantine/vm_profile.py", "src/cochem/quarantine/sandbox.py", "provisioning/create_quarantine_vm.ps1"],
    3: ["src/cochem/concurrency/layer1.py", "src/cochem/concurrency/layer2.py", "src/cochem/concurrency/runtime.py", "src/cochem/dsp/worker_daemon.py"],
    4: ["src/cochem/blackboard/schema.py", "src/cochem/blackboard/leases.py", "src/cochem/blackboard/lifecycle.py"],
    5: ["src/cochem/research/verification.py", "src/cochem/research/state_machine.py", "src/cochem/research/literature.py", "src/cochem/research/research.py"],
    6: ["src/cochem/knowledge/server.py", "src/cochem/knowledge/indexer.py"],
    7: ["src/cochem/dsp/base.py", "src/cochem/dsp/hardware_guard.py", "src/cochem/dsp/forge/orchestrator.py", "src/cochem/dsp/press/orchestrator.py", "src/cochem/dsp/pedagogy/orchestrator.py", "src/cochem/dsp/toolkit/scaffold.py", "src/cochem/dsp/toolkit/package.py"],
    8: []
}

def run_gemini_coder(script_rel_path, staging_dir, srs_path, audit_feedback=""):
    staging_dir.mkdir(parents=True, exist_ok=True)
    script_name = Path(script_rel_path).name
    script_path = staging_dir / script_name
    
    feedback_section = f"\n[PREVIOUS AUDIT FAILED - PLEASE FIX]:\n{audit_feedback}\n" if audit_feedback else ""
    
    prompt = f'''[SYSTEM ARCHITECTURE MANDATE]
You are acting as the MASTER ORCHESTRATOR (g-coder).
1. Read the SRS file at: {srs_path}
2. Write the complete, production-ready script {script_name} strictly following the SRS requirements.
3. Use the write_to_file tool to save the code to EXACTLY: {script_path}
4. DO NOT use mocked variables, PySCF, ASE, or Mendeleev. This is a general software pipeline.
5. Ensure you import necessary standard libraries and any required Antigravity SDK components.
{feedback_section}
Once the file is written, end your response with exactly: [CODER: DONE]
'''
    cmd = ["agy", "--dangerously-skip-permissions", "--agent", "g-coder", "-p", prompt]
    print(f"Running g-coder for {script_rel_path}...", flush=True)
    res = subprocess.run(cmd, creationflags=0x08000000, capture_output=True, text=True, errors="replace")
    if "[CODER: DONE]" in res.stdout or script_path.exists():
        return True
    print(f"g-coder failed: {res.stdout}\n{res.stderr}", flush=True)
    return False

def run_gemini_audit(script_path, srs_path):
    print(f"Running Gemini 3.1 Pro Asymmetric Audit (g-audit) on {script_path.name}...", flush=True)

    prompt = f'''You are the final auditor.
Read the physical file: {script_path}
Compare it to the SRS file: {srs_path}

Check for absolutely zero mocked data, no spoofed states, and strict adherence.
DO NOT enforce chemistry constraints. This is a general software system.
DO NOT write a pre-flight plan. Use tools to read the files, verify the logic, and output your decision.
If it passes, end your response with exactly: [AUDIT: PASS]
If it fails, end your response with exactly: [AUDIT: FAIL] and explain why so the coder can fix it.
'''
    cmd = ["agy", "--dangerously-skip-permissions", "--agent", "g-audit", "-p", prompt]
    res = subprocess.run(cmd, creationflags=0x08000000, capture_output=True, text=True, errors="replace")
    output = res.stdout
    print(output, flush=True)
    
    if "[AUDIT: PASS]" in output:
        return True, output
    return False, output

def main():
    import glob
    import shutil
    
    for ch_num, scripts in PIPELINE_SCRIPTS.items():
        if not scripts:
            continue
        
        srs_file = Path(f"D:\\__CoChem\\__agentic\\v4.1.2\\wiki\\srs\\ch{ch_num:02d}_*.md")
        srs_matches = glob.glob(str(srs_file))
        if not srs_matches:
            print(f"SRS file for Chapter {ch_num} not found!", flush=True)
            continue
        
        srs_path = Path(srs_matches[0])
        
        for script_rel_path in scripts:
            script_name = Path(script_rel_path).name
            staging_dir = Path(f"D:\\__CoChem\\__agentic\\v4.1.2\\.staging\\ch{ch_num:02d}\\{script_name.replace('.py', '')}")
            
            if staging_dir.exists():
                subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(staging_dir)])
            
            passed = False
            attempts = 0
            audit_feedback = ""
            while not passed and attempts < 3:
                attempts += 1
                print(f"\\n--- Building {script_rel_path} (Attempt {attempts}) ---", flush=True)
                
                if run_gemini_coder(script_rel_path, staging_dir, srs_path, audit_feedback):
                    script_path = staging_dir / script_name
                    
                    if script_path.exists():
                        passed, audit_feedback = run_gemini_audit(script_path, str(srs_path))
                        if not passed:
                            print(f"Audit failed. Retrying...", flush=True)
                    else:
                        print(f"File {script_name} was not created.", flush=True)
                else:
                    print("Coder execution failed.", flush=True)
            
            if not passed:
                print(f"HARD ABORT: Failed to build {script_rel_path} after 3 attempts.", flush=True)
                sys.exit(1)
            
            print(f"SUCCESS: {script_rel_path} verified.", flush=True)
            
            target_path = Path(f"D:\\__CoChem\\__agentic\\v4.1.2\\{script_rel_path}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging_dir / script_name, target_path)
            print(f"Migrated verified script to {target_path}", flush=True)

    print("ALL CHAPTERS RECONSTRUCTED AND VERIFIED.", flush=True)

if __name__ == "__main__":
    main()
