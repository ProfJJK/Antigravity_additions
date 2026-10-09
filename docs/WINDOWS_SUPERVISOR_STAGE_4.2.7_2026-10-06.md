# AETHERDESK supervisor and cutover staging — 2026-10-06

Status: **HELD_NOT_INSTALLED**. This supplements
[the Windows deployment record](WINDOWS_STAGE_4.2.7_2026-10-06.md) and follows
[the canonical handoff](WINDOWS_CODEX_HANDOFF_4.2.7.md),
[4.2.7 SRS](../4.2.7_SRS.md), and
[owner addendum](SRS_ADDENDUM_4.2.7.md). No installer, account provisioning,
authentication, task registration, state migration, daemon activation or model
job was executed by this staging package. Windows checks below are separate
from the earlier Linux evidence and do not establish SYSTEM acceptance.

## Proposed configuration

[aetherdesk-427.supervisor.proposed.json](../config/windows/aetherdesk-427.supervisor.proposed.json)
uses these explicit destinations:

| Purpose | Proposed path or identity |
| --- | --- |
| Independent supervisor code, interpreter and acceptance snapshot | `C:\Program Files\CoChem\Supervisor4.2.7-windows-20261006` |
| Supervisor state and repair workspace | `C:\ProgramData\CoChemSupervisor427-windows-20261006` |
| Accepted release trees | `C:\Program Files\CoChem\PipelineReleases4.2.7-windows-20261006` |
| Dedicated repair identity / existing credential-target convention | `CoChem423Repair` / `CoChem423/repair` |
| Proposed SYSTEM tasks | `CoChem-4.2.7-Warden`, `CoChem-4.2.7-Supervisor` |

The private root contains separate model and component budget ledgers, the repair
job board, quarantine marker, release journal, active-release pointer and
installation rollback evidence. Their concrete paths are recorded in `_staging`.
The repair identity differs from all six planned pipeline identities. It does not
authorize a fifth concurrent shared seat: independent repair requires the
pipeline to be stopped or contained and its hardware and Docker boundaries
attested.

The supervisor reads the protected `pipeline.reviewed.json` snapshot for Chapter
06 catalogue 2, score algorithm 1 routing. Provider model fields are catalogue
metadata, not dispatch pins. The pipeline remains at four shared execution slots.
Six isolated identities (`slot1` through `slot6`) support the canonical six-chapter
DAG because each chapter keeps its own immutable identity for that workflow.
Provisioning six identities does not permit six concurrent jobs: the
`max_execution_slots: 4` admission ceiling is shared by native work and Docker.
The Docker ceiling stays four and the two warm containers consume seats within
that ceiling. Canonical
4.2.7 repair ceilings remain two attempts per incident, four per day, and a minimum
1800-second cooldown. Historical remaining-budget authority is still unresolved;
these policy values do not reset it. `auto_deploy: true` retains the approved bounded repair
policy. The proposed configuration remains inactive because protected installation,
historical-budget migration and SYSTEM acceptance are unresolved. The `_staging`
object documents findings and is **not a runtime interlock**.

The owner confirms Agy is installed, signed in and functioning. Its isolated
pipeline integration still needs verified account checks, inference-only
isolation and dispatched-model evidence. A narrowly scoped `integration_hold`
records those missing checks and leaves the affected attempts eligible for
canonical Chapter 06 compatibility spillover. It does not establish an Agy
outage or impose a global startup hold. Native effort options alone do not
establish the required model/profile binding; Claude Extended and Agy bindings
remain unattested. No successful isolated-account check or isolation attestation
is invented. The pipeline proposal with four shared seats and six planned
identities has a valid configuration shape
with this explicit evidence-bound hold; the null integration fields do not make
the whole proposal invalid. Installation and activation still depend on the
separate protected-host, historical-budget and acceptance prerequisites.

## Read-only planner

