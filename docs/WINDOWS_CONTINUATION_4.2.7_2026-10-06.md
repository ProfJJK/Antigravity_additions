# Windows continuation: AETHERDESK, 4.2.7

Status: **HELD_NOT_INSTALLED**. This records the work performed after the owner
said “Proceed”, on October 6 local time / October 7 UTC. It supplements the
[first staging record](WINDOWS_STAGE_4.2.7_2026-10-06.md) and
[supervisor plan](WINDOWS_SUPERVISOR_STAGE_4.2.7_2026-10-06.md).
The [canonical handoff](WINDOWS_CODEX_HANDOFF_4.2.7.md),
[SRS](../4.2.7_SRS.md), and [owner addendum](SRS_ADDENDUM_4.2.7.md) remain
authoritative. Preparation has progressed; the pipeline is not operational.

The owner confirmed that **Agy is available, signed in and functioning on this
workstation**. Earlier conversational wording calling Agy “unavailable” was
incorrect. The outstanding verification concerns the new isolated pipeline
integration, not the health or login state of the owner's existing Agy session.

## Implemented and verified locally

- Kept the safely merged checkout and its original backup branch. Repairs remain
  reviewable working-tree changes; no push or destructive Git operation occurred.
- Preserved the initial four shared slots, Chapter 06 catalogue 2 / scoring
  algorithm 1 for all model jobs, native subscription policy, and existing
  repair ceilings. No production inference or repair attempt was submitted.
- Corrected Claude's inference settings to disable automatic flagged-content
  model switching as well as fallback models. Verified installed Codex's required
  feature gates using bounded offline capability inspection.
- Prepared an Agy NDJSON adapter matching the documented input/event format.
  `agy-stream-json` is held before authentication/probe/inference because its
  documented terminal response does not attest the actual serving model.
  A selected model in an initialization event is not execution proof. Subscription
  status and complete tool/subagent/MCP/hook isolation also remain unverified.
  Unreported effort must remain explicitly null/unverified; requested effort
  cannot be presented as observed metadata.
  See [native contract evidence](evidence/windows-2026-10-06/native-contract-followup.json)
  and [native effort requirements](NATIVE_EFFORT_4.2.7.md).
- Added an evidence-bound `integration_hold` for the pending isolated Agy
  integration. The complete routing catalogue and order remain present. The
  pipeline can validate the staged configuration and record a specific
  `integration_verification_hold`, then use normal Chapter 06 compatibility
  spillover. This does not claim a provider outage or login failure. Removing
  the hold restores full contract validation; missing, changed or mismatched
  evidence cannot release it. Before protected deployment, copy the exact
  capability evidence into protected custody and update the path while retaining
  its digest. A shape-valid proposal is not deployment acceptance.
- Added a CPU-only temperature probe and a verified-bundle integration path. The
  staged executable compiles against LibreHardwareMonitorLib 0.9.6.0. Runtime
  verification requires protected SYSTEM-owned code/dependencies, exact bundle
  hashes, no writable aliases/reparse points/hardlinks, bounded fresh output and
  direct current Intel core/package temperatures. TjMax headroom and cached
  aggregate readings cannot count as actual temperature. Missing or invalid
  readings continue to hold admission.
- Added SQLite online backup and legacy discovery helpers. Their tests include
  a real SQLite WAL writer with an uncommitted transaction; backups preserve the
  committed state and close their handles on Windows.
- Repaired a physical Windows HTTP race: rejecting an unauthorized POST while
  leaving its body unread could reset the connection before the client received
  `Unauthorized`. The server now sends the rejection with `Connection: close`
  and discards only a bounded declared body, with a one-second absolute deadline.
  It never parses or dispatches that body, and client exceptions are not
  relabeled. Real loopback tests reproduced the reset before the fix and verify
  fragmented requests, repeated exact rejections, bounded slow senders and an
  empty durable job board after the fix.
- Prepared explicit pipeline/supervisor configurations and an inert cutover
  planner with fresh roots, six planned worker identities, four shared execution
  slots and a separate repair identity. Each identity owns one chapter per
  workflow, so the canonical six-chapter acceptance requires all six; shared
  admission still limits simultaneous native/container work to four. Its
  output is a review artifact, never permission or evidence to activate.

