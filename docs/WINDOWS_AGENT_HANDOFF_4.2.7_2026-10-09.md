# Windows deployment agent handoff — 9 October 2026

Continue the owner's pipeline deployment on **AETHERDESK**, Windows, account
`ansac`. This is a state and engineering handoff, not an SRS amendment or a
declaration of completed deployment. The immediate work is to implement and
safely deploy a confirmed telemetry memory-leak repair, then run the two already
approved synthetic workflows and finish the applicable Windows acceptance.

## Repository and authority

- Repository: <https://github.com/ProfJJK/Pipeline-Mix-Model-Concurrent>;
  verified GitHub default branch `master` and remote HEAD
  `6894dddad946f09cfa12c0a03eef9128d58c1ff2` on 2026-10-09.
- Local checkout still has its old directory name:
  `D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions`.
  Do not rename or replace it merely because GitHub was renamed.
- Local `master` HEAD is `2144f067c78f0cd235f715a3e04df411ebe68696`.
  It is three commits ahead of the last fetched `origin/master` and has extensive
  modified and untracked Windows code, tests, configuration and evidence.
  Preserve all of it. The local-only commits are `2144f067c78f0cd235f715a3e04df411ebe68696`,
  `637a1ac9b1b09cdd21b28d6bcfe8cc2134fc5e49` and
  `fb5c66d72f1b88deef9cf93bee6ddf8e2e705606`.
- This handoff publication contains selected documentation and safe evidence;
  it does **not** publish that entire working-tree implementation. A cloud
  clone alone is not the current installed Windows runtime. Obtain the actual
  local source/manifests or work on this workstation before producing an upgrade.
- Read [the canonical SRS](../4.2.7_SRS.md),
  [owner addendum](SRS_ADDENDUM_4.2.7.md),
  [original cloud handoff](WINDOWS_CODEX_HANDOFF_4.2.7.md) and
  [Windows continuation history](WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md).
  This dated record supersedes historical deployment-status statements in those
  handoffs, not canonical policy. Installed captured authority uses
  `model-routing-2026-10-07`; a package version alone is not an implementation pin.

## Owner decisions and interaction

- Six isolated chapter identities share **four execution slots maximum**.
  Dynamic hardware admission may temporarily permit fewer. Do not increase
  capacity or interpret six authenticated accounts as six simultaneous seats.
- All model work, including planning, coding, reviews and repair, uses the durable
  board and canonical **Chapter 06** routing. Use native subscription CLIs;
  paid API substitution and hidden subagent/fallback bypasses are not authorized.
- Preserve credentials and successful sign-ins, account SIDs, original databases
  and WAL-backed state, captured routes, knowledge generations, repair budgets,
  quarantines, task identities and all previous evidence.
- Preserve the existing **8 GiB ImDisk R:** volume and scheduled startup task
  **`Mount_CoChem_RAMDisk`**. Adopt its CoChem subtree; do not format, resize,
  replace or recreate the drive/task. Preserve legacy applications and services.
- Agy is installed, signed in and functioning for the owner. Its remaining
  isolated pipeline integration/model-effort contract hold is not an Agy outage.
- Existing repair spend authority is **UNKNOWN**, not zero. Paid repair and
  component recovery remain disabled. Do not manufacture fresh empty ledgers,
  erase history, migrate budgets speculatively or enable spending to pass a test.
- The owner wants minimal attendance and has rejected repeated timing
  optimization chores. Docker physical correctness passed; the separately held
  roughly 2.7-second startup observation is not the immediate deployment blocker.
  Canonical S427-HOST-006 still requires the Warden 50 MiB/5% guard,
  actual desktop-heap evidence and 48-hour handle/resource stability.
- **Owner approval already granted:** “Approved—run both tests,” covering the
  exact [scope](evidence/windows-2026-10-09/agent-handoff/LIVE_WORKFLOW_TEST_SCOPE_20261009.json)
  and [approval](evidence/windows-2026-10-09/agent-handoff/LIVE_WORKFLOW_TEST_APPROVAL_20261009.json).
  Synthetic fixture code, generated artifacts, canonical governing requirements
  and diagnostics may go to OpenAI through Codex and eligible Anthropic Claude
  subscriptions, with Chapter 06 unchanged. Exclude credentials, private database
  contents and unrelated user projects. Do not ask again for this same scope.
  This is not approval to weaken policy, use paid API repair or submit other data.
- This chat's process is **not UAC elevated**. A sandbox permission grant or
  `require_escalated` execution does not make it a Windows administrator.
  Prepare and independently review concrete scripts/tests before asking for one
  consolidated administrator series. Explain each necessary action. There is
  currently no new reviewed upgrade command ready for the owner to run.

