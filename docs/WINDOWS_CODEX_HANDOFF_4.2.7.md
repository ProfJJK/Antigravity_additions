# Handoff to the Windows Codex session

Prepared 2026-10-07 for `ProfJJK/Antigravity_additions`. This is a continuation
record, not a new SRS or a declaration that Windows deployment has passed.

## Goal and starting point

Continue the existing work toward a working, SRS-compliant pipeline on the owner's
Windows 11 workstation. Antigravity 2.0 is the primary GUI; Windows Python runs
the protected controller; Codex, Claude and Agy provide subscription-authenticated
native CLI inference; WSL2/Linux Docker runs generated code and tests.

The implementation and evidence baseline is commit
`aa461682b0a7631ce8d0c1d88f7f4a35fb94b98b`, published to `master`.
This handoff is a subsequent documentation-only change. Package version remains
4.2.7; canonical revision is `model-routing-2026-10-07`. Old `v4.2.7` and
`v4.2.7-r2` tags retain their historical contents. A version string alone does
not establish which implementation is installed.

The owner's expected checkout is:

```text
D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions
```

Verify that path, Git branch, local modifications and actual installed state.
Fetch current `origin/master`, preserving local changes and local-only commits.
Use a fast-forward update where possible. Do not reset, clean, overwrite local
configuration or assume the Windows checkout already matches cloud work.
No new Git worktree is needed for the handoff by default.

The cloud interpreter, `/tmp` build outputs, running processes and interactive
chat state are not prerequisites for continuing. The relevant code, documentation,
research, test XML, source manifests and public knowledge snapshot are committed.
Create the Windows environment using the supported frozen dependency workflow.

## Read these first

1. [Canonical SRS](../4.2.7_SRS.md) and [owner addendum](SRS_ADDENDUM_4.2.7.md).
2. [Current validation](evidence/VALIDATION_model-routing-2026-10-07.json) and
   [build/asset checks](evidence/BUILD_model-routing-2026-10-07.json).
3. [Open audit findings](AUDIT_4.2.7-r2.md), including the linked per-requirement
   audit and physical counterexamples. The audit describes the earlier baseline;
   the routing amendment did not certify its nine findings closed.
4. [Native effort contracts](NATIVE_EFFORT_4.2.7.md) and
   [routing research](ROUTING_RESEARCH_2026-10-07.md).
5. [Deployment guide](EXECUTION_4.2.7.md),
   [operations guide](OPERATIONS_4.2.7-r2.md) and
   [workstation capacity guide](WORKSTATION_13700K_4.2.7.md).

Where older prose and inspected script defaults disagree, preserve the actual
installation and reconcile explicitly. Specific known mismatches appear below.

## Owner decisions that remain authoritative

- All model tasks use a durable job board and Chapter 06 complexity routing:
  planning, synthesis, coding, research, reviews, preflight and independent repair.
  Provider-named MCP tools do not authorize fixed-model exceptions.
- Complexity 3 belongs to the lightweight tier. The owner selected verified
  Fable 5.1 after Fable 5.5 could not be verified in the retrieved catalogues.
- Fresh work uses catalogue v2 and scoring algorithm v1. Historical captures,
  receipts and spent budgets remain immutable; retired targets receive no new
  reservations and follow their captured spillover/backoff rules.
- Claude alone has the hard provider ceiling of 20 concurrent agents. Codex and
  Agy have no assumed provider-specific ceiling. Shared slots, provisioned
  identities, hardware, quota/authentication holds and scheduling still apply.
- Start Windows acceptance at four shared execution slots. Native jobs and
  prepared/active Docker containers share that budget. Additional provisioned
  accounts do not increase simultaneous capacity. Measure six then eight only
  after the original four-worker queue observation and prerequisite checks.
- Preserve the existing 8 GiB ImDisk `R:` volume and the owner's startup task.
  Adoption must not format, replace or repeatedly recreate it. No safe automatic
  resizing has been demonstrated.
- All 18 SRS improvement inserts are accepted. Different-provider SRS/WBS/code
  reconciliation is required before final coding approval.
