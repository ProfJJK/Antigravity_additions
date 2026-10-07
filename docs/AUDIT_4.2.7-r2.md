# 4.2.7-r2 SRS and Windows readiness audit

**Verdict: the pipeline is not fully compliant with the canonical SRS and is not ready for full Windows deployment acceptance.** It can undergo controlled Windows prerequisite diagnostics. Confirmed application, deployment and evidence gaps must be repaired before an end-to-end acceptance claim.

Audited on 2026-10-07 against commit `af4e335945fbd5d25198166e16ca32323df65bcb` (tag `v4.2.7-r2`). The canonical specification SHA-256 is `2563c33b7237f099b936a48af6545a674012fc2fb6792f2d7c343e94c9afaf83`. All 252 files in the prior tested-source manifest still match. This audit changes documentation/evidence only; it does not fix production code, amend the SRS, supersede owner decisions or certify an unexecuted host.

The [machine-readable audit](evidence/audit_4.2.7-r2/audit.json) contains a separate disposition, literal requirement, implementation/test trace, finding links and remaining action for **all 110 mandatory requirements across all 18 chapters**. The [script inventory](evidence/audit_4.2.7-r2/script-inventory.json) accounts for **all 615 tracked script files**, including historical copies, tests and unrelated scientific utilities. Source review followed current entry points and requirement paths; inventory/AST parsing is not a line-by-line semantic proof or approval to run every historical script.

The inventory found 17 Python syntax failures among historical, auxiliary or unregistered files, and none in the identified active pipeline/operations Python scope. Their exact paths and parser errors are retained in the inventory. They are not approved current deployment entry points, and their presence is another reason not to describe every script in the repository as ready to run.

## Codex and Agy capacity

**They have no default provider-specific concurrency ceiling, but they are not capped only by observed hardware usage.** Claude alone has a hard twenty-agent provider ceiling. The configured shared hardware ceiling, isolated worker identities and scheduling eligibility apply to all providers.

| Constraint | Current behavior |
| --- | --- |
| Claude provider ceiling | 20 across its models; a lower configured operational limit may apply. |
| Codex / Agy default provider ceiling | None assumed or imposed by default. This does not assert unlimited subscription capacity. |
| Shared execution ceiling | Default 4; configuration supports 1–256. Native and Docker PREPARING/WARM/ACTIVE reservations share it. |
| Worker identities | Example provisions 6. Configured shared slots cannot exceed available configured isolated identities. |
| Live resource limits | CPU/RAM/commit/GPU/thermal/disk/worker headroom can reduce admission to zero. |
| Other eligibility | Optional reviewed operational caps, quota/auth/capability holds, backlog, retry budgets and durable delays. |
| Setup inconsistency | Main login helper currently accepts only slots 1–64 and Codex/Claude; see A427-03. |

The implemented tier order remains exactly: 1–3 Flash → Haiku → Luna; 4–6 Sonnet → Sol → Gemini Pro; 7–9 Opus → Astra low → Gemini Pro; 10 Fable → Astra ultra. Normal synthesis is routed. Existing configured limits are not facts about provider capacity, and concurrency counters do not measure remaining credits.

## Chapter-by-chapter disposition

