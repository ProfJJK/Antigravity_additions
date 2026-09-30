# CoChem Kanban Pipeline v2 — SRS Part 4

> Continuation of `SRS-COCHEM-KANBAN-PIPELINE-V2-20260921.md`.
> Covers §5 Phase 3–4, §6, §7, §8, §9 and Appendices A–B.
> Part manifest and reading order: Part 1, §0.1.

---

### Phase 3 — Verification suites

The suites are built **after** the modules they exercise, but they are not an afterthought: Phase 3
is a hard gate (§7, G-BUILD) and no v2 code reaches production without it. Every suite runs inside
the container against `/data/test_kanban_v2.db` with a stub gateway, so Phase 3 consumes zero LLM
quota and cannot reach the production database. Full specifications in §6.

### Task 3.01: Test harness — `conftest.py`, stub gateway, DB fixtures

- **Agent**: cochem-tester
- **Priority**: 1
- **Depends on**: 2.03, 2.05
- **Acceptance criteria**:
  - `tmp_queue` fixture builds a fresh DB by running the real `002`/`003` migrations — never a
    hand-written `CREATE TABLE`. A fixture schema that has drifted from the migration is a test
    suite that validates nothing.
  - `stub_gateway` fixture serves the real `/generate` contract (§3.7) on an ephemeral port with
    programmable per-agent responses, injectable latency, and injectable failures: `429`, `503`,
    timeout, malformed JSON, empty `content`.
  - `spoofing_agent` fixture returns a plausible prose summary claiming files were written while
    writing nothing to `/workspace` — the fixture that reproduces v1 defect D-10 on demand.
  - `honest_agent` fixture actually writes files into the mounted workspace, so `PASS` paths are
    exercised against real bytes rather than mocks.
  - `freeze_clock` fixture (freezegun) drives lease expiry and backoff deterministically; no test
    may contain a real `sleep` longer than 0.5 s.
  - Zero network egress: a session-scoped autouse fixture asserts `V2_LLM_GATEWAY_URL` points at
    the stub and fails the session otherwise.
- **Prompt template**:

  > IMPLEMENTATION TASK 3.01: Create `d:\__CoChem\__agentic\v2\tests\conftest.py` providing the fixtures every other v2 suite depends on. The tmp_queue fixture must create each test database by executing the real `db_migrations/002_v2_queue.sql` and `003_v2_indexes.sql` files against a temporary path, never by hand writing CREATE TABLE statements, because a fixture schema that drifts from the migration produces a suite that passes while production breaks. The stub_gateway fixture must serve the genuine `/generate` request and response contract from SRS section 3.7 on an ephemeral port using FastAPI and uvicorn in a background thread, with programmable per agent responses and injectable failure modes for HTTP 429, HTTP 503, connection timeout, malformed JSON body, and an empty content string, so quota and fault tests drive real HTTP rather than monkeypatched functions. Provide a spoofing_agent fixture that returns a confident prose summary claiming it created several files while writing nothing at all to the workspace, and an honest_agent fixture that genuinely writes files into the mounted workspace directory, so both audit outcomes are exercised against real disk state. Provide a freeze_clock fixture built on freezegun so lease expiry, heartbeat staleness and exponential backoff are driven deterministically, and ensure no test anywhere in the suite sleeps for longer than half a second. Add a session scoped autouse fixture that asserts the gateway URL environment variable points at the stub and aborts the entire session otherwise, so a misconfigured run can never spend real subscription quota. Write `tests/test_conftest_selftest.py` proving each fixture does what it claims, including that spoofing_agent leaves the workspace byte for byte unchanged. Run the tests and paste the real output.

### Task 3.02: Correctness suites — `test_queue_manager.py`, `test_race_conditions.py`, `test_recovery.py`

- **Agent**: cochem-tester
- **Priority**: 1
- **Depends on**: 3.01
- **Acceptance criteria**:
  - Every transition in the §3.2.2 matrix is asserted legal; every transition absent from it raises
    `IllegalTransition`. The test enumerates the full 11×11 product — 121 cases — rather than
    spot-checking, so a future edit to `LEGAL_TRANSITIONS` cannot silently widen the state machine.
  - Idempotency: the same `idempotency_key` twice yields `DuplicateIdempotencyKey` carrying the
    original `task_id`; queue depth is unchanged.
  - Backpressure: submitting at `V2_QUEUE_DEPTH_LIMIT` raises `QueueDepthExceeded`.
  - Lock discipline: a mutation presented with a stale `worker_lock_id` raises `TaskNotClaimed`
    and writes nothing — the split-brain guard.
  - 16 threads × 200 tasks: every task claimed exactly once, zero double-claims, zero lost tasks,
    no `SQLITE_BUSY` escaping to the caller.
  - Recovery: a DB seeded with 13 `in-progress` rows holding expired leases — the exact state of
    the host DB on 2026-09-21 — is fully reclaimed by `recover_orphans()`, each row's `claim_count`
    incremented, and a row already at `claim_count = 5` is quarantined rather than requeued.
