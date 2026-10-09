# Windows deployment staging: AETHERDESK

This is Windows workstation evidence from October 6, 2026 local time (October 7
UTC), following `WINDOWS_CODEX_HANDOFF_4.2.7.md`. It is **not full deployment
acceptance**. Canonical revision remains `model-routing-2026-10-07`.

This records the first staging pass. The subsequent authorized continuation,
current test/source commitments, actual legacy snapshots, and remaining activation
holds are in [Windows continuation](WINDOWS_CONTINUATION_4.2.7_2026-10-06.md).
The earlier counts and manifests below are retained as historical evidence.

The checkout fetched and merged `origin/master` at `6894ddd`, preserving the
two local-only commits `637a1ac` and `fb5c66d`. Branch
`backup/windows-pre-427-20261006` retains the original head. The initial working
tree was clean; ignored local environments/configuration were not replaced.
Repairs are working-tree changes for review; nothing was pushed.

## Actual host and preservation

Windows 11 Pro build 26300, i7-13700K (16 physical/24 logical CPUs), approximately
64 GiB RAM, RTX 3090 with 24 GiB VRAM. Installed command inspection found Codex
0.160.0, Claude 2.1.280, Agy 1.3.0, Node 24.19.0, npm 11.17.0 and uv 0.12.17.
Exact paths/hashes are in [host inspection](evidence/windows-2026-10-06/host-inspection.json).
Operator Codex/Claude status indicated subscription login; this does not
authenticate any isolated worker. An existing API-key variable was detected by
presence only, never collected or changed. No model inference was executed.

The running `CoChemHostWarden_V412` task uses the owner's `ansac` identity and
legacy `D:\__CoChem\__agentic\v4.1.2` source, listening at loopback port 47821.
Follow-up source inspection identified it as the independent Hyper-V quarantine
VM MCP, not the new pipeline controller. Preserve it independently; its name is
not authority to stop, replace or migrate it as a 4.2.7 Warden.
Its task, the swarm/backup/token-refresh tasks, profiles, credentials, database
files, knowledge, recovery history and budgets were left intact. No second
Warden, installer, account provisioning, login or deployment switch was run.
Legacy state also exists under `D:\__CoChem\__agentic\v4.2.0`; conventional
4.2.7 pipeline/supervisor roots were absent in discovery. This is not evidence
that historical repair spend is zero. No replacement production ledger was
created. Legacy migration remains an explicit prerequisite.

Read-only ImDisk inspection found an existing **8 GiB virtual-memory-backed**
`R:` NTFS volume. It is pageable VM backing, not demonstrated AWE backing.
It was not resized, formatted, relabeled, detached or recreated. The existing
Startup launcher refers to `CoChem_EnsureRamdisk` and waits for `COCHEM_RAM`;
that task was not visible from the non-elevated session, and the observed
volume label was blank. Preserve both and reconcile through elevated read-only
discovery before reboot testing. Do not create a replacement startup task.

WSL2 2.7.11.0/Docker Desktop 4.93.0 use a Linux engine 29.8.1; configured WSL
limits were 12 processors and 41,943,040,000 bytes. Those settings and existing
images/containers were preserved. A separate image was built with the reviewed
Dockerfile and empty build context:
`sha256:d9d3e8644b6fc407c7d5ee7151fcb22470d74b8c14a4e6ef8cf8e9acb62877a2`.
[Build evidence](evidence/windows-2026-10-06/image-build.json) describes Linux
image building through Windows Docker Desktop; it does not attest the protected
Docker pipe or sandbox execution.

The [resource sample](evidence/windows-2026-10-06/resources.json) measured CPU,
physical memory, Windows commit and disk counters. Protected CPU/GPU probes
correctly held because this session is not SYSTEM. Independent read-only
`nvidia-smi` inspection observed the GPU. A separate direct WMI query returned
`0x8004100e` (invalid namespace) for `root/LibreHardwareMonitor`; HWiNFO running
does not establish compatibility with that required sensor source. No sensor
requirement was disabled.

## Repairs and evidence scope

