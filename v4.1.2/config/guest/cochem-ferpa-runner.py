"""FERPA Air-Gapped Execution Plane Runner (SRS-412-02-FR-004)."""
from __future__ import annotations
import hashlib
import json
import subprocess
import sys
from pathlib import Path

def scrub_student_record(record: dict, salt: str) -> dict:
    scrubbed = dict(record)
    for field in ["student_id", "name", "email"]:
        if field in scrubbed and scrubbed[field] is not None:
            raw = f"{salt}:{scrubbed[field]}".encode("utf-8")
            scrubbed[field] = hashlib.sha256(raw).hexdigest()
    return scrubbed

def launch_ferpa_task(task_id: str, raw_payload_path: str, salt: str) -> int:
    raw_data = json.loads(Path(raw_payload_path).read_text(encoding="utf-8"))
    if isinstance(raw_data, list):
        scrubbed = [scrub_student_record(r, salt) for r in raw_data]
    else:
        scrubbed = scrub_student_record(raw_data, salt)

    scrubbed_file = Path(f"/tmp/ferpa_scrubbed_{task_id}.json")
    scrubbed_file.write_text(json.dumps(scrubbed), encoding="utf-8")

    cmd = [
        "docker", "run", "--rm",
        "--name", f"cochem-ferpa-{task_id}",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=2g",
        "--memory", "4096m",
        "--memory-swap", "4096m",
        "--cpus", "2",
        "--pids-limit", "512",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "-v", f"{scrubbed_file.resolve()}:/input:ro",
        "ferpa-sandbox:latest"
    ]
    try:
        return subprocess.run(cmd).returncode
    finally:
        if scrubbed_file.exists():
            scrubbed_file.unlink(missing_ok=True)

if __name__ == "__main__":
    if len(sys.argv) >= 4:
        sys.exit(launch_ferpa_task(sys.argv[1], sys.argv[2], sys.argv[3]))
