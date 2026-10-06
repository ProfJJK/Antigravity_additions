# 4.2.5 requirements and evidence ledger

> Superseded by the [4.2.6 audit](REQUIREMENTS_4.2.6.md). Further code gaps were found, including forgeable candidate-side acceptance reports. The historical results below do not certify full SRS compliance.

This ledger maps the active 4.2.5 implementation to the supplied requirements
and identifies the evidence and remaining limits for each clause. Code,
portable tests and actual Linux Docker execution do not establish native
Windows deployment, live provider execution or long-term host performance.

## Sources and precedence

The four primary user-supplied documents were read in full:

| Key | Source | Interpretation |
| --- | --- | --- |
| O | [Oracle_SRS_WBS.md](../Oracle_SRS_WBS.md) | Active Oracle, privilege, retrieval, concurrency and recovery requirements; WBS repeats the same obligations. |
| P | [Pipeline_4_2_0_Architecture.md](../Pipeline_4_2_0_Architecture.md) | Active planning DAG, ownership, fencing, synthesis and five acceptance criteria. |
| T | [full_dossier_untruncated.md](../full_dossier_untruncated.md) | Complete telemetry API, schema, edge cases, constraints and acceptance cases. |
| T-short | [full_dossier.md](../full_dossier.md) | Same telemetry scope and test list; line 34 explicitly contains a truncation marker. T supplies the missing detail. |
| R | [v4_pipeline_reconstruction_plan.md](../v4_pipeline_reconstruction_plan.md) | Native coding orchestration, staged changes and per-worker RAM-disk intent. Old fixed model ordering and 19/20-worker examples conflict with newer instructions. |
| M | [RAM_Disk_Reparse_Point_Standard.md](../.docs/improvements/RAM_Disk_Reparse_Point_Standard.md) | Adopted historical RAM-volume mount standard, preservation prerequisite and physical Claude prompt directory. |
| A | [V4_CORE_MODIFICATION_AUTHORIZATION.md](../V4_CORE_MODIFICATION_AUTHORIZATION.md) | Historical coding isolation, chunk/Git/audit invariants and Docker cleanup requirements; it is not new permission to modify unrelated legacy components. |
| H1–H5, H7–H8 | [Archived SRS chapters](../v4.1.2/wiki/srs/00_skeleton.md) | Relevant host, VM, concurrency, queue, coding and watchdog clauses are individually dispositioned below. Domain-specific science/pedagogy requirements are not inferred as general orchestration work. |
| I | [Archived test infrastructure](../v4.1.2/TEST_INFRA.md) | General test-first, real-evidence, fracture, container and verified-merge obligations; historical destructive migration and chemistry scenarios are separate. |
| V2 | [Earlier pipeline SRS](../SRS-COCHEM-KANBAN-PIPELINE-V2-20260921.md) | Explains native Windows CLI versus Linux-container separation, evidence-bound asymmetric audits and resource isolation. Its proposed container control plane and obsolete registry do not override the current protected Windows controller. |
| V2b | [Earlier SRS continuation](../.srs_part2.md) | Transition telemetry (§3.3.7, lines 312–343) and Docker/host watchdog checks (§3.6, lines 659–735); old service names, schema, ports and restart budgets are not current deployment facts. |

The user's corrected routing is authoritative: **1–3 Flash → Haiku → Luna;
4–6 Sonnet → Sol → Gemini Pro; 7–9 Opus → Astra `low` → Gemini Pro;
10 Fable → Astra `ultra`, with only those two candidates.** Native Windows
Python and subscription-authenticated CLI execution remain mandatory. The
planner's maximum of four simultaneous agents supersedes historical examples
of six containers, 19/20 workers and 60-agent swarms. Six chapter identities may
exist while only four execute. Final planning synthesis remains exact
`gemini-3.1-pro`; no alias remapping or silent model substitution is allowed.

The protected native controller and CLI authentication stay on Windows.
Container isolation applies to code/test execution; Linux containers cannot
run native `claude.exe`/Agy binaries or inherit a Windows subscription merely
by receiving a path. SQLite/WAL state, credentials, release records and
controller secrets must remain persistent and outside volatile RAM scratch.

## Evidence vocabulary

- **Baseline code/test**: active 4.2.4 code and named regression tests exist;
  this is not a new 4.2.5 test result.
- **Implemented; portable evidence**: active code and named executed checks
  exist, with their limits stated. This does not establish Windows acceptance.
- **Host pending**: needs Windows/WSL2/Docker/native provider execution. Linux
  tests or argument inspection cannot establish it.
- **Conflict / conditional / excluded**: the exact historical clause and its
  disposition are stated; it is neither silently dropped nor marked complete.

The final combined Linux suite recorded **1,826 passed, 29 skipped**, with
zero failures or errors. The [4.2.5 release notes](RELEASE_4.2.5.md) separately
record copied-source acceptance and build verification; those runs and earlier
release or component totals are not added together. Evidence includes real SQLite/Git,
physical subprocess/filesystem checks, measured host probes and actual Linux
Docker execution. Native/Docker completion records used only to exercise storage
protocols are explicitly identified as fixtures and are rejected by the live
coding-acceptance verifier.

**D07 has separate warm/cold results:** a prepared single-use fixture measured
0.4446s, within the 1.5s target; cold creation measured 4.85s, above target.
Receipts record `warm_pool_used`, `startup_seconds` and `startup_sla_met`.
Neither measurement establishes Windows, large-input or representative-load
performance. Native Windows isolation, RAM backing, driver/sensor behavior,
live subscription models and a 48-hour stability run remain host acceptance.

