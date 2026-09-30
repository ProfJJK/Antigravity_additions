# RECOVERY REPORT — bundle 20260930T073958Z_1

- **Recovery session**: v4-1-2-e6 (claude.exe PID 37452, launched by watchdog PID 31124)
- **Bundle timestamp**: 1790753998 (2026-09-30T07:39:58Z, 02:39:58 CDT)
- **Verdict in bundle**: COLLAPSED — `Signal ZD-8: 33 illegal queue state(s)`
- **Outcome**: Signal ZD-8 = **0** on the live board (verified read-only after COMMIT). Three code defects fixed. `job_board.db` was neither deleted nor recreated; one `BEGIN IMMEDIATE` transaction touched 33 `jobs` rows and inserted 1 `recovery_telemetry` row.

## 1. What the bundle showed

| Item | Value |
|---|---|
| Status counts (snapshot = live at start) | BLOCKED 88, COMPLETED 141, FAILED 33, total 262 |
| Stale leases (`status != RUNNING AND lease_owner NOT NULL`) | 0 |
| PENDING with attempts >= max_attempts | 0 |
| RUNNING rows / live DSP workers | 0 / 0 (all `.evidence/dsp_worker/worker_*.dead`) |
| frozen_worker_pids | [] |

All 33 ZD-8 hits were `status = 'FAILED'`. The bundle's `signal_zd8_rows` lists only 25 (the watchdog caps the listing at 25); the read-only snapshot holds all 33. They fall into three families.

### Family A — 3 rows: bridge subprocess crash recorded as an audit verdict
`MC-DSP-15` (attempts 10/10), `MC-HW-55` (9/10), `MC-CONCURRENCY-FIX` (1/10). `error_log` = `code_forge.audit() rejected result: {"status": "FAILED", ... "error": "Traceback ... task_work_loop.py line 699 safe_chat_cli ... run_in_executor"}`.

Timeline reconstructed from `..\.scripts\task_work_loop.log` and `updated_at`:
- 22:19 CDT `MC-HW-55` loaded; P1–P3 ran on the **agy legacy path** (`llm_router not available — falling back to agy-only dispatch`); at 22:34:10 P4 (`cochem-coder`) started and the process died within a second. `updated_at` = 1790739250 = 22:34:10.
- 22:35 `MC-DSP-15` same sequence; died at 22:49:18 in P4. `updated_at` = 1790740158 = 22:49:18.
- 22:49 `MC-CONCURRENCY-FIX` same; died at 23:04:52 when the pivot-strategist call went through the same agy path. `updated_at` = 1790741092.

Each task had exactly **one** `task_work_loop.py` run in the log. The earlier 8–9 attempts on MC-DSP-15 / MC-HW-55 were `code_forge.execute() raised TypeError/AttributeError: PipelineTelemetry ...` faults (the same error that BLOCKED their siblings MC-DSP-01..10 at attempts 10) which correctly went through `retry_or_block_job` (that is where their stale `not_before` came from). Once the telemetry bug was fixed, the next attempt reached the bridge and crashed.

Why the bridge crashes: `D:\__CoChem\__agentic\llm_router.py:976` does `from v2.MODEL_REGISTRY_V2 import MODEL_REGISTRY`, and the whole `..\v2` tree is deleted in the working tree (`git status` shows `D ../v2/MODEL_REGISTRY_V2.py` etc.). The import fails, every agent falls back to the `agy` subprocess, and the P4 call raises inside `run_cmd` (uncaught → non-zero exit → traceback on stderr). This environment fault is **outside v4.1.2** and is not fixed here; see §5.

### Family B — 10 rows: payload_json is raw Markdown
ids 368–377 (`status_mcp_v3_upgrade`, `audit_youtube_pipeline_v3`, `implement_spycfit_gpu`, `implement_spycfit_integration`, `audit_spycfit_spoofing_resolution`, `audit_spycfit_physics_autopsy`, `kanban_improve_BASE_Student_Flawless_UI_Phase1_62388135`, `kanban_improve_BASE_Fable_Production_Ready_Phase2_14de0654`, `kanban_srs_hybrid_rag_upgrade_31227497`, `migrate_mcp_to_v3`), created 1790733609 (21:00 CDT 09-29). `error_log` = `dispatch rejected: Expecting value: line 1 column 1 (char 0)`.

Source: `migrate_v3_to_v412.py` reads each v3 kanban `payload_uri` file, tries `json.loads`, and on failure does `except Exception: pass`, inserting the raw Markdown as `payload_json`. The worker's `json.loads` then fails and `fail_job` writes FAILED. The same script also ran `DELETE FROM jobs WHERE id > 291`, which removed ids 292–365 (the batch 07–10 rows); the snapshot confirms ids 292–365 are absent.

