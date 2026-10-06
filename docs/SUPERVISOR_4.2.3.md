# 4.2.3 supervisor: bounded recovery outside the pipeline

The supervisor observes and recovers the planning pipeline from a separate
protected Windows installation and Python environment. Its SQLite incident
ledger and repair limits remain independent of the pipeline's job board.
This separates the observer from the environment it may need to restart,
replace or roll back.

For a heartbeat or native-status incident, it attempts one restart before
model escalation. Eligible code/protocol defects and explicit update requests
can enter the bounded repair path directly, using an explicitly
configured native subscription CLI. The default repair route is Codex Astra;
Claude Fable is a configurable alternative. The actual executable and model
ID must be supported by the installed CLI/account. Selecting an alias does
not establish model availability.

## What authorizes recovery

Monitoring uses the pipeline's native heartbeat and read-only database
observations. Database access for diagnosis does not authorize changing job
states or marking model work complete. Healthy recovery requires actual
workflow state and continuing heartbeat progress after restart; a process
existing or a model saying “fixed” is insufficient.

The default limits are two repair reservations per incident, four per UTC
budget day, and a 30-minute cooldown. A reserved attempt remains charged even
if its process or supervisor crashes. The supervisor records incident/attempt decisions
durably so restarting it does not erase the limits. Exhausting a budget or
failing a prerequisite leaves an explicit unresolved incident rather than
starting an unlimited model loop.

Repair execution is not exactly once. For example, a process crash after the
release journal reaches COMMITTED but before the attempt ledger is finished
can leave an interrupted, charged attempt and permit a later retry within the
remaining budget. Restarting the supervisor does not erase that charge.

Authentication, quota, provider/network outages, insufficient resources,
deployment configuration and unclassified failures are held for diagnosis or
operator action. Repeated implementation/protocol failures can qualify for
bounded code repair. Monitoring reads bounded heartbeat/job metadata and
redacts diagnostic text; it does not collect prompt bodies, document artifacts
or authentication logs as routine observation evidence.

Compatibility failures in a pipeline worker adapter can qualify for source
repair. Incompatibility of the supervisor's own repair CLI, detected through
its version/help probes, is a nonrepairable configuration hold requiring an
administrator update. A repair model is not asked to rewrite its protected
launcher or compensate for missing native CLI capabilities.

Repair runs use a dedicated standard Windows identity with its own native
Codex/Claude subscription authentication. Worker account credentials remain
in the appropriate protected Windows credential store. The supervisor does
not replace subscription authentication with API keys or copy an interactive
user's provider credentials.

## Candidate validation and release activation

1. Collect bounded diagnostic evidence from the observed failure and record
   the incident before dispatching a model repair.
2. Copy the pipeline source into an isolated candidate workspace.
   Only changes within the configured source allowlist can be accepted.
   The repair identity cannot edit the protected supervisor, its ledger,
   release pointer or external acceptance tests.
3. Run the protected external acceptance suite against the candidate. These
   tests live outside the repair workspace and remain immutable to the repair
   identity. A model-written test result is not acceptance evidence.
4. Stage an accepted candidate as a protected versioned release. The trusted
   supervisor, rather than the repair account, controls release publication
   and the atomic active-release pointer.
5. Restart the pipeline against that release and check real workflow state
   plus heartbeat progress. If the checks fail, restore the prior release
   pointer and restart the prior version.
6. Recover interrupted publication/restart operations from the durable crash
   journal. A supervisor restart must resolve the recorded operation instead
   of assuming that either candidate activation or rollback completed.

The release mechanism changes deployed code, not the provider's reported
answer or the historical acceptance evidence. Protected accepted artifacts
and the pipeline's ownership/fencing rules remain the authority for workflow
completion.

Release snapshots reject links, hardlinks, reparse points, ambiguous Windows
paths and case collisions. Changed files must be allowed Python sources;
supervisor code, tests, configuration and dependency/build files are excluded
from repair. The protected release is identified by a SHA-256 file-manifest
digest. The active pointer records `schema`, absolute `source_root`, `digest`
and `release_id`; the latter is the same digest. Existing pointers are not
silently replaced during bootstrap.

The journal advances through `PREPARED`, `ACTIVATING`, `VERIFYING` and
`COMMITTED`. A failed candidate enters `ROLLING_BACK`, followed by
`ROLLED_BACK` or an explicit `ROLLBACK_FAILED`. Each stage is recorded before
its external operation. An interrupted nonterminal activation conservatively
restores the previous release before starting any candidate. Stop/start/probe
callbacks must report actual success, and the source digest is checked after
the health probe. Atomic replacement and file synchronization support process
crash recovery; full power-loss durability also depends on the filesystem and
storage policy.

