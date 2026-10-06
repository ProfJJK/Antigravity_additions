# FABLE_DEPLOYMENT_COMPLETION — V4.1.2 Pipeline Daemons

**Date:** 2026-09-29 · **Prompt:** `fable_deploy_completion_prompt.md` · **Test result:** 290 passed, 0 failed (268 baseline + 22 new)

## Status at a glance

| Item | State |
|---|---|
| `src/cochem/watchdog/watchdog_sre.py` (SRE Watchdog, ch08) | Implemented, tested, smoke-run against live `job_board.db` (HEALTHY, 6 ms cycle, DB hash unchanged) |
| `src/cochem/dsp/worker_daemon.py` (DSP Worker, ch03/04/07) | Implemented, tested, smoke-run against live `job_board.db` (idle, no rows touched) |
| `provisioning/Install-PipelineDaemons.ps1` | Written; `-DryRun` produces three well-formed task XMLs |
| Scheduled tasks registered | **No.** This session is not elevated. Run the installer from an elevated PowerShell (see "Deploy"). |
| `tests/test_stage7_pipeline_daemons.py` | 22 zero-mock tests (real WAL SQLite, real subprocesses, real suspend/resume) |

## Deploy (elevated PowerShell)

```powershell
cd D:\__CoChem\__agentic\v4.2.0\provisioning
.\Install-PipelineDaemons.ps1 -DryRun            # review
.\Install-PipelineDaemons.ps1                     # 1 worker, recovery enabled
.\Install-PipelineDaemons.ps1 -WorkerCount 3      # up to 6 workers (dsp_registration_manifest max_workers)
.\Install-PipelineDaemons.ps1 -NoRecovery         # watchdog bundles evidence but never launches claude.exe
.\Install-PipelineDaemons.ps1 -Uninstall
```

