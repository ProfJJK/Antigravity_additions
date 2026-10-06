# Host Warden Remediation Report: Ephemeral VM Lifecycle & Background Startup

**Date:** 2026-09-29 · **Scope:** `fable_remediation_prompt.md` items 1–4 · **Tree:** `D:\__CoChem\__agentic\v4.2.0`

## Summary

| # | Deficiency | Status | Evidence |
|---|---|---|---|
| 1 | `mcp_server.py` never instantiated FastMCP / no `@mcp.tool()` | Fixed | Live HTTP client listed 3 tools and called them |
| 2 | Transport unsuitable for a background daemon | Fixed: streamable HTTP (or SSE), bound to `127.0.0.1` only | Listener on `127.0.0.1:<port>`; forged `Host:` header → HTTP 421 |
| 3 | PowerShell command injection via `vm_name` | Fixed in `vm_control.py`, and the same pattern in `rollback.py` and `restart_service.py` | 5 live payloads rejected; 14 new regression tests |
| 4 | `Create-WardenTask.ps1` was an XML stub; direct-file launch broke relative imports | Fixed: real `Register-ScheduledTask`, `RunLevel=HighestAvailable`, `python -m cochem.warden.mcp_server`, explicit `PYTHONPATH` | Dry-run XML parsed; the task's exact `cmd.exe` action line started the server |

