"""Static configuration for the CoChem core production purge engine.

The values here define which files the purge engine may rewrite, which
directories are never traversed, which vocabulary routes a dead-end interface
to :class:`cochem.core.exceptions.HardwareTopologyError`, and where the
production tree and its mirror live.

The pipeline daemons ``task_work_loop.py`` and ``kanban_v2_daemon.py`` are
excluded on purpose. They are owned by other tasks, and the purge engine must
never modify them even when they sit inside ``src/cochem/core``.
"""

from dataclasses import dataclass
from typing import FrozenSet, Iterable

__all__ = [
    "PURGE_EXCLUDED_FILE_NAMES",
    "PURGE_EXCLUDED_DIR_NAMES",
    "HARDWARE_CONTEXT_KEYWORDS",
    "DOMAIN_EXCEPTION_MODULE",
    "DEFAULT_PRODUCTION_ROOT",
    "DEFAULT_MIRROR_ROOT",
    "PurgeConfig",
    "DEFAULT_PURGE_CONFIG",
]

PURGE_EXCLUDED_FILE_NAMES: FrozenSet[str] = frozenset({"task_work_loop.py", "kanban_v2_daemon.py"})

PURGE_EXCLUDED_DIR_NAMES: FrozenSet[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
    }
)

HARDWARE_CONTEXT_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "accelerator",
        "accelerators",
        "cuda",
        "device",
        "devices",
        "gpu",
        "gpus",
        "hardware",
        "mps",
        "npu",
        "nvlink",
        "rocm",
        "topology",
        "tpu",
        "xpu",
    }
)

DOMAIN_EXCEPTION_MODULE = "cochem.core.exceptions"
DEFAULT_PRODUCTION_ROOT = "src/cochem/core"
DEFAULT_MIRROR_ROOT = "cochem/core"


def _frozen_names(values: Iterable[str], field_name: str) -> FrozenSet[str]:
    """Validate and freeze a collection of names."""
    if isinstance(values, str):
        raise TypeError(f"{field_name} must be a collection of names, not a single string")
    frozen = frozenset(values)
    for value in frozen:
        if not isinstance(value, str) or not value:
            raise TypeError(f"{field_name} entries must be non-empty strings, got {value!r}")
    return frozen


@dataclass(frozen=True)
class PurgeConfig:
    """Immutable configuration consumed by the production purge engine."""

    excluded_file_names: FrozenSet[str] = PURGE_EXCLUDED_FILE_NAMES
    excluded_dir_names: FrozenSet[str] = PURGE_EXCLUDED_DIR_NAMES
    hardware_keywords: FrozenSet[str] = HARDWARE_CONTEXT_KEYWORDS
    domain_exception_module: str = DOMAIN_EXCEPTION_MODULE
    default_production_root: str = DEFAULT_PRODUCTION_ROOT
    default_mirror_root: str = DEFAULT_MIRROR_ROOT

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "excluded_file_names", _frozen_names(self.excluded_file_names, "excluded_file_names")
        )
        object.__setattr__(
            self, "excluded_dir_names", _frozen_names(self.excluded_dir_names, "excluded_dir_names")
        )
        object.__setattr__(
            self,
            "hardware_keywords",
            frozenset(word.lower() for word in _frozen_names(self.hardware_keywords, "hardware_keywords")),
        )
        if not self.domain_exception_module or not all(
            part.isidentifier() for part in self.domain_exception_module.split(".")
        ):
            raise ValueError(
                f"domain_exception_module {self.domain_exception_module!r} is not a dotted module path"
            )


DEFAULT_PURGE_CONFIG = PurgeConfig()
