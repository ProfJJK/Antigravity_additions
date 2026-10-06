# Pipeline 4.2.2: controller, Oracle and Windows execution

This pipeline turns one planning request into a manifest, concurrent chapter
jobs, and a final Gemini 3.1 Pro synthesis. The Windows controller owns the job
board, execution leases and output validation. Antigravity's interface agent
submits the request and reads progress through MCP; model-generated claims do
not advance the database.

The supplied four specifications are traced in the
[requirements and evidence map](REQUIREMENTS_4.2.2.md). The
[historical component audit](4.2.0_COMPONENT_AUDIT.md) explains why the old
submission wrappers and review loops did not provide this execution protocol.
This guide describes the implementation and its deployment requirements;
native Windows/provider acceptance remains necessary.

## Runtime boundary

```text
Antigravity / Gemini interface agent, interactive Windows user
    |
    | stdio MCP frontend
    | authenticated localhost controller API
    v
Windows SYSTEM controller + Oracle
    |-- private job_board.db: DAG, attempts, artifacts, receipts, context
    |-- separate private Oracle database: cryptographic watermarks
    |-- hardware admission guard: at most four active workers
    |
    | isolated native CLI process, dedicated standard Windows identity
    | suspended launch -> Job Object assignment -> process resume
    v
Identity pool: slot1 ... slot6 by default
each has its own account and ACL root
at most four execute concurrently
```

The frontend does not receive direct access to controller databases. The local
service accepts submission, observation and cancellation requests; it exposes no worker
claim or completion endpoint. Workers receive a task payload and context on
stdin, not database credentials, fencing tokens or controller state paths.
This separation implements the requirements for protected watermarks and
sibling isolation. It binds to `127.0.0.1`, port 47824 by default, and requires
an authorization token. The frontend user has read-only access to the token
file; that is a file permission, not a restriction of the API to read requests.
Workers must have no access to that token.

The Antigravity frontend exposes:

| MCP tool | Operation |
| --- | --- |
| `pipeline_submit(objective, requirements, chapter_count=6)` | Create the whole planning DAG and return its workflow ID/current state. Pass explicit requirement identifiers for traceable work. |
| `pipeline_status(workflow_id)` | Read controller-accepted job states, artifacts and hashes without advancing execution. |
| `pipeline_health()` | Read controller state, hardware admission, active execution and slot quarantine. |
| `pipeline_cancel(workflow_id)` | Terminate that workflow's processes and fence unfinished work as failed. |

The corresponding local HTTP operations are `POST /submit`,
`GET /workflow/<id>`, `GET /health`, and `POST /cancel`. There is no public
`claim` or `complete` operation. Use the MCP/CLI clients so credentials stay
in the configured token file instead of shell command arguments.

Each worker identity must be a dedicated standard account with access to its
own slot and the required native CLI installations. Its filesystem ACL must
deny access to other slots, controller state, the API token and the interactive
frontend's configuration. A separate directory under the same unrestricted
user does not satisfy the sibling-read requirement. The controller retains
artifacts privately and clears an execution slot before reusing it. An identity
is not reassigned to a different chapter of the same workflow: CLI profiles
can retain history even after workspace cleanup, so cleanup alone is not
sufficient sibling isolation. Six chapters therefore need at least six
provisioned identities, while active execution remains capped at four.

The controller must run as SYSTEM for the specified privilege-tier circuit
breaker. Administrator membership alone is not equivalent to the SYSTEM
identity. The native launcher places the suspended process in a kill-on-close
Windows Job Object before it can execute, so the controller can terminate its
process tree and reclaim the attempt. These properties must be checked on
Windows; Linux subprocess tests cannot establish them.

## Request and completion lifecycle

1. One request contains the objective, requirement identifiers and chapter
   count. The controller creates a `MACRO_PLANNING_REQUEST` root, a pending
   `MANIFEST_GENERATOR`, and one blocked `SYNTHESIS` job atomically.
2. Codex generates a structured manifest. It must contain the configured
   number of chapters with unique IDs and cover all supplied requirement IDs.
   A validated manifest atomically creates its chapter jobs.
3. The hardware guard measures CPU availability/utilization, memory and free
   disk. Its capacity is capped at four. The store issues an `attempt_id`,
   fencing token and lease in the same atomic claim that checks capacity.
   Chapter jobs are assigned to Codex or Claude without provider fallback.
4. Each worker receives only its chapter payload. Its structured output must
   include `chapter_id`, `requirements_traced`, `wbs_tasks_defined`,
   `artifact_uri` and nonempty `artifact_text`. The artifact URI is the
   controller-owned `db://<workflow_id>/<chapter_id>` reference, not a file
   the worker may write into another chapter's directory.
