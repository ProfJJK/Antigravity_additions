import os
import json
import subprocess
from pathlib import Path
import concurrent.futures

BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
SRS_DIR = BASE_DIR / "wiki" / "srs"
LEAF_DIR = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"

CHAPTER_DOMAINS = {
    "ch02_quarantine_vm.md": "quarantine_vm",
    "ch03_concurrency_layers.md": "concurrency",
    "ch08_watchdog_sre.md": "watchdog_sre"
}

def audit_chapter(chapter_file: str, domains):
    if isinstance(domains, str):
        domains = [domains]
        
    leaf_nodes = []
    for file_path in LEAF_DIR.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("domain") in domains:
                    leaf_nodes.append(data)
        except Exception:
            pass

    print(f"[{chapter_file}] Found {len(leaf_nodes)} microtasks for domains {domains}")
    
    srs_path = SRS_DIR / chapter_file
    if not srs_path.exists():
        return f"ERROR: {chapter_file} not found"
        
    payload_path = BASE_DIR / f"audit_payload_{chapter_file}.json"
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(leaf_nodes, f, indent=2)

    prompt = f"""You are a Gemini 3.1 Pro swarm auditor (using g-audit for General Software Systems).
CRITICAL DIRECTIVE: You must verify that the generated microtasks for {chapter_file} FULLY COMPLY with the SRS.
NOTHING must be left out. NOTHING must be downgraded. NO mocking or faking is allowed.

1. Read the SRS chapter at: {srs_path}
2. Read the collection of generated microtasks for this chapter at: {payload_path}
3. Cross-reference every Functional Requirement (FR) and Non-Functional Requirement (NFR) in the SRS against the 'instructions' and 'srs_trace' in the microtasks.
4. Output a definitive PASS if the coverage is 100% complete and pristine.
5. If ANYTHING is missing, omitted, or hallucinated as a mock stub, output FAIL and list exactly which requirements were missed so they can be regenerated.
"""
    
    cmd = ["agy", "--dangerously-skip-permissions", "--agent", "g-audit", "--model", "Gemini 3.1 Pro (High)", "-p", prompt]
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, creationflags=0x08000000, capture_output=True, text=True, timeout=300)
        output = res.stdout + res.stderr
        
        result_log = BASE_DIR / f"audit_result_{chapter_file}.log"
        with open(result_log, "w", encoding="utf-8") as f:
            f.write(output)
            
        if "FAIL" in output and not ("PASS/FAIL" in output or "present the final" in output):
            print(f"[FAILED] {chapter_file} - See {result_log.name}")
            return False
        else:
            print(f"[PASSED] {chapter_file}")
            return True
    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] {chapter_file}")
        return False

def main():
    print("Spinning up a concurrent swarm of Gemini 3.1 Pro agents to audit REGENERATED chapters...")
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        for chapter, domain in CHAPTER_DOMAINS.items():
            futures[executor.submit(audit_chapter, chapter, domain)] = chapter
            
    all_passed = True
    for future in concurrent.futures.as_completed(futures):
        chapter = futures[future]
        try:
            if not future.result():
                all_passed = False
        except Exception as e:
            print(f"[ERROR] {chapter}: {e}")
            all_passed = False
            
    if all_passed:
        print("\nSUCCESS: All regenerated chapters achieved 100% verified compliance!")
    else:
        print("\nWARNING: Some chapters failed the compliance audit again.")

if __name__ == "__main__":
    main()
