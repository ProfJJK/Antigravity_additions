"""
Pivot Council — Failed Task Recovery System
============================================
Three-agent pipeline that activates when a Kanban task exhausts all retry cycles.

Agents:
  1. pivot-researcher   — researches root cause and alternative approaches
  2. pivot-architect    — designs a fundamentally new implementation strategy (deep think)
  3. pivot-planner      — validates plan coherence vs project constraints, then re-queues

Entry point: python pivot_council.py --task-file <path_to_failed_prompt.json>
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from llm_router import MODEL_REGISTRY, get_provider  # noqa: E402

LOG_DIR = _HERE / ".logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PIVOT] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pivot_council.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("pivot_council")

PROMPTS_DIR = _HERE / ".scripts" / "prompts"
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_PIVOT_CYCLES = int(os.environ.get("MAX_PIVOT_CYCLES", "3"))


# ── Helpers ──────────────────────────────────────────────────────────────────

# Use QuotaFallbackRouter for full fallback chain: primary → claude-subscription → gemini → ollama
# This prevents crashes when the primary provider (e.g. Gemini) is quota-exhausted.
try:
    from llm_router import QuotaFallbackRouter as _QuotaFallbackRouter
    _router = _QuotaFallbackRouter()
    _ROUTER_AVAILABLE = True
    logger.info("[PIVOT] QuotaFallbackRouter loaded — full fallback chain active.")
except Exception as _e:
    _router = None
    _ROUTER_AVAILABLE = False
    logger.warning(f"[PIVOT] QuotaFallbackRouter unavailable ({_e}), using direct provider (no fallback).")


def _generate(agent_name: str, prompt: str, system: str = "") -> str:
    """Route through QuotaFallbackRouter for full fallback chain.
    Falls back to direct provider only if router unavailable."""
    if _ROUTER_AVAILABLE and _router is not None:
        logger.info(f"  [{agent_name}] → QuotaFallbackRouter")
        full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt
        response = _router.generate(agent_name, full_prompt, timeout=180)
        logger.info(f"  [{agent_name}] done via router. chars={len(response.content)}")
        return response.content

    # Direct fallback (no quota chain) — used only if router import failed
    entry = MODEL_REGISTRY.get(agent_name, {"provider": "claude-subscription", "model": "claude-sonnet-4-5"})
    provider = get_provider(entry["provider"])
    model = entry.get("model")
    logger.info(f"  [{agent_name}] DIRECT provider={entry['provider']} model={model}")
    response = provider.generate(prompt, system=system, model=model, agent_name=agent_name)
    logger.info(f"  [{agent_name}] done. tokens_in={response.input_tokens} tokens_out={response.output_tokens}")
    return response.content


def _stream_to_log(agent_name: str, prompt: str, system: str = "") -> str:
    """Stream generation — routes through QuotaFallbackRouter (non-streaming call with full output).
    ClaudeSubscriptionProvider does not support streaming; router handles graceful degradation."""
    # Router doesn't stream — use _generate which handles full fallback chain
    logger.info(f"  [{agent_name}] STREAMING (via router generate, full output)")
    return _generate(agent_name, prompt, system=system)


# ── Stage 1: Research ─────────────────────────────────────────────────────────

def stage_research(original_task: dict, failure_trace: str, pivot_cycle: int) -> str:
    logger.info(f"=== PIVOT CYCLE {pivot_cycle} — Stage 1: Research ===")
    prompt = f"""
<pivot_context>
  <cycle>{pivot_cycle} of {MAX_PIVOT_CYCLES}</cycle>
  <original_task>
    <agent>{original_task.get('agent_name', 'unknown')}</agent>
    <source>{original_task.get('kanban_source', '')}</source>
    <prompt>{original_task.get('prompt', '')}</prompt>
  </original_task>
  <failure_trace>
{failure_trace}
  </failure_trace>
</pivot_context>

<instructions>
You are the Pivot Council Researcher. The task above has failed all retry cycles.
Your job:
1. Identify the ROOT CAUSE of the failure (technical, environmental, architectural).
2. Research 2-3 alternative methodologies, libraries, or algorithms that could succeed.
3. Find any relevant open-source implementations, papers, or documentation.
4. Produce a structured research dossier with concrete, actionable findings.

Do NOT give generic advice. Be specific to the exact failure mode.
</instructions>
"""
    return _generate("pivot-researcher", prompt)


# ── Stage 2: Architect (deep thinking) ────────────────────────────────────────

def stage_architect(original_task: dict, failure_trace: str, research_dossier: str, pivot_cycle: int) -> str:
    logger.info(f"=== PIVOT CYCLE {pivot_cycle} — Stage 2: Architect (deep think) ===")
    prompt = f"""
<pivot_context>
  <cycle>{pivot_cycle} of {MAX_PIVOT_CYCLES}</cycle>
  <original_task>
    <agent>{original_task.get('agent_name', 'unknown')}</agent>
    <source>{original_task.get('kanban_source', '')}</source>
    <prompt>{original_task.get('prompt', '')}</prompt>
  </original_task>
  <failure_trace>
{failure_trace}
  </failure_trace>
</pivot_context>

<research_dossier>
{research_dossier}
</research_dossier>

<instructions>
You are the Pivot Council Architect. Think deeply and step by step.
Design a FUNDAMENTALLY DIFFERENT implementation approach that avoids the failure mode.