## Last verified Windows state

Treat the following as dated evidence. Reattest native PID **and creation time**,
task instance, image/source and controller instance before any lifecycle action.
Do not act on a PID number alone.

| Component | Evidence/state |
| --- | --- |
| Protected pipeline runtime | `C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3` |
| Warden | SYSTEM PID `5788`; creation FILETIME `134359858192107395`; instance `c867588c063c4362beca10358ebf79ef`; task `CoChem-4.2.7-Warden` |
| Held supervisor observer | SYSTEM PID `24608`; creation FILETIME `134359860348047610`; activation task instance `{91BC9A32-CAE6-4CF0-A0A7-48CE0CC7FCC6}`; observation only, repair/recovery disabled |
| Resource recorder | Task `CoChem-4.2.7-ResourceObservation-20261008-r3-v4`; instance `{B83C5305-B71E-49DD-8B26-9085DFC0D5BD}`; started successfully and initial native measurements verified |
| Native authentication | Six Codex and six Claude profiles completed authentication; do not repeat login unless fresh status explicitly proves logout |
| Knowledge | Published protected index verified read-only: 137 documents, 1,708 sections, 138 corpus files, 3,747,840-byte index, integrity `ok`; generation `g-abed1cd030c746559aed6bb30a47093a` |
| Current acceptance | Both synthetic model workflows remain unsubmitted; no full SRS, completed 48-hour, desktop-heap or recovery-timing acceptance |

The resource recorder's task duration `P2DT1H` is semantically **49 hours**;
Task Scheduler normalized the original literal. The reviewed recovery passed
all task-definition checks, preserved task action/limits/triggers/ACL and existing
daemons, and requested exactly one first start. **That recovery is complete.**
Do not rerun `resume-partial-resource-observer-r3-v1.ps1` or the old owner command
still present in workspace `RETURN_SETUP.txt` / `RETURN_SETUP_CONTINUE.txt`.
Historical installer, registration, recovery and commissioning series are not
general resume commands for the now-running deployment.

At the recorded health sample on 2026-10-09, the controller reported RSS
**61.949 MiB**, private memory **245.406 MiB**, CPU **2.425%**, temperature
**62°C**, free commit **20,653.77 MiB**, no active work and empty quarantine.
Docker and knowledge were healthy; hardware allowed three seats, but the
controller guard paused admission at zero for `CONTROLLER_RSS_ABOVE_50_MIB`.
See the [safe health report](evidence/windows-2026-10-09/agent-handoff/post-recorder-activation-live-readiness-safe-20261009.json).

A later ordinary OS snapshot at **2026-10-09 15:48:13 CDT** confirmed both Python
PIDs still present. Warden RSS was then 43,937,792 bytes and private memory
260,349,952 bytes. RSS fluctuates; that lower instantaneous working set does not
remove the retained allocations, repair the code or erase the earlier violation.
The admission state above is a historical API sample: check fresh health before
submission, rather than assuming it remains paused or has recovered.

The recorder's first sample was **65.26171875 MiB** / **1.15% CPU**. Its current
window already contains a memory-budget violation and cannot certify a clean
sustained Warden budget. Keep every sample and receipt. A replacement controller
requires new native bindings and a fresh continuous observation window; do not
carry an old PID's clock or clean up failed evidence to claim acceptance.

## Confirmed blocker and unimplemented repair

`src/cochem_pipeline/resource_telemetry.py::_commit` defines a fresh
`PerformanceInformation(ctypes.Structure)` on every call and uses it in
`ctypes.POINTER(...)`. Python's global ctypes pointer cache permanently retains
each new structure/pointer type. Repository source and installed r3 source both
still have SHA256:

```text
ad5ada81f1cc3ff683d4e43c706383594a4ab256dd1f3d62675f6a8a2f4d0e8a
```

Actual isolated Windows Python 3.12.13 processes established the cause:

| 3,000 native commit probes | Retained structure types | Initial → final RSS |
| --- | --- | --- |
| Installed dynamic type | 3,000 | 24.02 → 64.54 MiB |
| In-memory hoisted structure / cached native binding | 1 | 23.84 → 23.89 MiB |

Both variants preserved native counter arithmetic and output schema; neither
started the pipeline, opened production state or changed policy. A separate
1,000-probe experiment without tracing confirmed 1,000 retained types, RSS
growth 8.086 MiB and private-memory growth 8.727 MiB. See the
[comparison](evidence/windows-2026-10-09/agent-handoff/commit-type-cache-comparison-r3-20261009.json)
and [baseline](evidence/windows-2026-10-09/agent-handoff/commit-probe-type-growth-actual-windows-20261009.json).