### Family C — 20 rows: stub payload without `target_file`
ids 379–398: `MC-SRE-11..23`, `MC-TDD-05..10`, `MC-TDD-12`, created 1790736907 (21:55 CDT). Payload is `{"task_id","job_type","priority","target","agent_name":"pivot-strategist","description":"Execute task X from Batch 07"}`. `CodeForgeOrchestrator.validate()` requires `target_file`, so `fail_job` wrote `code_forge.validate() rejected payload`. These rows were re-inserted after the migration DELETE above; the inserting script was not found in the repo (an ad-hoc insert). Canonical WBS payloads with `target_file` and `verification_command` exist at `wiki/wbs/leaf_nodes/<task_id>.json`. Per prior session records, the batch 07 work these rows describe was already deployed live on 2026-09-29 (277 tests passing).

## 2. Root cause against the SRS

1. **ch08 §3 / FR-009**: `FAILED` is by definition an illegal queue state. So every non-retryable verdict written by the DSP worker pages Fable. That is by design ("a single FAILED row trips COLLAPSED"); the defects are in *what got classified as a verdict*.
2. **Defect 1 — `src/cochem/dsp/forge/orchestrator.py` `execute()`**: a non-zero exit of the `task_work_loop.py` bridge (an infrastructure crash with a traceback) was returned as `{"status": "FAILED"}`; the worker then took the `audit()`-rejected path → `fail_job` → FAILED → ZD-8. A TDD verdict (PIVOT/QUARANTINE) exits 0, so rc != 0 is never a verdict. Per the worker's own contract (module docstring: "Retryable failures return to PENDING rather than FAILED") it should have raised so `retry_or_block_job` applied backoff / BLOCKED.
3. **Defect 2 — `src/cochem/dsp/worker_daemon.py` `fail_job()`**: wrote FAILED unconditionally, ignoring `max_attempts`. This violates **ch04 FR-005** (attempts exhausted → terminal BLOCKED) and diverges from `blackboard/lifecycle.determine_failure_status`. Result: MC-DSP-15 sat at attempts 10/10 as FAILED, a row that can never be claimed again and can only page the watchdog. It also left a stale `not_before`.
4. **Defect 3 — `migrate_v3_to_v412.py`**: swallowed JSON errors and stored Markdown in `payload_json`; destructively deleted ids > 291 (violates **ch04 FR-008** idempotent, additive injection).
5. Contributing: no live DSP workers, so nothing could ever move these rows; two `watchdog_sre.py` instances are running (PIDs 31124 and 37536, both console-launched, parents gone, no single-instance guard), and each launched its own recovery session (bundles `20260930T073958Z` and `20260930T073958Z_1`). The sibling session (v4-1-2-ae, PID 3456) confirmed by message that it made no writes and deferred to this session.

## 3. Job board changes (live `job_board.db`)

Script: `repair_job_board.py` in this bundle directory (kept for audit). One connection, `PRAGMA busy_timeout=30000`, `BEGIN IMMEDIATE`, every UPDATE guarded by `WHERE task_id=? AND status='FAILED'` and asserted to hit exactly 1 row; the verbatim ch08 §3 ZD-8 query was evaluated inside the transaction (0) and the row total (262) asserted before COMMIT. Every `error_log` got an appended line starting with `[RESUSCITATION 2026-09-30 bundle 20260930T073958Z_1 session v4-1-2-e6]` explaining the transition; original error text was preserved. `updated_at` = 1790754443 on all 33 rows.

| Rows | From → To | Other columns | Rationale |
|---|---|---|---|
| `MC-DSP-15` (10/10), `MC-HW-55` (9/10) | FAILED → BLOCKED | lease NULL, `not_before` NULL | ch04 §3 poison-pill quarantine / FR-005; last attempt was an infrastructure crash, not a verdict; the crash is deterministic until llm_router imports again |
| `MC-CONCURRENCY-FIX` (1/10) | FAILED → PENDING | `not_before` = 1790758043 (+3600 s), attempts unchanged (1) | transient infrastructure fault on first attempt; with Defect 1 fixed, a repeat crash is retried with backoff and quarantined at max_attempts without paging |
| 10 Markdown rows (ids 368–377) | FAILED → BLOCKED | `payload_json` wrapped into `{"task_id", "prompt": <original markdown>, "target_file": "D:/__CoChem/__agentic/v3", "target": ..., "payload_provenance": ...}`; attempts unchanged (1) | payload was not executable; wrapping is exactly what the migration script intended for JSON payloads; quarantined for operator triage (set `status='PENDING', attempts=0` to run) |
| 20 stub rows (ids 379–398) | FAILED → BLOCKED | payload unchanged | no `target_file`; canonical payload is `wiki/wbs/leaf_nodes/<id>.json`; work already deployed live; operator decides re-run vs. COMPLETED with a real `result_path` (no blind COMPLETED written) |
| `recovery_telemetry` | +1 row | tier 3, action `fable_resuscitation`, details JSON | audit trail |

