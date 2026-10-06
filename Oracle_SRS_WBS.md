# Software Requirements Specification (SRS) & Work Breakdown Structure (WBS)
## For `cochem_warden_oracle.py` Daemon

This document outlines the formal SRS and WBS to guide coding agents in building the `cochem_warden_oracle.py` daemon based on the Astra Oracle Final Blueprint.

---

## 1. Software Requirements Specification (SRS)

### 1.1 Purpose
The `cochem_warden_oracle.py` daemon serves as the central orchestration and monitoring entity in the CoChem architecture. It evaluates task states, injects context based on Aho-Corasick multifaceted retrieval, mitigates ghost-locking and deadlock vulnerabilities, and prevents runaway processes via an OS-level circuit breaker and reaper mechanism.

### 1.2 Core Components & Requirements

#### 1.2.1 Multi-Tier Privilege Circuit Breaker
- **Requirement:** The daemon must implement a Velocity Circuit Breaker operating at a SYSTEM privilege tier.
- **Functionality:** Trip if events exceed >500 events/sec.
- **Action:** If tripped during an `IN_PROGRESS` state by a rogue agent, it must asynchronously override State-Gated constraints, halt monitoring, and terminate the rogue agent's PID to prevent OS locking and memory exhaustion (via Windows Job Objects).

#### 1.2.2 Reaper Mechanism for Deadlock Recovery
- **Requirement:** A recovery routine invoked after a Circuit Breaker termination.
- **Functionality:** Safe release of locked file handles and resolution of orphaned database locks.
- **Action:** Reset task states from `IN_PROGRESS` to `FAILED` or `PENDING_RETRY` in `job_board.db`.

#### 1.2.3 Defender Ghost-Locking Mitigations
- **Requirement:** Eliminate file I/O contention inherent to Windows Defender and Search Indexers.
- **Functionality:** Transient hints and rules must be delivered via the `job_board.db` SQLite payload or Named Pipes rather than raw files (e.g., avoiding `.warden_hints.md`). Ensure all agent working directories are excluded from Defender.

#### 1.2.4 Out-of-Band Watermarking (Idempotency)
- **Requirement:** Prevent duplicate rule injection and infinite feedback loops (Ouroboros vulnerability).
- **Functionality:** Cryptographic watermarks must be stored out-of-band in a hidden SQLite tracking table inaccessible to active agents.
- **Action:** Evaluate watermarks strictly *after* a 500ms debounce window.

#### 1.2.5 Reserved Budget Allocation (Context & RAG Optimization)
- **Requirement:** Aho-Corasick faceted retrieval engine restricted by a Knapsack Context Budget.
- **Functionality:** Prevent starvation of foundational safety rules.
- **Action:** Maintain a baseline Reserved Budget percentage for core, non-evictable alignment directives (using `<oracle_directive>` XML tags).

#### 1.2.6 Concurrency and I/O Lock Safety
- **Requirement:** Safe concurrent access for data and file processing.
- **Functionality:** Use SQLite WAL mode with `busy_timeout=5000` for `job_board.db`. Consolidate I/O events using a 500ms temporal debounce window before generation/watermarking.

---

## 2. Work Breakdown Structure (WBS)

### Phase 1: Core Foundation & Concurrency
- **1.1 SQLite WAL Connection Setup**
  - Configure `job_board.db` connection with WAL mode enabled.
  - Set `busy_timeout=5000` to prevent `PermissionError`.
  - Establish out-of-band hidden tracking table for cryptographic watermarks.
- **1.2 Watchdog Debouncing**
  - Implement a 500ms temporal debounce window for I/O and DB events.
  - Buffer and consolidate events before triggering processing logic.
  - Shift transient hint delivery from files (`.warden_hints.md`) to SQLite payloads/Named Pipes.

### Phase 2: RAG & Context Management
- **2.1 Aho-Corasick RAG Engine**
  - Integrate Aho-Corasick automaton for high-speed faceted retrieval of context rules.
  - Implement Knapsack Context Budget algorithm to cap injection size.
- **2.2 Reserved Budget Allocation**
  - Segment budget to guarantee a baseline allocation for core non-evictable safety directives.
  - Format baseline directives securely using `<oracle_directive>` XML tags.

### Phase 3: Idempotency & OS Protections
- **3.1 Out-of-Band Watermarking**
  - Implement cryptographic hashing for rule injection tracking.
  - Enforce watermark checking *after* the debounce window to ensure idempotency.
- **3.2 Velocity Circuit Breaker**
  - Implement real-time event frequency monitoring.
  - Configure SYSTEM privilege tier override for anomalous behavior (>500 events/sec).
  - Implement PID termination functionality via Windows Job Objects.

### Phase 4: Recovery & Resilience
- **4.1 Reaper**
  - Develop cleanup routines to release orphaned file handles and database locks post-termination.
  - Implement state rollback logic (e.g., `IN_PROGRESS` to `FAILED` / `PENDING_RETRY`) in `job_board.db`.
- **4.2 Ghost-Locking Mitigation Validation**
  - Validate that agent working directories are excluded from interference and transient hints flow through non-file channels safely.
