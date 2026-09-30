# Recovery Report: Signal ZD-8 collapse, bundle 20260930T073958Z

- Session: v4-1-2-ae (recovery PID 3456, launched by watchdog PID 37536)
- Bundle: `.evidence/watchdog/bundles/20260930T073958Z/evidence_bundle.json`
- Written: 2026-09-30, host AetherDesk
- Outcome: Signal ZD-8 = 0 on the live board. The board repair and the code fixes were applied by the sibling
  session v4-1-2-e6 (bundle `20260930T073958Z_1`, report there). This session made no writes to `job_board.db`
  and no edits under `src/`. This document is the independent diagnosis, the verification evidence, and the
  list of follow-ups.

## 1. Why there were two recovery sessions

Two `watchdog_sre.py` processes (PIDs 37536 and 31124) started at 02:39:58, in the same second, from two parent
processes that have since exited. `watchdog_sre.py` has no single-instance guard, so each one evaluated ZD-8,
assembled its own bundle (`20260930T073958Z` and `20260930T073958Z_1`), and launched its own `claude.exe`
recovery process (PIDs 3456 and 37452). `watchdog_state.json` records PID 37452 as `recovery_pid`.

The two sessions coordinated over cross-session messaging. v4-1-2-e6 claimed the write path; this session
stood down before touching the board. Both watchdogs now report HEALTHY (heartbeat cycle 20, PID 37536).

## 2. What the bundle showed

| Field | Value |
|---|---|
| Verdict | COLLAPSED, sole failure "Signal ZD-8: 33 illegal queue state(s)" |
| Status counts | BLOCKED 88, COMPLETED 141, FAILED 33 |
| RUNNING / PENDING | 0 / 0 (no DSP worker was alive; four `worker_*.dead` markers in `.evidence/dsp_worker`) |
| Frozen workers | none |
| Lease violations | none: every FAILED row already had `lease_owner = NULL` (FR-009 held) |

The snapshot and the live board were byte-identical at the time of diagnosis (262 rows, same max id, zero
differing rows). All 33 illegal rows were `status = 'FAILED'`; none matched the other two ZD-8 clauses.

## 3. Root-cause analysis against ch04 and ch08

ch08 FR-009 makes a single FAILED row a COLLAPSED state by design, and the DSP worker writes FAILED only for a
pipeline verdict (validate() or audit() rejection). So the collapse is the designed escalation; the question is
why 33 verdict failures accumulated between 21:04 and 23:04 on 2026-09-29 with no watchdog running to page anyone.
They fall into three groups with three distinct causes.

### Group A: 10 rows with Markdown in `payload_json` (ids 368-377)

`status_mcp_v3_upgrade`, `audit_youtube_pipeline_v3`, `implement_spycfit_gpu`, `implement_spycfit_integration`,
`audit_spycfit_spoofing_resolution`, `audit_spycfit_physics_autopsy`, two `kanban_improve_BASE_*` rows,
`kanban_srs_hybrid_rag_upgrade_31227497`, `migrate_mcp_to_v3`.

`migrate_v3_to_v412.py` read each legacy kanban prompt file, tried `json.loads`, swallowed the exception with
`except Exception: pass`, and inserted the raw Markdown. The worker's `json.loads(job["payload_json"])` then raised
`Expecting value: line 1 column 1`, which `process_job` maps to "dispatch rejected" and FAILED. The same script also
ran `DELETE FROM jobs WHERE id > 291`, which is how ids 292-365 disappeared. No schema-conformant payload exists for
these rows; they are v2/v3 kanban tasks unrelated to the v4.1.2 WBS.

### Group B: 20 rows rejected by `code_forge.validate()` (ids 379-398)

MC-SRE-11 to MC-SRE-23 and MC-TDD-05, 06, 07, 08, 09, 10, 12. Each payload is a stub:
`{"task_id", "job_type", "priority", "target", "agent_name": "pivot-strategist", "description": "Execute task X from Batch 07"}`.
There is no `target_file`, so `CodeForgeOrchestrator.validate()` returns False and the worker writes FAILED.

`reingest_batch7.py` produced these rows: it selects the FAILED batch-07 rows, appends a failure note to
`description`, sets `agent_name = 'pivot-strategist'`, resets `attempts = 0`, and sets PENDING, but it never restores
`target_file`. The canonical payloads with `target_file`, `instructions`, and `verification_command` exist at
`wiki/wbs/leaf_nodes/<task_id>.json` for 19 of the 20; there is no node for MC-SRE-14.

