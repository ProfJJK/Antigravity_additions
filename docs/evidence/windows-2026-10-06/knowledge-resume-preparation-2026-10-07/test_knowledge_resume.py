"""Inert regression tests; no SYSTEM task, production state, index or Apply."""
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('resume', ROOT / 'resume-private-knowledge.py')
resume = importlib.util.module_from_spec(spec); spec.loader.exec_module(resume)
PS = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def rows():
    return [{'relative': name, 'state': 'OBSERVED', 'file_identity': identity,
             'metadata_sha256': sha, 'reparse': False, 'links': 1, 'owner_sid': 'S-1-5-18',
             'null_dacl': False, 'owner_rights_ace': False, 'directory': name == 'state',
             'bytes': 0 if name == 'state' else 1, 'written_filetime': 10,
             'aces': [{'sid': 'S-1-5-18', 'mask': 2032127}]} for name, (identity, sha) in resume.BASELINE.items()]


def test_exact_diagnosed_shape_passes():
    assert set(resume.assert_blank(rows())) == set(resume.BASELINE)


@pytest.mark.parametrize('field,value', [('state', 'INACCESSIBLE_OR_ERROR'), ('file_identity', 'other'),
    ('metadata_sha256', 'f'*64), ('reparse', True), ('links', 2), ('owner_sid', 'S-1-5-32-544'),
    ('null_dacl', True), ('owner_rights_ace', True), ('directory', False), ('bytes', 1)])
def test_changed_root_refused(field, value):
    value_rows = rows(); value_rows[0][field] = value
    with pytest.raises(ValueError): resume.assert_blank(value_rows)


@pytest.mark.parametrize('extra', ['current.json', 'sources.json', 'g-'+'a'*32, 'unknown-private-name'])
def test_any_populated_or_unknown_state_refused(extra):
    value_rows = rows() + [{'relative': 'state/'+extra}]
    with pytest.raises(ValueError): resume.assert_blank(value_rows)


def test_root_write_timestamp_changes_allowed_but_lock_changes_refused():
    before = resume.assert_blank(rows())
    root = dict(before['state']); root['written_filetime'] += 1; root['metadata_sha256'] = 'new'
    lock = dict(before['state/writer.lock'])
    resume.assert_preserved(before, root, lock)
    root['owner_sid'] = 'other'
    with pytest.raises(ValueError): resume.assert_preserved(before, root, lock)
    root['owner_sid'] = 'S-1-5-18'; lock['metadata_sha256'] = 'new'
    with pytest.raises(ValueError): resume.assert_preserved(before, root, lock)


def test_one_byte_lock_is_bounded_and_exact(tmp_path):
    path = tmp_path/'writer.lock'; path.write_bytes(b'0')
    sha = hashlib.sha256(b'0').hexdigest()
    assert resume.read_pinned(path, sha, lambda _: None, 1) == b'0'
    for raw in (b'', b'1', b'00'):
        path.write_bytes(raw)
        with pytest.raises(ValueError): resume.read_pinned(path, sha, lambda _: None, 1)


def test_actual_hardlinked_lock_refused(tmp_path):
    path = tmp_path/'writer.lock'; path.write_bytes(b'0'); os.link(path, tmp_path/'alias')
    with pytest.raises(ValueError): resume.read_pinned(path, hashlib.sha256(b'0').hexdigest(), lambda _: None, 1)


def test_control_duplicate_fields_refused():
    with pytest.raises(ValueError): resume.strict_json('{"verified":true,"verified":false}')