| Chapter | Disposition | Result / outstanding work |
| --- | --- | --- |
| 01 Authority and scope | Open support-material / cross-cutting gaps | Canonical authority and phantom-requirement withdrawal exist. Reusable setup text still contradicts universal routing; the physical RED counterexample also prevents a blanket ten-phase compliance claim. Retire contradictory current setup instructions; retain the canonical owner decisions and close dependent coding evidence gaps. |
| 02 Topology and trust boundaries | Open code gap + Windows evidence | Protected controller/worker boundaries and physical checks exist. The generated deployment view omits several required observed attestations. Complete the view, then execute SYSTEM/worker/repair ACL, Docker pipe/backend denial, protected path/link and actual SQLite placement checks on Windows. |
| 03 Durable job state machine | Portable evidence; native acceptance pending | Durable claims/fences, leases, output binding, retries, cleanup quarantine and captured contracts have portable regression evidence; this audit found no additional independent counterexample in this chapter. Retain real Windows crash/cancellation/reboot/process-identity evidence and test recovery against the repaired planning/coding gates. |
| 04 Planning DAG | Open code gap + native workflow pending | Routed manifest/chapter/synthesis jobs, blocked gather and coverage are present. Manifest WBS is optional and structured accepted chapter WBS is dropped before synthesis. Fix WBS/output binding, then demonstrate the six-chapter native DAG, overlap, fallback, sibling rejection and crash-safe single gather. |
| 05 Coding execution | Open code gap + native workflow pending | Planning/review, research, sealed tests, asymmetric final SRS/WBS reconciliation and fenced Git integration are implemented. The physical RED predicate accepts a non-assertion exception. Repair RED evidence and rerun a complete Windows native coding task through final reconciliation and verified integration; verify earlier sealed regressions remain green. |
| 06 Complexity routing and spillover | Portable evidence; subscription acceptance pending | The exact four owner tiers, two-route complexity ten, Claude-only twenty cap and persistent ordered spillover/backoff are present. Codex/Agy still share configured hardware/worker limits. Verify exact installed model/effort/auth contracts and real busy/backlog/quota/compatibility observations without manufacturing quota exhaustion or pinning a model. |
| 07 Shared concurrency and hardware | Admission-latency gap + Windows evidence | Shared reservations and resource governors exist; cold maintenance holds the shared admission lock. The example permits four concurrent reservations with six configured identities. Shorten the admission critical section without losing atomic reservations. Measure actual sensors, Job Objects, Warden budget, desktop heap and a 48-hour native soak before raising capacity. |
| 08 Oracle context and circuit breaker | Portable evidence; Windows acceptance pending | Debounce, faceted retrieval, core-rule budgeting, durable delivery generations and velocity containment have portable evidence. Demonstrate native named-pipe ACL/inheritance, >500-event storm containment, descendant/handle cleanup, Defender exclusions and Search attributes on the actual host. |
| 09 RAM workspaces and persistent state | Portable evidence; Windows acceptance pending | Existing-volume adoption preserves the configured 8 GiB R: volume and its one-time startup ownership; repeated manual resizing is not the implemented design. Attest actual ImDisk/AWE/NTFS backing and isolated workspace mappings, persistent logins/state outside scratch, reboot adoption and exact restoration after cleanup. |
| 10 Prepared Docker pool | Two confirmed code/architecture gaps | Persistent single-use prepared containers, FIFO requests, policy checks at execution and cleanup exist. Recovery retains policy-drifted warm entries; cold replenishment serializes admissions. Fix both pool defects, then test Windows engine/pipe identity, reboot recovery, real prepared handoff, pressure revocation and shared-capacity fairness. |
| 11 Knowledge and retrieval | Portable/profile evidence; Windows GUI path pending | Immutable sources, catalog/FTS generations, authenticated search and authority labels exist. Historical 10,001-section profiling retained real corpus/results/authentication and before/after distributions. Repeat against the protected production corpus through Windows stdio/Antigravity, verify ACLs/Unicode/links/relevance and sustained index/RSS bounds. |
| 12 Native CLI and MCP interfaces | Worker setup / checker gap + native evidence | Routed authenticated surfaces and receipt checks exist. The main login helper omits normal Agy workers and the old checker conflicts with the current contract. Finish the supported login/checker path; collect actual Windows capability, auth, exact model/effort/session/process-creation, cancellation and subscription-retention receipts. |
| 13 Telemetry and diagnostics | Portable evidence; lifecycle acceptance pending | Atomic transition telemetry, the dossier SQLite API, Unicode/default handling, bounded crash evidence, consistent snapshots and conservative no-prune archive support exist. Verify native close/unlink semantics, actual process metrics, long-running growth/forecasts and the reviewed production retention/archive policy. |
| 14 Independent detection and repair | Portable evidence; native repair/rollback pending | Sterile observer, independent repair board, bounded budgets, asymmetric reconciliation and trusted outer acceptance exist. Finite Linux challenges do not certify native repair or arbitrary candidates. Execute a reversible Windows failure through containment, bounded repair/review, real live smoke, promotion and rollback; record <=1-second observation and <=5-second initiation timing. |
| 15 Evidence and acceptance | Confirmed evidence/gate gaps; not accepted | Exact historical release evidence is retained, but per-row dashboard verification does not check actual application-source freshness. Passing totals missed the RED counterexample. Reopen affected clauses, bind closure to current installed source and physical target-host evidence, and execute the full Chapter 15 matrix. |
| 16 Performance acceptance | Accepted observations; Windows conditions open | Owner-accepted 29.347 ms queue, 7.731 ms authenticated knowledge and historical 4.85 s cold creation remain acceptable. Queue acceptance is conditional on the exact four-worker Windows topology; cold lock coupling remains code work. Fix admission coupling; benchmark the actual Windows local database workload before any queue redesign. Measure all unwaived resource, deadline and recovery budgets independently. |
| 17 Installation, upgrade and rollback | Confirmed installer/login gaps + Windows acceptance | Protected frozen pipeline/supervisor installs, preserved state/credentials, consistent backup and deployment journals exist. Main worker login and MCP lock usage need correction. Correct release paths/login range/Agy contract and frozen client installation; then run clean install, retained-state upgrade, reboot, smoke and rollback on Windows. |
| 18 Traceability and remediation | Audit reopens release/remediation items | The canonical 110-row ledger exists; its historic green entries are not sufficient proof after new counterexamples or source changes. Current support material also needs reconciliation. Use this audit as supplemental unresolved evidence, preserve historical artifacts and publish new exact-revision closures after repairs and native acceptance. |