## Primary Oracle clauses

| ID | Exact source and obligation | Active target / validation | Status |
| --- | --- | --- | --- |
| O01 | O:11,16,69–72: central daemon at SYSTEM tier with real-time event velocity monitoring. | `cochem_warden_oracle.py`, `cochem_pipeline/runtime.py`, `windows.py`; `test_windows_contract.py`, `test_runtime_monitor.py`. | Baseline code/test; SYSTEM execution host pending. |
| O02 | O:17–18,71–72: strictly more than 500 events/sec trips immediately even for an `IN_PROGRESS` agent; bypass state gating and terminate its process tree with a Windows Job Object. | `oracle.py`, runtime asynchronous reaper, native Job Objects; `test_oracle.py` rolling boundary, concurrent storm and real-child termination cases. | Baseline code/test; native kill/descendant proof host pending. |
| O03 | O:21–23,76–77: after termination release handles/locks and move work to `FAILED` or `PENDING_RETRY`. | `runtime.py`, `store.py`, `windows.py`; lease fencing, cleanup barrier and real-process exit tests. | Baseline code/test; Windows handle release/relaunch remains host pending. |
| O04 | O:26–27,55,79: transient directives via SQLite payload or named pipe, never `.warden_hints.md`. | Oracle outbox and controller-to-worker stdin; `test_context_is_persisted_in_sqlite_and_inherited_without_hint_files`. | Baseline code/test. |
| O05 | O:26–27,79: exclude every agent workspace from Defender; validate ghost-locking mitigation including Search Indexer interference. | `RamdiskManager.ensure`, `RamWorkspace.validate`, `exclude_tree_from_indexing` and native layout enforce exact Defender exclusions plus `NOT_CONTENT_INDEXED` readback on RAM roots/new entries. | Implemented; portable path/driver contracts pass. Actual Defender/Search Indexer behavior and cross-account files remain Windows pending. |
| O06 | O:30–32,51,66–68: cryptographic out-of-band watermark tracking inaccessible to workers. | Private `oracle.py` SQLite DB plus Windows ACLs; `test_tracking_is_physically_separate_private_and_uses_wal`. | Baseline code/test; cross-account denial host pending. |
| O07 | O:32,41,53–54,68: 500ms temporal debounce, consolidate events, only then check/generate watermarks. | `oracle.py` debounce/latest-event/watermark tests and real watchdog monitor. | Baseline code/test. |
| O08 | O:35,58–59: Aho-Corasick faceted retrieval, including overlapping matches. | `oracle.py`; suffix/Unicode/all-required-facet tests. | Baseline code/test. |
| O09 | O:35,60: knapsack caps complete injected context. | `oracle.py`; exhaustive comparison and UTF-8/XML accounting tests. | Baseline code/test. |
| O10 | O:36–37,61–63: reserved baseline percentage retains non-evictable core rules, securely wrapped in `<oracle_directive>`. | `oracle.py`; reservation/core-fit/XML escaping tests. | Baseline code/test. |
| O11 | O:40–41,48–50: job board WAL and `busy_timeout=5000`. | `store.py`, Oracle, telemetry; real SQLite checks. | Baseline code/test; newer explicit 5s timeout supersedes H4's old 30s value. |
| O12 | O:11,52–55: observe filesystem and DB changes without an injection feedback loop. | Runtime watchdog plus ordered store events; `test_runtime_monitor.py`, store event/context-update tests. | Baseline code/test; newly mounted paths need actual monitoring proof. |

## Primary planning DAG clauses