[plan_aetherdesk_427_cutover.ps1](../scripts/plan_aetherdesk_427_cutover.ps1)
requires explicit inputs and emits structured command arrays for review. There
is no execution switch. `activation_ready` remains false even if individual
filesystem checks succeed. Its only optional write creates a new evidence file;
it refuses overwrites, alternate streams, reparse ancestors, protected code or
state roots, credential directories, legacy roots, and the adopted R: volume.

Run from the repository to inspect the plan. The Python and uv paths below are
proposed protected destinations, not claims that they have been installed:

```powershell
$repo = 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
& "$repo\scripts\plan_aetherdesk_427_cutover.ps1" `
  -StageManifest "$repo\config\windows\aetherdesk-427.stage.json" `
  -PipelineProposal "$repo\config\windows\aetherdesk-427.proposed.json" `
  -SupervisorProposal "$repo\config\windows\aetherdesk-427.supervisor.proposed.json" `
  -SourceManifest "$repo\docs\evidence\windows-2026-10-06\continuation-closeout-source-manifest.json" `
  -Python 'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe' `
  -Uv 'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\uv\uv.exe'
```

Optional `-Output` must name a new file outside preserved roots. Optional
`-PreviousSupervisorDataRoot` and `-MigrationEvidence` supply provenance; their
presence cannot certify the semantics of budget migration or writer containment.
An unresolved previous root appears as JSON null in the generated supervisor
command, making that command deliberately incomplete. Never substitute the
installer's default previous root, an arbitrary existing folder, or a fabricated
empty database.

The planner compares current source files with the supplied evidence manifest,
hashes its inputs and installer scripts, compares captured native source hashes,
and inspects protected executable paths and ACLs when present. Source drift
retains a hold; it does not silently reinterpret old passing tests as evidence
for new bytes. Full tree, hardlink, account and SYSTEM verification remains a
separate required stage. Built-in PowerShell inspection modules are imported
from the running interpreter's `$PSHOME`, avoiding an inherited PowerShell 7
module path shadowing Windows PowerShell 5.1 modules.

The command arrays distinguish four privileged phases:

1. Pipeline install/provision at explicit roots with `-Slots 6`, without daemon
   registration. This still creates SYSTEM provisioning tasks and isolated
   identities when eventually executed; it is not a read-only operation. The
   installer switch counts identities. The reviewed configuration separately
   retains `max_execution_slots: 4`, so concurrent shared capacity does not rise.
2. Independent supervisor install/provision and reviewed budget migration.
   Its own frozen environment and acceptance snapshot are separate from candidate
   pipeline code. Missing historical authority blocks this phase.
3. Explicit pipeline registration, initially stopped and disabled.
4. Final supervisor registration and activation, preserving the intended Warden
   XML and rollback evidence. This boundary enables the intended tasks and must
   follow reviewed migration, containment, native prerequisites and rollback.

All interpreter, uv, code, state, task and config paths are explicit. The frozen
installers use `uv sync --frozen --no-editable --link-mode copy`, because protected
deployment rejects alternate hardlinks into a user-writable uv cache. Stage the
complete exact Python distribution and any native payload dependencies; copying
only `python.exe` is not a runnable Python installation. Operator authentication
does not authenticate the isolated worker or repair accounts.

## Preserved state and remaining prerequisites

`CoChemHostWarden_V412` runs as `ansac` and serves the separate Hyper-V supervisory
MCP on port 47821. It is not the protected `cochem_pipeline` Warden and cannot be
adopted or replaced silently. Preserve that task, VM, pipe and checkpoint unless
independently retired. Legacy job-board writers and their databases require
separate reconciliation; a task with “Warden” in its name does not identify the
modern repair ledger. The
[legacy discovery report](evidence/windows-2026-10-06/legacy-migration-discovery.json)
records the observed `auto_architect_mcp.py` writer of the v4.2.0 root job board
and seven private SQLite snapshots. `COCHEM_DISABLE_SRE=1` alone does not quiesce
that ingress. Process identities must be refreshed before any writer disposition.