- **Prompt template**:

  > IMPLEMENTATION TASK 3.02: Create `tests/test_queue_manager.py`, `tests/test_race_conditions.py` and `tests/test_recovery.py` covering SRS section 3.2. For the transition tests, enumerate the complete eleven by eleven product of TaskState values, 121 cases, asserting that exactly the transitions listed in the section 3.2.2 matrix succeed and every other pair raises IllegalTransition, because spot checking a state machine lets a later edit widen it silently. Assert that submitting the same idempotency key twice raises DuplicateIdempotencyKey carrying the original task id and leaves queue depth unchanged, that submitting at the configured queue depth limit raises QueueDepthExceeded, and that any mutation presented with a stale worker lock id raises TaskNotClaimed and writes nothing at all, which is the split brain guard that stops a worker whose lease was stolen from overwriting the new owner's results. For the race tests, run sixteen real threads claiming from a queue of two hundred tasks and assert every task was claimed exactly once with zero double claims, zero lost tasks and no SQLITE_BUSY error reaching the caller, then repeat the run with a deliberately reduced busy timeout to prove the BEGIN IMMEDIATE claim still serialises correctly under contention. For the recovery tests, seed a database with thirteen in progress rows whose leases expired in the past, which is the literal state of the production database on 2026 09 21, and assert a cold boot reclaims all thirteen, increments claim_count on each, emits a LEASE_EXPIRED event per row, and that a row already carrying claim_count of five is quarantined as a poison task instead of being requeued a sixth time. Run the tests and paste the real output.

### Task 3.03: Resilience suites — `test_fault_injection.py`, `test_quota_exhaustion.py`, `test_throughput.py`

- **Agent**: cochem-tester
- **Priority**: 1
- **Depends on**: 3.01, 2.06
- **Acceptance criteria**:
  - `SIGKILL` of a worker mid-execution: the task is recovered, executed exactly once in total,
    and no completion record is lost. Asserted by event-log replay, not by a status poll.
  - Container restart mid-audit: the task resumes from `AWAITING_AUDIT`, not from `QUEUED` —
    execution work is not silently repeated.
  - Gateway returning `429` on every tier: the pipeline pauses **durably** (`system_metadata`
    survives a process restart), the lease is released rather than held indefinitely, and
    `resume_pipeline` drains the backlog. This is the test that v1 could not pass, because its
    pause flag lived in a module-level variable.
  - A task that kills its worker five times is quarantined, not retried forever.
  - 50-task burst reaches `done` with no duplicates at ≥ 8 tasks/min against the stub.
  - Throughput is asserted as a floor, not a fixed value, and the test records the observed rate
    into the JSON report so regression is visible across runs.
- **Prompt template**:

  > IMPLEMENTATION TASK 3.03: Create `tests/test_fault_injection.py`, `tests/test_quota_exhaustion.py` and `tests/test_throughput.py`. The fault injection suite must SIGKILL a worker process in the middle of executing a task and then prove, by replaying the kanban_task_events log rather than by polling a status column, that the task was recovered and executed exactly once in total and that no completion record was lost, which is the precise failure v1 could not survive because its completion ledger was a JSON file it deleted whenever the queue drained. It must also restart the container in the middle of an audit and assert the task resumes from the awaiting audit state rather than from queued, so expensive execution work is never silently repeated, and it must assert that a task which kills its worker five consecutive times is quarantined as poison instead of retried forever. The quota exhaustion suite must drive the stub gateway to return HTTP 429 for every tier of the fallback chain and then assert the pipeline pauses durably by writing to system_metadata rather than to an in memory flag, that the durable pause survives a full process restart, that the in flight lease is released rather than held indefinitely while paused, and that resume_pipeline drains the accumulated backlog correctly. Cover the gateway returning HTTP 503, a connection timeout, a malformed JSON body and an empty content string, asserting each maps to the documented error class and to a requeue with exponential backoff rather than to a crash. The throughput suite must submit a burst of fifty tasks and assert all of them reach done with no duplicates at a rate of at least eight tasks per minute against the stub, asserting a floor rather than an exact figure and writing the observed rate into the JSON report so throughput regressions become visible across runs. Run the tests and paste the real output.

