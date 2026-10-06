import os
import sys
import subprocess
from pathlib import Path

work_dir = Path(r"D:\__CoChem\__agentic\v4.2.0")
mcp_script_path = Path(r"D:\__agentic\mcp\codex_headless_mcp.py")
original_mcp_code = mcp_script_path.read_text(encoding="utf-8")

astra_prompt = f"""You are GPT-6 Astra.
Your task is to completely rewrite this Python MCP server (`codex_headless_mcp.py`).
Currently, it uses `subprocess.Popen` with `stdout=subprocess.DEVNULL`, destroying the model's output. This forces the agent swarm to hallucinate because they never see your actual responses.

Requirements:
1. It must execute `codex exec --dangerously-bypass-approvals-and-sandbox -p <prompt> --model <model>`.
2. It must synchronously run the command, capture stdout and stderr, and return the FULL output back through the MCP tool return value, rather than a fire-and-forget success message.
3. Ensure no deadlocks occur during subprocess capture (e.g., use `subprocess.run(capture_output=True, text=True, encoding="utf-8")`).
4. Provide the COMPLETE, ready-to-run Python script inside a ```python block.

CURRENT BROKEN CODE:
{original_mcp_code}
"""

MAX_ITERATIONS = 5
iteration = 1

while iteration <= MAX_ITERATIONS:
    print(f"--- ITERATION {iteration}: Sending to Codex (GPT-6 Astra) ---")
    codex_cmd = [
        r"C:\Users\ansac\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe",
        "exec",
        "--model", "gpt-6-astra",
        "--dangerously-bypass-approvals-and-sandbox"
    ]
    
    res_astra = subprocess.run(codex_cmd, input=astra_prompt, cwd=str(work_dir), capture_output=True, text=True, encoding="utf-8")
    astra_output = res_astra.stdout.strip()
    if res_astra.stderr:
        astra_output += "\n\nSTDERR:\n" + res_astra.stderr.strip()
        
    print(f"Astra completed. Output length: {len(astra_output)}")
    
    print(f"--- ITERATION {iteration}: Sending to Claude.exe (Opus 5.5) for Audit ---")
    opus_prompt = f"""You are Claude Opus 5.5, the Chief Architecture Auditor.
GPT-6 Astra has rewritten the `codex_headless_mcp.py` MCP server. It must capture Codex's output and return it via MCP, avoiding the DEVNULL black hole.

Here is Astra's proposed rewrite:
{astra_output}

Audit this code. 
- Does it use `subprocess.run` with `capture_output=True` (or equivalent) instead of `Popen` with `DEVNULL`?
- Does it return the actual `stdout`/`stderr` from the tool function?
- Are there any subprocess deadlocks or timeout risks?
- Does it correctly construct the `codex exec` command?

If the code is flawless and ready for production, you MUST include the exact phrase "VERDICT: PERFECT" anywhere in your response.
If there are flaws, list them clearly so Astra can fix them. Do NOT include the phrase "VERDICT: PERFECT".
"""
    
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    opus_file_path = work_dir / f"Opus_Prompt_Iter{iteration}.txt"
    opus_file_path.write_text(opus_prompt, encoding="utf-8")
    
    claude_cmd = ["pwsh", "-NoProfile", "-Command", f'Get-Content Opus_Prompt_Iter{iteration}.txt | & "C:\\Users\\ansac\\.local\\bin\\claude.exe"']
    res_opus = subprocess.run(claude_cmd, env=env, cwd=str(work_dir), capture_output=True, text=True, encoding="utf-8")
    opus_output = res_opus.stdout.strip()
    
    print(f"Opus completed. Verdict check...")
    
    if "VERDICT: PERFECT" in opus_output:
        print("Opus approved the code! Saving final artifacts.")
        (work_dir / "Final_Astra_MCP.md").write_text(astra_output, encoding="utf-8")
        (work_dir / "Final_Opus_Verdict.md").write_text(opus_output, encoding="utf-8")
        break
    else:
        print("Opus found flaws. Bouncing back to Astra.")
        astra_prompt = f"""Claude Opus 5.5 reviewed your code and found the following flaws:
        
{opus_output}
        
Please rewrite the MCP server Python code to fix these exact issues. Provide the complete corrected code inside a ```python block.
"""
        iteration += 1

if iteration > MAX_ITERATIONS:
    print("Reached max iterations without PERFECT verdict.")
    (work_dir / "Final_Astra_MCP.md").write_text(astra_output, encoding="utf-8")
    (work_dir / "Final_Opus_Verdict.md").write_text(opus_output, encoding="utf-8")

print("--- LOOP COMPLETE ---")
