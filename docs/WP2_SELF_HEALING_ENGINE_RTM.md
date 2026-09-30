# WP-2.0 Autonomous Self-Healing Engine — Requirements Traceability Matrix

**Work Package:** WP-2.0 (Level-3 tasks 2.01 through 2.20)
**Governing standards:** IEEE 830-1998, PMBOK 7th Edition, SWEBOK v4, Anti-Spoofing Protocol v4 (Invariants 1–14)
**Ratification gate:** `ci_tools/ratify_wp2_self_healing_engine.py` (Gate 1 parses this document)

## 1. Scope and MECE Statement

Each WP-2.0 task has exactly one row in the forward matrix. Each row names:

- the requirement identifiers it satisfies;
- the single production deliverable that realises it;
- the verification suite that proves it;
- exactly one Accountable party.

Every WP-2.0 production module under `src/cochem_ml/` is traced to at least one task. Every requirement is traced back to its tasks in the reverse matrix. Tasks do not overlap in scope (mutually exclusive) and jointly cover the work package (collectively exhaustive).

## 2. Forward Traceability (Task → Requirement → Deliverable → Verification → RACI)

| Task ID | Task Scope | Requirements | Deliverable Module | Verification Suite | RACI Assignments |
|---|---|---|---|---|---|
| 2.01 | Categorical fault ontology formalization and orthonormal basis vectors | FR-03, NFR-02 | `src/cochem_ml/fault_ontology.py` | `tests/tdd/test_task_2_01.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.02 | Context-aware traceback parser and AST anomaly classifier | FR-03, NFR-02 | `src/cochem_ml/anomaly_classifier.py` | `tests/tdd/test_task_2_02.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.03 | 8D root cause analysis state machine and Presidium governance | FR-03, NFR-03 | `src/cochem_ml/self_healing_fsm.py` | `tests/tdd/test_task_2_03.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.04 | CommitManager git tree checkpointing and SHA-256 state hashing | FR-03, NFR-01 | `src/cochem_ml/commit_manager.py` | `tests/tdd/test_task_2_04.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.05 | Workspace rollback controller and dirty state reconstitution | FR-03, NFR-01 | `src/cochem_ml/workspace_rollback_controller.py` | `tests/tdd/test_task_2_05.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.06 | Methodological pivot counter and recovery attempt ledger | FR-03, NFR-02 | `src/cochem_ml/pivot_counter.py` | `tests/tdd/test_task_2_06.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.07 | PHYSICS WALL hard-abort circuit breaker and tripwire latch | FR-03, NFR-03 | `src/cochem_ml/physics_wall_tripwire.py` | `tests/tdd/test_task_2_07.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.08 | Diagnostic dispatch hook and physics autopsy report generator | FR-03, NFR-02 | `src/cochem_ml/diagnostic_dispatch.py` | `tests/tdd/test_task_2_08.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.09 | AST syntax lint auto-repair dispatch engine | FR-03, NFR-02 | `src/cochem_ml/syntax_lint_repair.py` | `tests/tdd/test_task_2_09.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.10 | Physical parity reconciler (Lindh versus XTB2 model Hessian) | FR-03, NFR-02 | `src/cochem_ml/self_healing_fsm.py` | `tests/tdd/test_task_2_10.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.11 | Environment deadlock detector and kernel lock scanner | FR-03, NFR-03 | `src/cochem_ml/deadlock_detector.py` | `tests/tdd/test_task_2_11.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.12 | Safe lock reclamation protocol and Council immunity RBAC | FR-03, NFR-03 | `src/cochem_ml/lock_reclamation.py` | `tests/tdd/test_task_2_12.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.13 | Anti-spoofing containment protocol and adversary alerting | FR-03, NFR-03 | `src/cochem_ml/self_healing_fsm.py` | `tests/tdd/test_task_2_13.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.14 | Dynamic prompt modifier and decoding temperature controller | FR-03, NFR-02 | `src/cochem_ml/prompt_modifier.py` | `tests/tdd/test_task_2_14.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.15 | Zero-mock state-driven tool masking engine | FR-03, NFR-03 | `src/cochem_ml/tool_masking.py` | `tests/tdd/test_task_2_15.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.16 | Deterministic recovery across the five canonical fault categories | FR-03, NFR-01 | `src/cochem_ml/self_healing_fsm.py` | `tests/tdd/test_task_2_16.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.17 | Subagent pivot ceiling tamper resistance verification | FR-03, NFR-02 | `src/cochem_ml/pivot_counter.py` | `tests/tdd/test_task_2_17.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.18 | Append-only remote ledger streaming and cryptographic chaining | FR-03, NFR-02 | `src/cochem_ml/remote_state_ledger.py` | `tests/tdd/test_task_2_18.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.19 | Rollback and reconstitution latency benchmark (sub-500 ms SLA) | FR-03, NFR-01 | `src/cochem_ml/rollback_benchmark.py` | `tests/tdd/test_task_2_19.py` | Coder (R), Audit (A), Tester (C), Scribe (I) |
| 2.20 | WP-2.0 architectural compliance ratification engine | FR-03, NFR-01, NFR-02, NFR-03, NFR-04 | `ci_tools/ratify_wp2_self_healing_engine.py` | `tests/tdd/test_task_2_20.py` | SDP (R), Council (A), Audit (C), Scribe (I) |

