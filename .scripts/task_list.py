import json
import subprocess
import asyncio
import sys
import os
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_HOME = os.path.expanduser("~")

agent_semaphore = asyncio.Semaphore(3)

class TaskList(BaseModel):
    tasks: List[str] = Field(description="List of high-level tasks to accomplish the goal.")

def extract_json_object(text: str) -> str:
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    
    idx = text.find('"tasks"')
    if idx != -1:
        start_idx = text.rfind('{', 0, idx)
        if start_idx != -1:
            bracket_count = 0
            for i in range(start_idx, len(text)):
                if text[i] == '{':
                    bracket_count += 1
                elif text[i] == '}':
                    bracket_count -= 1
                    if bracket_count == 0:
                        return text[start_idx:i+1]
    return text

def run_cmd(agent_name: str, prompt: str, extract_type: str = "raw", pydantic_model: Optional[Any] = None, timeout=3600) -> Any:
    cmd = ["agy", "--agent", agent_name, "-p", prompt, "--print-timeout", "60m"]
    if extract_type == "structured" and pydantic_model:
        cmd.extend(["--output-format", "json", "--json-schema", pydantic_model.schema_json()])
    
    result = subprocess.run(creationflags=0x08000000, cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"agy failed: {result.stderr}")
    if extract_type == "structured":
        try:
            parsed = json.loads(result.stdout)
            if pydantic_model:
                response_data = parsed.get("response", "")
                if isinstance(response_data, str):
                    response_data = extract_json_object(response_data).strip()
                    if not response_data:
                        raise ValueError(f"Empty response after stripping. Full parsed: {parsed}")
                    model_data = json.loads(response_data)
                else:
                    model_data = response_data
                return pydantic_model(**model_data)
            return parsed
        except Exception as e:
            err_msg = f"Failed to parse json: {e}\nRAW PARSED={parsed.get('response', '')}"
            raise ValueError(err_msg)
    return result.stdout

async def safe_chat_cli(agent_name: str, prompt: str, extract_type: str = "raw", pydantic_model: Optional[Any] = None) -> Any:
    async with agent_semaphore:
        return await asyncio.to_thread(run_cmd, agent_name, prompt, extract_type, pydantic_model)

async def get_tasks(prompt: str) -> List[str]:
    data = await safe_chat_cli("cochem-sdp-manager", prompt, extract_type="structured", pydantic_model=TaskList)
    return data.tasks

async def process_l3(task: str) -> tuple[str, List[str]]:
    prompt = f"Break down the following L2 task into highly specific L3 component-level implementation tasks. Output structured list. Task: {task}"
    try:
        data = await safe_chat_cli("cochem-sdp-manager", prompt, extract_type="structured", pydantic_model=TaskList)
        return task, data.tasks
    except Exception as e:
        logger.error(f"Error processing task {task}: {e}")
        return task, []

async def process_l2(l1_task: str, research_context: str) -> tuple[str, Dict[str, List[str]]]:
    prompt = f"Break down the following Level 1 task into more detailed Level 2 technical tasks. Given this research context. Task: {l1_task}\nContext: {research_context}"
    try:
        data = await safe_chat_cli("cochem-sdp-manager", prompt, extract_type="structured", pydantic_model=TaskList)
        l2_tasks = data.tasks
    except Exception as e:
        logger.error(f"Error breaking down L1 task {l1_task}: {e}")
        return l1_task, {}
        
    l3_dict = {}
    results = await asyncio.gather(*(process_l3(t) for t in l2_tasks), return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            logger.error(f"Error in L3 breakdown: {res}")
        else:
            t, l3 = res
            l3_dict[t] = l3
            
    return l1_task, l3_dict

async def main():
    task = sys.argv[1] if len(sys.argv) > 1 else input("Enter task description: ")
    
    logger.info("Spawning researcher...")
    res_prompt = f"Create a folder at {os.path.join(USER_HOME, '.gemini', '.agents', '.sources')} if it doesn't exist. Research the following task to gather necessary info. Output a summary of your research. Task: {task}"
    research_context = await safe_chat_cli("researcher", res_prompt)
    
    logger.info("Researcher complete. Context gathered.")
    logger.info("Spawning cochem-sdp-manager for level 1 tasks...")
    
    l1_tasks = await get_tasks(f"Create high-level Level 1 tasks for the following goal, given this context. Task: {task}\nContext: {research_context}")
    
    logger.info(f"Found {len(l1_tasks)} L1 tasks. Spawning workers for L2/L3 breakdown...")
    
    results = await asyncio.gather(*(process_l2(t, research_context) for t in l1_tasks), return_exceptions=True)
    
    master_task_list = {}
    for k_v in results:
        if isinstance(k_v, Exception):
            logger.error(f"Error processing L1 task: {k_v}")
        else:
            k, v = k_v
            master_task_list[k] = v

    output_path = r"d:\__CoChem\__agentic\.scripts\raw_task_list.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(master_task_list, f, indent=4)
        
    logger.info(f"Task list generation complete. Saved to {output_path}")

if __name__ == "__main__":
    asyncio.run(main())
