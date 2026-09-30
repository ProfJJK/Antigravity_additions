# SRS Master Index & Global Architectural Specification (00_skeleton)
**System Specification & Canonical Topology for CoChem Pipeline V4.1.2**

- **Specification ID**: SRS-412-00
- **Version**: 4.1.2-RELEASE
- **Document Status**: RATIFIED CANONICAL BASELINE
- **Target Architecture**: Decoupled Asymmetric Execution Plane (Windows 11 Host + Hyper-V Guest VM)

---

## 1. Global Architectural Overview

The CoChem Pipeline Version 4.1.2 implements a hyper-converged, decoupled asymmetric execution plane designed to eliminate host desktop heap exhaustion and eliminate semantic counterfeit compliance. The system bifurcates supervisory orchestration and agentic computing into two distinct operational tiers:
1. **Windows 11 Host SRE Warden**: Pinned strictly to Intel Efficiency Cores (`0x00FF0000`) with below-normal CPU priority, supervising VM health and named-pipe telemetry.
2. **Hyper-V Guest Quarantine VM**: Ubuntu 24.04 LTS constrained to a hard 32 GB static RAM ceiling, executing agent workloads inside disposable, air-gapped Docker sandboxes (`--network none`, `--read-only`, tmpfs 2G).

---

## 2. Component Topology

The system comprises eight interconnected architectural subsystems:
```
+---------------------------------------------------------------------------------------+
|                               WINDOWS 11 PRO HOST WORKSTATION                         |
|  +------------------------+      +-------------------------------------------------+  |
|  | User Interactive Space |      | Host Warden Daemon (host_warden.py)             |  |
|  | (P-Cores, Threads 0-15)|      | - Pinned to E-Cores (0x00FF0000), BelowNormal   |  |
|  +------------------------+      | - CREATE_NO_WINDOW (0x08000000) Subprocesses    |  |
|                                  +------------------------+------------------------+  |
|                                                           | \\.\pipe\cochem_warden_vm |
+-----------------------------------------------------------|---------------------------+
                                                            v
+---------------------------------------------------------------------------------------+
|                           HYPER-V GUEST VM (UBUNTU 24.04 LTS)                         |
|  +---------------------------------------------------------------------------------+  |
|  | Guest Execution Plane (Static 32GB RAM Ceiling, 12 vCPUs at 70% Cap)            |  |
|  |                                                                                 |  |
|  |  +-----------------------+  +-----------------------+  +---------------------+  |  |
|  |  | Layer 1 CLI Swarm     |  | SQLite WAL Blackboard |  | SRE Watchdog Daemon |  |  |
|  |  | Fable 5.1 Director    |  | (job_board.db)        |  | 4-Matrix Engine     |  |  |
|  |  | 4096MB V8 Memory Cap  |  | BEGIN IMMEDIATE Leases|  | Out-of-band ro Mode |  |  |
|  |  +-----------+-----------+  +-----------+-----------+  +----------+----------+  |  |
|  |              |                          |                         |             |  |
|  |              v                          v                         v             |  |
|  |  +---------------------------------------------------------------------------+  |  |
|  |  | Ephemeral Docker Sandboxes (--network none, --read-only, tmpfs /tmp:2G)   |  |  |
|  |  | - Code Forge (Summit TDD) | - Academic Press | - Pedagogy Engine          |  |  |
|  |  | - Authentic Physics Canaries: PySCF, XTB, ASE EMT, Mendeleev Elements     |  |  |
|  |  +---------------------------------------------------------------------------+  |  |
|  +---------------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------------+
```

---

## 3. Non-Functional Requirement (NFR) Catalog

- **NFR-01: Resource Guardrails**: Host Warden memory footprint shall remain strictly under 50 MB RAM; Guest VM shall be bounded by static 32 GB RAM ceiling.
- **NFR-02: OS & Kernel Isolation**: All host subprocesses shall enforce `CREATE_NO_WINDOW = 0x08000000`, preventing desktop heap exhaustion.
- **NFR-03: Security & FERPA Air-Gapping**: Student data shall execute in air-gapped Docker sandboxes with zero network connectivity and zero host leakage.
- **NFR-04: Authentic Physics & Zero-Mock Invariance**: All chemical properties and masses shall resolve dynamically via `mendeleev`. Mocks and stubs are banned.
- **NFR-05: Transactional Concurrency**: SQLite queue operations shall enforce WAL mode, `PRAGMA synchronous = NORMAL`, and atomic `BEGIN IMMEDIATE` leases.
- **NFR-06: WBS Fracture Invariant (Rule 18)**: Code modifications shall be bounded between 20 and 100 context lines; whole-file rewrites (>500 lines) trigger hard abort.
- **NFR-07: Resuscitation & Self-Healing**: Out-of-band SRE Watchdog shall detect structural stalls and trigger self-healing via evidence bundle within 45 seconds.
- **NFR-08: RAG Search Latency**: Permanent knowledge retrieval via SQLite FTS5 database (`knowledge_index.db`) shall respond within 5 milliseconds.
- **NFR-09: Director Heap Memory Cap**: Layer 1 `claude.exe` Fable 5.1 Director process memory cap shall be strictly set to 4096 MB via `NODE_OPTIONS="--max-old-space-size=4096"` to prevent V8 heap crashes during multi-agent orchestration.
- **NFR-10: Worker Heap Memory Cap**: Layer 2 individual worker daemon processes shall operate within a 512 MB memory boundary to prevent desktop heap leakage.
- **NFR-11: Task Lease Duration**: The default task lease duration shall be strictly 1800 seconds (30 minutes) with 5-second heartbeat refresh intervals.
- **NFR-12: JIT Startup Jitter Delay**: Task daemons and workers shall introduce a randomized 100–500 ms JIT startup delay to prevent Thundering Herd lock contention on SQLite.


---

## 4. Master SRS Chapter Index

This skeleton document serves as the root index for the modular SRS specification series:

- [Chapter 1: Host Warden](ch01_host_warden.md)
- [Chapter 2: Quarantine VM Sandbox](ch02_quarantine_vm.md)
- [Chapter 3: Two-Staged Concurrency Layers](ch03_concurrency_layers.md)
- [Chapter 4: Task Matrix Blackboard](ch04_task_matrix_blackboard.md)
- [Chapter 5: Research-Driven TDD Pivot](ch05_research_tdd_pivot.md)
- [Chapter 6: Dual Wiki RAG Database](ch06_dual_wiki_rag.md)
- [Chapter 7: Domain-Specific Pipelines (DSPs)](ch07_dsp_domain_pipelines.md)
- [Chapter 8: SRE Watchdog Daemon](ch08_watchdog_sre.md)