| ID | Exact source and obligation | Active target / validation | Status |
| --- | --- | --- | --- |
| P01 | P:5,10–12,37: one ordinary request creates `MACRO_PLANNING_REQUEST` and a deterministic SQLite DAG without repeated delegation. | `store.py`, `service.py`, `server.py`; actual MCP→HTTP→SQLite tests in `test_service.py`. | Baseline code/test; ordinary Antigravity request host pending. |
| P02 | P:13,32: manifest generates WBS chapters; successful completion atomically scatters distinct `CHAPTER_DRAFT` jobs. | Store manifest validation/transaction; `test_invalid_manifest_rolls_back_scatter_and_completion`. | Baseline code/test. |
| P03 | P:14,38: concurrent chapter workers subject to hardware limits and absolute maximum four. | `hardware_guard.py`, `resource_limits.py`, `runtime.py`; measured admission, pre-resume Job limits, real resource snapshots and store contention tests. | Implemented; portable policy checks pass. Native Windows enforcement host pending. |
| P04 | P:14,23: one chapter per worker; prevent access to sibling chapters, not merely request compliance in a prompt. | Separate native identities, per-slot ACLs, durable identity assignment and private DB. | Baseline code/test; Windows sibling/profile denial host pending; RAM/container paths must preserve it. |
| P05 | P:14: store immutable outputs in job-board payload artifacts. | Store mutation guards; `test_artifacts_and_outputs_immutable_and_identical_completion_idempotent`. | Baseline code/test. |
| P06 | P:15,33: synthesis waits for all completed siblings, then Gemini 3.1 Pro gathers the real chapters. | Store barrier, worker prompt and exact native model metadata validation. | Baseline code/test; live Gemini synthesis host pending. |
| P07 | P:19: WAL readers monitor while workers write. | Real SQLite concurrency/store event tests. | Baseline code/test; production load host pending. |
| P08 | P:20: every attempt gets a unique ID, lease and fence; retry changes fence and rejects late zombie output. | Store ownership/lease/heartbeat checks and SQL triggers; abrupt-child-death tests. | Baseline code/test. |
| P09 | P:24: require structured `requirements_traced`, `wbs_tasks_defined`, `artifact_uri`; free prose is insufficient. | Store payload schema plus worker JSON parser; `test_worker_contract.py`. | Baseline code/test. |
| P10 | P:25: synthesis requires verified hashes of all N chapters; a chapter process cannot impersonate synthesis. | Controller-created receipts and completion authority; wrong-hash/non-Gemini rejection tests. | Baseline code/test; native identity proof host pending. |
| P11 | P:31: add `parent_job_id` and `fencing_token`. | Namespaced pipeline tables; migration preserves legacy tables and artifacts. | Baseline code/test; legacy rows are not fabricated as new accepted attempts. |
| P12 | P:34: Claude/Codex MCPs accept structured node payloads. | `{provider}_submit_node` in `cochem_mcp/server.py`; real FastMCP schema/session tests. | Baseline code/test. Standalone development submission does not claim a protected DAG lease. |
| P13 | P:38: six chapters, four-agent limit, at least two overlapping accepted executions. | `test_six_chapter_scatter_with_four_worker_limit_and_real_overlap`; `scripts/verify_pipeline_acceptance.py`. | SQLite/thread overlap baseline; overlapping live native-provider chapters host pending. |
| P14 | P:39: sibling submission rejected by a database constraint. | `test_chapter_ownership_enforced_in_python_and_database_trigger`. | Baseline code/test. |
| P15 | P:40: simultaneous last chapters release exactly one logical synthesis. | Atomic gather and unique synthesis record; concurrent completion test. | Baseline code/test. |
| P16 | P:41: crash at launch/commit boundary recovers without duplicate accepted outputs. | Durable attempts/fences, cleanup guards, reaper and supervisor launch journal. | Baseline code/test; actual Windows launch/commit crash acceptance host pending. |

## Telemetry clauses from both dossiers

All rows target [`src/cochem/warden/ladder.py`](../src/cochem/warden/ladder.py)
and [`pipeline_tests/test_recovery_telemetry.py`](../pipeline_tests/test_recovery_telemetry.py).
The six dossier acceptance areas are independently covered by real SQLite
tests; their presence is baseline evidence, not a new full-pipeline claim.

| ID | Exact source and obligation | Evidence / remaining boundary |
| --- | --- | --- |
| T01 | T:53–71,155,167; T-short:35,47: exact `emit_recovery_event(conn=None,event_data=None)->int`, sole dict, injected connection, string/Path, keyword and zero-argument dispatch. | Baseline subprocess/default-path and injected-connection tests. |
| T02 | T:73–92,156; T-short:15–16,36: automatically create exact `id INTEGER PRIMARY KEY AUTOINCREMENT`, `timestamp TEXT NOT NULL DEFAULT(datetime('now'))`, `tier INTEGER NOT NULL`, `action TEXT NOT NULL`, `details TEXT NOT NULL`. | Exact schema/PRAGMA and timestamp tests; not a substituted connection. |
| T03 | T:41–42,92,139–141,158,168; T-short:38,48: serialize full unmodified payload with `ensure_ascii=False`, including tier/action/custom nested metadata. | Deep comparison and literal Unicode retention tests. |
| T04 | T:119–127,158; T-short:38: absent or explicit-null tier/action become `1`/`unspecified_recovery`; otherwise `int`/`str` conversion. | Parameterized missing/null/coercion tests. |
| T05 | T:130–136: detect a sole dict only when `event_data is None`; do not confuse invalid falsy connection values with defaults. | Invalid-input/no-DB-mutation and empty/default dispatch tests. |
| T06 | T:94–95,143–145,159; T-short:16,39: positive integer `lastrowid`; monotonic IDs across tiers and no reuse. | Real insert/delete/sequential tests. |
| T07 | T:105–107,112,157,169; T-short:37,49: injected connections stay open; owned connections close in `finally`, including errors. | Real lifecycle/error tests; Windows unlink/handle semantics host pending. |
| T08 | T:108–110 and O:41: explicit commit; WAL/5s busy timeout; no subprocess, LLM or slow external I/O inside the write transaction. | Independent reader/transaction tests and source inspection. In-memory SQLite uses its native memory journal. |
| T09 | T:44–45,101–104,160,170; T-short:18,40,50: no mock/MagicMock/monkeypatch, `pass` or `NotImplementedError`; real SQLite acceptance. | AST compliance test plus real connection tests. |
| T10 | T:113: UTF-8 text operations. | Unicode serialization and explicit encoding tests/source. |
| T11 | T:14–18,164–170; T-short:14–18,44–50: named API/WBS scope and execute all six acceptance areas. | Root telemetry sink remains independent of archived broader FSM. The current regression suite includes this API; no additional recovery-FSM implementation is inferred. |
| T12 | T:20–25 and T-short:20–25: telemetry task excludes recovery FSM transitions, separate FastMCP exposure, direct Hyper-V cmdlets, remote export and guest named-pipe loops. | Explicit scope boundary for this API, not a blanket exemption from separately requested container/coding work. |

## RAM-disk requirements and safety obligations

