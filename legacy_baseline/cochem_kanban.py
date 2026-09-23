import argparse
import json
import os
import subprocess
import sys
import time
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
import psutil

# LLM Router — must be importable from the same directory
sys.path.insert(0, str(Path(__file__).parent))
try:
    from llm_router import get_provider, get_provider_for_agent, MODEL_REGISTRY  # noqa: F401
    _LLM_ROUTER_AVAILABLE = True
except ImportError:
    _LLM_ROUTER_AVAILABLE = False
from pydantic import BaseModel, Field, RootModel

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cochem_kanban")

# Dynamic base directories
BASE_DIR = Path(__file__).resolve().parent
COCHEM_ROOT = Path(os.getenv("COCHEM_ROOT", str(BASE_DIR.parent)))

PROMPTS_DIR = BASE_DIR / ".scripts" / "prompts"
TASK_WORK_LOOP = BASE_DIR / ".scripts" / "task_work_loop.py"
LOGS_DIR = BASE_DIR / ".logs"
ARTIFACTS_DIR = Path(os.getenv("COCHEM_ARTIFACTS_DIR", str(COCHEM_ROOT / ".agent_artifacts")))
SWARM_STATE_FILE = Path(os.getenv("COCHEM_SWARM_STATE", "D:/__CoChem/swarm_state.json"))
DROPZONES_DIR = Path(os.getenv("COCHEM_DROPZONES_DIR", str(BASE_DIR / "dropzones")))
PID_TABLE_FILE = Path(os.getenv("COCHEM_PID_TABLE", str(BASE_DIR / ".scripts" / "pid_table.json")))

def resolve_extractor() -> Path:
    env_path = os.getenv("COCHEM_EXTRACTOR_SCRIPT")
    if env_path: return Path(env_path)
    p1 = COCHEM_ROOT / "__agentic" / "pptx_extractor.py"
    if p1.exists(): return p1
    p2 = Path.home() / "Gdrive" / "__agentic" / "pptx_extractor.py"
    if p2.exists(): return p2
    return p1

EXTRACTOR_SCRIPT = resolve_extractor()

# Project Context Router integration
council_path = Path(os.getenv("COCHEM_COUNCIL", str(COCHEM_ROOT / "GitHub-Repo" / "CoChem-Council")))
if council_path.exists() and str(council_path) not in sys.path:
    sys.path.insert(0, str(council_path))

# Zombie Sweeper Subsystem Integration (Task 4.02 / Vector 6)
base_src = COCHEM_ROOT / "GitHub-Repo" / "CoChem-BASE" / "src"
if base_src.exists() and str(base_src) not in sys.path:
    sys.path.insert(0, str(base_src))
base_root = COCHEM_ROOT / "GitHub-Repo" / "CoChem-BASE"
if base_root.exists() and str(base_root) not in sys.path:
    sys.path.insert(0, str(base_root))
platform_src = COCHEM_ROOT / "src"
if platform_src.exists() and str(platform_src) not in sys.path:
    sys.path.insert(0, str(platform_src))

try:
    from cochem_platform.zombie_sweeper import register_child_pid, sweep_zombies
except ImportError:
    try:
        from cochem_platform.zombie_sweeper_module import register_child_pid, sweep_zombies
    except ImportError:
        try:
            from zombie_sweeper_module import register_child_pid, sweep_zombies
        except ImportError:
            def sweep_zombies(parent_pid: Optional[int] = None, timeout: float = 1.0) -> int:
                current_pid = parent_pid or os.getpid()
                reaped = 0
                for proc in psutil.process_iter(["pid", "name", "status", "ppid"]):
                    try:
                        if proc.info.get("ppid") == current_pid and proc.info.get("status") == psutil.STATUS_ZOMBIE:
                            proc.wait(timeout=timeout)
                            reaped += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                return reaped

            def register_child_pid(pid: int, metadata: Optional[Dict[str, Any]] = None) -> None:
                logger.debug("Registered child PID %d.", pid)


