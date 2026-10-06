import subprocess
from enum import Enum
from mcp.server.mcpserver import MCPServer

class GPT6Model(str, Enum):
    astra = "gpt-6-astra"
    sol = "gpt-6-sol"
    luna = "gpt-6-luna"

mcp = MCPServer("codex_headless_mcp")

@mcp.tool()
def run_codex_headless(model: GPT6Model, prompt: str, target_dir: str) -> str:
    """
    Submits a single, headless task directly to Codex using GPT-6 models. 
    Use this only when requested by the user for an out-of-pipeline task.
    
    Args:
        model: The GPT-6 model tier to execute the task (gpt-6-astra, gpt-6-sol, gpt-6-luna)
        prompt: The specific instruction or task for the agent to execute
        target_dir: The absolute path to the working directory where the agent should run
    """
    try:
        # Spawn Codex out-of-pipeline headlessly.
        # Uses --yes to bypass interactive confirmation, and subprocess.Popen so it runs in background detached.
        # This mirrors the behavior expected for the out of pipeline task submission.
        
        # Note: on Windows, using creationflags=subprocess.CREATE_NO_WINDOW prevents a console window from popping up
        command = [
            "codex", 
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-p", prompt, 
            "--model", model.value
        ]
        
        subprocess.Popen(
            command,
            cwd=target_dir,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        return f"Success: Task successfully submitted to Codex ({model.value}) in the background at {target_dir}."
    except Exception as e:
        return f"Error submitting task to Codex: {str(e)}"

if __name__ == "__main__":
    mcp.run()