5. The controller requires successful native process metadata, parses the
   structured result, computes the output/artifact hashes, validates ownership
   and the current lease, then commits immutable output. Prose such as
   “completed” or “PERFECT,” or a zero exit code alone, is insufficient.
6. The last accepted chapter releases exactly one logical synthesis job.
   Gemini 3.1 Pro receives all accepted chapters and their hashes. Its output
   must preserve the exact complete chapter-hash map. Only accepted synthesis
   completes the root workflow.

`PENDING`, `BLOCKED`, `IN_PROGRESS` and `PENDING_RETRY` are not successful
completion. Expired attempts are fenced off; a retry receives new ownership.
A late result from an expired or replaced worker is rejected. Artifact and
output tables reject mutation, and repeated identical completion from an
already accepted attempt is idempotent.

The database is SQLite with WAL and `busy_timeout=5000`. Process launch, model
inference and output collection occur outside write transactions. Database
constraints enforce chapter ownership and valid accepted output independently
of the worker's prose.

## Oracle behavior

The Oracle coalesces filesystem/database activity over a trailing debounce
window of at least 500ms. Rule rendering and SHA-256 watermark checks run
after that window. Watermarks live in a separate controller-private database;
a table name alone is not an access-control boundary.

Aho-Corasick matching retrieves rules by pattern and facet. The context engine
reserves a configured budget fraction for core, non-evictable directives and
uses an exact 0/1 knapsack for optional rules. Its budget unit is UTF-8 bytes,
including XML markup and escaping. Core rules must fit their reserved budget;
optional rules cannot consume unused reserved capacity. Context is delivered
through SQLite payloads and stdin, using escaped `<oracle_directive>` elements,
without transient `.warden_hints.md` files.

More than 500 events in one second trips the breaker for the affected task,
even while it is `IN_PROGRESS`. The asynchronous reaper terminates the worker
process tree, then records failure or a pending retry. The recovery audit sink
persists the full event dictionary to `recovery_telemetry`, retaining Unicode
and caller metadata. Termination and handle release must be observed before
claiming that Windows orphaned-lock recovery succeeded.

## Configuration and identity preparation

The controller configuration is loaded by `cochem_pipeline.config`. Its core
fields are:

| Field | Meaning |
| --- | --- |
| `private_root` | Absolute SYSTEM/Administrators-only controller state directory. |
| `slot_roots` | Named, absolute worker slot directories in the provisioned identity pool (up to 64). All slot/private paths must be distinct and nonoverlapping; the active-worker ceiling remains four. |
| `workers` | Matching slot keys, each with a distinct account `name` and its Windows Credential Manager `credential_target`. |
| `providers` | Explicit `codex`, `claude`, and `gemini` executable paths and exact account-supported model IDs. |
| `operator_name` | Windows account running Antigravity; receives read-only token-file access and must differ from the worker identities. |
| `token_file` | Absolute frontend-readable API token path that workers cannot access. |
| `port` | Localhost service port; default 47824. |
| `timeout_seconds` | Per-process deadline; default 1800 seconds. |
| `lease_seconds` | Execution lease renewed while a worker runs; default 30 seconds. |
| `max_attempts` | Configured retry bound; default 3. |
| `context_budget`, `reserved_fraction` | Oracle byte budget and core-rule reservation; defaults 16384 and 0.25. |
| `rules` | Oracle rule definitions including IDs, text, patterns, facets, core status and weight. |
| `min_free_memory_mb`, `min_free_disk_mb` | Admission reserves; defaults 1024 MiB and 512 MiB. These are not per-process OS memory limits. |

The six-chapter/four-worker acceptance case requires at least six isolated
identities and enough measured resources to admit four concurrent workers. A host unable to meet configured reserves
pauses admission; reducing a limit is not evidence that the original hardware
acceptance criterion passed.

Install the native Windows CLIs where every configured worker can execute
them. The isolated launcher requires administrator-protected native executables
under `Program Files` or Windows system directories and filters PATH to
protected machine directories. A CLI installation writable by an operator or
worker is not accepted for this SYSTEM-supervised deployment. Trusted file
ownership is SYSTEM, builtin Administrators or TrustedInstaller; copying a
per-user binary into `Program Files` while retaining its user owner is not
sufficient. Use an official machine-wide installation or a reviewed protected
deployment. Authenticate
Codex and Claude under **each worker account that can run
them**, using their native subscription login flows. A successful login under
the interactive Antigravity user does not authenticate a dedicated worker,
and SYSTEM does not inherit the interactive user's subscription. Account
credentials belong in Windows credential storage; do not embed passwords or
provider tokens in repository files.

