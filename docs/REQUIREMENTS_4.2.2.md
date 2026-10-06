# 4.2.2 requirements and evidence map

This implementation follows the four supplied specifications restored from the
`origin/v3.0.0` source snapshot `c557157`, while retaining the 4.2.1 CLI bridges.
The architecture document's **4.2.0** title is the supplied requirement source;
it is not a reason to replace the current checkout with that branch.

`full_dossier.md` is visibly truncated at lines 33–34. Its retained requirements
and test cases map to `full_dossier_untruncated.md`, which supplies the complete
API, schema, invariants, and edge cases. The truncated copy contributes no
different implementation requirement.

## Evidence classifications

- **Verified on Linux:** the named test was actually executed against the real
  resource described. This does not establish Windows behavior.
- **Verification pending:** implementation or tests are assigned to the named
  target, but this matrix has no passing execution evidence yet.
- **Windows host required:** a target-host observation is required, even where
  Linux tests can exercise the surrounding logic.
- **Explicitly excluded:** outside the supplied specification's task boundary.

Paths listed below identify implementation and verification targets. A listed
test path is not by itself a claim that the test exists, passes, or proves the
whole requirement. This map must be updated with final suite output and host
evidence before declaring complete deployment readiness.

The integrated Linux suite ran with
`python -m pytest pipeline_tests mcp_tests -q --junitxml=/tmp/cochem-422-tests.xml`:
**420 passed, 3 skipped**, with two dependency deprecation warnings, in 23.54s.
The per-file counts below were parsed from that JUnit report. Frozen dependency
synchronization, lockfile verification and the `cochem-4.2.2-py3-none-any.whl` build also succeeded. The final wheel runtime/worker files match the tested source bytes.

| Suite | Result | Resource actually exercised |
| --- | --- | --- |
| `pipeline_tests/test_acceptance_report.py` | 15 passed | Live acceptance report validator contracts; test data does not establish a real provider workflow. |
| `pipeline_tests/test_config.py` | 79 passed | Configuration/type validation, distinct identity pool and required native Gemini subscription-probe contract. |
| `pipeline_tests/test_hardware_guard.py` | 21 passed | Actual OS CPU/memory telemetry and filesystem disk measurements. |
| `pipeline_tests/test_oracle.py` | 23 passed | Matching/knapsack/debounce logic, private SQLite, concurrent storms and a physical child-process reaper. |
| `pipeline_tests/test_recovery_telemetry.py` | 12 passed | Real file-backed/in-memory SQLite and fresh-process default-path dispatch. |
| `pipeline_tests/test_runtime_monitor.py` | 2 passed | Real Linux watchdog observes 600 physical file writes and reaps a physical child; real symlink cleanup preserves outside targets. |
| `pipeline_tests/test_service.py` | 24 passed | Actual FastMCP frontend sessions through real loopback HTTP and SQLite using a deliberately limited database controller; no native worker execution. |
| `pipeline_tests/test_store.py` | 25 passed | Real SQLite, concurrent threads, physical Python subprocesses, abrupt process exit, durable identity binding and retry limits. |
| `pipeline_tests/test_windows_contract.py` | 8 passed, 3 skipped | Configuration rejection and non-Windows fail-closed behavior; three actual Windows security/process tests skipped. |
| `pipeline_tests/test_worker_contract.py` | 65 passed | Pure prompt, routing, structured-result and subscription-status parser contracts; no provider inference. |
| `mcp_tests/test_claude_subscription.py` | 40 passed | Subscription adapter/parser behavior with explicitly identified test processes. |
| `mcp_tests/test_config.py` | 2 passed | Standalone bridge configuration validation. |
| `mcp_tests/test_jobs.py` | 12 passed | Physical CLI protocol emulator processes, persistence, cancellation and timeouts. |
| `mcp_tests/test_legacy_status.py` | 3 passed | Legacy status/submission semantics; polling does not advance execution. |
| `mcp_tests/test_providers.py` | 72 passed | Native adapter command, authentication and result parsing contracts with explicit test boundaries. |
| `mcp_tests/test_router.py` | 5 passed | Provider selection and rejected fallback behavior. |
| `mcp_tests/test_server.py` | 12 passed | Actual FastMCP sessions and physical, explicitly emulated CLI processes; structured drafts leave real SQLite DAG unchanged. |

