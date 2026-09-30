"""CoChem ML package.

Re-exports the public RAM guardrail API (Task 1.13), preserves the package's
pre-existing public exports (``DEFAULT_IGNORE_PATTERNS`` and
``FileSystemEventListener``), and lazily exposes the lock reclamation API
(Task 2.12) and the rollback latency benchmark API (Task 2.19) through PEP 562
so heavy dependencies (``mendeleev``, ``pydantic``) load only on first use.
"""
from __future__ import annotations

import ast
import importlib
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ram_guardrail import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_WARNING_RATIO,
    RAM_CEILING_BYTES,
    RAMCeilingExceededError,
    RAMGuardrail,
    RAMGuardrailConfig,
    RAMSnapshot,
)

_LEGACY_EXPORTS = ("DEFAULT_IGNORE_PATTERNS", "FileSystemEventListener")
_PACKAGE_DIR = Path(__file__).resolve().parent

_LOCK_RECLAMATION_EXPORTS = (
    "BatchReclamationSummary",
    "CANONICAL_RECLAIMER_ROLE",
    "COUNCIL_OVERRIDE_TOKEN_PREFIX",
    "CouncilImmunityReclamationError",
    "CryptographicChallengeError",
    "DEFAULT_RECLAMATION_LEDGER_FILENAME",
    "LOCK_RECLAMATION_VERSION",
    "LockReclamationConfig",
    "LockReclamationError",
    "LockReclamationReceipt",
    "MAX_RECLAMATION_LATENCY_CEILING_MS",
    "OrphanClassification",
    "RECLAMATION_SUBSYSTEM_NAME",
    "ReclamationAuditChallenge",
    "ReclamationLatencyError",
    "ReclamationLedger",
    "ReclamationLedgerError",
    "ReclamationStatus",
    "SafeLockReclamationProtocol",
    "TOCTOURaceError",
    "compute_challenge_digest",
    "create_council_override_token",
    "generate_reclamation_signature",
    "safe_reclaim_batch",
    "safe_reclaim_lock",
    "verify_council_override_token",
)

_ROLLBACK_BENCHMARK_EXPORTS = (
    "RollbackLatencyBenchmarkRunner",
    "RollbackBenchmarkRunner",
    "RollbackLatencySLAExceededError",
    "LatencySLAExceededError",
    "RollbackBenchmarkError",
    "BenchmarkIntegrityError",
    "RollbackBenchmarkConfig",
    "LatencySample",
    "BenchmarkStatisticalSummary",
    "StateRollbackBenchmarkResult",
    "RollbackBenchmarkReceipt",
    "compute_latency_statistics",
    "compute_mendeleev_provenance_digest",
    "DEFAULT_ROLLBACK_LATENCY_CEILING_MS",
    "DEFAULT_BENCHMARK_ITERATIONS",
    "DEFAULT_WARMUP_ITERATIONS",
    "WORKSPACE_ROLLBACK_PHASE",
    "STATE_RECONSTITUTION_PHASE",
)

# Public name -> submodule that defines it (resolved on first attribute access).
_LAZY_EXPORTS: Dict[str, str] = {name: "lock_reclamation" for name in _LOCK_RECLAMATION_EXPORTS}
_LAZY_EXPORTS.update({name: "rollback_benchmark" for name in _ROLLBACK_BENCHMARK_EXPORTS})
_LAZY_SUBMODULES = frozenset(
    {"lock_reclamation", "safe_lock_reclamation", "deadlock_detector", "rollback_benchmark"}
)


def _module_source_files() -> List[Path]:
    """Sibling modules/packages of this package (excluding this file and the guardrail)."""
    files: List[Path] = []
    for entry in sorted(_PACKAGE_DIR.iterdir()):
        if entry.is_file() and entry.suffix == ".py":
            if entry.stem not in ("__init__", "ram_guardrail"):
                files.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            files.append(entry / "__init__.py")
    return files


def _defined_names(tree: ast.Module) -> set:
    """Top-level names bound in a module (classes, functions, assignments, imports)."""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _locate_legacy_exports() -> Dict[str, str]:
    """Map each legacy export name to the sibling module that defines it."""
    remaining = set(_LEGACY_EXPORTS)
    located: Dict[str, str] = {}
    for source_file in _module_source_files():
        if not remaining:
            break
        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module_name = source_file.parent.name if source_file.name == "__init__.py" else source_file.stem
        for name in _defined_names(tree) & remaining:
            located[name] = module_name
            remaining.discard(name)
    return located


def _restore_legacy_exports() -> List[str]:
    """Import and bind the legacy exports; return the names successfully restored."""
    restored: List[str] = []
    located = _locate_legacy_exports()
    for name in _LEGACY_EXPORTS:
        module_name: Optional[str] = located.get(name)
        if module_name is None:
            warnings.warn(
                f"cochem_ml: legacy export {name!r} could not be located in any package module",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        module = importlib.import_module(f".{module_name}", __name__)
        globals()[name] = getattr(module, name)
        restored.append(name)
    return restored


_restored_legacy = _restore_legacy_exports()


def __getattr__(name: str) -> Any:
    """PEP 562 lazy resolution of lock reclamation / rollback benchmark exports and submodules."""
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is not None:
        value = getattr(importlib.import_module(f".{submodule}", __name__), name)
        globals()[name] = value
        return value
    if name in _LAZY_SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | set(_LAZY_SUBMODULES))


__all__ = [
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_WARNING_RATIO",
    "RAM_CEILING_BYTES",
    "RAMCeilingExceededError",
    "RAMGuardrail",
    "RAMGuardrailConfig",
    "RAMSnapshot",
    *_restored_legacy,
    *_LOCK_RECLAMATION_EXPORTS,
    *_ROLLBACK_BENCHMARK_EXPORTS,
]
