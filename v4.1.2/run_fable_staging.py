import sys
from fable_srs_director import invoke_fable_director

prompt_path = r"D:\__CoChem\__agentic\v4.1.2\.staging\concurrency_vm\fable_prompt.txt"
with open(prompt_path, "r", encoding="utf-8") as f:
    prompt = f.read()

print("Invoking Fable 5.1...")
try:
    # 20 minute timeout for git diff workflow
    output = invoke_fable_director(prompt, timeout_sec=1200)
    print("=== SUCCESS ===")
    print(output)
except Exception as e:
    print(f"=== FAILED ===\n{e}")
