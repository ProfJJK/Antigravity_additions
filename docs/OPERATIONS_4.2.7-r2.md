# 4.2.7 owner-amendment operations

The canonical revision is `model-routing-2026-10-07`. Published tags
`v4.2.7-r2` and `v4.2.7` remain immutable history; this revision keeps version
4.2.7 and the existing installation paths. All 18 accepted
chapter improvements are mandatory and listed in the requirement ledger.

## Model jobs and concurrency

Use the Antigravity pipeline MCP for planning and coding. Every model node,
including synthesis, final reconciliation and repair, receives a Chapter 06
reservation. Provider-named compatibility tools forward to the same controller;
`model` must be empty. For coding, configure `controller.projects` with an exact
workspace-to-project mapping in the bridge configuration. A tool named
`claude_submit` can execute Codex/Agy if the tier's ordered spillover chooses it.

All generation CLIs run with verified inference-only tool/MCP/hook restrictions.
The controller applies validated artifacts; models cannot launch hidden nested
agents or acquire unaccounted capacity. Unsupported native restrictions produce
compatibility holds and normal tier spillover.

| Complexity | Ordered candidates |
| --- | --- |
| 1–3 | Gemini 3.8 Flash default → Claude Haiku 4.5 default → GPT-6 Luna low |
| 4–6 | Claude Sonnet 5.5 default → GPT-6.1 Sol medium → Gemini 3.8 Flash Extended |
| 7–9 | GPT-6.1 Sol high → Claude Opus 5.5 Extended → Gemini 3.1 Pro Preview high |
| 10 | Claude Fable 5.1 Extended → GPT-6 Astra ultra |

Read [the research record](ROUTING_RESEARCH_2026-10-07.md) for exact identifiers
and effort evidence. Sol medium/high is a latency/quota hypothesis, not measured
equivalence to Opus. Extended means a protected reviewed profile bound to exact
executable/version/model/native arguments or settings and observed metadata;
do not type `extended` as an invented CLI option. Verify the installed Agy
headless/auth/profile contract and disable native automatic fallback and internal
inference subagents. An unavailable profile holds and spills over normally.

`max_execution_slots` controls shared host admission, initially 4. Increase it
only after measuring the host and provisioning enough distinct isolated worker
identities. CPU, RAM, commit, GPU/thermal, disk and container reservations remain
active constraints. Claude has a hard provider ceiling 20. Codex/Agy have no
assumed provider ceiling. Coding batches and WBS leaf counts do not inherit the Claude limit of 20.

For the owner's 13700K/64 GB host, [workstation guidance](WORKSTATION_13700K_4.2.7.md)
recommends measuring six then eight seats after preflight, retaining the required
four-worker Windows queue benchmark. The six-identity example needs at least
eight identities for an eight-slot configuration. Eight requires 33 GiB of
current available RAM and 38 GiB free commit under default reservations; twelve
requires 49/54 GiB and is conditional, while sixteen cannot fit the default
physical-memory bound on 64 GiB. No slot or pool size changes automatically.

The prepared pool defaults to fixed per-profile targets. Optional adaptation
requires measured demand history and stays inside configured hardware capacity,
FIFO ordering and native fairness. Cold creation is background maintenance.

## Final approval

After final P10 tests and file audits, `RECONCILING_SRS` dispatches a different-
provider review through Chapter 06. Its immutable evidence binds captured SRS
chapters, WBS leaf, requirement and criterion/test mapping, actual source/patch,
physical tests and previous file reviews. Missing or divergent bindings block
Git integration. Old generic PASS text cannot substitute for this gate.

## Authenticated operator views

Call `pipeline_operator_view` in Antigravity. It returns governance captures,
transition prerequisites, score/rationale and ordered route/cooldown data,
measured admission/resource SVG plots, deployment Mermaid/evidence, Oracle rule
and delivery history, prepared-pool demand, RAM occupancy/scratch age, retention
forecasts, workload distributions and the requirement acceptance checklist.

The same protected data is exposed by authenticated controller `/operator`
operations. A view does not grant a claim/complete endpoint. Unknown quota
balances, missing sensor data, unobserved identities and absent acceptance
artifacts remain explicit. Diagnostic windows are bounded and expose their
pagination/truncation state. Knowledge search/read results show reviewed
current-normative, owner-decision, historical-source or unclassified badges.

## Existing R: RAM volume

