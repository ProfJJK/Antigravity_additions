import os
import sys
import subprocess
from pathlib import Path

work_dir = Path(r"D:\__CoChem\__agentic\v4.2.0")
daemon_path = work_dir / "src" / "cochem" / "dsp" / "worker_daemon.py"
daemon_code = daemon_path.read_text(encoding="utf-8")

astra_prompt = f"""You are GPT-6 Astra.
We are doing an out-of-band audit of the CoChem v4.2.0 pipeline.
A critical fail-open vulnerability was discovered: 'agy' blocked headless tool execution, exiting with code 0, and the daemon blindly marked jobs COMPLETED without checking if the file actually changed.
I have patched `worker_daemon.py` to mathematically verify physical file creation/modification (via sha256_pre vs sha256_post) before marking a job COMPLETED, and to raise RuntimeErrors on RAM disk sync failures.

Audit this patched code. Is the fail-open vulnerability definitively closed? Are there any remaining bypasses?

CODE:
{daemon_code}
"""

print("--- STEP 1: Sending to Codex (GPT-6 Astra) ---")
codex_cmd = [
    r"C:\Users\ansac\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe",
    "exec",
    "--model", "gpt-6-astra",
    "--dangerously-bypass-approvals-and-sandbox"
]

# Use input=astra_prompt to pass via stdin to bypass the 8191 char limit
res_astra = subprocess.run(codex_cmd, input=astra_prompt, cwd=str(work_dir), capture_output=True, text=True, encoding="utf-8")
astra_output = res_astra.stdout.strip()
if res_astra.stderr:
    astra_output += "\n\nSTDERR:\n" + res_astra.stderr.strip()

astra_file = work_dir / "Astra_OOB_Audit_Real.md"
astra_file.write_text(astra_output, encoding="utf-8")
print("Astra audit completed. Saved to Astra_OOB_Audit_Real.md.")

print("--- STEP 2: Sending to Claude.exe (Opus 5.5) ---")

env = os.environ.copy()
env.pop("ANTHROPIC_API_KEY", None)

opus_file_path = work_dir / "Opus_Prompt.txt"
opus_prompt = f"""You are Claude Opus 5.5, the Chief Architecture Auditor.
You are performing the second phase of an asymmetric out-of-band audit.
GPT-6 Astra has just reviewed the fail-open physical verification patches to 'worker_daemon.py'.

Here is Astra's audit report:
{astra_output}

Do you concur with Astra's assessment? Are there any remaining vectors where the daemon could silently fail-open and mark an empty execution as COMPLETED?
Output your definitive verdict.
"""
opus_file_path.write_text(opus_prompt, encoding="utf-8")

# Let's use PowerShell to pipe it to claude.exe to avoid char limits on args, or run it through powershell `-Command "Get-Content | claude.exe"`
# Actually Claude may drop into interactive if piped without flags, let's use -p but limit the length, or just pass a file path if claude supports it.
# Wait, let's use standard input, claude supports pipe: `echo "hello" | claude`
claude_cmd = ["pwsh", "-NoProfile", "-Command", r'Get-Content Opus_Prompt.txt | & "C:\Users\ansac\.local\bin\claude.exe"']
res_opus = subprocess.run(claude_cmd, env=env, cwd=str(work_dir), capture_output=True, text=True, encoding="utf-8")
opus_output = res_opus.stdout.strip()
if res_opus.stderr:
    opus_output += "\n\nSTDERR:\n" + res_opus.stderr.strip()

opus_file = work_dir / "Opus_OOB_Audit_Real.md"
opus_file.write_text(opus_output, encoding="utf-8")
print("Opus audit completed. Saved to Opus_OOB_Audit_Real.md.")
print("--- SEQUENCE COMPLETE ---")