The sensor bundle is private staging at
`C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\cpu-probe-bundle-v2`.
The official archive SHA-256 is
`086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001`.
All 45 bundle files match their manifest; `--capabilities` reports the expected
library and CPU-only behavior. This is compilation/capability evidence only:
no measurement mode, driver installation/loading or SYSTEM sensor acceptance
was performed. The current GUI's absence of the old WMI provider is why merely
installing LibreHardwareMonitor would not resolve the original sensor hold.
See [sensor staging evidence](evidence/windows-2026-10-06/cpu-probe-staging.json).

PawnIO 2.1.0, exactly matching that LibreHardwareMonitor release's embedded
installer, is downloaded in private staging and matches both the official
release digest and a valid namazso.eu Authenticode signature. The separately
verified 2.2.0 update is retained as a distinct candidate, not silently selected
or installed. Neither installer was executed. Installation and any required
reboot must be assessed in the privileged phase; registry/version presence
alone does not prove a working sensor driver. See
[dependency evidence](evidence/windows-2026-10-06/pawnio-dependency-staging.json).

## Actual preserved state and migration findings

Seven SQLite online snapshots passed full integrity checks, under private
`C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\legacy-online-snapshots-20261007`.
They cover both legacy root job boards and knowledge indexes plus the v4.2.0
`db` job board, Oracle tracking and WikiRAG databases. Separately, 6,104 selected
legacy evidence, repair, wiki and source files (506,126,075 bytes) were copied and
hash-verified under `legacy-file-preservation-20261007` in the same private root.
Original files were preserved. Credential-store and subscription-profile paths
were not selected for copying. Full manifests and payloads remain private.

These are individually consistent database snapshots and stable per-file
copies. Writers were not quiesced; there is no claim of one cross-database
cutover instant. Final cutover requires refreshed snapshots after reviewed
writer containment. These copies do not migrate state or initialize ledgers.

Discovery corrected a significant assumption: `CoChemHostWarden_V412` is the
independent Hyper-V quarantine VM MCP on port 47821. Preserve its task, VM, pipe
and checkpoint separately. The observed `auto_architect_mcp.py` process writes
the v4.2.0 root job board. Existing `COCHEM_DISABLE_SRE=1` is preserved but does
not stop that ingress. Process identities must be refreshed before intervention.

The inspected databases do not use the modern `supervisor_incidents` /
`supervisor_attempts` / component-recovery schemas. Legacy receipt counts,
duplicates and round history do not establish exact incident/day remaining
budget. Missing modern ledgers must not be interpreted as zero prior spend or
permission to create empty replacements. Historical jobs also lack the new
routing/document commitments; preserve them with explicit holds rather than
silently requeueing them as fresh work. See
[discovery](evidence/windows-2026-10-06/legacy-migration-discovery.json) and
[preservation evidence](evidence/windows-2026-10-06/legacy-preservation-followup.json).

The existing 8 GiB R: volume, blank observed label and startup ownership remain
unchanged. Existing tasks, credentials, databases, repair budgets and WSL
configuration remained intact. At that preservation checkpoint, no new accounts,
protected installation roots, production ledgers, scheduled tasks or daemons
had been created. The later protected payload copy is recorded below.

The owner's administrator capture at **2026-10-07 04:31 UTC** identifies the
actual startup task as **`\Mount_CoChem_RAMDisk`**, enabled under SYSTEM with a
boot trigger. Its recorded last run was 2026-10-05 19:07:36 -05:00, result 0.
Its existing action requests a new 8 GiB R: disk and a noninteractive NTFS quick
format after creation, without an explicit volume label. This describes the
configured boot action; no task was replayed, reboot requested, or current
volume formatted, resized or relabeled. The configured executable is the bare
name `imdisk.exe`; the capture does not establish historical executable-path
resolution. Preserve this task and its XML. The unchanged `cochem_startup.vbs`
still refers to the obsolete `CoChem_EnsureRamdisk` name and waits for
`COCHEM_RAM`; that separate legacy launch gate remains to be reconciled without
replacing the owner's actual startup task. See the
[curated administrator evidence](evidence/windows-2026-10-06/administrator-r-startup-followup.json).