### Group C: 3 rows rejected by `code_forge.audit()` (ids 136, 169, 399)

MC-DSP-15 (attempts 10/10), MC-HW-55 (9/10), MC-CONCURRENCY-FIX (1/10). `CodeForgeOrchestrator.execute()` ran
`..\.scripts\task_work_loop.py`, which exited non-zero with an uncaught traceback ending in
`safe_chat_cli -> asyncio.to_thread(run_cmd) -> subprocess.run` at phase P4 (the coder). The orchestrator wrapped that
crash in `{"status": "FAILED"}`, `audit()` rejected it, and the worker wrote FAILED. Two layers of cause:

1. The entire `..\v2` tree is deleted in the working tree (unstaged, see `git status`). `llm_router.py` line 976
   imports `v2.MODEL_REGISTRY_V2`, so the router fails to load and `task_work_loop.py` logs
   "llm_router not available; falling back to agy-only dispatch" for every agent.
2. The legacy path runs `agy --agent <name> -p <prompt>` with the whole prompt as one argv element. The P4 coder
   prompt is the task prompt, plan brief, context dossier, generated test file, and current file targets. For these
   runs the dossier alone is 11-24 KB and the tests 16-24 KB, so the prompt exceeds the 32,767-character Windows
   command-line limit and `subprocess.run` raises `[WinError 206] The filename or extension is too long` before
   `agy` starts. A 40,000-character argv reproduces the error on this host, and a short
   `agy --agent cochem-coder -p "Reply with the single word OK"` succeeds (exit 0), which rules out a missing or
   broken agent definition. P1 to P3 prompts are smaller and did go through `agy`, which matches the log.

Two defects in v4.1.2 code amplified Group C:

- `worker_daemon.fail_job()` wrote FAILED unconditionally, ignoring `max_attempts`. MC-DSP-15 sat at 10/10 as
  FAILED, a row that can never be claimed and pages the watchdog forever. ch04 FR-005 and
  `blackboard/lifecycle.determine_failure_status` both say BLOCKED at `attempts >= max_attempts`. It also left a
  stale `not_before` from an earlier retry.
- `forge/orchestrator.execute()` turned an infrastructure crash of the bridge process into a verdict. The worker
  has a retry path (`retry_or_block_job`: PENDING with backoff, BLOCKED at max) that is only reachable when
  `execute()` raises. A bridge crash never reached it.

The 88 rows already BLOCKED before the collapse all carry an older `PipelineTelemetry` signature mismatch
(`__init__() takes 2 positional arguments` or `no attribute 'start'`), 55 and 33 rows respectively. That bug is
no longer present in the orchestrator, but those rows are exhausted at 10/10 and were not part of ZD-8.

## 4. Repair applied to the live board (by session v4-1-2-e6)

Reported by that session and confirmed here by read-only query at 07:47 UTC (row `recovery_telemetry` id 3,
tier 3, `fable_resuscitation`, `rows_changed: 33`). One `BEGIN IMMEDIATE` transaction:

| Rows | Change |
|---|---|
| MC-DSP-15, MC-HW-55 | FAILED to BLOCKED (attempts exhausted or near-exhausted, bridge still broken) |
| MC-CONCURRENCY-FIX | FAILED to PENDING, `not_before` = now + 3600 s |
| Group A, 10 rows | FAILED to BLOCKED, payload wrapped into `{task_id, prompt, target_file}` |
| Group B, 20 rows | FAILED to BLOCKED, payload unchanged, `error_log` points at the leaf-node file |

Post-repair state: BLOCKED 120, COMPLETED 141, PENDING 1, RUNNING 0. ZD-8 count = 0.

Code changes (uncommitted, in the live tree, by v4-1-2-e6):

- `src/cochem/dsp/worker_daemon.py`: `fail_job` now uses
  `CASE WHEN attempts >= max_attempts THEN 'BLOCKED' ELSE 'FAILED' END` and clears `not_before`; the three
  `_finish` call sites report BLOCKED when attempts are exhausted.
- `src/cochem/dsp/forge/orchestrator.py`: new `CodeForgeBridgeError`; `execute()` raises it on a non-zero bridge
  exit instead of returning a FAILED result, so the worker retries with backoff and quarantines at max.
- `migrate_v3_to_v412.py`: no longer deletes rows or stores non-JSON payloads.
- v4-1-2-e6 reports `tests/test_stage7_pipeline_daemons.py`: 22 passed.

## 5. Verification evidence gathered by this session (read-only)

Ran the `verification_command` of each Group B leaf node against the live tree with `PYTHONPATH=src`.

