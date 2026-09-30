"""CoChem CLI subpackage.

The entrypoint module is deliberately not imported here: import it explicitly as
``from cochem.cli.main import main``. Keeping ``main`` out of this namespace avoids
a circular import (``cochem.cli.main`` imports its sibling modules) and prevents the
``main`` function from shadowing the ``cochem.cli.main`` submodule attribute.
"""

from cochem.cli.audit import (
    AntiSpoofScanner,
    AuditResult,
    AuditViolation,
    ProjectIntegrityError,
    run_audit,
    verify_project_integrity,
)
from cochem.cli.hardware import (
    BinaryAuditRecord,
    EnvironmentVerifier,
    HardwareAuditor,
    HardwareProfile,
)
from cochem.cli.init_wizard import (
    MICRO_SILOS,
    ExistingProjectError,
    InsufficientDiskSpaceError,
    RollbackError,
    run_init_wizard,
)

__all__ = [
    "AntiSpoofScanner",
    "AuditResult",
    "AuditViolation",
    "BinaryAuditRecord",
    "EnvironmentVerifier",
    "ExistingProjectError",
    "HardwareAuditor",
    "HardwareProfile",
    "InsufficientDiskSpaceError",
    "MICRO_SILOS",
    "ProjectIntegrityError",
    "RollbackError",
    "run_audit",
    "run_init_wizard",
    "verify_project_integrity",
]