The administrator capture found no PawnIO uninstall registration, service key,
or system-driver record. The inspected HWiNFO/LibreHardwareMonitor/OpenHardwareMonitor
process names were not running at that observation. A pending file-rename
restart flag was present; its contents were not exposed and no reboot was
performed. These are metadata observations, not a CPU temperature sample or
SYSTEM pipeline acceptance. A sharing-projection defect converted string-array
items into length objects. The original reports remain immutable; the linked
evidence restores the strings from the hash-matched private capture using the
corrected projection, without another administrator run.

## Current Windows evidence

The [machine-readable closeout record](evidence/windows-2026-10-06/continuation-closeout-validation.json)
binds the source, tests, package, configuration and preservation evidence at
that earlier recorded run. Its manifests remain historical; the subsequent
administrator findings, projection repair and stage metadata correction above
do not retroactively update that commitment or rerun its acceptance tests.

| Check | Actual result | Scope |
| --- | --- | --- |
| Earlier recorded regression | **1,531 passed, 66 skipped, 0 failures/errors**, 160.673 seconds in JUnit | 45 modules in ordinary-user Windows; two SYSTEM/protected-Git cases explicitly deselected; fixtures are not live native execution |
| Earlier source commitment | 242 files; no drift during that run | Historical manifest SHA-256 `e6cd9bcd85598644434a4cf0b36784fdc56d87f9ce1ffa4745b5a02c4b13439b` |
| Staged frozen wheel | 106 package files exactly match source; 115 compatible installed packages; Python 3.12.13 | User-profile staging with independent copies, not protected deployment |
| PowerShell parsing | 10 scripts, zero parse errors in Windows PowerShell 5.1 | Parser checks do not execute installers |
| Refreshed cutover planner | 242 source files checked, zero mismatches, 21 findings/holds, zero commands executed | `activation_ready: false` |
| Proposed pipeline configuration | Valid shape, four shared slots, six planned worker identities, routing policy 2, scoped Agy integration hold | Separate protected-state/SYSTEM and migration prerequisites remain |
| Administrator inspection | Owner completed read-only captures; actual SYSTEM R: startup task identified, 8 GiB preserved, PawnIO metadata absent, pending-file-rename flag present | Inspector ran as elevated ansac, not SYSTEM; no driver installation, sensor sample or deployment acceptance |

Evidence: [test result](evidence/windows-2026-10-06/continuation-closeout-result.json),
[exact command](evidence/windows-2026-10-06/continuation-closeout-command.json),
[JUnit](evidence/windows-2026-10-06/continuation-closeout-consolidated.xml),
[source manifest](evidence/windows-2026-10-06/continuation-closeout-source-manifest.json),
[wheel verification](evidence/windows-2026-10-06/staged-wheel-continuation-closeout.json),
[PowerShell](evidence/windows-2026-10-06/powershell51-parse-continuation-closeout.json),
[cutover plan](evidence/windows-2026-10-06/cutover-plan-continuation-closeout.json),
[configuration validation](evidence/windows-2026-10-06/host-configuration-continuation-closeout.json),
[privilege evidence](evidence/windows-2026-10-06/privilege-continuation-closeout.json).

Earlier Windows diagnostic failures and scoped passes remain in place; the
consolidated result does not relabel the full suite as passing. Overlapping
focused tests must not be summed. Historical Linux 2,597 passed / 35 skipped and
93 asset checks remain separate and do not establish Windows acceptance.

The intermediate `continuation-consolidated.xml` run recorded 1,468 passed,
66 skipped and two failures: the physical Windows auth reset and an obsolete
planner assertion expecting an invalid provider contract after the scoped
integration-hold feature made that configuration valid. Both were repaired.
The deterministic auth reproduction and follow-ups remain in
`auth-rejection-before.xml`, `auth-rejection-after.xml` and
`auth-rejection-focused.xml`. The broader auth diagnostic passed 73 tests but
could not construct two existing SYSTEM/protected-Git fixtures. The final
ordinary-user command explicitly deselects those two cases; it does not bypass
their production guards or claim their privileged acceptance.

