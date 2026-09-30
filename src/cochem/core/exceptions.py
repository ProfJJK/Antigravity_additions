"""Domain exception hierarchy for the CoChem core subsystem.

Every CoChem error carries three machine-actionable attributes:

* ``message``: human readable diagnostic text (also the ``str()`` of the error),
* ``error_code``: a stable ``COCHEM_E_*`` identifier that automation can branch on,
* ``details``: a dictionary of structured context (iteration counts, device ids, ...).

This module deliberately imports only the standard library so that every other
``cochem.core`` module can depend on it without creating import cycles.
"""

import re
from typing import Any, ClassVar, Dict, Mapping, Optional, Type

__all__ = [
    "CoChemError",
    "PhysicalConvergenceError",
    "HardwareTopologyError",
    "AtomicWriteError",
    "PurgeVerificationError",
    "ERROR_CODES",
]

_ERROR_CODE_PATTERN = re.compile(r"COCHEM_E_[A-Z][A-Z_]*")


def _rebuild_error(
    cls: Type["CoChemError"], message: str, error_code: str, details: Dict[str, Any]
) -> "CoChemError":
    """Reconstruct a pickled CoChem error with its keyword-only metadata."""
    return cls(message, error_code=error_code, details=details)


def _normalize_details(details: Any) -> Dict[str, Any]:
    """Return a private dictionary copy of the structured error context."""
    if details is None:
        return {}
    if isinstance(details, Mapping):
        return dict(details)
    return {"value": details}


class CoChemError(Exception):
    """Base class of every CoChem domain error."""

    ERROR_CODE: ClassVar[str] = "COCHEM_E_GENERIC"
    DEFAULT_MESSAGE: ClassVar[str] = "CoChem runtime failure."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        error_code: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        resolved_message = self.DEFAULT_MESSAGE if message is None else str(message)
        resolved_code = self.ERROR_CODE if error_code is None else str(error_code)
        if _ERROR_CODE_PATTERN.fullmatch(resolved_code) is None:
            raise ValueError(
                f"error_code {resolved_code!r} must match {_ERROR_CODE_PATTERN.pattern}"
            )
        super().__init__(resolved_message)
        self.message: str = resolved_message
        self.error_code: str = resolved_code
        self.details: Dict[str, Any] = _normalize_details(details)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible machine-actionable representation."""
        return {
            "exception": type(self).__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": dict(self.details),
        }

    def __reduce__(self) -> Any:
        return (_rebuild_error, (type(self), self.message, self.error_code, dict(self.details)))


class PhysicalConvergenceError(CoChemError, RuntimeError):
    """Raised when an ab-initio or physical calculation trajectory fails to converge."""

    ERROR_CODE: ClassVar[str] = "COCHEM_E_PHYSICAL_CONVERGENCE"
    DEFAULT_MESSAGE: ClassVar[str] = "Physical convergence computation failed: method not converged."


class HardwareTopologyError(CoChemError, RuntimeError):
    """Raised when execution hardware accelerator topology constraints are unsatisfied."""

    ERROR_CODE: ClassVar[str] = "COCHEM_E_HARDWARE_TOPOLOGY"
    DEFAULT_MESSAGE: ClassVar[str] = "Hardware topology execution constraint violated."


class AtomicWriteError(CoChemError, RuntimeError):
    """Raised when a crash-safe file replacement cannot be committed or verified."""

    ERROR_CODE: ClassVar[str] = "COCHEM_E_ATOMIC_WRITE"
    DEFAULT_MESSAGE: ClassVar[str] = "Atomic file write could not be committed."


class PurgeVerificationError(CoChemError, RuntimeError):
    """Raised when a remediated source fails its post-rewrite syntax-tree verification."""

    ERROR_CODE: ClassVar[str] = "COCHEM_E_PURGE_VERIFICATION"
    DEFAULT_MESSAGE: ClassVar[str] = "Remediated source failed syntax-tree verification."


ERROR_CODES: Dict[str, str] = {
    cls.__name__: cls.ERROR_CODE
    for cls in (
        CoChemError,
        PhysicalConvergenceError,
        HardwareTopologyError,
        AtomicWriteError,
        PurgeVerificationError,
    )
}
