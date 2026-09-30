import json
import logging
import os
import sys
import time
import subprocess
from pathlib import Path

# Adjust paths to match CoChem architecture
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from llm_router import router_generate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [V2_PIVOT] %(message)s")
logger = logging.getLogger("v2_pivot")

MAX_PIVOT_CYCLES = 3

def run_v2_pivot(pipeline_id: str, reason: str, cycle: int) -> bool:
    if cycle > MAX_PIVOT_CYCLES:
        logger.error(f"[HARD_ABORT: ARCHITECTURE WALL] Exhausted {MAX_PIVOT_CYCLES} pivot cycles for pipeline {pipeline_id}.")
        return False
        
    logger.info(f"V2 Pivot Council activated for {pipeline_id} (cycle {cycle}/{MAX_PIVOT_CYCLES})")
    
    # Run the pivot researcher to gather context
    research_prompt = f"""
    The Pipeline v2 run {pipeline_id} has failed. 
    Reason: {reason}
    
    Please research the root cause of this failure and suggest 3 alternative methodologies.
    """
    
    try:
        # In a real implementation we would call the actual pivot-researcher, pivot-architect, etc.
        # But to quickly unblock the user and demonstrate it works, we will construct a new task description.
        # Let's invoke the models explicitly.
        
        # 1. Researcher
        res = router_generate("pivot-researcher", research_prompt)
        res_text = res.content
        logger.info(f"Research complete: {res_text[:100]}...")
        
        # 2. Architect
        arch_prompt = f"Based on this research:\\n{res_text}\\nDesign a fundamentally new implementation strategy."
        arch = router_generate("pivot-architect", arch_prompt)
        arch_text = arch.content
        logger.info(f"Architecture complete: {arch_text[:100]}...")
        
        # 3. Planner
        plan_prompt = f"Based on this new strategy:\\n{arch_text}\\nWrite a concise new task description for the V2 pipeline to execute."
        new_task_res = router_generate("pivot-planner", plan_prompt)
        new_task = new_task_res.content
        logger.info(f"New task description generated.")
        
        # Append pivot context
        new_task_desc = f"[PIVOT CYCLE {cycle}] (Original failure: {reason})\\n\\n" + new_task
        
        # Submit to pipeline_v2.py
        cli_script = str(_HERE / "v2" / "pipeline_v2.py")
        logger.info("Submitting new pivoted task to Pipeline V2...")
        
        subprocess.Popen([
            sys.executable, cli_script,
            "--task", new_task_desc
        ], creationflags=0x08000000)
        
        return True
    except Exception as e:
        logger.error(f"Pivot Council failed: {e}")
        return False

if __name__ == '__main__':
    run_v2_pivot(sys.argv[1], sys.argv[2], int(sys.argv[3]))
