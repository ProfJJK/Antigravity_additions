# 4.2.4 model routing and deployment

The corrected user matrix below supersedes every earlier complexity grouping
and route ordering. Route selection belongs to the protected controller. A
model's claim about its identity or a successful prose response does not select
a route or establish that a native CLI executed it.

| Complexity | First route | Second route | Third route |
| --- | --- | --- | --- |
| 1–3 | Gemini 3.8 Flash | Claude Haiku 4.5 | GPT-6 Luna |
| 4–6 | Claude Sonnet 5.5 | GPT-6 Sol | Gemini 3.1 Pro |
| 7–9 | Claude Opus 5.5 | GPT-6 Astra, `low` effort | Gemini 3.1 Pro |
| 10 | Claude Fable 5.1 | GPT-6 Astra, `ultra` effort | None: two routes only |

This routing applies to routed planning work. The architecture's final
`SYNTHESIS` node remains Gemini 3.1 Pro, with the gather barrier and accepted
chapter hashes. The routing table does not authorize a different synthesis
provider, an extra complexity-10 route, API-key fallback, or a tight retry loop.
Synthesis uses the exact native identifier `gemini-3.1-pro`. Aliases such as a
preview suffix or other strings merely containing `3.1` and `pro` are rejected;
the controller does not silently remap an accepted alias. A different synthesis
identifier requires an explicitly reviewed policy/code change.

Each actual attempt must retain its selected provider, model and reasoning
effort together with its job/attempt identity and fencing information.
Native execution receipts must agree with that controller-owned selection.
The native executable paths remain the configured Codex, Claude and Gemini
CLIs; selecting another model does not create a separate account or supply a
missing subscription login.

## Scoring, admission and waiting

The controller calculates complexity from task content. Its transparent
heuristic starts at 1, adds points for planning work, estimated text size,
requirements, dependencies, chapter/WBS count and distinct risk categories,
then caps the result at 10. The exact thresholds are documented in
[`routing.py`](../src/cochem_pipeline/routing.py). This is scheduling metadata,
not a claim about model intelligence or an exact tokenizer count. Submitted
`score`, `tier`, provider/model or routing fields do not confer dispatch
authority; there is no user/model complexity override in the submission API.

The default policy in
[`pipeline.windows.example.json`](../config/pipeline.windows.example.json)
has one concurrent execution per provider/model/effort target, two per provider
and two per provider quota pool. These admission limits operate inside the
existing four-active-worker hardware ceiling. A model selection cannot create
additional worker identities or bypass memory/disk admission checks.
These are configured limits on concurrent reservations, not readings of an
account's remaining subscription credits.

Unavailable/busy routes advance through the configured tier. When a cycle
cannot dispatch, the durable queue records when the job is next eligible.
Default backoff begins at 30 seconds, is capped at 600 seconds and includes
20% jitter. Authentication/quota and configuration/compatibility holds default
to 300 seconds; other availability holds generally use 30 seconds. Planned
routing waits are reported as waits and do not authorize supervisor code
repair solely because the queue is waiting.

`max_attempts` limits ordinary task-error attempts. `max_dispatches` separately
limits total native dispatches. The routing policy also supports
`max_routing_cycles` and `max_routing_seconds`. A value of **0** for these
policy ceilings means no operator-supplied ceiling, not zero work; the default
availability cycle can therefore keep waiting/retrying with bounded delays.
Set explicit ceilings when a job needs a total dispatch, cycle or time limit.
This is independent of the supervisor's two-per-incident/four-per-day model
repair budget.

Reservations count as attempts/dispatches, while the ordinary failure budget
uses a separate count for task, protocol, code and expired-lease failures.
Quota, authentication, busy/backlog, provider, resource and context availability
failures do not consume that ordinary failure budget. Exhausting a configured
total dispatch cap fails the workflow; cycle/time/operator prerequisite holds
are explicit blocked states, never fabricated completion. After an unavailable
cycle's backoff, selection starts again at the preferred route.

HTTP `/submit` and MCP `pipeline_submit` accept optional `max_attempts` and
`max_dispatches`. Status exposes `node.routing` with the captured policy,
digest, score rationale, route candidates/cursor, cycle, next eligibility,
expiry and wait reason; `node.route` records the actual assignment. Read these
controller records and native receipts to distinguish queued, attempted and
completed work. A model's prose is not that evidence.

