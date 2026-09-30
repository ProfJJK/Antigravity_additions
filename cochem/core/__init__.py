"""CoChem core subsystem package.

Exposes the domain exception hierarchy, the purge configuration, the read-only
AST static inspector with its immutable Pydantic v2 diagnostic contracts, and
the production stub, pass and comment purge engine. ``pkgutil.extend_path``
keeps ``cochem.core`` mergeable across several source roots, matching the root
``cochem`` namespace package.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from cochem.core.exceptions import (  # noqa: E402
    AtomicWriteError,
    CoChemError,
    HardwareTopologyError,
    PhysicalConvergenceError,
    PurgeVerificationError,
)
from cochem.core.config import DEFAULT_PURGE_CONFIG, PurgeConfig  # noqa: E402
from cochem.core import ast_eradication_engine as _ast_engine  # noqa: E402

# Public names re-exported from the AST eradication engine. Only names the
# engine module actually defines are exposed, so a symbol that was renamed or
# removed there (for example ``CommentSanitizationEngine``) can no longer
# break package initialisation and, with it, every ``cochem.core.*`` import.
_ENGINE_EXPORTS = (
    "ASTDefectRecord",
    "ASTDefectVisitor",
    "ASTEradicationEngine",
    "CommentSanitizationEngine",
    "DefectCategory",
    "DualTreeSyncResult",
    "EradicationPlan",
    "FileASTReport",
    "PassEradicationTransformer",
    "ProductionPurgeTransformer",
    "StubEradicationTransformer",
    "compute_diff",
    "compute_sha256",
    "eradicate_source",
    "purge_production_stubs_and_comments",
    "purge_source",
    "remediate_file",
    "scan_lexical_tokens",
    "scan_purge_defects",
    "strip_unauthorized_comments",
    "sync_trees",
    "write_atomic",
)

_available_engine_exports = [
    _name for _name in _ENGINE_EXPORTS if hasattr(_ast_engine, _name)
]
for _name in _available_engine_exports:
    globals()[_name] = getattr(_ast_engine, _name)

__all__ = [
    "AtomicWriteError",
    "CoChemError",
    "DEFAULT_PURGE_CONFIG",
    "HardwareTopologyError",
    "PhysicalConvergenceError",
    "PurgeConfig",
    "PurgeVerificationError",
    *_available_engine_exports,
]