| ID | Source / obligation | Baseline and 4.2.5 target | Evidence required for closure |
| --- | --- | --- | --- |
| R01 | M:7,15: real ImDisk RAM volume mounted at the documented workspace path, not a variable pointing at an ordinary disk directory. | Implemented in `ramdisk.py`: `RamdiskConfig`, `RamdiskManager.ensure/inspect`, `_native_volume` and IOCTL parsing; default canonical directory and AWE nonpageable backing. | Portable driver-response/path contracts pass; actual ImDisk/AWEAlloc mount, physical backing and native I/O remain Windows pending. |
| R02 | M:16: mount target must be empty; preserve existing content before clearing. | `backup_mount_contents` uses same-volume rename; PREPARING/READY ledger and `mount_recovery_action` preserve originals and reject unknown mounts. | Real filesystem backup/link-rejection and lifecycle contract tests in `test_ramdisk.py` pass. Native partial-provision/reboot acceptance remains pending. |
| R03 | R:29–35: isolated ephemeral scratch for each worker, preventing NVMe test/cache write storms. | `RamWorkspace`, native launch integration, runtime and `CodingCoordinator` use owned RAM descriptors, cwd and temporary directories. | Portable tests reject ordinary Linux directories pretending to be RAM; native per-worker RAM I/O and sibling denial remain pending. |
| R04 | R:34: redirect Claude's large mutable project caches away from NVMe. | `bind_native_caches` selectively maps the native `.claude/projects` cache to owned RAM, preserves previous contents and requires native `projectsDirectory`/`configDirectory` confirmation; `cache_write_observation` counts real files/bytes. | Implemented with parser/path tests and actual Claude help/status contract inspection, without inference. Windows binding/write verification pending; no claim about undocumented caches or eliminating all SSD writes. |
| R05 | R:35: native subscription credentials stay persistent; no token loss after scratch destruction. | Native profile/auth home remains persistent; selective project-cache binding rejects replacing the credential/config directory. | Portable selection/junction checks pass. Native login retention across cleanup/reboot remains Windows pending. |
| R06 | M:17: Claude prompt payload files remain on ordinary physical storage because `realpath` may resolve a mount outside workspace policy. | Native prompt delivery remains stdin; persistent protected logs and authentication stay outside RAM. Supported Claude no-session-persistence/debug-file flags control specific ephemeral output. | Actual CLI help checked; native Windows prompt/cache interaction remains pending. No broad sandbox bypass. |
| R07 | M:7 versus current anti-reparse ACL checks: allow only the administrator-verified RAM mount boundary, not arbitrary worker junctions. | `RamWorkspace.validate`, exact mount reparse/IOCTL identity, per-slot ACL validation and `windows.launch_worker` admit only the verified boundary. | Malformed/foreign/nested link and lifecycle contract cases pass; real native ACL/reparse replacement checks remain pending. |
| R08 | Ephemeral storage must not erase authoritative progress: P:14,20,41; O:31,41; R:35. | Authoritative SQLite/WAL, routing/fences, receipts, release and repair ledgers remain private persistent state; RAM ledger records boot/volume identity. | Existing real SQLite/recovery tests plus RAM lifecycle tests; combined native reboot/retry acceptance pending. |
| R09 | O:27,79 and P:14: new scratch shares Defender/resource/concurrency controls. | RAM allocation checks physical-memory reserve; hardware governor measures RAM/commit/disk/thermal pressure; exact RAM slot Defender and indexer attributes are validated. | Portable resource/ramdisk suites pass; actual Windows sensor, exclusion and admission enforcement pending. |

## Docker execution and lifecycle requirements

