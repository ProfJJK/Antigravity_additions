# Windows continuation bundle and Google Drive deletion audit

This is a read-only source and path audit captured on 2026-10-09. No frozen files,
tasks, runtime, authentication, databases, repair budgets or R: settings changed.
The target working checkout is `C:\Users\ansac\source\repos\Pipeline-Mix-Model-Concurrent`.

**Do not delete the old checkout yet.** Preserve its Git history, local-only
commits, dirty/untracked implementation and ignored installation inputs outside
Google Drive first. Verify the new clone and published commit independently.
Then prepare additive successors for dependencies that actually execute from
the old path. Historical evidence may keep its original provenance paths.
Do not replace strings in frozen source files or pinned manifests.

## Bundle inventory

`portable-continuation-inventory-20261009.json` is a relative-path/size/SHA-256
inventory of 934 candidate source, test, configuration and evidence files,
32,507,294 bytes. It records 223 files with old repository references, including
line numbers and referenced path strings. Its SHA-256 is
`3c1e9d2d0f7d22fe5575e64494d6851fce530b7cd31f39d477a8c0b879c26222`.
The file count includes curated snapshots of earlier frozen versions. They are
important for lineage and recovery audits; they are not a sequence to rerun.

The ordinary Git index query was denied, so this inventory does not certify
which candidates have already been published. The publisher must compare its
chosen paths and bytes against the new checkout and Git index. Literal scanning
is a conservative filter, not complete privacy approval.

Exclude these historical failed-test reports until a publication reviewer has
resolved the detected apparent secret literals; this audit never printed them:

- `execution-prerequisites-r3-tests-initial.xml`
- `first-start-reboot-r3-v5-tests.xml`
- `worker-native-status-draft-tests.xml`

The inventory excludes test fixture/temp trees, Python/pytest caches, raw
stderr/stdout/logs, the nested Git publication checkout and raw GitHub responses.
Do not publish production DB/WAL/SHM, provider logs, OAuth/device-code material,
credential-store exports, `controller.token`, native account passwords or copied
private journals. Their existing host locations and access boundaries remain.
The synthetic knowledge database directory is a disposable experiment; retain
the script and sanitized result instead of its generated database/corpus.

## Essential continuation material

Preserve the repository's complete current `src`, `scripts`, `pipeline_tests`,
`supervisor_tests`, `config`, canonical knowledge sources, project metadata,
lockfiles and documentation, including all local-only commits and unstaged
changes. Preserve the dated handoff and its curated evidence, then append this
bundle map to the publication. A documentation-only PR is not the complete
implementation.

Preserve the selected top-level `windows-deployment-next` Python/PowerShell
sources, tests, manifests, preparation/review JSON and test XML, subject to
publication review above. The exact-byte inventory is preferable to hand-picking
only the highest version: current wrappers import earlier frozen functions and
their hashes. Preserve these curated directories and source packages as well:

- `claude-reboot-continuation-v5-evidence-20261008`
- `claude-paste-continuation-v6-evidence-20261008`
- `pending-warden-recovery-v7-evidence-20261008`
- `running-monitoring-continuation-v8-evidence-20261008`
- `partial-resource-recovery-evidence-20261009`
- `resource-observer-r3-v1`, `resource-observer-r3-v2`, `resource-observer-r3-v3`,
  and especially the actually commissioned `resource-observer-r3-v4`
- selected ordinary regression source/evidence snapshots and foundation/Docker
  reconciliation snapshots listed in the inventory.

The latest useful source groups are:

| Purpose | Frozen source group | Current use |
| --- | --- | --- |
| Six isolated native subscriptions | `claude-code-input-v6.ps1`, `claude-session-history-v6.ps1`, `login_pipeline_worker_interactive_r3_v6.ps1`, `login-six-workers-status-first-r3-v6.ps1`, `authenticate-native-profiles-status-first-r3-v6.ps1`, related tests/preparation | Authentication completed; preserve sessions and evidence, do not rerun the commissioning series. |
| First Warden commissioning | `commission-first-warden-r3-v5.py/.ps1`, `resume-pending-warden-r3-v7.ps1`, `task-private-acl-v8.ps1`, reviewed pins and tests | Recovery completed; preserve exact original inputs and receipts. |
| Independent held supervisor | `held-supervisor-observation-r3-v1.py`, `install-independent-supervisor-staging-r3-v2.ps1`, `install-held-supervisor-observation-r3-v3.ps1`, `resume-running-held-observer-r3-v1.ps1`, `run-running-monitoring-continuation-r3-v8.ps1`, manifest/plan/reviews/tests | Installed and observation running under unresolved authority. Paid repair and component recovery remain disabled. |
| Resource recorder | `resource-observer-r3-v4`, `install-resource-observer-r3-v5.ps1`, dependency manifest, `resource-observer-duration-r3-v1.ps1`, `resume-partial-resource-observer-r3-v1.ps1`, tests | Recovery completed and recorder running. Its initial budget breach prevents treating that window as clean 48-hour acceptance. |
| Live workflow acceptance | `run-live-commissioning-r3-v2.py`, `continue-live-prepost-r3-v1.py`, exact journal reconciliation/inspection, approval/scope JSON, `await-live-admission-readonly-r3-v1.py`, tests | No model workflow submitted. Keep the original two IDs and old private journal. These drivers pin the old controller/r3; a runtime change needs a reviewed successor, not a fresh request namespace. |
| Codex live MCP bridge | `verify-codex-live-bridge-r3.py`, bridge source/configuration and sanitized live result | GET-only bridge acceptance passed; successor must preserve the established source/token boundaries. |
| Telemetry repair investigation | `compare_commit_type_cache_memory_r3.py`, `reproduce-commit-probe-type-growth-20261009.py`, sanitized comparison/results | Source-only AST experiment. No production telemetry fix or fresh protected runtime upgrade has been implemented/installed by this audit. |