**No source repair, fresh r4 runtime or upgrade procedure has been implemented.**
The candidate exists only as a narrow AST transform in the
[experiment script](evidence/windows-2026-10-09/agent-handoff/compare_commit_type_cache_memory_r3.py).
Implement the identical structure once at module scope and lazily cache the
`GetPerformanceInfo` function binding. Keep fresh `info` instances, ABI fields
(`c_uint32`, ten `c_size_t` counters, three `c_uint32` counters), `cb`, existing
non-Windows behavior, error handling, schema, thresholds and native readings.
Do not cache a failed call, trim the working set, clear ctypes internals in the
live process or raise the limit to hide the leak. Meaningful tests should cover
bounded retained types across repeated native probes, Windows ABI/counters,
error/non-Windows handling and unchanged schema. The isolated result does not
prove the entire repaired Warden will meet 50 MiB under its real workload.

The [generated knowledge fixture](evidence/windows-2026-10-09/agent-handoff/knowledge-memory-generated-fixture-r3-20261009.json)
showed bounded reader/cache allocations with release on closure. It used mocked
permission checks and synthetic documents; it is not production isolation or
full acceptance. No knowledge rewrite is justified by that experiment.

## Safe engineering sequence for the receiving agent

1. Inspect Git and native running state read-only. Preserve dirty files and
   local-only commits. Fetch/update safely if needed; never reset, clean, silently
   overwrite local changes or replay already completed installation series.
2. Implement the small telemetry fix against the actual local source, add
   meaningful regression checks and retain actual Windows results separately
   from Linux. Freeze a new source/dependency manifest and use a fresh protected
   runtime root. Old r3 source, installed files and receipts remain untouched.
3. Prepare staging that can run while r3 remains active without opening databases,
   changing accounts or scheduling workers. Existing `install-stopped-runtime-r3.ps1`
   has stopped-daemon and r2→r3 pins; it cannot be reused unchanged on this host.
4. Prepare and independently review a controlled switch: preserve task XML and
   security descriptors, prove no active workflow/native worker, exclude new
   admission, take a verified SQLite backup including committed WAL, retain
   native identity/handles, stop only the attested old instance, prove physical
   exit and database-lock release, then start exactly one replacement against
   the same durable state/configuration. Preserve accounts/authentication,
   knowledge, budgets and R:. Never run two Wardens against shared state.
5. Account for actual startup behavior. It quarantines unfinished attempts and
   clears nonquarantined **ephemeral** slot/RAM workspaces. Installed
   `DockerRunner.reap_orphans` defaults `include_warm=False`; valid prepared
   unused containers may survive after reattestation, while invalid/expired/orphan
   instances may be removed. Preserve the registry and inspect reconciliation.
   Do not promise that every temporary fixture stays unchanged.
6. The task currently launches Python directly. There is no HTTP graceful-stop
   endpoint. Supervisor `stop_task` requires release-pointer/native-Job ownership
   proof absent from this commissioning path; do not invoke it blindly. Prepare
   a new lifecycle adapter and fresh commissioning attestation/output namespace.
   The original first-start helper is fresh-only. Existing held supervisor and
   recorder bind the old controller instance/source and also need explicit
   preservation and reviewed replacement/rebinding; do not restart them blindly.
7. Only after scripts, tests, preservation and failure projections are concrete,
   give the owner one reviewed Administrator PowerShell series if necessary.
   Do not ask them to perform speculative diagnostics, repeat valid logins,
   optimize Docker seconds or approve the already approved tests again.
8. Obtain fresh native resource/admission readiness for the repaired whole Warden.
   Execute the two approved workflows through the durable board using the
   submission controls below. Record Windows native process/model/effort,
   planning artifacts, physical RED/GREEN, independent review and Git outcomes.
9. Start and collect a new applicable uninterrupted 48-hour observation window
   after the controlled instance/source change; preserve old failed observations.
   Desktop-heap collection, Oracle/native teardown, actual four-worker queue
   observation and independent recovery authority/timing still need their own
   dispositions. Tests of mocks or a recorder start do not satisfy these rows.
   Keep paid repair/component recovery held until authority is actually resolved.

## Approved workflow submission custody

Original fixed workflow IDs, to preserve:

```text
windows427-r3-plan-eb1e6810a6e3ee27105b
windows427-r3-code-eb1e6810a6e3ee27105b
```

