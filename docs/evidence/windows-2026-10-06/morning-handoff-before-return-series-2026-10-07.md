# Windows setup handoff — 7 October 2026

## Current status and next owner step

**The published knowledge index passed actual Windows SYSTEM verification.**
The protected task exited zero with `PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED`.
It verified 137 documents, 1,708 sections and 138 corpus files, SQLite integrity,
canonical source authority, original root identity/security and the preserved
writer lock. The existing index was read-only throughout verification.

- Protected receipt SHA256: `f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b`.
- Generation: `g-abed1cd030c746559aed6bb30a47093a`.
- Index SHA256: `af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d`.
- [Owner-returned SYSTEM result](evidence/windows-2026-10-06/published-knowledge-verification-owner-result.json).

The earlier failure was a verification defect: NTFS reported the state directory
as 4,096 bytes after publication instead of its original zero bytes. Directory
write time also changed. Identity, security and writer-lock preservation passed
the subsequent independent read-only check. Do not repeat recovery, rebuild the
index or rerun the successful verifier. Preserve every prior task/root/receipt.

**The original six-worker batch stopped at slot 1. Preserve it; do not rerun it.**
The directly read protected receipt reports `DENIAL_CHECK_FAILED` at
`worker_launch`, `ResourcePolicyError`, with worker release false. The SYSTEM
sensor-device ACL check passed. Its old slot 2–6 tasks were never started. The original slot 1
root/task/receipt must remain intact. Its receipt hash is
`691c70560436f948464c529b9d2d6a3e33ce240749642af86ca79c748791461d`.

The actual resource-limit failure was reproduced with an ordinary-user anonymous
empty Job Object, without worker credentials or a child process. Topology
inspection correctly found eight efficiency logical processors (mask
`0xff0000`). Windows rejected the extended-limit request with Win32 error 87
when group affinity was set first. Setting extended limits and CPU rate before
group affinity retained every configured limit in the reproduction. The repository fix passed 49 actual Windows tests, including a live ordinary-
user process confined to the required efficiency cores and memory/process caps.
An additional 31 integration checks passed with 13 scope-specific skips.
The repaired r3 runtime is installed and its public bindings passed independent
verification. All six new SYSTEM worker receipts now report successful handle
denials and verified cleanup. The chat directly read their exact bytes and reused
the frozen child-result validator to check each nonce, SID, PID and handle result.
No worker was rerun by this archival check. The next administrator step rechecks
the six exact task definitions and terminal results before any new inspection.
See [six-worker SYSTEM evidence](evidence/windows-2026-10-06/worker-denials-r3-system-2026-10-07/summary.json).

The failed receipt does not attest cleanup. Source ordering places this specific
error before child creation; the current process inventory also found no new
Python worker. Neither observation changes the receipt's cleanup field. The
administrator installer verified that the preserved task was terminal. The
next Docker/R: inspection repeats that exact check. No prior task/process was
stopped, deleted or rerun. Keep pipeline daemons disabled.

The original worker scripts remain frozen and their prior fixture/preview results
remain historical preparation evidence. The completed r3 checks used fresh namespaces
and actual account tokens, process/Job identity checks, handle-only probes
and cleanup validation. They ran one account at a time; shared capacity
remains four. RAM and Docker acceptance are separate.

- [Native resource-policy repair and test evidence](WINDOWS_JOB_AFFINITY_FIX_4.2.7_2026-10-07.md).
- [Preserved slot 1 receipt](evidence/windows-2026-10-06/worker-denial-slot1-failed-receipt.json).
- [Read-only task/process observation](evidence/windows-2026-10-06/worker-denial-slot1-failure-metadata-observation.json).
- [Historical pre-attempt review/preview](evidence/windows-2026-10-06/six-worker-denials-ready-after-published-verification.json).

## Requested administrator step: inspect Docker and existing R:

The owner completed installation of the repaired r3 runtime. The installer
verified 15,013 custody entries and the expected 109 application assets, with
zero holds. A subsequent ordinary-user preview independently verified all 166
frozen source files, application assets, interpreter/configuration bindings and
all six fresh worker-test namespaces. It also returned zero holds.

Installation receipt SHA256:
`3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6`.
The only source delta from r2 is `resource_limits.py`; the configuration remains
byte-for-byte r2. Knowledge, credentials, databases, budgets, R: and prior
runtimes remain preserved. Do not rerun the installer.

Run once in the existing Administrator PowerShell window and return final JSON
or the first error. Do not rerun after an error or remove any partial files:

```powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -File "C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\inspect-execution-prerequisites-r3.ps1" -Apply
```

Administrator access is required to create a fresh protected SYSTEM inspection
task and read private evidence; this chat's Windows process is not elevated.
The package passed 45 Windows preparation tests and independent review. Its
ordinary preview has zero holds; private knowledge and exact prior task checks
remain administrator preconditions before its fresh root/task creation.