Exclude the configured worker slot directories from Defender as specified by
the Oracle SRS, and verify those exclusions on the host. Keep the exclusion
scope tied to the agent working directories. Controller DB and watermark
privacy depends on ACLs, including sidecar files; Defender configuration is
not a replacement for those ACLs.

## Install the protected Windows controller

Use an elevated Windows PowerShell terminal and a machine-wide Python 3.12+
installation beneath `Program Files`. The SYSTEM daemon must not import its
runtime from a worker-writable checkout or editable virtual environment. The
installer copies this version's source and installs a separate protected venv.
Its native Windows security operations remain unvalidated in this Linux
development environment.

1. Provision the installation and six isolated accounts. Replace the sample
   Python path and operator name with their actual Windows values; the operator
   is the account running Antigravity, which may differ from the elevated
   administrator account:

   ```powershell
   .\scripts\install_pipeline_windows.ps1 -Python 'C:\Program Files\Python312\python.exe' -OperatorName 'COMPUTER\YourWindowsUser'
   ```

   Defaults are `%ProgramFiles%\CoChem\Pipeline4.2.2` for protected code,
   `%ProgramData%\CoChemPipeline422` for private/worker data, and
   `%USERPROFILE%\CoChem422\controller.token` for the operator-readable token.
   Use `-TokenFile` explicitly if elevation changes the intended profile.
   The SYSTEM provisioning task creates accounts `CoChem422Worker1` through
   `CoChem422Worker6`, slot keys `slot1` through `slot6`, and stores their random
   account passwords only in SYSTEM's Credential Manager. It provisions ACLs
   and Defender exclusions, then records nonsecret layout metadata in
   `%ProgramFiles%\CoChem\Pipeline4.2.2\windows-layout.json`.
2. Create your local controller JSON from
   `config/pipeline.windows.example.json`, preserving any existing local file.
   Copy actual layout fields as follows. The example now matches the default
   `C:\ProgramData\CoChemPipeline422\workers\slot1` through `slot6` layout;
   operator/token/CLI values and any installation overrides still need review:

   | Layout metadata | Controller setting |
   | --- | --- |
   | `private_root` | `private_root` |
   | `slots.<slot>.root` | `slot_roots.<slot>` |
   | `slots.<slot>.identity` | `workers.<slot>.name` |
   | `slots.<slot>.credential_target` | `workers.<slot>.credential_target` |
   | `operator_name`, `token_file` | Corresponding controller fields. |

   Set real executable paths and account-supported model IDs. Resolve the Agy
   invocation/result contract below before expecting synthesis to run.
3. Authenticate each native CLI under each slot account. The helper launches
   the login using the provisioned identity, so no worker password needs to be
   disclosed or copied. For example:

   ```powershell
   .\scripts\login_pipeline_worker.ps1 -Slot slot1 -Provider codex -Executable 'C:\Program Files\Codex\codex.exe'
   .\scripts\login_pipeline_worker.ps1 -Slot slot1 -Provider claude -Executable 'C:\Program Files\Claude\claude.exe'
   ```

   Repeat for `slot2` through `slot6`, and any additional configured slots,
   since scheduling can use any provisioned identity.
   Use the actual executable locations. Codex uses its native device-auth
   flow; Claude uses its native Claude subscription login. The helper displays
   the provider's URL/code locally and saves an operator-readable private log.
   Complete the flow in your browser. Login exit success does not establish
   model access; execution checks subscription status again before inference.
   Stop a slot's running job before trying to log in through the same identity.

   The helper currently covers Codex and Claude. Gemini/Agy authentication
   under those same worker profiles needs its real supported native flow;
   that remains an integration prerequisite while the Agy contract is unknown.
4. Register the daemon with your reviewed controller JSON:

   ```powershell
   .\scripts\install_pipeline_windows.ps1 -Python 'C:\Program Files\Python312\python.exe' -OperatorName 'COMPUTER\YourWindowsUser' -Config .\config\pipeline.local.json -RegisterDaemon
   ```

   Use the same path overrides as the first invocation, if any. The installer
   preserves an existing protected installation and refuses to overwrite an
   installed `pipeline.json` with differing contents. It registers
   `CoChem-4.2.2-Warden` as a SYSTEM startup task. It does not automatically
   start provider work. After authentication and configuration are resolved:

   ```powershell
   Start-ScheduledTask -TaskName 'CoChem-4.2.2-Warden'
   ```