Planning is a complete six-chapter study-note SRS/WBS (import, search, spaced
review, privacy, backup, validation). Coding is addition/pytest in registered
project `windows-acceptance`, repository
`C:\ProgramData\CoChemPipeline427\projects\windows-acceptance`, branch
`pipeline/accepted`, baseline `c52a3a97eb085e6bafbbd14bd6a75f3274288530`.
Preserve the signature, prove meaningful failing tests before implementation,
cover positive/negative/zero/finite fractional inputs and integrate only accepted
fixture changes. Provider tools/hooks/delegation are disabled; generated code
runs in the controller's Docker boundary. No extra dependencies or network task.

The private local journal `C:\Users\ansac\CoChem427\live-commissioning-r3-v2`
still contained exactly these three files at the handoff inspection:

| File | SHA256 |
| --- | --- |
| `intent.json` (2,597 bytes) | `294bfc7cfc8bccd38c883600876751862c35d562409352c36c2f576b9e5159d3` |
| `invocation.lock` (empty) | Empty lock file; use the original exclusive lock protocol |
| `observation-0002.json` (397 bytes) | `2a6796e91c20fadf26602355b50213ce9883b2b65ab008d02120bf4dc2774eb2` |

No continuation fence, POST-attempt marker or workflow snapshot exists in that
inspection. Two prior automatic approval rejections occurred **before process
creation**, not after a POST. The later explicit human approval resolves that
scope issue. No synthetic model test was submitted.

Workspace frozen `run-live-commissioning-r3-v2.py` has SHA256
`cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363`;
`continue-live-prepost-r3-v1.py` has SHA256
`ecd0ff981cff06b5abdc4ac3ceae81e55a9557c7d313c1829850d1eb33053068`.
Do not simply rerun the original v2 driver: its existing-root behavior does not
authorize a new POST. The reviewed continuation requires exact original bytes
under the original lock, durable one-shot intent before readiness, and POST
markers before requests. Missing-job GETs are consistency checks, not authority
to resubmit. Any attempt marker, snapshot or ambiguity forbids automatic retry.

A replacement controller intentionally invalidates old runtime/commissioning
bindings in these frozen drivers. Prepare a reviewed successor preserving the
original request IDs, approved scope, old journal/proofs and pre-POST custody.
Do not delete the root, invent new IDs or infer absence from unreadable files.

## Evidence and paths

Local engineering workspace:

```text
C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next
```

This contains frozen installers/manifests, test XML, experiment scripts and
historical owner guides. New agents must inspect individual pins and failure
receipts rather than run the newest-looking numbered file automatically.
[`evidence-manifest.json`](evidence/windows-2026-10-09/agent-handoff/evidence-manifest.json)
pins the selected safe artifacts copied into this repository handoff. It contains
no credential material, private document text or production database files.

Important authoritative installed receipt pins (verify locally before use):

| Receipt | SHA256 |
| --- | --- |
| Warden `WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json` | `4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3` |
| r3 configuration | `135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c` |
| r3 install receipt | `3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6` |
| r3 source manifest | `6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1` |
| r3 installed revision | `309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4` |
| Knowledge index | `af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d` |
| Resource `observer-started.json` | `1477eeb23e38e79c9fe701f1c973e6726dd2d107bac0013c31da73238ee3d01a` |
| Resource recovery `recovery-complete.json` | `9686c4a47243543931886dcc43224637e553f1fd5cebc954c28162fc1b30a4a0` |
| Held supervisor `activation.json` | `5599e920e8b457e6389e5d7677a4eca6e5479e8dc72bba2f41ccb5d46e640171` |
| Held observer recovery `recovery-complete.json` | `20c2e18bf33409bed4813faec3f4917b000917b032dbb39113f00a6c9a216c4a` |

All roots above are under `C:\Program Files\CoChem`. Resource code root is
`ResourceObservation4.2.7-windows-20261008-r3-v4`; state root is
`ResourceObservationState4.2.7-windows-20261008-r3-v4`. Held supervisor root is
`SupervisorObservation4.2.7-windows-20261007-r3-v1`; recovery roots are
`ResourceObserverRecovery4.2.7-windows-20261009-r3-v1` and
`HeldObserverRecovery4.2.7-windows-20261008-r3-v1`.

Previous Linux results remain **2,597 passed / 35 Windows-only skipped**, plus
93 later asset/governance checks, within their original recorded scope. They do
not certify this Windows host. Existing Windows SYSTEM temperature, containment,
foundation, protected knowledge, native authentication and physical Docker
results are their own evidence; do not relabel source/mocked tests as live
subscription inference, uninterrupted monitoring or full SRS acceptance.
