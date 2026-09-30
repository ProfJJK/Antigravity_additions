# Recovery Report: no-consumer collapse, bundle 20260930T085258Z

- Session: v4-1-2-1f (claude.exe PID 4820, launched by watchdog PID 37536)
- Bundle: `.evidence/watchdog/bundles/20260930T085258Z/evidence_bundle.json` (timestamp 1790758378 = 2026-09-30 03:52:58 CDT)
- Written: 2026-09-30 ~04:05 CDT, host AetherDesk
- Outcome: **analysis only**. This session wrote nothing to `job_board.db` and edited nothing under `src/`.
  The sibling session v4-1-2-d3 (claude.exe PID 15852, launched 30 s earlier by watchdog PID 31124 for the
  identical bundle `20260930T085228Z`) claimed the write path; both sessions agreed by cross-session message
  that the earlier bundle owns writes, and this session handed it the diagnosis and fix list below.
  Signal ZD-8 was already 0 in both bundles and on the live board at every check made here.

## 1. Why there were two recovery sessions again

Two `watchdog_sre.py` processes (PIDs 31124 and 37536) have run since 02:39:58 CDT; their console parents
are gone and `SREWatchdog.run()` has no single-instance guard. Each evaluated the same condition:

| Watchdog | Bundle | Recovery PID | Note |
|---|---|---|---|
| 31124 | `20260930T085228Z` (03:52:28) | 15852 = session v4-1-2-d3 | first to page |
| 37536 | `20260930T085258Z` (03:52:58) | 4820 = this session | its cycle 146 raised `FileExistsError` on `20260930T085228Z` (bundle-dir race with 31124), so it re-bundled one cycle later |

The race is visible in `watchdog_state.json` / `watchdog_heartbeat.json` (`last_cycle_error`, cycle 146).
This is the second duplicate page today (see the two `20260930T073958Z*` bundles).

## 2. What the bundle showed

| Field | Value |
|---|---|
| Verdict | COLLAPSED |
| Sole failure | `Matrix 1: 1 ready PENDING job(s) and no live DSP worker for 330s` |
| Signal ZD-8 | 0 (no FAILED rows, no stale lease, no PENDING at max_attempts) |
| Status counts | BLOCKED 120, COMPLETED 141, PENDING 1 (262 rows) |
| RUNNING rows / live workers / frozen | 0 / 0 / none |
| Snapshot vs live board | identical on the 9 lease/status columns at 04:03 CDT (262 = 262 rows) |

The one ready row is id 399 `MC-CONCURRENCY-FIX` (job_type `micro_code`, priority 1, attempts 1/10,
`lease_owner` NULL, `not_before` 1790758043). Its `not_before` was set by the 07:47 UTC resuscitation
(session v4-1-2-e6, bundle `20260930T073958Z_1`) as "now + 3600 s" when it moved the row FAILED -> PENDING.
It became claimable at 03:47:23 CDT; `NO_CONSUMER_GRACE_SEC` is 300, so both watchdogs declared COLLAPSED
at 03:52:2x, exactly on schedule.

No DSP worker exists. `.evidence/dsp_worker/` holds four `worker_*.dead` markers, the last three with final
heartbeats at 02:39:40-02:39:55 CDT (jobs_processed 523, 352, 7), i.e. the workers died in the same
seconds the two watchdogs were started. `Get-ScheduledTask` shows only `CoChemHostWarden_V412` and
`CoChem_Ecosystem_Backup`; the worker and watchdog tasks from `provisioning/Install-PipelineDaemons.ps1`
were never registered, so nothing restarts a worker.

## 3. Root cause against ch04 and ch08

1. **Not a queue-corruption collapse.** ch08 FR-009 (Signal ZD-8) is satisfied: 0 rows. ch04 FR-009
   (lease cleared on every terminal transition) holds: no row has a `lease_owner`. Nothing on the board is
   illegal, so there is no ZD-8 repair to make.
2. **Direct cause: a job was re-queued into a queue with no consumer.** The 07:47 UTC resuscitation put
   MC-CONCURRENCY-FIX back to PENDING with a one-hour `not_before` while documenting that no DSP workers
   were running. The watchdog's no-consumer rule (`worker_record_failures`: `ready_pending > 0` and zero
   live worker records for 300 s) is the designed detector for "all workers gone while work is pending";
   it fired as designed one hour later.
3. **The job could not have run anyway.** Its last attempt died in `..\.scripts\task_work_loop.py` at P4
   because `..\v2` is deleted in the working tree (`llm_router.py:976` import fails, every agent falls back
   to `agy`) and the P4 prompt exceeds the 32,767-character Windows argv limit (WinError 206). Both faults
   are unchanged since the earlier reports. Starting a worker now would burn up to 9 more attempts of P1-P3
   `agy` calls before `retry_or_block_job` quarantined the row.