def test_real_production_lock_precedes_baseline_and_refuses_second_resume(tmp_path, monkeypatch):
    sys.path.insert(0, r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\src')
    from cochem_pipeline import knowledge
    from cochem_pipeline import windows as win
    diag_spec = importlib.util.spec_from_file_location('real_diag', ROOT/'diagnose-knowledge-state.py')
    diag = importlib.util.module_from_spec(diag_spec); diag_spec.loader.exec_module(diag)
    # Explicit disposable ordinary-user permission fixture only; real Windows
    # production locking remains intact and no constructor/refresh is invoked.
    monkeypatch.setattr(knowledge, '_protected', lambda *a, **k: None)
    path = tmp_path/'writer.lock'; path.write_bytes(b'0')
    initial_metadata = diag.metadata(path, win)
    import msvcrt
    sequence = []
    def check():
        # Actual Win32 READ_CONTROL|FILE_READ_ATTRIBUTES succeeds while the
        # byte is exclusively locked; no lock content read is attempted here.
        assert diag.metadata(path, win) == initial_metadata
        with path.open('r+b') as other:
            with pytest.raises(OSError): msvcrt.locking(other.fileno(), msvcrt.LK_NBLCK, 1)
        with path.open('rb') as other:
            with pytest.raises(OSError): other.read(1)
        sequence.append('checked-under-real-lock')
    cls = resume.guarded_service_type(knowledge.KnowledgeService, check)
    service = cls.__new__(cls); service.state = tmp_path; service._writer = threading.RLock()
    with service._write_lock(): sequence.append('body')
    assert sequence == ['checked-under-real-lock', 'body']
    with pytest.raises(ValueError, match='only once'):
        with service._write_lock(): raise AssertionError('Second resume body reached')
    assert path.read_bytes() == b'0'


def test_concurrent_writer_population_between_precheck_and_lock_is_refused():
    lock_held = False; entered_body = False
    class Base:
        @contextmanager
        def _write_lock(self):
            nonlocal lock_held
            lock_held = True
            try: yield
            finally: lock_held = False
    captured = rows(); resume.assert_blank(captured)
    captured.append({'relative': 'state/current.json'})
    def check():
        assert lock_held
        resume.assert_blank(captured)
    service = resume.guarded_service_type(Base, check)()
    with pytest.raises(ValueError):
        with service._write_lock(): entered_body = True
    assert not entered_body and not lock_held


def test_revision_verified_boolean_does_not_accept_matching_source_and_installed_drift():
    revision = {'verified': True, 'files': 109, 'source_sha256': 'a'*64, 'read_only': True}
    installed = {'verification': {'revision': copy.deepcopy(revision)}}
    resume.assert_revision_matches(revision, installed)
    # Both source and installed package can agree on the same changed bytes;
    # the frozen install receipt must still refuse their new aggregate.
    revision['source_sha256'] = 'b'*64
    with pytest.raises(ValueError): resume.assert_revision_matches(revision, installed)


def test_all_166_actual_manifest_records_bind_dependencies_without_executing_them():
    path = resume.INSTALL/'source-manifest.json'
    raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == resume.MANIFEST_SHA
    checked = []
    def read(path, pin, maximum):
        result = resume.read_pinned(path, pin, lambda _: None, maximum); checked.append(path.name); return result
    resume.verify_source_manifest(resume.strict_json(raw), read)
    assert len(checked) == 166 and 'uv.lock' in checked and 'pyproject.toml' in checked


@pytest.mark.parametrize('change', ['hash', 'traversal', 'duplicate', 'count'])
def test_source_manifest_drift_or_unsafe_paths_refused(change):
    value = resume.strict_json((resume.INSTALL/'source-manifest.json').read_bytes())
    if change == 'hash': value['files'][0]['sha256'] = '0'*64
    elif change == 'traversal': value['files'][0]['relative'] = '../private'
    elif change == 'duplicate': value['files'][1]['relative'] = value['files'][0]['relative']
    else: value['files'].pop()
    with pytest.raises(ValueError):
        resume.verify_source_manifest(value, lambda p, pin, bound: resume.read_pinned(p, pin, lambda _: None, bound))


def test_actual_install_receipt_is_bound_and_boolean_types_are_strict():
    value = resume.strict_json((resume.INSTALL/'install-after.json').read_bytes())
    resume.check_install_receipt(value)
    value['credentials_modified'] = 0
    with pytest.raises(ValueError): resume.check_install_receipt(value)


def test_actual_configs_only_differ_by_authorized_ram_workspace():
    old = (resume.OLD_INSTALL/'pipeline.json').read_bytes(); new = (resume.INSTALL/'pipeline.json').read_bytes()
    resume.assert_config_delta(old, new)
    value = resume.strict_json(new); value['max_execution_slots'] = 5
    with pytest.raises(ValueError): resume.assert_config_delta(old, json.dumps(value))


def powershell(tmp_path, functions, body):
    script = tmp_path/'fixture.ps1'
    quoted = str(ROOT/'resume-private-knowledge.ps1').replace("'", "''")
    names = ','.join("'"+name+"'" for name in functions)
    prefix = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{quoted}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Wrapper parse error'}}
foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{if($f.Name -in @({names})){{. ([scriptblock]::Create($f.Extent.Text))}}}}
"""
    script.write_text(prefix+body, encoding='utf-8')
    return subprocess.run([str(PS), '-NoProfile', '-File', str(script)], capture_output=True, text=True, timeout=20)


def test_ps51_actual_uv_config_accepts_and_old_or_changed_format_refuses(tmp_path):
    body = r"""
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$cfg=[IO.File]::ReadAllText('C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\.venv\pyvenv.cfg')
Assert-R2VenvBinding $cfg
$badConfigs=@();$badConfigs+=($cfg.Replace('version_info','version'));$badConfigs+=($cfg.Replace($basePythonRoot,'C:\untrusted'));$badConfigs+=($cfg.Replace('false','true'));$badConfigs+=($cfg+"`nuv = 0.12.17")
foreach($bad in $badConfigs){
  $refused=$false;try{Assert-R2VenvBinding $bad}catch{$refused=$true};if(-not $refused){throw 'Bad binding accepted'}
};'PASS'
"""
    value = powershell(tmp_path, ['Assert-R2VenvBinding'], body)
    assert value.returncode == 0, value.stderr


def test_ps51_read_text_keeps_held_stream_open(tmp_path):
    path = str(tmp_path/'data.json').replace("'", "''")
    body = f"""[IO.File]::WriteAllText('{path}','{{}}')
