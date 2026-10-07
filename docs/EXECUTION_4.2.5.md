# 4.2.5 coding, container and RAM-disk deployment

4.2.5 extends the protected Windows pipeline with coding execution, Docker test
isolation, verified RAM workspaces and measured hardware admission. The native
Windows controller, subscription CLIs and durable state remain separate from
the offline code/test containers. Follow the
[requirements ledger](REQUIREMENTS_4.2.5.md) for exact source clauses and
remaining evidence; a deployed Windows system and live three-provider run are
not established by the Linux development checks.

## Execution boundaries

| Component | Location and authority |
| --- | --- |
| Antigravity and MCP client | Ordinary Windows operator account; authenticated loopback API submits work and reads status. It cannot claim leases or manufacture completion receipts. |
| Pipeline controller | SYSTEM, protected Python and installed source; owns SQLite DAGs, routing, leases, hardware admission, RAM mounting and Docker lifecycle. |
| Native model runners | Dedicated standard worker accounts, each with its own native subscription login. Real Codex/Claude/Agy process metadata is required. |
| Coding scratch | Verified ImDisk NTFS directory mount, separate per-worker ACLs, bounded RAM allocation. Persistent account/profile/authentication is not moved. |
| Test containers | Offline, read-only root, bounded writable tmpfs, nonroot, limited memory/CPU/PIDs, no privileged host binds or Docker socket. The controller selects commands and exact image IDs. |
| Repair supervisor | Independent protected installation, interpreter, tests, incident ledger and release pointer. It retains bounded repair budgets and cannot repair its own policy or dependencies. |

The model order remains exactly the user's corrected matrix:

| Complexity | Ordered candidates |
| --- | --- |
| 1–3 | Gemini 3.8 Flash → Claude Haiku 4.5 → GPT-6 Luna |
| 4–6 | Claude Sonnet 5.5 → GPT-6 Sol → Gemini 3.1 Pro |
| 7–9 | Claude Opus 5.5 → GPT-6 Astra with `low` effort → Gemini 3.1 Pro |
| 10 | Claude Fable 5.1 → GPT-6 Astra with `ultra` effort; only two |

The planning/coding scheduler has a shared four-seat native/container limit.
The independent repair child has its own explicit resource bounds. Historical
19/20/60-agent and six-container pipeline examples are superseded. Planning synthesis stays pinned to
the exact native ID `gemini-3.1-pro` after all chapter hashes are accepted.

## Prerequisites on Windows

Use elevated Windows PowerShell 5.1 or later for installation. Install Python
3.12+ and standalone `uv.exe` in machine-protected Program Files directories.
The installers validate ownership and write permissions, create separate
protected virtual environments, and run `uv sync --frozen --no-editable` from
the copied `uv.lock`. uv must be outside the environment it synchronizes.
The default uv path is `C:\Program Files\uv\uv.exe`; pass `-Uv` for another
protected installation.

Install the actual native Codex, Claude and Agy executables in protected
machine-wide locations. Verify their installed headless arguments, subscription
status contracts and model availability. `scripts/inspect_agy_windows.ps1`
collects version/help without inference; it cannot supply a missing native
headless/authentication interface. Do not replace native subscription login
with API keys or copy another account's credential files.

For coding execution, the same Windows SYSTEM identity that runs the controller
must be able to reach a local Docker engine running Linux containers, including
after reboot. A Docker Desktop window open under your interactive account is
not proof of that access. Configure the explicit local named-pipe endpoint and
protected `docker.exe`; the installer checks them through a SYSTEM task.
Set `docker.pipe_server_executables` to the exact reviewed, protected native
backend executable paths used by this installation. The controller queries
the real pipe-server PID, process creation time, token and executable; only
SYSTEM or the configured operator may own the server, and its protected Docker
backend path must match that list. A worker-owned or unknown server is rejected
even if it denies client access. No server PID or backend location is guessed.
Server attestation runs before each Windows Docker API command. Native model
auth/probe/inference launches also recheck the current worker's access boundary.
Actual read, write and duplex access to the configured and existing known
Docker Desktop API pipes must be denied to every model worker. The supervisor
also checks its separate repair identity before charging a repair and rechecks
before each repair or candidate-test process. Missing/busy pipes do not prove
denial for a required Docker engine. Even when pipeline Docker is disabled,
existing known local Desktop API pipes must deny access; absence of an optional
engine does not block work. This checks local configured/known API pipes, not
every unrelated remote Docker endpoint. A failed repair boundary holds repairs
while the independent watchdog remains able to diagnose or recover an
unavailable engine. Pipe probes use identification-only security quality of
service; if restoring SYSTEM impersonation fails, the entire process exits
instead of returning an unsafe thread to its pool.

