import argparse
import json
import os
import subprocess
import sys
import time
import psutil
from pydantic import BaseModel, Field, RootModel
from typing import Dict, Any, Optional
from pathlib import Path

# cochem_kanban.py is in C:\Users\ansac\Gdrive\__agentic, but the scripts are in D:\__CoChem\__agentic
BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / ".scripts" / "prompts"
SWARM_STATE_FILE = Path(os.getenv("COCHEM_SWARM_STATE", "D:/__CoChem/swarm_state.json"))
LOGS_DIR = BASE_DIR / '.logs'
PID_TABLE_FILE = BASE_DIR / '.scripts' / 'pid_table.json'

def resolve_extractor() -> Path:
    env_path = os.getenv("COCHEM_EXTRACTOR_SCRIPT")
    if env_path: return Path(env_path)
    p1 = BASE_DIR.parent / "__agentic" / "pptx_extractor.py"
    if p1.exists(): return p1
    p2 = Path.home() / "Gdrive" / "__agentic" / "pptx_extractor.py"
    if p2.exists(): return p2
    return p1

EXTRACTOR_SCRIPT = resolve_extractor()

class Telemetry(BaseModel):
    status: str
    target: str
    runner: Optional[str] = None
    log_file: Optional[str] = None
    timestamp: float
    pid: Optional[int] = None
    returncode: Optional[int] = None
    prompt_file: Optional[str] = None
    tasks_extracted: Optional[int] = None

class SwarmTaskEntry(BaseModel):
    status: str
    target: str
    runner: Optional[str] = None
    timestamp: float
    pid: Optional[int] = None
    tasks_extracted: Optional[int] = None

class SwarmStateSchema(RootModel):
    root: Dict[str, Any]

def update_swarm_state(key: str, value: Any) -> None:
    state_data = {}
    if SWARM_STATE_FILE.exists():
        # Crash if JSON Decode fails to prevent complete ledger wipeout
        with open(SWARM_STATE_FILE, "r", encoding="utf-8") as f:
            state_data = json.load(f)
            
    if isinstance(value, Telemetry):
        entry = SwarmTaskEntry(**value.model_dump(exclude_none=True))
        state_data[key] = entry.model_dump(exclude_none=True)
    else:
        state_data[key] = value
        
    validated = SwarmStateSchema(root=state_data)
    
    tmp_file = SWARM_STATE_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(validated.model_dump_json(indent=4))
    os.replace(tmp_file, SWARM_STATE_FILE)

def register_pid(pid: int, runner_name: str):
    PID_TABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pids = {}
    if PID_TABLE_FILE.exists():
        try:
            with open(PID_TABLE_FILE, "r", encoding="utf-8") as f:
                pids = json.load(f)
        except json.JSONDecodeError:
            pass
            
    active_pids = {}
    for p_str, metadata in pids.items():
        try:
            if psutil.pid_exists(int(p_str)):
                active_pids[p_str] = metadata
        except Exception:
            pass
            
    active_pids[str(pid)] = {"runner": runner_name, "timestamp": time.time()}
    
    tmp_file = PID_TABLE_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(active_pids, f, indent=4)
    os.replace(tmp_file, PID_TABLE_FILE)

def resolve_target(target: str) -> Path:
    p = Path(target)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise FileNotFoundError(f"Target not found: {p}")
    return p