| ID | Source / obligation | Baseline and 4.2.5 target | Evidence required for closure |
| --- | --- | --- | --- |
| D01 | A:15; H2:30; I:114: actual ephemeral code/test container execution. | Implemented `DockerRunner.run`, `CodingCoordinator` CODE_TEST execution, runtime ownership/cleanup; new image builder and immutable image policy. | Real Linux container tests pass. Physical Linux container execution is tested separately from labelled native/storage fixtures; full Windows/native coding acceptance remains pending. |
| D02 | H2:21,30: `--rm --network none --read-only --tmpfs /tmp:rw,size=2g`. | `DockerRunner.create_arguments` plus `_verify_limits` enforce offline/read-only/nonroot/tmpfs/capability constraints. | Real Linux tests inspect actual configuration and exercise network denial, read-only filesystem and tmpfs; Windows engine pending. |
| D03 | H2:34,52–60: 4GiB memory, 2 CPUs, 512 PIDs, `cap_drop=ALL`, `no-new-privileges`; concurrency bounded. | `DockerPolicy`, durable reservations and the shared admission lock account for native work plus prepared/active containers within the hard four-seat limit. Native processes receive Job Object limits before resume; readiness reserves the largest native/container hard memory cap per seat, without subtracting RAM scratch twice. | Linux real container limits and concurrent reservation tests pass; native Windows limits and combined host pressure pending. |
| D04 | H2:35,69: purge per-task scratch/tmpfs/intermediate data after exit; scan orphan scratch every 60s. | `_remove`, `reap_orphans`, `census` and runtime30s independent maintenance remove only owned containers/scratch, including prepared slots. | Real Linux timeout/OOM/orphan/cancel-path tests; full controller-crash and Windows lifecycle acceptance pending. No global prune. |
| D05 | A:25,29,32: census containers and reap owned orphans when native worker dies. | Container lease labels/registry bind job/attempt; runtime passes active attempts to reaper and confirms cleanup before reuse. | Real Linux tests cover late objects after removal, uncertain create surviving 404, unknown-owner quarantine and four warm containers draining before a native SQLite claim. Ambiguous creation needs exact physical cleanup or verified full Windows reboot plus unchanged attested engine identity; native reboot acceptance remains pending. |
| D06 | H2:41,68: isolate OOM to offending task; record exit 137 and memory evidence; quarantine/retry without cascading host failure. | Real container OOM metadata/exit137, bounded logs, cleanup evidence and quarantine-required result propagate to runtime. | Real Linux OOM test passes with physical teardown. Windows host/peer survival and recovery still require host evidence. |
| D07 | H2:40: request-to-test startup <=1.5s. | Prepared single-use warm container measured 0.4446s, satisfying 1.5s for that fixture. Cold creation measured 4.85s and remains above target; earlier loaded cold runs 3.8–6.9s. | Warm/cold distinction is explicit in `warm_pool_used`, `startup_seconds`, `startup_sla_met`. No blanket SLA pass; Windows, large source trees and representative load pending. |
| D08 | A:35; Dockerfile:12,17–28; V2:343–418: known image/dependency versions. | Reviewed `pipeline-tests.Dockerfile` and `build_pipeline_sandbox.py`; runtime accepts only exact local allowlisted sha256 image IDs. | Image built and actual imports/execution pass on Linux. Each deployment must build/verify its own configured image; old scientific image is not selected implicitly. |
| D09 | V2:128–146: native Windows CLIs cannot run in Linux containers; live SQLite/WAL cannot be mounted across an unreliable Windows/Linux filesystem boundary. | Native Windows subscription plane remains separate. Containers receive bounded source archives, no host binds/socket/auth. `attest_docker_pipe_server` queries the actual live server PID/token/protected executable against reviewed operator/SYSTEM authority and exact backend paths; `verify_docker_access_boundary` then checks selected and known existing Desktop API pipes for worker read/write/duplex denial. `verify_repair_docker_boundary` also gates the separate repair identity before a paid reservation and every repair/candidate child, without blocking watchdog recovery. | Linux container isolation and portable configuration/ledger checks pass; opt-in worker/repair denial tests require actual Windows SYSTEM and do not count as passed here. |
| D10 | V2:148–164: bounded memory/PIDs/filesystem isolation does not guarantee a BSOD-proof host. | `HardwareGuard`/`GovernorState`, real resource probes, container caps and native `ResourceLimits` enforce measured admission and emergency teardown. | Portable hardware tests and real Linux container-pressure checks pass; native limits/sensors remain pending. No BSOD-proof claim. |
| D11 | V2:299–307,568–588: reproducible isolated test environment separate from production state. | New image/build helper, operator-owned test argv, `DockerRunner` authenticated-by-controller source snapshot/JUnit collection, and coding test stage. | Real tests reject zero/skipped/forged/collection-only evidence and source mutation; full native coding task pending. |
| D12 | H2:28–29: Ubuntu24.04 Hyper-V guest, static32GiB, dynamic memory off,12vCPU/70%. | Historical deployment topology; current user specifies Windows11+WSL2. Exact Hyper-V guest settings are **not** silently reported implemented by Docker Desktop/WSL2. | Target-host WSL2/VM configuration and resource-cap evidence remain pending. No static 32 GiB/12 vCPU/70% Hyper-V provisioning is claimed. |

## Coding pipeline and acceptance evidence

The 4.2.4 planning-only baseline is extended by `coding.py`, `coding_store.py`,
`coding_git.py`, the runtime and coding API/CLI. Legacy `.scripts/task_work_loop.py`
and `v4.1.2/` are not used as evidence that the new path executes successfully.
The storage tests explicitly label their native/Docker completion fixtures;
actual Git, SQLite, hashing and fencing are exercised, with real container
execution validated separately. A complete native coding run remains pending.