### Task 3.04: Guarantee suites — `test_audit_rejection.py`, `test_telemetry.py`, `test_mcp_auth.py`

- **Agent**: cochem-tester
- **Priority**: 1
- **Depends on**: 3.01, 2.09, 2.10, 2.11
- **Acceptance criteria**:
  - Work produced by `spoofing_agent` never reaches `done` — it reaches `quarantine`. Asserted
    over every claim shape: no files written; files claimed that do not exist; files written
    outside the mounted workspace; a file whose post-execution `sha256` equals its pre-execution
    value (claimed edit, byte-identical file).
  - An empty evidence bundle on a mutating workflow fails the task **without invoking the
    auditor** — proven by asserting the stub gateway received zero `cochem-audit` requests.
  - A forced-symmetric audit (executor and auditor resolved to the same `(provider, model)`)
    yields `asymmetry_ok = 0`, `audit_status='rejected_symmetry'`, and `blocked` — never `done`.
    The test additionally asserts `complete_task()` itself refuses such a row, so the guarantee
    holds even if a future caller skips the audit layer.
  - Every state transition writes exactly one `kanban_telemetry` row, with no `NOT NULL`
    violation on `execution_time_ms`, `cpu_percent`, `vram_used_mb`, `status_verdict`,
    `worker_id`, `state_from`, `state_to`.
  - MCP: absent token → 401; wrong token → 403 and an `AUTH_FAILURE` event; 10 failures in 60 s
    → peer blocked 15 min; each bucket in `RATE_LIMITS` enforced; `429` carries `retry_after_sec`;
    `submit_task` at depth limit → `503`.
- **Prompt template**:

  > IMPLEMENTATION TASK 3.04: Create `tests/test_audit_rejection.py`, `tests/test_telemetry.py` and `tests/test_mcp_auth.py`. The audit rejection suite is the most important suite in the project and must assert that work produced by the spoofing_agent fixture can never reach done and instead reaches quarantine, across every shape of false claim: an agent that writes no files at all, an agent that names files which do not exist on disk, an agent that writes outside the mounted workspace, and an agent that claims to have edited a file whose post execution sha256 is byte for byte identical to its pre execution value. Assert that an empty evidence bundle on a mutating workflow fails the task without the auditor ever being consulted, proving it by asserting the stub gateway received zero requests for the cochem audit agent, because v1 asked an auditor to verify physical work against zero bytes of evidence and the auditor could only guess. Force a symmetric audit in which executor and auditor resolve to the same provider and model pair and assert the result is recorded with asymmetry_ok set to zero, audit status rejected_symmetry and a blocked task, never done, and additionally assert that calling complete_task directly on such a row raises, so the never self verify guarantee holds structurally even if a future caller bypasses the audit layer entirely. The telemetry suite must assert that every state transition writes exactly one kanban_telemetry row and that no row violates the existing NOT NULL constraints on execution_time_ms, cpu_percent, vram_used_mb, status_verdict, worker_id, state_from and state_to, since those constraints already exist in the live schema and a missing value would abort the transaction that carries the task's progress. The MCP suite must assert a missing bearer token returns 401, a wrong token returns 403 and writes an AUTH_FAILURE event, ten failures from one peer inside sixty seconds blocks that peer for fifteen minutes, every bucket in the documented RATE_LIMITS table is enforced, a throttled response carries a retry_after_sec field, and submit_task at the queue depth limit returns 503 with the current depth. Run the tests and paste the real output.

### Task 3.05: `test_cutover_readiness.py` and `scripts/assert_stress_report.py`

- **Agent**: cochem-tester
- **Priority**: 1
- **Depends on**: 3.02, 3.03, 3.04
- **Acceptance criteria**:
  - `test_cutover_readiness.py` evaluates the §7 G-BUILD checklist as executable assertions, so
    "are we ready to cut over" is a command, not a judgement call.
  - It verifies isolation directly: the container cannot open `d:\__CoChem\cochem_kanban.db`, and
    no v2 code path holds a writable handle to it except the single `system_metadata` pointer
    update in `v2_bridge.py`.
  - It verifies the migration firewall: with the 716 legacy `todo` rows present, `claim_task()`
    returns `None`.
  - `assert_stress_report.py` exits non-zero on any failure, any error, any skip that is not
    explicitly allow-listed with a reason, or coverage below the threshold. A skipped test that
    silently passes a gate is how a green suite hides a missing guarantee.
  - `--require-all` additionally asserts all ten suites are present in the report; a suite that
    failed to collect must fail the gate rather than vanish from it.
