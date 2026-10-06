# 4.2.7 source inventory and owner decisions

This is the source disposition for the clean [4.2.7 SRS](../4.2.7_SRS.md).
It distinguishes actual requirements from historical implementation examples,
debugging claims and superseded design choices. The canonical SRS defines the
current execution contract; source captures remain evidence of their original
wording, not competing instructions to workers.

## Authority and changes approved by the owner

The owner's current instructions take precedence over all archived documents.
The following decisions are effective for 4.2.7, dated October 6, 2026:

| Decision | Canonical disposition |
| --- | --- |
| The seven-stage planning bullet is obsolete; Method Matrix M-1–M-8 describes chemical calculation tiers, not agentic execution. | Withdraw both as pipeline execution requirements. Do not request missing definitions, create synthetic replacements or retain a production hold because either reference is absent. Chemical task specifications may independently use their own method matrix. |
| Create the Alternative Path from the actual 4.2.0 architecture, concurrency/spillover requirements and Oracle SRS. | Define explicit planning, coding, routing, execution, recovery and terminal transitions from those requirements. Preserve meaningful evidence and asymmetric audit gates. Withdrawal of the phantom requirements does not withdraw the separate P1–P10 coding cycle. |
| Queue acquisition of 29.347 ms on the prior Linux ten-process experiment is acceptable, conditional on Windows launch measurement. | Record the result as conditionally accepted. Benchmark Windows, four actual production workers, the configured physical database location and representative work first. Redesign only if that production measurement still misses the 5 ms optimization target. |
| Backend retrieval around 4.1 ms and authenticated knowledge requests around 7.7 ms are acceptable. | Retain these accepted measurements and profile the approximately 3.6 ms difference. The old authenticated 5 ms target does not block launch solely because the accepted baseline exceeds it. Preserve authentication and result correctness while optimizing. |
| Cold Docker startup around 4.85 seconds is acceptable as pool replenishment. | Normal jobs use a persistent pool of prepared, single-use containers. Cold creation belongs to replenishment; empty capacity causes a visible bounded wait rather than a request-path cold launch. Record cold creation and prepared handoff separately. |
| Isolate ordinary task failures; amend archived FR009. | Preserve the 4.2.6 amendment: structural lease/budget corruption triggers global containment; an unrelated historical FAILED row does not. |
| Exact complexity routing was corrected by the owner. | 1–3 Flash → Haiku → Luna; 4–6 Sonnet → Sol → Gemini Pro; 7–9 Opus → Astra low → Gemini Pro; 10 Fable → Astra ultra, two candidates only. Every model job, including synthesis and repair, uses these chains; there is no fixed-model exception. |
| Windows Python beside the native subscription CLIs is the host execution choice. | Protected Windows controller and native Codex, Claude and Agy subscription runners; WSL2/Linux Docker executes code/tests. Do not silently replace subscriptions with API-key inference. |

The accepted performance observations are not newly invented universal ceilings.
An individual measurement does not certify other hardware, all workloads or
the 48-hour stability target. Conversely, those accepted observations must not
continue to be listed as blocking failures against the withdrawn interpretation.

## Primary source inventory

| Key | Actual source and clause location | Requirements retained |
| --- | --- | --- |
| P | [Pipeline_4_2_0_Architecture.md](../Pipeline_4_2_0_Architecture.md), Core Design §§1–3, WBS and five acceptance criteria | One request creates macro → manifest → independent chapters → synthesis (now Chapter 06 routed); atomic scatter/gather, hardware admission (now configurable with conservative four-seat initial setting), per-chapter ownership, unique attempt/lease/fence, structured outputs, immutable hashes and crash-safe completion. Six chapter identities do not mean six simultaneous agents. |
| O | [Oracle_SRS_WBS.md](../Oracle_SRS_WBS.md), §§1.2.1–1.2.6 and repeated WBS phases 1–4 | SYSTEM circuit breaker strictly above 500 events/sec, asynchronous Job Object cleanup, fenced retry/failure, DB/pipe hints, workspace Defender exclusion, private watermarks after 500 ms debounce, Aho-Corasick facets, bounded knapsack and reserved core directives, WAL and 5000 ms busy timeout. |
| T | [full_dossier_untruncated.md](../full_dossier_untruncated.md), §§3–6, AC1–AC6/T1–T6 | Exact recovery telemetry signature/calling forms/schema; complete Unicode JSON; null/default normalization; positive monotonic row IDs; injected-connection ownership; owned-connection cleanup; explicit commit; short transactions; real SQLite and anti-spoof verification. |
| T-short | [full_dossier.md](../full_dossier.md), §§1 and 6–7 | The same telemetry task, with a literal truncation marker. T supplies missing details; this is not another recovery implementation specification. |