After restoring an operator prerequisite, authenticated HTTP
`/routing/resume` or MCP `pipeline_resume_routing(job_id, reason)` can reopen
an eligible blocked routing job. This does not reset its policy, score,
dispatch/failure budgets, cycles or deadlines, and cannot bypass an exhausted
original ceiling. It is separate from the supervisor's incident `resume`
command.

An enqueued workflow retains its immutable policy snapshot; an executable job
captures its score/rationale and candidates. Synthesis captures its selection
after the gather barrier. Changing configuration does not rewrite those saved
orders or reasoning-effort values, while lower current hardware/model/provider/
shared-pool admission limits still apply.

The database upgrade is additive. Existing accepted artifacts and fencing
records are retained. An already-running 4.2.3 attempt is not given fabricated
routing evidence or rescored: new routing claims wait while such an active
attempt has no reservation. Its fenced completion remains valid, or its lease
must be reaped and OS cleanup verified before the pending job captures a route.
Retaining the old installation is useful for administrator recovery; code
rollback alone is not a rollback of changed database state.
Returning to 4.2.3 requires a compatible restored database or explicitly
drained/migrated workflow state. Preserve a consistent pre-upgrade database
backup and review compatibility before selecting old code; new routing tables
and lifecycle semantics are not automatically backward-safe.

### Cleanup is part of admission

Native claims create a durable cleanup record tied to the exact job, attempt,
fencing token and worker identity before execution. Cancellation or lease
expiry can revoke database authority before a process has actually exited;
neither event is permission to reuse its capacity or workspace. An unresolved
retired/expired execution or cleanup quarantine blocks new claims until the
trusted native owner verifies process-tree/profile teardown and records that
proof. Healthy active executions still run concurrently within the limits.

An automated same-boot restart can retain the exact native Job Object and
verify its tree is empty while the task remains disabled. The protected
[cleanup.py](../src/cochem_supervisor/cleanup.py) helper then clears only the guard nonce and boot scope covered
by that receipt. A closed control record alone does not clear a scope, and
each new Warden launch receives a fresh containment nonce so an earlier proof
cannot release a later execution's guards. This is scoped physical cleanup
evidence, not a global database clear.
The protected launcher also reconciles its own scope immediately after its
actual final process wait and empty-Job check, before returning to Task
Scheduler. This permits automatic task restart after a verified controller
exit without requiring a whole-machine restart.

These records survive Warden crashes. Restarting the daemon, observing a
missing PID, waiting longer, or calling routing `resume` does not clear them.
Cleanup acknowledgement is not exposed through the public MCP/HTTP surface.
A later verified Windows boot can establish that an earlier boot's processes
are gone; legacy attempts without an original boot identity need an observed
boot witness or verified owned teardown, not invented cleanup evidence.
Use a full Windows Restart when the documented recovery path requires a new
boot identity. This pipeline admission barrier is separate from the
supervisor repair account's `repair-quarantine.json` marker.

## Requirements preserved from the four source documents

The release extends routing while retaining the existing requirements below.
The detailed clause-by-clause baseline is the
[4.2.2 requirements map](REQUIREMENTS_4.2.2.md). Its test totals are historical;
they are not relabeled as current 4.2.4 evidence.

| Primary source | Requirements retained | Implementation and evidence boundary |
| --- | --- | --- |
| [Oracle SRS/WBS](../Oracle_SRS_WBS.md), O-01–O-12 | SYSTEM velocity breaker above 500 events/sec; asynchronous reaper; payload-based hints; Defender exclusions; private cryptographic watermarks after 500ms debounce; Aho-Corasick retrieval and knapsack/reserved core budget; SQLite WAL and `busy_timeout=5000`. | `oracle.py`, `runtime.py`, `hardware_guard.py`, `windows.py`, `store.py` and the telemetry sink. Linux contracts do not establish native Defender/ACL/Job Object behavior. |
| [Planner architecture](../Pipeline_4_2_0_Architecture.md), P-01–P-11 and P-AC2–P-AC5 | One request creates the macro/manifest/chapter/synthesis DAG; atomic scatter and gather; four active workers maximum; isolated chapter ownership; unique attempt, lease and fencing token; structured outputs; one Gemini 3.1 Pro synthesis with accepted hashes; crash recovery rejects duplicate/stale completion. | Controller/store/runtime and structured MCP/API surfaces. Routing cannot bypass a lease, change sibling ownership, remove the gather barrier or mark a submitted job completed. Native concurrent inference still needs target-host evidence. |
| [Complete telemetry dossier](../full_dossier_untruncated.md), T-01–T-11 / AC1–AC6 | Exact `emit_recovery_event` signature and calling forms; exact SQLite schema; injected/owned connection lifecycle; complete Unicode JSON payload; defaults including explicit `None`; positive monotonic IDs; committed short transactions; real SQLite tests and AST constraints. | `src/cochem/warden/ladder.py` and `pipeline_tests/test_recovery_telemetry.py`. Routing is separate from the telemetry API and does not add a recovery FSM or privileged Hyper-V operations. |
| [Truncated telemetry dossier](../full_dossier.md) | Same stated API/acceptance scope; its missing middle section is supplied by the complete dossier above. | The complete dossier resolves the truncation. It does not create another independent or conflicting telemetry API. |