## Confirmed findings

P1 identifies a correctness or normal deployment blocker. P2 identifies required implementation, performance-architecture, evidence or operator-support work. All findings below are open in the audited source. Related clauses are distinguished from directly failed clauses in the JSON; a shared reference is not an additional independent failure.

### A427-01 — A non-assertion pytest failure satisfies physical assertion RED (P1)

**Requirements:** S427-CODE-005, S427-CODE-009, S427-AC-002.

**Observed:** A real planned pytest test raises RuntimeError before reaching its requirement assertion. Pytest reports one failure, zero errors and exit 1. The controller accepts that report and advances the actual SQLite coding workflow to EDITING. The preceding test-author gate also accepted the unreachable assertion.

**Scope:** The pytest execution, Git work and SQLite transition are physical. Native identity and controller-container receipt wrappers are the existing explicitly labelled protocol fixtures, not live subscription/Docker attestations. This isolates the incorrect RED predicate.

**Effect:** Implementation can start without demonstrating the required failing assertion; the P3 phase label can overstate what was executed.

**Remediation:** Collect trusted pytest call-phase exception/assertion evidence bound to the sealed test identity and source. Require an executed, meaningful requirement assertion failure for RED; unrelated call exceptions, setup/collection errors and skips must not qualify. Preserve independent test review for semantic relevance.

**Closure:** Run actual pytest counterexamples for RuntimeError before an assertion, setup/collection failure and skips, plus a genuine failing assertion. Confirm only the latter advances the durable workflow, then repeat the complete native Windows coding workflow.