Install ImDisk and its AWEAlloc support for nonpageable physical RAM. The
canonical mount remains
`D:\__CoChem\__agentic\.scripts\tdd_runs`. Its physical parent/ancestors must
meet the protected-path requirements. Existing content is preserved before an
empty mount point is created; an unexpected junction or unknown mounted volume
is rejected. Do not broadly loosen ACLs or reparse-point checks to get past an
installation failure.

Default RAM backing is `awe`. Ordinary ImDisk `vm` backing is pageable and
cannot guarantee that writes never reach an SSD. RAM workspaces and native
temporary files do not prove that every undocumented CLI cache has moved:
`CLAUDE_PROJECT_DIR` from the reconstruction plan is not treated as a verified
cache contract. The implementation instead backs up and selectively redirects
the native `.claude/projects` directory to the owned RAM slot, then requires
the CLI's native `projectsDirectory` and `configDirectory` report to confirm
the project-cache/authentication separation. `cache_write_observation` records
actual RAM project files and bytes. This does not claim that every CLI cache
has moved or that persistent authentication performs no SSD writes.
Credentials, SQLite/WAL, receipts, source baselines, release
journals and repair budgets remain on persistent storage. Any prompt files
must also respect the adopted physical-storage rule; native prompt delivery
uses stdin rather than staging a Claude prompt inside the RAM mount.

Hardware admission requires actual measurements. The default policy expects
GPU, Windows commit, CPU-temperature and disk-I/O telemetry. Windows CPU
temperature collection uses supported LibreHardwareMonitor WMI CPU sensors.
An absent required sensor is a blocked prerequisite, not a zero reading.
NVIDIA measurements require the protected System32 `nvidia-smi.exe`, with no
SYSTEM PATH fallback. Windows disk performance counters must be available;
check the machine's `diskperf` configuration if disk-I/O telemetry is missing.
An unavailable counter is not evidence of idle storage.
Review `hardware` and `execution_limits` for the actual host; do not disable a
required sensor merely to label the machine ready. CPU-only deployment is an
explicit operator configuration, not the supplied GPU policy.

The default native execution policy also requires actual heterogeneous CPU
topology with available group-0 efficiency cores. It derives affinity from
Windows `GetSystemCpuSetInformation`; the old machine-specific `0x00FF0000`
mask is accepted only if it matches the current host. A homogeneous machine
does not pass the required E-core profile. Each Job Object receives and reads
back BelowNormal priority, affinity, aggregate memory, CPU and process limits
before the suspended child runs. Independent launch/poll jitter stays within
100–500 ms. Node children receive a 512 MiB old-space limit and a 16 MiB
semi-space limit; these are not a 512 MiB total heap/process limit and do not
constrain native Rust/Bun runtimes. The Job Object provides the aggregate cap.

## Build and pin the test environment

The new image recipe is
[`docker/pipeline-tests.Dockerfile`](../docker/pipeline-tests.Dockerfile).
The older scientific `docker/cochem_exec.Dockerfile` is retained historical
code and is not automatically selected for 4.2.5 execution.

From the reviewed checkout, build with the same local Docker endpoint that
the controller will use:

```powershell
& 'C:\Program Files\Python312\python.exe' .\scripts\build_pipeline_sandbox.py `
  --docker 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' `
  --endpoint 'npipe:////./pipe/docker_engine'