The dossiers explicitly exclude recovery-ladder FSM logic, direct Hyper-V
commands, a separate FastMCP exposure, remote telemetry and guest pipe loops
from this *telemetry API task*. Those exclusions do not erase separate Oracle,
host supervision or execution-environment obligations.

## Concurrency, spillover and execution sources

There is no separate file named a concurrency-spillover SRS in the available
repository. A full hidden-file Markdown search also found no literal
`spillover`/`spill-over` requirement. The relevant requirements are distributed
across the following actual sources and the owner's explicit routing/backoff
instructions; no missing document is invented as a prerequisite.

| Key | Source and relevant clauses | Current disposition |
| --- | --- | --- |
| H3 | [Chapter 3 concurrency](../v4.1.2/wiki/srs/ch03_concurrency_layers.md), FR001–008, NFR-CON-01–03 | Retain bounded OS concurrency, hidden process groups, real descendant ownership, sanitized Node heap settings where applicable, 100–500 ms launch/poll jitter and stability observations. Twenty is only the Claude CLI concurrency cap; old 1–4/5–8/9–10 routing is superseded. |
| H4 | [Chapter 4 blackboard](../v4.1.2/wiki/srs/ch04_task_matrix_blackboard.md), FR001–009, NFR-TM-01–03 | WAL/NORMAL, atomic BEGIN IMMEDIATE claims, 1800 s default leases, 5 s heartbeat, separate three-failure ordinary execution budget, read-only observers, idempotency and atomic clearing of lease authority. Oracle's 5000 ms busy timeout replaces historical 30000 ms. Queue performance follows the owner decision above. |
| Q | [Hidden quota-balancing proposal](../.docs/improvements/PROPOSAL_FABLE_QUOTA_BALANCING.md), §§2–6 | Source for availability classification, cooldowns, bounded fallback, context limits and actual-versus-estimated usage. Historical two-provider orders, no-Haiku floor, terminal local-model sentinels and assumed pool/cost weights do not override the corrected three-provider tier chains. |
| G | [Legacy resource governor](../.scripts/resource_governor.py), module contract and AdaptiveLimiter | Historical design rationale: shrinking admission does not reset the count of already running work; measure Windows commit headroom as well as available RAM, retain reserve and cap descendants with Job Objects. Existing snippet defaults are not current deployment facts. |
| I/O | [Legacy concurrency guard](../.scripts/concurrency_guard.py), disk metrics and guarded execution slot | Disk rate/capacity observations must be fresh, bounded and enforced at admission, not merely logged. Its platform-specific implementation is historical evidence; the active modules are hardware_guard.py, routing_store.py, admission.py and runtime.py. |
| A | [Core modification authorization](../V4_CORE_MODIFICATION_AUTHORIZATION.md), permitted purposes and Conditions | Retain Docker-aware admission/reaping, staged Git changes, path-contained evidence, objective research confidence, separate plan/implementation audits and PASS-only dispatch. Its old file permissions and obsolete seven-stage clause do not override current authorization. |
| R | [Reconstruction plan](../v4_pipeline_reconstruction_plan.md), tasks 1–5 | Native orchestration, isolated per-worker RAM scratch, persistent subscription credentials, explicit fallback/backoff and physical Git patches. Old Fable-only direction, 19/20 workers and Fable→Opus→Gemini 1.5 order are superseded. |
| M | [RAM mount standard](../.docs/improvements/RAM_Disk_Reparse_Point_Standard.md), Architectural Rules | Verify genuine RAM-backed mount identity, preserve original content before mounting, protect persistent credentials/state and avoid arbitrary junction trust. Current protected paths require a deliberate deployment mapping; a directory variable does not establish RAM backing. |

