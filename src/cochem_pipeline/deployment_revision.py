"""Match protected installed code/assets to the exact owner-amended checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat


_PACKAGES = ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor')
_SUFFIXES = {'.py', '.md', '.json', '.xml'}


def _inventory(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('Required installed revision directory is missing: ' + str(root))
    result = {}
    for path in root.rglob('*'):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Revision verification cannot follow links or reparse points')
        if '__pycache__' not in path.parts and path.suffix in _SUFFIXES and path.is_file():
            if info.st_nlink != 1:
                raise ValueError('Revision files must not have alternate hardlinks')
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify_installed_revision(repository, installed_packages, installed_source, *, acceptance_root=None):
    """No writes or imports from the proposed repository. Caller verifies ACLs."""
    repository, packages, source = map(Path, (repository, installed_packages, installed_source))
    expected, actual = {}, {}
    for package in _PACKAGES:
        expected.update({package + '/' + key: value for key, value in _inventory(repository/'src'/package).items()})
        actual.update({package + '/' + key: value for key, value in _inventory(packages/package).items()})
    if expected != actual:
        changed = sorted(key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key))
        raise ValueError('Installed canonical revision differs; preserve it and use a fresh protected InstallRoot: ' + ', '.join(changed[:20]))
    for name in ('pyproject.toml', 'uv.lock'):
        if (repository/name).read_bytes() != (source/name).read_bytes():
            raise ValueError('Installed dependency/source policy differs; choose a fresh protected InstallRoot: ' + name)
    acceptance = {}
    if acceptance_root is not None:
        for suite in ('pipeline_tests', 'mcp_tests'):
            target = _inventory(Path(acceptance_root)/suite)
            if _inventory(repository/suite) != target:
                raise ValueError('Installed immutable acceptance suite differs: ' + suite)
            acceptance.update({suite + '/' + key: value for key, value in target.items()})
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return {'schema': 'cochem-installed-revision/1', 'verified': True, 'read_only': True,
            'files': len(actual), 'source_sha256': hashlib.sha256(encode(actual)).hexdigest(),
            'acceptance_files': len(acceptance),
            'acceptance_sha256': hashlib.sha256(encode(acceptance)).hexdigest() if acceptance else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repository', required=True)
    parser.add_argument('--installed-source', required=True)
    parser.add_argument('--acceptance-root')
    args = parser.parse_args()
    report = verify_installed_revision(args.source_repository, Path(__file__).resolve().parent.parent,
                                      args.installed_source, acceptance_root=args.acceptance_root)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