```

Use the actual endpoint on the machine if it differs. The helper sends an
empty build context containing only the reviewed Dockerfile and prints the
resulting exact `sha256:` image ID. Copy that ID into both `docker.image` and
`docker.allowed_images`. Runtime configuration rejects mutable tags and a
missing/non-allowlisted ID. Image creation may download dependencies; execution
containers have no network. Project-specific test dependencies require an
administrator-reviewed image built ahead of time, not runtime package installs.

`docker.commands` contains literal operator-owned test argument arrays and
timeouts. Models cannot replace them with arbitrary shell commands. Pytest
runs must execute tests; the controller owns report paths and checks result
evidence. Defaults cap each container at 4 GiB memory, two CPUs and 512 PIDs,
with a 2 GiB tmpfs limit; the actual tmpfs allocation is included in the
container memory limit. A container does not make the Windows host immune to
driver failure or resource exhaustion.

The runtime can prepare fresh single-use containers in a bounded warm pool;
prepared and active containers share the same maximum of four and memory
reservations. A measured warm fixture reached test readiness in 0.4446s; cold
creation took 4.85s and missed the historical 1.5s target. Receipts identify
whether a warm slot was used and report the actual startup time/SLA result.
Larger workloads, contention and Windows timing still require acceptance.

## Install or upgrade without resetting state

Drain managed work before upgrading. Stop and disable both existing tasks;
the installers reject running instances using Task Scheduler's instance list,
not just the displayed `Disabled` state. Preserve the old source, configuration,
release pointer and a consistent SQLite recovery backup. Do not copy a live
SQLite file while ignoring its WAL. If physical process cleanup is uncertain,
perform a full Windows Restart with the old tasks disabled before continuing.

The current default directories are:

| Purpose | 4.2.5 path / preserved identity |
| --- | --- |
| Pipeline code/interpreter | `C:\Program Files\CoChem\Pipeline4.2.5` |
| Pipeline durable state | `C:\ProgramData\CoChemPipeline422` — preserved |
| Worker identities | `CoChem422Worker1`…`CoChem422Worker6` and existing Credential Manager targets — preserved |
| Supervisor code/tests/interpreter | `C:\Program Files\CoChem\Supervisor4.2.5` |
| Supervisor state | `C:\ProgramData\CoChemSupervisor425` |
| Previous supervisor ledger | `C:\ProgramData\CoChemSupervisor424` — retained |
| Protected pipeline releases | `C:\Program Files\CoChem\PipelineReleases425` |
| Repair identity | `CoChem423Repair` / `CoChem423/repair` — preserved |
| Managed tasks | `CoChem-4.2.2-Warden`, `CoChem-4.2.3-Supervisor` — preserved |

First install the fresh pipeline code and account layout:

```powershell
.\scripts\install_pipeline_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -Uv 'C:\Program Files\uv\uv.exe' `
  -OperatorName 'COMPUTER\YourUser'
```

Merge the generated `windows-layout.json` values into a reviewed copy of
[`config/pipeline.windows.example.json`](../config/pipeline.windows.example.json).
Preserve actual worker identities, token location and private state. Replace
all rejected provider placeholders with verified native CLI contracts. Configure
the reviewed `hardware`, `execution_limits`, `ramdisk`, `docker` and registered
`coding_projects` sections. Coding configuration must explicitly enable RAM
and Docker; planning-only configuration does not establish a coding environment.

Each registered coding project specifies `repository`, `branch`,
`allowed_paths`, `test_paths` (default `['tests']`), `auto_integrate` (default
`false`) and `test_strategy`. The default `red_green` strategy requires newly
authored regression tests and a real failing assertion before implementation;
collection/import failures are not a red baseline. Explicit
`preserve_behavior` allows verified existing regression tests and requires a
passing baseline. The implementation cannot rewrite the protected tests.
Use a dedicated destination branch that is not checked out in any worktree.
Planned pytest functions are bound to their exact immutable file, module/class
and source hash; an unrelated same-name test is insufficient. Ordinary module,
class and parameterized cases are supported. Dynamic, imported, inherited or
ambiguous test identities are rejected instead of being reported as coverage.