MC-SRE-11 to MC-SRE-23 (all select from `tests/test_ch08_watchdog_sre.py`): the union of their `-k` selectors
(`check_process_liveness`, `fr_002` to `fr_009`, `evidence_bundle`, `nfr_sre_01`, `nfr_sre_03`) ran 176 tests,
176 passed, 0 failed, 20 deselected, 36 s. Every MC-SRE node's verification command passes. MC-SRE-14 has no leaf
node, so it has no verification command to run.

MC-TDD-05 to 12 (`tests/test_ch05_research_tdd_pivot.py`), MC-HW-55 (`tests/test_stage6_activation_mcp.py`),
MC-DSP-15 (`tests/test_stage4_tdd_sandboxing.py::test_f14_...`): see section 5a.

Both target functions named by the Group C nodes already exist in the live tree: `emit_recovery_event()` in
`src/cochem/warden/ladder.py` (line 69) and `run_in_docker_sandbox()` in `src/cochem/dsp/forge/sandbox.py` (line 16).

### 5a. Second verification run

| Rows | Verification command (from leaf node) | Result |
|---|---|---|
| MC-TDD-05, 06, 07, 08, 09, 10, 12 | `pytest tests/test_ch05_research_tdd_pivot.py -k <tdd_cycle_state, fr_001, nfr_tdd_01, nfr_tdd_02, fr_002, fr_003, fr_005>` | 75 passed, 0 failed, 98 deselected, 288 s |
| MC-HW-55 | `pytest tests/test_stage6_activation_mcp.py` | 35 passed, 0 failed, 5 s |
| MC-DSP-15 | `pytest tests/test_stage4_tdd_sandboxing.py::test_f14_docker_sandbox_verifies_network_none_isolation` | 1 passed, 0.2 s |

Combined with section 5, every leaf node behind the 33 FAILED rows that has a verification command passes it
against the live tree today: MC-SRE-11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23; MC-TDD-05, 06, 07, 08, 09,
10, 12; MC-HW-55; MC-DSP-15. Only MC-SRE-14 (no leaf node) and MC-CONCURRENCY-FIX (ad hoc mandate, no
verification command) are unverified. These 21 rows are therefore candidates for COMPLETED with a real
`result_path`; they are currently BLOCKED, which is legal for ZD-8 but understates their state. This session did
not write COMPLETED rows because the sibling session owned the write path and the choice belongs to the operator.

## 6. Follow-ups the repair does not cover

1. **The bridge is still broken.** Any code_forge job that reaches P4 will crash the same way until either the
   `..\v2` tree is restored (it is an unstaged deletion; `git checkout -- v2` from `D:\__CoChem\__agentic` would
   bring it back) or `llm_router.py` stops depending on it, and, separately, the legacy `agy` path passes long
   prompts by file or stdin rather than argv. With the orchestrator fix, MC-CONCURRENCY-FIX will now cycle
   PENDING -> RUNNING -> PENDING with backoff up to attempts 10 and then BLOCK, which is legal for ZD-8 but wastes
   nine bridge runs. Consider holding it BLOCKED until the bridge is repaired.
2. **Group B disposition.** The MC-SRE-11..23 verification commands pass against the live tree, so those rows are
   candidates for COMPLETED with a real `result_path` (an evidence JSON recording the pytest run) rather than
   BLOCKED. That is an operator decision; neither session wrote COMPLETED rows. If they are ever requeued instead,
   their payloads must be replaced with `wiki/wbs/leaf_nodes/<id>.json` first, and MC-SRE-14 needs a node.
3. **Duplicate watchdogs.** PIDs 37536 and 31124 both remain alive with no singleton guard. Stop one, and add a
   pid-file or named-mutex guard to `watchdog_sre.py` so a future collapse launches one recovery session. The
   provisioning script `Install-PipelineDaemons.ps1` registers `CoChemSREWatchdog_V412`, but only
   `CoChemHostWarden_V412` is registered today; both watchdogs were started by ad hoc parents that have exited.
4. **No DSP workers are running.** All four heartbeat files are `.dead` (last one at 02:39:55). Nothing will consume
   the single PENDING row until workers are started.
5. **`reingest_batch7.py`** should restore the canonical payload from the leaf node instead of only editing
   `description`, or be retired.

## 7. Changes made by this session

- No changes to `job_board.db`.
- No changes under `src/` or to any script.
- Wrote this report.
- Ran read-only queries against `job_board.db` and the snapshot, one short `agy` probe, one 40k-argv
  `subprocess.run` probe, and the pytest selections listed in section 5.