4. **Code defects surfaced by this incident (in `src/cochem/watchdog/watchdog_sre.py`):**
   - (a) No single-instance guard in `SREWatchdog.run()`: every collapse pages Fable once per running
     watchdog. Fix: lock file under `evidence_dir` opened with `O_CREAT|O_EXCL`, stale-PID check via
     `psutil`, released on exit; a second instance logs and exits non-zero.
   - (b) `assemble_evidence_bundle` chooses the bundle dir with an `exists()` scan and then calls
     `mkdir(parents=True)`; two instances race and the loser's whole cycle fails with `FileExistsError`
     (observed, cycle 146 of PID 37536). Fix: loop on `mkdir` and catch `FileExistsError`.
   - (c) A no-consumer-only collapse is escalated exactly like queue corruption:
     `TRIGGER_FABLE_RESUSCITATION`, a claude.exe session, and a re-page after every 3600 s cooldown for as
     long as any PENDING row is ready and no worker runs. Fable cannot register the worker tasks (elevated
     `Install-PipelineDaemons.ps1`), so the only board-level move it has is to quarantine the ready row.
     ch08 FR-003 scopes Matrix 1 to dead, hung or orphaned worker *processes*; ch08 section 8 lists
     "False Positive Collapse" as a failure mode to prevent. Because `matrix_process_death` only logs an
     idle worker's death, the no-consumer rule is the sole detector for "all workers gone", so it must stay;
     the escalation should differ (for example a distinct heartbeat verdict such as DEGRADED that the Host
     Warden acts on, and no claude.exe launch). No test in `tests/test_ch08_watchdog_sre.py` covers the
     no-consumer branch or a singleton guard, so both can change with new tests.

## 4. Repair recommended to the writing session (v4-1-2-d3)

One `BEGIN IMMEDIATE` transaction on the live `job_board.db`, no delete or recreate:

| Row | From -> To | Other columns | Rationale |
|---|---|---|---|
| id 399 `MC-CONCURRENCY-FIX` | PENDING -> BLOCKED | `lease_owner`/`lease_expires_at` stay NULL, `not_before` NULL, attempts unchanged, `error_log` appended with a `[RESUSCITATION 2026-09-30 bundle ...]` line naming the reason | ch04 section 3 poison-pill quarantine: no consumer deployed and the bridge crash is deterministic until `..\v2` is restored and the argv limit is handled; operator re-queues by setting PENDING |
| `recovery_telemetry` | +1 row (tier 3, `fable_resuscitation`) | | audit trail |

After that edit `ready_pending` is 0, so both watchdogs return HEALTHY on their next cycle and ZD-8 stays 0.
Board expected afterwards: BLOCKED 121, COMPLETED 141, PENDING 0.

## 5. Changes made by this session

- `job_board.db`: none.
- `src/`: none.
- Processes: none killed, none started.
- Files written: this report only.

## 6. Verification performed here (read-only)

- `SELECT COUNT(*) ... ZD-8` on the live board = 0 (04:00 and 04:03 CDT).
- Live board and bundle snapshot identical on id, task_id, status, attempts, max_attempts, lease_owner,
  lease_expires_at, not_before, updated_at (262 rows each).
- Process table: two `watchdog_sre.py` (31124, 37536), two recovery `claude.exe` (15852, 4820), no
  `worker_daemon` process.

## 7. Final state

See the addendum at the end of this file; it is appended once v4-1-2-d3 reports its board edit, or records
that no confirmation arrived before this session ended.

## 8. Open items for the operator

1. Deploy the daemons: run `provisioning/Install-PipelineDaemons.ps1` elevated so the watchdog and the DSP
   workers exist as scheduled tasks and are restarted by the Host Warden. Until then every PENDING row will
   re-trigger this page one hour after it becomes ready.
2. Stop one of the two console-launched watchdogs (31124 is the parent of v4-1-2-d3, 37536 of this session;
   killing a watchdog does not kill its claude.exe child). Add the singleton guard before restarting.
3. Restore `..\v2\MODEL_REGISTRY_V2.py` (in git history) or repoint `llm_router.py:976`, and move the P4
   prompt off argv (stdin or a file) before re-queuing any `code_forge` row.
4. Decide the fate of MC-CONCURRENCY-FIX: its payload is an architectural mandate for Fable, not a
   micro-task, and should probably be executed as an interactive session rather than through the worker.

---
### Addendum 1 (04:06 CDT, epoch 1790758746): state at the end of this session's active work

- Live board still BLOCKED 120 / COMPLETED 141 / PENDING 1; ZD-8 = 0; ready_pending = 1 (id 399 unchanged,
  `updated_at` 1790754443). v4-1-2-d3 had not yet committed its edit; no report exists in its bundle dir.
- Both watchdogs still report COLLAPSED in the heartbeat (PID 37536, cycle 159) and will keep doing so until
  the ready row is quarantined or a worker appears; no new page is possible before the 3600 s cooldown
  (next earliest re-page ~04:52 CDT) and only if the failure signature changes or the cooldown lapses.
- `git status`: `watchdog_sre.py`, `worker_daemon.py`, `forge/orchestrator.py` carry only the earlier
  sessions' uncommitted changes; nothing from this session.
- This session subscribed to v4-1-2-d3's idle notice and will re-verify the board and extend this addendum if
  a confirmation arrives; otherwise the operator should check the peer's `RECOVERY_REPORT.md` in
  `bundles/20260930T085228Z/`.