The default is the existing ImDisk `R:\` 8 GiB volume created by the owner's
Windows startup task. The controller waits up to 120 seconds for it, then verifies
driver/device/NTFS backing and protected worker mappings. Startup-task recovery
retries automatically after failed launch. It never formats, detaches or resizes
an adopted volume. Occupancy and scratch age are measured; disk-write savings
remain unknown without a real counter baseline. Online resizing is not claimed
for an unverified driver capability. Existing persistent logins stay outside RAM.

## Upgrade preview and preservation

Run the read-only preview using the installed Windows Python:

```powershell
& $PipelinePython -m cochem_pipeline upgrade-preview --current-config $CurrentConfig --proposed-config $ProposedConfig
```

It shows exact path/identity/config/schema changes, captured workflow effects and
rollback prerequisites. Stage code in `Pipeline4.2.7-r2`/`Supervisor4.2.7-r2` and
knowledge in `Knowledge4.2.7-r2`, with a fresh protected knowledge index. The
installer verifies installed code and canonical assets, not just version 4.2.7.
Preserve existing account names, credential targets, job database, immutable
receipts, recovery budgets and quarantine history. Use the actual previous
supervisor data root; a reused target ledger is preserved and verified, never
replaced by an older budget snapshot.

The original corpus is archived outside retrieval. The committed portable FTS5
snapshot contains public canonical documents and reviewed authority metadata;
Windows still builds/validates its protected index. Existing immutable source
pins must not be silently overwritten. An old captured execution contract may
require a visible migration hold; it never receives retrospective approval.

New model submissions capture catalogue version 2; score algorithm version 1 is
unchanged. Version-1 task policies stay decodable and immutable. At a new attempt,
retired targets receive compatibility holds and ordinary spillover within the
captured chain. They are not rewritten to similarly named models/efforts. Active
reservations and accepted receipts remain unchanged, and upgrades cannot reset
dispatch, ordinary-failure, quota/backoff or repair budgets.

## Incident replay and manual preflight

Replay redacted captured detector evidence without a subscription call:

```powershell
& $SupervisorPython -m cochem_supervisor replay-incident --input $RedactedReplayFile --config $SupervisorConfig
```

Use the replay JSON schema in `cochem_supervisor.replay`. The optional configuration
loads effective budget caps and a read-only projection of the existing ledger;
missing ledger information remains unknown. Projected actions are not performed recovery. Do not include subscription credentials or raw prompts.

Manual CLI capability inspection is non-inference by default:

```powershell
& $SupervisorPython -m cochem_supervisor preflight --config $SupervisorConfig
& $SupervisorPython -m cochem_supervisor preflight --config $SupervisorConfig --model-probe --timeout-seconds 120
```

The explicit model probe is a single main-board task with ordinary Chapter 06
routing and a finite invocation budget/deadline. It records only actual executed
provider/model evidence; unselected routes remain unverified. It does not force
provider selection by inventing quota exhaustion. Version/help checks do not
prove authentication or model availability.

Independent repair uses a protected secondary job board with the same canonical
scoring and routes. Primary inference is physically contained before dispatch;
this board can operate when the main database is corrupt. Paid repair budgets,
outer acceptance and promotion/rollback remain independent of model prose. Repair
generation and final asymmetric review share the two-per-incident/four-per-day
paid attempt allowance. Post-deployment live native smoke is a separate single
routed board job with at most three dispatches and a finite deadline; its
workflow ID and consumed budget survive retries and journal recovery. Record
both scopes explicitly. An unavailable reviewer resumes from a protected checkpoint
of the same validated candidate and the same charged repair attempt; it does not
regenerate the repair. A paid interrupted review remains charged and cannot
receive a fabricated approval. Failure rolls back; unknown credit usage stays unknown.

## Storage and workload objectives

The operations module runs under the protected controller identity:

```powershell
& $PipelinePython -m cochem_pipeline.operations_policy --private-root $PrivateRoot forecast
& $PipelinePython -m cochem_pipeline.operations_policy --private-root $PrivateRoot archive $EvidenceRelativePath
& $PipelinePython -m cochem_pipeline.operations_policy --private-root $PrivateRoot objectives
```

Archive copies are hashed and verified; SQLite uses an online backup. Originals,
receipt authority and acceptance references are preserved. There is no automatic
pruning. The 90-day age, 30-day forecast and 512 MiB bounded archive settings are
conservative implementation defaults, not measured storage requirements.

Interactive and background latency/error observations are separate. After
Windows launch evidence exists, `ratify-objective` accepts numerical p95 and
error-budget targets with a review ID and launch-evidence ID. At least 64 real
observations are required. Until then objectives remain unratified. Historical
accepted queue/MCP/cold-preparation observations stay acceptable, while
correctness, containment and 30-second physical test deadlines stay mandatory.

## Evidence limits

This routing update does not close the nine [4.2.7-r2 audit findings](AUDIT_4.2.7-r2.md).
In particular, the dashboard's source-drift check and deployment attestation view
remain incomplete; a displayed verified row is not proof that changed source
meets every semantic requirement. The prior release record does not validate this
new canonical revision without fresh evidence.

The dashboard checks exact artifact hashes, revision and required platforms.
A missing row, stale artifact or missing Windows evidence prevents full
acceptance. Linux test success, offline CLI help and PowerShell parsing cannot
establish native subscriptions, Windows ACLs, real R: backing or 48-hour stability.
Run those acceptance steps on the actual deployment and retain their receipts.