Each coding request starts with a source-grounded plan and a separate native
planning audit. The controller validates requirement-to-test traceability,
modular SRS artifacts of at most 400 lines each, matching machine/Mermaid DAGs,
one-file leaves and batches of at most 20 tasks. Only the accepted plan can
dispatch implementation. The durable record preserves PLAN, RESEARCH,
TEST_FIRST_RED, CODE, TEST, AUDIT, IMPROVE, PLAN_NEXT, REFINE and
TEST_AND_AUDIT as distinct phases. Ten-cycle and three-research-pivot budgets
are separate limits. Skipping an improvement/refinement phase requires the
specific verified condition; it does not fabricate test or audit work.

Models supply complete-file artifact blocks, but those blocks do not grant
filesystem authority. The controller enforces registered source/test paths,
sealed tests, one targeted leaf and actual diff limits before materializing
accepted bytes. Each chunk is limited to 100 changed lines; small targeted
fixes are allowed. Cumulative edits to existing files also face the 500-line
and 80-percent rewrite limits, with explicit handling of new/short files.

```powershell
.\scripts\install_pipeline_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -Uv 'C:\Program Files\uv\uv.exe' `
  -OperatorName 'COMPUTER\YourUser' `
  -Config 'C:\CoChemReview\pipeline.json' -RegisterDaemon
```

After identity provisioning and copying the reviewed configuration, a protected
SYSTEM task runs `python -I -m cochem_pipeline provision-execution --config
<installed-config>`. It provisions/verifies the RAM mount and checks Docker and
hardware readiness. Failure stops daemon registration and names the protected
diagnostic log. It does not invent an image ID, turn an ordinary directory into
reported RAM, disable required sensors, or overwrite a differing installed
configuration. The standalone pipeline task is registered stopped and disabled.

Retain existing native authentication. For a new or unauthenticated account,
use the login helper for each required provider and slot:

```powershell
.\scripts\login_pipeline_worker.ps1 -Slot slot1 -Provider codex `
  -Executable 'C:\Program Files\Codex\codex.exe'
.\scripts\login_pipeline_worker.ps1 -Slot slot1 -Provider claude `
  -Executable 'C:\Program Files\Claude\claude.exe'
```

Those paths are examples: use protected installed executables. The helper
authenticates only that slot's native profile. Agy authentication must follow
its verified native per-account procedure; the Codex/Claude helper does not
pretend to implement an unverified Gemini login command.

Install a fresh supervisor and frozen acceptance snapshot:

```powershell
.\scripts\install_supervisor_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -Uv 'C:\Program Files\uv\uv.exe' `
  -OperatorName 'COMPUTER\YourUser' `
  -PipelineConfig 'C:\CoChemReview\pipeline.json'
```

The SYSTEM SQLite-backup migration retains prior incident history, pending
charges, cooldowns and quarantine. It leaves the old ledger untouched, accepts
an identical repeat, and rejects a differing destination or unresolved old
release journal. Do not delete the old ledger to reset a budget. Specify
`-PreviousDataRoot` if the actual previous installation used a custom path.

Review `Supervisor4.2.5\supervisor.generated.json`, preserving the fresh source,
test and state paths. Existing repair-account logins remain native; use
`login_supervisor_worker.ps1` only when authentication is needed. Then register:

```powershell
.\scripts\install_supervisor_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -Uv 'C:\Program Files\uv\uv.exe' `
  -OperatorName 'COMPUTER\YourUser' `
  -PipelineConfig 'C:\CoChemReview\pipeline.json' `
  -Config 'C:\CoChemReview\supervisor.json' -RegisterSupervisor
