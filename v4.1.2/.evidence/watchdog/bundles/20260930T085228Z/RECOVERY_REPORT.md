# RECOVERY REPORT — bundle 20260930T085228Z

- **Recovery session**: v4-1-2-d3 (claude.exe PID 15852, launched by watchdog PID 31124 at 03:52:28 CDT)
- **Bundle timestamp**: 1790758348 (2026-09-30T08:52:28Z, 03:52:28 CDT)
- **Verdict in bundle**: COLLAPSED — `Matrix 1: 1 ready PENDING job(s) and no live DSP worker for 300s`
- **Signal ZD-8 in bundle**: **0** (no illegal queue state existed; this was not a queue-corruption collapse)
- **Outcome**: live board now ZD-8 = **0**, ready PENDING = **0**, `BLOCKED 121 / COMPLETED 141` (262 rows). One `BEGIN IMMEDIATE` transaction touched **1** `jobs` row and inserted **1** `recovery_telemetry` row. `job_board.db` was neither deleted nor recreated. Three watchdog code defects fixed; duplicate watchdog process terminated. Surviving watchdog (PID 31124) reports HEALTHY since cycle 168.
- **Duplicate page**: watchdog PID 37536 raised the same event 30 s later (bundle `20260930T085258Z`, session v4-1-2-1f, claude PID 4820). The two sessions agreed by message that v4-1-2-d3 owns all writes; v4-1-2-1f wrote nothing and produced an analysis-only report.

## 1. What the bundle showed

| Item | Value |
|---|---|
| Status counts (snapshot = live at start) | BLOCKED 120, COMPLETED 141, PENDING 1 |
| Signal ZD-8 (verbatim ch08 §3 query) | 0 |
| Ready PENDING (`attempts < max_attempts AND (not_before IS NULL OR not_before <= now)`) | 1 |
| RUNNING rows / live DSP worker records | 0 / 0 |
| frozen_worker_pids / stack_traces | [] / {} |

The single ready row was `MC-CONCURRENCY-FIX` (id 399, `micro_code`, attempts 1/10, `lease_owner` NULL, `not_before` 1790758043, `updated_at` 1790754443). Its payload is an operator "architectural mandate" targeting `src/cochem/dsp/worker_daemon.py` via the code_forge bridge with agent `pivot-strategist`. Its `error_log` holds the P4 bridge traceback from attempt 1 (`task_work_loop.py` → `safe_chat_cli` → `run_in_executor`) plus the 07:47 resuscitation note that set it to PENDING with a 3600 s backoff.

## 2. Root cause against the SRS

**Timeline (all CDT, 2026-09-30):**
- 02:39:40–02:39:55 — last heartbeats of the three DSP workers (PIDs 23816, 35876, 24888; `.evidence/dsp_worker/worker_*.dead`, jobs_processed 523/352/7). No worker log exists (they were started ad hoc, not by the scheduled tasks, which were never registered: `Get-ScheduledTask CoChem*` shows only `CoChemHostWarden_V412` and `CoChem_Ecosystem_Backup`). No file in the repo was modified in that window. The workers most likely were stopped by whoever restarted the pipeline three seconds later.
- 02:39:58 — **two** `python src\cochem\watchdog\watchdog_sre.py` instances started (PIDs 31124 and 37536, different and now-gone parents), both on the same evidence dir. The watchdog has no single-instance guard.
- 02:40 — both paged Fable for the 33 FAILED rows (bundles `20260930T073958Z`, `_1`). Session v4-1-2-e6 repaired the board and set `MC-CONCURRENCY-FIX` to PENDING with `not_before` = 1790758043 (+3600 s), explicitly noting "the single PENDING row will not move until a worker starts".
- 03:47:23 — the backoff expired; the row became "ready". `read_job_board` counted `ready_pending = 1`; `worker_record_failures` saw zero live worker records and started `no_consumer_since`.
- 03:52:28 / 03:52:58 — after `NO_CONSUMER_GRACE_SEC` (300 s), each watchdog appended `Matrix 1: 1 ready PENDING job(s) and no live DSP worker`, forced the verdict to COLLAPSED (`SREWatchdog.diagnose`), assembled a bundle and launched a claude.exe session (this one, and v4-1-2-1f). PID 37536 additionally hit `FileExistsError` in `assemble_evidence_bundle` at its cycle 146 because both instances computed the same bundle stamp (exists()-then-mkdir race), which delayed its page by one cycle.