- The phantom agentic seven-stage protocol and Method Matrix M-1–M-8 are
  withdrawn. The chemistry Method Matrix is a separate domain concept.
- FR009 is amended: isolated task failures/cancellations do not freeze the entire
  pipeline; structural corruption requires containment.
- Historical queue 29.347 ms, authenticated knowledge 7.731 ms and cold Docker
  preparation 4.85 s observations are accepted within their recorded scope.
  Windows queue acceptance remains conditional on the actual four-worker,
  actual-database-location workload. Measure before redesigning the queue.
- Native subscription CLIs are required; paid API substitution is not authorized.
  Native hidden subagents and automatic model fallback must not bypass the board.

| Complexity | Preferred | Second | Third |
| --- | --- | --- | --- |
| 1–3 | Gemini 3.8 Flash, default | Claude Haiku 4.5, default | GPT-6 Luna, low |
| 4–6 | Claude Sonnet 5.5, default | GPT-6.1 Sol, medium | Gemini 3.8 Flash, Extended |
| 7–9 | GPT-6.1 Sol, high | Claude Opus 5.5, Extended | Gemini 3.1 Pro Preview, high |
| 10 | Claude Fable 5.1, Extended | GPT-6 Astra, ultra | — |

Sol medium/high are research-informed engineering starting choices, not measured
Sol–Opus equivalence. Extended is a protected native configuration profile;
unsupported profiles remain visible compatibility holds with ordinary spillover.
Requested effort must not be presented as observed native effort.

## What has actually been verified

- Final Linux suite: **2,597 passed, 35 Windows-only skipped**. It included actual
  Linux Docker execution and offline Codex/Claude capability checks. No paid
  native subscription inference or comparative model benchmark was performed.
- **93 additional asset/governance tests passed** after updating the ledgers;
  wheel assets were checked against the canonical files and implementation.
- The passing suite's exact source manifest and initial corrected fixture failure
  are retained. A cloud connection interrupted one repeat; no pass was inferred
  from it. The final complete run exited zero with unchanged source hashes.
- The current public knowledge export has **11 documents and 126 sections**,
  passed SQLite integrity checks, and preserves earlier source captures. It has
  not replaced the protected Windows index.

Linux results do not establish Windows SYSTEM/worker ACLs, Job Objects, native
process identity or cleanup, ImDisk backing, Docker pipe/backend denial, hardware
sensors, actual subscription/model/effort access, Antigravity stdio behavior,
the Windows queue benchmark or the 48-hour stability requirement.

The cloud startup-instructions draft save returned `stale_base`. The complete
proposal is committed at [config/cloud-environment.proposed.json](../config/cloud-environment.proposed.json).
That cloud configuration failure does not block a local Windows development
session. Its Linux setup paths are not Windows deployment instructions.

## Known work still outstanding

| Audit ID | Required correction |
| --- | --- |
| A427-01 | RED must require a meaningful executed failing assertion; RuntimeError before an assertion currently qualifies incorrectly. |
| A427-02 | Preserve structured WBS declarations and complete accepted output commitments through planning scatter/gather and synthesis. |
| A427-03 | Fix normal-worker login paths, add reviewed Agy login support and align the slot range with provisioning. |
| A427-04 | Reattest prepared containers against actual engine/resource policy before retaining WARM state after recovery. |
| A427-05 | Keep slow cold-container preparation outside the shared admission critical section while preserving durable reservations and fairness. |
| A427-06 | Invalidate acceptance evidence when installed source/dependency commitments differ; do not infer full compliance from a green row. |
| A427-07 | Display actual controller, Docker boundary and RAM backing attestations and drift in deployment views. |
| A427-08 | Make MCP client installation use the frozen release dependency lock. |
| A427-09 | Finish retiring/replacing the obsolete live MCP checker. Reusable setup text was corrected, but this finding is not fully closed. |

Concrete deployment traps observed in current scripts:

- `scripts/login_pipeline_worker.ps1` defaults to `Pipeline4.2.7`, whereas the
  installer uses `Pipeline4.2.7-r2`; it supports only Codex/Claude and slots 1–64.
