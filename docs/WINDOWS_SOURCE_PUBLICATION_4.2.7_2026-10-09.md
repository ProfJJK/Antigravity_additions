# Complete Windows source publication and checkout relocation

This publication preserves the current Windows 4.2.7 implementation, tests,
host configuration proposals and deployment continuation material. It is a
source snapshot for continuing work, not a new release or completed deployment.
The owner requested that the newest complete local work be pushed before the
Google Drive checkout is removed.

The working checkout is now outside Google Drive:

```text
C:\Users\ansac\source\repos\Pipeline-Mix-Model-Concurrent
```

The remote is <https://github.com/ProfJJK/Pipeline-Mix-Model-Concurrent>, branch
`master`. The previous committed baseline was
`2144f067c78f0cd235f715a3e04df411ebe68696`. This publication also incorporates
the approved documentation handoff commit
`7c2987f4cef536f206434225ebc2519b58f15539` from PR #1.

## What was preserved

Before preparing this checkout, a private, exact-byte backup of **all 25,739
files / 642,511,502 bytes** in the old checkout was verified. A separate Git
bundle preserves all local branches, tags and history. Both are outside Google
Drive:

```text
C:\Users\ansac\source\repo-backups\Pipeline-Mix-Model-Concurrent-20261009
```

Keep that backup private. It includes ignored environments and local state and
is not a public source export. The old checkout was not reset, cleaned, deleted
or overwritten. The independent Codex continuation workspace also remains at
its original location outside Google Drive.

The new checkout imports the **86 modified tracked files** and **847 untracked
source/evidence candidates** from the preserved snapshot. Unchanged tracked
files use the existing Git tree, avoiding incidental Windows newline changes.
Four pre-existing Git links retain their recorded commits; their local contents
are preserved in the private backup, not silently replaced with new code.

The generated `build/lib` directory is retained privately instead of becoming
the source authority. Its 159 files comprise 146 byte-identical source copies
and 13 differing generated copies; those differences were not discarded from
the backup. Environments, caches, temporary fixture trees and private runtime
data are not publication material.

The additional [continuation bundle](evidence/windows-2026-10-09/continuation/README.md)
contains the Python/PowerShell helpers, tests, source packages, manifests,
reviews, configuration captures and result metadata needed by the next agent.
Its manifest preserves original and published byte hashes. Four historical XML
failure reports required removal of credential fragments captured from the
environment. Their original files remain private and unchanged; adjacent
redaction audits record the original and public hashes and unchanged test
outcomes. Credentials, production databases and provider session stores were
not included in the publication.

## Continue from this state

Start with the [dated deployment handoff](WINDOWS_AGENT_HANDOFF_4.2.7_2026-10-09.md),
then the [continuation map](evidence/windows-2026-10-09/continuation/portable-continuation-audit-20261009.md).
This publication supersedes the handoff's earlier statement that the Windows
working-tree implementation had not been uploaded; the earlier runtime and
failure observations remain dated evidence.

The installed protected Pipeline **r3** has not been replaced. The independent
held supervisor and recorder installations remain separate from it. A telemetry
leak is proven, but its proposed repair is still an isolated AST experiment.
The immediate engineering task remains the minimal source repair, meaningful
Windows validation and a reviewed fresh-runtime/lifecycle switch. Preserve four
shared slots, six identities, Chapter 06 routing, account sign-ins, knowledge,
databases, repair-budget holds and the existing 8 GiB R: startup task.

The two synthetic workflow tests already have owner approval. Preserve their
original IDs and private submission journal; do not ask for the same approval
again or automatically resubmit after an ambiguous attempt. The current failed
resource observation cannot be relabeled as a clean 48-hour acceptance window.
No model job or protected deployment action was performed by this publication.

## Paths after Google Drive removal

Historical scripts and manifests intentionally retain their original absolute
source paths and byte commitments. Do **not** search-and-replace them or run old
`RETURN_SETUP`/recovery commands. Their completed phases must not be replayed.
New continuation scripts must bind reviewed helpers and newly frozen inputs
under the new checkout path; the [bundle map](evidence/windows-2026-10-09/continuation/portable-continuation-audit-20261009.md)
lists the old-path dependencies and the current installed-versus-candidate state.

The installed r3 configuration and owner MCP configuration contain no old
checkout reference. Saved Warden/commissioning task definitions reference
Program Files; the saved R: startup definition invokes `imdisk.exe` independently
of the checkout. These are saved definition checks: ordinary live Task Scheduler
access was denied. Current protected task bindings must be checked as part of
the next privileged runtime switch. This source publication does not claim a
new live startup attestation or authorize changing the R: task.

The preserved backup and the published source allow recovery after removal of
the old checkout. Keep the backup and the Codex workspace; continue development
only in the new non-synchronized checkout. No automatic old-folder deletion is
performed.

Windows publication validation results are recorded separately in
`docs/evidence/windows-2026-10-09/source-publication/validation.json`. They must
not be interpreted as Linux results, native subscription inference or full SRS
acceptance.