`RETURN_SETUP*.txt`, old morning/login instructions and completed recovery
wrappers are historical instructions. They must not become the next agent's
default run command. The authoritative current next step is the dated handoff:
implement the causal telemetry fix and safe protected runtime/lifecycle
successor while retaining the canonical 50 MiB/5% guard, then reattest and resume
the already approved two workflows without resubmitting any ambiguous request.

## Candidate versus installed source: important correction

The October 7 `supervision-authority-candidate-preparation-20261007.json` correctly
records its state *at preparation time*. It must not be used as current evidence
that every candidate remains uninstalled.

Actual ordinary read-only SHA-256 comparisons on October 9 show:

| Source | Current repository SHA-256 | Current installed comparison |
| --- | --- | --- |
| `src/cochem_supervisor/engine.py` | `9ec21d808bfbb1c5dfe01a6dd41b0dc969f9fc92df4ff5de010c4890c1dddeea` | Matches independent `Supervisor4.2.7-windows-20261007-staging-v1` installed module. |
| `src/cochem_supervisor/replay.py` | `d0a10b30e3f16d44a2906d855d46f44dc0fbad7249b8b43d6b67b98e4d94d60d` | Matches independent staging-v1 installed module. |
| `src/cochem_pipeline/performance_acceptance.py` | `8164ca69e6f31ad1e1912d1396df086b2590c103fd3c9a64d7a6031872c56e4c` | Pipeline r3 still has `3a6e1b0f5a62005c09ba07b4261bfef0e4d5f3962af9f087ae91b81ca2bab9e2`; independent recorder package has the candidate bytes. |
| Recorder `cochem_supervisor/resource_observation.py` | `ec3fa5a9c8e20228b6f43b15c5c4b5c332e1d7191ca563afbd8507baa5e5a3d7` | Matches protected `ResourceObservation4.2.7-windows-20261008-r3-v4\package` installed module. |

Preserve the supervisor candidate provenance, narrow diffs, paired uncertainty
bootstrap, latest freeze/manifest, tests and independent review. These code
changes were not installed into Pipeline r3 itself; the independent held
supervisor installation and isolated recorder installation are separate facts.
The paired unknown authority remains held; installation is not permission to
reset budgets or activate repair.

## Old-path dependencies and removal conditions

The latest native/auth/denial/first-start/observer wrappers still pin/import:

`D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1`

with SHA-256
`0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b`.
The live workflow driver pins:

`D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\verify_pipeline_acceptance.py`

with SHA-256
`206cbeea48774042fa676ca71350ac3c17cd468619cc2cfbd7ba5bc999f6dca0`.

Preparation generators, source manifests, test `REPO` constants and archive
writers also use the old root. Copying them preserves bytes but does not make
them executable from a different path. New additive wrappers should verify the
preserved helper bytes at the new path, generate their own new pins and reviews,
and retain old manifests/receipts untouched. Do not silently substitute a new
repository HEAD for a frozen source manifest. Host-specific installed and private
paths are intentional custody bindings and need fresh verification on another
workstation; they are not portable installation instructions.

Read-only host evidence obtained here:

- Installed r3 `pipeline.json` retains SHA-256
  `135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c`
  and contains no old repository/GDrive reference.
- Owner Codex `config.toml` and saved `codex-connection-20261008T011353Z-05b57a5c\connection.json`
  contain no old repository/GDrive reference. Their contents/credentials were not
  copied or printed.
- Saved protected Warden and commissioning XMLs point to Program Files r3 Python
  and working directory, and contain no old repository reference. These are saved
  definitions, not a new live Task Scheduler attestation.
- The saved pre-PawnIO `Mount_CoChem_RAMDisk` XML invokes `imdisk.exe`, has no saved
  working directory and no old repository/GDrive reference in its arguments.
  Arguments were not printed. This is historical unchanged-task evidence.
- One ordinary live Task Scheduler connection was denied with `0x80070005`.
  The current definitions of RAM startup, Warden, supervisor and recorder could
  not be independently queried. No task was changed or retried.

Before deletion, verify current task actions/startup references through the
existing reviewed administrative deployment/cutover inspection, preserve the
8 GiB R: startup task and native driver, and resolve any actual old-path references.
This is part of the next required installation work, not a request for another
manual timing test. Until then the correct deletion status is **not yet
attested safe**, even though the observed installed configuration and saved task
definitions do not depend on the old checkout.