- Pipeline installer `DataRoot` defaults to `CoChemPipeline422`; the example
  configuration uses `CoChemPipeline427`. Use one consistent actual layout.
- Supervisor installer `PreviousDataRoot` defaults to the 4.2.6 data directory,
  despite launch-guide wording suggesting the current 4.2.7 directory. Locate
  and pass the real existing ledger directory when migrating.
- Exact-content checks preserve existing installations. A changed build cannot
  reuse old protected source merely because both versions report 4.2.7. Select a
  fresh protected installation root when the existing root has different bytes.
- The example contains deliberately rejected Agy and Docker image placeholders.
  Empty Claude/Agy effort contracts are unverified routes, not working bindings.
- `scripts/verify_cli_mcp.py` is a legacy checker with incompatible workspace,
  model-pin and direct-edit assumptions. Use current routed preflight/acceptance.

## First local work and staged Windows trial

1. Inspect Git state and existing deployment read-only. Inventory Windows Python,
   uv, Git, Codex, Claude, Agy, WSL2/Docker, existing service tasks, paths, accounts,
   RAM startup ownership and sensors. Record versions, executable hashes and
   relevant capability output without collecting credentials or dumping environment
   variables. Run [inspect_agy_windows.ps1](../scripts/inspect_agy_windows.ps1)
   for bounded version/help evidence. Do not infer Agy flags from public Gemini
   CLI documentation or from a model's claim.
2. Repair confirmed repository defects with meaningful regression checks, starting
   with installer/login and correctness blockers. Preserve SRS requirements;
   do not disable containment or evidence checks to make installation pass.
3. Prepare one actual host configuration: consistent protected roots, operator
   identity, native contracts, exact Docker image digest, hardware/WSL2 limits,
   existing R: adoption, knowledge source/index paths and a disposable registered
   coding repository/branch. Ordinary-user login does not authenticate isolated
   workers; use the corrected per-account login path and preserve those profiles.
4. Stage installation and recoverable state consistently. Preserve job databases,
   original Git baselines, receipts, routing captures, quota/retry/repair budgets,
   `supervisor.db`, `component-recovery.db`, accounts, credential targets,
   quarantine, release pointers and scheduled-task identities. Never reset a
   budget by creating an empty replacement ledger. Keep active databases on local
   storage outside the GDrive-synchronized source checkout. Do not run two
   Wardens against the same state or overwrite an existing knowledge corpus.
5. Run native prerequisite and containment tests, then bounded routed model
   preflight. Start live inference through the board and retain process-derived
   receipts. Test planning, physical RED/GREEN coding, asymmetric reconciliation,
   Git integration, independent repair and rollback on the disposable project.
6. Observe the required actual four-worker queue topology. Test reboot/recovery,
   then six/eight-slot capacity only if measurements justify it. Complete the
   48-hour resource/handle/desktop-heap observation before full acceptance.

Read-only Windows discovery can begin before every defect is repaired. Native
acceptance is work to perform on Windows, not a prerequisite that can be completed
in Linux before starting Windows testing. Separate observed passes, failures,
skips, holds and unrun checks in every progress report.

## Suggested opening instruction for local Codex

> Continue the Windows deployment work from docs/WINDOWS_CODEX_HANDOFF_4.2.7.md.
> Read that handoff and its canonical SRS/owner decisions before changing code.
> Preserve local changes and existing installation state. First inspect this
> Windows workstation and the installed CLIs without starting model jobs or
> services. Then repair the documented blockers and prepare the host-specific
> configuration and staged test procedure. Keep all model work on Chapter 06
> routing, preserve the existing 8 GiB R: setup, and report actual evidence.

Use a session able to execute native Windows PowerShell/Python for the host
checks. An ordinary user session is suitable for initial inspection and coding;
installer/provisioning operations require the repository's administrator/SYSTEM
execution paths when that stage is reached. Chat history need not be transferable
for this handoff to work: the committed record is the continuation context.
