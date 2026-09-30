"""Quarantine VM Container OOM Crash Handler (SRS-412-02 Section 8)."""
from __future__ import annotations
import json
import sys
from pathlib import Path

def handle_oom_event(task_id: str, inspect_json_path: str, memory_series_json_path: str) -> dict:
    inspect_data = json.loads(Path(inspect_json_path).read_text(encoding="utf-8"))
    state = inspect_data.get("State", {})
    oom_killed = state.get("OOMKilled", False)
    exit_code = state.get("ExitCode", 0)

    slope = 0.0
    if Path(memory_series_json_path).exists():
        series = json.loads(Path(memory_series_json_path).read_text(encoding="utf-8"))
        if len(series) >= 2:
            t0, m0 = series[0]
            t1, m1 = series[-1]
            dt = t1 - t0
            if dt > 0:
                slope = (m1 - m0) / dt

    record = {
        "task_id": task_id,
        "oom_killed": oom_killed,
        "exit_code": exit_code,
        "memory_slope": slope,
        "status": "QUARANTINED" if (exit_code == 137 or oom_killed) else "ABORTED"
    }
    return record

if __name__ == "__main__":
    if len(sys.argv) >= 4:
        res = handle_oom_event(sys.argv[1], sys.argv[2], sys.argv[3])
        print(json.dumps(res, indent=2))