- **Prompt template**:

  > IMPLEMENTATION TASK 3.05: Create `tests/test_cutover_readiness.py` and `d:\__CoChem\__agentic\v2\scripts\assert_stress_report.py`. The readiness suite must evaluate the G-BUILD checklist from SRS section 7 as executable assertions so that asking whether the system is ready to cut over is a command rather than a judgement call. It must verify isolation directly by asserting that code running inside the container cannot open the host database at `d:\__CoChem\cochem_kanban.db` at all, and that no v2 code path anywhere holds a writable handle to that file except the single system_metadata pipeline pointer update inside v2_bridge.py. It must verify the migration firewall by seeding the seven hundred and sixteen legacy todo rows with queue_version v1 and asserting that claim_task returns None, because if that firewall leaks the first v2 boot dispatches seven hundred and sixteen unbudgeted LLM tasks. It must assert the documented schema version is present in system_metadata, that every index named in the migrations exists, and that PRAGMA integrity_check returns ok. The assert_stress_report script must read the pytest JSON report and exit non zero on any failure, any error, any skipped test that is not explicitly allow listed together with a written reason, or any coverage figure below the threshold passed on the command line, because a skipped test that silently satisfies a gate is exactly how a green suite hides a missing guarantee. With the require all flag it must additionally assert that all ten named suites appear in the report, so a suite that failed to collect fails the gate instead of disappearing from it. Print a readable summary table of suite, passed, failed, skipped and duration. Write `tests/test_assert_stress_report.py` covering a clean report, a report with one failure, a report with an unexplained skip, a report missing a suite and a report below the coverage floor. Run the tests and paste the real output.

### Phase 4 — Cutover tooling and handover

### Task 4.01: Cutover and rollback automation — `scripts/cutover.py`, `scripts/rollback.py`

- **Agent**: cochem-coder
- **Priority**: 1
- **Depends on**: 3.05, 2.12, 2.13
- **Acceptance criteria**:
  - `cutover.py` implements §4.4 Steps 1–6 as discrete, individually re-runnable, idempotent
    steps with `--step N`, `--from-step N` and a mandatory `--dry-run` default.
  - It refuses to proceed when G-BUILD (§7) is not satisfied, re-running
    `assert_stress_report.py` rather than trusting a previous run.
  - The drain step has no timeout and never kills a running v1 task; it reports `fs=` and
    `db_inflight=` counts every 30 s, exactly as §4.4 Step 2 specifies.
  - Step 3 takes a `VACUUM INTO` backup — not a file copy. A file copy of a live WAL database
    loses whatever is sitting in the WAL, which on 2026-09-21 was 4.12 MB against a 1.99 MB main
    file, i.e. the majority of recent writes.
  - Step 6 evaluates V1–V12 and exits non-zero naming every failing check.
  - `rollback.py` implements §4.5 in under 15 minutes wall-clock, deletes nothing, preserves the
    `cochem_v2_db` volume, and exports post-cutover v2 tasks for replay before flipping the
    pointer back.
  - A test proves `rollback.py` is runnable from a half-completed cutover at every step boundary.
- **Prompt template**:

  > IMPLEMENTATION TASK 4.01: Create `d:\__CoChem\__agentic\v2\scripts\cutover.py` and `d:\__CoChem\__agentic\v2\scripts\rollback.py` implementing SRS sections 4.4 and 4.5 as automation rather than as a runbook a tired operator follows at two in the morning. The cutover script must implement steps one through six as discrete, individually re runnable, idempotent steps selectable with a step flag and a from step flag, defaulting to dry run so that an accidental invocation changes nothing. It must refuse to proceed unless the G-BUILD gate from section 7 is satisfied, and it must establish that by actually re running assert_stress_report.py rather than trusting a report from a previous session. The drain step must have no timeout and must never kill a running v1 task, printing the filesystem prompt count and the database in flight count every thirty seconds exactly as section 4.4 step two specifies, because the entire zero downtime claim rests on letting v1 finish what it started. The backup step must use VACUUM INTO and never a file copy, because copying a live WAL database file loses whatever is sitting in the write ahead log, which on this host was 4.12 megabytes against a 1.99 megabyte main file, meaning a file copy would silently discard the majority of recent writes. The verification step must evaluate all twelve checks V1 through V12 from section 4.4 and exit non zero naming every check that failed. The rollback script must implement section 4.5 within fifteen minutes of wall clock time, must delete nothing at all, must preserve the cochem_v2_db volume, and must export any tasks v2 accepted after cutover into a replayable file before flipping the active pipeline pointer back to v1. Write `tests/test_cutover.py` proving every step is idempotent when run twice, that the gate refusal actually blocks a cutover when the stress report contains a failure, and that rollback succeeds from a half completed cutover at every step boundary. Run the tests and paste the real output.