import atexit
atexit.register(sweep_zombies)

get_project_context_router = None
try:
    from cochem_council.project_context_router import ProjectContextRouter, get_project_context_router
except ImportError:
    pass

class PromptPayload(BaseModel):
    kanban_source: str
    agent_name: str
    prompt: str
    task_id: Optional[str] = None

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

def get_markdown_content(target: Path) -> str:
    if target.is_dir():
        print(f"[ERROR] Target must be a precise markdown file, not a directory: {target}", file=sys.stderr)
        sys.exit(1)
    return target.read_text(encoding="utf-8")

def trigger_improve(target: str) -> Telemetry:
    resolved_target = resolve_target(target)
    
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    target_name = resolved_target.name
    timestamp_prefix = int(time.time() * 1000)
    prompt_file = PROMPTS_DIR / f"{timestamp_prefix}_{target_name}_prompt.json"
    
    improve_agent = "cochem-coder"
    if get_project_context_router is not None:
        improve_agent = get_project_context_router().resolve_agent("coder", target_path=resolved_target)

    dropzone_srs = DROPZONES_DIR / "inbox_srs"
    dropzone_srs.mkdir(parents=True, exist_ok=True)
    
    prompt_text = (
        f"Execute Architectural Review on target codebase at {resolved_target}. "
        "You are EXEMPT from physical codebase mutation mandates. Your job is strictly to write markdown improvement vectors. "
        f"Analyze the codebase and save a structured Markdown chunk proposal directly to the dropzone directory: {dropzone_srs.as_posix()} "
        "CRITICAL: You must use the `write_to_file` tool to save the markdown file directly to the disk so the kanban watcher can pick it up. Do NOT just output the text in chat, or you will fail the physical audit. "
        "DO NOT USE MCP TOOLS to trigger further workflows."
    )
    
    payload = PromptPayload(kanban_source=resolved_target.as_posix(), agent_name=improve_agent, prompt=prompt_text)
    prompt_file.write_text(payload.model_dump_json(indent=4), encoding="utf-8")
        
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    
    t = Telemetry(status="PENDING", target=resolved_target.as_posix(), runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=daemon_telem.pid, prompt_file=prompt_file.as_posix())
    update_swarm_state(f"kanban_improve_{target_name}", t)
    return t

def trigger_srs(target: str) -> Telemetry:
    resolved_target = resolve_target(target)
    
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    target_name = resolved_target.name
    timestamp_prefix = int(time.time() * 1000)
    prompt_file = PROMPTS_DIR / f"{timestamp_prefix}_{target_name}_srs_prompt.json"

    srs_agent = "cochem-scribe"
    if get_project_context_router is not None:
        srs_agent = get_project_context_router().resolve_agent("scribe", target_path=resolved_target)

    dropzone_code = DROPZONES_DIR / "inbox_code"
    dropzone_code.mkdir(parents=True, exist_ok=True)
    
    prompt_text = (
        f"You are given an approved Improvement Chunk at {resolved_target}. "
        "Please read it and generate a formal, highly detailed Software Requirements Specification (SRS) for this improvement. "
        f"CRITICAL: You must save the final SRS markdown file directly to {dropzone_code.as_posix()} "
        "so the kanban watcher can pick it up for the next phase. "
        "Do NOT use MCP tools. Output physical files only."
    )
    
    payload = PromptPayload(kanban_source=resolved_target.as_posix(), agent_name=srs_agent, prompt=prompt_text)
    prompt_file.write_text(payload.model_dump_json(indent=4), encoding="utf-8")
        
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    
    t = Telemetry(status="PENDING", target=resolved_target.as_posix(), runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=daemon_telem.pid, prompt_file=prompt_file.as_posix())
    update_swarm_state(f"kanban_srs_{target_name}", t)
    return t

