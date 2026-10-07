"""Start the protected observer without site, .pth or virtualenv startup code.

The privileged parent supplies two previously protected, exact package paths.
They are package search locations only: no site-packages directory is added to
sys.path, and no package or executable path is discovered from candidate code.
"""
from __future__ import annotations

import argparse
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys


class _PackageImports(importlib.abc.MetaPathFinder):
    def __init__(self, packages):
        self.packages = packages
        self.allowed = set(sys.stdlib_module_names) | {'__main__', *packages}

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in self.allowed:
            raise ImportError('Observer imports are limited to standard library and protected packages')
        package = self.packages.get(fullname)
        if package is not None:
            return importlib.util.spec_from_file_location(fullname, package / '__init__.py',
                submodule_search_locations=[str(package)])
        return None


def configure_packages(supervisor_package: Path, psutil_package: Path) -> None:
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError('The observer requires isolated startup with site disabled')
    packages = {'cochem_supervisor': Path(supervisor_package), 'psutil': Path(psutil_package)}
    for name, package in packages.items():
        if not package.is_absolute() or package.name != name or not (package / '__init__.py').is_file():
            raise ValueError('The observer requires exact protected package directories')
        for item in (package / '__init__.py', package, *package.parents):
            if item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Observer package locations cannot traverse links')
    allowed = set(sys.stdlib_module_names) | {'__main__'}
    if any(name.split('.')[0] not in allowed for name in sys.modules):
        raise RuntimeError('Nonstandard startup modules executed before the observer boundary')
    sys.meta_path.insert(0, _PackageImports(packages))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Protected isolated observer bootstrap')
    parser.add_argument('--supervisor-package', required=True, type=Path)
    parser.add_argument('--psutil-package', required=True, type=Path)
    args, detector_args = parser.parse_known_args(argv)
    try:
        configure_packages(args.supervisor_package, args.psutil_package)
        from cochem_supervisor.detector import main as detect
        return detect(detector_args)
    except Exception as exc:
        sys.stdout.write(json.dumps({'error_type': type(exc).__name__}) + '\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
