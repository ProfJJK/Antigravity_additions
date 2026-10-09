# Next protected payload stage

This copy-only step was completed by the owner in Administrator PowerShell,
independently of the unresolved supervisor budget history. All **3,505 installed
files and 283 directories** passed the subsequent hash/permission inspection:
[installed verification](evidence/windows-2026-10-06/protected-payload-installed-verification.json).
The helper does not run Python,
uv, the sensor, an installer or a provider CLI; it does not create accounts,
credentials, tasks, databases or configuration, and never touches R:.

The owner-supplied administrator evidence now identifies the existing R: startup
task as `\Mount_CoChem_RAMDisk`, running at startup as SYSTEM with last result 0.
The existing 8192 MiB Virtual Memory volume and that task remain preserved.
This evidence does not establish CPU sensor or pipeline SYSTEM acceptance.

## Exact effect and reviewed inputs

`scripts/stage_aetherdesk_427_payloads.ps1` defaults to a read-only byte/handle/ACL
preflight. Its explicit `-Apply` option requires an elevated **Windows PowerShell
5.1** session and copies 3,505 files (120,722,049 bytes) into these fresh roots:

- `C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006`: complete Python
  3.12.13 distribution and pinned uv 0.12.17. Python is copied from the concrete
  `C:\Users\ansac\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none`
  tree; the unversioned uv alias is a junction and is not copied.
- `C:\Program Files\CoChem\CpuSensors4.2.7-windows-20261006`: exact staged CPU
  probe bundle, including its 45 manifest-listed files and the manifest itself.
  Its original pinned manifest SHA256 is
  `defbb5b713e1cd81316b0d16dc16a49c37e9c170365c7699b18a47be6f24ef65`.

The complete source/destination inventory is
`config/windows/aetherdesk-427.payloads.json`, SHA256
`db9e6567095326bbeaf528a400525ee047a7887b5a5479bf8ac0ba6f8b4625d0`.
This inventory is a new Windows capture, not part of the previous consolidated
source manifest. The CPU inventory was checked against the original pinned
bundle manifest before this step was offered. Provider CLIs and evidence are
excluded: Agy changed during a separate native inspection and must be reviewed
under a new capture, without rewriting its historical evidence.

The helper rejects existing destinations, mismatched bytes, source hardlinks,
reparse paths, ambiguous relative names and untrusted protected ancestors.
It hashes all sources before creating anything, then rechecks each source using
a held read handle that blocks writes/deletion during copying. Files use exclusive
creation and flush before verification. New directories are protected before
payload bytes enter them. Their ACLs grant SYSTEM/Administrators full control and
Users read/execute; existing parent ACLs are inspected but never rewritten.
Failures preserve partial destinations for review. It never deletes or repairs
an existing target automatically. No provider profiles or credentials are read.

## Copy command executed by the owner

Run this in the owner's elevated Windows PowerShell 5.1 session after reviewing
the helper. The two hashes bind the code and the byte inventory. Without `-Apply`
the final line only verifies and prints the plan; `-Apply` is the distinct copy
operation. No restart or driver launch is included.

```powershell
$repo = 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$helper = Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1'
$inventory = Join-Path $repo 'config\windows\aetherdesk-427.payloads.json'
if ((Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b') { throw 'Reviewed staging helper changed' }
& $helper -Manifest $inventory -ManifestSha256 'db9e6567095326bbeaf528a400525ee047a7887b5a5479bf8ac0ba6f8b4625d0' -Apply
```

Capture the returned JSON. A copied result establishes byte/ACL staging only.
Actual SYSTEM sensor collection remains a separate step after reviewed PawnIO
installation, protected interpreter installation and device/worker denial checks.
The PawnIO 2.1.0 installer is the one embedded in pinned LHM 0.9.6. The owner
subsequently ran the separate reviewed `install_aetherdesk_pawnio.ps1 -Install`
wrapper. Its actual administrator report records exit 0, registration version
2.1.0.0 and a Running driver; R: task XML and volume metadata are unchanged.
PendingFileRenameOperations was already present and remained unchanged, so it
cannot be attributed to this installation. No automatic reboot occurred. Sensor
measurement and SYSTEM acceptance remain unrun.

## Provisioning is distinct from budget migration

