# Windows continuation for the morning

**The original `continue-stopped-setup.ps1` command stopped at private knowledge
acceptance. Do not rerun that command.** The owner's protected report
records a Windows permission error during index initialization. The original
corpus, task, receipt and partial state must remain in place. The separate SYSTEM
diagnostic has now passed; later phases of the original command have not run.
See [reported failure](evidence/windows-2026-10-06/private-knowledge-failure-owner-receipt.json).

The source [directory-creation repair](WINDOWS_PRIVATE_DIRECTORY_FIX_4.2.7_2026-10-07.md)
passed 86 Windows tests with two privilege-related skips. The actual SYSTEM
diagnostic reproduced the defect: ordinary inherited permissions pass, while
Python's `0700` directory includes OWNER RIGHTS and fails the strict validator.
Only the original new state directory and its one-byte writer lock remain; no
index generation or publication file exists. The diagnostic and original failed
task/receipt are preserved. See [actual diagnostic result](evidence/windows-2026-10-06/knowledge-diagnostic-owner-result.json).

The fresh r2 runtime now has a successful protected installation receipt at
`C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\install-after.json`.
Independent checks passed for all 166 frozen source files, 109 installed assets,
configuration and interpreter bindings. The installed receipt is SHA256
`d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4`.
A separate state-specific recovery helper is now reviewed and ready. Do not rerun
the diagnostic, original continuation or r2 installer. Recovery must use the
repaired runtime, reuse the protected corpus,
and verify the exact preserved state inside the real writer lock before creating
the first index generation. The details below describe the preserved original
attempt and are not rerun instructions.

The owner's later r2 installer invocation reported "Fresh revision root already
exists." That is its preservation guard: the protected success receipt and all
independent installed-byte checks above had already passed. Keep that installation;
no replacement or rerun is needed.

The pipeline is installed and configured, but remains stopped. Six isolated
worker identities exist and shared capacity remains four. No model job has run.
The original R: drive/startup task, sign-ins, databases and repair budgets are
preserved. Actual Windows evidence is recorded separately from the earlier Linux
suite in [current host state](../config/windows/aetherdesk-427.observed-20261007.json).

## Current status: knowledge reached final verification

**The copyfix command below has run. Do not rerun it or rebuild the index.**
The copy stage completed and the protected SYSTEM task returned
`KNOWLEDGE_RESUME_HELD`, `final_verification`, `ValueError`. Its receipt SHA256 is
`8cdaebaaf9338046ef928368160f9ea7eff792c22d0f46a3a23820a78c94e66b`.
The owner returned the full receipt using read-only `Get-Content`. It records
137 documents, 1,708 sections, SQLite integrity `ok`, preserved source bytes and
canonical authority matching the capture. The published generation is
`g-abed1cd030c746559aed6bb30a47093a`; its index SHA256 is
`af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d`.
No worker checks or daemon activation followed this failure.

A disposable Windows reproduction found that the directory's reported byte size
changes from 0 to 4096 after publication. The frozen helper wrongly compares that
value as immutable. Its exact comparator fails with otherwise unchanged root
identity/permissions and writer lock. This explains a likely failure cause;
actual production metadata still needs verification. A separate read-only
validator is prepared to check the existing index, exact source hashes,
authority, private permissions and preserved lock without refreshing or writing
the index. Keep all earlier roots, tasks and receipts unchanged.

The returned JSON reconstructs the original Windows receipt SHA256 exactly
and passes the frozen verifier input check. The requested owner step is a
separate read-only verification of the existing published index. Run once in the existing
Administrator PowerShell window and return final JSON or error:

```powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -File "C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\verify-published-knowledge.ps1" -Apply
```

This creates one new private helper folder and on-demand SYSTEM task/receipt.
It never rebuilds, refreshes or repairs the existing index. Administrator access
is needed to create the protected task and inspect SYSTEM-only knowledge state;
the chat's Windows process is not elevated. Keep all artifacts if it fails and
do not rerun it. The new verifier has not been applied. Its Python helper is SHA256
`9f3b6c00255d6e5c9bc0a6e6bec546ae5a60a8287c2b24a65a2f675f6d3e0496`;
its PowerShell wrapper is SHA256
`7a6f64ec9502a042c416c6d7706ffb62d755997139b91f87cb48ebfcb1ea82f5`.
Twenty-eight Python tests and twenty-two Windows PowerShell 5.1 tests passed.
The tests exercise real read-only Windows handles and immutable SQLite access
to a disposable candidate, plus the wrapper's copy/control path with simulated
task registration. Independent reviews found no remaining concrete defect.
These are preparation results, not SYSTEM verification of the production index.
The ordinary-user preview returned no holds and explicitly deferred private
receipt/task checks to the administrator.

See [directory-metadata investigation](evidence/windows-2026-10-06/knowledge-directory-metadata-investigation-2026-10-07/investigation.json).
The [wrapper preparation](evidence/windows-2026-10-06/published-knowledge-wrapper-preparation.json)
records exact source, test and preview hashes. No worker check or daemon
activation is included in this step.

