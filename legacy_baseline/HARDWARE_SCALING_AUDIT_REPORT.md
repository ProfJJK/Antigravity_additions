# HARDWARE SCALING AUDIT REPORT: PIPELINE 3.0.0

- **Task:** V3_HARDWARE_SCALING_AUDIT
- **Date:** 2026-09-24
- **Audited files:** `.scripts/task_work_loop.py` (hardware-adaptive concurrency, lines 340-470 and the `main()` scheduler loop), `__agentic/v3/docker_runner.py` (`DockerSandboxRunner`), `Pipeline_3_0_0_Startup.bat` (daemon launcher)
- **Contract:** `tests/tdd/test_task_V3_HARDWARE_SCALING_AUDIT.py` (T1-T5)

## Executive Summary

This audit checks whether the Pipeline 3.0.0 work loop still sizes its worker pool from live hardware telemetry. It also checks that the daemon start-up path is headless and windowless, and that the Docker sandbox runner does not add a hidden limit on concurrency. Every claim below was checked against the source of the audited files. The tiered, telemetry-driven worker pool in `task_work_loop.py` is intact: it reads CPU, RAM and GPU usage and resizes an asyncio semaphore at run time. The Docker runner has no synchronisation primitives of its own, so the work loop's semaphore is the only thing that limits sandbox concurrency. The new `Pipeline_3_0_0_Startup.bat` starts the three daemons with a headless environment, windowless `start "" /B` launches, pre-flight existence checks, log redirection and no interactive commands or credentials. Two minor edge cases are recorded as observations. Neither blocks the audit.

## Dynamic Hardware Scaling Invariants

**Configuration (module globals, read once at import from the environment):**

- `_HW_CEIL` = `COCHEM_MAX_CONCURRENT_PROCESSES`, default **60** (hard ceiling).
- `_HW_FLOOR` = `COCHEM_MIN_CONCURRENT_PROCESSES`, default **1** (floor under heavy load).
- `_HW_POLL_INTERVAL` = `COCHEM_HW_POLL_INTERVAL`, default **30 s** between hardware polls.
- `_RAMP_STEP` = `max(1, COCHEM_RAMP_STEP)`, default **5** tasks per ramp-up step.

**Telemetry (`_compute_max_workers`):** when `psutil` is importable (`_PSUTIL_AVAILABLE = True`), CPU is sampled with `psutil.cpu_percent(interval=2.0)`, a 2-second window that captures the start-up burst of newly spawned processes. Free RAM is `psutil.virtual_memory().available` minus the resident set size of all running processes whose name contains `claude`, so headroom already used by those processes is not counted twice. GPU usage comes from `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits`, run through `subprocess.run` with `creationflags=subprocess.CREATE_NO_WINDOW`, `encoding="utf-8"` and a 5 s timeout. The highest used/total percentage across GPUs is kept. Any `nvidia-smi` failure is ignored, which leaves GPU usage at 0%.

**GPU rule:** if VRAM usage is above 80%, the working ceiling is reduced to `max(floor, ceil // 4)` before the tier table is applied.

**Tier table** (first matching row wins; `ceil` is `_HW_CEIL`, after any GPU cap; `floor` is `_HW_FLOOR`):

| Condition | Worker count | Default result (ceil 60, floor 1) |
|---|---|---|
| CPU < 30% and RAM free > 16 GB | `ceil` | 60 |
| CPU < 50% and RAM free > 8 GB | `max(floor, ceil*3//4)` | 45 |
| CPU < 70% and RAM free > 4 GB | `max(floor, ceil//2)` | 30 |
| CPU < 85% and RAM free > 2 GB | `max(floor, max(4, ceil//4))` | 15 |
| otherwise | `floor` | 1 |

**Fallback when psutil is unavailable:** the function keeps its safe defaults of `cpu = 50.0`, `ram_free_gb = 8.0` and `gpu_pct = 0.0`. It does **not** call `nvidia-smi` in this case, because the GPU query is inside the `psutil` branch. CPU 50.0 fails the `< 50` test, and 8.0 GB fails the `> 8` test, so the result is always the CPU < 70% tier: `max(floor, ceil // 2)`, which is 30 with the defaults. T2 checks this with (40, 3) -> 20, (100, 1) -> 50 and (12, 9) -> 9.

**Semaphore resize (`_refresh_worker_semaphore`):** calls `_compute_max_workers()`. A new `asyncio.Semaphore(new_limit)` is created and assigned to the global `_worker_semaphore`, with `_current_worker_limit` updated, **only when the new limit differs from `_current_worker_limit`**. If the limit is unchanged, the existing semaphore object is kept. The function returns the limit in both cases. `main()` calls it once at start-up, then every `_HW_POLL_INTERVAL` seconds, and again after each ramp step, where the scheduler sleeps `_HW_POLL_INTERVAL / 2`. Each ramp step starts at most `min(_RAMP_STEP, queued, _current_worker_limit - in_flight)` tasks. Each task takes a slot through `_run_with_hw_semaphore`. A task that already holds a slot in a replaced semaphore keeps it until it finishes. New tasks acquire from the new semaphore, and the ramp loop counts in-flight work through `_active_worker_count` so it does not overshoot.