The inspector verifies R: backing/root/startup preservation, fresh intended state
paths, the local Docker backend/image, all six workers' API-pipe denials and an
ownership census. Only its new helper/config/report directory and inspection task
are created. It does not provision RAM, create containers or ledgers, refresh
knowledge, change existing credentials/state or start the pipeline. It has been
requested, not reported complete. Do not rerun either worker batch.

Wrapper SHA256: `5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887`.
See [frozen inspection package and tests](evidence/windows-2026-10-06/execution-prerequisites-r3-preparation-2026-10-07/preparation.json).

- [Exact installed receipt](evidence/windows-2026-10-06/stopped-runtime-r3-installed-receipt.json).
- [Independent installed bindings](evidence/windows-2026-10-06/stopped-runtime-r3-independent-bindings.json).
- [Frozen installer preparation and tests](evidence/windows-2026-10-06/stopped-runtime-r3-preparation-2026-10-07/preparation.json).

The reviewed new batch is `check-all-worker-denials-r3.ps1` (SHA256
`b14130d52b390da1d92bc982b0a17969fe3b7b4ecfed185dfaf6c4b24c35e0fc`).
Its original preview held for missing r3. The post-installation preview checked
all six fresh namespaces and returned zero holds. The 101 preparation tests covered exact runtime and
source bindings, actual temporary file copies, simulated task registration,
preserved prior-failure checks and Windows PowerShell failure reporting. They
are not SYSTEM worker acceptance. The new tasks completed sequentially, preserving
every result. All six actual receipts passed the subsequent read-only validation
described above. Do not rerun either batch or substitute the old one.
See the [frozen worker package and tests](evidence/windows-2026-10-06/worker-denials-r3-preparation-2026-10-07/preparation.json).

## Installed and preserved

The fresh r2 runtime is installed at
`C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2`.
Independent public verification passed for 166 frozen source files, 109 installed
application assets, the configuration and interpreter bindings. The installed
receipt records the full 15,013-entry custody scan. Do not reinstall it or remove
the original runtime. A later "root already exists" error was its preservation
guard, not evidence that installation had failed.

- Install receipt SHA256: `d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4`.
- Source manifest SHA256: `df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8`.
- Configuration SHA256: `135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c`.
- [Independent runtime bindings](evidence/windows-2026-10-06/stopped-runtime-r2-independent-bindings.json).

Six identities are provisioned, with four shared execution slots and all model
jobs on canonical Chapter 06 routing. No pipeline model job or daemon has been
started by deployment. Existing owner credentials, original databases, repair
budgets, the 8 GiB R: volume and `\Mount_CoChem_RAMDisk` startup task are preserved.
The only r2 configuration delta selects `R:\CoChem427-windows-20261007` as the
future scoped workspace; that subtree has not been provisioned or accepted.
An ordinary-user [read-only R: root ACL observation](evidence/windows-2026-10-06/ram-root-ordinary-acl-20261007.json)
confirmed SYSTEM ownership and no effective untrusted parent replacement rights.
Root attributes remain 22. This supports the planned protected child directory;
SYSTEM backing/startup/adoption checks are still required. No R: permissions changed.

PawnIO 2.1.0 is installed and one SYSTEM CPU sample passed with 17 sensors.
HWiNFO settings remain unchanged. One valid sample does not establish 48-hour
monitoring. Agy is installed and working for the owner; isolated authentication,
inference-only controls and actual serving-model evidence remain separate work.

## Windows evidence and preserved failures

The original knowledge attempt failed during new-index directory validation.
A separate SYSTEM diagnostic established the permissions defect. The repaired r2
runtime preserved that directory and lock. The first recovery wrapper then failed
in its PowerShell copy-list expression. A separate copyfix reached publication
and stopped at the defective directory-size comparison. Its full owner-returned
receipt reconstructed the protected Windows receipt SHA256 exactly. The separate
read-only verifier subsequently accepted the published index.

The read-only verifier had 28 Python and 22 Windows PowerShell 5.1 tests before
actual SYSTEM execution. Its tests included real ordinary-user Windows file
handles and immutable SQLite reads against a disposable candidate; these remain
separate from the owner's actual SYSTEM result above.

- [Directory-metadata investigation](evidence/windows-2026-10-06/knowledge-directory-metadata-investigation-2026-10-07/investigation.json).
- [Verifier preparation and exact pins](evidence/windows-2026-10-06/published-knowledge-wrapper-preparation.json).
- [Preserved failed-recovery receipt](evidence/windows-2026-10-06/knowledge-resume-final-verification-owner-receipt.json).
- [Failed-receipt reconciliation](evidence/windows-2026-10-06/knowledge-resume-final-verification-owner-reconciliation-windows.json).
- [Earlier handoff snapshot — historical commands must not be rerun](evidence/windows-2026-10-06/morning-handoff-before-published-knowledge-verified.md).

The machine-readable [host observation](../config/windows/aetherdesk-427.observed-20261007.json)
separates completed, held, prepared and unrun Windows work. Historical Linux test
results are not evidence of Windows acceptance.