## Completed copyfix attempt (held at final verification; do not rerun)

The copy-list error was fixed in this separate reviewed wrapper. Its reported
final-verification failure is recorded above; this command is retained for history:

```powershell
powershell.exe -NoProfile -File "C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\recover-copy-stage-and-check-workers.ps1" -Apply
```

Entrypoint SHA256:
`131e10b4f43744c595b3123971aed8b2e2c6887e912fa07d584cada94466a877`.
Copyfix wrapper SHA256:
`71b4e2474a6802c36c8c9cebb2330449bd5e776fa2de3b5d293f333c1528404f`.
The failed wrapper and original Python helper remain unchanged. This continuation
requires the already-created helper root to retain its exact observed timestamps,
private ACL with inheritance, zero children and no existing task. It never deletes
or recreates that root. All three inputs are verified before the first copy.
Knowledge recovery must succeed before any of the six worker checks runs.

Fourteen actual PowerShell 5.1 tests passed, including the three-file copy loop
and the full Apply control path through simulated task registration. Twenty-two
sequence tests, independent review and the actual read-only preview also passed.
The subsequent owner result establishes that the copyfix reached the SYSTEM
helper's final verification. Complete index acceptance and worker checks remain
pending. Earlier tests missed the failed copy-loop expression; their results must
not be read as proof that this privileged path had run successfully.

This attempt is complete and must not be rerun. Four shared slots remain;
R:, its startup task, credentials and repair budgets are unchanged. Pipeline
daemons remain disabled. See [new sequence preparation](evidence/windows-2026-10-06/recover-copy-stage-and-check-workers-preparation.json).

## Failed copy-stage attempt (preserved; do not rerun)

**The command below was attempted and failed before copying its first helper.
Do not rerun it.** Windows PowerShell flattened its first source/hash pair into
strings, producing `Get-Item` path `C` at line 135. This was reproduced in actual
PowerShell 5.1. The protected root was created at 14:08:15.577697 UTC. At that
earlier observation, no recovery or worker-test task existed and all managed
daemons were absent. The
non-administrator chat cannot enumerate that private root. A new continuation
must verify its exact creation/write timestamps, private ACL and empty contents
before copying files. Existing scripts, root and prior evidence are preserved.

The subsequent copyfix attempt is recorded above; neither command may be rerun.

Preserved failed command:

```powershell
powershell.exe -NoProfile -File "C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\resume-knowledge-and-check-workers.ps1" -Apply
```

This entrypoint is SHA256
`e51f9c94d241eae4708b8f9d556f7fa09a98ed877c58787c67debd136eb4f2b9`.
It checks both phases first, then runs the reviewed r2 index recovery and only
after complete success runs the six sequential host-boundary checks. Recovery
passed 36 Windows tests, including real byte locking and unchanged lock metadata;
the combined entrypoint passed 20 control-flow tests. Independent reviews and
actual read-only previews passed. These are preparation checks, not completed
SYSTEM index or worker acceptance. Windows evidence remains separate from Linux.

Return final JSON or the displayed error and failure metadata. Keep every
existing and new task/root/receipt; do not rerun after a failure. The production
indexer may clean up its own unpublished failed generation, but recovery never
replaces the pre-existing corpus, state root, writer lock or earlier receipts.
It performs no model work, RAM changes, credential changes, budget migration or
pipeline activation. Administrator access is needed for SYSTEM task registration
and execution; the chat's process is not elevated.

## Original administrator step (preserved for history; do not rerun)

Open Start, type **Windows PowerShell**, choose **Run as administrator**, and
accept UAC under the `ansac` account. Paste this as **one physical line**:

```powershell
powershell.exe -NoProfile -File "C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\continue-stopped-setup.ps1" -Apply
```

The reviewed entrypoint is SHA256
`0f536acb274b194b8bd28e36d44c8c4e379a12103a184372433800a55f2e287b`.
Its actual read-only preview returned zero holds for all three phases. Twenty-five
Windows control-flow tests passed, and an independent source review found no
remaining concrete defect. These checks do not establish privileged acceptance.
The previously reviewed two-phase helper remains unchanged inside this entrypoint.

The command checks every phase before changing anything, then:

1. Copies the 137-document private knowledge candidate into a new protected
   corpus and asks SYSTEM to create and verify a new index. The corpus contains
   the 11 current public documents plus 126 exact preserved historical captures.
   It does not overwrite or import an old database. SYSTEM verifies actual
   permissions, source pins and current canonical authority. Some earlier raw
   source versions could not be recovered, so complete historical continuity
   remains explicitly unverified.
2. Runs six sequential worker checks using the actual account tokens, profiles,
   Job Objects and cleanup paths. They test private/sibling/controller-token and
   sensor-device access using handle-only probes. They do not issue device IOCTLs,
   read credentials or run native model providers. These are still unrun tests.
