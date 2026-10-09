"""Prepared sterile observer entry. Deployment/task wrapper is not yet released."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

DEPLOYMENT = Path(r'C:\Program Files\CoChem\ResourceObservation4.2.7-windows-20261008-r3-v1')
OUTPUT = Path(r'C:\ProgramData\CoChemPipeline427\private\resource-observation-20261008-r3-v1\samples')
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
PSUTIL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\psutil')
BOOTSTRAP_PIN = '227f4b1ecd3f88b45b53cc53f99f29e359350fb10b7f653314f12043f100b3b5'
BOOTSTRAP_MAX = 8192
INSTALLATION_CONTRACT_RELEASED = False


def bounded(path, maximum):
    with Path(path).open('rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_nlink != 1 or not 0 < before.st_size <= maximum:
            raise RuntimeError('SOURCE_SIZE_OR_LINK')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError('SOURCE_CHANGED')
    return raw


def verify_sources(root, manifest_raw):
    # This bound manifest is produced/reviewed outside the SYSTEM run. It is not
    # an authority for arbitrary import paths: the complete expected list is fixed.
    manifest = json.loads(manifest_raw)
    expected = {'observe_resources.py', 'detector_bootstrap.py',
                'cochem_supervisor/__init__.py', 'cochem_supervisor/windows.py',
                'cochem_supervisor/resource_observation.py', 'cochem_supervisor/performance_acceptance.py',
                'cochem_supervisor/shared_io.py'}
    if manifest.get('schema') != 'cochem-external-observer-source-manifest/1' or not isinstance(manifest.get('files'), list):
        raise RuntimeError('MANIFEST_SCHEMA')
    rows = manifest['files']
    if len(rows) != len(expected) or {x.get('path') for x in rows} != expected:
        raise RuntimeError('SOURCE_SET')
    for row in rows:
        if set(row) != {'path', 'size', 'sha256'} or type(row['size']) is not int or not 0 < row['size'] <= 1048576:
            raise RuntimeError('SOURCE_ROW')
        target = root / row['path']
        for item in (target, *target.parents):
            if item.is_symlink() or item.lstat().st_file_attributes & 0x400:
                raise RuntimeError('SOURCE_REPARSE')
        raw = bounded(target, row['size'])
        if len(raw) != row['size'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise RuntimeError('SOURCE_HASH')
    actual = set()
    for directory in (root, root / 'cochem_supervisor'):
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 9:
                    raise RuntimeError('SOURCE_INVENTORY_BOUND')
                item = Path(entry.path)
                if item.is_symlink() or item.lstat().st_file_attributes & 0x400:
                    raise RuntimeError('SOURCE_REPARSE')
                if item.is_dir():
                    if item != root / 'cochem_supervisor':
                        raise RuntimeError('UNEXPECTED_SOURCE_DIRECTORY')
                else:
                    actual.add(item.relative_to(root).as_posix())
    if actual != expected | {'source-manifest.json'}:
        raise RuntimeError('UNEXPECTED_DEPLOYED_SOURCE')
    return manifest


def prepare_imports(root):
    raw = bounded(root / 'detector_bootstrap.py', BOOTSTRAP_MAX)
    if hashlib.sha256(raw).hexdigest() != BOOTSTRAP_PIN:
        raise RuntimeError('BOOTSTRAP_HASH')
    namespace = {'__name__': '_observer_bootstrap_definitions'}
    exec(compile(raw, str(root / 'detector_bootstrap.py'), 'exec'), namespace)
    namespace['configure_packages'](root / 'cochem_supervisor', PSUTIL)
    from cochem_supervisor import resource_observation
    from cochem_supervisor import windows
    return resource_observation, windows


def import_audit():
    allowed = set(sys.stdlib_module_names) | {'__main__', 'cochem_supervisor', 'psutil'}
    # The unchanged stdlib multiprocessing imported by the exact performance
    # module adds this identity alias; it must not refer to a separate module.
    if sys.modules.get('__mp_main__') is sys.modules['__main__']:
        allowed.add('__mp_main__')
    if any(name.split('.')[0] not in allowed for name in sys.modules):
        raise RuntimeError('IMPORT_BOUNDARY')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-observer', action='store_true')
    parser.add_argument('--source-manifest-sha256')
    parser.add_argument('--commissioning-sha256')
    args = parser.parse_args()
    if not args.run_observer:
        print(json.dumps({'schema': 'cochem-external-resource-observer-plan/1', 'prepared_only': True,
            'deployment_root': str(DEPLOYMENT), 'sample_output': str(OUTPUT), 'duration_seconds': 172800,
            'interval_seconds': 1, 'native_descendant_interval_seconds': 30, 'no_controller_calls': True,
            'no_database_opens': True, 'full_srs_acceptance': False,
            'holds': ['Protected installer/task and complete Python/psutil custody not released.',
                      'Actual successful commissioning receipt is required.',
                      'Native desktop-heap used/allocation collector unavailable.',
                      'Independent monitor/recovery timing source unavailable.']}))
        return 0
    try:
        if not INSTALLATION_CONTRACT_RELEASED:
            raise RuntimeError('INSTALLATION_CONTRACT_NOT_RELEASED')
        if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
            raise RuntimeError('STERILE_FLAGS')
        if Path(__file__).resolve() != DEPLOYMENT / 'observe_resources.py' or Path(sys.executable).resolve() != BASE:
            raise RuntimeError('PROTECTED_ENTRYPOINT')
        if not all(re.fullmatch('[a-f0-9]{64}', value or '') for value in (args.source_manifest_sha256, args.commissioning_sha256)):
            raise RuntimeError('REQUIRED_BINDINGS')
        raw = bounded(DEPLOYMENT / 'source-manifest.json', 65536)
        if hashlib.sha256(raw).hexdigest() != args.source_manifest_sha256:
            raise RuntimeError('MANIFEST_HASH')
        verify_sources(DEPLOYMENT, raw)
        observer, windows = prepare_imports(DEPLOYMENT)
        import_audit()
        windows.require_system()
        windows.validate_code_path(DEPLOYMENT)
        windows.validate_code_path(BASE)
        windows.validate_code_path(PSUTIL)
        # Complete interpreter/stdlib/psutil dependency custody belongs to the
        # reviewed installer before this fixed entrypoint may be scheduled.
        result = observer.run(OUTPUT, args.commissioning_sha256)
        import_audit()
        print(json.dumps({'schema': result['schema'], 'status': result['status'],
                          'full_srs_acceptance': False}))
        return 0 if result['status'] == 'CONTINUOUS_RESOURCE_WINDOW_RECORDED_FULL_SRS_HELD' else 2
    except BaseException as error:
        print(json.dumps({'schema': 'cochem-external-resource-observer-bootstrap-failure/1',
                          'error_type': type(error).__name__, 'full_srs_acceptance': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
