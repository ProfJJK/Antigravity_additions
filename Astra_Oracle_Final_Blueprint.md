# Astra Final Blueprint: Oracle Daemon (cochem_warden_oracle.py)

This blueprint represents the final, perfect, and ready-to-implement structural architecture for the Oracle Daemon, fully synthesizing the summit findings and incorporating all missing edge case resolutions identified by the Opus 5.5 audit.

This architecture explicitly **satisfies all anti-spoofing and concurrency protocols**.

## 1. Multi-Tier Privilege Circuit Breaker
- **OS Protection Override:** The Velocity Circuit Breaker (tripping at >500 events/sec) operates at a SYSTEM privilege tier. It asynchronously overrides normal State-Gated constraints (which normally restrict Oracle intervention to PENDING states). If a rogue agent triggers the breaker during an IN_PROGRESS state, the Daemon will immediately halt monitoring and terminate the rogue agent PID to prevent OS locking and memory exhaustion (via Windows Job Objects and NTFS quotas).

## 2. Reaper Mechanism for Deadlock Recovery
- **Orphan State Cleanup:** Following a Circuit Breaker termination, a dedicated "Reaper" mechanism is invoked. It safely releases any locked file handles, resolves orphaned database locks, and resets the task state from IN_PROGRESS to FAILED or PENDING_RETRY in the SQLite job_board.db. This ensures the system does not deadlock after a rogue process is killed.

## 3. Defender Ghost-Locking Mitigations
- **Windows File System Safeties:** Windows Defender and Search Indexers inherently lock newly written files (e.g., .warden_hints.md), crashing subsequent read attempts by agents.
- **Resolution:** All agent working directories and artifact paths must be explicitly excluded from Windows Defender and Windows Search Indexing. Additionally, transient hints and rules should favor delivery via the job_board.db SQLite payload or Named Pipes, eliminating raw file I/O contention entirely.

## 4. Out-of-Band Watermarking (Idempotency)
- **Anti-Spoofing & The Ouroboros Vulnerability:** The Oracle uses watermarking to ensure the same rule is never injected twice, preventing infinite feedback loops.
- **Out-of-Band Storage:** To prevent advanced agents from learning and spoofing watermark hashes to suppress Oracle interventions, all cryptographic watermarks are stored entirely out-of-band in a hidden SQLite tracking table that is completely inaccessible to the active agents. Watermark evaluation is performed *after* the 500ms event debounce window.

## 5. Reserved Budget Allocation (Context & RAG Optimization)
- **Aho-Corasick with Knapsack Budgeting:** The Oracle uses an Aho-Corasick faceted retrieval engine for high-performance extraction of context rules. A strict Knapsack Context Budget caps the injection size.
- **Anti-Starvation Allocation:** To prevent highly specific task rules from starving out foundational safety rules in the Knapsack budget, a strict **Reserved Budget Allocation** is implemented. A baseline percentage of the context payload is permanently reserved for core, non-evictable alignment directives (using neutral XML tags like <oracle_directive>).

## 6. Concurrency and I/O Lock Safety
- **WAL & Debouncing:** job_board.db strictly uses the WAL engine with usy_timeout=5000 to prevent PermissionError on concurrent access.
- **Debounced Processing:** All incoming file system and database events are filtered through a 500ms temporal debounce window, consolidating I/O noise before applying context generation or watermarks.
