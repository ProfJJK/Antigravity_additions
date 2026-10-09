# Windows private directory creation repair, 2026-10-07

The first protected private-knowledge acceptance returned
`KNOWLEDGE_ACCEPTANCE_FAILED`, phase `new_index`, error type
`WindowsIsolationError`, after `index_created_new=true`. Its configuration,
helper and payload-inventory bindings passed. This receipt establishes failure;
it does not establish successful production indexing or authority acceptance.

The installed Python 3.12.13 behavior was reproduced in a separate temporary
Windows fixture. `Path.mkdir(mode=0o700)` adds an inheritable `OWNER RIGHTS`
(`S-1-3-4`) full-control ACE. Files in that directory inherit it. The pipeline's
strict private ACL validator correctly rejects that SID. Python's implementation
is explicit in [CPython 3.12.13 `os.mkdir`, lines 5024–5031](https://github.com/python/cpython/blob/v3.12.13/Modules/posixmodule.c#L5024-L5031).

The installed knowledge implementation creates its generation directory with
mode `0700`, then validates the generated database's ACL before publishing
`sources.json` or `current.json`. This is a reproduced implementation
incompatibility consistent with the actual receipt. It is not a claim that the
failed protected database was inspected. An unpublished failed generation may
already have been removed by the existing refresh error cleanup; the acceptance
root, receipt, copied corpus and created state are preserved for explicit review.

The repository repair changes only directory creation in `knowledge.py` and
`operations_policy.py`:

- On Windows, verify the effective private parent ACL and require inheritable
  SYSTEM full control for both files and directories, without inherit-only or
  no-propagate flags, before creating a directory through ACL inheritance.
- Validate the resulting directory before writing index, operations or archive
  bytes. Refuse unexpected existing ACLs; do not rewrite them.
- Retain POSIX mode `0700`. Preserve operations `create=False` behavior and
  validate each missing nested archive parent before creating its child.

The strict Windows ACL validator, protected installation, existing source/index
bytes, original databases, repair budgets, native credentials and R: were not
changed. The failed installer is not safe to blindly rerun: its targets now
exist, and recovery needs a separate reviewed step.

## Actual Windows tests

Using the existing staged Python 3.12.13 environment, the focused knowledge,
canonical-authority and operations suite completed **86 passed, 2 skipped in
6.44 seconds**. The two skips are file-symlink tests whose creation requires an
unavailable Windows privilege; separate real junction and hardlink tests passed.

The new Windows tests inspect real ACL masks, flags and inherited SIDs. They
use an explicit fixture boundary mapping the disposable directory's ordinary
test principal to SYSTEM for validation, because the test process is not
SYSTEM. Unexpected SIDs, including OWNER RIGHTS, remain unmodified. These are
actual Windows filesystem/SQLite tests, not production SYSTEM acceptance.

Coverage includes the old OWNER RIGHTS failure; inherited new state and
generation directories; a real FTS index and repeat refresh; refusal of missing,
partial, inherit-only and no-propagate SYSTEM inheritance; unchanged existing
ACLs; junction refusal; nested operations/archive directories; archive byte
preservation; and existing source-authority, HTTP/MCP and workload regressions.

The initial broader run had 65 passes and two fixture failures: unavailable
file-symlink privilege and a subprocess encoding mismatch. The tests now isolate
the privileged symlink case and explicitly request UTF-8 from the subprocess
whose output they decode as UTF-8. The intermediate knowledge-only result was
67 passed, 1 skipped. Both prior XML reports remain preserved.

Final evidence:
`docs/evidence/windows-2026-10-06/knowledge-operations-private-directory-python312-tests.xml`
(SHA-256 `bd1dde46e843429c74ac43355d73fb6c52f3f7bd078bcd6bd0ed824ce3277200`).

Source hashes reviewed for the next stopped runtime:

- `src/cochem_pipeline/knowledge.py`:
  `65e1dc17a5bee38f19e47abb24dd97864fcdcd4bc08fd28ab9199d311869d7b2`
- `src/cochem_pipeline/operations_policy.py`:
  `06bc54e218596bee4103da96a179f49a9551bae23d0380a31b0f005a53323f86`

Existing Linux results remain separate. No Linux run, SYSTEM acceptance, native
login, model execution or deployment success is asserted by this test result.

## Subsequent actual SYSTEM diagnostic

The owner returned the successful diagnostic at 13:39:49 UTC on October 7.
Task exit was zero; the protected diagnostic receipt SHA-256 is
`18ceba4394b92bb6e1d320f49226359902af0084a72195a8139f699594bc0307`.
The original failed receipt was bound to
`2cf8f1f4cafbb0537900af98349f6a3f4faec135a4061dd153f0cce78c1488dd`.
The state contains only its SYSTEM-owned directory and a one-byte `writer.lock`,
with no generation or publication pointer. Both retain SYSTEM-only full access.
No database contents were read or hashed, and no original artifact was changed.

Two empty fixtures in the separate diagnostic root now establish the behavior
under actual SYSTEM: the inherited private directory passed the installed ACL
validator, while the mode-0700 directory contained OWNER RIGHTS and was rejected.
All four managed daemon tasks were absent before and after. The original failed
task was terminal with zero instances and exit code two; it was not rerun.
See [owner-returned SYSTEM evidence](evidence/windows-2026-10-06/knowledge-diagnostic-owner-result.json).

This confirms the directory-creation incompatibility and the recoverable partial
state. It does not yet establish successful index recovery.

## Repaired runtime installed

The separate r2 code-only runtime subsequently installed successfully. Independent
ordinary-user checks at 13:49:24 UTC verified all 166 frozen source assets, all 109
installed application assets, the config/layout and protected Python 3.12.13
bindings. The installer recorded 15,013 full-tree custody entries; the independent
check repeated custody checks for the listed assets and their ancestors without
rescanning all dependencies or opening private knowledge state.

The protected installation receipt SHA-256 is
`d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4`.
The actual bindings are in [independent r2 evidence](evidence/windows-2026-10-06/stopped-runtime-r2-independent-bindings.json).
The old runtime remains preserved. A separate recovery helper must bind these
actual bytes and the SYSTEM diagnostic before creating the first index generation.
