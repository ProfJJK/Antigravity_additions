"""Pytest bootstrap for the CoChem agentic workspace.

Guarantees that this repository's own ``cochem`` package wins import resolution
over same-named legacy packages that may be reachable through parent-directory
pytest configuration or user-site ``.pth`` entries.
"""

import importlib
from pathlib import Path
import sys
from typing import Any

_ROOT = Path(__file__).resolve().parent
_ROOT_STR = str(_ROOT)


def _module_is_local(module: Any) -> bool:
    """True if the module was loaded from a location inside this repository."""
    locations = []
    module_file = getattr(module, "__file__", None)
    if module_file:
        locations.append(module_file)
    locations.extend(str(p) for p in getattr(module, "__path__", []) or [])
    if not locations:
        return False
    for location in locations:
        try:
            Path(location).resolve().relative_to(_ROOT)
        except ValueError:
            return False
    return True


def _pin_repo_root_first() -> None:
    """Put the repository root at sys.path[0] and evict foreign ``cochem`` modules."""
    while _ROOT_STR in sys.path:
        sys.path.remove(_ROOT_STR)
    sys.path.insert(0, _ROOT_STR)

    loaded = sys.modules.get("cochem")
    if loaded is not None and not _module_is_local(loaded):
        for name in [n for n in sys.modules if n == "cochem" or n.startswith("cochem.")]:
            del sys.modules[name]

    importlib.invalidate_caches()


_pin_repo_root_first()