| ID | Source / obligation | Active target and evidence | Remaining boundary |
| --- | --- | --- | --- |
| C01 | R:19–22: native code-forge orchestration, no delegation back to broken legacy loop. | `CodingCoordinator`, `CodingStoreMixin`, runtime, service/MCP and `code` CLI create explicit coding stages and requirement-bound outputs. Real SQLite/service checks exercise submission and state. | Live native coding task pending; planning JSON alone is not execution evidence. |
| C02 | R:24–27,44–47: worker receives bounded assignment and emits a staged diff. | `observed_changes` reads physical candidate bytes and creates before/after/diff hashes; `GitStager` writes actual verified commits under corrected routing. | Real Git tests pass; native editor invocation and RAM staging require Windows acceptance. |
| C03 | I:108–109,40–41; A:17,61: N=1 WBS fracture;20–100-line context chunks; reject >500-line or >80% whole-file rewrites and preserve artifact protocol. | Each approved N=1 leaf targets one registered source file. The complete-file artifact_blocks protocol preserves unchanged bytes while the controller enforces at most 100 changed lines per chunk and cumulative >500-line/>80% rewrite rejection for original files with at least 20 lines. Small targeted fixes are allowed; new/short files have explicit handling. Complete-file transport does not authorize whole-file replacement. | Real file/hash boundary tests pass. Preferred 20–100 context is not a mandatory 20-line minimum; padding small fixes is not required. |
| C04 | I:113: test-first gate before implementation. | `CODE_TEST_AUTHOR` seals tests before `CODE_EDIT`; `CODE_TEST` observes independent Docker results. Default `red_green` requires new tests and an actual assertion failure, not collection/import errors. Explicit `preserve_behavior` requires a passing existing regression baseline. | Store/Docker tests bind planned pytest cases to exact immutable file/module/class/function source, including parameterized cases; dynamic/imported/inherited/ambiguous identities fail closed. Full native test-author→editor acceptance remains pending. |
| C05 | A:57,62; H5:20,28: preserve phase structure and bound Execute→Audit→Repair to 10 cycles. | `coding_plan.TDD_PHASES` and fenced coding transitions retain P1 PLAN, P2 RESEARCH, P3 TEST_FIRST_RED, P4 CODE, P5 TEST, P6 AUDIT, P7 IMPROVE, P8 PLAN_NEXT, P9 REFINE and P10 TEST_AND_AUDIT. Durable cycle/pivot budgets are separate; skip records require specific justified conditions. | Real SQLite/Git workflow checks include the full phase ledger, two-leaf lineage, research and no-op-refinement proof deferral. Native phase execution remains pending. |
| C06 | H5:29–30,41: after 3 consecutive failures freeze generation and enter research/triage; diagnostic context <2000 tokens. | `_coding_retry` persists failures and requires `CODE_RESEARCH` after three; `bounded_coding_diagnostics` retains immutable full receipts and emits a diagnostic projection strictly under 2000 UTF-8 bytes with source hashes. | Actual SQLite failure-state tests and diagnostic-boundary tests exist. Byte bound is a conservative prompt limit, not a claimed measured native tokenizer count. Role routing follows the corrected user matrix. |
| C07 | H5:31–33,91–92: research dossier, root-cause triage and at most 3 methodological pivots, then hard abort/report. | Research verifies 2–20 exact quotes from immutable test diagnostics and project technical sources, binds the three failure hashes, requires a changed strategy and persists a maximum of three pivots. Structured root-cause categories cover implementation, test assumption, interface contract, dependency, environment, requirements and unknown. A pivot must change strategy; escalation records a durable RESEARCH_HOLD. A test-assumption pivot restarts sealed test authoring from the leaf baseline while retaining earlier evidence. | Native research task pending; external scientific searches/PHYSICS WALL apply to scientific work and are not fabricated by general coding source inspection. |
| C08 | V2:113–121; A:53,56,63: asymmetric evidence-bound planning and implementation audit; only PASS can dispatch/promote. | Per-file `CODE_REVIEW` binds actual path, file/diff/test hashes, nonempty findings and objective coverage; producing provider/model pair cannot approve itself. Missing/rejected audit blocks Git promotion. `CODE_PLAN_REVIEW` independently validates approved plan/artifact hashes before dispatch. | Helper and storage tests exercise rejected/missing/self-approved plan and implementation audits; real native reviewer acceptance remains pending. |
| C09 | A:14,67–68; I:116; R:44–47: baseline/result Git commits and verified merge under serialization. | `capture_repository` and `GitStager` retain baseline/result commits, serialize by common Git-directory lock, publish verified coding refs and advance only a matching un-checked-out branch by atomic compare-and-swap. | Real Git tests pass, including dirty/untracked preservation, linked worktrees and two-process CAS. External Git does not share the controller lock; reserve the integration branch from concurrent checkout. |
| C10 | I:117; H4:36: complete only after verified result/merge; clear lease authority atomically. | `complete_coding` binds stage evidence to attempt/fence/route; durable integration intent and final CAS share the active fence. `READY_TO_INTEGRATE` holds auto-disabled, checked-out or changed-baseline work instead of claiming completion. | Real SQLite/Git cancellation, stale receipt, missing-intent and integration checks pass; native crash-boundary acceptance pending. |
| C11 | A:50–55: hidden-path evidence search, bounded revision/research confidence, path containment. | Git tree enumeration includes tracked hidden paths; safe-path/reparse/link validation and registered editable/test paths constrain evidence. Verified source quotes/hashes replace self-attested confidence. | Real malicious-path/Git tests pass; native filesystem boundaries remain host pending. Untracked files are preserved, not silently ingested as trusted source. |
| C12 | T:101–104; I:28–32; V2:113–121: genuine file/DB/process/test evidence; no counterfeit success. | Real SQLite/Git/process suites and actual Docker executions are separately recorded. Native/Docker completion records used to test storage protocols are explicitly identified as fixtures. | No real-provider success or full Windows pipeline acceptance is inferred from those fixtures. |
| C13 | H5:40,42; I:88–93: bounded tests and meaningful feature/boundary/interaction/workload coverage. | `DockerRunner` owns argv, source manifest and report collection; real Linux tests reject empty/forged/skipped/collection-only outcomes, source mutation and stale evidence, and exercise timeout/OOM cleanup. | Real Docker evidence passes for the tested fixtures. Project-specific coverage and Windows/container cancellation load acceptance remain pending; old 230-test totals are not reused. |
| C14 | I:105–112: modular SRS≤400lines, machine+Mermaid acyclic graph, leaf schema and≤20-task batches. | `validate_plan` generates ≤400-line modular SRS chapters/skeleton, matching graph.json/graph.mmd, FractureManifest.json, one-file leaves and mirrored ≤20-node batches. `CODE_PLAN` and independent `CODE_PLAN_REVIEW` persist accepted artifacts before implementation. | Executed helper and real SQLite workflow checks cover malformed traceability, cycles, size limits, hashing and asymmetric audit. A live model-generated plan remains pending. |

## Hardware, queue, supervisor and migration cross-check