This mechanism replaces code only. It does not upgrade Python, native CLIs or
dependencies, change supervisor policy, or provide database/schema migration
and data rollback. Repairs must preserve the existing persistent-data
contract. Source allowlists and ordinary tests do not prove that every
possible behavioral or schema change is absent; changes requiring migration
need a separately reviewed deployment and backup plan.

Post-restart health requires two progressing heartbeat samples from the new
process and matching authenticated controller health, including the selected
source path. The deployment smoke check submits a real two-chapter workflow:
Codex manifest, Codex chapter, Claude chapter and Gemini synthesis, with one
attempt per node. It checks completed DAG state, native process/session
receipts, accepted artifact hashes and synthesis chapter-hash tracing.
Unfinished smoke work is cancelled on failure; unconfirmed cancellation is
recorded explicitly. Requested model IDs and native reported IDs are kept
distinct when a CLI omits model metadata.

These live smoke calls consume subscription quota in addition to the repair
call. The two/four reservation limits bound repair attempts, not the total
number of provider calls. Missing Agy capability or any required account login
prevents the full deployment check from passing.

The first restart and a rollback check the restored service's heartbeat and
authenticated API. The additional paid workflow is the candidate deployment
gate; recovery of the previous release does not require a second smoke run.

## Deployment boundaries

The supervisor and pipeline must have separate protected installation roots
and separate virtual environments. Their state directories, candidate roots,
release storage and acceptance-test location must follow the configured ACL
boundaries. A user-writable checkout or an editable Python installation is not
the production SYSTEM runtime. Repair identities must be unable to replace
either runtime or alter the external acceptance checks.

Use the [4.2.2 pipeline guide](PIPELINE_4.2.2.md) for the underlying planning
service and its dedicated provider identities. Supervisor configuration is a
separate deployment concern; existing pipeline/MCP settings are not replaced
by an unrelated example. Keep CLI model IDs explicit, paths absolute, and
provider authentication tied to the actual repair identity.

### Install on the Windows host

Complete the protected pipeline installation first, including its registered
`CoChem-4.2.2-Warden` task. Use an elevated Windows PowerShell terminal in this
checkout. Python 3.12+ and the native repair CLIs must be installed under
protected Program Files paths. Their owners must be SYSTEM, Administrators or
TrustedInstaller; standard users must not be able to replace the executables
or their parent directories. A per-user CLI installation does not satisfy
this privileged deployment boundary.

```powershell
.\scripts\install_supervisor_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -OperatorName 'COMPUTER\YourUser'
```

Replace the Python path and operator name with this host's values. The default
locations are:

| Purpose | Default location |
| --- | --- |
| Supervisor installation and separate `.venv` | `C:\Program Files\CoChem\Supervisor4.2.3` |
| Existing pipeline installation and `.venv` | `C:\Program Files\CoChem\Pipeline4.2.2` |
| Supervisor private state | `C:\ProgramData\CoChemSupervisor423\private` |
| Repair workspace | `C:\ProgramData\CoChemSupervisor423\workers\repair` |
| Protected versioned releases | `C:\Program Files\CoChem\PipelineReleases423` |
| External acceptance snapshot | `C:\Program Files\CoChem\Supervisor4.2.3\acceptance` |

The installer provisions `CoChem423Repair`, with its password held in SYSTEM
Credential Manager under `CoChem423/repair`. It preserves existing installed
code, test snapshots and generated configuration. It writes
`windows-layout.json` and `supervisor.generated.json` in the supervisor
installation. The generated configuration lists Codex Astra followed by Claude
Fable; review it and retain only the repair providers actually configured for
this account. No pipeline/supervisor daemon or inference is started at this stage.

Authenticate each retained provider under the dedicated repair identity:

```powershell
.\scripts\login_supervisor_worker.ps1 -Provider codex `
  -Executable 'C:\Program Files\Codex\codex.exe'
.\scripts\login_supervisor_worker.ps1 -Provider claude `
  -Executable 'C:\Program Files\Claude\claude.exe'
```

Those executable paths are examples; use the actual protected native binaries.
The helper displays the provider's browser/device login instructions and stores
authentication in the repair account's own profile. Its private local login
logs are separate from routine supervisor diagnostics. Log in only the
providers retained in `providers`; regular-user and pipeline-worker logins do
not authenticate this repair account.