def trigger_srs(target: str) -> Telemetry:
    resolved_target = resolve_target(target)
    print(f"[Kanban-CLI] Authentic SRS Pipeline initiating on: {resolved_target}")
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    target_name = resolved_target.name
    timestamp_prefix = int(time.time() * 1000)
    prompt_file = PROMPTS_DIR / f"{timestamp_prefix}_{target_name}_srs_prompt.json"
    prompt_content = {
        "agent_name": "teacher",
        "prompt": (
            f"You are given an approved WBS Chunk at {resolved_target}. "
            "Please read it and generate formal Didactic Student Guides (PDF-ready markdown) for ALL 16 WEEKS. "
            "CRITICAL STRUCTURAL MANDATES:\n"
            "1. STUDENT SPECIFIC: You must generate individual, separate markdown files for EACH student for each week (e.g., `week_7_student_1_WetChem.md`, `week_7_student_2_CompChem.md`, etc.). Do NOT merge all students into a single weekly document.\n"
            "2. HUMAN READABLE: The documents must be highly readable. Avoid dense blocks of text. Heavily utilize bullet points, short clear sentences, bolding for emphasis, and simple structured formatting.\n"
            "CRITICAL: You must save the final markdown files directly to D:/__CoChem/__agentic/dropzones/inbox_code/ so the kanban watcher can pick them up. "
            "Do NOT use MCP tools. Output physical files only. CRITICAL INSTRUCTION: DO NOT ENTER PLANNING MODE. DO NOT CREATE AN IMPLEMENTATION PLAN. DO NOT ASK FOR USER APPROVAL. EXECUTE IMMEDIATELY."
        )
    }
    with open(prompt_file, "w", encoding="utf-8") as f:
        json.dump(prompt_content, f, indent=4)
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    t = Telemetry(status="PENDING", target=resolved_target.as_posix(), runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=daemon_telem.pid, prompt_file=prompt_file.as_posix())
    update_swarm_state(f"student_srs_{target_name}", t)
    return t

def trigger_code(target: str) -> Dict[str, Any]:
    resolved_target = resolve_target(target)
    print(f"[Kanban-CLI] Authentic Coder TDD Machine initiating on: {resolved_target}")
    task_list_script = BASE_DIR / ".scripts" / "task_list.py"
    task_number_script = BASE_DIR / ".scripts" / "task_number_prompts.py"
    if not task_list_script.exists():
        raise FileNotFoundError(f"Script not found: {task_list_script}")
    print(f"[Kanban-CLI] Generating WBS Breakdown for {resolved_target}...")
    subprocess.run(creationflags=0x08000000, [sys.executable, str(task_list_script), f"Implement the architecture defined in the SRS located at {resolved_target}"], check=True)
    print(f"[Kanban-CLI] Generating execution prompts for PROMPTS queue...")
    subprocess.run(creationflags=0x08000000, [sys.executable, str(task_number_script)], check=True)
    return {"status": "PROMPTS_GENERATED", "srs_target": resolved_target.as_posix()}

def trigger_publish(target: str) -> Dict[str, Any]:
    raise NotImplementedError("[ZERO-MOCK VIOLATION PREVENTED] trigger_publish workflow is pending authentic state machine integration.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Unified CoChem Kanban CLI. Agents use this to trigger authentic state machines.')
    subparsers = parser.add_subparsers(dest='command', help='The Kanban workflow to execute')

    p_srs = subparsers.add_parser('srs', help='Trigger the SRS generation state machine')
    p_srs.add_argument('--target', required=True, help='Path to target directory or file')
    
    p_code = subparsers.add_parser('code', help='Trigger the Coder TDD state machine')
    p_code.add_argument('--target', required=True, help='Path to target markdown file')

    p_publish = subparsers.add_parser('publish', help='Trigger the Pinnacle Publishing Swarm')
    p_publish.add_argument('--target', required=True, help='Path to raw data or outline folder')

    p_presentation = subparsers.add_parser('presentation', help='Trigger the Presentation Swarm')
    p_presentation.add_argument('--target', required=True, help='Path to target directory')
    p_presentation.add_argument('--wait', action='store_true', help='Wait synchronously for completion')

    p_upgrade = subparsers.add_parser('upgrade-presentation', help='Trigger the Teardown & Rebuild PPTX')
    p_upgrade.add_argument('--target', required=True, help='Path to target pptx file')

    args = parser.parse_args()

    if args.command == 'srs':
        trigger_srs(args.target)
    elif args.command == 'code':
        trigger_code(args.target)
    elif args.command == 'publish':
        trigger_publish(args.target)
        trigger_presentation(args.target, wait=args.wait)
        trigger_upgrade_presentation(args.target)
    else:
        parser.print_help()