Scoped discovery has not located modern `supervisor.db` and
`component-recovery.db` authority in the inspected legacy roots. Legacy watchdog
JSON, recovery records, paid history and job databases must be mapped before
creating new supervisor state. Missing ledgers do not mean zero prior spend.
Consistent SQLite backups and original WAL/history, credential targets, profiles,
quarantine, pointers and task XML must be retained with writer disposition and
rollback provenance. This package does not perform migration.

The existing 8192 MiB R: volume and startup task are preserved. No format, resize,
recreation or startup replacement is proposed. Its existing label and startup
ownership still require reconciliation. The CPU-only LibreHardwareMonitorLib
0.9.6 provider is compiled and tested as a staged artifact. Reviewed PawnIO
installation and real SYSTEM temperature readings remain unverified, and the
required thermal guard stays enabled. Current LibreHardwareMonitor GUI
installation alone is not evidence for the former WMI namespace.

Administrator/SYSTEM work remains unrun: protected toolchain/native copies and
ACLs, account provisioning, any reviewed driver/sensor setup, SYSTEM doctor,
worker and repair containment, Docker-pipe/backend denial, RAM mapping, and
eventual task registration. Native routed planning/coding/repair/rollback,
four-worker queue observation, reboot recovery, and the 48-hour observation
remain separate acceptance stages.

## Windows evidence

`pipeline_tests/test_windows_cutover_plan.py` exercises the real Windows
PowerShell parser and planner, loads a disposable supervisor proposal through
the production validator, checks unchanged budgets and Chapter 06 routing,
verifies inert command output and stale-source holds, rejects capacity/budget/
legacy-task changes, and verifies evidence writes cannot overwrite or enter
preserved locations. The companion setup tests exercise installer argument
boundaries with captured stubs; they never execute an installer.

The initial fixture run is retained in
`docs/evidence/windows-2026-10-06/supervisor-staging-initial.xml`; it exposed the
inherited PowerShell module-path issue and a test expectation that omitted
normalization of unspecified model ceilings. The earlier native result is in
`docs/evidence/windows-2026-10-06/supervisor-staging.xml`: **24 passed** in 13.71
seconds on Windows. The original read-only planner capture is
[cutover-plan.json](evidence/windows-2026-10-06/cutover-plan.json): 203 manifest
files checked, 13 changed files retained as a stale-evidence hold, 22 total holds,
and zero commands executed. That capture predates completion of concurrent
sensor/native-contract changes and is retained as historical evidence.

The earlier follow-up capture,
[cutover-plan-followup.json](evidence/windows-2026-10-06/cutover-plan-followup.json),
checks **238 files with zero source mismatches**, retains **21 holds**, and
records **zero commands executed**. Its source authority is
[followup-source-manifest.json](evidence/windows-2026-10-06/followup-source-manifest.json),
SHA-256 `1414a1e144165e157ce85a8db343c6c1f6eb457cbc7831d972748f5c964007ed`.
The [completed consolidated Windows run](evidence/windows-2026-10-06/followup-result.json)
reported **1346 passed, 66 skipped,
zero failures/errors**, 1412 tests in 116.592 seconds with no source drift.
These are scoped Windows regression results, not full Docker/SYSTEM or
live-subscription acceptance. That capture remains `READ_ONLY_PLAN` with
`activation_ready: false`. This capture predates the subsequent narrow Agy
`integration_hold` change and planner wording clarification and is preserved as
historical evidence.

