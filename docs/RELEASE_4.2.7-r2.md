# 4.2.7 owner amendment: universal routing and verified final review

This amendment implements the owner-approved [canonical SRS](../4.2.7_SRS.md),
revision `owner-amendment-2026-10-06`: 18 chapters, 110 requirements, including
all 18 accepted improvements. `v4.2.7-r2` preserves the original `v4.2.7` tag.

Every model task enters a durable job board and uses Chapter 06, including
planning, synthesis, coding, research, review, preflight and independent repair.
Provider-named MCP tools forward to the authenticated controller. Fixed-model
submissions fail validation. Native CLIs run with verified inference-only
restrictions so hidden nested agents cannot bypass routing and capacity.

| Complexity | Ordered routes |
| --- | --- |
| 1–3 | Flash → Haiku → Luna |
| 4–6 | Sonnet → Sol → Gemini Pro |
| 7–9 | Opus → Astra low → Gemini Pro |
| 10 | Fable → Astra ultra |

Availability, quota holds, backlog, current admission caps and provider exclusions
control spillover. Exhausting the tier creates a durable bounded backoff before
retrying the preferred eligible route. Historical captured policies and spent
budgets remain intact. Claude alone has the hard 20-agent provider ceiling;
Codex/Agy have no assumed provider ceiling. Shared hardware capacity is
configurable, initially four pending host measurement.

Final coding approval now requires a different-provider SRS/WBS reconciliation
bound to the exact source, tests, chapter requirements and all prior WBS leaves.
The independent repair service uses the same route order and review obligation,
with physical native/Docker quiescence, durable budgets, crash recovery and
bounded deployment smoke verification. Repair proposals can safely introduce
new Python modules in captured, existing allowlisted directories.

The default RAM policy adopts the existing ImDisk `R:\` 8 GiB startup volume.
It verifies the actual backing and worker mapping, waits for automatic startup,
and never formats, detaches or resizes an adopted volume. No repeated manual
provisioning is introduced. Dynamic resizing is not claimed without a verified
safe driver capability.

Authenticated operator views expose governing hashes, transition blockers,
routing previews, resources, Oracle decisions, pool demand, storage forecasts
and a requirement-by-requirement acceptance checklist. Reviewable storage and
workload policies retain measured evidence; missing Windows measurements remain
unknown. Prepared containers persist between jobs, with optional measured-demand
replenishment. Incident replay projects actions and budgets without inference.

The active wiki/RAG corpus includes the canonical SRS, owner decisions and
explicitly labelled historical primary sources. A portable SQLite FTS5 snapshot
is committed with hashes; the Windows deployment builds its own protected index.
Old corpus bytes remain archived outside retrieval. The installed authority and
active index must match before model dispatch.

## Validation

The [full validation record](evidence/VALIDATION_4.2.7-r2.json) records **2,438
passed, 35 Windows-only skipped, zero failures/errors** with real Linux Docker
execution and offline Codex/Claude capability probes. The exact code/test/script
manifest remained unchanged throughout the run. Dependencies pass consistency
checks and all seven PowerShell scripts parse without syntax errors.

The separate protected regression passed **1,522 tests with 57 skips**: 30 Docker
cases excluded from its isolated repair fixture and 27 native Windows cases.
The independent outer verifier passed **four contracts across 24 physical child
executions** against the same frozen source. These are distinct scopes and are
not added together.

The [wheel verification](evidence/build_4.2.7-r2.json) checks all **134 packaged
Python modules** byte for byte against the tested source, exact canonical/evidence
assets, isolated imports and installed-revision matching. Another **48 checks**
passed after attaching the final evidence. The operator checklist contains all
110 requirements: 35 currently have every required evidence scope; 75 still
require target-host acceptance. This is not a claim of full Windows certification.

The [RAG snapshot receipt](evidence/knowledge_4.2.7-r2.json) binds nine documents,
101 sections and the exported SQLite database. Integrity checks passed; index
size is 2.046 times raw corpus size. The ten original corpus files remain
byte-identical to the original tag.
Windows identities/ACLs, real RAM backing, Docker access isolation, live native
subscriptions, full planning/coding/repair workflows and the 48-hour soak require
execution on the target host. Portable tests do not certify those obligations.

See [operations](OPERATIONS_4.2.7-r2.md) and [upgrade instructions](EXECUTION_4.2.7.md).
Stage the separate r2 code/corpus roots and preserve existing identities, logins,
job state, immutable receipts and recovery budgets. An incompatible old captured
contract remains explicitly held; an upgrade cannot grant retrospective approval.
