"""Decoding-temperature policy and recovery directives for the self-healing loop.

Temperatures are clamped to the statutory interval T in [0.10, 0.70]:

* base temperature per fault category (``CATEGORY_BASE_TEMPERATURES``),
* a category-specific severity slope (exploratory categories heat up with
  severity, conservative categories cool down),
* a pivot escalation for categories whose remedy benefits from exploration.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from .fault_ontology import (
    FaultCategory,
    FaultSeverity,
    coerce_category,
    coerce_severity,
)

TEMPERATURE_FLOOR = 0.10
TEMPERATURE_CEILING = 0.70

CATEGORY_BASE_TEMPERATURES: Dict[FaultCategory, float] = {
    FaultCategory.SYNTAX_LINT_ERROR: 0.10,
    FaultCategory.PHYSICAL_PARITY_DIVERGENCE: 0.50,
    FaultCategory.SPOOFING_VIOLATION: 0.10,
    FaultCategory.ENVIRONMENT_DEADLOCK: 0.15,
    FaultCategory.TIMEOUT_OVERFLOW: 0.25,
}

SEVERITY_RANK: Dict[FaultSeverity, int] = {
    FaultSeverity.LOW: -1,
    FaultSeverity.MEDIUM: 0,
    FaultSeverity.HIGH: 1,
    FaultSeverity.CRITICAL: 2,
}

CATEGORY_SEVERITY_SLOPE: Dict[FaultCategory, float] = {
    FaultCategory.SYNTAX_LINT_ERROR: -0.02,
    FaultCategory.PHYSICAL_PARITY_DIVERGENCE: 0.05,
    FaultCategory.SPOOFING_VIOLATION: -0.02,
    FaultCategory.ENVIRONMENT_DEADLOCK: -0.02,
    FaultCategory.TIMEOUT_OVERFLOW: -0.02,
}

PIVOT_ESCALATION: Dict[FaultCategory, float] = {
    FaultCategory.SYNTAX_LINT_ERROR: 0.0,
    FaultCategory.PHYSICAL_PARITY_DIVERGENCE: 0.05,
    FaultCategory.SPOOFING_VIOLATION: 0.0,
    FaultCategory.ENVIRONMENT_DEADLOCK: 0.0,
    FaultCategory.TIMEOUT_OVERFLOW: 0.02,
}

_CATEGORY_DIRECTIVES: Dict[FaultCategory, Tuple[str, ...]] = {
    FaultCategory.SYNTAX_LINT_ERROR: (
        "Repair only the AST node reported by the parser; leave every other definition byte-identical.",
        "Re-run ast.parse on the repaired file and reject the patch if compilation fails.",
    ),
    FaultCategory.PHYSICAL_PARITY_DIVERGENCE: (
        "Precondition the numerical grid and tolerances before changing the physical model.",
        "Resolve every atomic mass dynamically through mendeleev.element; never embed mass literals.",
        "Accept the pivot only when the residual falls below the declared tolerance.",
    ),
    FaultCategory.SPOOFING_VIOLATION: (
        "Freeze the branch with the atomic sentinel before any further edit.",
        "Roll the tainted file back to the last authentic commit and re-audit it.",
        "Alert the adversary role with the detected token count and the rollback target.",
    ),
    FaultCategory.ENVIRONMENT_DEADLOCK: (
        "Reclaim a lock only when its holder process is provably dead.",
        "Never terminate or signal a live Council member process (Invariant 9).",
        "Reacquire the lock with O_CREAT|O_EXCL to prove exclusive access.",
    ),
    FaultCategory.TIMEOUT_OVERFLOW: (
        "Tail-truncate captured logs to the most recent lines before analysis.",
        "Partition the workload into N=1 batches and re-measure each batch against the budget.",
    ),
}


def _clamp(value: float) -> float:
    return min(TEMPERATURE_CEILING, max(TEMPERATURE_FLOOR, value))


def calculate_decoding_temperature(
    category: Union[FaultCategory, str],
    severity: Union[FaultSeverity, str] = FaultSeverity.MEDIUM,
    pivot_cycle: int = 1,
    base_override: Optional[float] = None,
) -> Tuple[float, float]:
    """Return ``(temperature, top_p)`` for a fault, clamped to [0.10, 0.70]."""
    resolved_category = coerce_category(category)
    resolved_severity = coerce_severity(severity)
    if isinstance(pivot_cycle, bool) or not isinstance(pivot_cycle, int) or pivot_cycle < 1:
        raise ValueError(f"pivot_cycle must be an integer >= 1, got {pivot_cycle!r}")
    base = CATEGORY_BASE_TEMPERATURES[resolved_category] if base_override is None else float(base_override)
    raw = (
        base
        + CATEGORY_SEVERITY_SLOPE[resolved_category] * SEVERITY_RANK[resolved_severity]
        + PIVOT_ESCALATION[resolved_category] * (pivot_cycle - 1)
    )
    temperature = round(_clamp(raw), 6)
    top_p = round(min(0.95, max(0.70, 0.70 + 0.35 * temperature)), 6)
    return temperature, top_p


def get_temperature_for_failure(fault_record: Any, pivot_cycle: int = 1) -> float:
    """Temperature for a ``CategoricalFaultRecord``-like object."""
    return calculate_decoding_temperature(
        fault_record.category, fault_record.severity, pivot_cycle=pivot_cycle
    )[0]


def generate_directives_for_fault(
    category: Union[FaultCategory, str],
    fault_record: Optional[Any] = None,
) -> List[str]:
    """Deterministic, category-specific recovery directives."""
    resolved = coerce_category(category)
    directives = list(_CATEGORY_DIRECTIVES[resolved])
    if fault_record is not None:
        module = getattr(fault_record, "source_module", "")
        error_type = getattr(fault_record, "error_type", "")
        strategy = getattr(fault_record, "remediation_strategy", None)
        message = str(getattr(fault_record, "message", ""))[:240]
        strategy_text = getattr(strategy, "value", strategy)
        directives.append(
            f"Target {module or 'unknown module'}: {error_type or 'fault'} -> {strategy_text}. Evidence: {message}"
        )
    return directives


__all__ = [
    "CATEGORY_BASE_TEMPERATURES",
    "CATEGORY_SEVERITY_SLOPE",
    "PIVOT_ESCALATION",
    "SEVERITY_RANK",
    "TEMPERATURE_CEILING",
    "TEMPERATURE_FLOOR",
    "calculate_decoding_temperature",
    "generate_directives_for_fault",
    "get_temperature_for_failure",
]