**Observations (edge cases, not defects in normal configuration):** (1) If `COCHEM_MIN_CONCURRENT_PROCESSES` is set above `COCHEM_MAX_CONCURRENT_PROCESSES`, the top tier returns `ceil` while the other tiers return `floor`. (2) With a ceiling below 4, the CPU < 85% tier's `max(4, ...)` minimum can exceed the ceiling. The defaults (60 / 1) are not affected.

## Daemon Startup Audit

`Pipeline_3_0_0_Startup.bat` launches three daemons: `task_work_loop` (`%PIPELINE_ROOT%.scripts\task_work_loop.py`, where `PIPELINE_ROOT=%~dp0`), and `cochem_ha_daemon_watchdog` and `cochem_idle_sidecar` (both under `AGENTIC_SCRIPTS=D:\__agentic\scripts`).

**Headless environment:** before the first launch line the script runs plain `set` statements for `CI=1`, `NO_COLOR=1`, `TERM=dumb`, `NONINTERACTIVE=1`, `PYTHONUNBUFFERED=1`, `PYTHONIOENCODING=utf-8` and `GOLANG_HEADLESS=1`. The daemons inherit these, and so do the CLI subprocesses they spawn (for example the Go-based `agy` CLI).

**Root cause of the rc=2 crash:** the Go package `atotto/clipboard` binds `user32.dll` at package initialisation on Windows through `syscall.MustLoadDLL`. Unlike `LoadDLL`, `MustLoadDLL` panics when the DLL cannot be loaded or initialised. This can happen when a process runs outside an interactive desktop, or when many console windows have used up the desktop heap. An unrecovered Go panic ends the process with exit code 2, which the pipeline logged as rc=2 CLI failures. Because this load happens at package initialisation, environment variables cannot stop it. The headless variables do stop CLIs that honour them from reaching colour, TTY and clipboard code paths. The main fix is at the process level.

**Windowless mitigation:** each daemon starts on a single line of the form `start "" /B python -u "<script>" > "%LOG_DIR%\<name>.log" 2>&1`. `start ""` passes an empty title, so the quoted script path is not taken as the window title. `/B` runs the process without creating a new console window, so each daemon does not add a console host and use more desktop heap. `-u` together with `PYTHONUNBUFFERED=1` flushes the logs straight away.

**Safety checks:** `where python >nul 2>&1` stops the script with `exit /b 1` if Python is not on PATH. Each script path is checked with `if not exist ... (echo [ERROR] ... & exit /b 1)`. `LOG_DIR` is created if it is missing. The script has no `pause`, `choice`, `set /p` or `cmd /k`, and it contains no keys, tokens or credentials. It ends with `endlocal` and `exit /b 0`.

## Docker Concurrency Evaluation

`DockerSandboxRunner.build_run_command` produces a hardened, ephemeral argv: `docker run --rm --read-only --user 1000:1000 --cpus 2.0 --memory 2g --security-opt no-new-privileges --cap-drop ALL --network none -v cochem-data:/workspace/data -w /workspace`, then any validated `-e K=V` pairs, then the image (`cochem-v3:latest` by default), then the user command unchanged. Host-path and daemon-socket volume bindings and root users are rejected with `ValueError`. The `--cpus`/`--memory` limits apply to each container, not to the pipeline as a whole.

**No concurrency throttling:** the module has no lock, no semaphore, no queue, no event, no condition, no barrier and no `global` statement. The argv also has no fixed `--name`, which would make parallel runs collide on the container name. `DockerCommandConfig` is a frozen dataclass, and each `DockerSandboxRunner` only stores its config, so one instance can be shared across threads safely. `execute()` starts each job with `subprocess.Popen` (`stdin=DEVNULL`, `text=True`, `encoding="utf-8"`, `errors="replace"`, `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)`) and waits with `communicate(timeout=...)`. On timeout it kills the process and raises `SandboxTimeoutError`. Parallel `execute()` calls therefore run fully in parallel, which T3 checks with four overlapping 1.5 s probes. Sandbox concurrency is limited only by the `task_work_loop` asyncio semaphore described above.

## Audit Verdict

**PASS.** Dynamic hardware scaling is **PRESERVED**. `_compute_max_workers` still reads live `psutil` CPU/RAM and `nvidia-smi` VRAM data, applies the five-tier table with the VRAM cap above 80%, and stays within `_HW_FLOOR`/`_HW_CEIL` (defaults 1/60) in the default configuration. `_refresh_worker_semaphore` resizes the global asyncio semaphore only when the computed limit changes. `DockerSandboxRunner` keeps full container isolation and adds no serialisation of its own. `Pipeline_3_0_0_Startup.bat` starts all three daemons headless, windowless and non-interactively, with no embedded credentials. No remediation is required. The two configuration edge cases in the scaling section are advisory only.