Result: `BLOCKED 120, COMPLETED 141, PENDING 1` (total 262). ZD-8 = 0.

Rows deliberately **not** changed: the 88 pre-existing BLOCKED rows (legal state), the 141 COMPLETED rows.

## 4. Code changes

Patch script: `patch_code.py` in this bundle directory. All three files compile; changes are uncommitted in the working tree.

1. `src/cochem/dsp/worker_daemon.py`
   - `fail_job()` now writes `status = CASE WHEN attempts >= max_attempts THEN 'BLOCKED' ELSE 'FAILED' END` and clears `not_before` (FR-005 / FR-009). The three call sites (`dispatch rejected`, `validate() rejected`, `audit() rejected`) report `"BLOCKED"` when attempts are exhausted so the returned status matches the row.
2. `src/cochem/dsp/forge/orchestrator.py`
   - New `CodeForgeBridgeError(RuntimeError)`; `execute()` raises it when `task_work_loop.py` exits non-zero (FSM still transitions to FAILED and telemetry is finished). The worker's existing `except Exception` around `execute()` routes this to `retry_or_block_job` (PENDING with jittered backoff, BLOCKED at max_attempts). Exit 0 results are unchanged.
3. `migrate_v3_to_v412.py`
   - Removed `DELETE FROM jobs WHERE id > 291`; non-JSON payloads are wrapped into `{"prompt", "target", "target_file", "task_id", "payload_provenance"}`; `INSERT` → `INSERT OR IGNORE` (FR-008).

Test results: see §6 (appended after the run).

## 5. Open items for the operator (not fixed here)

- **`..\v2` tree deleted / `llm_router.py:976` import failure** — every code_forge job that reaches the bridge will crash at the first agy call for a coder/strategist agent. Restore `v2/MODEL_REGISTRY_V2.py` (it is in git history) or point the import at the top-level `MODEL_REGISTRY_V2`. Until then, re-queuing BLOCKED code_forge rows only burns cycles (safely, after the fix, but wastefully).
- **Two watchdog instances** (PIDs 31124, 37536) with no singleton guard. Kill one; consider a pid-file guard in `SREWatchdog.run()`. The installed scheduled task from `provisioning/Install-PipelineDaemons.ps1` is not registered (`schtasks` shows none).
- **No DSP workers running.** The single PENDING row (`MC-CONCURRENCY-FIX`) will not move until a worker starts.
- **Stub rows MC-SRE-11..23 / MC-TDD-05..10,12**: decide between restoring the leaf-node payload and re-running, or marking COMPLETED with a real `result_path` after running each node's `verification_command`. Sibling session v4-1-2-ae was running those verification commands read-only at the time of writing.
- Both watchdogs re-evaluate ZD-8 every 30 s and should now report HEALTHY; the cooldown signature (`bdfba16f9b27c414`) prevents a re-trigger for 3600 s in any case.

## 6. Verification

- `PYTHONPATH=src python -m pytest tests/test_stage7_pipeline_daemons.py -q` → **22 passed** in 14.4 s (includes `test_validation_failure_marks_failed_and_trips_zd8`, `test_unroutable_job_is_failed_not_crashed`, `test_execute_exception_requeues_with_backoff_then_blocks`).
- Targeted probe (temp DB): `fail_job` on a RUNNING row at attempts 10/10 → `BLOCKED`, `not_before` NULL; at 1/10 → `FAILED`; ZD-8 counts only the latter. `CodeForgeOrchestrator.execute()` with a mocked non-zero bridge exit raises `CodeForgeBridgeError: task_work_loop.py exited 1 for T: RuntimeError: agy failed`.
- Live board after COMMIT, read via `file:job_board.db?mode=ro`: ZD-8 = 0; `BLOCKED 120, COMPLETED 141, PENDING 1`.
- Wider regression: `tests/test_dsp_package.py tests/test_stage4_tdd_sandboxing.py tests/test_stage5_merge_lifecycle.py tests/test_stage6_activation_mcp.py` → **73 passed** in 6.6 s.
- Watchdog heartbeat (`.evidence/watchdog/watchdog_heartbeat.json`) at 1790754598: `"verdict": "HEALTHY"` (cycle 21, written by watchdog PID 37536).