### Task 4.02: Operator runbook and v1 deprecation record

- **Agent**: cochem-scribe
- **Priority**: 2
- **Depends on**: 4.01
- **Acceptance criteria**:
  - `v2/RUNBOOK.md` covers, with the exact command for each: start, stop, graceful drain, inspect
    a task end-to-end from its event log, release a quarantine, pause and resume for quota,
    checkpoint the WAL, rotate the two tokens, read the watchdog's event history, and recover from
    each of the ten watchdog failure modes in §3.6.1.
  - Every command in the runbook is copy-pasteable PowerShell that has been executed once, with
    its real output pasted beneath it. An untested runbook is a liability during an incident.
  - A troubleshooting table maps each documented `error_class` and watchdog `event_type` to a
    first diagnostic step.
  - `v2/DEPRECATION-V1.md` records the 14-day rollback window, what gets archived on day 14, what
    is deliberately kept (the 716 legacy `todo` rows and the 149 `quarantine` rows, pending
    separate triage), and the §9 defect register with each entry marked fixed, obsoleted by the
    rewrite, or still open.
- **Prompt template**:

  > IMPLEMENTATION TASK 4.02: Create `d:\__CoChem\__agentic\v2\RUNBOOK.md` and `d:\__CoChem\__agentic\v2\DEPRECATION-V1.md`. The runbook must cover, giving the exact command for each, how to start the pipeline, stop it, drain it gracefully, inspect any single task end to end from its event log, release a quarantined task with a justification, pause and resume the pipeline for quota, checkpoint the write ahead log, rotate both the MCP token and the gateway token, read the watchdog's event history out of the host database, and recover from each of the ten watchdog failure modes listed in SRS section 3.6.1. Every command must be copy pasteable PowerShell that you have actually executed once, with its real output pasted directly beneath it, because a runbook whose commands have never been run is a liability discovered during an incident rather than before one. Include a troubleshooting table mapping every documented error_class value and every watchdog event_type value to a concrete first diagnostic step. The deprecation record must state the fourteen day rollback window and its expiry date, exactly what is archived when it expires, what is deliberately retained and why, specifically the seven hundred and sixteen legacy todo rows and the one hundred and forty nine quarantine rows which both await separate triage and must not be swept up in a v1 archival, and it must reproduce the defect register from SRS section 9 with every entry marked as fixed in v2, obsoleted by the rewrite, or still open, so that nothing on that list is lost when v1 is finally retired. Run a markdown link checker over both files and paste the real output.

---

## 6. Verification and Stress Test Specification

### 6.0 Principles

Four rules govern every suite. They exist because v1's defects were not caught by the absence of
tests alone — they were caught by nothing, and several would have survived a naive test suite.

1. **Assert on durable state, not on return values.** A test that checks `process_task()` returned
   `True` would have passed against v1's broken audit. Tests assert against `kanban_tasks`,
   `kanban_task_events`, `kanban_audit_results` and `kanban_telemetry` — the rows an operator would
   read during a post-mortem.
2. **Real bytes for physical claims.** Evidence-related tests write and read actual files in the
   mounted workspace. Mocking the filesystem in a suite whose purpose is detecting fabricated
   filesystem work would reproduce v1's exact mistake one layer up.
3. **Real HTTP for the gateway boundary.** The stub gateway is a real server on a real socket. The
   failure modes that matter — timeouts, half-closed connections, malformed bodies — do not exist
   for a monkeypatched function.
4. **No test may consume LLM quota or open the production database.** Enforced by fixture, not by
   convention (§6.1).

### 6.1 Isolation guarantees