$stream=[IO.File]::Open('{path}',[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{{if((Read-HeldText $stream) -cne '{{}}'){{throw 'Text differs'}};[GC]::Collect();[GC]::WaitForPendingFinalizers();$stream.Position=0;if($stream.ReadByte() -ne 123){{throw 'Stream closed'}}}}finally{{$stream.Dispose()}}
'PASS'
"""
    value = powershell(tmp_path, ['Read-HeldText'], body)
    assert value.returncode == 0, value.stderr


def test_support_main_bodies_are_never_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, 'ROOT', tmp_path)
    raw = b"VALUE=42\nif __name__=='__main__':raise AssertionError('ran main')\n"
    (tmp_path/'test.py').write_bytes(raw)
    monkeypatch.setitem(resume.SUPPORT, 'test.py', hashlib.sha256(raw).hexdigest())
    class Win:
        validate_code_path = staticmethod(lambda _: None)
    assert resume.load_support('test.py', Win).VALUE == 42


def test_wrapper_and_python_source_pin_matches():
    source = (ROOT/'resume-private-knowledge.py').read_bytes()
    wrapper = (ROOT/'resume-private-knowledge.ps1').read_text()
    assert "$sourceHash='"+hashlib.sha256(source).hexdigest()+"'" in wrapper
    assert '$folder.RegisterTaskDefinition($taskName,$definition,2,' in wrapper
    assert "'Assert-EffectivePrivateReceipt'" in wrapper
    assert 'Remove-Item' not in wrapper and '-Force' not in wrapper.replace(' -Force).Length', '')


def test_ps51_preserved_task_state_and_exact_action_are_required(tmp_path):
    body = r"""
$oldInstall='C:\old';$root='C:\evidence';$argsText='abc xyz'
$action=[pscustomobject]@{Type=0;Path='C:\old\.venv\Scripts\python.exe';Arguments='-I -B "C:\evidence\helper.py" abc xyz';WorkingDirectory='C:\evidence'}
$actions=[pscustomobject]@{Count=1;Action=$action};$actions|Add-Member ScriptMethod Item {param($n)$this.Action}
$task=[pscustomobject]@{State=3;LastTaskResult=2;Instances=0;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
$task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$this.Instances}}
Assert-PreservedTask $task $root 'helper.py' $argsText 2
foreach($field in @('State','LastTaskResult','Instances')){
 $saved=$task.$field;$task.$field=99;$refused=$false
 try{Assert-PreservedTask $task $root 'helper.py' $argsText 2}catch{$refused=$true}
 if(-not $refused){throw 'Unsafe task accepted'};$task.$field=$saved
}
$task.Definition.Actions.Action.Arguments+=' changed';$refused=$false
try{Assert-PreservedTask $task $root 'helper.py' $argsText 2}catch{$refused=$true};if(-not $refused){throw 'Changed action accepted'}
$refused=$false;try{Assert-PreservedTask $null $root 'helper.py' $argsText 2}catch{$refused=$true};if(-not $refused){throw 'Missing original accepted'}
'PASS'
"""
    value = powershell(tmp_path, ['Assert-PreservedTask'], body)
    assert value.returncode == 0, value.stderr


def test_ps51_success_receipt_nonce_and_failed_receipt_refusal(tmp_path):
    body = r"""
$sourceHash='a';$installRoot='r2';$receiptHash='b';$originalHash='c';$diagnosticHash='d'
$r=[pscustomobject]@{schema='cochem-private-knowledge-resume/1';nonce='n';system_sid='S-1-5-18';helper_sha256='a';runtime_root='r2';install_receipt_sha256='b';original_receipt_sha256='c';diagnostic_receipt_sha256='d';status='PRESERVED_CORPUS_AND_RESUMED_INDEX_VERIFIED';original_root_and_lock_preserved=$true;documents=137;corpus_files=138;index_integrity_check='ok';source_bytes_preserved=$true;canonical_authority_matches_capture=$true;existing_acl_modified=$false;corpus_reprovisioned=$false;old_tasks_modified_or_run=$false;budgets_modified=$false;activation_ready=$false}
Assert-ResumeReceipt $r 'n'
foreach($field in @('nonce','status','install_receipt_sha256')){
 $saved=$r.$field;$r.$field='changed';$refused=$false;try{Assert-ResumeReceipt $r 'n'}catch{$refused=$true}
 if(-not $refused){throw 'Bad receipt accepted'};$r.$field=$saved
}
$refused=$false;try{Assert-ResumeReceipt $null 'n'}catch{$refused=$true};if(-not $refused){throw 'Missing receipt accepted'}
'PASS'
"""
    value = powershell(tmp_path, ['Assert-ResumeReceipt'], body)
    assert value.returncode == 0, value.stderr


def test_ps51_failure_receipt_only_returns_sanitized_bound_fields(tmp_path):
    body = r"""
$sourceHash='a';$installRoot='r2';$receiptHash='b';$originalHash='c';$diagnosticHash='d'
$r=[pscustomobject]@{schema='cochem-private-knowledge-resume/1';nonce='n';system_sid='S-1-5-18';helper_sha256='a';runtime_root='r2';install_receipt_sha256='b';original_receipt_sha256='c';diagnostic_receipt_sha256='d';status='KNOWLEDGE_RESUME_HELD';secret='DO-NOT-PUBLISH';failure=[pscustomobject]@{phase='blank_state';error_type='WindowsIsolationError';winerror=5;message='DO-NOT-PUBLISH'}}
$safe=Get-ResumeFailure $r 'n';$raw=$safe|ConvertTo-Json -Compress
if($raw.Contains('DO-NOT-PUBLISH') -or $safe.Count -ne 3){throw 'Failure details leaked'}
foreach($field in @('nonce','status','install_receipt_sha256')){
 $saved=$r.$field;$r.$field='changed';$refused=$false;try{$null=Get-ResumeFailure $r 'n'}catch{$refused=$true}
 if(-not $refused){throw 'Bad failure binding accepted'};$r.$field=$saved
}
$r.failure.error_type='secret path C:\bad';$refused=$false;try{$null=Get-ResumeFailure $r 'n'}catch{$refused=$true};if(-not $refused){throw 'Raw exception accepted'}
'PASS'
"""
    value = powershell(tmp_path, ['Get-ResumeFailure'], body)
    assert value.returncode == 0, value.stderr