Exact source Q is
[PROPOSAL_FABLE_QUOTA_BALANCING.md](../.docs/improvements/PROPOSAL_FABLE_QUOTA_BALANCING.md).
Its subscription pools and normalized price weights explicitly declare that
they are assumptions, not observed provider billing. Current reservations
measure occupancy and observed failures; they do not reveal remaining account
credits. No usage estimate may be presented as a measured currency charge.

## Additional source coverage

| Key | Source | Retained scope and explicit boundaries |
| --- | --- | --- |
| H1 | [Host Warden](../v4.1.2/wiki/srs/ch01_host_warden.md), FR001–008, NFR-HW-01–03 | E-core/BelowNormal policy, hidden subprocesses, liveness, PEP657 diagnostics and resilient startup. Use actual CPU topology. Exact legacy Hyper-V checkpoint/power-cycle commands are not WSL2 provisioning or permission to reset the user's VM. Chemistry mass lookup is domain-specific. |
| H2 | [Quarantine VM](../v4.1.2/wiki/srs/ch02_quarantine_vm.md), FR001–008, NFR-VM-01–03 | Air-gapped read-only disposable Docker execution, 2 GiB tmpfs, memory/CPU/PID caps, OOM containment and cleanup. Configured hardware admission covers native/prepared/active execution seats; Claude alone has a hard provider cap of 20. Persistent pool members remain single-use once work touches them. Legacy static 32 GiB/12 vCPU/70% Hyper-V topology is not silently declared equivalent to Windows/WSL2. |
| H5 | [Research/TDD](../v4.1.2/wiki/srs/ch05_research_tdd_pivot.md), FR001–008, NFR-TDD-01–03 | Bounded ten-cycle execution, three-failure research trigger, evidence-bound diagnosis, three-pivot terminal report, 30 s shared test/collection deadline and bounded diagnostics. Scientific database/canary/mutation requirements apply when the task's actual domain requires them. |
| H6 | [Knowledge](../v4.1.2/wiki/srs/ch06_dual_wiki_rag.md), FR001–008, NFR-RAG-01–03 | Immutable sources, ratified wiki, FTS5/BM25, UTF-8, zero broken links, ≤400-line documents, synchronized catalog, incremental/atomic rebuild, index ≤4× source and daemon <256 MB. Owner-accepted authenticated retrieval baseline supersedes the previous latency blocker. |
| H7 | [Domain pipelines](../v4.1.2/wiki/srs/ch07_dsp_domain_pipelines.md), FR001–008 | Domain isolation, lifecycle contracts and quota awareness inform extensibility. LMS grading, FERPA, journal-specific LaTeX and science modules are separate products, not general coding-pipeline launch gates. |
| H8 | [Watchdog](../v4.1.2/wiki/srs/ch08_watchdog_sre.md), FR001–009, NFR-SRE-01–03 | Sterile read-only detection, process/lease/database/resource diagnoses, private immutable evidence and bounded out-of-band repair. FR009 contains the owner-approved isolated-failure amendment. Model repair requires a code-repair diagnosis and independent acceptance. |
| I | [Test infrastructure](../v4.1.2/TEST_INFRA.md), §§2–4 | Genuine DB/process/filesystem tests, bounded one-file fracture, modular ≤400-line SRS, matching machine/Mermaid DAG, bounded leaf groups (the owner removes the incorrect 20-node ceiling), test-first execution, isolated tests and verified Git completion. Its method-matrix agent gate is withdrawn. Historical purge counts and 230-test count are not reusable deployment actions or present-day certification. |
| V2 | [Earlier v2 SRS](../SRS-COCHEM-KANBAN-PIPELINE-V2-20260921.md), §3 | Native Windows subscription/CLI versus Linux-container separation, protected SQLite/WAL placement, asymmetric physical evidence and resource isolation. Old services, schemas, ports, models and three-worker defaults are historical. |
| V2b | [Hidden v2 continuation](../.srs_part2.md), §§3.3.7 and 3.6 | Atomic state-transition telemetry and Docker/host/progress/WAL/disk/VRAM health checks with bounded component recovery. |
| V2 plans | [.srs_p3a.md](../.srs_p3a.md), [.srs_p3b.md](../.srs_p3b.md), [.srs_p4.md](../.srs_p4.md), [PART4](../SRS-COCHEM-KANBAN-PIPELINE-V2-20260921-PART4.md) | Historical implementation/test/rollback plans repeat the v2 contract. Old fixed paths, migration row counts, proxy gateways and takeover/purge instructions do not authorize present-day data deletion or reintroduction of retired architecture. |