## Remaining sequence

1. Complete the reviewed SYSTEM Docker/R: inspection, binding the installed r3
   revision, successful knowledge receipt and all six worker receipts/tasks.
2. After successful inspection, apply the separately prepared scoped R: adoption
   and empty Docker registry package. Its 44 Windows preparation tests and
   independent review passed. It creates only seven new RAM directories, missing
   exclusions for six slot paths, a new RAM ledger and an empty four-slot registry.
   SQLite may create transient WAL/SHM files within that new registry directory.
   Its preview waits for the successful inspection receipt; no provisioning ran
   and no owner command has been issued. Warm-container preparation and physical
   Docker execution remain later acceptance work.
   See [frozen foundation package and tests](evidence/windows-2026-10-06/execution-foundation-r3-preparation-2026-10-07/preparation.json).
   Preserve the R: root owner/ACL,
   attributes, volume/device identity and startup task. The old
   `initialize-execution-readiness.py` draft is disabled and must not run.
   Generic `execution_readiness(..., provision=False)` is not read-only: it can
   refresh knowledge and create or modify runtime state. Separate inspection
   from explicit provisioning.
3. Stage the disposable coding project. Its 622-byte Git bundle, seven objects
   and baseline are verified; its protected destination is not created. It has
   three ordinary files and no hooks, submodules or symlinks. The ordinary-user
   static Git custody inspection passed for 12 binary/DLL paths, 16 hardlink
   aliases and 24 ACL paths, with no unresolved non-system imports or effective
   untrusted writers. Existing Git can be retained. Privileged freshness and
   protected destination checks remain; dynamic helper behavior is not claimed.
   The fresh-only importer is prepared and independently reviewed. Its 27 Windows
   tests passed, including real disposable Git operations and cross-language
   file-identity/permission checks. Its ordinary preview binds installed r3 and
   holds only for administrator data-parent custody. No protected import ran and
   no owner command has been issued. It needs no new scheduled task.
   See [project preparation](evidence/windows-2026-10-06/protected-project-preparation-2026-10-07.json).
   See [Git custody follow-up](evidence/windows-2026-10-06/git-custody-followup-2026-10-07/summary.json).
   See [frozen importer and tests](evidence/windows-2026-10-06/disposable-project-import-preparation-2026-10-07/preparation.json).
4. Authenticate isolated native profiles through human browser/device sign-in.
   Existing owner sign-ins remain intact. An audit found that the earlier Claude
   interactive bridge and native-status helpers still bind the original runtime
   and obsolete interpreter metadata. Fresh r3 versions passed 156 Windows
   preparation tests and independent review; their three read-only previews have
   zero holds. The old 44 Claude and 89 native-status fixture passes remain
   historical evidence. These new checks used no provider, login or status calls.
   The generic Codex device-code launcher can select r3 explicitly and does
   not use the resource-limit launch path. No isolated worker is authenticated.
   The new `windows-deployment-next\HUMAN_NATIVE_LOGIN_R3.txt` guide is prepared;
   its attended phase has not been issued.
   See [frozen r3 native package and tests](evidence/windows-2026-10-06/native-r3-login-status-preparation-2026-10-07/preparation.json).
   Complete all
   six sign-ins for a provider before its status batch;
   never paste credentials, codes or tokens into chat. Resolve Agy native
   identity/inference-only and unsupported-effort contracts without treating its
   working owner installation as unavailable.
   The [read-only Agy follow-up](evidence/windows-2026-10-06/agy-contract-readonly-followup-2026-10-07.json)
   records remaining version-specific policy and serving-model evidence, including
   a subscription-only policy that disables AI-credit fallback. Unsupported
   model/profile contracts remain scoped holds with ordinary Chapter 06 spillover.
5. Reconcile legacy jobs/Oracle/history and repair-budget authority before cutover.
   Seven database snapshots remain preserved. Metadata preview maps 646 jobs:
   324 historical completions and 322 held records, with no automatic execution
   and no spend inferred from attempt counters. Complete historical source
   continuity is still unverified. Missing repair ledgers must not imply zero
   spend. Keep supervisor installation/migration held. The unresolved-authority
   guard has 114 Windows tests and two skips; it has not been applied to real
   ledgers. See [budget preservation](WINDOWS_LEGACY_BUDGET_HOLD_4.2.7_2026-10-07.md).
6. Run board-routed native preflight, planning, physical coding, different-provider
   reconciliation, Git integration and rollback on the disposable project.
7. Measure the actual four-worker queue at the real database location, perform
   controlled reboot/recovery, then complete the 48-hour resource/handle/desktop-
   heap observation. Six/eight-slot expansion requires later measurements and
   does not change the present four-slot configuration.
   The existing sustained-observation harness consumes external native desktop-
   heap used/capacity measurements; it does not collect those measurements itself.
   A collector and fresh process-bound measurement feed still need to be provided
   before a complete 48-hour acceptance run. USER/GDI counts cannot substitute.