```

Supervisor registration enables the reviewed Warden and supervisor task actions
without starting them. Activation follows a full Windows Restart. The old
4.2.4 frozen supervisor must not be reused to approve new 4.2.5 execution
contracts. Returning to an earlier release requires compatible restored or
drained/migrated database state as well as old source; a source-pointer change
alone is not a general schema rollback.

Infrastructure recovery is bounded independently of model-repair budgets.
Three failing observations spanning at least 30 seconds are required before
one automatic recovery attempt for a component; restarting the supervisor or
observing later health does not refund that attempt. The supervisor first
saves private diagnostic metadata and a hash. Docker recovery can request only
the fixed `com.docker.service` start, then must observe a healthy engine;
missing hardware/sensor/image prerequisites block paid code repair. These
diagnostics contain bounded heartbeat, database, hardware and component
evidence; they are not a native thread dump. Windows Service Control Manager
execution still needs host acceptance.

The supervisor's separate `repair_execution_limits` policy applies to every
repair CLI, version/auth probe and candidate acceptance child. Defaults are
2048 MiB aggregate Job memory, 20% CPU and 16 processes, with BelowNormal
priority and verified E-core affinity. Native limits are applied and read back
before resume, then retained in the process receipt. Before reserving a paid
attempt and before each child, the supervisor checks actual topology, free RAM
and Windows commit headroom; failure holds repairs while observation and
recovery continue. These limits cover a separate repair child: the four-seat
planning/native/Docker scheduler does not claim to include it.

## Submit work and inspect evidence

The ordinary client uses `config/pipeline.client.example.json` adapted to its
real token path. It must not read the controller's private database directly.

```powershell
cochem-pipeline health --client-config 'C:\CoChemReview\client.json'
cochem-pipeline projects --client-config 'C:\CoChemReview\client.json'
cochem-pipeline code --client-config 'C:\CoChemReview\client.json' `
  --project reviewed-project-id --objective 'Implement the specified change' `
  --requirements REQ-001 REQ-002
cochem-pipeline code-status --client-config 'C:\CoChemReview\client.json' workflow-id
```

Use the protected installation's full executable path if it is not on PATH.
A submission response means accepted work, not completed code. Follow the
workflow state and inspect actual stage, provider, tests, diff and Git evidence.
`code-cancel` requests cancellation; `code-resume --reason TEXT` is an explicit
operator action and must not reset exhausted counters or manufacture successful
research/audit results. Planning remains available through `submit`, `status`
and the Gemini synthesis barrier.

Verified coding commits are published under
`refs/cochem/coding/<workflowSHA>/<chunkSHA>`. Automatic integration advances the
registered branch only after the reviewed Git compare-and-swap check succeeds.
The controller holds work in `READY_TO_INTEGRATE` when `auto_integrate` is
false, the destination branch is checked out in any worktree, or its baseline
has changed. It preserves dirty/untracked files and never resets or checks out
the user's working tree. Reserve this branch from concurrent external checkout:
unrelated Git commands do not honor the controller's integration lock.

After one workflow actually reaches `COMPLETED`, verify its retained evidence:

```powershell
& 'C:\Program Files\CoChem\Pipeline4.2.5\.venv\Scripts\python.exe' `
  -I -m cochem_pipeline.coding_acceptance `
  --client-config 'C:\CoChemReview\client.json' --workflow-id actual-workflow-id `
  --output 'C:\CoChemReview\coding-acceptance.json'
```

The checkout wrapper is
[`scripts/verify_coding_acceptance.py`](../scripts/verify_coding_acceptance.py).
This command submits no work and invokes no model. It checks the authenticated
controller's plan/artifact hashes, completed leaves/phases, native subscription
receipts, audits, final Docker tests/cleanup and applied Git intent; explicitly
labelled storage fixtures are rejected. It creates a new report file and refuses
to overwrite one. The unprivileged verifier trusts controller receipts and
retained stdout hashes; it does not independently read private CLI logs or
certify the entire Windows host, other workflows or long-term stability.

## Validation boundary

The new installers have not been executed on the user's Windows machine.
All seven repository PowerShell helper scripts passed syntax parsing with
Microsoft PowerShell7.4.13 downloaded from its official release and verified
against the published SHA-256 manifest. Parsing executed none of the scripts
and does not prove Windows PowerShell5.1 cmdlet or provisioning behavior.
Native RAM backing/mount identity, SYSTEM Docker access, cross-account ACLs,
Windows Job Object enforcement, actual hardware sensors and live provider
availability require target-host acceptance. Keep failed, blocked, skipped and
unrun checks distinct. Missing prerequisites stop execution; they do not become
successful tests or an invitation to weaken policy.

See [the 4.2.5 release notes](RELEASE_4.2.5.md) for executed evidence as it is
recorded, and [the clause ledger](REQUIREMENTS_4.2.5.md) for requirements not yet
closed. Historical pass counts from 4.2.2–4.2.4 are not current acceptance.
