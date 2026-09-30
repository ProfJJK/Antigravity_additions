"""CoChem TOPOS subsystem: conformer ensemble domain models and unified pipeline.

Public API
----------
- ConformerCandidate       : Pydantic v2 model of a single conformer (symbols, Nx3 coordinates,
                             total electronic energy, relative energy, origin, cluster, provenance).
- ConformerEngineMode      : Enum selecting ORCA GOAT, CREST, or dual-union sampling tracks.
- ConformerPipelineConfig  : Pydantic v2 pipeline configuration with case-insensitive engine-mode
                             deserialization and a default 3.0 kcal/mol energy window.
- ConformerUnionPipeline   : Unified pipeline orchestrating conformer generation, energy filtering,
                             rotational constants evaluation, and deduplication.

Symbols are resolved lazily (PEP 562) so that importing the package never creates an
import cycle between ``conformer_ensemble`` and sibling modules such as ``clustering``,
which themselves consume ``ConformerCandidate``.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from cochem.topos.conformer_ensemble import (
        ConformerCandidate,
        ConformerEngineMode,
        ConformerPipelineConfig,
        ConformerUnionPipeline,
    )

__all__ = [
    "ConformerCandidate",
    "ConformerEngineMode",
    "ConformerPipelineConfig",
    "ConformerUnionPipeline",
]

# Mapping of public symbol -> defining submodule (absolute module path).
_EXPORT_SOURCES: Dict[str, str] = {
    "ConformerCandidate": "cochem.topos.conformer_ensemble",
    "ConformerEngineMode": "cochem.topos.conformer_ensemble",
    "ConformerPipelineConfig": "cochem.topos.conformer_ensemble",
    "ConformerUnionPipeline": "cochem.topos.conformer_ensemble",
    "filter_by_energy_window": "cochem.topos.energy_filter",
}


def __getattr__(name: str) -> Any:
    """Resolve public symbols on first access and cache them in the package namespace."""
    module_path = _EXPORT_SOURCES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> List[str]:
    """Expose lazily-resolved public symbols to introspection tools."""
    return sorted(set(globals()) | set(__all__))