5. Create `config/pipeline.client.local.json` from the client example with the
   actual controller port and generated token path. Configure Antigravity and
   run health/acceptance using the commands below. The protected interpreter
   at `%ProgramFiles%\CoChem\Pipeline4.2.2\.venv\Scripts\python.exe` can also
   run this unprivileged MCP frontend; launching it as the interactive user
   does not elevate that frontend.

Installer reruns preserve installed code. Code changes need a reviewed new
protected installation, rather than editing the live SYSTEM runtime through
an agent workspace. The installer's `provision.log`, native-login logs and
controller attempt logs distinguish setup errors from provider failures.

## Controller and frontend commands

The package exposes `python -m cochem_pipeline` and the installed
`cochem-pipeline` entry point. `cochem_warden_oracle.py` is the compatibility
entry point named in the Oracle SRS and accepts the same command arguments.

Use [the controller example](../config/pipeline.windows.example.json) as a
schema reference. Replace its illustrative executable paths and model IDs
with the installed, account-supported values. In particular,
`REPLACE_WITH_VERIFIED_AGY_HEADLESS_FLAGS` is an unresolved example value,
not a working Agy invocation. The controller requires the protected
deployment paths and actual provisioned identities.

Run the daemon under SYSTEM with the Python interpreter from the protected
installation, using `daemon --config <controller-config-path>`. The interactive
Antigravity user runs only the frontend/client commands. The
[client JSON](../config/pipeline.client.example.json) needs just `port` and
`token_file`; it contains the token's path rather than its secret value.

| Command arguments to `python -m cochem_pipeline` | Purpose |
| --- | --- |
| `daemon --config <controller-config-path>` | SYSTEM scheduler, Oracle, native process execution and localhost control service. |
| `mcp --client-config <client-config-path>` | Unprivileged stdio MCP frontend for Antigravity. |
| `health --client-config <client-config-path>` | Read service/hardware/runtime health without starting inference. |
| `submit --client-config <client-config-path> --objective "Create the SRS and WBS" --requirements REQ-1 REQ-2 --chapters 6` | Create a full six-chapter workflow. This leads to real provider work and consumes subscription quota. |
| `status --client-config <client-config-path> <workflow-id>` | Read accepted workflow state and artifacts. |
| `cancel --client-config <client-config-path> <workflow-id>` | Cancel the workflow and reject unfinished attempts. |

Substitute actual paths/IDs for the angle-bracketed arguments. Start with
`health`; it verifies service reachability and admission conditions, not all
provider logins or model availability. Save the `workflow_id` returned from
submission and poll that ID. Do not resubmit because a model takes time.

Merge the `cochem-pipeline` server entry from
[the Antigravity example](../config/antigravity.pipeline.example.json) into the
GUI's existing `mcpServers` object after adapting the Python and client-config
paths. Preserve unrelated server entries. This frontend has a different role
from the retained `cochem-codex` and `cochem-claude` single-job bridges.

Those standalone bridges additionally expose `codex_submit_node` and
`claude_submit_node` for structured manual development. They accept
`MANIFEST_GENERATOR` or `CHAPTER_DRAFT` payloads and call the same node prompt
and native adapter core used by the daemon. They return acceptance only and
never mutate the protected DAG, issue a lease or accept a workflow completion.
They also do not establish dedicated-identity isolation. Controller-owned
attempt/fencing/credential fields are rejected from their inputs; Gemini-only
synthesis remains on the protected pipeline surface. Use `pipeline_submit`
for the automatic scatter/gather workflow.

## Gemini/Agy integration contract

The actual native Agy headless argument syntax must come from the installed
Antigravity distribution. The pipeline does not guess that syntax, substitute
another provider, or use an API key when CLI integration is unavailable.

Run the bundled diagnostic on the Windows host to inspect its installed
version and full help without inference or authentication:

```powershell
.\scripts\inspect_agy_windows.ps1 -Executable 'C:\Program Files\Agy\agy.exe'
```

Use the real installed path. If that help advertises a headless subcommand,
rerun with `-HeadlessSubcommand` set to the exact advertised command name to
inspect its help as well. The diagnostic does not guess a command or prove
subscription/model access.

`providers.gemini.arguments` must contain the exact verified argument list
with a separate `{model}` element; `{workspace}` is replaced with the slot
path when present. The prompt is sent on stdin.