The next combined capture, `continuation-final-consolidated.xml`, recorded
1,521 passed, 66 skipped, two explicitly deselected privileged fixtures and
one failure in the timeout test's immediate PID-list assertion. Investigation
used actual Windows process handles: the virtual-environment launcher and
interpreter have distinct PIDs, child termination is asynchronous, and an
exited process can remain briefly listed. Retained exception tracebacks also
kept the launcher's process handle open. The follow-up closes that handle after
confirmed exit and checks the exact interpreter's native exit within the
original bounded cleanup interval, preserving the real launcher topology.

## Remaining deployment and acceptance work

1. **Apply the administrator findings to protected staging.** Preserve the
   confirmed `\Mount_CoChem_RAMDisk` boot task and 8 GiB R:. Reconcile the stale
   legacy VBS gate, review pending-restart state and the still-uninstalled
   PawnIO dependency. Administrator discovery is complete within its recorded
   scope; protected SYSTEM temperature and adoption acceptance remain unrun.
2. **Verify the isolated native integrations.** Obtain a verified Agy subscription-status
   command, complete inference-only controls and actual serving-model/effort
   evidence. Verify Claude Extended and Agy protected effort profiles against
   real installed capabilities. Unsupported profiles retain visible holds and
   canonical spillover; never invent flags, attestations or paid API substitutes.
   The scoped Agy hold permits limited Codex/Claude staging through ordinary
   ordered spillover once independent deployment prerequisites pass. It does
   not by itself block all pipeline startup. The login helper still requires
   a reviewed isolated-account procedure: documented bare interactive `agy`
   startup is not implemented by its current nonempty-argv, redirected-stdin
   contract. Do not infer that accepting an empty argument list alone would
   complete an interactive login flow.
3. **Approve a concrete legacy budget/state mapping.** Locate authoritative
   history or explicitly establish a conservative migration policy from the
   preserved records. Map jobs, knowledge, pointers and quarantine; reconcile
   live writers; refresh backups and task XML; retain rollback provenance.
   Exact historical spend cannot be reconstructed from job counts alone.
4. **Install the protected prerequisites and stopped deployment.** Stage complete
   pinned Python/uv/native payloads and the sensor bundle; review/install its
   required PawnIO driver; attest ACLs and actual SYSTEM temperatures. Use fresh
   paths, explicit `-Slots 6` identity provisioning and `max_execution_slots: 4`
   from the reviewed plan. The installer parameter counts identities; it does
   not set the shared execution ceiling.
   Provision and authenticate isolated worker/repair accounts without replacing
   existing operator credentials. No tasks should activate while holds remain.
5. **Run SYSTEM and integration acceptance.** Check doctor, Job Objects, sibling
   denial, Docker pipe/backend isolation, adopted R: mapping, cleanup, Oracle,
   protected knowledge and Antigravity stdio integration. Then run board-routed
   preflight, six-chapter planning, physical RED/GREEN, different-provider
   reconciliation, Git integration, independent repair and failed-upgrade rollback.
6. **Finish actual-host observation.** Measure the four-worker queue at the real
   database location, test recovery/reboot without replacing R: ownership, and
   complete the 48-hour resource/handle/desktop-heap observation. Keep capacity
   at four until its acceptance and measured headroom justify a later change.

The earlier administrator discovery command has now been run successfully.
Its private `prerequisites-elevated.json` and the subsequent detailed captures
must not be overwritten. The curated evidence records their hashes and scopes;
no repeated administrator capture is required to repair the sharing projection.

Do not execute the supervisor migration or activation command arrays until their
null migration input, native contracts and remaining holds are resolved. The
separate fresh-root pipeline installation without daemon registration can be
prepared and reviewed independently: it must not migrate budgets, launch model
jobs or change R:. The `_staging` metadata is documentation, not a runtime safety
switch.

## Protected payload progress at 2026-10-07 04:51 UTC