| Finding | Working-tree repair | Acceptance still required |
| --- | --- | --- |
| A427-01 | Controller pytest observer requires an actual call-phase assertion with sealed source identity; exception prose cannot establish RED. | Complete protected native coding RED/GREEN workflow and Docker receipt verification. |
| A427-02 | Owned structured WBS survives manifest, scatter and gather; synthesis binds full accepted outputs, artifacts and coverage. Incomplete legacy captures hold explicitly without rewriting history/budgets. | Six-chapter native workflow and protected upgrade/recovery. |
| A427-03 | Normal login helper matches r2, accepts slots 1–256 and uses reviewed Agy login contracts. | Actual protected accounts and their retained subscription logins; Agy contract is still unverified. |
| A427-04 | Recovery reattests captured policy, daemon, ownership, limits, readiness and expiry; old/missing policy captures drain or hold. | Physical engine drift/restart/reboot cases under SYSTEM. |
| A427-05 | Cold operations and reaping run outside admission; reservations/publication and cleanup decisions retain short synchronized checks. FIFO, pressure and completion signals survive concurrent maintenance. | Slow real engine creation, warm handoff and native workload concurrency. |
| A427-06 | Closure rows become stale when tested source/dependency commitments are missing or changed. | Hash-bound installed release and new per-requirement native evidence. |
| A427-07 | Views separate configured and observed controller, Docker boundary and RAM evidence, with stale/unknown/drift states. | Actual installed SYSTEM/worker/repair captures. |
| A427-08 | MCP installation consumes frozen `uv.lock` with non-editable install and preserves existing JSON/environment. | Protected staged installation. |
| A427-09 | Legacy live checker exits without imports/submission; historical guide is marked retired. | Routed native preflight replaces its obsolete acceptance claim. |

Additional Windows failures were repaired: bounded atomic heartbeat
publication/read retries for Windows file sharing, and flushing the new SQLite
budget backup through a writable handle while preserving read-only source
access. Frozen installers also request `--link-mode copy`: actual staged uv
hardlinks were rejected by the protected-file verifier, so the file protection
remains intact and installation changes to independent copies. Existing budget/history preservation tests run against disposable
databases, never the owner's ledgers.

Final follow-up fixes separate new MCP chapter-request validation from executable
worker prompts, preserving the requirement for an accepted WBS before execution.
Short Windows disk-counter samples now use the high-resolution performance
counter. Test fixtures explicitly use their declared UTF-8 transport and native
absolute paths; Linux filesystem-only fixtures remain skipped on Windows.

The authoritative session results, commands, exact source/lock hashes, failures
and skips are in [Windows validation](evidence/windows-2026-10-06/validation.json).
The final consolidated Windows regression passed **848 tests with 63 explicit
skips, zero failures and zero errors** across 30 test modules in 101.511 seconds.
All 203 captured source/dependency/test files stayed unchanged during and after
the run. Seven PowerShell scripts parsed without errors. The fresh Python 3.12.13
frozen staging environment has 115 compatible packages; all 103 installed
pipeline/MCP/supervisor source/assets match the checkout byte for byte. This
user-profile environment is not an installed protected deployment.

Focused tests include real Windows PowerShell parsing, process/token/file-handle
behavior, SQLite and Git/protocol fixtures. Mocked engine/CLI protocol checks are
identified as fixtures, not physical containment or subscription acceptance.
The initial broad user-session run failed: 2,157 passed, 254 failed, 66 errors,
156 skipped. Most failures required SYSTEM/protected Git/privileged fixtures;
the run also exposed the fixed heartbeat/backup and fixture portability defects.
It occurred during repairs and is retained only as diagnostic evidence.
A later broad diagnostic (`full-final.xml`, before the final follow-up fixes)
recorded 2,280 passed, 213 failed, 64 errors and 159 skipped. Its 277 failures/errors
were classified as 262 SYSTEM/protected-Git/symlink-privilege prerequisites and
15 implementation/fixture failures. Those 15 prompted fixes covered by the final
consolidated regression; the broad suite was not rerun or relabeled passing.

The historical Linux **2,597 passed / 35 skipped** and 93 later asset checks
remain in their original evidence files. They are not Windows results and were
not rerun or altered by this session. No four-worker production queue result,
live subscription receipt, full coding/repair rollback or 48-hour acceptance is
claimed here.

## Concrete staged configuration

[Proposed configuration](../config/windows/aetherdesk-427.proposed.json) and
[staged plan](../config/windows/aetherdesk-427.stage.json) contain the actual
operator, image digest, four shared slots, protected destination paths,
unchanged Chapter 06 catalogue-2 routing and 8,192 MiB existing-R adoption.
Two warm containers are the initial target within the same four shared seats;
the Docker ceiling remains four. Worker names are planned identities, not a
claim they exist. Different-provider final reconciliation remains required.

