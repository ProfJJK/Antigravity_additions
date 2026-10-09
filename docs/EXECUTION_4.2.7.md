# 4.2.7 upgrade and launch

Read the [canonical SRS](../4.2.7_SRS.md) and
[owner addendum](SRS_ADDENDUM_4.2.7.md) first. This release implements the
Alternative Path; the obsolete seven-stage and agentic Method Matrix references
are not production prerequisites. Chemical method selection remains a domain
concern, not a universal agent workflow gate.

The current revision is `model-routing-2026-10-07`, with version 4.2.7 and existing
installation paths retained. It updates the Chapter 06 model catalogue, not
historical release tags or evidence. Read [routing research](ROUTING_RESEARCH_2026-10-07.md)
for exact IDs and reviewed effort profiles. The [nine audit findings](AUDIT_4.2.7-r2.md)
remain outstanding; this guide is not certification that Windows deployment is ready.

## Preserve deployment state

Stage new protected `Pipeline4.2.7-r2` and `Supervisor4.2.7-r2` code/environments.
Use the supplied Windows installers only after merging the actual deployment
configuration. The example's paths identify a fresh installation; copying them
over a running configuration does not migrate its database, accounts or logins.
Keep the existing private job database, native credentials, task identities,
quarantine and immutable evidence. Back up active SQLite consistently.

Preserve both `supervisor.db` and `component-recovery.db` through the existing
SYSTEM migration helper. Use the actual existing supervisor data directory as the previous-data source;
pass it explicitly as `-PreviousDataRoot`. The script's historical default is
`CoChemSupervisor426`, not the current 4.2.7 ledger directory. Do not initialize
new budget ledgers over an existing deployment. Paid repair budgets
and component recovery budgets must not reset. A new protected acceptance
snapshot is required because the execution contract changed.

The pipeline installer's historical `-DataRoot` default is
`C:\ProgramData\CoChemPipeline422`; the example's `CoChemPipeline427` is a
fresh-layout illustration. Select the actual existing local data directory and
pass it explicitly along with its matching token, worker roots and task names.
The installers do not rename or migrate the pipeline database just because the
package version changed. Keep the defaults for existing account/task identity
compatibility, and review every path against the installed layout. If a protected
code root contains different bytes, choose a fresh code root and pass that same
`-InstallRoot` to its login helper; do not overwrite an old installation.

Drain/stop the old daemon before activating the new installation. Its exclusive
service lock prevents running a second Warden against the same private state.
Windows SYSTEM, identity/ACL, real RAM backing, Docker pipe/backend, native
subscription and Agy capability checks remain mandatory. The sample contains
explicit rejected Agy placeholders; this release does not invent native flags.
Public Gemini identifiers, including `gemini-3.1-pro-preview`, do not prove that
the installed Agy version supports the required headless/profile contract. Bind
Extended intent to reviewed native arguments/settings and observed metadata;
disable native fallback/subagents and retain visible compatibility holds when
the required restriction or profile cannot be verified.

## Worker login and MCP client staging

`scripts/login_pipeline_worker.ps1` defaults to `Pipeline4.2.7-r2` and accepts
provisioned `slot1` through `slot256`. These are account identifiers, not an
increase to the four shared execution slots. Use the installed layout's exact
slot and the actual protected CLI executable. Each login uses that isolated
worker's persistent profile; regular-account login does not authenticate it.

For Agy select `-Provider gemini` and pass `-LoginContract` pointing to a protected,
reviewed JSON file. The normal and repair helpers use the same native contract
validator. Its fields are `provider` (`gemini`), `executable` (the exact executable
path), `purpose` (`subscription-login`), `arguments` (the bounded literal native
argv array), and `capability_reference` (the reviewed evidence reference).
Populate it only after the installed CLI documents an actual subscription-login
command. Help that does not expose login support leaves this step on hold; do
not infer a command from another product. Login success, per-account subscription
access and retention still require actual Windows verification.

The unprivileged `scripts/install_mcp_windows.ps1` uses `uv sync --frozen
--no-editable --extra mcp` with the release `uv.lock` and a selected installed
Windows Python 3.12 or newer. Supply `-Uv` when uv is outside PATH, and use
`-Python <absolute-python.exe> -PythonArgs @()` for an explicit interpreter.
It stages `.venv-mcp`, restores the caller's `UV_PROJECT_ENVIRONMENT`, and
preserves existing client JSON and controller/project mappings. A successful
client installation is separate from authenticated stdio and native acceptance.

`scripts/verify_cli_mcp.py` is retired. It exits nonzero without submitting work;
its old unregistered workspace, model pinning and direct-edit behavior is not
valid acceptance. Use bounded controller preflight from the
[operations guide](OPERATIONS_4.2.7-r2.md), then the registered-project coding
acceptance workflow. Every model job remains Chapter 06 routed.

## Activate the clean knowledge corpus

The supplied `knowledge/` corpus contains the new canonical SRS, owner addendum,
current owner decisions and exact captures of the four primary documents.
The exact 4.2.6 corpus is retained under `docs/archive/knowledge_4.2.6`; it is
outside the active source/wiki collections so obsolete snippets are not retrieved
as current instructions. Original repository documents also remain available.

