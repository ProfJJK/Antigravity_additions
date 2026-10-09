"""One diagnosed blank-state resume, under the real production writer lock.

No corpus provisioning, ACL changes, old-state deletion, daemon activation,
provider calls, budget migration, or automatic retry. Existing state/control
evidence survives. Production refresh may clean its own unpublished generation
and temporary pointer files on failure; its behavior is not modified here.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import SimpleNamespace

ROOT = Path(r'C:\Program Files\CoChem\KnowledgeResume4.2.7-windows-20261007-r2')
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2')
OLD_INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312')
ORIGINAL = Path(r'C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006')
DIAGNOSTIC = Path(r'C:\Program Files\CoChem\KnowledgeDiagnostic4.2.7-windows-20261007-a')
CORPUS = Path(r'C:\Program Files\CoChem\Knowledge4.2.7-windows-20261006')
PRIVATE = Path(r'C:\ProgramData\CoChemPipeline427\private')
STATE = PRIVATE / 'knowledge-windows-20261006'
DIAGNOSTIC_SHA = '18ceba4394b92bb6e1d320f49226359902af0084a72195a8139f699594bc0307'
DIAGNOSTIC_NONCE = 'e9f31a03b0d04b8697967407c47e3518'
ORIGINAL_SHA = '2cf8f1f4cafbb0537900af98349f6a3f4faec135a4061dd153f0cce78c1488dd'
CONFIG_SHA = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
MANIFEST_SHA = 'df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'
BASE_SHA = 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
SUPPORT = {
    'diagnose-knowledge-state.py': 'e799d3d932a42af251c5ea752530eb9bcee022196d0c22db60c75e481e67da87',
    'accept-private-knowledge.py': '832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca',
}
PINS = {
    'windows.py': 'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba',
    'knowledge.py': '65e1dc17a5bee38f19e47abb24dd97864fcdcd4bc08fd28ab9199d311869d7b2',
    'operations_policy.py': '06bc54e218596bee4103da96a179f49a9551bae23d0380a31b0f005a53323f86',
    'knowledge_authority.py': 'e7dd99cb0f9a6710d3a03cff2d983a58879fef0b68bd463c0f38607603ba1f27',
}
BASELINE = {
    'state': ('2c540dda:d0000:2ebf70', '7a91710d6b9c800ad902db134b79c9c6b03f90578f7ec651048c19e0237e2bca'),
    'state/writer.lock': ('2c540dda:d0000:2ebf71', '9c885bd1583d736bbc8e32cd8b81d559800e45da71f246556d2638c52279b15d'),
}
# Actual owner-installed r2 bytes; independently verified without state/DB reads.
INSTALL_RECEIPT_SHA = 'd92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4'
PYTHON_SHA = '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'
VENV_SHA = '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate control field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite control field')))


def read_pinned(path, expected, validate, maximum=1048576):
    validate(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or getattr(before, 'st_file_attributes', 0) & 0x400 or before.st_size > maximum):
        raise ValueError('Control file is not bounded ordinary single-link data')
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        finished = os.fstat(stream.fileno())
    if not identity(before) == identity(opened) == identity(finished) == identity(path.lstat()):
        raise ValueError('Control file changed identity')
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Control file pin differs')
    return raw


def load_support(name, win):
    raw = read_pinned(ROOT / name, SUPPORT[name], win.validate_code_path)
    namespace = {'__name__': 'reviewed_' + name.replace('-', '_'), '__file__': str(ROOT / name)}
    exec(compile(raw, str(ROOT / name), 'exec'), namespace)
    return SimpleNamespace(**namespace)


def assert_blank(rows):
    if len(rows) != 2 or {row.get('relative') for row in rows} != set(BASELINE):
        raise ValueError('Diagnosed state is no longer exactly root plus writer lock')
    for row in rows:
        expected = BASELINE[row['relative']]
        if (row.get('state') != 'OBSERVED' or (row.get('file_identity'), row.get('metadata_sha256')) != expected
                or row.get('reparse') is not False or row.get('links') != 1
                or row.get('owner_sid') != 'S-1-5-18' or row.get('null_dacl') is not False
                or row.get('owner_rights_ace') is not False
                or row.get('directory') is not (row['relative'] == 'state')
                or row.get('bytes') != (0 if row['relative'] == 'state' else 1)):
            raise ValueError('Diagnosed state identity, metadata or custody changed')
    return {row['relative']: row for row in rows}


def blank_state(diag, win, *, read_lock=True):
    win.validate_private_directory(STATE)
    rows = diag.inspect_state(STATE, lambda path: diag.metadata(path, win), maximum=8)
    result = assert_blank(rows)
    if read_lock:
        # Exactly one authorized byte. The real msvcrt lock prevents reading it
        # through another handle once acquired, so the in-lock recheck uses its
        # exact previously observed file identity, length and write metadata.
        read_pinned(STATE / 'writer.lock', hashlib.sha256(b'0').hexdigest(), win.validate_private_path, 1)
        assert_blank(diag.inspect_state(STATE, lambda path: diag.metadata(path, win), maximum=8))
    return result


def guarded_service_type(base, check):
    class ResumeService(base):
        @contextmanager
        def _write_lock(self):
            with super()._write_lock():
                if getattr(self, '_resume_wrote_once', False):
                    raise ValueError('A resume may index only once')
                check()  # After the real production cross-process lock, before yield.
                self._resume_wrote_once = True
                yield
    return ResumeService


def assert_preserved(before, after_root, after_lock):
    original = before['state']
    ignored = {'relative', 'state', 'metadata_sha256', 'written_filetime'}
    if any(after_root.get(key) != value for key, value in original.items() if key not in ignored):
        raise ValueError('Existing state root identity or ACL changed')
    if (after_lock.get('file_identity'), after_lock.get('metadata_sha256')) != BASELINE['state/writer.lock']:
        raise ValueError('Existing writer lock changed')


def assert_config_delta(old, new):
    old = strict_json(old); new = strict_json(new)
    if 'workspace_subdirectory' in old['ramdisk'] or new['ramdisk'].pop('workspace_subdirectory', None) != 'CoChem427-windows-20261007' or old != new:
        raise ValueError('Configuration differs beyond the reviewed RAM workspace field')


def check_install_receipt(value):
    expected = {'schema': 'cochem-stopped-runtime-update/1', 'mode': 'FRESH_STOPPED_RUNTIME_READY',
                'target_root': str(INSTALL), 'source_manifest_sha256': MANIFEST_SHA, 'source_files': 166,
                'configuration_sha256': CONFIG_SHA, 'only_config_change': 'ramdisk.workspace_subdirectory',
                'accounts_provisioned': 0, 'tasks_changed': 0, 'credentials_modified': False,
                'databases_modified': False, 'ram_modified': False, 'pipeline_started': False, 'activation_ready': False}
    if (not isinstance(value, dict) or any(type(value.get(k)) is not type(v) or value.get(k) != v for k, v in expected.items())
            or value.get('holds') != [] or value.get('verification', {}).get('revision', {}).get('verified') is not True
            or value.get('verification', {}).get('configuration_parsed') is not True):
        raise ValueError('Actual r2 installation receipt lacks complete binding')


def verify_source_manifest(value, read):
    if (value.get('schema') != 'cochem-stopped-runtime-source/1' or value.get('target_root') != str(INSTALL)
            or value.get('complete_source_freeze') is not True or not isinstance(value.get('files'), list)
            or len(value['files']) != 166):
        raise ValueError('Frozen source manifest binding differs')
    seen = set()
    for row in value['files']:
        key = row.get('relative')
        if (not isinstance(key, str) or '\\' in key or ':' in key or key.startswith('/')
                or any(part in ('', '.', '..') for part in key.split('/')) or key.casefold() in seen
                or not (key.startswith('src/') or key in ('README.md', 'pyproject.toml', 'uv.lock'))
                or not re.fullmatch('[a-f0-9]{64}', str(row.get('sha256')))
                or type(row.get('length')) is not int or not 0 <= row['length'] <= 16777216):
            raise ValueError('Unsafe frozen source manifest record')
        seen.add(key.casefold())
        if len(read(INSTALL / 'source' / key, row['sha256'], 16777216)) != row['length']:
            raise ValueError('Frozen source file length differs')
    if not {'pyproject.toml', 'uv.lock', 'readme.md'} <= seen:
        raise ValueError('Frozen source manifest omits project dependency files')


def assert_revision_matches(actual, installed_receipt):
    expected = installed_receipt['verification']['revision']
    if actual != expected or actual.get('verified') is not True:
        raise ValueError('Installed r2 revision differs from actual pinned installation')


def diagnostic_task(win):
    raw = win._powershell(r'''
      $s=New-Object -ComObject 'Schedule.Service';$s.Connect();$t=$s.GetFolder('\').GetTask('CoChem-4.2.7-KnowledgeDiagnostic-20261007-a');
      $d=$t.Definition;$a=$d.Actions.Item(1);
      if($t.State -notin @(1,3) -or $t.GetInstances(0).Count -ne 0 -or $t.LastTaskResult -ne 0 -or
         $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
         $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or $a.Type -ne 0 -or $a.Path -cne $data.python -or
         $a.Arguments -cne $data.arguments -or $a.WorkingDirectory -cne $data.root){throw 'Preserved diagnostic task binding differs.'}
      'VERIFIED'
    ''', {'python': str(OLD_INSTALL / '.venv/Scripts/python.exe'), 'root': str(DIAGNOSTIC),
          'arguments': f'-I -B "{DIAGNOSTIC / "diagnose-knowledge-state.py"}" {DIAGNOSTIC_NONCE} {ORIGINAL_SHA}'})
    if raw.strip() != 'VERIFIED':
        raise ValueError('Preserved diagnostic task is unverified')


def run(nonce):
    if any(not isinstance(pin, str) or not re.fullmatch('[a-f0-9]{64}', pin) for pin in (INSTALL_RECEIPT_SHA, PYTHON_SHA, VENV_SHA)):
        raise ValueError('Actual r2 installation pins have not been bound; draft cannot run')
    from cochem_pipeline import windows as win, knowledge, knowledge_authority
    from cochem_pipeline.planning_governance import canonical_authority
    from cochem_pipeline.deployment_revision import verify_installed_revision
    win.require_system()
    if (Path(__file__).resolve() != ROOT / 'resume-private-knowledge.py' or Path(sys.executable).resolve() != INSTALL / '.venv/Scripts/python.exe'
            or Path(sys.base_prefix).resolve() != BASE or Path(sys._base_executable).resolve() != BASE / 'python.exe'
            or sys.version_info[:3] != (3, 12, 13) or not sys.flags.isolated or not sys.dont_write_bytecode):
        raise ValueError('Use only the reviewed protected r2 runtime')
    win.validate_private_directory(ROOT); win.validate_code_path(__file__)
    diag = load_support('diagnose-knowledge-state.py', win)
    acceptance = load_support('accept-private-knowledge.py', win)
    report = {'schema': 'cochem-private-knowledge-resume/1', 'nonce': nonce, 'system_sid': win.SYSTEM_SID,
        'status': 'UNVERIFIED', 'started_at_unix_ms': int(time.time() * 1000),
        'helper_sha256': acceptance.digest(Path(__file__)), 'runtime_root': str(INSTALL),
        'install_receipt_sha256': INSTALL_RECEIPT_SHA, 'diagnostic_receipt_sha256': DIAGNOSTIC_SHA,
        'original_receipt_sha256': ORIGINAL_SHA, 'native_model_jobs_executed': 0,
        'existing_acl_modified': False, 'corpus_reprovisioned': False, 'old_tasks_modified_or_run': False,
        'configuration_modified': False, 'budgets_modified': False, 'daemon_started': False,
        'legacy_full_continuity_verified': False, 'activation_ready': False, 'index_resume_started': False}
    phase = 'runtime_binding'
    with (ROOT / 'resume-acceptance.json').open('x', encoding='utf-8') as output:
        try:
            for path, pin, limit in ((INSTALL / '.venv/pyvenv.cfg', VENV_SHA, 8192),
                    (INSTALL / '.venv/Scripts/python.exe', PYTHON_SHA, 1048576),
                    (BASE / 'python.exe', BASE_SHA, 1048576),
                    (INSTALL / 'source-manifest.json', MANIFEST_SHA, 1048576)):
                read_pinned(path, pin, win.validate_code_path, limit)
            for name, pin in PINS.items():
                read_pinned(Path(win.__file__).parent / name, pin, win.validate_code_path)
            installed_receipt = strict_json(read_pinned(INSTALL / 'install-after.json', INSTALL_RECEIPT_SHA, win.validate_code_path))
            check_install_receipt(installed_receipt)
            manifest = strict_json(read_pinned(INSTALL / 'source-manifest.json', MANIFEST_SHA, win.validate_code_path))
            verify_source_manifest(manifest, lambda path, pin, bound: read_pinned(path, pin, win.validate_code_path, bound))
            revision = verify_installed_revision(INSTALL / 'source', INSTALL / '.venv/Lib/site-packages', INSTALL / 'source')
            assert_revision_matches(revision, installed_receipt)
            report.update(module_pins=PINS, revision=revision, source_manifest_sha256=MANIFEST_SHA,
                          source_files_verified=166, pipeline_config_sha256=CONFIG_SHA,
                          payload_inventory_sha256=diag.INVENTORY)
            phase = 'preserved_evidence'
            original = strict_json(read_pinned(ORIGINAL / 'knowledge-acceptance.json', ORIGINAL_SHA, win.validate_code_path, 32768))
            diag.check_original(original); diag.task_evidence(win); diagnostic_task(win)
            diagnostic = strict_json(read_pinned(DIAGNOSTIC / 'diagnostic.json', DIAGNOSTIC_SHA, win.validate_code_path, 131072))
            if (diagnostic.get('nonce') != DIAGNOSTIC_NONCE or diagnostic.get('status') != 'METADATA_DIAGNOSTIC_COMPLETE'
                    or diagnostic.get('original_receipt_sha256') != ORIGINAL_SHA):
                raise ValueError('Preserved diagnostic differs')
            assert_blank(diagnostic['state_entries'])
            phase = 'configuration'
            old_raw = read_pinned(OLD_INSTALL / 'pipeline.json', acceptance.CONFIG_SHA, win.validate_code_path)
            new_raw = read_pinned(INSTALL / 'pipeline.json', CONFIG_SHA, win.validate_code_path)
            assert_config_delta(old_raw, new_raw)
            config_raw = strict_json(new_raw); config = knowledge.KnowledgeConfig.from_dict(config_raw['knowledge'])
            expected = {'source_root': str(CORPUS / '.sources'), 'wiki_root': str(CORPUS / 'wiki'),
                        'manifest_path': str(CORPUS / 'v4.1.2_manifest.json'), 'state_root': str(STATE)}
            if not config.enabled or any(getattr(config, k) != v for k, v in expected.items()) or config_raw['private_root'] != str(PRIVATE) or config_raw['max_execution_slots'] != 4:
                raise ValueError('Frozen knowledge configuration differs')
            config.validate_placement(PRIVATE, config_raw['slot_roots'].values())
            phase = 'corpus_verification'
            inventory = strict_json(read_pinned(ORIGINAL / 'private-install-inventory.json', diag.INVENTORY, win.validate_code_path))
            original_bytes = acceptance.verify_inventory(inventory, CORPUS, knowledge._ordinary)
            win.validate_private_directory(PRIVATE); win.validate_private_directory(CORPUS)
            report['daemons_before'] = diag.stopped_daemons(win)
            phase = 'blank_state'
            before = blank_state(diag, win)
            service_type = guarded_service_type(knowledge.KnowledgeService, lambda: blank_state(diag, win, read_lock=False))
            phase = 'index_resume'
            service = service_type(config)
            try:
                report['index_resume_started'] = True
                result = service.refresh()
                authority = knowledge_authority.KnowledgeAuthority(service, canonical_authority()).status(force=True)
                with service._reader() as (_, db):
                    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
                    documents = db.execute('SELECT count(*) FROM documents').fetchone()[0]
                    sections = db.execute('SELECT count(*) FROM fts_index').fetchone()[0]
                _, index_path = service._current()
                actual_bytes = acceptance.verify_inventory(inventory, CORPUS, knowledge._ordinary)
                pins = acceptance.digest(STATE / 'sources.json')
                generation = result['generation']
                if (not re.fullmatch('g-[0-9a-f]{32}', generation) or
                        {p.name for p in STATE.iterdir()} != {'writer.lock', 'current.json', 'sources.json', generation} or
                        {p.name for p in (STATE / generation).iterdir()} != {'knowledge_index.db'}):
                    raise ValueError('Unexpected post-resume state shape')
                if original_bytes != actual_bytes or pins != acceptance.SOURCE_PINS_SHA or documents != 137 or sections != 1708 or integrity != 'ok' or not result['index_size_sla_met'] or not authority['ready']:
                    raise ValueError('Knowledge acceptance gates did not all pass')
                report.update(documents=documents, sections=sections, corpus_files=len(actual_bytes),
                    corpus_bytes=result['corpus_bytes'], index_bytes=result['index_bytes'], index_ratio=result['index_ratio'],
                    corpus_manifest_sha256=acceptance.MANIFEST_SHA, source_pins_sha256=pins,
                    index_sha256=acceptance.digest(index_path, 134217728), index_integrity_check=integrity,
                    index_size_sla_met=True, source_bytes_preserved=True, canonical_authority_matches_capture=True,
                    canonical_source=authority['authority_source'], generation=generation,
                    owner_amendment_sources=[row['resolved_source'] for row in authority['owner_amendments']])
            finally:
                service.close()
            phase = 'final_verification'
            assert_preserved(before, diag.metadata(STATE, win), diag.metadata(STATE / 'writer.lock', win))
            read_pinned(STATE / 'writer.lock', hashlib.sha256(b'0').hexdigest(), win.validate_private_path, 1)
            read_pinned(INSTALL / 'pipeline.json', CONFIG_SHA, win.validate_code_path)
            diag.task_evidence(win); diagnostic_task(win)
            report['daemons_after'] = diag.stopped_daemons(win)
            report.update(status='PRESERVED_CORPUS_AND_RESUMED_INDEX_VERIFIED', original_root_and_lock_preserved=True)
        except BaseException as error:
            report.update(status='KNOWLEDGE_RESUME_HELD', failure={'phase': phase, **diag.safe_error(error)}, operator_review_required=True)
        finally:
            report['finished_at_unix_ms'] = int(time.time() * 1000)
            json.dump(report, output, sort_keys=True, indent=2); output.flush(); os.fsync(output.fileno())
    return 0 if report['status'] == 'PRESERVED_CORPUS_AND_RESUMED_INDEX_VERIFIED' else 2


if __name__ == '__main__':
    if len(sys.argv) != 2 or not re.fullmatch('[a-f0-9]{32}', sys.argv[1]):
        raise SystemExit(3)
    try:
        raise SystemExit(run(sys.argv[1]))
    except Exception:
        raise SystemExit(3)
