# Context Dossier: Host Warden Recovery Event Telemetry Notification Sink (`emit_recovery_event`)

**Target Module:** [`src/cochem/warden/ladder.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/ladder.py)  
**Task Identifier:** `MC-HW-55` / `SRS-412-01-FR-006`  
**Role:** P2 Researcher Briefing for Implementation Engineer  

---

## 1. Executive Summary & Scope

This dossier equips the implementation engineer to finalize and verify `emit_recovery_event()` in [`src/cochem/warden/ladder.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/ladder.py). The function serves as the Host Warden's escalation audit ledger sink, persisting escalation and recovery events to the `recovery_telemetry` table in SQLite (`job_board.db` or an injected database handle).

### In-Scope Boundaries
- Function API signature, argument polymorphism, and connection lifecycle management in [`ladder.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/ladder.py).
- Auto-creation of the `recovery_telemetry` table if not present.
- Safe serialization of event dictionaries into SQLite rows with incrementing integer primary keys.
- Verification against Acceptance Criteria `[AC1]`–`[AC6]` and Test Cases `[T1]`–`[T6]`.
- Strict compliance with CoChem Zero-Mock and Anti-Spoofing Protocols.

### Explicitly Out-of-Scope
- `HealthEscalationLadder` state transitions and FSM step logic (`MC-HW-54`).
- FastMCP tool registration and server exposure (`MC-HW-58`, in [`src/cochem/warden/mcp_server.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/mcp_server.py)).
- Direct Hyper-V execution cmdlets (`Restart-VM`, `Restore-VMSnapshot`) and PowerShell IPC bridges.
- Remote telemetry streaming, syslog export, or distributed queue synchronization.
- Named-pipe guest health monitoring loops (`MC-HW-42`).

---

## 2. Existing Code & Reusable Patterns

### 2.1 File Target: `src/cochem/warden/ladder.py`
[`src/cochem/warden/ladder.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/ladder.py) currently houses:
- Module constants:
  - `REPO_ROOT: Path = Path(__file__).resolve().parents[3]`
  - `DEFAULT_DB_PATH: Path = REPO_ROOT / "job_board.db"`
- `HealthEscalationLadder`: The 3-tier FSM managing recovery actions (`service_restart` $\to$ `vm_reboot` $\to$ `golden_rollback`).
- An initial baseline for `emit_recovery_event()` (lines 69–115).

### 2.2 Telemetry & Serialization Patterns
Inspect [`src/cochem/warden/telemetry.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/telemetry.py) for project telemetry conventions:
- Use standard `json.dumps(...)` with `ensure_ascii=False` when serializing structured metadata payloads.
- Preserve caller metadata without mutating input dictionaries.

### 2.3 AST Audit & Anti-Spoof Linter
The test suite enforces zero-mock invariants via AST parsing and [`AntiSpoofLinter`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/dsp/forge/linter.py) from [`src/cochem/dsp/forge/linter.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/dsp/forge/linter.py). Reference [`tests/test_compliance_audit.py`](file:///D:/__CoChem/__agentic/v4.1.2/tests/test_compliance_audit.py) lines 18–40 for exact AST traversal assertions banning `ast.Pass`, `NotImplementedError`, and `unittest.mock`.

---

## 3. Exact APIs, Signatures & Data Schemas

### 3.1 Function Signature
```python
def emit_recovery_event(
    conn: sqlite3.Connection | str | Path | None = None,
    event_data: dict[str, Any] | None = None,
) -> int:
    """Emits escalation audit record to telemetry log table in job_board.db (or provided SQLite conn)."""
```

### 3.2 Dual Calling Conventions (Polymorphism)
Per `[AC1]` and `[AC3]`, the function must transparently support both single-argument and two-argument invocations:
1. **Single Argument (Event Dict):**
   `emit_recovery_event({"tier": 1, "action": "service_restart", ...})`
   *Here, `conn` receives the `dict` and `event_data` is `None`.* The implementation must detect `isinstance(conn, dict)` and redirect `event_data = conn`, setting `conn = None` to trigger fallback to `DEFAULT_DB_PATH`.
2. **Two Arguments (Connection + Event Dict):**
   `emit_recovery_event(conn, {"tier": 2, "action": "vm_reboot"})`
   *`conn` is an open `sqlite3.Connection`, path string, or `Path` object.*
3. **Keyword Arguments:**
   `emit_recovery_event(event_data={...})` or `emit_recovery_event(conn=..., event_data={...})`.
4. **Zero Arguments / Defaults:**
   `emit_recovery_event()` defaults `event_data` to `{}` and targets `DEFAULT_DB_PATH`.

### 3.3 Database Schema: `recovery_telemetry`
Per `[AC2]` and `[T2]`, the schema must match `PRAGMA table_info` expectations:

```sql
CREATE TABLE IF NOT EXISTS recovery_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    tier INTEGER NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL
)
```

| Column Name | SQLite Affinity | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | Unique monotonic recovery audit identifier. |
| `timestamp` | `TEXT` | `NOT NULL DEFAULT (datetime('now'))` | ISO/UTC string timestamp generated by SQLite. |
| `tier` | `INTEGER` | `NOT NULL` | Escalation tier (1, 2, or 3; default: 1). |
| `action` | `TEXT` | `NOT NULL` | Action label (default: `'unspecified_recovery'`). |
| `details` | `TEXT` | `NOT NULL` | Serialized JSON string of the complete input payload. |

### 3.4 Return Value
- Returns `int(cur.lastrowid)`: A positive integer (`id >= 1`) representing the inserted primary key.

---

## 4. Hard Constraints & Execution Invariants

1. **Zero-Mock Rule (`[AC6]`):**
   - Strictly forbidden: `import mock`, `from unittest import mock`, `MagicMock`, `monkeypatch`.
   - Forbidden syntax: `pass` statements (use explicit returns or context management instead) and `raise NotImplementedError`.
   - All tests must use real SQLite connections (`:memory:` or on-disk `tmp_path`).
2. **Connection Lifecycle Ownership (`[AC3]`):**
   - **Injected `sqlite3.Connection`:** Must **NEVER** be closed by `emit_recovery_event`. The caller retains ownership.
   - **Path / String / Default Path:** Must be created inside the function and **GUARANTEED** closed via `try ... finally` block.
3. **SQLite WAL / Transaction Integrity:**
   - Changes must be explicitly committed via `active_conn.commit()`.
   - No slow I/O, subprocess calls, or LLM network invocations inside open SQLite write transactions.
4. **Windows OS Specifics:**
   - On Windows, unclosed SQLite handles cause `PermissionError: [WinError 32]` during file unlinking or directory cleanup. Proper `finally: active_conn.close()` on path-based connections is mandatory.
   - Text operations and files must specify `encoding='utf-8'`.

---

## 5. Implementation Pitfalls & Edge Cases

### Pitfall 1: Explicit `None` Values in Payload Dictionaries
*Trap:* Using `int(event_data.get("tier", 1))` fails if the caller explicitly supplies `{"tier": None}`. In Python, `dict.get(key, default)` only returns `default` if the key is **absent**. If `{"tier": None}` is passed, `event_data.get("tier", 1)` evaluates to `None`, causing `int(None)` to raise `TypeError`.  
*Resolution:* Normalize cleanly:
```python
raw_tier = event_data.get("tier")
tier = int(raw_tier) if raw_tier is not None else 1