Older reconstructed v4 material describing 19 workers or Gemini 1.5 does not
override these supplied requirements or the corrected routing matrix. Those
historical designs are not claimed as active 4.2.4 behavior. The telemetry
dossiers' exclusions for Hyper-V, a separate escalation FSM, remote telemetry
and guest named-pipe monitoring remain exclusions.

## Native CLI evidence and remaining acceptance

The requested model IDs require verification against each actual CLI account.
Codex 0.159.0-alpha.3 accepted the exact `low` and `ultra` reasoning-effort
configuration values in an offline configuration check. That establishes local
CLI syntax acceptance only; it did not perform inference, authenticate a
subscription or establish model availability.

Windows uses native Windows Python and native CLIs with each dedicated worker's
own profile. The protected controller retains the database, completion authority
and account boundaries. Agy still needs a verified native headless command,
subscription-status command and structured result/session/model contract.
The repository's rejected Agy placeholders must be replaced with observed
values before live three-provider acceptance is possible.

Every worker identity that may receive a route needs that provider's native
subscription login. An old setup where only one synthesis identity had Gemini
access is insufficient for Flash-routed chapters. Codex/Claude login helpers
use the selected worker profile; they cannot supply an unverified Gemini login
command. Antigravity GUI authentication does not automatically authenticate
those separate Windows profiles.

Run the explicit development suites with:

```bash
python -m pytest supervisor_tests pipeline_tests mcp_tests -q
```

Current 4.2.4 totals are recorded only after the integrated run completes in
the [release notes](RELEASE_4.2.4.md). Fixture CLIs, real SQLite tests and Linux
process tests do not establish Windows isolation or live model execution.

## Protected upgrade boundary

The 4.2.3 supervisor, acceptance bootstrap and external test snapshot are
frozen deployment policy. They assume the earlier routing contract and cannot
be reused to approve 4.2.4 dynamic routing. Install a fresh protected 4.2.4
supervisor source/virtual environment and matching acceptance snapshot using
the updated Windows installer. Do not edit the installed 4.2.3 validator or
acceptance files in place, and do not ask a repair model to modify its own
supervisor, dependencies or test policy.

Worker subscription profiles, Credential Manager targets and task identities
are deployment state, not release-version aliases. Preserve the existing
identities during migration. Keep the previous protected installation and
configuration available for administrator review and recovery. Automatic code
repair is not a database/schema migration or data rollback mechanism.

The [4.2.3 supervisor guide](SUPERVISOR_4.2.3.md) remains the historical account
of its independent ledger, budgets, quarantine and crash journal. Those
safeguards remain relevant; its versioned installation commands refer to
4.2.3 and must not be mistaken for the 4.2.4 upgrade procedure.

### Existing Windows installation

Use elevated Windows PowerShell and machine-protected Python 3.12+. Wait for
active repairs to finish, then stop and disable both existing tasks before
installation. The default names remain `CoChem-4.2.2-Warden` and
`CoChem-4.2.3-Supervisor`; versioned code paths do not rename them. If process
cleanup is uncertain, perform a full Windows Restart with the tasks disabled
before continuing. A task state alone is not proof of process-tree cleanup.
The installer also inspects the scheduler's actual running-instance collection;
an enabled task or a remaining instance blocks the upgrade even if its displayed
state is Disabled.

Copy the current pipeline example to a reviewed local file. Preserve the
existing private database root, slot paths, worker identities, Credential
Manager targets, token and port; merge the new `routing` object and verify the
native CLI contracts. Do not replace the existing worker profiles. For the
default earlier deployment, the first installation command is:

