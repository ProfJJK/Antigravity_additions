"""One-shot SYSTEM staging of a NEW unpublished independent budget-hold pair.

No Supervisor, worker, service, live database, credentials, release pointer,
pipeline provisioning, model or Docker API is used. No retry or reset path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys
import time

ROOT = Path(r'C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1')
CONTROL = ROOT / 'controls'
PRIVATE = ROOT / 'unpublished-private'
PAIR = PRIVATE / 'unresolved-pair'
PIPELINE = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
BOOTSTRAP_SHA = '1c431c12f5b502664494fb09bf6c0ef75bea9e6fad7739f8478b3591a24dcc11'
EVIDENCE_SHA = 'b7b822ff263720dfb443607f0e3cbf515d175b9b7784ea4fd15f8968b7ff3709'
CONFIG_SHA = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
ENGINE_SHA = '9ec21d808bfbb1c5dfe01a6dd41b0dc969f9fc92df4ff5de010c4890c1dddeea'
REPLAY_SHA = 'd0a10b30e3f16d44a2906d855d46f44dc0fbad7249b8b43d6b67b98e4d94d60d'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary(path, directory=False):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('REPARSE_PATH')
    info = path.lstat()
    if (directory and not stat.S_ISDIR(info.st_mode) or not directory
            and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise ValueError('NONORDINARY_PATH')
    return info


def read(path, expected=None, limit=16 * 1024 * 1024):
    before = ordinary(path)
    if before.st_size > limit:
        raise ValueError('OVERSIZED_CONTROL')
    with Path(path).open('rb') as stream:
        opened = os.fstat(stream.fileno()); raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    end = ordinary(path)
    if len({(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            for s in (before, opened, after, end)}) != 1 or len(raw) != before.st_size:
        raise ValueError('SOURCE_CHANGED')
    if expected is not None and sha(raw) != expected:
        raise ValueError('HASH_MISMATCH')
    return raw


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or any(c in value for c in ':\x00<>"|?*'):
        raise ValueError('INVALID_RELATIVE_PATH')
    for part in value.split('/'):
        if (part in ('', '.', '..') or part[-1:] in ('.', ' ') or any(ord(c) < 32 for c in part)
                or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)):
            raise ValueError('INVALID_RELATIVE_PATH')
    return value


def manifest_rows(manifest):
    if (manifest.get('schema') != 'cochem-independent-supervisor-staging-freeze/1'
            or manifest.get('complete_source_freeze') is not True
            or manifest.get('target_root') != str(ROOT)
            or manifest.get('unpublished_pair_root') != str(PAIR)
            or manifest.get('pipeline_config_sha256') != CONFIG_SHA
            or manifest.get('activation_authorized') is not False):
        raise ValueError('UNREVIEWED_MANIFEST')
    result = []; seen = set()
    for field, destination in (('source_files', ROOT / 'source'),
                               ('acceptance_files', ROOT / 'acceptance')):
        values = manifest.get(field)
        if not isinstance(values, list) or not 1 <= len(values) <= 5000:
            raise ValueError('INVALID_INVENTORY')
        for row in values:
            if not isinstance(row, dict) or set(row) != {'relative', 'sha256', 'bytes'}:
                raise ValueError('INVALID_INVENTORY_ROW')
            relative = safe_relative(row['relative']); key = (field, relative.casefold())
            if key in seen or not re.fullmatch('[0-9a-f]{64}', str(row['sha256'])) or type(row['bytes']) is not int or not 0 <= row['bytes'] <= 16 * 1024 * 1024:
                raise ValueError('INVALID_INVENTORY_ROW')
            seen.add(key); result.append((destination / relative, row))
    if sum(row['bytes'] for _, row in result) > 256 * 1024 * 1024:
        raise ValueError('INVENTORY_TOO_LARGE')
    source = {row['relative']: row['sha256'] for row in manifest['source_files']}
    if len(source) != 166 or source.get('src/cochem_supervisor/engine.py') != ENGINE_SHA or source.get('src/cochem_supervisor/replay.py') != REPLAY_SHA:
        raise ValueError('CANDIDATE_SOURCE_MISMATCH')
    return result


def verify_runtime(manifest_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.deployment_revision import verify_installed_revision
    win.require_system()
    if Path(sys.executable).absolute() != ROOT / '.venv/Scripts/python.exe' or PIPELINE in Path(sys.executable).parents:
        raise ValueError('INDEPENDENT_INTERPRETER_REQUIRED')
    for path in (ROOT, ROOT / 'source', ROOT / 'acceptance', CONTROL):
        ordinary(path, True); win.validate_code_path(path)
    for path in (Path(__file__), ROOT / '.venv/Scripts/python.exe', Path(sys._base_executable)):
        ordinary(path); win.validate_code_path(path)
    manifest = json.loads(read(CONTROL / 'staging-manifest.json', manifest_sha))
    for path, row in manifest_rows(manifest):
        win.validate_code_path(path)
        if len(read(path, row['sha256'])) != row['bytes']:
            raise ValueError('SOURCE_LENGTH_MISMATCH')
    config = PIPELINE / 'pipeline.json'; win.validate_code_path(config); read(config, CONFIG_SHA)
    revision = verify_installed_revision(ROOT / 'source', ROOT / '.venv/Lib/site-packages', ROOT / 'source')
    if revision['verified'] is not True or revision['files'] != 109:
        raise ValueError('INSTALLED_REVISION_MISMATCH')
    return manifest, revision


def assert_pair_absent():
    ordinary(PRIVATE, True)
    from cochem_pipeline import windows as win
    win.validate_private_directory(PRIVATE)
    _, _, rules = win._acl(PRIVATE)
    if not any(sid == win.SYSTEM_SID and mask == win.FULL_CONTROL and flags & 3 == 3 and not flags & 12
               for sid, mask, flags in rules):
        raise ValueError('PRIVATE_INHERITANCE_UNVERIFIED')
    if list(PRIVATE.iterdir()):
        raise ValueError('PRIVATE_STAGING_NOT_EMPTY')
    try:
        PAIR.lstat()
    except FileNotFoundError:
        return
    raise ValueError('PRESERVE_EXISTING_PAIR')


def run(nonce, manifest_sha):
    if not re.fullmatch('[0-9a-f]{32}', nonce) or not re.fullmatch('[0-9a-f]{64}', manifest_sha):
        raise ValueError('INVALID_INVOCATION')
    report = {'schema': 'cochem-independent-supervisor-staging/1',
        'status': 'STAGING_HELD', 'nonce': nonce, 'root': str(ROOT),
        'helper_sha256': sha(read(Path(__file__))), 'manifest_sha256': manifest_sha,
        'started_at': time.time(), 'phase': 'runtime', 'pair_creation_started': False,
        'activation_ready': False, 'paid_repair_enabled': False, 'component_recovery_enabled': False,
        'old_ledgers_opened': False, 'provider_or_model_calls': 0,
        'active_warden_changed': False, 'pipeline_configuration_changed': False,
        'partial_outputs_preserved': True}
    from cochem_pipeline import windows as win
    win.require_system()
    # Reject a repeated receipt before any source/ledger operation.
    target = CONTROL / 'staging-receipt.json'
    ordinary(CONTROL, True)
    win.validate_code_path(CONTROL)
    with target.open('xb') as receipt:
        try:
            _, revision = verify_runtime(manifest_sha)
            report['revision'] = revision
            report['phase'] = 'fresh_private_boundary'
            assert_pair_absent()
            bootstrap = CONTROL / 'bootstrap-unresolved-budget-pair.py'
            win.validate_code_path(bootstrap); read(bootstrap, BOOTSTRAP_SHA)
            evidence = json.loads(read(CONTROL / 'unresolved-budget-evidence.json', EVIDENCE_SHA))
            definitions = runpy.run_path(str(bootstrap), run_name='cochem_unpublished_pair_definitions')
            report['phase'] = 'paired_hold_creation'; report['pair_creation_started'] = True
            pair = definitions['bootstrap_pair'](PAIR, evidence)
            if (pair['status'] != 'UNRESOLVED_PAIR_STAGED_UNPUBLISHED' or pair['historical_allowance'] is not None
                    or any(pair[key] is not False for key in ('paid_repair_enabled', 'component_recovery_enabled',
                        'activation_ready', 'live_configuration_changed', 'old_ledgers_opened'))):
                raise ValueError('PAIR_COMPLETION_MISMATCH')
            report['paired_receipt_sha256'] = sha(read(PAIR / 'paired-hold-complete.json', limit=65536))
            report['paired_evidence_sha256'] = pair['evidence_sha256']
            report['ledger_sha256'] = {name: value['sha256'] for name, value in pair['ledgers'].items()}
            report['phase'] = 'post_verification'
            _, again = verify_runtime(manifest_sha)
            if revision != again or definitions['verify_pair'](PAIR, evidence) != pair:
                raise ValueError('STAGING_CHANGED')
            report.update(status='INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED', phase='complete')
        except Exception as error:
            code = getattr(error, 'winerror', None)
            report['failure'] = {'phase': report['phase'], 'error_type': type(error).__name__,
                'winerror': code if type(code) is int and 0 <= code <= 65535 else None}
        finally:
            report['finished_at'] = time.time()
            raw = (json.dumps(report, indent=2, allow_nan=False) + '\n').encode()
            if len(raw) > 65536:
                raise ValueError('RECEIPT_TOO_LARGE')
            receipt.write(raw); receipt.flush(); os.fsync(receipt.fileno())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--nonce'); parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'status': 'READ_ONLY_PREVIEW_NO_IO', 'root': str(ROOT),
            'pair': str(PAIR), 'activation_ready': False, 'old_ledgers_opened': False}))
        return 0
    report = run(args.nonce or '', args.manifest_sha256 or '')
    print(json.dumps({'status': report['status'], 'phase': report['phase'], 'activation_ready': False}))
    return 0 if report['status'] == 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