**Code:** [src/cochem_pipeline/coding_store.py:684](../src/cochem_pipeline/coding_store.py#L684), [src/cochem_pipeline/containers.py:148](../src/cochem_pipeline/containers.py#L148), [src/cochem_pipeline/containers.py:188](../src/cochem_pipeline/containers.py#L188).

**Reproduction:** [controller-reproductions.json](evidence/audit_4.2.7-r2/controller-reproductions.json), [nonassertion-red.xml](evidence/audit_4.2.7-r2/nonassertion-red.xml), [reproduce_controller.py](evidence/audit_4.2.7-r2/reproduce_controller.py).

### A427-02 — Document planning loses structured WBS between manifest, chapters and synthesis (P1)

**Requirements:** S427-PLAN-001, S427-PLAN-003, S427-OPS-004.

**Observed:** A manifest without WBS is accepted. Scatter copies chapter identity/title/requirements but no manifest WBS. After a chapter supplies a unique structured WBS task, that task remains in its accepted output but is absent from the entire synthesis payload. Coverage/chapter_hashes bind artifact_text, not the complete accepted structured output.

**Scope:** Actual SQLite planning DAG with explicit existing storage receipt fixtures. Chapter identifiers are present; the defect is missing structured WBS and incomplete output binding, not missing chapter IDs.

**Effect:** Synthesis cannot reliably reconcile or preserve the accepted chapter work breakdown and traceability; a complete document cannot be inferred from a text-only hash.

**Remediation:** Require bounded owned WBS declarations in manifests; carry them through scatter. Gather the accepted structured chapter outputs, full output digests and immutable artifact digests. Bind coverage and synthesis acceptance to those commitments without rewriting historical accepted outputs.

**Closure:** Demonstrate unique manifest/chapter WBS survives scatter/gather; reject missing or changed WBS/output commitments; retain concurrent single-gather/crash tests and run the six-chapter native workflow.

**Code:** [src/cochem_pipeline/store.py:677](../src/cochem_pipeline/store.py#L677), [src/cochem_pipeline/store.py:789](../src/cochem_pipeline/store.py#L789), [src/cochem_pipeline/store.py:809](../src/cochem_pipeline/store.py#L809).

**Reproduction:** [controller-reproductions.json](evidence/audit_4.2.7-r2/controller-reproductions.json), [reproduce_controller.py](evidence/audit_4.2.7-r2/reproduce_controller.py).

### A427-03 — Main worker-login helper does not match the 4.2.7-r2 deployment (P1)

**Requirements:** S427-DEPLOY-001.

**Observed:** The main helper defaults to Pipeline4.2.7 while the installer creates Pipeline4.2.7-r2; it accepts only codex/claude and slots 1-64 while provisioning/configuration supports 256. The actual Python main login parser rejects --provider gemini.

**Scope:** Source/argument-parser confirmation, not a Windows login execution. A generic reviewed Gemini login implementation exists in cochem_supervisor.windows and can accept a layout/slot; the packaged supervisor PowerShell helper hardcodes repair. The normal worker setup path is incomplete, not an absence of all Gemini login code.

**Effect:** Default instructions can target an old or missing installation; the ordinary setup helper cannot authenticate Agy worker accounts or slots above 64.

**Remediation:** Unify the primary helper with the reviewed Gemini login-contract implementation, correct its r2 installation default, and use the same 1-256 identity range as provisioning. Retain protected paths, per-account authentication and actual subscription checks.

**Closure:** From a fresh Windows installation execute the documented helper for all three CLIs, verify each intended worker account retains its own subscription, test boundary slot parsing and preserve logins across reboot/upgrade.

**Code:** [scripts/login_pipeline_worker.ps1:5](../scripts/login_pipeline_worker.ps1#L5), [scripts/login_pipeline_worker.ps1:8](../scripts/login_pipeline_worker.ps1#L8), [scripts/install_pipeline_windows.ps1:8](../scripts/install_pipeline_windows.ps1#L8), [src/cochem_pipeline/windows.py:1048](../src/cochem_pipeline/windows.py#L1048), [src/cochem_pipeline/windows.py:1087](../src/cochem_pipeline/windows.py#L1087), [src/cochem_supervisor/windows.py:875](../src/cochem_supervisor/windows.py#L875), [scripts/login_supervisor_worker.ps1:10](../scripts/login_supervisor_worker.ps1#L10).

**Reproduction:** [controller-reproductions.json](evidence/audit_4.2.7-r2/controller-reproductions.json), [reproduce_controller.py](evidence/audit_4.2.7-r2/reproduce_controller.py).

### A427-04 — Restart recovery retains a WARM container with mismatching resource policy (P2)

**Requirements:** S427-DOCKER-001, S427-DOCKER-002.

**Observed:** Prepare an actual 4096 MiB container, change its limit to 512 MiB with docker update, create a fresh runner on the same registry and run startup reaping. The record remains WARM although physical limit verification rejects it.

**Scope:** Actual Linux Docker. Execution verifies limits again before source injection: this is a recovery/readiness defect, not proof of sandbox escape. All containers created by this probe were removed; none remain owned.

**Effect:** Restart inventory can report unusable prepared capacity and defer rejection until a job acquires it, contrary to reattestation before adoption.

**Remediation:** Reattest engine identity, exact owned physical object, captured image/policy, readiness and expiry before retaining a WARM member. Quarantine/remove mismatches and replenish through maintenance.

**Closure:** Exercise resource/image/ownership/readiness drift, engine restart and controller reboot against a real engine. Healthy unused members survive; mismatching ones never appear as available prepared capacity.

**Code:** [src/cochem_pipeline/containers.py:580](../src/cochem_pipeline/containers.py#L580), [src/cochem_pipeline/containers.py:640](../src/cochem_pipeline/containers.py#L640), [src/cochem_pipeline/containers.py:1052](../src/cochem_pipeline/containers.py#L1052).

**Reproduction:** [docker-reproductions.json](evidence/audit_4.2.7-r2/docker-reproductions.json), [reproduce_docker.py](evidence/audit_4.2.7-r2/reproduce_docker.py).

### A427-05 — Cold background replenishment holds the global admission lock (P2)

**Requirements:** S427-DOCKER-001, S427-PERF-004.

**Observed:** With capacity four, one WARM and one PREPARING container, and no active native jobs, a queued native manifest receives no claim while maintenance holds the shared lock. The same job claims after replenishment finishes. The recorded maintenance/claim interval was 1.341 seconds on this host.

**Scope:** Actual Docker/SQLite and thread synchronization. No inline create was found and no 30-second test deadline failure was demonstrated. This is indirect cold-preparation admission delay, not a claim that the accepted historical cold timing is itself a failure.

**Effect:** Moving creation to another thread does not fully remove cold-start cost from waiting foreground jobs when all admissions require the same held lock.

**Remediation:** Reserve PREPARING capacity atomically under shared admission, release the short critical section for Docker operations, then reacquire it to verify/publish or quarantine. Preserve pressure revocation, FIFO fairness and durable uncertain reservations.

**Closure:** During deliberately slow real cold replenishment, prove an eligible native job and an available matching warm job can claim spare capacity without waiting for that creation. Simultaneously prove no oversubscription, starvation or premature release under pressure/failure.

**Code:** [src/cochem_pipeline/admission.py:108](../src/cochem_pipeline/admission.py#L108), [src/cochem_pipeline/admission.py:187](../src/cochem_pipeline/admission.py#L187), [src/cochem_pipeline/admission.py:230](../src/cochem_pipeline/admission.py#L230).

**Reproduction:** [docker-reproductions.json](evidence/audit_4.2.7-r2/docker-reproductions.json), [reproduce_docker.py](evidence/audit_4.2.7-r2/reproduce_docker.py).

### A427-06 — Acceptance dashboard does not invalidate evidence when deployed source changes (P2)

**Requirements:** S427-OPS-015, S427-OPS-018.

**Observed:** Copy the release/spec/evidence/source into an isolated directory. Replace copied store.py with a failing program. Every dashboard requirement status remains identical: 35 verified and 75 unverified. It validates evidence-file and specification hashes plus a revision string, not the actual current application against the tested-source manifest.

**Scope:** The real repository was not modified by this counterexample. Aggregate full acceptance was false both before and after. Protected deployment ACLs are useful, but they do not bind old evidence to later authorized upgrades/repair.

**Effect:** A green per-requirement row can describe old source after a change. Neither its green label nor the historical 35 count establishes semantic compliance with every clause.

**Remediation:** Bind evaluation to the actual installed release/source/dependency manifest and captured contract. Preserve historical evidence while marking changed, missing, stale or wrong-platform closure evidence open; let new counterexamples reopen previously green clauses.

**Closure:** Change/remove an installed module or lockfile in an isolated deployment fixture and observe stale evidence; unchanged byte-identical deployments retain only their correct platform-scoped evidence. Audit counterexamples remain visible until hash-bound repair verification closes them.

**Code:** [src/cochem_pipeline/operator_views.py:60](../src/cochem_pipeline/operator_views.py#L60), [src/cochem_pipeline/operator_views.py:112](../src/cochem_pipeline/operator_views.py#L112), [docs/evidence/source_4.2.7-r2.json](../docs/evidence/source_4.2.7-r2.json).

**Reproduction:** [controller-reproductions.json](evidence/audit_4.2.7-r2/controller-reproductions.json), [reproduce_controller.py](evidence/audit_4.2.7-r2/reproduce_controller.py).

### A427-07 — Deployment view omits required observed identity and pipe/RAM attestations (P2)

**Requirements:** S427-OPS-002.

**Observed:** The view exposes configured SYSTEM plus PID/instance, a configured Docker endpoint plus engine ID, and configured RAM path/size plus a boolean. Worker account drift is considered, but the view does not include observed controller token identity, Docker pipe-server identity/access-denial attestations, or actual RAM device/backing/path drift.

**Scope:** Confirmed by the projection source. Lower-level attestation functions exist and must be connected; this does not show the lower-level protection is absent. The view correctly marks unobserved information as not healthy.

**Effect:** The accepted topology improvement cannot show all of the configured-versus-observed trust-boundary facts or their drift.

**Remediation:** Project protected current controller, pipe server/engine, worker/repair denial and RAM attestation records with timestamps, source hashes, unknown/stale states and explicit drift comparisons.

**Closure:** On Windows change each relevant configured/observed identity or backing fact in controlled fixtures and demonstrate the diagram displays evidence, unknowns and drift without exposing secrets.

**Code:** [src/cochem_pipeline/operator_views.py:244](../src/cochem_pipeline/operator_views.py#L244), [src/cochem_pipeline/deployment.py](../src/cochem_pipeline/deployment.py).

### A427-08 — MCP installer resolves dependencies outside the frozen release lock (P2)

**Requirements:** S427-DEPLOY-001.

**Observed:** The unprivileged MCP installer uses pip install -e .[mcp]. It does not consume uv.lock; the protected pipeline/supervisor installers do use frozen uv installs.

**Scope:** Source-confirmed reproducibility gap; no fresh Windows dependency resolution was performed and no actual dependency failure is asserted.

**Effect:** The Antigravity MCP client can resolve a different dependency set than the one whose physical tests were published.

**Remediation:** Install the MCP client from the reviewed frozen dependency lock and recorded Python/toolchain policy, preserving existing client configuration and registered controller mapping.

**Closure:** Build a fresh Windows MCP environment from the release; compare installed dependency/version hashes with the lock and rerun the authenticated MCP contract/stdio tests.

**Code:** [scripts/install_mcp_windows.ps1:99](../scripts/install_mcp_windows.ps1#L99), [scripts/install_pipeline_windows.ps1:27](../scripts/install_pipeline_windows.ps1#L27), [uv.lock](../uv.lock).

### A427-09 — Reusable setup guidance and the old live MCP checker conflict with current routing (P2)

**Requirements:** S427-GOV-001, S427-TRACE-001.

**Observed:** The reusable start_skill text still says final synthesis remains Gemini 3.1 Pro. The older verify_cli_mcp.py creates an unregistered random workspace, optionally supplies a pinned model and asks the CLI to write directly. Current compatibility submission requires an exact registered project and rejects model pinning; models now run inference-only and the controller applies validated artifacts.

**Scope:** Source-confirmed stale guidance/checker, not evidence that current runtime synthesis ignores Chapter 06. The old checker is referenced by historical 4.2.1 documentation; it is not valid current acceptance.

**Effect:** An operator or later cloud agent following these shipped paths receives contradictory routing instructions or a checker that fails before the intended routed workflow.

**Remediation:** Replace reusable setup guidance with the current canonical contract. Explicitly retire/archive the legacy checker or rewrite it around a bounded routed preflight/registered acceptance project, retaining real native receipt verification and controller-owned file changes. Do not weaken exact-project/model guards to make the old checker pass.

**Closure:** Search active setup/launch instructions for fixed-model synthesis and stale direct-CLI claims, exercise the current checker through the authenticated board, and clearly label remaining historical documents.

**Code:** [config/cloud-environment.proposed.json](../config/cloud-environment.proposed.json), [scripts/verify_cli_mcp.py:30](../scripts/verify_cli_mcp.py#L30), [src/cochem_mcp/config.py:41](../src/cochem_mcp/config.py#L41), [src/cochem_mcp/jobs.py:81](../src/cochem_mcp/jobs.py#L81), [docs/MCP_4.2.1.md:134](../docs/MCP_4.2.1.md#L134).

## Evidence and limits

The existing dashboard reports 35 verified / 75 unverified rows. That is an artifact/platform catalog evaluation, **not a reliable assertion that 35 complete requirements are semantically proved**. The new RED/WBS/source-freshness counterexamples reopen affected conclusions. This audit preserves historical test counts and evidence instead of rewriting them into failures or pretending the counterexamples were already covered.

| Executed scope | Passed | Skipped | Failures / errors |
| --- | ---: | ---: | ---: |
| Prior exact-source full release suite (historical evidence) | 2,438 | 35 | 0 / 0 |
| authority-planning-tests.xml (this audit) | 258 | 18 | 0 / 0 |
| routing-resources-tests.xml (this audit) | 425 | 5 | 0 / 0 |
| ram-docker-tests.xml (this audit) | 165 | 13 | 0 / 0 |

These test scopes overlap and must not be added together. Passing them did not prevent the newly reproduced failures. The separate negative audit programs intentionally demonstrate the present defects; their successful exit means the counterexamples were reproduced, not that compliance passed. The stored nonassertion-red.xml contains the expected physical failing pytest case. The Docker probe used an actual local Linux engine and exact image and left zero audit-owned containers. It did not test Windows Docker Desktop/WSL2.

The reproduction programs are the exact programs executed, retained with their original cloud workspace/image paths. Their outputs and hashes are recorded in audit.json. For another host, adapt only the workspace/interpreter/explicit reviewed Docker endpoint and image, and retain the new command/environment/output; do not call protocol fixtures real provider receipts. Do not run the Docker program against shared/operator containers: it changes only its own newly prepared containers.

The historical knowledge profile used 10,001 sections and 900 interleaved requests per implementation; its observed mean changed from 6.997 to 6.550 ms and its maximum worsened. Those scoped results remain useful, not a universal Windows latency promise. The owner-accepted 29.347 ms queue, 7.731 ms authenticated knowledge and 4.85 s cold creation remain accepted observations. The exact Windows four-worker queue measurement is still a launch condition; no queue redesign is authorized merely by the old Linux result.

No live subscription inference, native Windows execution, native desktop-heap observation or 48-hour soak was performed in this audit. No upgrade-preview mutation or Docker sandbox escape was established. The default Agy example deliberately rejects placeholder flags; actual supported headless/auth/model/effort contracts must be obtained from installed CLIs, never guessed.

## Ordered remediation and Windows acceptance

1. Repair physical RED and planning WBS/full-output binding. Add physical counterexamples to the protected regression/outer evidence where appropriate; keep sealed-test, asymmetry and captured-contract requirements intact.
2. Correct primary login release paths, full slot range and reviewed Gemini login path. Freeze MCP client dependencies. Reconcile active setup guidance and retire/rewrite the incompatible historical checker.
3. Reattest prepared members on recovery and remove cold Docker calls from long admission critical sections while retaining durable shared reservations and pressure/fairness handling.
4. Connect actual topology attestations to the operator view and bind acceptance to installed-source/dependency manifests. Reopen stale or counterexample-invalidated rows; do not weaken the SRS to turn them green.
5. On Windows 11/WSL2, verify protected SYSTEM/worker/repair identities and ACL/link boundaries, actual database/WAL placement, reviewed sensors and WSL2 caps, existing 8 GiB R: startup/backing, per-account subscription retention and exact native CLI contracts. Unsupported capabilities remain visible holds.
6. Verify Docker pipe/backend denial and actual engine identity; exercise pressure, pool refill/reboot, Oracle storm, cancellation/crash/reaper and handle cleanup at the measured shared capacity. Run the separate four-worker queue benchmark at the actual production database location with realistic claims/heartbeats/Oracle/completions.
7. Execute a routed six-chapter native planning workflow and a complete native coding workflow through physical RED/GREEN, P1–P10 evidence, asymmetric SRS/WBS reconciliation and verified Git integration. Retain exact receipts, artifact/source/test/image/policy commitments and actual fallbacks.
8. Execute a reversible independent repair/review/promotion/live-smoke/rollback scenario with durable paid budgets; measure observation/containment timing. Complete the 48-hour Warden/native handle/resource/desktop-heap soak and protected knowledge GUI/stdio measurements.
9. Publish hash-bound exact-release per-requirement closure evidence. Only then reassess full acceptance. Native evidence cannot be replaced by Linux passing counts, configuration examples, model prose or a green dashboard row.

This report is supplemental audit evidence, not a ratified normative wiki chapter or a changed source-precedence rule. Historical scripts are inventoried to expose their status; current deployment must use the reviewed current entry points.