The owner ran the reviewed copy-only helper in Administrator PowerShell. Its
returned receipt reports **3,505 files copied** into the fresh protected
`Toolchain4.2.7-windows-20261006` and `CpuSensors4.2.7-windows-20261006`
directories beneath `C:\Program Files\CoChem`. This is an actual installation
of those payload bytes, not a preview. The helper reports zero payloads executed,
tasks or accounts changed, and no driver installation. The original manifest
remains pinned to `db9e6567095326bbeaf528a400525ee047a7887b5a5479bf8ac0ba6f8b4625d0`.

An independent ordinary-user inspection subsequently checked every installed
file's length/hash and the protected ACL/reparse conditions of **3,505 files and
283 directories**, with **zero failures**. See
[installed payload verification](evidence/windows-2026-10-06/protected-payload-installed-verification.json).
Actual execution from these protected paths returned Python **3.12.13**, uv
**0.12.17**, and the CPU probe's declared LibreHardwareMonitor **0.9.6.0**
capabilities, all with exit code zero; the exact paths, hashes and output are in
[runtime capability evidence](evidence/windows-2026-10-06/protected-runtime-capabilities.json).
These ordinary-user capability commands did not measure temperature and do not
establish driver or SYSTEM acceptance.

The separate native copy package now includes the complete observed two-file
Codex release directory, Claude, Agy **1.3.1**, and its new capability evidence.
Its read-only preview and **9 passing Windows tests** are documented in
[native staging](WINDOWS_NATIVE_PAYLOAD_STAGE_4.2.7_2026-10-06.md). The owner then
completed the separate native copy; an independent 04:54 UTC inspection verified
all five installed files' hashes, sizes, held-handle identities and protected
ACLs, with zero failures. See
[installed native verification](evidence/windows-2026-10-06/protected-native-installed-verification.json).
Agy's stable owner-account status capture confirms selected-model and
quota output; the proposed integration hold still requires isolated-account
authentication, inference isolation and actual serving-model evidence. See
[Agy Windows diagnostics](AGY_STATUS_WINDOWS_4.2.7_2026-10-07.md). No owner credential
or existing native installation was replaced.

## Sensor installation and HWiNFO decision

The owner's separate administrator report
`pawnio-2.1.0-install-20261007T045610Z-7d020ed3-after.json` records PawnIO
installation exit **0**, registry version **2.1.0.0**, and a **Running** driver.
The before/after R: task XML digest is identical, and its volume metadata is
unchanged. Pending-file-rename was already true and stayed true; the other checked
restart flags stayed false. No restart was performed. These observations do not
yet establish a valid CPU sample, SYSTEM collection or worker device denial.