Your output must be a concrete, executable implementation plan specifying:
1. Exact files to create or modify (with full paths)
2. Exact functions/classes to implement
3. Exact algorithms, libraries, or external tools to use
4. Anti-spoofing compliance: no mocks, no stubs, no synthetic data

This plan will be dispatched directly back into the Kanban pipeline as a new task prompt.
Format it as a self-contained task prompt that an implementing agent can execute directly.
</instructions>
"""
    # Use streaming for the architect — deep thinking may be verbose
    return _stream_to_log("pivot-architect", prompt)


# ── Stage 3: Planner (validation & re-queue) ──────────────────────────────────

def stage_planner(original_task: dict, architect_plan: str, pivot_cycle: int) -> str:
    logger.info(f"=== PIVOT CYCLE {pivot_cycle} — Stage 3: Planner (validate & re-queue) ===")
    prompt = f"""
<pivot_context>
  <cycle>{pivot_cycle} of {MAX_PIVOT_CYCLES}</cycle>
  <original_task>
    <agent>{original_task.get('agent_name', 'unknown')}</agent>
    <source>{original_task.get('kanban_source', '')}</source>
    <original_prompt>{original_task.get('prompt', '')}</original_prompt>
  </original_task>
</pivot_context>

<architect_plan>
{architect_plan}
</architect_plan>

<instructions>
You are the Pivot Council Planner. Using your large context window:
1. Verify the architect's plan is coherent with the ORIGINAL task intent.
2. Check it does not contradict CoChem infrastructure constraints (WAL SQLite, no subprocess.CREATE_WINDOW, no mocks).
3. Verify the plan is complete and executable without further clarification.

If the plan is APPROVED: output it verbatim with a header "APPROVED PIVOT PLAN:".
If REJECTED: output "REJECTED:" followed by specific objections for the architect.
</instructions>
"""
    return _generate("pivot-planner", prompt)


# ── Main pivot council orchestrator ───────────────────────────────────────────

def run_pivot_council(task_file: Path, failure_trace: str, pivot_cycle: int = 1) -> bool:
    """
    Run the 3-stage pivot council for a failed task.
    Returns True if a new pivoted task was successfully queued.
    """
    if pivot_cycle > MAX_PIVOT_CYCLES:
        logger.error(f"[HARD_ABORT: PHYSICS WALL] Exhausted {MAX_PIVOT_CYCLES} pivot cycles for {task_file}.")
        return False

    with open(task_file, encoding="utf-8") as f:
        original_task = json.load(f)

    logger.info(f"Pivot Council activated for: {task_file.name} (cycle {pivot_cycle}/{MAX_PIVOT_CYCLES})")

    # Stage 1
    research = stage_research(original_task, failure_trace, pivot_cycle)

    # Stage 2
    architect_plan = stage_architect(original_task, failure_trace, research, pivot_cycle)

    # Stage 3
    planner_output = stage_planner(original_task, architect_plan, pivot_cycle)

    if not planner_output.startswith("APPROVED"):
        logger.warning("Planner REJECTED the architect plan. Logging for manual review.")
        rejection_log = LOG_DIR / f"pivot_rejected_{task_file.stem}_cycle{pivot_cycle}.md"
        rejection_log.write_text(
            f"# Pivot Rejection — Cycle {pivot_cycle}\n\n"
            f"## Architect Plan\n{architect_plan}\n\n"
            f"## Planner Rejection\n{planner_output}",
            encoding="utf-8",
        )
        logger.info(f"Rejection saved to: {rejection_log}")
        return False

    # Extract the approved plan text
    approved_plan = planner_output.partition("APPROVED PIVOT PLAN:")[2].strip()
    if not approved_plan:
        approved_plan = planner_output  # fallback: use full output

    # Re-queue as a new prompt file
    timestamp = int(time.time() * 1000)
    new_prompt = {
        "kanban_source": original_task.get("kanban_source", ""),
        "agent_name": original_task.get("agent_name", "cochem-coder"),
        "prompt": approved_plan,
        "task_id": f"pivot_{original_task.get('task_id', 'unknown')}_c{pivot_cycle}",
        "pivot_cycle": pivot_cycle,
        "original_task_file": str(task_file),
    }
    new_task_file = PROMPTS_DIR / f"{timestamp}_pivot_c{pivot_cycle}_{task_file.stem}_prompt.json"
    new_task_file.write_text(json.dumps(new_prompt, indent=4), encoding="utf-8")
    logger.info(f"[PIVOT SUCCESS] New pivoted task queued: {new_task_file.name}")

    return True


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pivot Council — failed task recovery")
    parser.add_argument("--task-file", required=True, help="Path to the failed prompt JSON file")
    parser.add_argument("--failure-trace", default="", help="Error trace or failure summary")
    parser.add_argument("--failure-trace-file", default="",
                        help="File containing the failure trace (alternative to --failure-trace)")
    parser.add_argument("--cycle", type=int, default=1, help="Current pivot cycle number (1-3)")
    args = parser.parse_args()

    trace = args.failure_trace
    if args.failure_trace_file and Path(args.failure_trace_file).exists():
        trace = Path(args.failure_trace_file).read_text(encoding="utf-8")

    success = run_pivot_council(Path(args.task_file), trace, pivot_cycle=args.cycle)
    sys.exit(0 if success else 1)