```powershell
.\scripts\install_supervisor_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -OperatorName 'COMPUTER\YourUser' `
  -PipelineRoot 'C:\Program Files\CoChem\Pipeline4.2.2' `
  -PipelineConfig 'C:\Users\YourUser\pipeline-4.2.4.reviewed.json' `
  -PreviousDataRoot 'C:\ProgramData\CoChemSupervisor423'
```

Replace these example paths/account names with the actual deployment values.
The existing pipeline interpreter is retained; the new launcher selects the
reviewed 4.2.4 source. The installer validates and copies the reviewed pipeline
configuration into the fresh protected supervisor installation. It preserves
an existing different configuration and refuses to overwrite it.

| Deployment component | 4.2.4 default / preservation rule |
| --- | --- |
| Supervisor source, `.venv` and acceptance tests | New `C:\Program Files\CoChem\Supervisor4.2.4` |
| Supervisor private state / active pointer | New `C:\ProgramData\CoChemSupervisor424\private` |
| Protected release snapshots | New `C:\Program Files\CoChem\PipelineReleases424` |
| Previous supervisor state | Retained at `C:\ProgramData\CoChemSupervisor423` by default |
| Pipeline private DB / worker slots | Existing `CoChemPipeline422` locations retained |
| Worker identities / credentials | Existing `CoChem422WorkerN` / `CoChem422/slotN` retained |
| Repair identity / credential | Existing `CoChem423Repair` / `CoChem423/repair` retained |
| Warden / supervisor task names | Existing names retained; no competing versioned daemon tasks |

The SYSTEM `migrate-ledger` helper copies `supervisor.db` with SQLite backup,
preserving incident history, attempts, cooldowns and pending charges. It leaves
the old ledger untouched, accepts an identical repeat, and refuses a different
destination ledger. It also preserves repair quarantine. An unresolved old
release journal must be recovered before migration. A truly new installation
with no prior ledger records `NO_PRIOR_LEDGER`; an upgrade does not silently
reset repair budgets. The new pointer bootstraps administrator-reviewed 4.2.4
source while the previous installation and pointer remain available.
If the preserved supervisor task exists, its prior ledger must exist too;
a missing or empty mistyped source directory stops migration. Only a protected
receipt matching an earlier genuinely fresh installation permits an idempotent
`NO_PRIOR_LEDGER` retry.

Review `Supervisor4.2.4\supervisor.generated.json`, including budgets and
repair providers. Authenticate the preserved repair account only where access
is missing, using `login_supervisor_worker.ps1` (whose default installation is
now `Supervisor4.2.4`). Then rerun with the same paths and reviewed supervisor
configuration:

```powershell
.\scripts\install_supervisor_windows.ps1 `
  -Python 'C:\Program Files\Python312\python.exe' `
  -OperatorName 'COMPUTER\YourUser' `
  -PipelineRoot 'C:\Program Files\CoChem\Pipeline4.2.2' `
  -PipelineConfig 'C:\Users\YourUser\pipeline-4.2.4.reviewed.json' `
  -PreviousDataRoot 'C:\ProgramData\CoChemSupervisor423' `
  -Config 'C:\Users\YourUser\supervisor-4.2.4.reviewed.json' `
  -RegisterSupervisor
```

Registration replaces and enables the reviewed Warden and supervisor task
actions but starts no daemon or model. Perform a
full Windows Restart to activate the installation with verified boot cleanup.
The supervisor's configured automatic repair policy can then consume native
subscription quota. Inspect the native heartbeat, authenticated health, ledger
and routing status, then run live acceptance on the actual host. A copied
quarantine marker still requires administrator cleanup verification and
explicit clearing; reboot does not erase it. Preserve old code/configuration
and rollback records until the new deployment is accepted.

### First installation

`install_pipeline_windows.ps1` and `login_pipeline_worker.ps1` now default to
`C:\Program Files\CoChem\Pipeline4.2.4`. State/account defaults retain their
established names. Provision the pipeline, merge `windows-layout.json` into
the reviewed current example, and register its Warden with `-Config ...
-RegisterDaemon`. Registration leaves the task stopped and disabled. Install
the supervisor using the sequence above with `-PipelineRoot ...\Pipeline4.2.4`;
the absent prior supervisor ledger is recorded explicitly. Complete per-worker
native logins and restart Windows after supervisor registration before
target-host acceptance. The earlier pipeline guide describes the ACL/account
boundaries, but its old versioned installation paths are historical.
