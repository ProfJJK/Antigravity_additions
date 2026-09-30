# CoChem V4.1.2 Pipeline: Master Test Infrastructure Specification (`TEST_INFRA.md`)
**Authoritative E2E Testing Track Blueprint & Operational Verification Plan**

- **Document Version**: 4.1.2-TEST-RELEASE
- **Date**: 2026-09-29
- **Author**: `test_writer_e2e` (Quality Assurance & Testing Specialist)
- **Status**: RATIFIED E2E SPECIFICATION
- **Scope**: CoChem V4.1.2 Pipeline Bootstrap Protocol (Features 1–21, Milestones M1–M7)
- **Target Repository**: `D:\__CoChem\__agentic\v4.1.2\`
- **Test Suite Location**: `D:\__CoChem\__agentic\v4.1.2\tests\`

---

## 1. Executive Summary & Architectural Overview

The **CoChem V4.1.2 Pipeline** establishes a hyper-converged, decoupled asymmetric execution plane designed to eliminate Windows kernel heap exhaustion and eradicate counterfeit compliance traps. The pipeline operates across two decoupled layers:
1. **Layer 1 Internal CLI Swarms**: Single `claude.exe` process (Fable 5.1 Director with 4096 MB V8 heap) spawning up to 20 native Opus 5.5 / Sonnet 5 sub-agents within a single OS process tree, eliminating Windows pseudoconsole exhaustion.
2. **Layer 2 OS-Level Supervised Queue**: Lightweight Python daemons polling `job_board.db` via SQLite Write-Ahead Logging (WAL) with `CREATE_NO_WINDOW = 0x08000000`, 512 MB worker heap, 100–500 ms JIT jitter, and `hardware_guard.py` admission control.

The **E2E Testing Track** provides rigorous, opaque-box, requirement-driven verification across all 21 pipeline features specified in `PROJECT.md § Feature Inventory`. It enforces an uncompromising **Zero-Mock Policy**, ensuring every test interacts with authentic SQLite databases, real filesystem trees, and authentic physics canaries (PySCF, XTB, ASE, and dynamic `mendeleev` atomic mass resolution).

---

## 2. Testing Philosophy & Anti-Spoofing Protocols

All test suites and harnesses adhere to the following mandatory execution invariants:

### 2.1 Zero-Mock & Anti-Spoofing Mandate (Anti-Spoofing Protocol v4.2)
- **No Mock Libraries**: Absolutely NO use of `unittest.mock`, `unittest.mock.MagicMock`, or `monkeypatch` to fake database connections, process launches, or physical calculations.
- **No NotImplementedError / Pass Stubs (Rule 3)**: Dead-end stubs and tautological tests asserting NotImplementedError are strictly prohibited.
- **Semantic Spoofing Ban (Rule 8)**: Synthetic array generators (`np.zeros`, `np.ones`, `np.eye`) are forbidden for faking physical state. Test fixtures must load authentic data or compute genuine physical states.
- **Physical Fallback Mandate (Rule 14)**: Core domain logic tests must NEVER be silently bypassed via `try...except` and `pytest.skip` blocks when an external binary is absent. Tests must implement rigorous physical fallbacks (e.g., using ASE `EMT()` calculator and dynamic Mendeleev elemental resolution) to guarantee real physical simulation.

### 2.2 Mendeleev Library Mandate
- **Dynamic Mass Resolution**: All atomic and isotopic masses must be dynamically retrieved using the `mendeleev` library (`mendeleev.element(symbol).mass`). Hardcoding CODATA tables or atomic weights is strictly forbidden.

### 2.3 Windows Subprocess Hardening (Rule 15)
- **Window Popup Prevention**: All subprocess invocations on Windows (`subprocess.run`, `subprocess.Popen`) must include `creationflags=0x08000000` (`subprocess.CREATE_NO_WINDOW`) to prevent console terminal windows from popping up and stealing desktop focus.

### 2.4 WBS Fracture Invariant (Rule 18)
- **Whole-File Rewrite Ban**: Edits are bounded to 20–100 line context chunks using the Delimited Artifact Protocol. Any test or worker proposing a whole-file rewrite (>500 lines or >80% file content) triggers `[HARD_ABORT: WBS FRACTURE VIOLATION]`.

---

## 3. Four-Tier Testing Hierarchy

The test infrastructure is architected into four distinct, cumulative tiers:

```
+===================================================================================================+
|                               FOUR-TIER E2E TESTING HIERARCHY                                      |
+===================================================================================================+
|  TIER 4: REAL-WORLD SCENARIOS                                                                     |
|  - Complete multi-stage pipeline bootstrap flows (Stage 0 -> Stage 6 -> Victory Audit)            |
|  - Poison-pill quarantine & 10-cycle research pivot recovery                                      |
|  - High-concurrency lease race & zombie reclamation                                                |
|  - Physical molecular energy minimization via Mendeleev + ASE                                     |
|  - Adversarial whole-file violation rejection & recovery                                          |
+---------------------------------------------------------------------------------------------------+
                                                  ^
                                                  |