At this earlier checkpoint the proposal intentionally failed validation while
Agy's reviewed stdin/result, subscription and inference-only contracts were
missing. The continuation adds an explicit scoped integration hold, permitting
staging validation and canonical compatibility spillover; Agy itself is
owner-confirmed working and signed in. Installed help is not
sufficient proof for disabling hidden tools/subagents/fallback or binding effort.
The [configuration check](evidence/windows-2026-10-06/host-configuration-validation.json)
records that hold; do not fill it with guessed flags or bypass validation.

Fresh protected roots are named `Pipeline4.2.7-windows-20261006`,
`Supervisor4.2.7-windows-20261006`, `Knowledge4.2.7-windows-20261006` and
`Native4.2.7-windows-20261006` beneath `C:\Program Files\CoChem`. None was
created by this session. They avoid reusing different bytes under an old version
name. Proposed persistent data is local `C:\ProgramData\CoChemPipeline427`,
but it cannot become an empty replacement for the legacy state.

A real disposable Git fixture was created under
`C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\disposable-project`,
branch `pipeline/accepted`, with its baseline recorded in the staged plan.
Before registering it, copy/recheck it under the proposed protected project
root and verify controller/worker ACLs. It is not the user's source repository.

## Staged execution procedure

1. Resolve the Agy native contracts and CPU sensor source. Stage exact pinned
   Python/uv/native binaries and required dependencies under protected roots;
   verify hashes and ancestor ownership. Preserve subscription profiles.
2. Inspect the actual legacy writer/state/recovery configuration. Identify both
   original repair and component budgets, current pointers and quarantine.
   Make SQLite-online consistent backups with evidence, export task definitions,
   drain/reconcile old work and review the migration. Pass the **actual** previous
   supervisor root explicitly; do not use the installer's 4.2.6 default or create
   a zero-budget substitute. Keep old code/state available for rollback.
3. Supply explicit `InstallRoot`, `DataRoot`, token, operator, configuration,
   **`-Slots 6`** and task identities to the protected installers, while keeping
   the reviewed configuration's **`max_execution_slots: 4`**. Follow-up review
   corrected the earlier four-identity plan: six permanent chapter owners are
   required for the six-chapter workflow; they share four execution slots.
   Do not register/start a second
   Warden. The helper default `Pipeline4.2.7-r2` must be overridden with this
   staged root. Provision six identities only after the state mapping is valid.
4. Use installed frozen Python under the repository's SYSTEM execution path:
   `python -I -m cochem_pipeline doctor --config <protected-config>`.
   Run deployment, Job Object, RAM mapping, sibling denial, Docker pipe/backend,
   process creation/cleanup and Oracle tests with their exact documented opt-ins.
   `COCHEM_WINDOWS_DEPLOYMENT_CONFIG` and `COCHEM_WINDOWS_RAMDISK_CONFIG` must
   point to the actual provisioned configuration. Keep destructive disposable-R
   cleanup and disposable-task tests opted out until their own targets exist.
5. Run per-account login through the corrected helper using explicit protected
   root and reviewed Agy contract. After non-inference preflight and migration
   checks pass, confirm legacy writers are drained and consistent state migration
   is complete. Preserve previous task definitions, code/configuration pointers
   and state for rollback. Activate only the selected protected SYSTEM Warden
   against reconciled state, preventing conflicting legacy job-writer startup.
   This does not retire the independent Hyper-V MCP task. This activation
   is still held; it was not performed in this session. Then run one bounded
   model probe **through the board**, followed by six-chapter planning and the
   disposable project's physical RED/GREEN, asymmetric reconciliation and Git
   integration. Preserve real receipt hashes and actual model/effort coverage.
6. Exercise independent bounded repair, failed-candidate rollback, process crash
   and reboot recovery; retain all charged attempts and original baselines.
7. Observe the selected migrated Warden's real four-worker workload at the actual
   local database location with the queue-launch observer. Do not introduce
   synthetic jobs or a second controller. Complete 48-hour resource, handle
   and desktop-heap observation. Only then consider measured six/eight-slot work.

No instruction above authorizes changing the owner's R: size/startup ownership,
resetting a ledger, replacing credentials, or substituting paid APIs.

Generated `build/` files remain untracked: automatic approval review rejected
their cleanup with only “blocked by policy.” No deletion retry or bypass was made.
