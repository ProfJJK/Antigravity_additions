import sys
import os
import asyncio
import json
import logging
import pydantic
import subprocess

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROMPTS_DIR = r"d:\__CoChem\__agentic\.scripts\prompts"

class PromptPlan(pydantic.BaseModel):
    agent_name: str
    prompt: str

async def safe_chat_cli(agent_name, prompt, timeout=1200, extract_type="text", pydantic_model=None):
    cmd = ["agy", "--agent", agent_name, "-p", prompt]
    if extract_type == "structured" and pydantic_model:
        cmd.extend(["--output-format", "json", "--json-schema", pydantic_model.schema_json()])
    
    def run_cmd():
        result = subprocess.run(creationflags=0x08000000, cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"agy failed: {result.stderr}")
        if extract_type == "structured":
            try:
                outer = json.loads(result.stdout)
                inner_str = json.dumps(outer.get("structured_output", {}))
                # Clean up potential markdown formatting
                if inner_str.startswith("`json"):
                    inner_str = inner_str[7:-3]
                parsed = json.loads(inner_str)
                if pydantic_model:
                    return pydantic_model(**parsed)
                return parsed
            except Exception as e:
                raise ValueError(f"Failed to parse json: {e}")
        return result.stdout

    return await asyncio.to_thread(run_cmd)

async def plan_prompt_for_task(task_num, task_desc, parent_context):
    prompt = f"""
Given the following project hierarchy:
{parent_context}

The specific task to execute is:
{task_num} - {task_desc}

Please provide:
1. The EXACT execution agent needed for this task (e.g. cochem-coder, ui, artist, cochem-scribe).
2. The exact prompt/instructions to give to that agent.

IMPORTANT RULES FOR THE PROMPT:
- Explicitly instruct the agent to use its tools to read the existing project files to gain context.
- Explicitly instruct the agent to use its tools to write its final code/results to actual files on disk. 
- Explicitly instruct the agent to return a final text report detailing exactly which file paths it modified so the auditor can verify them.
"""
    try:
        plan = await safe_chat_cli("0rchestrator", prompt, extract_type="structured", pydantic_model=PromptPlan)
        if not plan:
            raise ValueError("Structured output failed")
        return plan
    except Exception as e:
        logger.error(f"Failed to plan prompt for {task_num}: {e}")
        raise

async def process_task(i, l1_task, j, l2_task, k, l3_task, sem):
    task_num = f"{i+1}.{j+1}.{k+1}"
    logger.info(f"Planning prompt for Task {task_num}...")
    parent_context = f"Level 1: {l1_task}\nLevel 2: {l2_task}"
    
    async with sem:
        plan = await plan_prompt_for_task(task_num, l3_task, parent_context)
    
    file_name = f"{task_num}_prompt.json"
    file_path = os.path.join(PROMPTS_DIR, file_name)
    
    def write_json():
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(plan.model_dump(), f, indent=4)
            
    await asyncio.to_thread(write_json)
    logger.info(f"Saved {file_path}")

async def main():
    input_path = r"d:\__CoChem\__agentic\.scripts\raw_task_list.json"
    if not os.path.exists(input_path):
        logger.error(f"{input_path} not found. Run task_list.py first.")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        master_task_list = json.load(f)

    os.makedirs(PROMPTS_DIR, exist_ok=True)
    logger.info("Spawning 0rchestrator to plan prompts sequentially...")
    
    sem = asyncio.Semaphore(1)  # Sequential to prevent API rate limits with agy
    
    tasks_to_run = []
    for i, (l1_task, l2_dict) in enumerate(master_task_list.items()):
        for j, (l2_task, l3_list) in enumerate(l2_dict.items()):
            for k, l3_task in enumerate(l3_list):
                tasks_to_run.append(process_task(i, l1_task, j, l2_task, k, l3_task, sem))
                
    results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            logger.error(f"Error planning prompt: {res}")
            
    logger.info("Finished planning all prompts.")

if __name__ == "__main__":
    asyncio.run(main())