**Against the SRS:**
1. **ch08 FR-009 / §3 (Signal ZD-8)** was satisfied throughout: 0. **ch04 FR-009** (lease columns NULL off RUNNING) holds on every row. Nothing on the board was illegal, so there was nothing for a resuscitation agent to "repair" in the ZD-8 sense.
2. **ch08 FR-003 (Matrix 1)** specifies detection of *dead, hung or orphaned worker processes*. The implementation extends this with a starvation check (ready work, zero live consumers, 300 s). That check is a legitimate signal — it is the only place that detects "all workers gone" (`matrix_process_death` merely logs an idle dead worker) — but escalating it to COLLAPSED → freeze → bundle → `claude.exe` (FR-007/FR-008) is a **code defect**: the board holds no illegal state, a resuscitation session cannot register the worker tasks (`Install-PipelineDaemons.ps1` requires elevation; this session is not elevated) and cannot safely start a consumer either (see §5), so the page repeats every cooldown (3600 s) for as long as any PENDING row is ready. Two identical pages an hour apart today demonstrate this.
3. **No single-instance guard** in `SREWatchdog.run()`: two watchdogs on one evidence dir share `watchdog_state.json` / `watchdog_heartbeat.json` (last writer wins; the state file alternated between `recovery_pid` 15852 and 4820), and each pages its own Fable session for the same event. Four recovery sessions were launched today for two events. This is the second occurrence (prior report §5 listed it as open).
4. **Bundle-directory race** in `assemble_evidence_bundle`: `next(... if not p.exists())` followed by `mkdir(parents=True)` is not atomic; with two instances it raised `FileExistsError` (recorded in PID 37536's `last_cycle_error`).
5. Environmental (unchanged, outside v4.1.2): `..\v2` is deleted in the working tree, so `D:\__CoChem\__agentic\llm_router.py:976` (`from v2.MODEL_REGISTRY_V2 import ...`) fails, every agent falls to the `agy` legacy path, and the P4 coder/strategist prompt exceeds the Windows 32,767-char argv limit (`WinError 206`). Any code_forge row that reaches the bridge crashes deterministically. Nothing in this session changes that.

**Conclusion:** the collapse was a **starvation false positive**: a legal board with one ready row and no consumer deployed, escalated by the watchdog as if it were queue corruption, and doubled by a duplicate watchdog instance.

## 3. Job board changes (live `job_board.db`)

Script: `repair_job_board.py` in this bundle directory (kept for audit). One connection, `PRAGMA busy_timeout=30000`, `BEGIN IMMEDIATE`; the UPDATE is guarded by `WHERE task_id='MC-CONCURRENCY-FIX' AND status='PENDING' AND attempts=1 AND updated_at=1790754443` and asserted to hit exactly one row; the verbatim ch08 §3 ZD-8 query (0), the ready-PENDING count (0) and the row total (262 before and after) were asserted inside the transaction before COMMIT. Committed at `updated_at` = **1790758752** (04:59:12 UTC / 03:59:12 CDT). Verified afterwards through a separate `file:job_board.db?mode=ro` connection.

| Row | From → To | Other columns | Rationale |
|---|---|---|---|
| `MC-CONCURRENCY-FIX` (id 399, attempts 1/10) | PENDING → BLOCKED | `lease_owner` NULL, `lease_expires_at` NULL, `not_before` NULL, `error_log` += `[RESUSCITATION 2026-09-30 bundle 20260930T085228Z session v4-1-2-d3] PENDING -> BLOCKED (operator quarantine, not a verdict): …` including the exact re-queue SQL; original error text preserved | Operator-style quarantine, not a poison-pill verdict (attempts 1 < 3): the retry window elapsed with no consumer deployed and the bridge environment still crashes deterministically, so leaving it ready only re-pages Fable hourly without any possibility of progress. BLOCKED is a legal terminal state under ch04 §3 and is invisible to ZD-8; re-queue is one UPDATE (in the error_log) once workers exist and `..\v2` / the argv limit are fixed. |
| `recovery_telemetry` | +1 row (id 4) | tier 3, action `fable_resuscitation`, details `{"bundle": "20260930T085228Z", "session": "v4-1-2-d3", "rows_changed": 1, "task_id": "MC-CONCURRENCY-FIX", "transition": "PENDING->BLOCKED", "reason": "no DSP consumer deployed; bridge environment broken", "ts": 1790758752}` | audit trail |

Result: `BLOCKED 121, COMPLETED 141` (262 rows), ZD-8 = 0, ready PENDING = 0. No other row was touched. Rejected alternatives: (a) pushing `not_before` further out — misrepresents a retry backoff and only defers the page; (b) starting a worker from this session — see §5.

## 4. Code changes (uncommitted, working tree)

Patch script: `patch_code.py` in this bundle directory (nine exact-match replacements, each asserted to occur once); the resulting unified diff is `watchdog_sre.diff` beside it (103 added lines). File: `src/cochem/watchdog/watchdog_sre.py` (still stdlib + psutil only; `test_watchdog_is_sterile` passes). New tests: `tests/test_watchdog_resuscitation_20260930.py` (8 tests).

1. **Starvation is DEGRADED, not COLLAPSED.** `SREWatchdog.diagnose()` splits the worker-record findings: stale-heartbeat / job-timeout findings still force COLLAPSED; the `no live DSP worker` finding (marker constant `NO_CONSUMER_MARKER`, helper `is_no_consumer_failure`) sets verdict `DEGRADED` when nothing else collapsed, is exposed as `diagnosis["degraded_failures"]`, is logged at error level every cycle by `_cycle`, and is written into the heartbeat (new `matrix_failures` field in `watchdog_heartbeat.json`, via an optional `failures` argument on `write_heartbeat`). No freeze, no bundle, no `claude.exe` launch. When another matrix collapses at the same time the starvation line is still appended to the bundle's `matrix_failures` for context. Detection itself (300 s grace, `no_consumer_since` reset when a worker appears or the queue drains) is unchanged.
2. **Single-instance lock.** `SREWatchdog.run()` takes `<evidence_dir>/watchdog.pid` with `O_CREAT|O_EXCL`; if the file exists and its pid is a live process whose command line names `watchdog_sre` (AccessDenied counts as live), the instance logs the holder and returns exit code 3 (`EXIT_ALREADY_RUNNING`) without running a cycle. A stale lock (dead pid, pid reused by an unrelated process, unreadable content) is reclaimed. The lock is released in a `finally` only if it still holds this pid. Constructing `SREWatchdog` or calling `run_cycle()` directly (as the test suites do) does not touch the lock.
3. **Bundle-directory allocation.** `assemble_evidence_bundle` now loops `mkdir(parents=True)` catching `FileExistsError`, so the mkdir is the existence check; naming (`<stamp>`, `<stamp>_1`, …) is unchanged and the existing ch08 test for it still passes.

Not changed: the already-uncommitted fixes from the 07:47 session in `worker_daemon.py`, `forge/orchestrator.py` and `migrate_v3_to_v412.py` (verified present in the working tree).

## 5. Process changes and open items for the operator

- **Terminated duplicate watchdog PID 37536** (`python src\cochem\watchdog\watchdog_sre.py`, started 02:39:58, identical command line, cwd and evidence dir to PID 31124; its recovery child had already stood down). Its claude child was not touched (it exited on its own). PID 31124 continues unchanged and reported HEALTHY at cycle 168 (heartbeat 1790759008) after the board edit.
- **PID 31124 runs the pre-patch code** (no lock, starvation still pages). Restart it — `python src\cochem\watchdog\watchdog_sre.py` from `v4.1.2`, or register `CoChemSREWatchdog_V412` via elevated `provisioning\Install-PipelineDaemons.ps1` — to load the fix; the new instance takes `.evidence\watchdog\watchdog.pid`. This session did not restart it: a daemon spawned from a recovery session would be tied to this session's process tree.
- **No DSP workers are deployed** and none can be started responsibly from here: a worker would immediately claim any ready row and run the code_forge bridge, which spends ~15 min of `agy`/Gemini P1–P3 calls writing `tests/tdd/` artifacts before crashing at P4, ten times per row. Fix `..\v2` (restore `v2/MODEL_REGISTRY_V2.py` from git history or repoint `llm_router.py:976`) and the P4 argv-limit path first, then register `CoChemDspWorker_V412_W<n>` (elevated install script).
- **Re-queue `MC-CONCURRENCY-FIX`** only after the above: `UPDATE jobs SET status='PENDING', not_before=NULL WHERE task_id='MC-CONCURRENCY-FIX';` (also in its `error_log`). The 120 rows quarantined earlier today have the same dependency; see the `20260930T073958Z_1` report §5.
- **`master_reconstruction_loop.py`** (operator's pwsh loop, PID 31456 at the time of writing) regenerates `src/cochem/dsp/worker_daemon.py` and other chapter files with Fable and can silently overwrite the uncommitted `fail_job` fix; `watchdog_sre.py` is not in its chapter list. Nothing from either resuscitation is committed; commit or stash before that loop runs again.
- **Heartbeat consumers**: the Host Warden only restarts the watchdog when the heartbeat ceases; nothing yet acts on `verdict: DEGRADED` / `matrix_failures` in `watchdog_heartbeat.json`. That field is now the place to alarm on starvation.
- The 20 stub rows (MC-SRE-11..23 / MC-TDD-05..10,12) and the 10 Markdown-payload rows remain BLOCKED pending the operator decision recorded in the previous report.

## 6. Verification

- `PYTHONPATH=src python -m pytest tests/test_watchdog_resuscitation_20260930.py tests/test_stage7_pipeline_daemons.py -q` → **30 passed** in 18.7 s (8 new: starvation → DEGRADED with heartbeat and no bundle; starvation inside grace → HEALTHY; the quarantined row leaves nothing ready; starvation plus a real ZD-8 hit stays COLLAPSED and is listed; second instance exits 3 and leaves the live lock untouched; stale/foreign/garbage lock reclaimed and released; lock held for the whole run; pre-created bundle dirs are skipped, not raised).
- Smoke run of the patched module against the **live** board with a scratch evidence dir: `python src\cochem\watchdog\watchdog_sre.py --once --no-recovery --evidence-dir %TEMP%\wd_smoke` → `HEALTHY {'BLOCKED': 121, 'COMPLETED': 141}`, heartbeat `matrix_failures: []`, lock file created and released.
- Live watchdog PID 31124 (old code) heartbeat after the COMMIT: `verdict: HEALTHY`, cycle 168, `last_cycle_error: null`.
- Live board after COMMIT, read via `file:job_board.db?mode=ro`: ZD-8 = 0; `BLOCKED 121, COMPLETED 141`; row 399 = `('BLOCKED', attempts 1, not_before NULL, updated_at 1790758752)`.
- `tests/test_ch08_watchdog_sre.py` (full SRS chapter suite, 3,700 lines, spawns real subprocesses): result appended below.

### 6a. ch08 suite result

- First run: **150 passed, 1 failed** — `test_ch08_fr_007_bundle_assembly_failure_workers_still_thawed` (bundles dir is a regular file). The new mkdir loop caught the resulting `FileExistsError` and spun through all 10^6 suffixes before raising; the worker tree under test had exited by then. Fixed in `patch_code.py` / `watchdog_sre.py`: the loop only retries when the colliding path `is_dir()` (a real stamp collision) and re-raises otherwise. `watchdog_sre.diff` regenerated (105 added lines).
- Rerun: `PYTHONPATH=src python -m pytest tests/test_ch08_watchdog_sre.py -q` → **196 passed** in 42.3 s. Combined with §6: 196 + 22 + 8 = **226 passed, 0 failed** across `test_ch08_watchdog_sre.py`, `test_stage7_pipeline_daemons.py` and `test_watchdog_resuscitation_20260930.py`.

### 6b. Can a worker start at all? (checked after the report body was written)

`start_pipeline.bat` (repo root) is the ad-hoc launcher: one `python -m cochem.dsp.worker_daemon` and one `python src\cochem\watchdog\watchdog_sre.py` per invocation, via `start /b`. Two watchdogs starting in the same second means it was run twice at 02:39:58; the pid lock (§4.2) now makes the second invocation exit with code 3. The workers those two runs should have started left no `.evidence/dsp_worker/worker_*.json` record at all, so they never reached the poll loop. To rule out an import/startup defect: `PYTHONPATH=src python -c "import cochem.dsp.worker_daemon"` succeeds (domains `academic_press`, `code_forge`, `pedagogy_engine`), and `python -m cochem.dsp.worker_daemon --exit-when-idle --state-dir %TEMP%\wd_worker_smoke` against the live board polled once, found nothing ready and exited 0 (`stopped after 0 job(s)`); the board was unchanged afterwards (`max(updated_at)` still 1790758752). The worker code starts fine today; why the 02:39:58 workers left no trace (a `start /b` console closed, or the bat never got that far) is not recoverable from disk. Restarting a consumer remains the operator's call for the reasons in §5.