The earlier continuation capture,
[cutover-plan-continuation.json](evidence/windows-2026-10-06/cutover-plan-continuation.json),
checks **241 files with zero source mismatches**, retains **21 findings/holds**,
and records **zero commands executed**. It uses
[continuation-source-manifest.json](evidence/windows-2026-10-06/continuation-source-manifest.json),
SHA-256 `82e670a3d14d2aa19466e745eee1ff3a9c8d8f773679b150c9927d22f869cbca`.
The capture includes the scoped Agy integration hold and acknowledges the owner's
confirmation that Agy is working and signed in. It remains `READ_ONLY_PLAN`, with
`activation_ready: false`; valid configuration shape does not establish the
remaining privileged, budget-migration or native acceptance evidence. The
consolidated test result for this continuation is reported separately in the
[Windows deployment record](WINDOWS_STAGE_4.2.7_2026-10-06.md).

The most recent pre-identity-coherence capture,
[cutover-plan-continuation-final.json](evidence/windows-2026-10-06/cutover-plan-continuation-final.json),
checks **242 files with zero source mismatches**, retains **21 findings/holds**,
and records **zero commands executed**. It uses
[continuation-final-source-manifest.json](evidence/windows-2026-10-06/continuation-final-source-manifest.json),
SHA-256 `85f8039acdbbd9de1ed77a61c6defddfacb7be27b188ce1238e2960c2f7a9f0b`,
which includes the bounded unauthorized-HTTP-body repair and its native loopback
regressions. The outcome remains `READ_ONLY_PLAN` and `activation_ready: false`.
Previous captures and the earlier failing consolidated diagnostic remain
unchanged; the final ordinary-user test outcome and its two explicit
SYSTEM/protected-Git deselections are reported separately in the deployment
record.

The identity-coherence correction adds planned `slot5` and `slot6` with the
existing `CoChem422WorkerN` / `CoChem422/slotN` conventions and separate roots.
Stage metadata and planner output distinguish six planned identities from four
maximum shared execution seats. Earlier captures remain historical.
The targeted regression uses actual SQLite admission to complete six separately
owned chapter fixtures while refusing a fifth simultaneous claim and preserving
earlier chapter ownership. This is storage/admission evidence, not account
provisioning or native model acceptance.
The targeted native Windows planner/setup/configuration run passed **141 tests**
in 26.27 seconds; evidence is
[identity-capacity-coherence.xml](evidence/windows-2026-10-06/identity-capacity-coherence.xml).

The current capture is
[cutover-plan-continuation-closeout.json](evidence/windows-2026-10-06/cutover-plan-continuation-closeout.json):
**242 files, zero source mismatches, 21 findings/holds and zero commands
executed**, with six proposed identities and four shared execution slots.
It binds [the closeout source manifest](evidence/windows-2026-10-06/continuation-closeout-source-manifest.json),
SHA-256 `e6cd9bcd85598644434a4cf0b36784fdc56d87f9ce1ffa4745b5a02c4b13439b`.
Activation remains false. The latest scoped Windows test outcome is recorded
in [the continuation report](WINDOWS_CONTINUATION_4.2.7_2026-10-06.md).

The remaining findings overlap and group into deployment prerequisites and
provider-specific compatibility holds:

- Protected Python, uv, Codex, Claude and Agy payloads are absent at the proposed
  destinations; full-tree ACLs, isolated identities and protected project copying
  remain unverified.
- Agy's isolated pipeline account/isolation and model/effort bindings remain
  unverified despite the functioning user installation. Hold only affected native
  attempts and retain canonical Chapter 06 spillover; this does not independently
  prevent global startup. Claude Extended bindings also remain unverified.
- Historical repair/component budget authority, consistent migration and legacy
  writer disposition remain unresolved. Existing snapshot discovery is evidence,
  not a reviewed migration or permission to bootstrap empty ledgers.
- Existing R: startup ownership, reviewed sensor dependencies and real SYSTEM
  temperatures remain unverified.
- SYSTEM containment, Docker boundaries, native routed workloads and repair/
  rollback, four-worker queue observation, reboot, 48-hour stability and final
  task activation remain unrun.

Independent read-only staging and evidence reconciliation are complete for this
package. Further capture refreshes or contract research cannot substitute for
the unresolved migration decisions and privileged/native acceptance phases.