The pipeline installer's code path **without `-RegisterDaemon`** can install a
frozen copied environment and provision six isolated identities while supervisor
history remains held. It has no supervisor-ledger migration or model-launch step.
The earlier planner's `migration-reviewed` prerequisite on this phase is cautious
staging guidance, not a runtime dependency. Shared concurrency remains four;
`-Slots 6` counts identities for immutable six-chapter ownership.

That phase is still a privileged mutation and is not performed by this helper:

- It uses the fixed SYSTEM setup task `CoChem-4.2.2-Provision`. The new stopped
  installation guard requires its absence and passes
  `-RefuseProvisionTaskOverwrite`, which omits `-Force` so a later collision also
  fails without replacement. Direct installer use without this switch retains
  its earlier behavior; use the reviewed guard for this fresh deployment.
  The intended Warden/supervisor names must be explicit and absent or disabled.
- SYSTEM provisioning preserves existing account passwords and requires matching
  SYSTEM Credential Manager entries. It never copies operator subscription
  credentials. Existing accounts without matching credentials fail rather than
  resetting passwords. Do not interpret a failure as permission to reset them.
- It grants batch logon, creates/validates the dedicated private/worker layout,
  creates or validates the controller token, adds worker Defender exclusions and
  indexing flags. Existing nonempty roots must already meet the isolation ACLs.
  Six names/credential targets may overlap an older deployment; confirm those
  identities have no live conflicting ownership before provisioning.
- Without `-RegisterDaemon`, `-Config` is not installed/validated and execution
  provisioning is not called. This phase does not create RAM mappings, run the
  sensor or configure Docker; native payload and config acceptance remain later
  gates. The reviewed proposal still controls the eventual four shared seats.

The supervisor installer **always** calls repair-identity provisioning and budget
migration, even without `-RegisterSupervisor`. Do not run it while historical
authority is unresolved, do not substitute a missing default previous root and
do not use `NO_PRIOR_LEDGER` to erase known legacy history. Configuration validity
does not authorize activation. The next stages remain: reviewed driver setup;
exact stopped pipeline install/provision after task/identity preservation checks;
isolated-account authentication and SYSTEM containment/thermal/Docker/R checks;
reviewed historical migration and supervisor setup; then bounded routed acceptance
and activation. No live model or repair work is authorized by this copy receipt.

After the protected copy receipt and the setup-task/account preservation checks,
the concrete **stopped pipeline provision** invocation is the following. Freeze
and hash the exact reviewed current source/lock before invoking it; the previous
consolidated manifest predates this follow-up work. This command has not run.

```powershell
$repo = 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
& "$repo\scripts\install_pipeline_windows.ps1" `
  -Python 'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe' `
  -Uv 'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\uv\uv.exe' `
  -OperatorName 'AETHERDESK\ansac' `
  -InstallRoot 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006' `
  -DataRoot 'C:\ProgramData\CoChemPipeline427' `
  -TokenFile 'C:\Users\ansac\CoChem427\controller.token' `
  -WardenTaskName 'CoChem-4.2.7-Warden' `
  -SupervisorTaskName 'CoChem-4.2.7-Supervisor' `
  -Slots 6
```

There is deliberately no `-RegisterDaemon`, `-RegisterSupervisor` or supervisor
installer in that command. It does not validate/install the proposed runtime
configuration. Preserve the returned `windows-layout.json` and provisioning
logs; they are installation evidence, not native-account acceptance. A failure
preserves partial protected state for inspection, not an automatic retry/reset.

Later native CLI copying requires a separate exact source inventory and fresh
destination review. Codex's observed executable directory also contains
`codex-code-mode-host.exe`; copying only `codex.exe` is not an established complete
payload. The user-installed Agy has moved from 1.3.0 to 1.3.1, so the old executable
pin/evidence cannot authorize the new bytes or claim their integration contract.
Copying any CLI must preserve user profiles, persistent credentials and the scoped
integration hold. The two-root helper cannot perform that native copy.

## Windows verification

The actual non-elevated copy preview verified all 3,505 files with zero copied or
executed payloads, zero account/task changes and `activation_ready: false`.
The focused Windows run passed **13 tests in 5.80 seconds**; its JUnit record is
`docs/evidence/windows-2026-10-06/payload-staging-tests-complete.xml`. Tests exercise
actual read-handle write exclusion, hardlink
rejection, digest drift, manifest destination escapes/duplicates, ACL construction,
default read-only behavior and non-elevated Apply rejection. They do not establish
the unrun privileged copy or SYSTEM acceptance. Earlier failed diagnostic captures
are retained separately rather than overwritten.