Duplicate chapter captures under `wiki/plans/PLAN-20260928-host-warden-ladder`
and `docs/archive/knowledge_4.2.6/.sources` are not additional requirements. Files under
`wiki/code`, `.staging`, `.evidence` and prior agent audit/completion reports
are implementation/evidence history, not sources of new normative clauses.

## Implementation and evidence crosswalk

Use [the 4.2.6 ledger](REQUIREMENTS_4.2.6.md) for the detailed pre-change
clause mapping, and [its validation record](VALIDATION_4.2.6.json) for actual
executed checks. Its old planning blockers and performance verdicts are
superseded by the decisions in this document; test history remains unchanged.

| Requirement group | Active implementation to inspect | Evidence needed beyond source presence |
| --- | --- | --- |
| P, H4 | `store.py`, `service.py`, `coding_store.py`, `routing_store.py` | Physical claims/fences, concurrent scatter/gather, crash recovery and native six-chapter execution. |
| Routing, Q, G, I/O | `routing.py`, `routing_store.py`, `hardware_guard.py`, `resource_limits.py`, `admission.py`, `runtime.py` | Actual CLI identity/availability, shared reservation limits, Windows commit/thermal/disk sampling and representative four-worker queue timing. |
| O | `oracle.py`, `runtime.py`, `windows.py`, `transport.py` | Native SYSTEM authority, Job Objects, pipe ACLs, Defender/indexer behavior and physical cleanup. |
| T | `src/cochem/warden/ladder.py` | Real SQLite schema/ownership/Unicode tests; Windows file-handle closure. |
| A, R, I, H5 | `coding.py`, `coding_plan.py`, `coding_store.py`, `planning_governance.py` | Fresh canonical planning binding without phantom gates; physical RED→edit→GREEN→audit→Git receipts and live-model workflow. |
| H2, M | `containers.py`, `runtime.py`, `admission.py`, `ramdisk.py` | Persistent replenished prepared pool, no request-path cold launch, bounded seats/cleanup and native RAM/ACL acceptance. |
| H6 | `knowledge.py`, `knowledge_mcp.py`, authenticated service/client | Profiled backend/transport/MCP time with authentication retained, current clean source selection and index correctness. |
| H1, H8, V2b | `controller_guard.py`, `crash.py`, `cochem_supervisor` | Independent bounded repair, real identity/ACL isolation, rollback and actual Windows 48-hour stability. |

Each canonical SRS chapter records implemented behavior, actual gaps, proposed
remediation and an inline owner-comment space. Suggestions are identified as
proposals; an unapproved suggestion is not silently promoted into a launch gate.

## Latest owner amendment, 2026-10-06

Every model job uses Chapter 06; Gemini-only synthesis and fixed repair models are
withdrawn. Claude has at most 20 concurrent agents; Codex/Agy and coding WBS leaf
counts do not inherit that ceiling. Final asymmetric SRS/WBS reconciliation is
mandatory. Adopt the existing 8 GiB `R:` RAM volume with one-time setup and automatic
startup. All 18 improvement inserts are accepted. The current requirement ledger
and acceptance dashboard distinguish implementation from physical host evidence.