The skipped tests require real Windows: provisioned SYSTEM/layout ACL checks,
cross-account private/sibling reads, and Job Object descendant termination.
This integrated test run is not a completed Windows acceptance run or evidence
of inference by any live provider. The SYSTEM `Runtime` is not substituted
with a fake OS to declare those requirements passed.

## Oracle SRS and WBS

Every core requirement and repeated WBS deliverable is accounted for below.

| ID | Required behavior and source | Implementation target | Validation classification |
| --- | --- | --- | --- |
| O-01 | Central daemon evaluates task state, retrieves context, handles runaway workers and recovery. [Purpose, line 11](../Oracle_SRS_WBS.md#L11). | `src/cochem_pipeline/oracle.py`, `worker.py`, `runtime.py`; daemon entry point. | Oracle, store, HTTP service and pure worker contracts passed in the integrated Linux suite. Actual SYSTEM runtime/Oracle/native-process integration: Windows host required. |
| O-02 | Velocity breaker operates at SYSTEM privilege, trips only above 500 events/sec, overrides state gates during `IN_PROGRESS`, halts monitoring and asynchronously terminates the rogue process via Job Objects. [Lines 16–18, 69–72](../Oracle_SRS_WBS.md#L16-L18). | `oracle.py`, `windows.py`, `scripts/install_pipeline_windows.ps1`. | Oracle threshold/rolling-window/concurrent-trip logic and a real Python child reaper verified on Linux, including a locked SQLite writer. A real watchdog observer also processed 600 physical file writes and triggered asynchronous termination of a physical child. SYSTEM identity and real Job Object containment: Windows host required. |
| O-03 | Reaper runs after breaker termination, releases orphaned handles/DB locks, and transitions `IN_PROGRESS` to `FAILED` or `PENDING_RETRY`. [Lines 21–23, 75–77](../Oracle_SRS_WBS.md#L21-L23). | `oracle.py`, `store.py`, `windows.py`, worker recovery. | Store expiry/retry transitions and Oracle real-child reaping verified on Linux. Integrated worker reaper verification pending. Windows handle release and post-kill DB usability: Windows host required. |
| O-04 | Transient hints use SQLite payloads or Named Pipes instead of `.warden_hints.md` or other raw files. [Lines 26–27, 55, 79](../Oracle_SRS_WBS.md#L26-L27). | Oracle payload construction and worker delivery in `oracle.py` / `worker.py`. | Store context persistence/inheritance, no hint-file creation and shared worker prompt payloads verified on Linux; native Windows runtime delivery remains unverified. SQLite delivery satisfies the stated alternative; a new Named Pipe implementation is not independently required. |
| O-05 | All agent working directories are excluded from Defender interference; validate exclusions. [Lines 27, 78–79](../Oracle_SRS_WBS.md#L27). | `scripts/install_pipeline_windows.ps1`; worker slot directories. | Windows host required: check actual Defender exclusions and event/IO behavior for every configured slot. |
| O-06 | Cryptographic watermarks prevent duplicate injection and feedback loops, using a hidden SQLite tracking table inaccessible to active agents. [Lines 30–31, 51, 66–67](../Oracle_SRS_WBS.md#L30-L31). | `oracle.py`, protected controller DB, Windows identities/ACLs. | Oracle hash/idempotency, restart persistence and physically separate private tracking DB verified on Linux. Actual worker denial of DB, WAL and SHM access: Windows host required. A hidden table name alone does not provide isolation. |
| O-07 | Watermarks are evaluated strictly after a 500ms debounce. [Lines 32, 68](../Oracle_SRS_WBS.md#L32). | Oracle debounce scheduling and watermark check order. | Verified on Linux: trailing-debounce/latest-event checks and restart-persistent duplicate suppression in `test_oracle.py`. |
| O-08 | Aho-Corasick automaton performs faceted rule retrieval. [Lines 35, 58–59](../Oracle_SRS_WBS.md#L35). | `oracle.py` retrieval implementation. | Verified on Linux: suffix/overlapping/Unicode matches and all-required facet matching in `test_oracle.py`. |
| O-09 | Knapsack context budget caps injected rule size. [Lines 35, 60](../Oracle_SRS_WBS.md#L35). | `oracle.py` budget selection. | Verified on Linux: exact deterministic knapsack compared with exhaustive search; full UTF-8/XML budget accounting. |
| O-10 | Reserve a baseline percentage for non-evictable foundational safety directives and format them securely with `<oracle_directive>` XML tags. [Lines 36–37, 61–63](../Oracle_SRS_WBS.md#L36-L37). | `oracle.py` mandatory directives, budget reservation and XML escaping. | Verified on Linux: core retention, reserved-allocation fit, non-spendable reserved slack and XML escaping in `test_oracle.py`. |
| O-11 | `job_board.db` uses WAL with `busy_timeout=5000` for concurrent access. [Lines 40–41, 48–50](../Oracle_SRS_WBS.md#L40-L41). | `store.py`, Oracle connections, telemetry sink. | Telemetry, store and Oracle WAL/timeout checks verified on Linux; concurrent store commits exercised. WAL/timeout reduce contention; they do not eliminate filesystem permission errors. |
| O-12 | Buffer and consolidate filesystem and DB IO events over a 500ms temporal window before generation and watermarking. [Lines 41, 52–54](../Oracle_SRS_WBS.md#L41). | `oracle.py`, runtime event source. | Oracle coalescing/generation order, durable outbox/ack replay and real Linux watchdog event delivery verified; a 600-file storm triggers the physical-child reaper. Store ordered event batches and context-update feedback suppression also passed. Windows monitoring/native-runtime integration remains unverified. |

## Multi-agent planner architecture

| ID | Required behavior and source | Implementation target | Validation classification |
| --- | --- | --- | --- |
| P-01 | One ordinary orchestrator request creates a deterministic SQLite DAG, without repeated manual delegation. Root type is `MACRO_PLANNING_REQUEST`. [Lines 5, 10–12, 37](../Pipeline_4_2_0_Architecture.md#L10-L12). | `store.py`, `server.py`, `worker.py`, runtime/CLI entry point. | Atomic/idempotent workflow creation and actual FastMCP-to-authenticated-HTTP-to-SQLite submission/status/cancellation verified on Linux, including concurrent duplicate requests. Full SYSTEM runtime and Antigravity request: Windows host required. |
| P-02 | `MANIFEST_GENERATOR` creates the WBS chunk breakdown and scatter atomically inserts distinct `CHAPTER_DRAFT` tasks following manifest completion. [Lines 13, 32](../Pipeline_4_2_0_Architecture.md#L13). | Store manifest commit and worker scatter controller. | Store scatter/rollback, immutable manifest order, prompt structure and deterministic Codex/Claude routing verified on Linux. Native Windows/provider integration remains unverified. |
| P-03 | N chapter workers run concurrently under hardware guardrails, each owns exactly one chapter, and immutable artifact outputs are stored in SQLite payloads. [Line 14](../Pipeline_4_2_0_Architecture.md#L14). | `hardware_guard.py`, `store.py`, `worker.py`, Windows slot allocation. | Store four-slot concurrent acceptance, chapter ownership and immutable artifacts verified on Linux; hardware guard uses real OS measurements. Native Windows/provider concurrency still requires host evidence. |
| P-04 | `SYNTHESIS` is eligible only when every chapter of its parent workflow is `COMPLETED`; Gemini 3.1 Pro gathers chapter outputs and produces the final document. [Lines 15, 33](../Pipeline_4_2_0_Architecture.md#L15). | Store gather barrier and structured Gemini synthesis delivery through MCP/worker. | Store barrier and exact chapter-hash validation verified on Linux using clearly identified contract data. Actual Gemini 3.1 Pro synthesis and final artifact: Windows/Antigravity host required. |
| P-05 | WAL readers can monitor progress while worker writes occur. [Line 19](../Pipeline_4_2_0_Architecture.md#L19). | `store.py` connection/transaction discipline. | Store WAL and concurrent completion transactions verified on Linux. Production monitoring alongside native workers still requires integration evidence. |
| P-06 | Every execution has a unique `attempt_id`, hardware lease, and fencing token; expired/crashed attempts can retry with new tokens, and stale completion is rejected. [Line 20](../Pipeline_4_2_0_Architecture.md#L20). | Store attempt/lease transitions, guard, worker commit. | Verified on Linux: lease expiry, fresh attempt/token, heartbeat, stale application/SQL rejection and abrupt real-process exit in `test_store.py`. |
| P-07 | Chapter workers cannot read sibling chapters. [Line 23](../Pipeline_4_2_0_Architecture.md#L23). | Dedicated Windows standard worker identities, per-slot ACLs, protected controller DB, and no identity assignment to different sibling chapters within a workflow. | Durable identity binding, concurrent binding rejection and retry identity retention verified in real SQLite on Linux. Actual POSIX symlink cleanup preserves files outside the worker slot. Cross-account reads/profile access: Windows host required (native isolation test skipped). A prompt or working directory alone does not satisfy this. |
| P-08 | Chapter completion schema requires `requirements_traced`, `wbs_tasks_defined`, and `artifact_uri`; free text alone is invalid. [Line 24](../Pipeline_4_2_0_Architecture.md#L24). | Store structured payload validation and Codex/Claude worker adapters. | Store schema, worker structured-output parsing and actual standalone MCP structured-node contracts verified on Linux. Native Windows CLI worker integration remains unverified. |
| P-09 | Synthesis cannot be claimed by a chapter worker and must use verified output hashes from all N chapters. [Line 25](../Pipeline_4_2_0_Architecture.md#L25). | Controller-owned completion tokens, immutable artifact hashes and synthesis validation in `store.py` / `worker.py`. | Store non-Gemini synthesis and wrong-hash rejection plus output/artifact mutation rejection verified on Linux. Real process identity and host ACL denial still require Windows/provider evidence. |
| P-10 | Add `parent_job_id` and `fencing_token` columns to the job board. [Line 31](../Pipeline_4_2_0_Architecture.md#L31). | `store.py` schema/migration. | Store schema includes both fields in namespaced pipeline tables and preserves existing legacy tables; preservation/idempotency verified on Linux. Existing legacy rows are not silently imported as accepted pipeline attempts. |
| P-11 | Codex and Claude MCP integration accepts structured scatter/gather payloads. [Line 34](../Pipeline_4_2_0_Architecture.md#L34). | `server.py`, worker provider adapters, retained 4.2.1 CLI bridges. | Verified on Linux: real FastMCP sessions expose the structured Codex/Claude node schemas and pass manifest/chapter payloads through physical, explicitly emulated CLI processes; the existing DAG remains unchanged and control-plane fields are rejected. `mcp_tests/test_server.py`: 12 passed. These standalone development tools do not claim protected leases/completion. Real subscription CLI handoffs and Windows worker integration: target host required. |
| P-AC2 | Six chapters, four-agent ceiling, and at least two overlapping accepted chapter executions. [Line 38](../Pipeline_4_2_0_Architecture.md#L38). | Guard/worker/store integration. | Verified on Linux for store concurrency: six chapters, four overlapping accepted thread executions, fifth claim denied. Actual hardware telemetry tested separately. This does not establish overlapping live provider execution. |
| P-AC3 | Attempting to submit a sibling chapter is rejected by the database constraint. [Line 39](../Pipeline_4_2_0_Architecture.md#L39). | Ownership/fencing constraints in `store.py`. | Verified on Linux: wrong chapter rejected by application and by a direct SQLite insert against the ownership trigger; no artifact accepted. |
| P-AC4 | Simultaneous final chapter commits release exactly one logical synthesis job. [Line 40](../Pipeline_4_2_0_Architecture.md#L40). | Atomic gather barrier and synthesis uniqueness in `store.py`. | Verified on Linux: concurrent six-chapter completion produces one synthesis job and one `SYNTHESIS_RELEASED` event. |
| P-AC5 | Crash at launch/commit boundary recovers without accepting duplicate outputs. [Line 41](../Pipeline_4_2_0_Architecture.md#L41). | Worker startup recovery, lease fencing and immutable commit. | Store abrupt process death, lease fencing, immutable/idempotent commit and persistent retry exhaustion verified on Linux. Full native launch/commit-boundary recovery and Job Object cleanup still require Windows evidence. |

## Recovery telemetry API and acceptance tests

Implementation: [the telemetry sink](../src/cochem/warden/ladder.py).
Tests: [real SQLite acceptance tests](../pipeline_tests/test_recovery_telemetry.py).
The telemetry suite was executed with
`/workspace/.venvs/antigravity/bin/python -m pytest pipeline_tests/test_recovery_telemetry.py -q`:
**12 passed** on Linux. This evidence applies only to the telemetry rows below.

| ID | Exact requirement and source | Implementation / test evidence | Classification |
| --- | --- | --- | --- |
| T-01 / AC1 / T1 | `emit_recovery_event(conn=None, event_data=None) -> int` supports a sole event dict, connection/path plus dict, keyword forms, and zero arguments using the default DB. [Complete dossier lines 53–71, 155; truncated lines 14, 35, 47](../full_dossier_untruncated.md#L53-L71). | Exact signature; typed dispatch; `COCHEM_JOB_DB` selects the default before import. `test_emit_recovery_event_execution_and_row_id` runs a fresh real Python process for default/sole-dict/keyword/zero-arg calls. | Verified on Linux. |
| T-02 / AC2 / T2 | Auto-create `recovery_telemetry` with exactly `id INTEGER PRIMARY KEY AUTOINCREMENT`, `timestamp TEXT NOT NULL DEFAULT (datetime('now'))`, `tier INTEGER NOT NULL`, `action TEXT NOT NULL`, `details TEXT NOT NULL`. [Complete lines 73–92, 156; truncated lines 15–16, 36](../full_dossier_untruncated.md#L73-L92). | `test_recovery_telemetry_schema_definition` compares all PRAGMA rows, AUTOINCREMENT declaration, and current UTC timestamp. | Verified on Linux. |
| T-03 / AC3 / T3 | Caller-owned SQLite connections remain open; internally opened string, `Path` and default connections close in `finally`, including errors. [Complete lines 65–69, 105–107, 112, 157, 169; truncated lines 37, 49](../full_dossier_untruncated.md#L105-L112). | `test_emit_recovery_event_connection_injection_and_lifecycle`, `test_sql_failure_closes_owned_connection_and_preserves_injected_connection`, and in-memory connection test use real connections. | Open/commit/lifecycle behavior verified on Linux; Windows file-lock-sensitive unlink behavior requires the same tests on Windows. |
| T-04 / AC4 / T4 | Serialize the complete, unmodified input dictionary with `ensure_ascii=False`, retaining tier/action/custom/nested metadata. [Complete lines 41–42, 92, 139–141, 158, 168; truncated lines 38, 48](../full_dossier_untruncated.md#L139-L141). | `test_emit_recovery_event_payload_serialization_and_defaults` compares deserialized data to a deep copy and checks literal Unicode retention. | Verified on Linux. |
| T-05 | Missing or explicitly `None` tier/action normalize to `1` / `unspecified_recovery`; coercion follows `int(raw_tier)` / `str(raw_action)` otherwise. [Complete lines 119–127, 158; truncated line 38](../full_dossier_untruncated.md#L119-L127). | Parameterized metadata/default test covers missing, explicit null, string tier and numeric action without changing details. | Verified on Linux. |
| T-06 | Dispatch explicitly tests types; an event dict is redirected only when `event_data is None`, avoiding falsy argument confusion. [Complete lines 130–136, 167; truncated line 47](../full_dossier_untruncated.md#L130-L136). | Default subprocess test plus `test_invalid_metadata_does_not_create_or_change_database`; empty string/invalid connection values are rejected explicitly. | Verified on Linux. |
| T-07 / AC5 / T5 | Return a positive integer row ID and monotonically increment IDs for consecutive tiers 1, 2, 3. Validate `lastrowid` rather than silently returning zero. [Complete lines 94–95, 143–145, 159; truncated lines 16, 39](../full_dossier_untruncated.md#L94-L95). | `test_emit_recovery_event_sequential_multi_tier_logging` verifies stored tiers, strict monotonicity, and no ID reuse after deleting the last row. | Verified on Linux. |
| T-08 | Explicit `active_conn.commit()`; no subprocess, LLM calls or slow external IO inside SQLite write transactions; WAL and `busy_timeout=5000`. [Complete lines 108–110; Oracle lines 41, 49–50](../full_dossier_untruncated.md#L108-L110). | Serialization occurs before the write transaction; the sink contains only SQLite operations during it. Independent reader confirms the committed row; injected connection checks timeout and WAL. | Verified on Linux by lifecycle/schema tests and source inspection. In-memory SQLite retains its native memory journal. |
| T-09 / AC6 / T6 | No `pass`, `NotImplementedError`, `mock`, `MagicMock`, or `monkeypatch`; tests use real SQLite, not substituted connections. [Complete lines 44–45, 101–104, 160, 170; truncated lines 18, 40, 50](../full_dossier_untruncated.md#L101-L104). | `test_ladder_zero_mock_anti_spoof_compliance` traverses the sink AST; all telemetry data tests use real SQLite. | Verified on Linux. |
| T-10 | Text/file operations specify UTF-8. [Complete line 113](../full_dossier_untruncated.md#L113). | Sink serializes Unicode directly into SQLite TEXT; test source reading and subprocess text decoding explicitly use UTF-8. | Verified by source inspection and Unicode test. |
| T-11 | Limit implementation to the named file/API/WBS scope and run the acceptance cases. [Complete lines 14–18, 164–170; truncated lines 14–18, 44–50](../full_dossier_untruncated.md#L164-L170). | Root `ladder.py` implements the telemetry sink; it does not import the archive's broader recovery FSM or Hyper-V actions. All six named acceptance areas are covered by the 12 executed tests. | Verified on Linux for telemetry scope. |

## Explicit exclusions and deployment boundaries

The telemetry dossier explicitly excludes `HealthEscalationLadder` transitions
and FSM logic, its FastMCP registration, direct Hyper-V cmdlets/PowerShell IPC,
remote telemetry streaming, and named-pipe guest health monitoring
([both dossiers, lines 20–25](../full_dossier_untruncated.md#L20-L25)). These
are not additional 4.2.2 deliverables inferred from archive filenames. No
unrelated Hyper-V or generic TDD acceptance requirement is added to these
four specifications.

Windows deployment must keep the SYSTEM controller's SQLite database, WAL/SHM
files, watermark state and completion tokens inaccessible to active workers.
The planned deployment uses separate standard worker identities and per-slot
ACLs, with cleanup and no reassignment of an identity to a different chapter
of the same workflow. Six chapters require six identities, while at most four
workers execute concurrently. Native
CLI subscription login belongs to each worker identity, not automatically to
the interactive Antigravity user or SYSTEM account. Configuration alone cannot
prove that authentication or isolation works: target-host checks must verify
both before live multi-agent acceptance is claimed.

The Linux component and protocol checks above have passed. Until the native
Windows runtime, account/ACL/Job Object checks and real three-provider workflow
have actual passing results, **this matrix does not certify the full pipeline
as ready**.