| Guarantee | Mechanism | Failure mode if absent |
|---|---|---|
| No LLM spend | Autouse fixture asserts `V2_LLM_GATEWAY_URL` resolves to the stub; the `tests` compose service points it at `http://localhost:9999` | A full suite run against the real gateway could cost hundreds of Fable-5.1 calls |
| No production DB access | `tests` service sets `V2_DB_PATH=/data/test_kanban_v2.db`; the host DB is not mounted into the container at all | A test with a stray absolute path corrupts 2525 live rows |
| No host filesystem writes | Only `/workspace/v2` and `/workspace/dropzones` are writable; reference files are mounted `read_only` | An agent-simulating test scribbling over `llm_router.py` |
| Deterministic time | `freezegun`; no real sleep > 0.5 s | Lease/backoff tests that are flaky on a loaded host, then get disabled |
| Clean slate per test | `tmp_queue` builds a new DB from the real migrations per test | Cross-test state leakage producing order-dependent passes |

### 6.2 Suite specifications

#### `test_queue_manager.py` — the state machine

| Assertion group | Detail |
|---|---|
| Transition completeness | All 121 `(from, to)` pairs enumerated; exactly the §3.2.2 set succeeds |
| Claim correctness | Priority order `1 → 2 → 3`, then `created_at` ascending; `not_before` respected; `depends_on_task_id` gating evaluated in SQL |
| Agent resolution | `COALESCE(assigned_agent, WORKFLOW_AGENT_DEFAULTS[type], 'cochem-coder')` — exercised with `assigned_agent IS NULL`, which is the case for 2524 of 2525 production rows |
| Idempotency | Duplicate key → `DuplicateIdempotencyKey.task_id` is the original; depth unchanged |
| Backpressure | At `V2_QUEUE_DEPTH_LIMIT` → `QueueDepthExceeded` |
| Lock discipline | Stale `worker_lock_id` → `TaskNotClaimed`, zero writes |
| Terminal-state enforcement | `complete_task()` refuses without a `PASS` row with `asymmetry_ok=1`; `done`/`quarantine` reject all outbound transitions |
| Backoff | `not_before = now + min(600, 15·2^retry_count)` ± ≤ 20 % jitter; monotonic, capped |
| WAL hygiene | `checkpoint_wal(truncate=True)` bounds the WAL under sustained write load |

#### `test_race_conditions.py` — concurrency

16 threads, 200 tasks, repeated at `busy_timeout` of 5000 ms and a deliberately hostile 50 ms.
Asserts: each task claimed exactly once; no task unclaimed; no `SQLITE_BUSY` reaches a caller; no
`IllegalTransition` from interleaving; `claim_count` sums to exactly 200. Also asserts two workers
racing `renew_lease` on one task cannot both succeed, and that the loser abandons without writing.

#### `test_recovery.py` — restart and orphan reclamation

Seeds the production shape: 13 `in-progress` rows with `lease_expires_at` in the past and
`worker_lock_id`/`worker_pid` set. Asserts full reclamation on cold boot; `claim_count` incremented
per row; `LEASE_EXPIRED` emitted per row; a row at `claim_count = 5` quarantined with
`last_error` naming the poison condition; a task killed mid-audit resumes at `AWAITING_AUDIT`;
a worker whose `last_heartbeat` is older than `stale_worker_seconds` has its rows reclaimed by a
*different* worker.

#### `test_fault_injection.py` — process and container failure

`SIGKILL` at four points: after claim, mid-execution, after execution before evidence capture, and
mid-audit. Each asserts exactly-once execution by event-log replay and zero lost completions.
Additionally: DB file locked by an external process → `busy_timeout` respected, no crash; disk-full
simulated on `/data` → task requeued, not silently dropped; `/workspace` mounted read-only →
`error_class='workspace_readonly'`, task blocked with a clear message rather than an opaque
`PermissionError`.

#### `test_quota_exhaustion.py` — degradation

All tiers `429` → durable pause via `system_metadata`, lease released, `PAUSED_QUOTA` emitted;
pause survives a process restart; `resume_pipeline` drains the backlog. Each of `503`, timeout,
malformed JSON, and empty `content` maps to its documented `error_class` and to a backoff requeue.
A non-quota error (the stub returns a `NameError` traceback — the shape v1 actually produced from
its Ollama tier, D-13) must **not** be misclassified as quota and must not pause the pipeline.

#### `test_audit_rejection.py` — the anti-spoofing guarantee

The suite that justifies the rewrite. Cases, all asserting the task never reaches `done`:

| Case | Expected |
|---|---|
| Agent writes nothing, claims three files | `evidence_files == 0` → `FAIL`, `no_physical_change`, auditor never called |
| Agent claims files that do not exist | Absent files listed in the bundle; `SPOOFING_DETECTED` → `quarantine` |
| Agent writes outside `/workspace` | Write fails; `error_class='workspace_escape'`; quarantine |
| Claimed edit, `sha256` unchanged pre/post | `SPOOFING_DETECTED` → `quarantine` |
| Files changed on disk but not claimed | Surfaced in the bundle's unclaimed-changes section; audit proceeds with the discrepancy visible |
| Executor and auditor share `(provider, model)` | `asymmetry_ok = 0`, `rejected_symmetry`, `blocked`; `complete_task()` raises |
| Auditor returns malformed output | `INDETERMINATE`, revision dispatched; never an implicit `PASS` |
| Honest agent, real files, real diff | `PASS`, `score ≥ 85`, `asymmetry_ok = 1` → `done` |

The last row matters as much as the others: a gate that fails everything is as useless as one that
passes everything, and v1's gate in fact failed everything (D-09).

#### `test_throughput.py` — capacity

50-task burst at `V2_MAX_CONCURRENCY=3`: all `done`, no duplicates, ≥ 8 tasks/min against the stub.
Concurrency is asserted directly — peak simultaneous in-flight equals 3 and never exceeds it — and
head-of-line blocking is asserted absent: one task that fails all its cycles must not delay the 49
behind it. Observed rate is written to the report for cross-run comparison.

#### `test_telemetry.py` — observability

Every transition produces exactly one `kanban_telemetry` row. No `NOT NULL` violation on the
columns the live schema already constrains. `agent_name`, `provider`, `model`, `queue_version`,
`error_class` and `cost_usd` are populated from the gateway's actual response, not from the
registry's nominal primary — so a task degraded to Gemini records Gemini. Asserts ≥ 6 rows per
completed task (matching V7), and that `kanban_task_events` covers ≥ 6 distinct `event_type`
values for a normal lifecycle (matching V8).

#### `test_mcp_auth.py` — access control

401 on missing token; 403 plus `AUTH_FAILURE` on a wrong one; constant-time comparison exercised;
10 failures in 60 s → 15-minute peer block; every `RATE_LIMITS` bucket enforced with
`retry_after_sec` on `429`; `submit_task` at depth limit → `503` with current depth;
`release_quarantine` refuses a justification under 20 characters; `healthz` unauthenticated only
from loopback; every subprocess call carries a timeout (the direct regression test for v1's
unbounded `subprocess.run`, D-18).

#### `test_cutover_readiness.py` — the gate as code

G-BUILD (§7) evaluated as assertions; container cannot open the host DB; no writable handle to the
host DB outside the single `system_metadata` pointer update; the 716 legacy rows are unclaimable;
schema version present; every documented index exists; `PRAGMA integrity_check` returns `ok`.

### 6.3 Coverage and reporting

`--cov=/app --cov-report=term-missing`, floor **80 %** overall and **95 %** on
`kanban_queue_manager.py` and `audit_layer.py`. Those two modules carry the correctness and
anti-spoofing guarantees; a 20 % blind spot in either is not acceptable at any overall figure.

`assert_stress_report.py` is the gate. It fails on any failure, any error, any skip without an
allow-listed written reason, any missing suite under `--require-all`, and any coverage below
threshold. The report is written to `d:\__CoChem\__agentic\v2\.test-reports\stress-report.json`
and retained per cutover attempt.

---

## 7. Acceptance Criteria

Three sequential gates. Each is objective and each is evaluated by a command, not by opinion.

### 7.1 G-BUILD — v2 may be cut over

| # | Criterion | Evidence |
|---|---|---|
| B1 | All 26 WBS tasks `done` with an asymmetric `PASS` on record | `SELECT COUNT(*) FROM kanban_audit_results WHERE verdict='PASS' AND asymmetry_ok=1` ≥ 26 |
| B2 | All ten suites pass | `assert_stress_report.py --require-all --min-coverage 80` exits 0 |
| B3 | Coverage floors met | 80 % overall; 95 % on `kanban_queue_manager.py` and `audit_layer.py` |
| B4 | Containers healthy ≥ 60 min continuously | `docker inspect` health `healthy`; no restart in the window |
| B5 | Isolation proven | Container cannot open the host DB; host DB row count unchanged at 2525 |
| B6 | Migration firewall proven | With 716 legacy `todo` rows present, `claim_task()` returns `None` |
| B7 | Zero `done` tasks with `asymmetry_ok=0` | `SELECT COUNT(*) ... WHERE asymmetry_ok=0` on `done` tasks = 0 |
| B8 | Watchdog verified against all ten failure modes | `test_watchdog.py` passes; `daemon_watchdog_events` rows present for each simulated mode |
| B9 | Rollback rehearsed | `rollback.py` executed from a half-cutover in a test context, under 15 min, nothing deleted |
| B10 | Secrets not in source control or logs | Both tokens ≥ 32 chars, sourced from `.env`; grep of `v2/` and `/logs` finds neither value |
| B11 | `PRAGMA integrity_check` = `ok` on both databases | Run on host DB and v2 volume DB |
| B12 | Both v1 fixes deployed and verified | Tasks 1.01–1.02 done; `grep -c daemon_telem cochem_kanban.py` = 0; both presentation subcommands dispatch |

