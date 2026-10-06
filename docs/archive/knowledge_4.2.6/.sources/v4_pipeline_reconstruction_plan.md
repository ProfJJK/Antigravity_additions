# CoChem V4.1.2 Pipeline Reconstruction Plan

## 1. Executive Summary
The V4.1.2 pipeline is currently structurally compromised. The routing and database layers are intact, but the core execution engine (`code_forge`) is an empty wrapper that illegally delegates to a legacy V3 Gemini script (`task_work_loop.py`). 

This Reconstruction Plan will completely eradicate the V3 legacy bridge and rebuild the Code Forge natively into V4.1.2. The execution will be performed **outside** the broken pipeline via a direct Swarm topology (Fable 5.1 orchestrating Opus 5.5, with Gemini 3.1 Pro performing asymmetric file-by-file audits).

## 2. Execution Topology (The "Outside-In" Protocol)

1. **Phase 1 (Planning & Delegation):** Fable 5.1 will be fed this exact plan and will break it down into granular sub-tasks.
2. **Phase 2 (Implementation):** Opus 5.5 workers will execute the heavy coding. They will **NOT** modify the live pipeline directly. All output will be generated as Git differentials inside `D:\__CoChem\__agentic\v4.1.2\.staging\`.
3. **Phase 3 (Internal Audit):** Fable 5.1 will review the Opus generated differentials for architectural coherence.
4. **Phase 4 (Asymmetric Verification & Merge):** Gemini Pro 3.1 (Me) will assign exactly **one subagent per generated file**. These subagents will verify the physical file against this SRS, ensure zero mocking, confirm the Model Fallback matrix is implemented, and then physically merge the code into the live `src/` tree.

---

## 3. Work Breakdown Structure (The Missing Components)

### Task 1: Native Forge Orchestrator (Eradicating V3)
*   **Target File:** `v4.1.2\src\cochem\dsp\forge\orchestrator.py`
*   **Objective:** Delete the `subprocess.run` call to `.scripts\task_work_loop.py`. Rewrite the `execute()` method to natively invoke `fable_srs_director.py` to handle the Master Orchestrator logic.
*   **Requirements:** Must pass the raw `job_board.db` payload directly to Fable, bypassing the old Gemini API (`agy`) entirely.

### Task 2: Opus Worker Subsystem
*   **Target File:** `v4.1.2\src\cochem\dsp\forge\opus_worker.py` (NEW)
*   **Objective:** Implement the subagent execution logic for the 19 Opus 5.5 workers. 
*   **Requirements:** Must interface natively with the `MODEL_REGISTRY.py` fallback matrix. Must accept assignments from Fable, write code, and return a structured diff.

### Task 3: RAM Disk Concurrency Overhaul
*   **Target Files:** `v4.1.2\src\cochem\dsp\worker_daemon.py` & `v4.1.2\src\cochem\dsp\sandbox.py`
*   **Objective:** Implement the hardware I/O bypass to allow 20 concurrent `claude.exe` instances.
*   **Requirements:** 
    *   Dynamically provision an ephemeral RAM Disk (tmpfs / Windows Junction) for each `worker_daemon` instance.
    *   Inject `CLAUDE_PROJECT_DIR` into the `env` block mapped to the RAM disk so the 500MB+ SQLite caches never hit the NVMe.
    *   Ensure the `.credentials.json` (Auth Token) is safely loaded from the persistent, read-only NVMe drive to prevent subscription drops.

### Task 4: Agent Fallback Matrix Restoration
*   **Target File:** `v4.1.2\src\cochem\MODEL_REGISTRY.py`
*   **Objective:** Restore and wire in the exact fallback table designed in V4.0.0.
*   **Requirements:** 
    *   Implement the routing table: `Fable 5.1 -> Opus 5.5 -> Gemini 1.5 Pro`.
    *   Must catch `RateLimitError`, `ContextOverflow`, and `SubscriptionTokenDropped` exceptions and automatically trigger the Jittered Exponential Backoff or Fallback tier without crashing the `worker_daemon`.

### Task 5: Git Differential Staging Engine
*   **Target File:** `v4.1.2\src\cochem\dsp\forge\git_stager.py` (NEW)
*   **Objective:** Enforce the Rule 18 "WBS Fracture / Git Commit" mandate. 
*   **Requirements:** Opus agents must never overwrite live files. They must produce standard Git patch files (`.patch`) into `.staging/`. Fable will approve the patch, and Gemini will apply it using `git apply`.

---

## 4. Approval Checkpoint
Please review this Markdown Plan. If you approve, I will initialize the outside-in protocol, invoke `claude.exe` manually to spin up Fable 5.1 with this exact prompt, and begin orchestrating the Opus workers into the `.staging` directory.
