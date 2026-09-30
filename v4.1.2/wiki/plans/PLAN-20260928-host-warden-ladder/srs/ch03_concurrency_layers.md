# Chapter 3: Two-Staged Concurrency Model (ch03)
**Layer 1 CLI-Internal Swarms, Layer 2 Supervised OS Queues, and Memory Safeguards**

- **Document ID**: SRS-412-03
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 1: Host Warden](ch01_host_warden.md) | [Chapter 4: Task Matrix Blackboard](ch04_task_matrix_blackboard.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Two-Staged Concurrency Architecture**, solving the Windows Desktop Heap exhaustion crisis by decoupling CLI-internal sub-agent swarms from OS-level supervised daemon queues.

---

## 2. Boundary
The concurrency model operates across two distinct planes:
1. **Layer 1 (CLI-Level)**: Internal sub-agent execution within a single `claude.exe` Node.js process tree.
2. **Layer 2 (OS-Level)**: Multi-process coordination of background domain daemons communicating over an SQLite WAL task queue.

---

## 3. Definitions
- **Desktop Heap**: A Win32 kernel memory pool exhausted when hundreds of hidden `conhost.exe` console windows are created.
- **Layer 1 CLI Swarm**: A single OS process running Claude Fable 5.1 Director with a 4096 MB V8 heap that multiplexes up to 20 native sub-agents.
- **JIT Jitter**: A randomized 100–500ms delay introduced into daemon poll loops to prevent lock contention.

---

## 4. Functional Requirements

- **SRS-412-03-FR-001**: Layer 1 CLI-level internal swarms shall execute through a single `claude.exe` process hosting Claude Fable 5.1 Director.
- **SRS-412-03-FR-002**: The Layer 1 Director process shall execute with `NODE_OPTIONS="--max-old-space-size=4096"` to prevent V8 JavaScript heap exhaustion during sub-agent multiplexing.
- **SRS-412-03-FR-003**: The Layer 1 Director shall multiplex up to 20 concurrent Claude Opus 5.5 / Sonnet 5 sub-agents within its partitioned memory runtime.
- **SRS-412-03-FR-004**: Layer 2 domain daemons shall operate as independent OS processes coordinated exclusively through the SQLite WAL task queue (`job_board.db`).
- **SRS-412-03-FR-005**: Layer 2 worker CLI processes shall enforce `NODE_OPTIONS="--max-old-space-size=512"`.
- **SRS-412-03-FR-006**: Layer 2 daemon poll loops shall inject randomized JIT startup jitter between 100ms and 500ms to eliminate thundering herd disk contention.
- **SRS-412-03-FR-007**: All Windows subprocess calls shall enforce `creationflags = 0x08000000 | 0x00000200` (`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`).
- **SRS-412-03-FR-008**: Tasks shall be routed based on Cognitive Complexity Scores: Scores 1–4 to Gemini Flash, Scores 5–8 to Sonnet 5, Scores 9–10 to Opus 5.5.

---

## 5. Non-Functional Requirements
- **NFR-CON-01**: Windows Desktop Heap usage shall remain below 15% of allocation limit under full 20-agent execution.
- **NFR-CON-02**: Process handle leaks shall be zero across sustained 48-hour continuous polling runs.
- **NFR-CON-03**: Sub-agent communication overhead within Layer 1 shall average less than 10ms per message.

---

## 6. Interfaces & Process Invocation Implementation

```python
import os
import random
import subprocess
import time
from pathlib import Path

CREATE_NO_WINDOW: int = 0x08000000

def launch_layer1_fable_director(prompt_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--max-old-space-size=4096"
    
    cmd = [
        r"C:\Users\ansac\.local\bin\claude.exe",
        "--model", "claude-fable-5-1",
        "--file", str(prompt_path)
    ]
    
    # Introduce JIT jitter before launch
    time.sleep(random.uniform(0.1, 0.5))
    
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
        timeout=1800
    )
```

---

## 7. Data Models
```json
{
  "concurrency_profile": {
    "layer1_director_pid": 10420,
    "active_subagents": 18,
    "v8_heap_allocated_mb": 1850,
    "layer2_active_daemons": 3,
    "jit_jitter_range_ms": [100, 500]
  }
}
```

---

## 8. Failure Modes & Recovery
- **V8 Heap Warning**: If Director heap approaches 3800 MB, the Director completes in-flight tasks and gracefully spawns a fresh process instance.
- **Subprocess Hang**: Subprocess timeout is enforced at 1740 seconds, terminating hung processes cleanly before the 1800s lease expires.

---

## 9. Test Obligations & Verification
- Test asserts `NODE_OPTIONS` contains `--max-old-space-size=4096` for Director invocations.
- Test asserts CLI arguments contain valid executable, model name, and prompt file path.
- Test verifies subprocess timeout raises `TimeoutExpired` cleanly without orphaned child processes.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-03-FR-001 | V4.1.2 Master Architecture Plan §2.1 | `test_f04_fable_invocation_constructs_valid_cli_arguments` |
| SRS-412-03-FR-002 | V4.1.2 Master Architecture Plan §2.1 | `test_f04_fable_invocation_injects_4096mb_v8_memory_cap` |
| SRS-412-03-FR-007 | System Rule 15 | `test_f04_fable_invocation_enforces_create_no_window_flag` |