+---------------------------------------------------------------------------------------------------+
|  TIER 3: CROSS-FEATURE INTERACTIONS (PAIRWISE COMBINATIONS)                                       |
|  - 15 Orthogonal Pairwise Interaction Tests                                                       |
|  - Atomic lease acquisition under simulated process crashes                                       |
|  - Layer 1 CLI sub-agent generation under Docker --network none constraints                       |
|  - Stage 0 Purge + Stage 3 Blackboard Injection concurrency                                       |
|  - Rule 18 Fracture Audit + Stage 5 Live Tree Git Merge                                           |
+---------------------------------------------------------------------------------------------------+
                                                  ^
                                                  |
+---------------------------------------------------------------------------------------------------+
|  TIER 2: BOUNDARY & CORNER CASES (>=5 PER FEATURE, >=105 TESTS)                                   |
|  - Numerical extremes, off-by-one errors, corrupted JSON payloads                                 |
|  - Read-only database locks, filesystem permissions, process timeouts                             |
|  - Empty batches, max attempt bounds (3-strike poison pill), UTF-8 / CP1252 encodings              |
+---------------------------------------------------------------------------------------------------+
                                                  ^
                                                  |
+---------------------------------------------------------------------------------------------------+
|  TIER 1: FEATURE COVERAGE (>=5 PER FEATURE, >=105 TESTS)                                          |
|  - Happy-path validation for all 21 features in isolation                                         |
|  - Direct interface contract verification against PROJECT.md § Interface Contracts                |
|  - Deterministic inputs with verifiable ground-truth outputs                                      |
+===================================================================================================+
```

- **Tier 1 (Feature Coverage)**: Validates baseline operational behavior for each of the 21 features independently (≥5 tests per feature = 105 tests).
- **Tier 2 (Boundary & Corner Cases)**: Stress tests edge conditions, invalid configurations, corrupted inputs, resource ceilings, and error handlers for each feature (≥5 tests per feature = 105 tests).
- **Tier 3 (Cross-Feature Interactions)**: Validates orthogonal pairwise combinations across architectural boundaries, ensuring subsystems interact seamlessly under concurrent execution (15 tests).
- **Tier 4 (Real-World Scenarios)**: Simulates end-to-end, multi-stage production workloads covering complete pipeline lifecycles, failure self-healing, and physical quantum chemistry verification (5 comprehensive scenarios).

**Total Test Count**: ≥ 230 Executable Tests.

---

## 4. Comprehensive 21-Feature Specification & Verification Matrix

| # | Feature Name | Description | Target Milestone | Tier 1 Tests | Tier 2 Tests | Primary Test File |
|---|--------------|-------------|------------------|--------------|--------------|-------------------|
| 1 | Stage 0 Database Purge | Purge all uncompleted tasks (547 PENDING + 1 RUNNING) from `job_board.db` via `DELETE WHERE status != 'COMPLETED'` | M1 | 5 | 5 | `test_stage0_purge.py` |
| 2 | Stage 0 File System Wipe | Physically delete 42 pre-ratification WBS, SRS, and micro-prompt markdown files (31 build + 11 staging) | M1 | 5 | 5 | `test_stage0_purge.py` |
| 3 | Stage 0 Integrity Checkpoint | Verify zero-touch protection on `V4.1.2_Master_Architecture_Plan.md`, `CAP_01`–`CAP_22`, and Rule 19 core scripts | M1 | 5 | 5 | `test_stage0_purge.py` |
| 4 | Stage 1 Fable Invocations | Launch `claude.exe` (Fable 5.1 Director) with memory cap 4096MB and `CREATE_NO_WINDOW = 0x08000000` | M2 | 5 | 5 | `test_stage1_srs_dag.py` |
| 5 | Stage 1 Multi-Part SRS Generation | Author modular SRS (`00_skeleton.md` + chapters `ch01`..`chNN`, ≤400 lines each) | M2 | 5 | 5 | `test_stage1_srs_dag.py` |
| 6 | Stage 1 Dependency Graph | Generate machine (`graph.json`) and Mermaid (`graph.mmd`) DAG; verify acyclic topology | M2 | 5 | 5 | `test_stage1_srs_dag.py` |
| 7 | Stage 1 Guardrail & Method Matrix Audit | Audit SRS chapters against `user_global.md` and Method Matrix M-1..M-8 | M2 | 5 | 5 | `test_stage1_srs_dag.py` |
| 8 | Stage 2 WBS Graph Fracture | Fracture Dependency Graph into strict N=1 leaf nodes (20–100 lines per chunk) | M3 | 5 | 5 | `test_stage2_wbs_fracture.py` |
| 9 | Stage 2 Rule 18 Whole-File Ban Audit | Audit leaf nodes against whole-file rewrite ban (checks W-1..W-10) | M3 | 5 | 5 | `test_stage2_wbs_fracture.py` |
| 10 | Stage 3 JSON Payload Formatting | Format WBS leaf nodes into canonical JSON schema `4.1.1-wbs-node/1` | M4 | 5 | 5 | `test_stage3_payload_batches.py` |
| 11 | Stage 3 Blackboard Injection | Physically execute raw SQLite `INSERT` commands to inject batches into `job_board.db` | M4 | 5 | 5 | `test_stage3_payload_batches.py` |
| 12 | Stage 3.5 Batch Manifest Generation | Cluster tasks into batches of ≤20 tasks; generate `wbs/batches/B<nn>.json` mirrored to `WBS_Micro_Prompts/` | M4 | 5 | 5 | `test_stage3_payload_batches.py` |
| 13 | Stage 4 Test Authoring (Test-First) | Author exhaustive `pytest` suites first (Gate G6) with zero mocks prior to implementation | M5 | 5 | 5 | `test_stage4_tdd_sandboxing.py` |
| 14 | Stage 4 Ephemeral Docker Sandbox Coding | Verify isolated `--network none`, `--read-only`, tmpfs 2G, memory 4G Docker execution constraints | M5 | 5 | 5 | `test_stage4_tdd_sandboxing.py` |
| 15 | Stage 4 Physical Verification Swarm | Audit and physically test code against physics canaries (PySCF, XTB, ASE, dynamic Mendeleev) | M5 | 5 | 5 | `test_stage4_tdd_sandboxing.py` |
| 16 | Stage 5 Live Tree Git Merge | Execute verified `git commit` merges into live `D:\__CoChem\__agentic\v4.1.2\` under `merge.lock` | M5 | 5 | 5 | `test_stage5_merge_lifecycle.py` |
| 17 | Stage 5 Task Completion State Update | Mark completed tasks as `COMPLETED` in `job_board.db`, clearing leases and recording result path | M5 | 5 | 5 | `test_stage5_merge_lifecycle.py` |
| 18 | Stage 6 Ecosystem Boot & Daemon Setup | Verify Windows Host Warden E-core affinity (`0x00FF0000`), BelowNormal priority, and Hyper-V RAM cap | M6 | 5 | 5 | `test_stage6_activation_mcp.py` |
| 19 | Stage 6 Queue Migration | Migrate waiting/deferred tasks into new V4.1.2 queue with schema adaptation and lease reset | M6 | 5 | 5 | `test_stage6_activation_mcp.py` |
| 20 | Stage 6 Global Tool & MCP Integration | Integrate pipeline into FastMCP servers (`cochem-knowledge-mcp`, `cochem-queue`) and SQLite FTS5 RAG | M6 | 5 | 5 | `test_stage6_activation_mcp.py` |
| 21 | Final Verification & Victory Audit | Compile proof-of-work receipts, verify zero mock compliance, generate cryptographic ledger, write handoff | M7 | 5 | 5 | `test_victory_audit.py` |

---

## 5. Category-Partition & Boundary Value Analysis (BVA)

To guarantee rigorous opaque-box coverage, test inputs are partitioned into formal category classes and bounded parameters:

### 5.1 Category-Partition Rules

1. **Database Job Status (`status`)**:
   - Equivalence Classes: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `BLOCKED`.
   - Invalid Classes: `UNKNOWN`, `None`, `""`, lowercase `pending`.
2. **Lease Duration (`lease_expires_at`)**:
   - Nominal: `current_time + 1800` (30 minutes).
   - Boundary: Expired (`current_time - 1`), Instantaneous (`current_time`), Future max (`current_time + 86400`).
3. **Chunk Line Bounding (`line_count`)**:
   - Valid: `20 <= line_count <= 100`.
   - Boundary Violations: `19` lines (under-fractured/trivial), `101` lines (over-fractured), `0` lines (empty), `501` lines (whole-file violation).
4. **Batch Size (`batch_size`)**:
   - Valid: `1 <= batch_size <= 20`.
   - Boundary Violations: `0` (empty batch), `21` (oversized batch), negative values.
5. **Execution Environment Security**:
   - Container Flags: `--network none`, `--read-only`, `--tmpfs /tmp:rw,size=2g`, `--memory 4g`.
   - Subprocess Flags: `creationflags=0x08000000` (Windows), `timeout <= 900`.
6. **Chemical Elemental Mass (`mass`)**:
   - Retrieved via `mendeleev.element(symbol).mass`.
   - Boundary: Hydrogen (1.008), Carbon (12.011), Uranium (238.029), Oganesson (294.0).
   - Invariant: Dynamic float > 0, exact isotopic masses verifiable.

### 5.2 Boundary Value Analysis (BVA) Numerical Limits

| Parameter | Minimum Valid | Nominal | Maximum Valid | Edge / Boundary Values Tested |
|-----------|---------------|---------|---------------|-------------------------------|
| Task Attempts (`attempts`) | `0` | `1` | `9` (`max_attempts=10`) | `0`, `1`, `9`, `10` (Triggers `BLOCKED`), `11` |
| WBS Chunk Context (lines) | `20` | `50` | `100` | `19`, `20`, `21`, `99`, `100`, `101`, `500` |
| SRS Chapter Length (lines) | `10` | `250` | `400` | `399`, `400`, `401` (Flagged), `1000` |
| Batch Size (tasks) | `1` | `10` | `20` | `0` (Rejected), `1`, `19`, `20`, `21` (Rejected) |
| Lease TTL (seconds) | `1` | `1800` | `3600` | `-1` (Expired), `0`, `1800`, `1801` |
| CPU Affinity Mask | `0x00FF0000` | `0x00FF0000` | `0x00FF0000` | Non-E-core masks (`0x000000FF`, `0xFFFFFFFF`) |
| Guest VM RAM Cap (GB) | `32` | `32` | `38` | `16` (Under-provisioned), `32`, `38`, `64` (Over-cap) |

---

## 6. Combinatorial Pairwise Interaction Matrix (Tier 3)

The Tier 3 test suite (`test_cross_feature_interactions.py`) executes 15 orthogonal pairwise interaction tests:

| ID | Feature Pair | Interaction Scope | Physical Verification Invariant |
|----|--------------|-------------------|---------------------------------|
| `INT-01` | F01 (Purge) x F11 (Inject) | Database Sanitization followed by immediate Batch Injection | Zero remnant rows; newly injected rows have `status='PENDING'`; WAL checkpoint clean |
| `INT-02` | F11 (Inject) x F17 (Lease) | Atomic lease acquisition under simulated daemon crash | Expired lease reclaimed by watchdog; zero double-claims across concurrent workers |
| `INT-03` | F04 (CLI) x F14 (Docker) | Layer 1 CLI sub-agent code generation inside isolated container | Docker `--network none` enforces zero external network egress during code generation |
| `INT-04` | F08 (WBS) x F16 (Merge) | WBS 20–100 line chunk bounds enforced during live Git merge | Git commit diff is strictly <=100 lines; reject attempts to merge full file replacements |
| `INT-05` | F15 (Physics) x F13 (TDD) | Dynamic Mendeleev atomic masses driving ASE EMT energy tests | ASE Atoms built with dynamic masses; energy minimization converges to ground state |
| `INT-06` | F12 (Batches) x F17 (State) | Batch Manifest execution tracking with task state updates | Batch progress calculation dynamically reflects transitions from PENDING -> COMPLETED |
| `INT-07` | F20 (MCP) x F03 (Integrity)| FastMCP RAG queries against SQLite FTS5 database | FTS5 queries resolve sub-millisecond; read-only connections preserve index integrity |
| `INT-08` | F18 (Warden) x F04 (Subproc)| Windows Host Warden E-core affinity & CREATE_NO_WINDOW | Process affinity strictly `0x00FF0000`; `0x08000000` flag set on child process handles |
| `INT-09` | F11 (Queue) x F07 (Audit) | 3-Strike Poison-Pill quarantine triggering research pivot | Task reaching `max_attempts` transitions to `BLOCKED`; research trigger logged |
| `INT-10` | F19 (Migrate) x F11 (Queue)| Legacy queue migration under concurrent queue reads | SQLite transaction ensures zero duplicate task IDs; legacy leases reset to NULL |
| `INT-11` | F06 (DAG) x F12 (Batches) | Dependency DAG topological sort governing batch groupings | Tasks in Batch N+1 strictly depend on tasks in Batch N; zero forward dependency violations |
| `INT-12` | F03 (Integrity) x F16 (Merge)| Rule 19 core scripts protection against Git merge corruption | Live tree merge fails with hard abort if commit diff targets any Rule 19 protected file |
| `INT-13` | F14 (Docker) x F17 (State) | Docker container OOM / memory ceiling crash handling | Task attempts incremented; lease reset; error log records container memory violation |
| `INT-14` | F07 (Audit) x F13 (TDD) | Method Matrix audit validating test-first gate compliance | Gate G6 verifies test fails first; zero mocks; code passes audit without pass stubs |
| `INT-15` | F21 (Victory) x F17 (State)| Victory Audit ledger reconciling 100% of completed tasks | Cryptographic hash ledger matches every `COMPLETED` row in `job_board.db` |

---

## 7. Real-World Application Workload Scenarios (Tier 4)

The Tier 4 test suite (`test_real_world_e2e_scenarios.py`) models 5 complete end-to-end operational workflows:

1. **Scenario 1: Full Pipeline Bootstrap Lifecycle (`test_scenario_1_full_pipeline_bootstrap_lifecycle`)**:
   - Executes the complete Stage 0 to Stage 6 sequence: Database purge (548 tasks removed) -> 42 file wipe -> Integrity checkpoint -> SRS generation (≤400 lines) -> DAG creation -> WBS fracture (20–100 lines) -> Rule 18 audit -> JSON payload formatting -> Blackboard injection -> Batch manifests -> Test-first authoring -> Sandbox coding -> Physical verification -> Live git merge -> Activation -> Victory Audit.
2. **Scenario 2: Poison-Pill Quarantine & Research Pivot (`test_scenario_2_poison_pill_quarantine_and_research_pivot`)**:
   - Simulates a complex task failing 3 consecutive execution attempts. The system freezes code generation, transitions task to `BLOCKED`, triggers Mandatory Literature Research, updates the payload with research findings, and on the 4th attempt executes cleanly and merges.
3. **Scenario 3: Multi-Worker Lease Contention & Zombie Reclamation (`test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation`)**:
   - Launches 10 concurrent worker processes contending for 20 queued tasks using atomic `BEGIN IMMEDIATE` leases. Simulates a worker crash holding an active lease. The SRE Watchdog reclaims the expired lease, allowing another worker to acquire and complete the task with zero data loss or duplicate execution.
4. **Scenario 4: Physics Canary Verification & Molecular Energy Minimization (`test_scenario_4_physics_canary_dynamic_mass_and_emt_geometry_optimization`)**:
   - Executes genuine quantum/classical molecular simulation: Loads dynamic isotopic masses from `mendeleev`, constructs physical geometry for carbon dioxide and water molecules, executes ASE EMT potential calculations, optimizes geometry, and verifies energy conservation without synthetic numbers or mocked data.
5. **Scenario 5: Adversarial Whole-File Violation Rejection & Recovery (`test_scenario_5_adversarial_whole_file_violation_rejection_and_chunk_recovery`)**:
   - Injects an adversarial payload attempting a 650-line whole-file rewrite. The Rule 18 Guardrail rejects the attempt with `[HARD_ABORT: WBS FRACTURE VIOLATION]`. The failure triggers autonomous re-fracture into three compliant 40-line chunks, which successfully pass the audit and merge cleanly.

---

## 8. Test Execution Harness & Invocation Protocols

### 8.1 Directory Layout
```
D:\__CoChem\__agentic\v4.1.2\
├── tests\
│   ├── conftest.py                       # Zero-mock fixtures, SQLite DB builders, physics helpers
│   ├── test_stage0_purge.py              # Features 1, 2, 3 (30 tests)
│   ├── test_stage1_srs_dag.py            # Features 4, 5, 6, 7 (40 tests)
│   ├── test_stage2_wbs_fracture.py       # Features 8, 9 (20 tests)
│   ├── test_stage3_payload_batches.py    # Features 10, 11, 12 (30 tests)
│   ├── test_stage4_tdd_sandboxing.py     # Features 13, 14, 15 (30 tests)
│   ├── test_stage5_merge_lifecycle.py    # Features 16, 17 (20 tests)
│   ├── test_stage6_activation_mcp.py     # Features 18, 19, 20 (30 tests)
│   ├── test_victory_audit.py             # Feature 21 (10 tests)
│   ├── test_cross_feature_interactions.py# Tier 3 Pairwise Interactions (15 tests)
│   └── test_real_world_e2e_scenarios.py  # Tier 4 Real-World Workflows (5 tests)
├── job_board.db                          # Blackboard database
├── knowledge_index.db                    # FTS5 permanent knowledge index
├── TEST_INFRA.md                         # This specification
└── TEST_READY.md                         # Operational readiness declaration
```

### 8.2 Pytest Invocation Commands

```powershell
# Run the complete test suite across all 4 tiers (230 tests)
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests -v

# Run by tier using pytest markers
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests -m tier1 -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests -m tier2 -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests -m tier3 -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests -m tier4 -v

# Run individual feature stage test files
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage0_purge.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage1_srs_dag.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage2_wbs_fracture.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage3_payload_batches.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage4_tdd_sandboxing.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage5_merge_lifecycle.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_stage6_activation_mcp.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_victory_audit.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_cross_feature_interactions.py -v
python -m pytest D:\__CoChem\__agentic\v4.1.2\tests\test_real_world_e2e_scenarios.py -v
```