`providers.gemini.subscription_probe` is also required. Configure its
`arguments` from the installed CLI's documented, non-inference subscription
status command. These are literal arguments to the same Agy executable; no
model, workspace or credential placeholders are substituted. The controller
runs this check under the assigned worker identity before Gemini inference
and requires exit code zero plus the configured native success contract:

- `protocol: "json-fields"` requires a nonempty `expected` object mapping
  dotted JSON field paths to exact scalar values in stdout. Every field must
  match, including its JSON type; `true` does not match `1` or `"true"`.
- `protocol: "exact-line"` requires `success_line` to match one complete
  native stdout or stderr line exactly, including case and whitespace.

Select actual fields or a native status line that positively establish
subscription authentication and billing. A version string or an absent error
does not establish that. Use metadata only; never put credential values in
this configuration. The supplied example deliberately contains rejected
`REPLACE_WITH...` values for both inference and subscription probing. No
Agy status command or status schema has been assumed or verified here.

The selected model must be the account's exact Gemini 3.1 Pro ID, as required
for synthesis. Current supported result parsers require one of these
structured native contracts:

| Protocol | Required native output |
| --- | --- |
| `gemini-json` | A JSON object with nonempty `session_id`, nonempty string `response`, no error, and exactly one `stats.models` key identifying the configured model. |
| `terminal-json` | A JSON object with `type: "result"`, `subtype: "success"`, `is_error: false`, nonempty `session_id` and string `result`, plus `model` exactly equal to the configured model. |

The response/result string must itself contain the synthesis JSON object,
including `artifact_text` and `chapter_hashes`. The controller also requires
the native process to exit successfully. Generated text claiming “I am
Gemini 3.1 Pro” is not native identity metadata and cannot satisfy the parser.

The Agy CLI command and output shape have not yet been confirmed on the user's
Windows installation. If its actual supported protocol differs, integration
remains blocked until a parser is implemented against that real contract.
Writing guessed arguments or fabricating matching metadata would not resolve
the blocker.

## Acceptance evidence to collect on the Windows host

After the daemon is healthy and all three native subscription flows and model
IDs are configured, run the automated six-chapter check as the interactive
operator. **`--run-live` submits real jobs and consumes subscription quota.**

```powershell
$PipelinePython = Join-Path $env:ProgramFiles 'CoChem\Pipeline4.2.2\.venv\Scripts\python.exe'
& $PipelinePython .\scripts\verify_pipeline_acceptance.py --config .\config\pipeline.local.json --report "$env:USERPROFILE\CoChem422\acceptance-4.2.2.json" --run-live
```

Use the actual protected installation path if overridden. The config provides
model names, port and token-file path; optional `--port` and `--token-file`
override the client connection. The verifier defaults to a two-hour timeout
and two-second polls; `--timeout` and `--poll-seconds` adjust them. Choose a new
`--report` path for each run because existing reports are not overwritten.

Require exit code zero and `status: "PASSED"`. The saved report retains the
workflow and checks six distinct chapter identities, all eight native process
receipts/subscription confirmations, the six chapter artifacts and one final
artifact, recomputed output/artifact hashes, exactly one synthesis release,
and accepted chapter overlap between two and four. It rejects explicit
test/emulator receipts. It retains the private stdout digest but does not
claim to have independently reread the controller's private stdout. On failure
it records the error and attempts to cancel unfinished work.

This script has not completed a real Windows/provider run here. Its report
does not replace the separate native ACL/Job Object/Defender/crash checks.

The [requirements matrix](REQUIREMENTS_4.2.2.md) is the detailed checklist;
the mandatory end-to-end observations are:

- One Antigravity request creates the complete planning workflow without
  repeated delegation instructions.
- Six chapters execute under a four-worker ceiling, with recorded intervals
  proving at least two accepted chapter executions overlap.
- A worker cannot read sibling slots, controller DB/WAL/SHM, watermark state
  or API token; submitting another chapter's output is rejected by the DB.
- Concurrent final chapter commits release one synthesis job, and Gemini's
  accepted result includes the independently verified hashes of every chapter.
- A controller/worker interruption at launch and commit boundaries recovers
  without accepting duplicate or stale outputs; Job Object cleanup leaves
  no running descendants.
- An event burst above 500/sec trips the SYSTEM reaper during active work;
  handles are released, database writes become usable, and recovery telemetry
  records the resulting state.
- Defender exclusions, dedicated-account subscription logins, executable
  accessibility and the native Agy result protocol work on the actual host.

Contract tests using real SQLite, threads and Python subprocesses verify those
components' local mechanics; they do not establish successful provider
inference, Windows isolation or the requested model entitlements. Preserve
that distinction when recording the final acceptance result.