Review `supervisor.generated.json`, including paths, providers, budgets and
`auto_deploy`. Save the reviewed JSON separately, then register using its path:

```powershell
.\scripts\install_supervisor_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -OperatorName 'COMPUTER\YourUser' `
  -Config 'C:\Users\YourUser\supervisor.reviewed.json' `
  -RegisterSupervisor
```

Registration installs `supervisor.json` and refuses to overwrite an existing
different configuration. It migrates the Warden task to the
protected release-pointer launcher. It registers the independent
`CoChem-4.2.3-Supervisor` SYSTEM task without starting it. The initial pointer
selects the administrator-installed 4.2.3 source snapshot; the pipeline keeps
its separate interpreter. Original Warden task XML and the previous source
hash are retained under `private\installation-rollback`. That installation
record is separate from subsequent automatic candidate rollback.

At a safe workflow boundary, a full Windows **Restart** is the safest way to
activate the migrated installation and clear old process trees. Both registered
tasks start at boot. After signing back in, inspect their state:

```powershell
Get-ScheduledTask -TaskName 'CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor'
```

Starting the supervisor enables the configured automatic repair policy and
can consume subscription quota. A Running task alone is not acceptance:
verify the native heartbeat, ledger decisions and a real workflow through the
pipeline's authenticated frontend. If custom task names or roots were used,
use those same values throughout registration and operation.

### Operator commands and updates

Use the installed supervisor interpreter and protected configuration. The
following commands belong in the supervisor's authorized SYSTEM execution
context; they are not tools exposed by the unprivileged Antigravity MCP
frontend. An ordinary Windows Python terminal does not provide that context.

```powershell
$SupervisorPython = 'C:\Program Files\CoChem\Supervisor4.2.3\.venv\Scripts\python.exe'
$SupervisorConfig = 'C:\Program Files\CoChem\Supervisor4.2.3\supervisor.json'
& $SupervisorPython -I -m cochem_supervisor status --config $SupervisorConfig
& $SupervisorPython -I -m cochem_supervisor request-update --config $SupervisorConfig `
  --objective 'Correct the demonstrated pipeline behavior while preserving its data and provider contracts.'
& $SupervisorPython -I -m cochem_supervisor resume --config $SupervisorConfig `
  --fingerprint '<blocked incident fingerprint>' --reason 'Required provider login restored'
```

`status` reports the supervisor status file and incident ledger.
`request-update` records an operator objective; the daemon later processes it
through the same reservation limits, source allowlist, external acceptance,
deployment smoke and rollback checks as automatic repairs. It does not
immediately run a model or grant broader repair scope. `resume` reopens a
BLOCKED incident after its prerequisite is restored, preserving spent attempts
and cooldown history.

`daemon` runs the monitoring loop. `once` performs recovery and one observation/
action cycle and can spend quota. `recover` resolves the deployment crash
journal. These commands take the same `--config` argument; the installer
registers `daemon` as the persistent SYSTEM task.

With `auto_deploy: false`, a validated candidate is retained and its attempt
is recorded as BLOCKED. There is currently no manual promotion command.
`resume` does not activate that prepared candidate, and changing this policy
does not bypass the deployment checks or restore an exhausted budget. Updating
the supervisor itself, dependencies, native CLIs or persistent-data schema
requires a separate administrator-managed release.

### Cleanup quarantine

If the repair process tree or Windows profile cannot be confirmed closed,
the supervisor writes `private\repair-quarantine.json` and enters the
quarantined stage. The marker persists across supervisor restarts and blocks
further normal supervisor work, including native repair/probe dispatch and
workspace reuse. It is separate from incident budgets and the release crash
journal; `resume` does not clear it.

Stop the supervisor and have an administrator verify that the repair account's
processes and profile are no longer active. A host reboot is the safest
starting point when cleanup is uncertain. Preserve the diagnostic receipts,
then clear the protected marker only after cleanup is verified and restart
the supervisor. A task reporting Stopped, deleting the marker, or restarting
the supervisor alone is not evidence that cleanup succeeded. Deployment
journal recovery remains a separate operation for an interrupted release.

The Warden has a separate `private\warden-process.json` ownership record.
It records the launcher PID, process creation time, native Job Object handle
and Windows boot identity reported by the OS. Before stopping a running
Warden, the supervisor retains the recorded Job handle, terminates the tree
and verifies that its active-process count reaches zero. A normal Warden child
crash lets the launcher record verified cleanup. An uncertain hard crash of
the launcher during the same Windows boot blocks automatic restart/deployment;
a task state or missing PID cannot prove tree cleanup.