def trigger_code(target: str) -> Telemetry:
    resolved_target = resolve_target(target)
    
    content = get_markdown_content(resolved_target)
    import re
    pattern = r'- \*\*Task (\d+\.\d+):\*\* (.*?)(?=\n- \*\*Task |\n\n|\n#|\Z)'
    matches = re.findall(pattern, content, flags=re.DOTALL)
    
    if not matches:
        print(f"[ERROR] Zero tasks matching the regex pattern found in {resolved_target}. Aborting dispatch.", file=sys.stderr)
        sys.exit(1)
    
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    for task_id, description in matches:
        task_id = task_id.strip()
        description = description.strip().replace('\n', ' ')
        prompt_text = f"IMPLEMENTATION TASK {task_id}:\n{description}\n\nYou must strictly adhere to the Anti-Spoofing and Zero-Mock Protocols."
        
        coder_agent = "cochem-coder"
        if get_project_context_router is not None:
            coder_agent = get_project_context_router().resolve_agent("coder", target_path=resolved_target, content=description)

        payload = PromptPayload(kanban_source=resolved_target.as_posix(), agent_name=coder_agent, prompt=prompt_text, task_id=task_id)
        prompt_file = PROMPTS_DIR / f"{task_id}_prompt.json"
        prompt_file.write_text(payload.model_dump_json(indent=4), encoding="utf-8")
        
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    
    t = Telemetry(status="PENDING", target=resolved_target.as_posix(), runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=daemon_telem.pid, tasks_extracted=len(matches))
    update_swarm_state(f"kanban_code_{resolved_target.name}", t)
    return t

def trigger_publish(target: str) -> Telemetry:
    resolved_target = resolve_target(target)
    
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    target_name = resolved_target.name
    timestamp_prefix = int(time.time() * 1000)
    prompt_file = PROMPTS_DIR / f"{timestamp_prefix}_{target_name}_publish_prompt.json"
    
    publisher_agent = "cochem-publisher"
    if get_project_context_router is not None:
        publisher_agent = get_project_context_router().resolve_agent("publisher", target_path=resolved_target)

    prompt_text = (
        f"Execute publishing pipeline on data at {resolved_target}. "
        "Draft full manuscript sections, compile LaTeX, and produce the final output. "
        "CRITICAL: Use physical file tools. DO NOT use placeholders."
    )
    
    payload = PromptPayload(kanban_source=resolved_target.as_posix(), agent_name=publisher_agent, prompt=prompt_text)
    prompt_file.write_text(payload.model_dump_json(indent=4), encoding="utf-8")
        
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    
    t = Telemetry(status="PENDING", target=resolved_target.as_posix(), runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=daemon_telem.pid, prompt_file=prompt_file.as_posix())
    update_swarm_state(f"kanban_publish_{target_name}", t)
    return t

def trigger_syllabus(targets: List[str]) -> Telemetry:
    
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    tasks_queued = 0
    for target in targets:
        resolved_target = resolve_target(target)
        target_name = resolved_target.name
        timestamp_prefix = int(time.time() * 1000)
        prompt_file = PROMPTS_DIR / f"{timestamp_prefix}_{target_name}_syllabus_prompt.json"
        
        prompt_text = (
            f"Migrate the syllabus at {resolved_target.as_posix()} to the new Cumberland University template format. "
            f"The template is located at D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex. "
            "Use the `cu-syllabus` MCP server to fetch important dates and cross-reference the course shell to ensure dynamic schedule and Title II/ADA compliance. "
            "Rewrite the file in place with the updated LaTeX content."
        )
        
        coder_agent = "cochem-coder"
        if get_project_context_router is not None:
            coder_agent = get_project_context_router().resolve_agent("coder", target_path=resolved_target)

        payload = PromptPayload(kanban_source=resolved_target.as_posix(), agent_name=coder_agent, prompt=prompt_text)
        prompt_file.write_text(payload.model_dump_json(indent=4), encoding="utf-8")
        tasks_queued += 1
        time.sleep(0.01) # ensure unique timestamps
        
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    
    t = Telemetry(status="PENDING", target="batch_syllabus", runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=daemon_telem.pid, tasks_extracted=tasks_queued)
    update_swarm_state("kanban_syllabus_batch", t)
    return t

