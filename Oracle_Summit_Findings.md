# Oracle Daemon Summit Findings

## 1. Agent Psychology (LLM Attention & Formatting)
Dropping `.oracle_context.md` files is a passive mechanism that standard agents will likely ignore unless explicitly prompted to look for them. Injecting rules into the SQLite JSON payload is far superior. To prevent LLM over-fixation or hallucination, use neutral, declarative XML tags placed at the end of the text payload (e.g., `<oracle_directive scope="restricted_keywords">...</oracle_directive>`).

## 2. Context & Retrieval (RAG Optimization)
Naive keyword matching causes "keyword explosion." Shift to a faceted retrieval engine using the Aho-Corasick algorithm for high-performance extraction. Rules should have specificity scores and follow a strict "Context Budget" (Knapsack approach) to cap injection size. Context should be managed ephemerally based on the task lifecycle.

## 3. Adversarial Exploits & Failsafes
**The Ouroboros Vulnerability:** An infinite loop where the Oracle triggers on its own injected keywords or reacts continuously to an agent's edits.
**Failsafes:**
* **Out-of-Band Delivery:** The Oracle must only inject rules via the `job_board.db` payload or to an isolated `.warden_hints.md` file, never mutating the active working file.
* **State-Gated Triggers:** The Oracle must only intervene when tasks are in a transient state (e.g., `PENDING`), never while `IN_PROGRESS`.
* **Idempotency:** Implement cryptographic watermarking to ensure the Oracle never injects the same rule twice for a single task.

## 4. Systems & OS Architecture (Windows 11)
Running a continuous `watchdog` on a directory that might experience a 200,000+ file TDD crash will overflow the `ReadDirectoryChangesW` buffer and lock the GIL.
**Guardrails:**
* Wrap agents in **Windows Job Objects** to enforce memory and process limits.
* Apply **NTFS Disk Quotas** to the agent directories.
* Implement a **Velocity Circuit Breaker**: If filesystem events exceed 500/sec, the daemon trips the breaker, halts monitoring, and terminates the rogue agent PID.

## 5. Concurrency & I/O Locks
Windows enforces strict file locking. Concurrent access between the Oracle and active agents will crash processes with `PermissionError`.
**Solutions:**
* Ensure `job_board.db` utilizes the WAL engine and `busy_timeout=5000`.
* The Oracle must employ **Atomic Rename** awareness (listening only for file creations/moves, ignoring `.tmp` writes) or use file sharing flags with exponential backoff.
* Implement **Event Debouncing** with a temporal filter (e.g., 500ms) to consolidate I/O noise before processing.
