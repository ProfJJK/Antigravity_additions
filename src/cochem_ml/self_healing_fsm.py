"""8D Root Cause Analysis self-healing state machine (Task 2.16, WP-2.0).

Disciplines D0..D8 are enforced in order. Every transition is written to an
append-only ledger of ``StateTransitionLedgerEntry`` objects whose SHA-256
digests are chained back to a genesis digest. The pivot ceiling
(``MAX_PIVOT_CYCLES``) is a hard tripwire: exceeding it seals the machine in
``HARD_ABORT``, writes an autopsy dossier for ``cochem-debug`` and raises
``HardAbortTriggered``.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from .fault_ontology import (
    GENESIS_HASH,
    CategoricalFaultRecord,
    FaultCategory,
    FaultOntologyRegistry,
    RemediationStrategy,
    coerce_category,
    coerce_strategy,
    is_sha256_hex,
)
from .prompt_modifier import calculate_decoding_temperature, generate_directives_for_fault

MAX_PIVOT_CYCLES = 3
HARD_ABORT_TRIPWIRE_CANONICAL = "[HARD_ABORT: PHYSICS WALL]"
CANONICAL_PRESIDIUM_ROLES: Tuple[str, ...] = (
    "0rchestrator",
    "cochem-sdp-manager",
    "cochem-audit",
    "adversary",
    "cochem-improve",
    "cochem-scribe",
    "cochem-coder",
    "cochem-tester",
    "cochem-debug",
)
COUNCIL_IMMUNE_ROLES = frozenset(CANONICAL_PRESIDIUM_ROLES)
DEFAULT_ACCOUNTABLE_ROLE = "0rchestrator"
FORBIDDEN_ACCOUNTABLE_ROLE = "cochem-coder"
AUTOPSY_ROLE = "cochem-debug"
STALE_LOCK_AGE_S = 600.0
BRANCH_FREEZE_SENTINEL_NAME = "branch_freeze.sentinel"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_WINDOWS_STILL_ACTIVE = 259
_WINDOWS_ACCESS_DENIED = 5
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class EightDStateMachineError(RuntimeError):
    """Illegal transition or malformed discipline input."""


class InvariantBreachError(EightDStateMachineError):
    """A governance invariant (RACI, unanimity, roster) was violated."""


class AsymmetricVerificationError(InvariantBreachError):
    """An agent attempted to hold verification authority over its own work."""


class HardAbortTriggered(EightDStateMachineError):
    """The pivot ceiling was breached; the machine is sealed."""


class EightDState(str, Enum):
    IDLE = "IDLE"
    D0_EMERGENCY_CONTAINMENT = "D0_EMERGENCY_CONTAINMENT"
    D1_TEAM_FORMATION = "D1_TEAM_FORMATION"
    D2_PROBLEM_DESCRIPTION = "D2_PROBLEM_DESCRIPTION"
    D3_INTERIM_CONTAINMENT = "D3_INTERIM_CONTAINMENT"
    D4_ROOT_CAUSE_ANALYSIS = "D4_ROOT_CAUSE_ANALYSIS"
    D5_PERMANENT_CORRECTIVE_ACTIONS = "D5_PERMANENT_CORRECTIVE_ACTIONS"
    D6_CORRECTIVE_EXECUTION = "D6_CORRECTIVE_EXECUTION"
    D7_PREVENTIVE_ACTIONS = "D7_PREVENTIVE_ACTIONS"
    D8_RATIFICATION_AND_RELEASE = "D8_RATIFICATION_AND_RELEASE"
    HARD_ABORT = "HARD_ABORT"


class RACIAuthority(str, Enum):
    RESPONSIBLE = "R"
    ACCOUNTABLE = "A"
    CONSULTED = "C"
    INFORMED = "I"


class FishboneCategory(str, Enum):
    CODE = "CODE"
    PHYSICS = "PHYSICS"
    PROCESS = "PROCESS"
    ENVIRONMENT = "ENVIRONMENT"
    TOOLING = "TOOLING"
    MEASUREMENT = "MEASUREMENT"


@dataclass(frozen=True)
class PresidiumMember:
    role_identifier: str
    raci: RACIAuthority
    seat_index: int
    mandate: str


@dataclass(frozen=True)
class ProblemDescription5W2H:
    what: str
    where: str
    when: str
    who: str
    why: str
    how: str
    how_many: str


@dataclass(frozen=True)
class InterimContainmentAction:
    action_id: str
    action_type: str
    description: str
    target: str
    evidence: str
    executed: bool = True


@dataclass(frozen=True)
class FiveWhysAnalysis:
    whys: Sequence[Union[str, Tuple[str, str]]]
    fishbone_category: FishboneCategory
    root_cause_summary: str
    identified_fault_category: Optional[FaultCategory] = None
    detection_gap_explanation: str = ""

    def normalized_chain(self) -> List[Tuple[str, str]]:
        chain: List[Tuple[str, str]] = []
        for level, item in enumerate(self.whys, start=1):
            if isinstance(item, str):
                question, answer = f"Why (level {level})?", item
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                question, answer = str(item[0]), str(item[1])
            else:
                raise EightDStateMachineError(f"why level {level} is not a string or (question, answer) pair")
            if not question.strip() or not answer.strip():
                raise EightDStateMachineError(f"why level {level} is empty")
            chain.append((question, answer))
        return chain


@dataclass(frozen=True)
class PermanentCorrectiveAction:
    pca_id: str
    description: str
    remediation_strategy: RemediationStrategy
    target_recovery_role: str
    verification_criteria: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chain_digest(
    predecessor_hash: str,
    sequence: int,
    incident_id: str,
    from_state: EightDState,
    to_state: EightDState,
    pivot_cycle: int,
    timestamp: str,
    event: str,
    actor_role: str,
    payload_digest: str,
) -> str:
    material = "|".join(
        (
            predecessor_hash,
            str(sequence),
            incident_id,
            EightDState(from_state).value,
            EightDState(to_state).value,
            str(pivot_cycle),
            timestamp,
            event,
            actor_role,
            payload_digest,
        )
    )
    return _sha256(material)


@dataclass(frozen=True)
class StateTransitionLedgerEntry:
    sequence: int
    incident_id: str
    from_state: EightDState
    to_state: EightDState
    pivot_cycle: int
    timestamp: str
    event: str
    actor_role: str
    payload_json: str
    payload_digest: str
    predecessor_hash: str
    state_digest: str

    def payload_intact(self) -> bool:
        return _sha256(self.payload_json) == self.payload_digest

    def recompute_digest(self) -> str:
        return _chain_digest(
            self.predecessor_hash, self.sequence, self.incident_id, self.from_state,
            self.to_state, self.pivot_cycle, self.timestamp, self.event,
            self.actor_role, self.payload_digest,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(staging, path)
    return path


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text) or "incident"


def _windows_pid_alive(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    get_exit_code.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        # Access denied means the process exists but belongs to someone else.
        return ctypes.get_last_error() == _WINDOWS_ACCESS_DENIED  # type: ignore[attr-defined]
    try:
        code = ctypes.c_uint32(0)
        if not get_exit_code(handle, ctypes.byref(code)):
            return True  # cannot prove death -> treat as alive (Invariant 9)
        return code.value == _WINDOWS_STILL_ACTIVE
    finally:
        close_handle(handle)


def pid_is_alive(pid: int) -> bool:
    """Read-only liveness probe; it never signals or terminates the process.

    On Windows ``os.kill`` would call TerminateProcess, so a query-only
    OpenProcess handle is used instead. On POSIX, signal 0 performs only an
    existence/permission check.
    """
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock_holder(path: Path) -> Tuple[Optional[int], Optional[str], str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data: Any = json.loads(raw)
    except ValueError:
        data = raw.strip()
    pid_value: Any = None
    role: Optional[str] = None
    if isinstance(data, dict):
        pid_value = data.get("pid")
        role_value = data.get("holder_role") or data.get("role")
        role = str(role_value) if role_value is not None else None
    elif isinstance(data, int) and not isinstance(data, bool):
        pid_value = data
    elif isinstance(data, str) and data.isdigit():
        pid_value = int(data)
    pid: Optional[int] = None
    if isinstance(pid_value, int) and not isinstance(pid_value, bool):
        pid = pid_value
    elif isinstance(pid_value, str) and pid_value.strip().isdigit():
        pid = int(pid_value.strip())
    return pid, role, raw


def _git_head(root: Path) -> Optional[str]:
    if not (root / ".git").exists():
        return None
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=_NO_WINDOW,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


class EightDRCAStateMachine:
    """Closed-loop 8D RCA self-healing state machine."""

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        genesis_digest: str = GENESIS_HASH,
        max_pivot_cycles: int = MAX_PIVOT_CYCLES,
        remote_ledger: Optional[Any] = None,
    ) -> None:
        if not is_sha256_hex(genesis_digest):
            raise ValueError(f"genesis_digest must be a 64-char lowercase hex digest: {genesis_digest!r}")
        if isinstance(max_pivot_cycles, bool) or not isinstance(max_pivot_cycles, int) or max_pivot_cycles < 1:
            raise ValueError(f"max_pivot_cycles must be an integer >= 1, got {max_pivot_cycles!r}")
        root = Path(workspace_root) if workspace_root is not None else Path.cwd()
        self.workspace_root: Path = root.resolve()
        self.genesis_digest: str = genesis_digest
        self.max_pivot_cycles: int = max_pivot_cycles
        self.remote_ledger = remote_ledger
        self.registry = FaultOntologyRegistry()
        self.current_state: EightDState = EightDState.IDLE
        self.ledger: List[StateTransitionLedgerEntry] = []
        self.pivot_cycle_count: int = 0
        self.incident_id: Optional[str] = None
        self.target_module: Optional[str] = None
        self.target_work_package: Optional[str] = None
        self.fault_record: Optional[CategoricalFaultRecord] = None
        self.decoding_temperature: Optional[float] = None
        self.top_p: Optional[float] = None
        self.directives: List[str] = []
        self.quarantine_gate_active: bool = False
        self.freeze_sentinel_path: Optional[Path] = None
        self.autopsy_report_path: Optional[Path] = None
        self.adversary_alert_path: Optional[Path] = None
        self.last_verification_error: Optional[str] = None
        self._reset_incident_artifacts()

    # ------------------------------------------------------------------ helpers
    def _reset_incident_artifacts(self) -> None:
        self.presidium: List[PresidiumMember] = []
        self.problem_description: Optional[ProblemDescription5W2H] = None
        self.containment_actions: List[InterimContainmentAction] = []
        self.root_cause_analysis: Optional[FiveWhysAnalysis] = None
        self.corrective_actions: List[PermanentCorrectiveAction] = []
        self.execution_verified: bool = False
        self.preventive_measures: List[str] = []
        self.resolution_dossier: Optional[Dict[str, Any]] = None

    @property
    def pivots_remaining(self) -> int:
        return max(0, self.max_pivot_cycles - self.pivot_cycle_count)

    def _head_digest(self) -> str:
        return self.ledger[-1].state_digest if self.ledger else self.genesis_digest

    def _record(self) -> CategoricalFaultRecord:
        if self.fault_record is None:
            raise EightDStateMachineError("no fault record is bound; trigger D0 first")
        return self.fault_record

    def _responsible_role(self) -> str:
        return self.registry.get_spec(self._record().category).responsible_role

    def _append_ledger(self, to_state: EightDState, event: str, actor_role: str,
                       payload: Dict[str, Any]) -> StateTransitionLedgerEntry:
        predecessor = self._head_digest()
        sequence = len(self.ledger)
        incident = self.incident_id or "INC-UNASSIGNED"
        timestamp = _utc_now()
        payload_json = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        payload_digest = _sha256(payload_json)
        digest = _chain_digest(
            predecessor, sequence, incident, self.current_state, to_state,
            self.pivot_cycle_count, timestamp, event, actor_role, payload_digest,
        )
        entry = StateTransitionLedgerEntry(
            sequence=sequence,
            incident_id=incident,
            from_state=self.current_state,
            to_state=to_state,
            pivot_cycle=self.pivot_cycle_count,
            timestamp=timestamp,
            event=event,
            actor_role=actor_role,
            payload_json=payload_json,
            payload_digest=payload_digest,
            predecessor_hash=predecessor,
            state_digest=digest,
        )
        self.ledger.append(entry)
        self._stream_remote(entry)
        return entry

    def _stream_remote(self, entry: StateTransitionLedgerEntry) -> None:
        if self.remote_ledger is None:
            return
        document = {
            "sequence": entry.sequence,
            "incident_id": entry.incident_id,
            "from_state": entry.from_state.value,
            "to_state": entry.to_state.value,
            "pivot_cycle": entry.pivot_cycle,
            "timestamp": entry.timestamp,
            "event": entry.event,
            "actor_role": entry.actor_role,
            "payload_digest": entry.payload_digest,
            "predecessor_hash": entry.predecessor_hash,
            "state_digest": entry.state_digest,
        }
        for method_name in ("append_entry", "append"):
            method = getattr(self.remote_ledger, method_name, None)
            if callable(method):
                method(document)
                return
        raise EightDStateMachineError("remote_ledger exposes neither append_entry() nor append()")

    def _transition(self, expected: EightDState, to_state: EightDState, event: str,
                    actor_role: str, payload: Dict[str, Any]) -> None:
        self._require_state(expected)
        self._append_ledger(to_state, event, actor_role, payload)
        self.current_state = to_state

    def _require_state(self, expected: EightDState) -> None:
        if self.current_state is EightDState.HARD_ABORT:
            raise HardAbortTriggered(
                f"{HARD_ABORT_TRIPWIRE_CANONICAL} machine is sealed; no further transitions are permitted"
            )
        if self.current_state is not expected:
            raise EightDStateMachineError(
                f"illegal transition: expected state {expected.value}, machine is in {self.current_state.value}"
            )

    # ---------------------------------------------------------------------- D0
    def trigger_d0_emergency_containment(
        self,
        fault: Union[CategoricalFaultRecord, BaseException],
        target_module: str,
        target_work_package: str = "WP-2.0",
        incident_id: Optional[str] = None,
    ) -> str:
        if self.current_state is EightDState.HARD_ABORT:
            raise HardAbortTriggered(
                f"{HARD_ABORT_TRIPWIRE_CANONICAL} machine already aborted; autopsy at {self.autopsy_report_path}"
            )
        if not isinstance(target_module, str) or not target_module.strip():
            raise EightDStateMachineError("target_module must be a non-empty string")
        if isinstance(fault, CategoricalFaultRecord):
            record = fault
        elif isinstance(fault, BaseException):
            record = self.registry.record_from_exception(fault, target_module, predecessor_hash=self._head_digest())
        else:
            raise EightDStateMachineError(f"unsupported fault object: {type(fault).__name__}")

        if self.current_state is EightDState.D8_RATIFICATION_AND_RELEASE:
            self.pivot_cycle_count = 0  # previous incident ratified; a fresh pivot budget applies
        attempted_cycle = self.pivot_cycle_count + 1
        self.fault_record = record
        self.target_module = target_module
        self.target_work_package = target_work_package
        self.incident_id = incident_id or f"INC-{record.state_digest[:12]}-C{attempted_cycle}"
        self.quarantine_gate_active = True

        if attempted_cycle > self.max_pivot_cycles:
            tripwire = self.trigger_hard_abort(
                reason=(
                    f"PIVOT BUDGET EXHAUSTED: cycle {attempted_cycle} exceeds "
                    f"MAX_PIVOT_CYCLES={self.max_pivot_cycles}"
                )
            )
            raise HardAbortTriggered(
                f"{tripwire} pivot budget of {self.max_pivot_cycles} exhausted for {self.incident_id}; "
                f"autopsy dispatched to {AUTOPSY_ROLE} at {self.autopsy_report_path}"
            )

        self.pivot_cycle_count = attempted_cycle
        self._reset_incident_artifacts()
        self.temperature_pair = calculate_decoding_temperature(
            record.category, record.severity, pivot_cycle=attempted_cycle
        )
        self.decoding_temperature, self.top_p = self.temperature_pair
        self.directives = generate_directives_for_fault(record.category, record)
        self._append_ledger(
            EightDState.D0_EMERGENCY_CONTAINMENT,
            "D0_EMERGENCY_CONTAINMENT",
            DEFAULT_ACCOUNTABLE_ROLE,
            {
                "fault_record_id": record.record_id,
                "fault_digest": record.state_digest,
                "category": record.category.value,
                "severity": record.severity.value,
                "remediation_strategy": record.remediation_strategy.value,
                "target_module": target_module,
                "work_package": target_work_package,
                "pivot_cycle": attempted_cycle,
                "pivots_remaining": self.pivots_remaining,
                "decoding_temperature": self.decoding_temperature,
                "top_p": self.top_p,
                "quarantine_gate": "ENGAGED",
            },
        )
        self.current_state = EightDState.D0_EMERGENCY_CONTAINMENT
        return self.incident_id

    def trigger_hard_abort(self, reason: str = "PIVOT BUDGET EXHAUSTED", tripwire: Optional[str] = None) -> str:
        label = tripwire or HARD_ABORT_TRIPWIRE_CANONICAL
        if HARD_ABORT_TRIPWIRE_CANONICAL not in label:
            label = f"{HARD_ABORT_TRIPWIRE_CANONICAL} {label}"
        record = self.fault_record
        incident = self.incident_id or "INC-UNASSIGNED"
        autopsy = {
            "tripwire": label,
            "reason": reason,
            "dispatched_to": AUTOPSY_ROLE,
            "report_type": "physics_autopsy",
            "incident_id": incident,
            "work_package": self.target_work_package,
            "target_module": self.target_module,
            "pivot_cycles_consumed": self.pivot_cycle_count,
            "max_pivot_cycles": self.max_pivot_cycles,
            "state_at_abort": self.current_state.value,
            "fault": None if record is None else {
                "record_id": record.record_id,
                "category": record.category.value,
                "severity": record.severity.value,
                "remediation_strategy": record.remediation_strategy.value,
                "error_type": record.error_type,
                "message": record.message,
                "state_digest": record.state_digest,
            },
            "ledger_trail": [
                {"sequence": e.sequence, "to_state": e.to_state.value, "event": e.event,
                 "pivot_cycle": e.pivot_cycle, "state_digest": e.state_digest}
                for e in self.ledger
            ],
            "ledger_head_digest": self._head_digest(),
            "generated_at": _utc_now(),
        }
        path = self.workspace_root / f"hard_abort_autopsy_{_safe_name(incident)}.json"
        self.autopsy_report_path = _atomic_write_json(path, autopsy)
        self._append_ledger(
            EightDState.HARD_ABORT,
            "HARD_ABORT_TRIPWIRE",
            AUTOPSY_ROLE,
            {"tripwire": label, "reason": reason, "dispatched_to": AUTOPSY_ROLE,
             "autopsy_report": str(self.autopsy_report_path)},
        )
        self.current_state = EightDState.HARD_ABORT
        self.quarantine_gate_active = True
        return label

    # ---------------------------------------------------------------------- D1
    def _default_presidium(self) -> List[PresidiumMember]:
        responsible = self._responsible_role()
        roster: List[PresidiumMember] = []
        for seat, role in enumerate(CANONICAL_PRESIDIUM_ROLES):
            if role == DEFAULT_ACCOUNTABLE_ROLE:
                raci, mandate = RACIAuthority.ACCOUNTABLE, "Single accountable authority for incident closure"
            elif role == responsible:
                raci, mandate = RACIAuthority.RESPONSIBLE, "Executes the permanent corrective action"
            elif role in ("cochem-audit", "cochem-tester", "adversary"):
                raci, mandate = RACIAuthority.CONSULTED, "Independent verification and challenge"
            else:
                raci, mandate = RACIAuthority.INFORMED, "Kept informed through the ledger"
            roster.append(PresidiumMember(role_identifier=role, raci=raci, seat_index=seat, mandate=mandate))
        return roster

    def _validate_presidium(self, roster: Sequence[PresidiumMember]) -> List[PresidiumMember]:
        members = list(roster)
        if not all(isinstance(m, PresidiumMember) for m in members):
            raise InvariantBreachError("presidium roster must consist of PresidiumMember objects")
        roles = sorted(m.role_identifier for m in members)
        if roles != sorted(CANONICAL_PRESIDIUM_ROLES):
            raise InvariantBreachError(f"presidium must seat exactly the 9 canonical roles, got {roles}")
        normalized = [
            m if isinstance(m.raci, RACIAuthority) else PresidiumMember(
                role_identifier=m.role_identifier, raci=RACIAuthority(str(m.raci).strip().upper()),
                seat_index=m.seat_index, mandate=m.mandate)
            for m in members
        ]
        accountable = [m for m in normalized if m.raci is RACIAuthority.ACCOUNTABLE]
        if len(accountable) != 1:
            raise InvariantBreachError(f"RACI single accountability violated: A={len(accountable)} (required A=1)")
        holder = accountable[0].role_identifier
        if holder == FORBIDDEN_ACCOUNTABLE_ROLE:
            raise AsymmetricVerificationError(
                f"{FORBIDDEN_ACCOUNTABLE_ROLE} cannot hold Accountable authority over its own work"
            )
        if holder == self._responsible_role():
            raise AsymmetricVerificationError(
                f"{holder} executes the corrective action and cannot also be Accountable for verifying it"
            )
        return normalized

    def advance_to_d1_team_formation(
        self, presidium_override: Optional[List[PresidiumMember]] = None
    ) -> List[PresidiumMember]:
        self._require_state(EightDState.D0_EMERGENCY_CONTAINMENT)
        roster = self._validate_presidium(
            presidium_override if presidium_override is not None else self._default_presidium()
        )
        accountable = next(m.role_identifier for m in roster if m.raci is RACIAuthority.ACCOUNTABLE)
        self._transition(
            EightDState.D0_EMERGENCY_CONTAINMENT, EightDState.D1_TEAM_FORMATION,
            "D1_TEAM_FORMATION", DEFAULT_ACCOUNTABLE_ROLE,
            {"roster": {m.role_identifier: m.raci.value for m in roster}, "accountable": accountable},
        )
        self.presidium = roster
        return list(roster)

    # ---------------------------------------------------------------------- D2
    def advance_to_d2_problem_description(
        self, custom_description: Optional[ProblemDescription5W2H] = None
    ) -> ProblemDescription5W2H:
        self._require_state(EightDState.D1_TEAM_FORMATION)
        record = self._record()
        if custom_description is not None:
            if not isinstance(custom_description, ProblemDescription5W2H):
                raise EightDStateMachineError("custom_description must be a ProblemDescription5W2H")
            description = custom_description
        else:
            spec = self.registry.get_spec(record.category)
            description = ProblemDescription5W2H(
                what=f"{record.category.value}: {record.error_type} - {record.message}",
                where=f"{self.target_module} ({self.target_work_package})",
                when=f"{record.timestamp} (pivot cycle {self.pivot_cycle_count})",
                who=f"responsible={spec.responsible_role}; accountable={DEFAULT_ACCOUNTABLE_ROLE}",
                why=spec.description,
                how=f"Detected as {record.error_type}; routed to {record.remediation_strategy.value}",
                how_many=(
                    f"pivot cycle {self.pivot_cycle_count} of {self.max_pivot_cycles}; "
                    f"{self.pivots_remaining} remaining"
                ),
            )
        empty = [k for k, v in vars(description).items() if not str(v).strip()]
        if empty:
            raise EightDStateMachineError(f"5W2H fields must be non-empty: {empty}")
        self._transition(
            EightDState.D1_TEAM_FORMATION, EightDState.D2_PROBLEM_DESCRIPTION,
            "D2_PROBLEM_DESCRIPTION", "cochem-scribe", dict(vars(description)),
        )
        self.problem_description = description
        return description

    # ---------------------------------------------------------------------- D3
    def _freeze_branch(self) -> InterimContainmentAction:
        head = _git_head(self.workspace_root)
        record = self._record()
        path = self.workspace_root / BRANCH_FREEZE_SENTINEL_NAME
        _atomic_write_json(path, {
            "incident_id": self.incident_id,
            "frozen_at": _utc_now(),
            "frozen_head": head,
            "category": record.category.value,
            "strategy": record.remediation_strategy.value,
            "holder_pid": os.getpid(),
        })
        self.freeze_sentinel_path = path
        return InterimContainmentAction(
            action_id=f"ICA-FREEZE-{len(self.containment_actions) + 1}",
            action_type="BRANCH_FREEZE",
            description="Atomic branch freeze sentinel written; all edits are blocked until D8 release",
            target=str(path),
            evidence=f"frozen_head={head or 'no-git-head'}",
        )

    def _reclaim_locks(self) -> List[InterimContainmentAction]:
        actions: List[InterimContainmentAction] = []
        lock_files = sorted(p for p in self.workspace_root.glob("*.lock") if p.is_file())
        if not lock_files:
            actions.append(InterimContainmentAction(
                action_id="ICA-LOCKSCAN-0", action_type="LOCK_SCAN",
                description="Lock scan found no lock files", target=str(self.workspace_root),
                evidence="0 locks",
            ))
            return actions
        for index, path in enumerate(lock_files, start=1):
            try:
                stat_before = path.stat()
                pid, role, raw = _read_lock_holder(path)
            except FileNotFoundError:
                continue
            age = time.time() - stat_before.st_mtime
            if pid is not None:
                alive = pid_is_alive(pid)
                reclaim = not alive
                reason = f"holder pid {pid} ({role or 'unknown role'}) {'alive' if alive else 'exited'}"
            else:
                reclaim = age > STALE_LOCK_AGE_S
                reason = f"no holder pid; age {age:.0f}s vs stale threshold {STALE_LOCK_AGE_S:.0f}s"
            if reclaim:
                try:
                    stat_after = path.stat()
                    unchanged = (
                        stat_after.st_mtime == stat_before.st_mtime
                        and path.read_text(encoding="utf-8", errors="replace") == raw
                    )
                    if unchanged:
                        os.remove(path)
                        action_type, outcome = "LOCK_RECLAIMED", "orphaned lock removed"
                    else:
                        action_type, outcome = "LOCK_PRESERVED", "lock changed during inspection; left intact"
                except FileNotFoundError:
                    action_type, outcome = "LOCK_RELEASED_BY_HOLDER", "lock vanished before reclamation"
            else:
                immune = role in COUNCIL_IMMUNE_ROLES
                action_type = "LOCK_PRESERVED_COUNCIL_IMMUNITY" if immune else "LOCK_PRESERVED_LIVE_HOLDER"
                outcome = "live holder untouched (Invariant 9); no process was signalled"
            actions.append(InterimContainmentAction(
                action_id=f"ICA-LOCK-{index}", action_type=action_type,
                description=outcome, target=str(path), evidence=reason,
            ))
        return actions

    def advance_to_d3_interim_containment(
        self,
        additional_actions: Optional[Sequence[InterimContainmentAction]] = None,
        workspace_freeze: bool = True,
        reclaim_locks: bool = True,
    ) -> List[InterimContainmentAction]:
        self._require_state(EightDState.D2_PROBLEM_DESCRIPTION)
        actions: List[InterimContainmentAction] = [
            InterimContainmentAction(
                action_id="ICA-QUARANTINE-1", action_type="QUARANTINE_GATE",
                description="Fail-closed quarantine gate engaged on the faulty module",
                target=str(self.target_module), evidence=f"incident={self.incident_id}",
            ),
            InterimContainmentAction(
                action_id="ICA-PIVOT-1", action_type="PIVOT_BUDGET",
                description="Pivot budget decremented for this containment cycle",
                target=str(self.target_work_package),
                evidence=f"cycle={self.pivot_cycle_count}; remaining={self.pivots_remaining}",
            ),
        ]
        self.containment_actions = actions
        if workspace_freeze:
            actions.append(self._freeze_branch())
        if reclaim_locks:
            actions.extend(self._reclaim_locks())
        for extra in additional_actions or ():
            if not isinstance(extra, InterimContainmentAction):
                raise EightDStateMachineError("additional_actions must contain InterimContainmentAction objects")
            actions.append(extra)
        self._transition(
            EightDState.D2_PROBLEM_DESCRIPTION, EightDState.D3_INTERIM_CONTAINMENT,
            "D3_INTERIM_CONTAINMENT", "cochem-sdp-manager",
            {"actions": [vars(a) for a in actions]},
        )
        return list(actions)

    # ---------------------------------------------------------------------- D4
    def advance_to_d4_root_cause_analysis(self, five_whys: FiveWhysAnalysis) -> FiveWhysAnalysis:
        self._require_state(EightDState.D3_INTERIM_CONTAINMENT)
        if not isinstance(five_whys, FiveWhysAnalysis):
            raise EightDStateMachineError("D4 requires a FiveWhysAnalysis")
        chain = five_whys.normalized_chain()
        if len(chain) < 5:
            raise EightDStateMachineError(f"5-Whys analysis requires at least 5 levels, got {len(chain)}")
        if not str(five_whys.root_cause_summary).strip():
            raise EightDStateMachineError("root_cause_summary must be non-empty")
        fishbone = FishboneCategory(five_whys.fishbone_category)
        record = self._record()
        if five_whys.identified_fault_category is not None:
            identified = coerce_category(five_whys.identified_fault_category)
            if identified is not record.category:
                raise EightDStateMachineError(
                    f"root cause names {identified.value} but the incident is {record.category.value}"
                )
        self._transition(
            EightDState.D3_INTERIM_CONTAINMENT, EightDState.D4_ROOT_CAUSE_ANALYSIS,
            "D4_ROOT_CAUSE_ANALYSIS", AUTOPSY_ROLE,
            {"chain": chain, "fishbone": fishbone.value, "root_cause": five_whys.root_cause_summary,
             "detection_gap": five_whys.detection_gap_explanation},
        )
        self.root_cause_analysis = five_whys
        return five_whys

    # ---------------------------------------------------------------------- D5
    def advance_to_d5_permanent_corrective_actions(
        self, pcas: Sequence[PermanentCorrectiveAction]
    ) -> List[PermanentCorrectiveAction]:
        self._require_state(EightDState.D4_ROOT_CAUSE_ANALYSIS)
        actions = list(pcas)
        if not actions:
            raise EightDStateMachineError("D5 requires at least one permanent corrective action")
        spec = self.registry.get_spec(self._record().category)
        for pca in actions:
            if not isinstance(pca, PermanentCorrectiveAction):
                raise EightDStateMachineError("D5 accepts PermanentCorrectiveAction objects only")
            strategy = coerce_strategy(pca.remediation_strategy)
            if not spec.accepts(strategy):
                raise EightDStateMachineError(
                    f"{strategy.value} is not a canonical remedy for {spec.category.value}"
                )
            if pca.target_recovery_role not in CANONICAL_PRESIDIUM_ROLES:
                raise EightDStateMachineError(f"unknown recovery role {pca.target_recovery_role!r}")
            if not str(pca.description).strip():
                raise EightDStateMachineError("PCA description must be non-empty")
        self._transition(
            EightDState.D4_ROOT_CAUSE_ANALYSIS, EightDState.D5_PERMANENT_CORRECTIVE_ACTIONS,
            "D5_PERMANENT_CORRECTIVE_ACTIONS", spec.responsible_role,
            {"pcas": [{"pca_id": p.pca_id, "strategy": coerce_strategy(p.remediation_strategy).value,
                       "role": p.target_recovery_role, "criteria": p.verification_criteria}
                      for p in actions]},
        )
        self.corrective_actions = actions
        return list(actions)

    # ---------------------------------------------------------------------- D6
    def _alert_adversary(self) -> Path:
        record = self._record()
        path = self.workspace_root / f"adversary_alert_{_safe_name(self.incident_id or 'incident')}.json"
        return _atomic_write_json(path, {
            "role": "adversary",
            "alerted_role": "adversary",
            "incident_id": self.incident_id,
            "category": record.category.value,
            "strategy": record.remediation_strategy.value,
            "evidence": record.message,
            "rollback_verified": True,
            "frozen_head": _git_head(self.workspace_root),
            "issued_at": _utc_now(),
        })

    def advance_to_d6_corrective_execution(
        self, physical_verifier: Optional[Callable[[], bool]] = None
    ) -> bool:
        self._require_state(EightDState.D5_PERMANENT_CORRECTIVE_ACTIONS)
        if physical_verifier is None or not callable(physical_verifier):
            raise EightDStateMachineError("D6 requires a callable physical verifier; execution cannot be assumed")
        self.last_verification_error = None
        try:
            outcome = physical_verifier()
        except Exception as exc:  # recorded in the ledger, reported as a failed execution
            outcome = False
            self.last_verification_error = f"{type(exc).__name__}: {exc}"
        verified = outcome is True
        responsible = self._responsible_role()
        if not verified:
            self._append_ledger(
                self.current_state, "D6_VERIFICATION_FAILED", "cochem-tester",
                {"verified": False, "returned": repr(outcome), "error": self.last_verification_error},
            )
            return False
        strategies = {coerce_strategy(p.remediation_strategy) for p in self.corrective_actions}
        if RemediationStrategy.BRANCH_LOCK_AND_ROLLBACK in strategies:
            self.adversary_alert_path = self._alert_adversary()
        self._transition(
            EightDState.D5_PERMANENT_CORRECTIVE_ACTIONS, EightDState.D6_CORRECTIVE_EXECUTION,
            "D6_CORRECTIVE_EXECUTION", responsible,
            {"verified": True, "verified_by": "cochem-tester",
             "adversary_alert": str(self.adversary_alert_path) if self.adversary_alert_path else None},
        )
        self.execution_verified = True
        return True

    # ---------------------------------------------------------------------- D7
    def advance_to_d7_preventive_actions(self, preventive_measures: Sequence[str]) -> List[str]:
        self._require_state(EightDState.D6_CORRECTIVE_EXECUTION)
        measures = list(preventive_measures)
        if not measures or not all(isinstance(m, str) and m.strip() for m in measures):
            raise EightDStateMachineError("D7 requires at least one non-empty preventive measure")
        self._transition(
            EightDState.D6_CORRECTIVE_EXECUTION, EightDState.D7_PREVENTIVE_ACTIONS,
            "D7_PREVENTIVE_ACTIONS", "cochem-improve", {"measures": measures},
        )
        self.preventive_measures = measures
        return list(measures)

    # ---------------------------------------------------------------------- D8
    def advance_to_d8_ratification_and_release(self, presidium_votes: Dict[str, str]) -> Dict[str, Any]:
        self._require_state(EightDState.D7_PREVENTIVE_ACTIONS)
        if not isinstance(presidium_votes, dict):
            raise EightDStateMachineError("presidium_votes must be a role -> vote mapping")
        votes = {str(k).strip(): str(v).strip().upper() for k, v in presidium_votes.items()}
        missing = sorted(set(CANONICAL_PRESIDIUM_ROLES) - set(votes))
        unknown = sorted(set(votes) - set(CANONICAL_PRESIDIUM_ROLES))
        if missing or unknown:
            raise InvariantBreachError(f"roll-call incomplete: missing={missing}, unknown={unknown}")
        ayes = sum(1 for v in votes.values() if v == "AYE")
        nays = sum(1 for v in votes.values() if v == "NAY")
        abstains = len(votes) - ayes - nays
        tally = f"{ayes}-{nays}-{abstains}"
        if ayes != len(CANONICAL_PRESIDIUM_ROLES):
            raise InvariantBreachError(f"ratification requires unanimous 9-0-0 AYE, got {tally}")
        accountable = next(m.role_identifier for m in self.presidium if m.raci is RACIAuthority.ACCOUNTABLE)
        if accountable == FORBIDDEN_ACCOUNTABLE_ROLE:
            raise AsymmetricVerificationError("cochem-coder cannot ratify its own work")

        sentinel_released = False
        if self.freeze_sentinel_path is not None and self.freeze_sentinel_path.exists():
            os.remove(self.freeze_sentinel_path)
            sentinel_released = True
        record = self._record()
        self._transition(
            EightDState.D7_PREVENTIVE_ACTIONS, EightDState.D8_RATIFICATION_AND_RELEASE,
            "D8_RATIFICATION_AND_RELEASE", accountable,
            {"votes": votes, "tally": tally, "sentinel_released": sentinel_released},
        )
        self.quarantine_gate_active = False
        self.freeze_sentinel_path = None
        analysis = self.root_cause_analysis
        self.resolution_dossier = {
            "incident_id": self.incident_id,
            "work_package": self.target_work_package,
            "target_module": self.target_module,
            "fault_category": record.category.value,
            "remediation_strategy": record.remediation_strategy.value,
            "responsible_role": self._responsible_role(),
            "accountable_role": accountable,
            "roll_call": votes,
            "tally": tally,
            "unanimous": True,
            "pivot_cycle": self.pivot_cycle_count,
            "decoding_temperature": self.decoding_temperature,
            "root_cause": analysis.root_cause_summary if analysis is not None else None,
            "corrective_actions": [p.pca_id for p in self.corrective_actions],
            "preventive_measures": list(self.preventive_measures),
            "quarantine_released": True,
            "freeze_sentinel_released": sentinel_released,
            "genesis_digest": self.genesis_digest,
            "final_state_digest": self._head_digest(),
            "ledger_length": len(self.ledger),
        }
        return dict(self.resolution_dossier)

    # ------------------------------------------------------------------ ledger
    def verify_state_chain_integrity(self) -> bool:
        expected = self.genesis_digest
        for index, entry in enumerate(self.ledger):
            if not isinstance(entry, StateTransitionLedgerEntry):
                return False
            if entry.sequence != index or entry.predecessor_hash != expected:
                return False
            if not entry.payload_intact() or entry.recompute_digest() != entry.state_digest:
                return False
            expected = entry.state_digest
        return True


SelfHealingFSM = EightDRCAStateMachine

__all__ = [
    "AUTOPSY_ROLE",
    "BRANCH_FREEZE_SENTINEL_NAME",
    "CANONICAL_PRESIDIUM_ROLES",
    "COUNCIL_IMMUNE_ROLES",
    "HARD_ABORT_TRIPWIRE_CANONICAL",
    "MAX_PIVOT_CYCLES",
    "AsymmetricVerificationError",
    "EightDRCAStateMachine",
    "EightDState",
    "EightDStateMachineError",
    "FishboneCategory",
    "FiveWhysAnalysis",
    "HardAbortTriggered",
    "InterimContainmentAction",
    "InvariantBreachError",
    "PermanentCorrectiveAction",
    "PresidiumMember",
    "ProblemDescription5W2H",
    "RACIAuthority",
    "SelfHealingFSM",
    "StateTransitionLedgerEntry",
    "pid_is_alive",
]
