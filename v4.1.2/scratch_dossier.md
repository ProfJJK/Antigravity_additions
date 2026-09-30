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
- Module con
<truncated 6588 bytes>
test_emit_recovery_event_execution_and_row_id` | `[AC1]` | Invokes `emit_recovery_event({"tier": 1, "action": "test"})`. Asserts return value `row_id >= 1` and row exists in DB. |
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