A full Windows Restart supplies a new OS boot identity, allowing prior-boot
ownership records to be treated as exited. This recovery path does not clear
the separate repair quarantine marker or its administrator verification
requirement. Avoid deleting either ownership evidence or quarantine records
merely to force another launch. These native Windows behaviors still require
target-host acceptance.

### Configuration policy

The supervisor configuration uses absolute native paths. `private_root` holds
its ledger and `pointer_file`; `repair_workspace`, `release_root` and
`acceptance_root` must be separate, non-overlapping locations.
`baseline_source` identifies the initial protected source snapshot.
`pipeline_python` and `pipeline_config` identify the separate pipeline runtime;
`test_python` identifies the interpreter for the external acceptance checks.
The repair identity is configured through `repair_worker.name` and
`repair_worker.credential_target`.

`providers` contains one or two explicit native CLI specifications. Each has
`provider`, `executable` and `model`; the supported repair selections are
`codex` / `gpt-6-astra` and `claude` / `claude-fable-5-1`. These configured
identifiers still require availability in the actual installed CLI/account.
Missing authentication or an unavailable model cannot be repaired by changing
source code.

The default `allowed_paths` cover `src/cochem_pipeline/`, `src/cochem_mcp/`
and `src/cochem/warden/ladder.py`. They may be narrowed; supervisor code,
acceptance tests, configuration and dependency files remain excluded.
`test_targets` defaults to selected core files from the protected
`pipeline_tests` and `mcp_tests` snapshots. It is not a full-repository test
run: legacy tests needing root wrappers or report fixtures are outside that
installed acceptance set. The current list is in
[`config.py`](../src/cochem_supervisor/config.py); the installer preserves the
code-defined default rather than generating a broader suite selection.
Acceptance checks the actual process exit, named JUnit cases, exact counts,
zero failures/errors, and passed/skipped thresholds. These normal regression
tests and the independent deployment probe are not cryptographic attestation
against arbitrary malicious candidate code.

| Policy setting | Default |
| --- | --- |
| `poll_seconds` | 10 seconds |
| `startup_grace_seconds` | 120 seconds |
| `heartbeat_timeout` | 30 seconds |
| `stall_timeout` | 600 seconds |
| `repeated_failures` | 3 matching failed jobs or job attempts |
| `max_per_incident` / `max_per_day` | 2 / 4 repair reservations |
| `cooldown_seconds` | 1,800 seconds |
| `repair_timeout_seconds` | 900 seconds |
| `test_timeout_seconds` / `smoke_timeout_seconds` | 600 / 600 seconds |
| `max_log_bytes` | 16 MiB combined process output |
| `minimum_passed_tests` / `maximum_skipped_tests` | 200 / 64 |
| `version_probe_seconds` | 3,600 seconds between CLI version/help probes |

The output limit is checked while the child runs and after it exits; it is a
termination guard, not a filesystem quota. Repair receipts record the native
process, requested model, any reported model, session and output hashes. They establish
execution evidence, while the external checks decide candidate acceptance.

The supervisor does not remove the existing Agy integration prerequisite.
Gemini synthesis still needs the installed native headless command,
subscription-status probe, native result/session/model metadata and
worker-profile authentication described in the pipeline guide. A missing
external capability or login must remain a reported blocker, not be converted
into a repeated source repair.

## Development checks and evidence limits

The development suites are explicit:

```bash
python -m pytest supervisor_tests pipeline_tests mcp_tests -q
```

Linux tests can exercise real SQLite, candidate files, child processes,
publication pointers and failure/recovery contracts. They do not establish
Windows SYSTEM privilege, cross-account ACLs, native subscription inference,
or a successful live repair-and-rollback cycle. Those require the actual
provisioned Windows host and configured provider accounts. No full Windows
or live-model acceptance is claimed by this guide.

The native checks in [`test_windows.py`](../supervisor_tests/test_windows.py)
require `COCHEM_SUPERVISOR_WINDOWS_CONFIG` to name the installed
`supervisor.json` and the supervisor interpreter to run as SYSTEM. They test
the independent layout and actual child/descendant containment. On Linux they
are skipped. Only reviewed, administrator-protected test code should be run
in that privileged host context; the cloud run does not provide it.

The cloud environment proposal adds these suites and the supervisor version
check in [`config/cloud-environment.proposed.json`](../config/cloud-environment.proposed.json).
The earlier draft save returned `stale_base`; this release updates the local
proposal without retrying that stale draft. A new setup chat is needed only
to apply the recipe to a fresh cloud environment draft.
