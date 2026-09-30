# Fable 5.1 & Opus 5.5 Engineering Task: Complete V4.1.2 Pipeline Deployment

## Current State
The Host Warden FastMCP daemon has been successfully deployed and is running locally on port `47821`.
However, the V4.1.2 pipeline is essentially a dead database because the `WBS_Fracturer` missed two critical background daemon loops defined in the SRS:

1. **The Autonomous SRE Watchdog (`src/cochem/watchdog/watchdog_sre.py`)**
   - Must implement the 4-Matrix Diagnostic Engine defined in `wiki/srs/ch08_watchdog_sre.md`.
   - Must poll `job_board.db` in `?mode=ro`.
   - Must assemble Evidence Bundles and trigger self-healing via `claude.exe` Fable 5.1.

2. **The DSP Worker Daemon (`src/cochem/dsp/worker_daemon.py`)**
   - The Domain-Specific Pipeline (DSP) toolkit exists in `src/cochem/dsp/`, but there is no continuous `while True` daemon polling `job_board.db` to actually pick up `PENDING` jobs, lock their leases, process them through the DSP orchestrators, and mark them `COMPLETED` or `FAILED`.
   - According to Global Rule 1, any state machine daemon loop polling SQLite MUST implement hardware guardrails (jittered exponential backoff) to prevent Thundering Herd I/O crashes.

## Your Mission
Act as the Fable 5.1 Director with Opus 5.5 sub-agents. You have full `--dangerously-skip-permissions`.

1. Implement `src/cochem/watchdog/watchdog_sre.py` exactly as specified in the SRS `ch08`.
2. Implement `src/cochem/dsp/worker_daemon.py` to continuously poll `job_board.db`, locking jobs and executing them using the existing `src/cochem/dsp/toolkit/mcp_server.py` and `router.py`. Ensure you implement safe polling logic (jitter, lease timeouts).
3. Update or create the necessary Windows Scheduled Task registration scripts (or `.bat` startup scripts) in `provisioning/` to ensure these two daemons start automatically alongside the Host Warden.
4. Provide a `FABLE_DEPLOYMENT_COMPLETION.md` report summarizing your work.
