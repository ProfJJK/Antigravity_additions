"""SYSTEM-only acceptance of a freshly copied private corpus and NEW index.

No permission fixtures, original database reads, native providers, repairs,
daemon activation, source rewriting, or production configuration writes.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(r'C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006')
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
CORPUS = Path(r'C:\Program Files\CoChem\Knowledge4.2.7-windows-20261006')
PRIVATE = Path(r'C:\ProgramData\CoChemPipeline427\private')
STATE = PRIVATE / 'knowledge-windows-20261006'
PYTHON = INSTALL / '.venv/Scripts/python.exe'
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312')
BASE_SHA = 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
VENV_SHA = 'd6ebb0d905488486e5a438a8a30a589f256f3499515baf1dd9d4ab71693b5f95'
MODULE_PINS = {
    'cochem_pipeline.windows': 'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba',
    'cochem_pipeline.knowledge': 'f7b14fc86a7e0739f7788b5710bda09f330d0b1032d403942708fdb415071b76',
    'cochem_pipeline.knowledge_authority': 'e7dd99cb0f9a6710d3a03cff2d983a58879fef0b68bd463c0f38607603ba1f27',
}
MANIFEST_SHA = '7c16c12d319aa89b78fcd83628be7e23250259ad9b05b0c246dbd48746d44ec5'
SOURCE_PINS_SHA = 'df99c5cab0eebead545980d68e205aeed0b8649221aec03e0ec6b4ec7d9691ba'
CONFIG_SHA = '2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
DAEMONS = ['CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor', 'CoChem-4.2.2-Warden', 'CoChem-4.2.3-Supervisor']


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON field')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def digest(path, maximum=8 * 1048576):
    before = path.stat()
    if before.st_size > maximum:
        raise ValueError('Evidence file exceeds bound')
    raw = path.read_bytes()
    after = path.stat()
    if len(raw) > maximum or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Evidence file changed while reading')
    return hashlib.sha256(raw).hexdigest()


def require_stopped(win):
    win._powershell(r"""
        $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');
        foreach ($name in $data) {
            $task=$null;
            try {$task=$folder.GetTask($name)} catch {
                $missing=$false;$errorItem=$_.Exception;
                while ($null -ne $errorItem) {if ($errorItem.HResult -eq -2147024894) {$missing=$true;break};$errorItem=$errorItem.InnerException}
                if (-not $missing) {throw 'Task state unknown.'}
            }
            if ($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)) {throw 'Protected daemons must remain stopped and disabled.'}
        }
    """, DAEMONS)


def verify_inventory(inventory, root, ordinary):
    if (not isinstance(inventory, dict) or inventory.get('schema') != 'cochem-private-knowledge-payload/1'
            or inventory.get('documents') != 137 or inventory.get('corpus_manifest_sha256') != MANIFEST_SHA
            or inventory.get('target_root') != str(CORPUS) or inventory.get('target_state') != str(STATE)
            or not isinstance(inventory.get('files'), list) or len(inventory['files']) != 138):
        raise ValueError('Private payload manifest differs from reviewed candidate')
    seen = set(); expected = {}
    for row in inventory['files']:
        if not isinstance(row, dict) or set(row) != {'relative', 'sha256', 'length'}:
            raise ValueError('Invalid payload record')
        key = row['relative']
        if (not isinstance(key, str) or '\\' in key or ':' in key or key.startswith('/')
                or any(part in ('', '.', '..') for part in key.split('/'))
                or key.casefold() in seen or not re.fullmatch('[0-9a-f]{64}', str(row['sha256']))
                or type(row['length']) is not int or not 0 < row['length'] <= 1048576):
            raise ValueError('Unsafe or duplicate private payload path')
        if key != 'v4.1.2_manifest.json':
            from cochem_pipeline.knowledge import _key
            _key(key)
        seen.add(key.casefold()); expected[key] = row
    expected_dirs = {'.sources', '.sources/legacy', '.sources/legacy/sha256', 'wiki'}
    if set(inventory.get('directories', [])) != expected_dirs:
        raise ValueError('Unexpected private corpus directories')
    files = {}; observed_dirs = set()
    for current, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            path = Path(current) / name; ordinary(path, directory=True)
            observed_dirs.add(path.relative_to(root).as_posix())
        for name in names:
            path = Path(current) / name; ordinary(path)
            files[path.relative_to(root).as_posix()] = path
        if len(files) > 138 or not observed_dirs <= expected_dirs:
            raise ValueError('Extra private corpus files')
    if set(files) != set(expected) or observed_dirs != expected_dirs:
        raise ValueError('Private corpus inventory is not exact')
    for key, path in files.items():
        if path.stat().st_size != expected[key]['length'] or digest(path, 1048576) != expected[key]['sha256']:
            raise ValueError('Private corpus bytes differ from payload inventory')
    return {key: row['sha256'] for key, row in expected.items()}


def require_absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise FileExistsError('Preserve existing production index state')


def safe_failure(phase, error):
    allowed = {'runtime_custody', 'configuration', 'payload_inventory',
               'private_corpus_provision', 'new_index', 'final_verification'}
    kind = re.sub('[^A-Za-z0-9_]', '', type(error).__name__)[:80] or 'Exception'
    code, current = None, error
    for _ in range(5):
        value = getattr(current, 'winerror', None)
        if type(value) is int and 0 <= value <= 0xFFFFFFFF:
            code = value; break
        current = current.__cause__ or current.__context__
        if current is None:
            break
    return {'phase': phase if phase in allowed else 'trusted_preflight', 'error_type': kind, 'winerror': code}


def verify_runtime_binding():
    if (Path(sys.base_prefix).resolve() != BASE or Path(sys._base_executable).resolve() != BASE / 'python.exe'
            or sys.version_info[:3] != (3, 12, 13) or not sys.flags.isolated or not sys.dont_write_bytecode
            or digest(INSTALL / '.venv/pyvenv.cfg') != VENV_SHA or digest(BASE / 'python.exe') != BASE_SHA):
        raise ValueError('Acceptance runtime differs from its reviewed isolated Python binding')


def run(nonce, inventory_sha):
    from cochem_pipeline import knowledge, knowledge_authority, windows as win
    from cochem_pipeline.planning_governance import canonical_authority
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    if Path(__file__).resolve() != ROOT / 'accept-private-knowledge.py' or Path(sys.executable).resolve() != PYTHON:
        raise ValueError('Use only protected acceptance helper and installed interpreter')
    for path in (ROOT, Path(__file__), PYTHON, BASE, BASE / 'python.exe', INSTALL / '.venv/pyvenv.cfg'):
        ordinary_tree(path); win.validate_code_path(path)
    verify_runtime_binding()
    report = {'schema': 'cochem-private-knowledge-system-acceptance/1', 'nonce': nonce,
        'system_sid': win.SYSTEM_SID, 'status': 'UNVERIFIED', 'started_at_unix_ms': int(time.time() * 1000),
        'helper_sha256': digest(Path(__file__)), 'payload_inventory_sha256': inventory_sha,
        'native_model_jobs_executed': 0, 'original_databases_read_or_modified': False,
        'source_databases_imported': False, 'daemon_started': False, 'configuration_modified': False,
        'budgets_modified': False, 'legacy_full_continuity_verified': False,
        'index_created_new': False, 'production_permission_checks_used': True}
    phase = 'runtime_custody'
    with (ROOT / 'knowledge-acceptance.json').open('x', encoding='utf-8') as receipt:
        try:
            for module in (win, knowledge, knowledge_authority):
                path = Path(module.__file__); ordinary_tree(path); win.validate_code_path(path)
                if digest(path) != MODULE_PINS[module.__name__]:
                    raise ValueError('Installed validation implementation changed')
            phase = 'configuration'
            config_file = INSTALL / 'pipeline.json'
            ordinary_tree(config_file); win.validate_code_path(config_file)
            config_hash = digest(config_file)
            if config_hash != CONFIG_SHA:
                raise ValueError('Protected host configuration changed from its reviewed capture')
            config_raw = strict_json(config_file.read_bytes())
            config = knowledge.KnowledgeConfig.from_dict(config_raw['knowledge'])
            expected = {'source_root': str(CORPUS / '.sources'), 'wiki_root': str(CORPUS / 'wiki'),
                        'manifest_path': str(CORPUS / 'v4.1.2_manifest.json'), 'state_root': str(STATE)}
            if (not config.enabled or any(getattr(config, key) != value for key, value in expected.items())
                    or config_raw['private_root'] != str(PRIVATE) or config_raw['max_execution_slots'] != 4):
                raise ValueError('Frozen host knowledge configuration differs')
            report['pipeline_config_sha256'] = config_hash
            config.validate_placement(PRIVATE, config_raw['slot_roots'].values())
            win.validate_private_directory(PRIVATE)
            # Never create, replace, reuse or refresh an existing production index.
            ordinary_tree(STATE, allow_missing=True)
            require_absent(STATE)
            phase = 'payload_inventory'
            inventory_path = ROOT / 'private-install-inventory.json'
            ordinary_tree(inventory_path); win.validate_code_path(inventory_path)
            if digest(inventory_path) != inventory_sha:
                raise ValueError('Payload inventory changed')
            inventory = strict_json(inventory_path.read_bytes())
            ordinary_tree(CORPUS)
            original = verify_inventory(inventory, CORPUS, knowledge._ordinary)
            require_stopped(win)
            phase = 'private_corpus_provision'
            protection = knowledge.provision_knowledge(config)
            phase = 'new_index'
            STATE.mkdir(exist_ok=False)
            win.validate_private_directory(STATE)
            report['index_created_new'] = True
            service = knowledge.KnowledgeService(config)
            try:
                result = service.refresh()
                authority = knowledge_authority.KnowledgeAuthority(service, canonical_authority()).status(force=True)
                with service._reader() as (_, database):
                    integrity = database.execute('PRAGMA integrity_check').fetchone()[0]
                    documents = database.execute('SELECT count(*) FROM documents').fetchone()[0]
                    sections = database.execute('SELECT count(*) FROM fts_index').fetchone()[0]
                _, index_path = service._current()
                source_pins = digest(STATE / 'sources.json')
                actual = verify_inventory(inventory, CORPUS, knowledge._ordinary)
                if (original != actual or source_pins != SOURCE_PINS_SHA or documents != 137
                        or integrity != 'ok' or not result['index_size_sla_met'] or not authority['ready']):
                    raise ValueError('Production knowledge acceptance did not satisfy all evidence gates')
                report.update(documents=documents, sections=sections, corpus_bytes=result['corpus_bytes'],
                    index_bytes=result['index_bytes'], index_size_sla_met=True, index_integrity_check=integrity,
                    corpus_manifest_sha256=digest(CORPUS / 'v4.1.2_manifest.json'), source_pins_sha256=source_pins,
                    index_sha256=digest(index_path, 134217728), source_bytes_preserved=original == actual,
                    corpus_protection=protection, canonical_authority_matches_capture=True,
                    canonical_source=authority['authority_source'],
                    owner_amendment_sources=[row['resolved_source'] for row in authority['owner_amendments']],
                    generation=result['generation'])
            finally:
                service.close()
            phase = 'final_verification'
            if digest(config_file) != config_hash:
                raise ValueError('Protected configuration changed during acceptance')
            require_stopped(win)
            report['status'] = 'PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED'
        except BaseException as error:
            report['status'] = 'KNOWLEDGE_ACCEPTANCE_FAILED'
            report['failure'] = safe_failure(phase, error)
            report['operator_review_required'] = True
        finally:
            report['finished_at_unix_ms'] = int(time.time() * 1000)
            json.dump(report, receipt, sort_keys=True, indent=2)
            receipt.flush(); os.fsync(receipt.fileno())
    return 0 if report['status'] == 'PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED' else 2


def main():
    if len(sys.argv) != 3 or not re.fullmatch('[0-9a-f]{32}', sys.argv[1]) or not re.fullmatch('[0-9a-f]{64}', sys.argv[2]):
        return 3
    try:
        return run(sys.argv[1], sys.argv[2])
    except BaseException:
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
