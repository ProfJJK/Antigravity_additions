"""Sterile observer successor for explicit reviewed installation and one-time start."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

CODE_ROOT = Path(r'C:\Program Files\CoChem\ResourceObservation4.2.7-windows-20261008-r3-v2')
DEPLOYMENT = CODE_ROOT / 'package'
STATE_ROOT = Path(r'C:\Program Files\CoChem\ResourceObservationState4.2.7-windows-20261008-r3-v2')
OUTPUT = STATE_ROOT / 'samples'
CACHE = STATE_ROOT / 'empty-cache'
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
PSUTIL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\psutil')
BOOTSTRAP_PIN = '227f4b1ecd3f88b45b53cc53f99f29e359350fb10b7f653314f12043f100b3b5'
BOOTSTRAP_MAX = 8192
INSTALLATION_CONTRACT_RELEASED = True
WINDOWS_BOUNDARY_PIN = 'd784fad19be42fd84aab237455308d26e2626755de413da2fe84e1ecbbbb0c4b'
DEPENDENCY_PIN = 'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429'


def bounded(path, maximum, *, allow_empty=False):
    with Path(path).open('rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_nlink != 1 or not (0 if allow_empty else 1) <= before.st_size <= maximum:
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


def public_failure(result):
    failure = result.get('failure')
    if isinstance(failure, dict):
        phase = failure.get('phase')
        if phase not in ('commissioning_binding', 'native_observation'):
            phase = 'native_observation'
        name = failure.get('error_type')
        if not isinstance(name, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', name):
            name = 'Exception'
        code = failure.get('code')
        if not isinstance(code, str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,79}', code):
            code = None
        native = failure.get('winerror')
        if type(native) is not int or not 0 <= native <= 0xffffffff:
            native = None
        return {'phase': phase, 'error_type': name, 'code': code, 'winerror': native}
    error = result.get('resource_result', {}).get('error')
    if error is not None:
        match = re.fullmatch('([A-Za-z][A-Za-z0-9_]{0,63}): ([A-Z][A-Z0-9_]{0,79})', str(error))
        return {'phase': 'native_observation', 'error_type': match[1] if match else 'Exception',
                'code': match[2] if match else 'PERFORMANCE_OBSERVATION_FAILED', 'winerror': None}
    return None


def publish_completion(result, dependency_proof, source_manifest_sha256):
    """CreateNew ordinary-readable projection; private native rows stay private."""
    performance = result.get('resource_result', {})
    private_receipt = STATE_ROOT / 'observation-result.json'
    raw = bounded(private_receipt, 65536)
    summary = {'schema': 'cochem-external-resource-observer-completion/1',
               'status': result['status'], 'commissioning_sha256': result['commissioning_sha256'],
               'receipt_path': str(private_receipt), 'receipt_sha256': hashlib.sha256(raw).hexdigest(),
               'continuous_48h_complete': performance.get('continuous_48h_complete', False),
               'sample_count': performance.get('sample_count', 0),
               'controller': performance.get('controller'), 'handles': performance.get('handles'),
               'desktop_heap_available': False, 'recovery_timing_available': False,
               'dependency_verification': dependency_proof,
               'source_manifest_sha256': source_manifest_sha256,
               'source_initial_and_final_verified': True, 'failure': public_failure(result),
               'full_srs_acceptance': False, 'automatic_restart_or_resume': False}
    with (CODE_ROOT / 'observer-completed.json').open('x', encoding='utf-8') as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def failure_boundary():
    """Read-only hash-bound definitions; no nonstandard module registration."""
    if Path(__file__).resolve() != DEPLOYMENT / 'observe_resources.py':
        raise RuntimeError('PROTECTED_ENTRYPOINT')
    raw = bounded(DEPLOYMENT / 'cochem_supervisor/windows.py', 65536)
    if hashlib.sha256(raw).hexdigest() != WINDOWS_BOUNDARY_PIN:
        raise RuntimeError('ERROR_BOUNDARY_HASH')
    namespace = {'__name__': '_observer_failure_boundary'}
    exec(compile(raw, 'hash-bound-observer-windows-boundary', 'exec'), namespace)
    namespace['require_system']()
    namespace['validate_code_path'](CODE_ROOT)
    namespace['validate_code_path'](Path(__file__))
    return namespace


def verify_dependencies(boundary):
    """Bounded initial/final byte-and-custody check, never a per-sample scan."""
    raw = bounded(CODE_ROOT / 'dependencies.json', 2097152)
    if hashlib.sha256(raw).hexdigest() != DEPENDENCY_PIN:
        raise RuntimeError('DEPENDENCY_MANIFEST_HASH')
    value = json.loads(raw)
    if (value.get('schema') != 'cochem-sterile-observer-dependencies/1' or
            value.get('base_root') != str(BASE.parent) or value.get('psutil_root') != str(PSUTIL) or
            len(value.get('base_files', [])) != 2777 or len(value.get('psutil_files', [])) != 11):
        raise RuntimeError('DEPENDENCY_BINDING')
    began = time.monotonic()
    count = 0
    for root, rows, skip_pyc in ((BASE.parent, value['base_files'], True), (PSUTIL, value['psutil_files'], False)):
        boundary['validate_code_path'](root)
        expected = {}
        for row in rows:
            relative = row.get('relative')
            if (not isinstance(relative, str) or '\\' in relative or ':' in relative or
                    any(x in ('', '.', '..') for x in relative.split('/')) or relative in expected):
                raise RuntimeError('DEPENDENCY_PATH')
            expected[relative] = row
        seen = set()
        pending = [root]
        while pending:
            if count >= 5000 or time.monotonic() - began > 240:
                raise RuntimeError('DEPENDENCY_INSPECTION_BOUND')
            current = pending.pop()
            stat = current.lstat()
            count += 1
            if current.is_symlink() or stat.st_file_attributes & 0x400:
                raise RuntimeError('DEPENDENCY_REPARSE')
            owner, rules = boundary['acl'](current)
            trusted = {boundary['SYSTEM'], boundary['ADMIN'], boundary['INSTALLER']}
            if owner not in trusted or any(sid not in trusted and not flags & 8 and mask & 0x500d0116
                                           for sid, mask, flags in rules):
                raise RuntimeError('DEPENDENCY_WRITABLE')
            if current.is_dir():
                with os.scandir(current) as entries:
                    for entry in entries:
                        if len(pending) + count >= 5000:
                            raise RuntimeError('DEPENDENCY_INSPECTION_BOUND')
                        pending.append(Path(entry.path))
                continue
            if not current.is_file() or stat.st_nlink != 1:
                raise RuntimeError('DEPENDENCY_FILE_IDENTITY')
            relative = current.relative_to(root).as_posix()
            if skip_pyc and relative.lower().endswith('.pyc'):
                continue  # Only the separate, verified empty cache prefix is searched.
            row = expected.get(relative)
            if row is None or type(row.get('length')) is not int or not 0 <= row['length'] <= 67108864:
                raise RuntimeError('DEPENDENCY_FILE_SET')
            data = bounded(current, max(1, row['length']), allow_empty=True)
            if len(data) != row['length'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise RuntimeError('DEPENDENCY_BYTES_CHANGED')
            seen.add(relative)
        if seen != set(expected):
            raise RuntimeError('DEPENDENCY_FILE_MISSING')
    return {'manifest_sha256': DEPENDENCY_PIN, 'base_files': 2777, 'psutil_files': 11,
            'custody_entries': count, 'historical_bytecode_not_executed': True}


def publish_failure(error, phase, boundary):
    boundary['validate_code_path'](CODE_ROOT)
    name = type(error).__name__
    if not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', name):
        name = 'Exception'
    code = str(error)
    if not re.fullmatch('[A-Z][A-Z0-9_]{0,79}', code):
        code = None
    native = getattr(error, 'winerror', None)
    if type(native) is not int or not 0 <= native <= 0xffffffff:
        native = None
    value = {'schema': 'cochem-external-resource-observer-bootstrap-failure/1',
             'phase': phase, 'error_type': name, 'code': code, 'winerror': native,
             'full_srs_acceptance': False, 'automatic_restart_or_resume': False}
    private_receipt = STATE_ROOT / 'observation-result.json'
    try:
        boundary['validate_private_path'](private_receipt)
        value['private_receipt_sha256'] = hashlib.sha256(bounded(private_receipt, 65536)).hexdigest()
        value['private_receipt_path'] = str(private_receipt)
    except (OSError, RuntimeError):
        pass
    with (CODE_ROOT / 'observer-bootstrap-failure.json').open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


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
            'holds': ['Protected installation and explicit one-time start are required.',
                      'Actual successful commissioning receipt is required.',
                      'Native desktop-heap used/allocation collector unavailable.',
                      'Independent monitor/recovery timing source unavailable.']}))
        return 0
    boundary = None
    phase = 'entry_custody'
    try:
        if not INSTALLATION_CONTRACT_RELEASED:
            raise RuntimeError('INSTALLATION_CONTRACT_NOT_RELEASED')
        if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and sys.pycache_prefix == str(CACHE)):
            raise RuntimeError('STERILE_FLAGS')
        boundary = failure_boundary()
        if Path(__file__).resolve() != DEPLOYMENT / 'observe_resources.py' or Path(sys.executable).resolve() != BASE:
            raise RuntimeError('PROTECTED_ENTRYPOINT')
        if not all(re.fullmatch('[a-f0-9]{64}', value or '') for value in (args.source_manifest_sha256, args.commissioning_sha256)):
            raise RuntimeError('REQUIRED_BINDINGS')
        phase = 'source_manifest'
        raw = bounded(DEPLOYMENT / 'source-manifest.json', 65536)
        if hashlib.sha256(raw).hexdigest() != args.source_manifest_sha256:
            raise RuntimeError('MANIFEST_HASH')
        verify_sources(DEPLOYMENT, raw)
        phase = 'initial_dependencies'
        initial_dependencies = verify_dependencies(boundary)
        phase = 'sterile_imports'
        observer, windows = prepare_imports(DEPLOYMENT)
        import_audit()
        windows.require_system()
        windows.validate_code_path(DEPLOYMENT)
        windows.validate_code_path(BASE)
        windows.validate_code_path(PSUTIL)
        windows.validate_private_directory(STATE_ROOT)
        windows.validate_private_directory(CACHE)
        if next(CACHE.iterdir(), None) is not None:
            raise RuntimeError('BYTECODE_CACHE_NOT_EMPTY')
        # Installer verifies/holds all pinned non-cache source/DLL dependencies.
        # Historical bytecode caches remain untouched; all cache reads point
        # to this private empty namespace and -B prevents cache publication.
        phase = 'resource_observation'
        result = observer.run(OUTPUT, args.commissioning_sha256)
        import_audit()
        phase = 'final_dependencies'
        final_dependencies = verify_dependencies(boundary)
        final_source = bounded(DEPLOYMENT / 'source-manifest.json', 65536)
        if hashlib.sha256(final_source).hexdigest() != args.source_manifest_sha256:
            raise RuntimeError('FINAL_SOURCE_MANIFEST_HASH')
        verify_sources(DEPLOYMENT, final_source)
        if next(CACHE.iterdir(), None) is not None:
            raise RuntimeError('FINAL_BYTECODE_CACHE_NOT_EMPTY')
        phase = 'completion_projection'
        publish_completion(result, {'initial': initial_dependencies, 'final': final_dependencies,
                                    'initial_and_final_verified': True}, args.source_manifest_sha256)
        print(json.dumps({'schema': result['schema'], 'status': result['status'],
                          'full_srs_acceptance': False}))
        return 0 if result['status'] == 'CONTINUOUS_RESOURCE_WINDOW_RECORDED_FULL_SRS_HELD' else 2
    except BaseException as error:
        if boundary is not None:
            try:
                publish_failure(error, phase, boundary)
            except BaseException:
                pass  # Installer retains its independently bound task result.
        print(json.dumps({'schema': 'cochem-external-resource-observer-bootstrap-failure/1',
                          'error_type': type(error).__name__, 'full_srs_acceptance': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