def trigger_questions(target: str) -> Telemetry:
    resolved_target = resolve_target(target)
    
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    content = get_markdown_content(resolved_target)
    
    # Parse the topic map to extract Chapters 1-5 and their sections/topics
    import re
    chapters_found = 0
    current_chapter = None
    current_section = None
    current_topics = []
    
    tasks_queued = 0
    lines = content.splitlines()
    
    def dispatch_section():
        nonlocal tasks_queued
        if not current_section or not current_topics:
            return
        
        # Ensure we only process chapters 1 to 5
        try:
            chap_num = int(current_chapter)
            if chap_num > 5:
                return
        except (ValueError, TypeError):
            return
            
        timestamp_prefix = int(time.time() * 1000)
        safe_sec = current_section.replace('.', '_').replace(' ', '_').replace('/', '_').replace(':', '').replace('–', '_').replace('’', '')
        prompt_file = PROMPTS_DIR / f"{timestamp_prefix}_ch{current_chapter}_{safe_sec}_questions_prompt.json"
        
        output_dir = Path("D:/Gdrive/__agentic/.sources/r_exams/CHEM311/question_bank") / f"Chapter_{current_chapter}"
        output_file = output_dir / f"{safe_sec}.md"
        
        topics_str = "\\n- ".join(current_topics)
        prompt_text = (
            f"Generate a databank of exactly 10 Multiple Choice Questions (MCQs) for Chapter {current_chapter}, Section {current_section}.\\n"
            f"The questions must cover the following topics explicitly:\\n- {topics_str}\\n\\n"
            "REQUIREMENTS:\\n"
            "1. Generate exactly 10 questions.\\n"
            "2. Distribute them across a Bloom's Taxonomy pyramid (e.g., Remember, Understand, Apply, Analyze, Evaluate).\\n"
            "3. Format the questions for use with r/exams and OCR.\\n"
            "4. Include metadata for each question specifying the Section, Topic, and Bloom's Taxonomy level.\\n"
            f"5. Save the output directly as a physical file to: {output_file.as_posix()}\\n\\n"
            "CRITICAL: Use the write_to_file tool. Do not just output text in chat."
        )
        
        educator_agent = "cochem-educator" # Or cochem-coder if educator not in router
        if get_project_context_router is not None:
            # Educator might not be in router, fallback to coder
            educator_agent = get_project_context_router().resolve_agent("coder", target_path=resolved_target)
        else:
            educator_agent = "cochem-coder"
            
        payload = PromptPayload(kanban_source=resolved_target.as_posix(), agent_name=educator_agent, prompt=prompt_text)
        prompt_file.write_text(payload.model_dump_json(indent=4), encoding="utf-8")
        tasks_queued += 1
        time.sleep(0.01)
    
    for line in lines:
        line = line.strip()
        m_chap = re.match(r'^##\s+Chapter\s+(\d+):', line)
        if m_chap:
            dispatch_section() # Dispatch previous section if exists
            current_chapter = m_chap.group(1)
            current_section = None
            current_topics = []
            continue
            
        m_sec = re.match(r'^###\s+(.*)', line)
        if m_sec:
            dispatch_section() # Dispatch previous section if exists
            current_section = m_sec.group(1).strip()
            current_topics = []
            continue
            
        m_topic = re.match(r'^-\s+(.*)', line)
        if m_topic and current_section:
            current_topics.append(m_topic.group(1).strip())

    dispatch_section() # Dispatch final section
    
    log_file = LOGS_DIR / f"daemon_task_work_loop.log"
    
    t = Telemetry(status="PENDING", target="batch_questions", runner=TASK_WORK_LOOP.as_posix(), log_file=log_file.as_posix(), timestamp=time.time(), pid=0, tasks_extracted=tasks_queued)
    update_swarm_state("kanban_questions_batch", t)
    return t

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Unified CoChem Kanban CLI. Agents use this to trigger authentic state machines.')
    # Global LLM routing flags
    parser.add_argument('--provider', choices=['gemini', 'claude'],
                        default=os.environ.get('DEFAULT_LLM_PROVIDER', 'gemini'),
                        help='LLM provider for this task batch (default: from DEFAULT_LLM_PROVIDER env)')
    parser.add_argument('--model', default='',
                        help='Override the LLM model string for this invocation')
    subparsers = parser.add_subparsers(dest='command', help='The Kanban workflow to execute')

    p_srs = subparsers.add_parser('srs', help='Trigger the SRS generation state machine')
    p_srs.add_argument('--target', required=True, help='Path to target directory or file')
    p_improve = subparsers.add_parser('improve', help='Trigger the 10-Cycle Improve state machine')
    p_improve.add_argument('--target', required=True, help='Path to target codebase or file')
    p_code = subparsers.add_parser('code', help='Trigger the Coder TDD state machine')
    p_code.add_argument('--target', required=True, help='Path to target markdown file')
    p_publish = subparsers.add_parser('publish', help='Trigger the Pinnacle Publishing Swarm')
    p_publish.add_argument('--target', required=True, help='Path to raw data or outline folder')
    p_presentation = subparsers.add_parser('presentation', help='Trigger the Presentation Swarm')
    p_presentation.add_argument('--target', required=True, help='Path to target directory')
    p_presentation.add_argument('--wait', action='store_true', help='Wait synchronously for completion')

    p_upgrade = subparsers.add_parser('upgrade-presentation', help='Trigger the Teardown & Rebuild PPTX')
    p_upgrade.add_argument('--target', required=True, help='Path to target pptx file')

    p_syllabus = subparsers.add_parser('syllabus', help='Trigger the Syllabus Migration state machine')
    p_syllabus.add_argument('--targets', nargs='+', required=True, help='Paths to target syllabus files')

    p_questions = subparsers.add_parser('questions', help='Trigger the Questions Generation state machine')
    p_questions.add_argument('--target', required=True, help='Path to target topic map file')

    # Pivot Council sub-command
    p_pivot = subparsers.add_parser('pivot', help='Activate the Pivot Council for a failed task')
    p_pivot.add_argument('--task-file', required=True, help='Path to the failed prompt JSON file')
    p_pivot.add_argument('--failure-trace', default='', help='Failure trace text')
    p_pivot.add_argument('--failure-trace-file', default='', help='File containing failure trace')
    p_pivot.add_argument('--cycle', type=int, default=1, help='Pivot cycle number (1-3)')

    args = parser.parse_args()

    # Propagate provider/model overrides to environment so downstream tools pick them up
    if args.provider:
        os.environ['DEFAULT_LLM_PROVIDER'] = args.provider
    if args.model:
        os.environ['DEFAULT_CLAUDE_MODEL'] = args.model
        os.environ['DEFAULT_GEMINI_MODEL'] = args.model

    # Reap any defunct child processes prior to workflow dispatch (Task 4.02)
    sweep_zombies()

    result = None
    if args.command == 'srs':
        result = trigger_srs(args.target)
    elif args.command == 'improve':
        result = trigger_improve(args.target)
    elif args.command == 'code':
        result = trigger_code(args.target)
    elif args.command == 'publish':
        result = trigger_publish(args.target)
    elif args.command == 'syllabus':
        result = trigger_syllabus(args.targets)
    elif args.command == 'questions':
        result = trigger_questions(args.target)
    elif args.command == 'pivot':
        from pivot_council import run_pivot_council  # type: ignore
        trace = args.failure_trace
        if args.failure_trace_file and Path(args.failure_trace_file).exists():
            trace = Path(args.failure_trace_file).read_text(encoding='utf-8')
        success = run_pivot_council(Path(args.task_file), trace, pivot_cycle=args.cycle)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)

    if result:
        # Emit raw JSON to stdout for callers
        print(result.model_dump_json(indent=2))