| ID | Source / obligation | Active baseline and disposition |
| --- | --- | --- |
| H01 | H1:29; H3:36; I:38: native subprocesses no-window, appropriate process group and full descendant ownership. | `windows.launch_worker` uses `CREATE_NO_WINDOW`/`CREATE_NEW_PROCESS_GROUP` and applies kill-on-close plus `ResourceLimits` before ResumeThread. Native structures/contracts are tested; physical Windows launch remains pending. Seven PowerShell scripts parse without execution under Microsoft PowerShell 7.4.13; Windows 5.1 cmdlets remain target-host checks. |
| H02 | H1:28: E-core affinity and BelowNormal priority; historical host mask `0x00FF0000`. | `native_cpu_sets`/`select_e_core_affinity` query actual Windows EfficiencyClass topology and require available group-0 E cores. Job Object affinity/BelowNormal settings are read back before resume; an explicit mask must match actual E cores. Portable topology/structure tests pass; heterogeneous native host test pending. No blind historical mask reuse. |
| H03 | H3:31,34: director 4096 MiB/worker 512 MiB V8 heaps. | Native jobs have default aggregate 2048 MiB/20% CPU/16-process limits. `node_heap_options` supplies sanitized 512 MiB old-space and 16 MiB semi-space flags; actual Node 24.19.0 reported a 560 MiB total V8 heap, so 512 MiB is not claimed as total process/heap memory. Rust/Bun are not claimed Node-limited. The old internal 20-agent Node director is superseded. |
| H04 | H3:35: 100–500 ms launch/poll jitter. | Separate `launch_delay_seconds` and `poll_delay_seconds` are used by native launch/polling, independent of routing backoff. Bounds, configured fixed intervals and inherited Node-flag sanitization are tested in `test_resource_limits.py`. Native timing under load remains pending. |
| H05 | H1:40–42; H3:42–44:50MB/5% supervisor targets,3s VM escalation,15% desktop heap,48h handle stability and10ms internal messages. | Performance evidence pending. Fixed20-agent/internal-message and Hyper-V timing measures are conditional on obsolete topology; resource/handle stability still matters for current hard 4. |
| H06 | H4:28–31: WAL/NORMAL,30s busy timeout,1800s leases,5s heartbeats. | WAL/atomic claim implemented; O:41 overrides timeout to5000ms. Current renewable30s default lease and bounded heartbeat differ from old1800s; document this explicit design, test expiry/refresh/cleanup rather than assert literal parity. |
| H07 | H4:29,35–36,41–43: atomic claims, idempotent injection, clear leases on terminal transition, no duplicate claims. | Real store contention/immutability tests exist. Historical<5ms ten-daemon performance unverified and not guaranteed by a passing functional test. |
| H08 | H4:32–33:3-failure poison pill and Urgency×Fidelity capability matrix. | Active configurable task-failure budgets exist; availability skips do not spend ordinary failures. Corrected deterministic complexity routing replaces old selector; do not claim literal old urgency/fidelity routing. |
| H09 | H4:34; H8:29,44: out-of-band watchdog reads DB without write authority and is independent of mutable pipeline code. | Separate protected supervisor/venv and read-only monitor exist; it imports its own protected support modules. Exact old stdlib+psutil-only rule differs from newer independent protected-supervisor architecture. |
| H10 | H8:30–37: process death, stale lease, DB corruption, memory slope, immutable evidence and bounded recovery. | Supervisor `_component_status`, `_wal_health`, process/heartbeat/DB/lease monitoring and immutable evidence distinguish code defects from infrastructure/resource holds. Runtime publishes hardware/container health; historical FAILED-always-collapse is not reused. |
| H11 | H1:35; H8:93: automatic startup and recovery if supervisor/warden dies. | SYSTEM scheduled tasks, durable native ownership records, same-boot uncertainty hold and reboot recovery exist; native execution host pending. |
| H12 | R:35; P:20,41; current4.2.3–4.2.4 contracts: upgrades preserve identity/auth, work artifacts/fences, charged repair attempts and quarantine. | Current SQLite backup/budget migration and protected release journal exist. New installs must preserve them; no reset-to-zero upgrade or scratch placement. |
| H13 | Current repair-supervisor contract, [4.2.3 guide](SUPERVISOR_4.2.3.md):2 repairs/incident,4/day,30min cooldown, first bounded restart then direct native repair. | The durable supervisor ledger retains paid budgets. Frozen repair_execution_limits also bounds each repair/acceptance child, with native readback and measured readiness before reservation; an unavailable prerequisite holds repairs while recovery continues. This separate repair child is not falsely counted inside the pipeline four-seat scheduler. |
| H14 | Current repair-supervisor contract: protected supervisor/tests/dependencies/schema cannot self-modify; verified versioned release and rollback. | Existing allowlist, immutable external tests, hash validation, journal recovery and post-restart workflow/heartbeat probe. Docker/RAM integration must not grant these paths to model workers. |
| H15 | V2b:314–343: telemetry for every state transition, atomic with the state change; elapsed time, CPU, actual provider/model and GPU status, no detached success report. | `JobStore.set_transition_telemetry` publishes a copied host sample outside transactions; `_event` records it atomically with the event, observation time, elapsed time and actual available native receipt/model fields. CPU scope is explicitly host; unavailable samples/receipts remain null. `test_execution_integration.py` verifies copied real host samples and rollback of both state and telemetry in an aborted SQLite transaction. Native per-process resource measurements are not implied by host-scoped samples. |
| H16 | V2b:676–686: engine/container health, service response, fresh heartbeat, progressing queue, gateway, bounded WAL, disk reserve and VRAM headroom. | `containers.health` and runtime maintenance publish infrastructure evidence. Supervisor checks actual completion progress separately from scheduler events, WAL size (default 256 MiB), hardware holds and independent authenticated HTTP health matching heartbeat PID/instance. Active-stall age uses the immutable CLAIMED event and actual phase timeout plus cleanup grace, not lease-renewed updated_at. Physical SQLite/host-probe and monitor checks pass; native sensors/thresholds/load acceptance remain pending. |
| H17 | V2b:671–674,703–714: repeated failure strikes, bounded restart backoff, diagnose before restart and recover the failing component rather than unrelated services. | `RecoveryLedger` requires three failing observations and 30 seconds before one durable automatic attempt per component; healthy resets/restarts do not refund it. The supervisor saves bounded private diagnostic JSON and its hash before recovery. Only the fixed `com.docker.service` start is permitted; a start does not prove engine health. Warden retains its bounded restart policy; infrastructure holds block paid code repair. Real SQLite/concurrency tests pass, Windows SCM remains pending. Diagnostics are heartbeat/DB/resource/component metadata, not a claimed py-spy/native thread dump. |