**pytest:** 267 passed, 1 failed. The failure is not caused by this work. See [Test results](#test-results).

**Not done:** I did **not** register the scheduled task on this host. It creates a persistent, elevated boot entry, so an administrator should run it deliberately (see [Deployment](#deployment)).

---

## 1. FastMCP exposure (`src/cochem/warden/mcp_server.py`)

- Added `mcp = FastMCP(name="cochem-host-warden", instructions=...)` and decorated the three tools with `@mcp.tool(...)`:
  - `get_vm_health_status()`: `readOnlyHint`, `idempotentHint`
  - `trigger_vm_resuscitation(tier)`: `destructiveHint=True`
  - `get_crash_envelopes(limit)`: `readOnlyHint`, `idempotentHint`
- In FastMCP 3.4.7 (installed), `@mcp.tool()` returns the original function. Existing in-process callers and tests (`wmcp.get_vm_health_status()`, etc.) still work unchanged.
- **Attack-surface reduction.** The tools no longer accept a `vm_name` argument: every action is pinned to `QUARANTINE_VM_NAME`. If an LLM sends `vm_name`, FastMCP rejects it with `unexpected_keyword_argument` (verified live).
  - `get_crash_envelopes` no longer exposes `evidence_dir`, which would have let a caller read JSON from any directory. That parameter now exists only on the in-process helper `read_crash_envelopes()`.
  - `limit` is capped at 100.
  - `tier` rejects `bool` (`True == 1` in Python).
- The crash evidence path is now derived from the module location instead of a hard-coded absolute string. It resolves to the same directory.
- Added a `main()` / `if __name__ == "__main__"` entry point so `python -m cochem.warden.mcp_server` starts the daemon.

## 2. Transport (`mcp_server.main`)

- `--transport` accepts only `http` (default, streamable HTTP at `/mcp`) or `sse` (legacy, at `/sse`). **stdio is not offered**, because nothing is attached to stdin/stdout when the server starts at boot.
- The bind host is the constant `BIND_HOST = "127.0.0.1"`. No CLI flag or environment variable can change it.
- Port: `--port`, else env `COCHEM_WARDEN_MCP_PORT`, else **47821**. Must be in 1024–65535.
- `host_origin_protection=True` makes the server validate the `Host` and `Origin` headers. This blocks DNS-rebinding attacks, where a web page in a local browser tries to reach the loopback endpoint. Verified: a request with `Host: evil.example` returned **421 Misdirected Request**.
- `show_banner=False`. Logs go to stderr, and the scheduled task redirects them to `.evidence\warden\mcp_server.log`.

## 3. Command injection (`vm_control.py`, `rollback.py`, `restart_service.py`)

**Root cause:** caller strings were f-string-interpolated into `powershell -Command "... -Name '{vm_name}' ..."`. For example, a payload of `x'; <cmd>; '` would run `<cmd>` with the daemon's elevated rights.

**Fix (two independent layers):**

1. **Allowlist validation.** `validate_identifier()` accepts only `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, so quotes, `;`, `$`, backticks, pipes, parentheses, whitespace and newlines are all rejected.
   - `require_quarantine_vm()` also rejects any **mutating** call (`reboot_quarantine_vm`, `restore_golden_checkpoint`, `prune_stale_checkpoints`) whose target is not `CoChem-Quarantine-VM`.
   - Read-only `get_vm_state()` still accepts other safe names. The existing test that probes `NonExistentVM-Test-Probe` still passes.
2. **No interpolation.** The new helper `run_powershell(script, params, timeout)` runs a *fixed* script string and passes values through environment variables, e.g. `Restart-VM -Name $env:COCHEM_VM_NAME -Force -ErrorAction Stop`. Even a value that got past layer 1 would be treated as data, never parsed as code.

**Also changed:**
- The same fix was applied to `rollback.restore_golden_checkpoint` / `prune_stale_checkpoints`, and to `restart_service.trigger_graceful_restart` / `wait_for_service_recovery`. They had the identical injection pattern and are reachable from `trigger_vm_resuscitation`.
- Added `-NonInteractive` and `-ErrorAction Stop` so failures surface as non-zero exit codes instead of silent success.
- `CREATE_NO_WINDOW` is preserved.

## 4. Provisioning

### `provisioning/Create-WardenTask.ps1` (rewritten)

- Builds a full Task Scheduler 1.4 XML definition and runs **`Register-ScheduledTask -Xml ... -Force`**:
  - `BootTrigger` with a 30 s delay
  - `<RunLevel>HighestAvailable</RunLevel>`
  - `MultipleInstancesPolicy=IgnoreNew`
  - `ExecutionTimeLimit=PT0S` (no time limit)
  - `RestartOnFailure` every 1 min, up to 999 times
  - runs on battery; `StartWhenAvailable`
- **Action:**
  ```
  cmd.exe /d /s /c "set "PYTHONPATH=<InstallDir>\src;<user site-packages>" && "C:\Python314\python.exe" -m cochem.warden.mcp_server --transport http --port 47821 >> "<InstallDir>\.evidence\warden\mcp_server.log" 2>&1"
  ```
  with `WorkingDirectory=<InstallDir>\src`. So:
  - the server launches as a **module** (`-m`), so relative imports resolve;
  - **`PYTHONPATH` is set explicitly** to `src`.
- **Principal:**
  - Default: the installing user with `LogonType=S4U`. It runs at boot with no one logged on and no stored password.
  - `-RunAsSystem`: switches to `S-1-5-18`.
  - Both use `HighestAvailable`.
- **Why the user site-packages are on `PYTHONPATH`:** `fastmcp` is installed per-user (`%APPDATA%\Python\Python314\site-packages`). SYSTEM, and some S4U contexts, cannot see that directory otherwise, and the daemon would die with `ModuleNotFoundError` at boot. The script resolves the path at install time with `python -m site --user-site`.
- `-DryRun` prints the XML and command line without registering anything.
- Without `-DryRun`, the script refuses to run unless the session is elevated.
- The task name is `CoChemHostWarden_V412`, consistent with the installer.

### `provisioning/Install-WardenDaemon.ps1` (rewritten)

1. Requires an elevated session.
2. Pre-flight: the interpreter exists, and `import fastmcp, cochem.warden.mcp_server` succeeds with the same `PYTHONPATH` the task will use.
3. Stops a running instance of the task before re-registering. Fails fast if the port is already taken.
4. Calls `Create-WardenTask.ps1`, then `Start-ScheduledTask`.
5. Polls `127.0.0.1:<port>` for up to 30 s. It reports the endpoint URL on success. On failure it exits 1, citing `LastTaskResult` and the log path.

**Replaced from the old installer:**
- It ran `python ...\mcp_server.py` directly, which breaks relative imports.
- It ran as SYSTEM without the per-user site-packages on the path.
- It had no error handling and printed "installed and started successfully!" unconditionally.

### Verification performed

- Both scripts parse with zero errors in the PowerShell 7.6 AST parser.
- `Create-WardenTask.ps1 -DryRun` produced well-formed XML with `RunLevel=HighestAvailable` and `LogonType=S4U`.
- **The exact `Command` / `Arguments` / `WorkingDirectory` from that XML were executed via `Start-Process`.** The server came up listening on `127.0.0.1` and logged to the redirected file. The test process was then stopped.
- `Register-ScheduledTask` was **not** executed, because the session was not elevated and this is a persistent host change.

## Test results

- **New tests:** `tests/test_warden_mcp_hardening.py`, 14 tests, zero mocks. They cover:
  - injection payloads against all five entry points
  - the quarantine-VM pin
  - the tool surface (exactly 3 tools; no `vm_name` / `evidence_dir` arguments; destructive annotation), checked through FastMCP's in-memory client
  - `tier` validation
  - the `limit` cap
  - loopback-only binding and the absence of a stdio transport
- **Full suite:**
  ```
  python -m pytest -q
  1 failed, 267 passed
  ```
- **Pre-existing failure, unrelated to this work:** `tests/test_stage3_payload_batches.py::test_f11_canonical_job_board_state_after_injection` expects exactly 183 rows in `job_board.db` and finds 184.
  - The extra row is `id=184, MC-AUDIT-HW-01, macro_audit, PENDING`, created 2026-09-29 04:05 local, before this remediation started. It was most likely added by `submit_audit.py`.
  - `job_board.db` was not modified. Whether that audit job belongs in the canonical board, with the test updated to match, is a decision for the owner.

## Deployment

From an **elevated** PowerShell:

```powershell
cd D:\__CoChem\__agentic\v4.2.0\provisioning
.\Install-WardenDaemon.ps1            # or: -RunAsSystem, -Port <n>, -Transport sse
```

MCP clients connect to `http://127.0.0.1:47821/mcp` (or `/sse` if you used `-Transport sse`).

## Residual risks / follow-ups

1. **No authentication on the endpoint.** Any local process running as any user can call `trigger_vm_resuscitation`, including a tier-3 golden rollback. Loopback binding and Host/Origin checks stop remote and browser access, but not other local processes. If other users or untrusted code run on the host, add a bearer token (FastMCP `auth=`).
2. **Elevated task runs code from a user-writable tree.** The boot task runs Python sources in `D:\__CoChem\__agentic\v4.2.0\src` with elevated rights. Anyone who can write to that tree can gain those rights. Restrict its ACLs to Administrators and the installing user.
3. **`cochem` import path.** On this machine `cochem` resolves first to `D:\__CoChem\GitHub-Repo\CoChem-BASE\src\cochem` (a regular package whose `__init__` extends `__path__`), and only then to `v4.2.0\src\cochem`. Imports work, but the daemon depends on that external package's `__init__`.
4. **Conflicting legacy task.** `Register-Startup.ps1` still registers a separate `CoChemHostWarden` task pointing at a non-existent `host_warden.py`. It was left untouched because it is out of scope; retire it or reconcile it.
5. **Unbounded log.** `mcp_server.log` is append-only and has no rotation.
6. **Hyper-V paths not exercised.** Tier 2 and tier 3 (`Restart-VM`, `Restore-VMSnapshot`) were not run against a real VM, because `CoChem-Quarantine-VM` reports `Offline` on this host.

## Files changed

- `src/cochem/warden/mcp_server.py`: FastMCP instance, tools, HTTP/SSE loopback entry point
- `src/cochem/warden/vm_control.py`: `QUARANTINE_VM_NAME`, `validate_identifier`, `require_quarantine_vm`, `run_powershell`; injection fix
- `src/cochem/warden/rollback.py`: injection fix (restore / prune)
- `src/cochem/warden/restart_service.py`: injection fix (restart / recovery probe)
- `provisioning/Create-WardenTask.ps1`: real `Register-ScheduledTask`
- `provisioning/Install-WardenDaemon.ps1`: pre-flight, register, start, probe
- `tests/test_warden_mcp_hardening.py`: new
- `FABLE_REMEDIATION_REPORT.md`: this report