## 3. Reverse Traceability (Requirement → Tasks)

| Requirement | Statement | Allocated Tasks | Task Count |
|---|---|---|---|
| FR-03 | Autonomous self-healing, 8D RCA, rollback recovery and remote state streaming | 2.01, 2.02, 2.03, 2.04, 2.05, 2.06, 2.07, 2.08, 2.09, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20 | 20 |
| NFR-01 | Heap memory below the 2048 MB ceiling; rollback latency strictly below 500 ms | 2.04, 2.05, 2.16, 2.19, 2.20 | 5 |
| NFR-02 | IEEE 830 unambiguous typed interfaces, strict Pydantic v2 schemas (extra forbid, frozen), dynamic mendeleev atomic weights | 2.01, 2.02, 2.06, 2.08, 2.09, 2.10, 2.14, 2.17, 2.18, 2.20 | 10 |
| NFR-03 | Subprocess hygiene (CREATE_NO_WINDOW, UTF-8), deadlock isolation, Council immunity | 2.03, 2.07, 2.11, 2.12, 2.13, 2.15, 2.20 | 7 |
| NFR-04 | Fail-closed automated ratification gate and cryptographic deliverable receipt | 2.20 | 1 |

Zero requirements are unmapped. No task is left without a requirement.

## 4. RACI Legend

- **R — Responsible:** performs the work (`cochem-coder`, or `cochem-sdp-manager` for the ratification engine).
- **A — Accountable:** exactly one per task (`cochem-audit` for delivery tasks; the Council for the ratification engine).
- **C — Consulted:** `cochem-tester` / `cochem-audit`.
- **I — Informed:** `cochem-scribe`.

Presidium Ruling D1-01 applies: `cochem-coder` never holds Accountable status.

## 5. Ratification Gate Mapping

| Gate | Evidence | Pass Criterion |
|---|---|---|
| Gate 1 | This RTM | 20 task rows; every referenced file exists; one (A) per row; forward and reverse allocations agree |
| Gate 2 | AST contracts, package initialiser, schema probe | All public callables annotated; PEP 562 lazy package; four strict schemas reject undeclared fields; mendeleev weights queried live |
| Gate 3 | AST scan, `anti_spoof_linter.py --strict`, `ci_tools/mendeleev_ast_linter.py` | Zero banned constructs; both linters exit 0 |
| Gate 4 | Rollback benchmark probe, tracemalloc, subprocess AST audit | Rollback strictly below 500 ms; peak heap below 2048 MB; all subprocess calls use CREATE_NO_WINDOW and UTF-8 |

The receipt `COCHEM-DELIVERABLE-RECEIPT-TASK-2-20-WP20-RATIFICATION.json` is emitted only after gates 1–4 pass. It records SHA-256 digests computed from disk. Any failure exits 1 and purges the receipt.