3. Builds the repaired runtime in a fresh protected directory,
   `C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r1`, using 166 frozen
   source assets and the dependency lock. It verifies the installed revision and
   configuration. The only configuration change selects
   `R:\CoChem427-windows-20261007` as the future protected workspace subtree;
   this command does not create that subtree or change the R: root. The old
   runtime remains preserved. No daemon task is registered or selected.

Wait for the final JSON and return it to this chat. If anything fails, keep the
error and all new files/tasks. **Do not rerun or delete partial artifacts.** The
helpers deliberately refuse to overwrite them; recovery must use the recorded
checkpoint. Do not restart Windows or run the old installer commands yet.

Administrator interaction is required because the chat's actual Windows process
is not elevated. Filesystem access alone does not grant a SYSTEM token or allow
it to accept a UAC prompt.

## Work completed independently

The protected configuration was independently rechecked after the owner's last
administrator step. Its six identities, four shared slots, Chapter 06 routing,
required CPU monitoring, knowledge policy and exact native evidence paths match
the staged proposal. The actual SYSTEM CPU sample already passed with 17 sensors;
it is one sample, not the sustained monitoring requirement.

The private corpus candidate passed staging validation (137 documents and 1,708
sections). The privileged copy/index helper has separate runtime, path, ACL,
source-hash and task-completion checks. No protected corpus/index was created
while the owner was away.

A new metadata-only [legacy preview](../src/cochem_pipeline/legacy_history.py)
verified all seven preserved snapshot hashes and SQLite integrity. It gives each
of 646 historical job records an unambiguous source namespace: 324 historical
completions and 322 held records. No old job became executable and no budget was
inferred from an attempt counter. Private payloads/rules were not selected into
the map. See [sanitized Windows evidence](evidence/windows-2026-10-06/legacy-held-preview-summary.json).

Read-only inspection found a compatibility issue with the installed RAM
adoption implementation: it would replace the R: root ACL and remove ordinary
user access. Repository repair work adds a dedicated protected subtree on the
same volume. **Do not run the installed RAM/execution provisioner until that
revised runtime and its reviewed subtree configuration are installed.** The
morning command above does not provision or change R:.

## Remaining sequence

After the current recovery command, return the final report for review. Its actual SYSTEM
receipts and newly installed runtime must be bound into the next acceptance
helper before Docker/backend denial, scoped R: adoption and disposable-project
execution checks. The earlier `initialize-execution-readiness.py` draft is
deliberately disabled and refers to the old runtime; do not execute it.

The preserved six-worker denial package still targets the original stopped
runtime and configuration. Its tested Windows isolation implementation and host
bindings are unchanged in r2; the only configuration delta is the unused RAM
workspace field. Its new read-only preview has no holds. These future results
will attest the six actual host/device boundaries in that stated scope; separate
current-runtime RAM and Docker checks remain required. See [continuity evidence](evidence/windows-2026-10-06/worker-boundary-r2-continuity.json).

The disposable project's 622-byte Git bundle and seven contained objects are
verified against the baseline commit. It contains three ordinary files and no
hooks, submodules or symlinks. Its protected destination has not been created.
The current user cannot inspect the destination parent's ACL, and full installed
Git dependency/hardlink custody is still unverified. No project installer has
been issued; see [project preparation](evidence/windows-2026-10-06/protected-project-preparation-2026-10-07.json).

Isolated native profiles require human browser/device sign-in, one profile and
provider at a time. Existing owner sign-ins remain intact. Codex supports the
device-code flow. A new Claude interactive bridge is prepared for its browser
callback or masked one-line code entry; 44 Windows fixture tests and its read-only
preview passed. The separate native-status package passed 89 fixture tests.
Neither package has authenticated a worker yet. The exact attended commands are
in `windows-deployment-next\HUMAN_NATIVE_LOGIN.txt` in this chat's workspace.
Complete all six sign-ins for a provider before its one-shot status batch.
Never paste credentials, codes or tokens into this chat. Agy is installed and
works for the owner; its isolated inference-only integration and actual
serving-model evidence remain separate
compatibility work with ordinary Chapter 06 spillover.

Preserved legacy jobs/Oracle records require explicit continuation mapping and a
consistent cutover capture before resumption. Repair-budget authority is still
unresolved; keep the supervisor unregistered and do not run its migration
installer. Missing historical ledgers must not become a zero-spend assertion.
The repository now has an explicit unresolved-authority guard with 114 passing
Windows tests and two skips, including direct ledger-write protections. It has
not been inserted into a real repair ledger and does not resolve historical
spend. See [budget preservation](WINDOWS_LEGACY_BUDGET_HOLD_4.2.7_2026-10-07.md).

Only after those checks can board-routed native preflight, planning/coding,
different-provider reconciliation, integration and rollback run on the disposable
project. Actual four-worker queue measurement, controlled reboot/recovery and the
48-hour observation are still required. None is established by Linux test results
or by the offline Windows helper tests.
