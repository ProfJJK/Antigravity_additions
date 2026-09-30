# V4 Pipeline Core Modification Authorization
# Issued: 2026-09-25T18:38:51-05:00
# Authorized by: User (ansac)
# Conversation: dc9a4f9e-9691-47b6-ad55-8afa32c08eac

## Scope of Authorization

The following ReadOnly core pipeline files are AUTHORIZED for modification
for the specific purposes listed below ONLY:

### Authorized Files & Purposes

1. **`D:\__CoChem\__agentic\.scripts\task_work_loop.py`**
   - Git commit wrapping around chunk execution (per-chunk baseline commit + result commit)
   - Docker container isolation for code/test execution phases
   - Integration with the OS-level AdaptiveLimiter (resource_governor.py)
   - Preservation of `_artifact_protocol` and Fracture Manifest chunking is MANDATORY

2. **`D:\__CoChem\__agentic\.scripts\kanban_v3_daemon.py`**
   - 3-Daemon architecture split (Worker queue daemon)
   - AdaptiveLimiter integration for dynamic worker pool sizing

3. **`D:\__CoChem\__agentic\.scripts\cochem_meta_auditor.py`**
   - 3-Daemon architecture split (Meta-Auditor daemon)
   - Process census extension for Docker container awareness

4. **`D:\__CoChem\__agentic\v2\watchdog.py`** (or `.scripts\watchdog.py`)
   - 3-Daemon architecture split (Watchdog daemon)
   - Docker container reaper for orphaned containers after worker kill

5. **`D:\__CoChem\__agentic\.scripts\concurrency_guard.py`**
   - Process census extension for Docker container tracking

6. **`D:\__CoChem\__agentic\v2\pipeline_v2_config.py`**
   - V4 configuration constants (Docker image pin, timeout values, worker limits)

7. **`D:\__CoChem\__agentic\cochem_kanban.py`**
   - PromptPayload schema updates (chunk_id, parent_task_id, fracture_depth fields for V4 chunks)

8. **`D:\__CoChem\__agentic\cochem_kanban_mcp.py`**
   - MCP tool updates to produce V4 payload format with chunk metadata

9. **`D:\__CoChem\__agentic\v2_pivot_orchestrator.py`**
   - Pivot logic updates for Docker-awareness

10. **`D:\__CoChem\__agentic\llm_router.py`**
   - ONLY if needed to reconcile fallback chain correctness (V2 AUDIT REPORT CRITICAL-1)

11. **`D:\__CoChem\__agentic\v2\task_planning_orchestra.py`**
   - Fix ripgrep hidden-directory blindness (--hidden flag)
   - Increase revision budget and add smart revision logic
   - Add research confidence gate (deterministic, not self-attested)
   - Separate planning-phase audit from implementation-phase audit
   - Fix Haiku fallback chain (remove broken model)
   - Add path containment security for evidence gathering
   - Fix dispatch gate to block any verdict != PASS
   - 7-stage flow structure MUST be preserved

### NOT Authorized
- `v2\MODEL_REGISTRY_V2.py` — NO MODIFICATIONS. Model registry stays pristine.
- Removal or replacement of `_artifact_protocol` or Fracture Manifest chunking
- Any change to the 10-phase TDD cycle structure
- Any change to the asymmetric audit boundary rules

### Conditions
- All modifications MUST go through the full Orchestrator pipeline (Fable → Opus audit)
- SHA256 hashes of all files MUST be recorded before and after modification
- Git commits MUST be used to track every change for revert capability