raw_action = event_data.get("action")
action = str(raw_action) if raw_action is not None else "unspecified_recovery"
```

### Pitfall 2: Polymorphic Dispatch with Falsy Arguments
*Trap:* Relying on `if not conn and event_data:` fails when an empty string `""` or invalid falsy object is passed.  
*Resolution:* Explicit type testing:
```python
if isinstance(conn, dict) and event_data is None:
    event_data = conn
    conn = None
```

### Pitfall 3: Column Mapping & `details` Preservation
*Trap:* Stripping `tier` and `action` from the dictionary before JSON serialization.  
*Resolution:* `[AC4]` mandates that the `details` column contains the **complete** event dictionary (including `tier`, `action`, and all custom metadata keys such as `{"vm_name": "...", "reason": "timeout"}`). Do not mutate or pop keys from `event_data`.

### Pitfall 4: `lastrowid` Edge Cases
*Trap:* `cur.lastrowid` can theoretically return `None` or `0` on uncommitted or non-insert operations.  
*Resolution:* Ensure `cur.lastrowid is not None and cur.lastrowid > 0`. Cast to `int(cur.lastrowid)`.

---

## 6. Test Mapping & Verification Strategy

The implementing engineer should ensure the test suite covers all 6 test specifications:

| Test Case | Acceptance Criteria | Assertion Focus |
|---|---|---|
| `[T1] test_emit_recovery_event_execution_and_row_id` | `[AC1]` | Invokes `emit_recovery_event({"tier": 1, "action": "test"})`. Asserts return value `row_id >= 1` and row exists in DB. |
| `[T2] test_recovery_telemetry_schema_definition` | `[AC2]` | Queries `PRAGMA table_info(recovery_telemetry)`. Asserts columns `id`, `timestamp`, `tier`, `action`, `details` and their exact types. |
| `[T3] test_emit_recovery_event_connection_injection_and_lifecycle` | `[AC3]` | 1) Injected `sqlite3.Connection`: executes query afterwards to prove connection remained open.<br>2) Path string / `Path`: unlinks the database file after emit without hitting Windows file-lock errors. |
| `[T4] test_emit_recovery_event_payload_serialization_and_defaults` | `[AC4]` | Emits `{}`. Asserts `tier == 1`, `action == "unspecified_recovery"`. Emits custom keys (`{"custom_uuid": "xyz"}`) and checks `json.loads(row["details"])["custom_uuid"] == "xyz"`. |
| `[T5] test_emit_recovery_event_sequential_multi_tier_logging` | `[AC5]` | Consecutive calls for Tier 1, Tier 2, and Tier 3. Asserts monotonic incrementing IDs (`id1 < id2 < id3`) and correct tier values stored in sequential order. |
| `[T6] test_ladder_zero_mock_anti_spoof_compliance` | `[AC6]` | AST parser reads [`src/cochem/warden/ladder.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/ladder.py). Asserts 0 `ast.Pass`, 0 `NotImplementedError`, and 0 imports/usages of `mock`, `MagicMock`, `monkeypatch`. |

---

## 7. Deliverable Checklist for the Engineer

- [ ] Confirm [`src/cochem/warden/ladder.py`](file:///D:/__CoChem/__agentic/v4.1.2/src/cochem/warden/ladder.py) adheres to line and WBS bounds.
- [ ] Ensure `emit_recovery_event` handles dual dispatch (`conn` as dict vs connection).
- [ ] Verify `details` retains 100% of event dictionary metadata as valid JSON.
- [ ] Confirm injected `sqlite3.Connection` instances are left open, while path-created connections are closed in `finally`.
- [ ] Run `pytest` against the test cases; verify clean execution without mock warnings or AST violations.