The installer registers `CoChemSREWatchdog_V412` and `CoChemDspWorker_V412_W<n>` as boot tasks (45 s and 60 s after boot, after the Host Warden's 30 s). They run as the current user (S4U, LeastPrivilege) with PYTHONPATH set to `src` plus the user site-packages, where psutil lives. The installer starts the tasks and waits for both daemons' heartbeat files.

**Auto-restart:** each task repeats every minute with `MultipleInstancesPolicy=IgnoreNew`, so a dead daemon is relaunched within about 60 s. `RestartOnFailure` alone does not fire on a non-zero exit code, so it would not restart a crashed Python process.

Logs: `.evidence\watchdog\watchdog_sre.log`, `.evidence\dsp_worker\CoChemDspWorker_V412_W<n>.log` (rotating, 5 MB × 3). Uncaught tracebacks go to `*.stderr.log`.

## 1. SRE Watchdog (`python -m cochem.watchdog.watchdog_sre`)

- **Sterile (FR-001):** imports only the stdlib and psutil (an AST test enforces this). The DB is opened only via `file:…?mode=ro` plus `PRAGMA query_only`. A test confirms a live WAL's size and mtime are unchanged after a cycle.
- **30 s loop (FR-002):** a heartbeat goes to `.evidence\watchdog\watchdog_heartbeat.json`, and it warns if a cycle exceeds the 1.0 s budget (NFR-SRE-01).
- **Matrix 1, Process Death:** the Matrix 1 checks rely on worker heartbeat files (`.evidence\dsp_worker\worker_<pid>.json`). A failure is raised for:
  - a dead worker that was holding a task;
  - a zombie worker;
  - a hung worker (heartbeat older than 120 s, or on one task for more than 1740 s);
  - an orphaned lease (owner PID from `dsp-worker@<host>:<pid>` is dead and the row hasn't been refreshed for 60 s);
  - ready PENDING work with no live worker for 5 minutes.
- **Matrix 2, Zombie Deadlocks (FR-004):** RUNNING rows whose `lease_expires_at` is more than 60 s in the past (the 1800 + 60 = 1860 s calibration).
- **Matrix 3, Data Corruption (FR-005):** checks the `SQLite format 3\x00` header, the WAL magic number, and whether the DB can be read at all.
- **Matrix 4, Resource Exhaustion (FR-006):** worker RSS over 512 MB, a least-squares RSS slope over 5 MB/min across 10 samples (with RSS above 256 MB), and host memory over 95%.
- **Signal ZD-8 (FR-009):** runs the exact SRS query. Any non-zero count means COLLAPSED.
- **When a collapse is confirmed (FR-007/008):**
  1. It suspends the worker processes and saves their PIDs to `watchdog_state.json` first.
  2. It writes an immutable (read-only) Evidence Bundle to `.evidence\watchdog\bundles\<UTC stamp>\`:
     - `evidence_bundle.json`, a superset of the ch08 data model;
     - a `job_board_snapshot.db` made with the SQLite backup API from the read-only connection;
     - the tails of the worker logs.
  3. It launches `claude.exe -p <prompt> --model claude-fable-5-1 --permission-mode bypassPermissions --add-dir <repo>` with `NODE_OPTIONS=--max-old-space-size=4096` and `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`. Output goes to `recovery_session.log` in the bundle.
  4. Workers are resumed when the recovery process exits, or when it is killed after 1740 s. A restarted watchdog resumes any workers still recorded as frozen.
- **Dedupe:** a collapse with the same signature is not dispatched again within 1 hour (`--cooldown-sec`). A collapse found while recovery is running is only logged.

## 2. DSP Worker (`python -m cochem.dsp.worker_daemon`)

- **Claim (ch04 FR-002):** a `BEGIN IMMEDIATE` lease takes the highest-priority PENDING row with `attempts < max_attempts` and `not_before` due. `lease_owner` is `dsp-worker@<host>:<pid>`, and the lease lasts 1800 s. A test with 10 threads and 60 jobs showed zero double claims.
- **Heartbeat (FR-004):** a thread refreshes the lease every 5 s and stops renewing after 1740 s, so a hung job's lease runs out and gets reclaimed.
- **Fencing:** every terminal UPDATE is conditioned on `status='RUNNING' AND lease_owner=me`, and every one clears `lease_owner` and `lease_expires_at` (FR-009).
- **Dispatch:** uses `DomainRouter` with the domain taken from `payload.dsp_domain`, then `payload.domain`, then a `job_type` default (`micro_code`/`macro_audit` → `code_forge`, `research` → `academic_press`). A domain must appear in both `toolkit.mcp_server.list_dsp_pipelines()` and the router registry. The three orchestrators now register themselves with `@register_pipeline` (one decorator plus one import in each `orchestrator.py`).
- **Outcomes:**
  - validate() or audit() rejects, or the payload can't be routed → `FAILED`
  - execute() raises → `PENDING` with `not_before` set by jittered exponential backoff (30 s up to 1800 s), or `BLOCKED` once `attempts >= max_attempts`
  - success → `COMPLETED`, with the result JSON in `.evidence\dsp_worker\results\<task_id>.json` and `result_path` set
- **Reaper (every 30 s):** expired leases, plus leases whose owner process on this host is dead, go back to PENDING (or BLOCKED at max attempts). This belongs in the worker because the watchdog must stay read-only.
- **Guardrails:**
  - 100–500 ms startup jitter;
  - full-jitter exponential idle and lock backoff (1 s up to 30 s);
  - `busy_timeout=30000` and `synchronous=NORMAL`;
  - after each job it checks its own memory, and if RSS is over 512 MB it exits with code 3 so the task relaunches it;
  - on SIGINT/SIGTERM/SIGBREAK it finishes the current job and exits cleanly.

## Open issues — read before relying on this

1. **The DSP orchestrators are still stubs.** `CodeForgeOrchestrator.execute()`, `AcademicPressOrchestrator.execute()` and `PedagogyOrchestrator.execute()` return a hard-coded SUCCESS without doing any work. The worker is real, but any job it picks up today will be marked `COMPLETED` with no real work behind it. This conflicts with NFR-04 (zero mocks). Implement the orchestrators before injecting real jobs, or keep the worker tasks disabled.
2. **`toolkit/mcp_server.py` is an in-memory registry.** `trigger_dsp_pipeline` only records QUEUED in a Python dict, and `get_dsp_status` advances state each time it is called. The worker uses only `list_dsp_pipelines()` from it. Submitting jobs through that MCP tool does **not** reach `job_board.db`. I left it unchanged because `test_f20_*` depends on its current behaviour.
3. **The recovery agent runs with `bypassPermissions` by default.** A ZD-8 hit, including one validate/audit rejection that marks a job FAILED, launches an autonomous Fable session with full permissions against the repo. Use `-NoRecovery` (or `--permission-mode acceptEdits`) if you want a human to act on the bundles instead.
4. **Workers stay frozen while recovery runs**, for up to 1740 s. If the watchdog dies mid-recovery, the next watchdog start resumes them.
5. **Where I departed from the SRS:**
   - **Attempt limit:** blocking uses the per-row `max_attempts` column (default 10), not ch04's hard-coded 3, which keeps it consistent with the ZD-8 query.
   - **Retryable failures:** these go back to PENDING with a backoff instead of `FAILED`. Otherwise every transient fault would trip ZD-8 and launch a Fable session.
   - **Lease reclamation:** ch04 §8 says the watchdog resets expired leases, but NFR-SRE-02 forbids the watchdog from writing. The worker's reaper does it instead.
   - **Recovery launch:** ch03's `claude.exe --file <prompt>` is wrong for this purpose. `--file` downloads remote file resources, so the prompt is passed with `-p`.
   - **Restarting the watchdog:** ch08 says "the Warden restarts the Watchdog", but the Host Warden has no such logic. The repeating scheduled task does the restart instead.
   - **Where it runs:** everything runs on the Windows host, not inside the guest VM that ch00/ch08 describe.
6. **Not implemented:** `hardware_guard.py` per-DSP quotas (ch07 FR-007). The 512 MB cap is a self-check after each job, not a hard OS limit. A job can't be pre-empted mid-run, because it runs inside the worker process.
7. **The job board has no work.** It holds 183 COMPLETED rows and nothing else, so the worker will idle until new jobs are injected.

## Files

- **New:**
  - `src/cochem/watchdog/watchdog_sre.py`
  - `src/cochem/dsp/worker_daemon.py`
  - `provisioning/Install-PipelineDaemons.ps1`
  - `tests/test_stage7_pipeline_daemons.py`
  - `FABLE_DEPLOYMENT_COMPLETION.md`
- **Modified:** `src/cochem/dsp/{forge,press,pedagogy}/orchestrator.py` (+2 lines each: `@register_pipeline`)
- **Runtime artifacts from the smoke run:** `.evidence/watchdog/watchdog_heartbeat.json`, `.evidence/watchdog/watchdog_state.json`