### 7.2 G-CUTOVER — cutover is complete

All of V1–V12 (§4.4 Step 6) pass within 30 minutes of activation. Any failure triggers §4.5
immediately; a half-cutover system is not debugged forward.

### 7.3 G-STEADY — v1 may be archived (day 14)

| # | Criterion | Threshold |
|---|---|---|
| S1 | Continuous uptime | 14 days, no unplanned outage > 15 min |
| S2 | Unrecovered task loss | Zero. Every submitted task reaches a terminal state or is explainable from its event log |
| S3 | `done` tasks with `asymmetry_ok=0` | Zero |
| S4 | Watchdog restarts | ≤ 3 in 14 days; zero `RESTART_BUDGET_EXHAUSTED` |
| S5 | Telemetry completeness | ≥ 1 row per transition, no gaps |
| S6 | WAL bounded | `< 64 MB` at every sample |
| S7 | Spoofing gate exercised | ≥ 1 real `SPOOFING_DETECTED` or a documented explanation of why none occurred |
| S8 | BSOD count | Zero attributable to the pipeline |
| S9 | Operator confidence | Runbook used at least once for a real incident without escalation |

Only when S1–S9 hold is v1 archived. Until then it stays on disk, unmodified, runnable.

### 7.4 Traceability

Every v1 defect maps to the requirement that fixes it and the test that proves it.

| Defect (§9) | Fixed by | Proven by |
|---|---|---|
| D-01 `daemon_telem` NameError | Task 1.01 | B12; `test_cutover_readiness.py` |
| D-02 missing presentation triggers | Task 1.02, §3.5.2 | `test_mcp_auth.py` tool inventory |
| D-03 head-of-line blocking | §3.2.3 `fail_task`, §3.3.3 | `test_throughput.py` |
| D-04 non-transactional queue | §3.2.3 `claim_task` | `test_race_conditions.py` |
| D-05 state file deleted on drain | §3.2.1 durable `kanban_tasks` | `test_fault_injection.py` |
| D-06 non-atomic `save_state` | §3.2.4 transactions | `test_fault_injection.py` |
| D-07 orphaned `kanban_tasks` | §3.2.1, §3.2.3 | `test_queue_manager.py` |
| D-08 no boot recovery | §3.2.3 `recover_orphans` | `test_recovery.py` |
| D-09 audit verdict always `FAIL` | §3.4 typed gateway contract | `test_audit_rejection.py` (honest path) |
| D-10 evidence bundle always empty | §3.3.5, §3.4.1 | `test_audit_rejection.py` |
| D-11 `break`/`continue` evidence truncation | §3.3.5 | `test_audit_rejection.py` |
| D-12 dead spoofing branch | §3.4.4 | `test_audit_rejection.py` |
| D-13 Ollama `json` NameError | §3.7 gateway, Task 2.04 | `test_quota_exhaustion.py` |
| D-14 dead `run_cmd` fallback | §3.7 gateway | `test_quota_exhaustion.py` |
| D-15 council bypasses router | Task 2.08 | `test_pivot.py`, `test_quota_exhaustion.py` |
| D-16 in-RAM pivot counts | §3.2.1 `kanban_pivot_state` | `test_recovery.py` |
| D-17 in-memory quota flag | §3.2.3 `pause_pipeline` | `test_quota_exhaustion.py` |
| D-18 MCP unbounded subprocess, no auth | §3.5.3–3.5.5 | `test_mcp_auth.py` |
| D-19 unbounded spawn, no resource bounds | §3.1.4 limits | `test_throughput.py`, watchdog checks |
| D-20 no telemetry | §3.3.7 | `test_telemetry.py` |
| D-21 WAL never checkpointed | §3.1.5, §3.2.3 `checkpoint_wal` | `test_queue_manager.py`; S6 |
| D-22 stale model IDs in registry | ADR-006, Task 2.04 | `test_quota_exhaustion.py` chain assertions |