## Explicit conflicts and unrelated historical domains

| Source | Disposition |
| --- | --- |
| R:26,31; M:4,11; H2:34; H3:23,32 |19/20/60 workers and6containers are superseded by P:38 hard 4. They are not extra pools allowed outside the cap. |
| R:41; H3:37; historical pristine model registry in A:60 |Old Fable→Opus→Gemini1.5 and1–4/5–8/9–10 bands are superseded by the user's exact corrected four tiers. Final planning synthesis remains Gemini3.1Pro. |
| R:21–22 |Fable-only directorship and bypassing Gemini everywhere conflict with native three-provider routing and required Gemini synthesis; preserve native orchestration intent without adopting obsolete model exclusivity. |
| V2:138–146,299–307; H2:10,15,28–29 |Historical container control plane/separate Hyper-V Ubuntu VM differs from current Windows SYSTEM controller+WSL2 code/test plane. Exact old image/Compose/service names are not silently claimed installed. |
| H1:30–32,42,89–90; H8:32 |Hyper-V15s health polls/45s restart/checkpoint restore and1860s lease thresholds are conditional on that old topology. Do not invoke destructive VM commands or use old thresholds for current renewable leases. |
| H1:34,65–68; H2:22,32–33; H5:31,34–35; I:31–35,115 |Chemistry masses/canaries/PySCF/XTB/ASE/mutation score85% apply to scientific numerical tasks. H2 explicitly labels some as removed hallucinated requirements. They do not become universal pipeline acceptance gates. |
| H2:31; H7:29–34,41,43 |FERPA student data, Academic Press, LMS/R-exams/LaTeX and DSP scaffolding are separate application domains, not requested coding-environment prerequisites. |
| I:101–103,119 |Historical purge of547pending jobs/42files and migration of a specific old queue are not reusable installation steps. Preserve user state; no destructive purge inferred from archived task counts. |
| I:93; archived evidence reports |Old230test target and old PASS claims are historical. They cannot certify current code, Docker readiness, target-host RAM mounts or subscription execution. |
| [Thermal incident resolution](../COCHEM-COUNCIL-RES-176-8D-THERMAL-TRIP-SRS-SPOOFING-RESOLUTION-20260920.md):126–129 |Physical nonempty evidence and bounded attempts remain relevant; its older fixed5-execution limit does not replace current configured ordinary-failure budgets or falsely charge availability waits as code failures. The historical incident report is not a verified measurement of the current host. |
| M:15 and current protected-slot paths |The adopted literal D: mount and current ProgramData isolation need an explicit deployment mapping. Changing path strings or weakening all reparse checks does not satisfy RAM backing or sibling isolation. |
| T:20–25 |Telemetry dossier exclusions apply to that API. They do not erase separately requested coding, Docker or RAM-disk deliverables. |

## Remaining target-host acceptance

1. On Windows, provision and verify actual ImDisk/AWE RAM backing, trusted
   mounts, per-account ACLs, Defender/indexer behavior, native authentication
   retention and the protected Docker backend/client boundaries.
2. Run a real coding task through the approved plan, test-first implementation,
   bounded research/repair, independent audits and applied Git integration.
   Retain the completed workflow and run `verify_coding_acceptance.py`; local
   storage fixtures are not a substitute for native model evidence.
3. Execute the six-chapter planning acceptance with at least two overlapping
   accepted native runs and final Gemini synthesis. Verify actual Agy headless,
   authentication and result contracts plus account/model availability; help
   output and offline effort parsing are not inference evidence.
4. Exercise native cancel/crash/OOM/mount/engine-loss/reboot boundaries and
   verify physical cleanup, preserved fencing, durable state and repair budgets.
   Existing Linux crash/container evidence does not prove Windows behavior.
5. Measure representative host pressure, warm and cold startup, sensor limits
   and the 48-hour handle/resource stability targets. The measured cold path
   exceeds 1.5s; a successful warm fixture does not close that general SLA.
   Target-host WSL2/VM configuration remains to be inspected separately.

These are concrete limits on the validation claim, not hidden completed work.
The source implementation and executed Linux evidence can be delivered while
native acceptance remains pending; they cannot honestly certify the entire
Windows pipeline.