Provision a fresh protected corpus location such as `Knowledge4.2.7-r2` and a
dedicated private knowledge-index directory, then point the reviewed knowledge
configuration there. The installer only seeds an absent destination; it preserves
an existing corpus. Do not silently overwrite an operator's custom corpus or
delete its immutable pins. Merge desired domain sources deliberately and ratify
the complete physical inventory/hashes before indexing. The historical manifest
basename `v4.1.2_manifest.json` is retained for parser compatibility; its version
and contents identify the 4.2.7 owner amendment. Original4.2.7 captures are
archived under `docs/archive/knowledge_4.2.7_original`. The exported SQLite FTS5
snapshot is a reviewable public-corpus artifact; the installed protected index
is rebuilt locally and is not a claim that the Windows database was updated.

## Planning and held requests

Fresh coding requests capture the controller-owned `cochem-planning/4.2.7`
contract and proceed to actual `CODE_PLAN` work when real deployment prerequisites
are satisfied. Plan review, real research, test-first RED, source-only changes,
GREEN, independent audit, explicit different-provider SRS/WBS reconciliation
and fenced Git integration remain evidence-gated. Coding leaf/batch counts are
not capped at 20; Claude concurrency alone is capped at 20.

Use authenticated `code-resume` with the original workflow ID and a reason to
migrate an eligible untouched phantom-only hold. Migration must retain its
original Git baseline and record why the old prerequisite was withdrawn.
Already executed, cancelled, exhausted, research-failed or no-progress workflows
cannot masquerade as an untouched hold or reset their budgets. See
[planning prerequisites](PLANNING_PREREQUISITES.md) for exact eligibility.

## Prepared containers

Prepared pool members are persistent across controller restart but single-use
after receiving work. Background maintenance creates compatible containers,
records preparation provenance and reconciles actual engine identity/health.
Normal request execution cannot fall back to cold creation. Empty or incompatible
capacity is an explicit resource wait; durable profile demand requests
replenishment. Consumed/dead containers are removed with verified cleanup.

Native attempts, preparation, unused prepared members and active containers all
remain within the configured hardware/resource ceiling. A pool cannot reserve all
idle seats indefinitely while starving eligible native work. Refill is triggered
on handoff and released capacity. Record preparation, request/queue and prepared
handoff times separately; the accepted cold preparation observation is not a
promise of instant response when the pool is empty.

## Windows queue launch observation

Queue performance is conditionally accepted. Do not redesign based on the prior
Linux ten-contender result. At launch, enable the optional queue observer on the
existing Warden, using a new protected evidence directory under the actual
private deployment root:

```powershell
& $PipelinePython -m cochem_pipeline daemon --config $PipelineConfig --queue-launch-output $NewPrivateEvidenceDirectory
```

Run this through the configured SYSTEM startup mechanism after draining the
previous daemon, not beside it. Submit representative ordinary work through the
normal authenticated interface, exercising the four-worker topology, heartbeats,
Oracle writes, routing waits and completions. The observer measures existing
operations on the actual configured database; it does not inject synthetic jobs
or grant worker database access. Stop/drain normally to seal the observation.
See [queue launch evidence](QUEUE_LAUNCH_4.2.7.md) for coverage and interpretation.

Record storage/OS/configuration, workload and claim timing distributions. Only
the actual Windows run can close conditional acceptance. Missing four-worker
coverage, native receipts, meaningful workload or successful invariant checks
remains pending. If the 5 ms objective is still missed, investigate the observed
cause before changing the queue; correctness and durability stay mandatory.

After that original observation and prerequisite checks, use the
[13700K capacity guide](WORKSTATION_13700K_4.2.7.md) to evaluate six then eight
shared seats with sufficient identities and measured memory/commit headroom.
The global default remains four; do not relabel an eight-worker run as the
required four-worker acceptance observation.

## Remaining acceptance

Run the existing native identity/RAM/Docker/governor/Oracle tests, full planning
and coding workflows, and independent repair/promotion/rollback with actual
subscription CLIs. Complete the genuine 48-hour resource/handle observation and
desktop-heap measurement. Linux checks, PowerShell parsing and offline provider
help/version probes do not establish those results. Current executed evidence
belongs in [the release record](RELEASE_4.2.7.md).

## Owner-amendment operations

Use [the operations guide](OPERATIONS_4.2.7-r2.md) for the authenticated operator
views, one-time R: adoption, read-only upgrade preview, independent incident
replay, bounded routed preflight, archival and observed workload objectives.
All model tasks use Chapter 06. The legacy provider-named bridges require the
controller token/port and exact registered coding project mappings; the former
direct-inference queue is removed. Preserve old bridge receipts as history.

Existing workflows retain immutable captured policies and budgets. Pending
legacy single-Gemini synthesis receives an audited candidate amendment before
a new attempt. Coding workflows with an older execution contract cannot bypass
the new final reconciliation gate; the preview reports these holds and the
controller rejects incompatible captures. Review/re-submit with explicit
lineage where a safe untouched-hold migration does not apply; do not erase
history or claim an old review proves the amended contract.

New model jobs capture catalogue version 2 with score algorithm version 1
unchanged. Existing version-1 captures remain decodable and immutable. Retired
targets hold visibly on new dispatch, then use their captured spillover chain;
do not silently substitute the new Sol version or new effort into old policies.
Active/accepted receipts and consumed budgets remain unchanged.
