"""Canonical MECE fault ontology for the cochem_ml self-healing loop (Task 2.16, WP-2.0).

Five mutually exclusive, collectively exhaustive fault categories are defined,
each bound to a canonical remediation strategy, a responsible recovery role
and a five-axis feature vector. Every categorical fault record carries a
SHA-256 state digest chained to a predecessor digest, rooted at
``GENESIS_HASH``.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple, Union

GENESIS_HASH = "0" * 64
_HEX_DIGITS = frozenset("0123456789abcdef")


class FaultCategory(str, Enum):
    """The five canonical, mutually exclusive fault categories."""

    SYNTAX_LINT_ERROR = "SYNTAX_LINT_ERROR"
    PHYSICAL_PARITY_DIVERGENCE = "PHYSICAL_PARITY_DIVERGENCE"
    SPOOFING_VIOLATION = "SPOOFING_VIOLATION"
    ENVIRONMENT_DEADLOCK = "ENVIRONMENT_DEADLOCK"
    TIMEOUT_OVERFLOW = "TIMEOUT_OVERFLOW"


class FaultSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RemediationStrategy(str, Enum):
    """Canonical remediation strategies.

    ``TIMEOUT_BACKOFF_AND_TRUNCATION`` (log tail-truncation plus workload
    back-off) and ``TIMEOUT_BACKOFF_PARTITION`` (N>1 -> N=1 decomposition)
    are both accepted remedies for ``TIMEOUT_OVERFLOW``.
    """

    AST_ISOLATED_REPAIR = "AST_ISOLATED_REPAIR"
    NUMERICAL_PRECONDITIONING_PIVOT = "NUMERICAL_PRECONDITIONING_PIVOT"
    BRANCH_LOCK_AND_ROLLBACK = "BRANCH_LOCK_AND_ROLLBACK"
    SAFE_LOCK_RECLAMATION = "SAFE_LOCK_RECLAMATION"
    TIMEOUT_BACKOFF_PARTITION = "TIMEOUT_BACKOFF_PARTITION"
    TIMEOUT_BACKOFF_AND_TRUNCATION = "TIMEOUT_BACKOFF_AND_TRUNCATION"


class SpoofingViolationError(RuntimeError):
    """Raised when a prohibited bypass token is found in audited source."""


class ResidualConvergenceError(ArithmeticError):
    """Raised when a physical residual fails to converge below tolerance."""


class EnvironmentDeadlockError(RuntimeError):
    """Raised when an exclusive OS resource cannot be acquired."""


def is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX_DIGITS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def coerce_category(value: Union[FaultCategory, str]) -> FaultCategory:
    if isinstance(value, FaultCategory):
        return value
    if isinstance(value, str):
        text = value.strip()
        for member in FaultCategory:
            if text == member.value or text.upper() == member.name:
                return member
    raise ValueError(f"unknown fault category: {value!r}")


def coerce_severity(value: Union[FaultSeverity, str]) -> FaultSeverity:
    if isinstance(value, FaultSeverity):
        return value
    if isinstance(value, str):
        text = value.strip().upper()
        for member in FaultSeverity:
            if text == member.value:
                return member
    raise ValueError(f"unknown fault severity: {value!r}")


def coerce_strategy(value: Union[RemediationStrategy, str]) -> RemediationStrategy:
    if isinstance(value, RemediationStrategy):
        return value
    if isinstance(value, str):
        text = value.strip().upper()
        for member in RemediationStrategy:
            if text == member.value:
                return member
    raise ValueError(f"unknown remediation strategy: {value!r}")


@dataclass(frozen=True)
class FaultCategorySpec:
    category: FaultCategory
    primary_strategy: RemediationStrategy
    accepted_strategies: Tuple[RemediationStrategy, ...]
    responsible_role: str
    default_severity: FaultSeverity
    description: str
    vector: Tuple[float, float, float, float, float]

    def accepts(self, strategy: Union[RemediationStrategy, str]) -> bool:
        return coerce_strategy(strategy) in self.accepted_strategies


# Vector axes: (code_locality, numerical_sensitivity, integrity_risk,
#               environment_coupling, time_pressure)
_CANONICAL_SPECS: Dict[FaultCategory, FaultCategorySpec] = {
    FaultCategory.SYNTAX_LINT_ERROR: FaultCategorySpec(
        category=FaultCategory.SYNTAX_LINT_ERROR,
        primary_strategy=RemediationStrategy.AST_ISOLATED_REPAIR,
        accepted_strategies=(RemediationStrategy.AST_ISOLATED_REPAIR,),
        responsible_role="cochem-coder",
        default_severity=FaultSeverity.MEDIUM,
        description="Source fails AST compilation or static lint; repair is confined to the faulty node.",
        vector=(1.0, 0.0, 0.2, 0.0, 0.0),
    ),
    FaultCategory.PHYSICAL_PARITY_DIVERGENCE: FaultCategorySpec(
        category=FaultCategory.PHYSICAL_PARITY_DIVERGENCE,
        primary_strategy=RemediationStrategy.NUMERICAL_PRECONDITIONING_PIVOT,
        accepted_strategies=(RemediationStrategy.NUMERICAL_PRECONDITIONING_PIVOT,),
        responsible_role="cochem-coder",
        default_severity=FaultSeverity.HIGH,
        description="Physical residual exceeds tolerance; grids and tolerances must be preconditioned.",
        vector=(0.2, 1.0, 0.0, 0.0, 0.3),
    ),
    FaultCategory.SPOOFING_VIOLATION: FaultCategorySpec(
        category=FaultCategory.SPOOFING_VIOLATION,
        primary_strategy=RemediationStrategy.BRANCH_LOCK_AND_ROLLBACK,
        accepted_strategies=(RemediationStrategy.BRANCH_LOCK_AND_ROLLBACK,),
        responsible_role="adversary",
        default_severity=FaultSeverity.CRITICAL,
        description="A prohibited bypass token was found; the branch is frozen and rolled back.",
        vector=(0.4, 0.0, 1.0, 0.0, 0.0),
    ),
    FaultCategory.ENVIRONMENT_DEADLOCK: FaultCategorySpec(
        category=FaultCategory.ENVIRONMENT_DEADLOCK,
        primary_strategy=RemediationStrategy.SAFE_LOCK_RECLAMATION,
        accepted_strategies=(RemediationStrategy.SAFE_LOCK_RECLAMATION,),
        responsible_role="cochem-debug",
        default_severity=FaultSeverity.HIGH,
        description="An orphaned OS lock blocks exclusive access; reclaim it without harming live holders.",
        vector=(0.0, 0.0, 0.0, 1.0, 0.4),
    ),
    FaultCategory.TIMEOUT_OVERFLOW: FaultCategorySpec(
        category=FaultCategory.TIMEOUT_OVERFLOW,
        primary_strategy=RemediationStrategy.TIMEOUT_BACKOFF_AND_TRUNCATION,
        accepted_strategies=(
            RemediationStrategy.TIMEOUT_BACKOFF_AND_TRUNCATION,
            RemediationStrategy.TIMEOUT_BACKOFF_PARTITION,
        ),
        responsible_role="cochem-sdp-manager",
        default_severity=FaultSeverity.MEDIUM,
        description="Execution exceeded its wall-clock budget; truncate logs and partition the workload.",
        vector=(0.0, 0.3, 0.0, 0.4, 1.0),
    ),
}


def compute_fault_state_digest(
    category: Union[FaultCategory, str],
    source_module: str,
    error_type: str,
    message: str,
    severity: Union[FaultSeverity, str],
    strategy: Union[RemediationStrategy, str],
    timestamp: str,
    predecessor_hash: str = GENESIS_HASH,
) -> str:
    """SHA-256 over the record attributes, chained to ``predecessor_hash``."""
    if not is_sha256_hex(predecessor_hash):
        raise ValueError(f"predecessor_hash must be a 64-char lowercase hex digest: {predecessor_hash!r}")
    material = "|".join(
        (
            predecessor_hash,
            coerce_category(category).value,
            str(source_module),
            str(error_type),
            str(message),
            coerce_severity(severity).value,
            coerce_strategy(strategy).value,
            str(timestamp),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CategoricalFaultRecord:
    record_id: str
    category: FaultCategory
    source_module: str
    error_type: str
    message: str
    severity: FaultSeverity
    remediation_strategy: RemediationStrategy
    timestamp: str
    predecessor_hash: str
    state_digest: str

    def recompute_digest(self) -> str:
        return compute_fault_state_digest(
            self.category,
            self.source_module,
            self.error_type,
            self.message,
            self.severity,
            self.remediation_strategy,
            self.timestamp,
            self.predecessor_hash,
        )

    def is_intact(self) -> bool:
        return self.recompute_digest() == self.state_digest


_KEYWORDS: Tuple[Tuple[FaultCategory, Tuple[str, ...]], ...] = (
    (FaultCategory.SPOOFING_VIOLATION, ("spoof", "prohibited token", "bypass token", "banned token")),
    (FaultCategory.TIMEOUT_OVERFLOW, ("timed out", "timeout", "wall-clock", "deadline exceeded")),
    (FaultCategory.ENVIRONMENT_DEADLOCK, ("deadlock", "stale lock", "lock contention", ".lock")),
    (FaultCategory.PHYSICAL_PARITY_DIVERGENCE, ("residual", "converge", "divergen", "parity", "scf")),
)


class FaultOntologyRegistry:
    """Registry of the canonical fault ontology and record factory."""

    def __init__(self) -> None:
        self._specs: Dict[FaultCategory, FaultCategorySpec] = dict(_CANONICAL_SPECS)

    def specs(self) -> Tuple[FaultCategorySpec, ...]:
        return tuple(self._specs[c] for c in FaultCategory)

    def get_spec(self, category: Union[FaultCategory, str]) -> FaultCategorySpec:
        return self._specs[coerce_category(category)]

    def vectorize(self, category: Union[FaultCategory, str]) -> Tuple[float, float, float, float, float]:
        vector = self.get_spec(category).vector
        return tuple(float(x) for x in vector)  # type: ignore[return-value]

    def create_record(
        self,
        category: Union[FaultCategory, str],
        source_module: str,
        error_type: str,
        message: str,
        severity: Optional[Union[FaultSeverity, str]] = None,
        strategy: Optional[Union[RemediationStrategy, str]] = None,
        predecessor_hash: str = GENESIS_HASH,
        timestamp: Optional[str] = None,
    ) -> CategoricalFaultRecord:
        spec = self.get_spec(category)
        resolved_severity = coerce_severity(severity) if severity is not None else spec.default_severity
        resolved_strategy = coerce_strategy(strategy) if strategy is not None else spec.primary_strategy
        if not spec.accepts(resolved_strategy):
            raise ValueError(
                f"strategy {resolved_strategy.value} is not an accepted remedy for {spec.category.value}"
            )
        if not str(source_module).strip():
            raise ValueError("source_module must be a non-empty string")
        stamp = timestamp or _utc_now()
        digest = compute_fault_state_digest(
            spec.category, source_module, error_type, message,
            resolved_severity, resolved_strategy, stamp, predecessor_hash,
        )
        return CategoricalFaultRecord(
            record_id=f"FLT-{digest[:16]}",
            category=spec.category,
            source_module=str(source_module),
            error_type=str(error_type),
            message=str(message),
            severity=resolved_severity,
            remediation_strategy=resolved_strategy,
            timestamp=stamp,
            predecessor_hash=predecessor_hash,
            state_digest=digest,
        )

    @staticmethod
    def classify_exception(exc: BaseException) -> FaultCategory:
        """Deterministically map a real exception onto exactly one category."""
        if isinstance(exc, (subprocess.TimeoutExpired, TimeoutError)):
            return FaultCategory.TIMEOUT_OVERFLOW
        if isinstance(exc, SyntaxError):
            return FaultCategory.SYNTAX_LINT_ERROR
        if isinstance(exc, SpoofingViolationError):
            return FaultCategory.SPOOFING_VIOLATION
        if isinstance(exc, (EnvironmentDeadlockError, FileExistsError, BlockingIOError)):
            return FaultCategory.ENVIRONMENT_DEADLOCK
        if isinstance(exc, (ResidualConvergenceError, ArithmeticError)):
            return FaultCategory.PHYSICAL_PARITY_DIVERGENCE
        text = f"{type(exc).__name__} {exc}".lower()
        for category, needles in _KEYWORDS:
            if any(needle in text for needle in needles):
                return category
        # Any remaining exception raised by code (NameError, ImportError,
        # TypeError, AttributeError, ...) is a source-level defect.
        return FaultCategory.SYNTAX_LINT_ERROR

    def record_from_exception(
        self,
        exc: BaseException,
        source_module: str,
        predecessor_hash: str = GENESIS_HASH,
    ) -> CategoricalFaultRecord:
        category = self.classify_exception(exc)
        if isinstance(exc, SyntaxError):
            message = f"{exc.msg} (line {exc.lineno}, offset {exc.offset})"
        else:
            message = str(exc) or type(exc).__name__
        return self.create_record(
            category=category,
            source_module=source_module,
            error_type=type(exc).__name__,
            message=message,
            predecessor_hash=predecessor_hash,
        )


__all__ = [
    "GENESIS_HASH",
    "CategoricalFaultRecord",
    "EnvironmentDeadlockError",
    "FaultCategory",
    "FaultCategorySpec",
    "FaultOntologyRegistry",
    "FaultSeverity",
    "RemediationStrategy",
    "ResidualConvergenceError",
    "SpoofingViolationError",
    "coerce_category",
    "coerce_severity",
    "coerce_strategy",
    "compute_fault_state_digest",
    "is_sha256_hex",
]