The owner asked why the existing HWiNFO was not used and then confirmed it is the
**Free edition**. The original concrete implementation gap was that the pipeline
had a LibreHardwareMonitor collector but no verified HWiNFO adapter; that did not
prove HWiNFO itself unavailable or unsuitable. This alternative should have been
checked and explained before selecting the extra driver. HWiNFO's
[official feature table](https://www.hwinfo.com/licenses/) documents a 12-hour
shared-memory limit for the free 64-bit edition, after which manual re-enabling
is required. Consequently it cannot provide that interface continuously during
the planned unattended 48-hour observation. The separate LHM/PawnIO collector
remains the deployment candidate; the existing HWiNFO installation and settings
are preserved. No license key was requested or read.

## Stopped installation recovery on 2026-10-07

The initial guard copied its 171 pinned source assets and completed the SYSTEM
identity/credential-target presence check. Its observer then failed with
`0x8004130B` because the short task had already finished. The original task,
protected source and receipt were retained. A separate continuation verified
that checkpoint and ran a new SYSTEM presence check before invoking the same
frozen installer. The owner returned a successful recovery result:
[administrator recovery receipt](evidence/windows-2026-10-06/stopped-install-resume-owner-receipt.json).

The protected pipeline installation now exists at
`C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006`. Independent local
metadata inspection confirms all six expected worker account names and SIDs
match `windows-layout.json`. The initial 171-file snapshot still matches its
manifest. R: remains NTFS with the observed volume size of 8,589,930,496 bytes.
No daemon was registered, no supervisor migration was invoked, and the pipeline
configuration has not yet been installed. The six identities do not change the
planned four-slot shared capacity.

The first independent code-tree custody check stopped at the legitimate
Mendeleev dependency's `elements.db` resource because the verifier rejected every
database extension. That diagnostic remains preserved; it did not report a
deployment fault or inspect database contents. The corrected verifier checks
file-handle identity/link metadata without reading that resource's contents.
The final scan at 05:31 UTC verified custody of all 15,956 entries (14,214 files)
and matched all 107 installed package source/assets plus dependency policy to the
frozen source. See
[installed source and custody](evidence/windows-2026-10-06/stopped-postinstall-verified-readonly.json).
Actual SYSTEM temperature collection, worker denial and native authentication
are separately reported checks. No Linux result is included in these counts.

## Actual SYSTEM CPU sample

The owner ran the separate one-shot `CoChem-4.2.7-CPU-Acceptance` task. Its
protected receipt reports actual SYSTEM SID `S-1-5-18`, **17 CPU sensors**, maximum
**68.0 C**, `CPU_SAMPLE_VALID`, and task exit **0**. The collector completed within
the production three-second bound, and the production parser validated its nonce
and freshness. The observed receipt SHA256 is
`355b1f3f6dd79b9162d347476f8f9541d0811d5c931e9fee7de4ba88e4ffb611`;
the copied evidence is
[SYSTEM CPU sample](evidence/windows-2026-10-06/cpu-system-acceptance-observed.json).
Independent local inspection confirms the receipt's SYSTEM owner, read-only
ordinary-user ACL, pinned collector manifest and matching installed module hashes.
This is one actual Windows sensor sample, not sustained monitoring or worker
device-denial acceptance. The task/root are preserved, and the pipeline remains
stopped. No HWiNFO setting, R: configuration or repair budget was changed.

## Protected configuration staged

The owner completed the separate config-only administrator helper. The installed
`Pipeline4.2.7-windows-20261006\pipeline.json` has SHA256
`2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6`.
Independent inspection confirms its Administrators owner, protected ACL with only
SYSTEM/Administrators write authority, six worker mappings, four shared slots,
routing policy v2, required CPU temperature collection, enabled knowledge, and
8 GiB `adopt_existing` R: policy. The exact frozen proposal changed only the Agy
integration-hold evidence path to its already verified protected copy; all holds
and routing decisions remain intact. See
[installed configuration](evidence/windows-2026-10-06/pipeline-config-installed-readonly.json).

The installed parser and **23 Windows tests** passed before the write. The helper
checked 15,963 installed entries and 3,727 protected base-Python entries, verified
the installed source revision and reported no config-staging holds. Earlier
preview evidence preserves a Windows PowerShell 5.1 native-argument quoting
failure, fixed before installation; it did not alter installed configuration.
No daemon was registered, database opened, corpus imported, repair budget migrated
or model job executed by this step. Parser validity is not activation acceptance.

## Autonomous continuation on 2026-10-07

The next owner action is documented in the
[morning handoff](WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md). A new reviewed
entrypoint prepares three sequential phases: protected private knowledge/index
acceptance, six actual worker handle-denial checks, and a fresh stopped runtime
build. Its actual read-only preview has zero holds; 25 Windows control-flow tests
passed. No phase has been applied. Existing protected helpers and installation
checkpoints remain unchanged.

The fresh runtime adds a protected workspace subtree on the adopted R: volume,
avoiding the original implementation's root-ACL replacement. It also includes
metadata-only legacy history handling and explicit unresolved repair-authority
guards. Seven preserved snapshots produced 646 non-executable historical records
with unchanged source hashes and SQLite integrity. No existing budget ledger was
opened, migrated or reset. The runtime's 166-file frozen source, configuration and
separate 30-test installer result are recorded in
[runtime preparation](evidence/windows-2026-10-06/stopped-runtime-r1-preparation.json).

Prepared human sign-in support includes a reviewed Claude interactive bridge
(44 Windows fixture tests) and six-worker native-status wrappers (89 tests).
These results do not establish actual authentication, serving model, entitlement
or effort. The owner's working Agy session remains intact; isolated inference
integration still needs its own evidence. No native model job has run.

The [current host state](../config/windows/aetherdesk-427.observed-20261007.json)
distinguishes actual installed Windows checks, prepared but unapplied work and
remaining physical acceptance. Historical Linux results remain separate.
