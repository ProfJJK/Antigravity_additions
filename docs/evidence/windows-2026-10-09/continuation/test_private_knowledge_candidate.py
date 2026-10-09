"""Inert parser/custody/scheduler fixtures; no SYSTEM or deployed-state actions."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / 'install-private-knowledge-candidate.ps1'
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0, str(REPO / 'src'))
from cochem_pipeline.knowledge import _ordinary

spec = importlib.util.spec_from_file_location('draft_private_knowledge', HERE / 'accept-private-knowledge.py')
accept = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accept)
CANDIDATE = Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\knowledge-continuation-candidate-20261007T053341Z')


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def ps(body, helpers=False):
    code = "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"
    scripts = [SCRIPT]
    if helpers:
        scripts.insert(0, REPO / 'scripts/stage_aetherdesk_427_payloads.ps1')
    for path in scripts:
        code += f"""$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile({quote(path)},[ref]$tokens,[ref]$errors);
        if($errors.Count){{throw ($errors|Out-String)}};
        foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
        """
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code + body], text=True, capture_output=True, timeout=30)


def test_actual_private_candidate_exact_bytes_and_catalog_count():
    raw = (CANDIDATE / 'custody/private-install-inventory.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892'
    result = accept.verify_inventory(accept.strict_json(raw), CANDIDATE / 'corpus', _ordinary)
    assert len(result) == 138
    assert result['v4.1.2_manifest.json'] == accept.MANIFEST_SHA
    assert sum(key.startswith('.sources/legacy/sha256/') for key in result) == 126
    assert len(accept.strict_json((CANDIDATE / 'corpus/v4.1.2_manifest.json').read_bytes())['documents']) == 137


@pytest.fixture
def payload(tmp_path):
    dirs = ['.sources', '.sources/legacy', '.sources/legacy/sha256', 'wiki']
    for directory in dirs:
        (tmp_path / directory).mkdir()
    rows = []
    for key in ['v4.1.2_manifest.json', *(f'wiki/{number}.md' for number in range(137))]:
        raw = b'# synthetic inert fixture\n'
        (tmp_path / key).write_bytes(raw)
        rows.append({'relative': key, 'length': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return tmp_path, dict(schema='cochem-private-knowledge-payload/1', documents=137,
        corpus_manifest_sha256=accept.MANIFEST_SHA, target_root=str(accept.CORPUS), target_state=str(accept.STATE),
        directories=dirs, files=rows)


@pytest.mark.parametrize('change', ['extra-file', 'missing-file', 'modified-byte', 'extra-directory', 'symlink', 'hardlink', 'duplicate', 'traversal', 'wrong-target', 'wrong-count'])
def test_payload_refuses_changes_and_links(payload, change):
    root, inventory = payload
    assert len(accept.verify_inventory(inventory, root, _ordinary)) == 138
    if change == 'extra-file':
        (root / 'wiki/extra.md').write_bytes(b'extra')
    elif change == 'missing-file':
        (root / 'wiki/0.md').unlink()
    elif change == 'modified-byte':
        (root / 'wiki/0.md').write_bytes(b'# synthetic inert fixturE\n')
    elif change == 'extra-directory':
        (root / 'wiki/extra').mkdir()
    elif change == 'symlink':
        # Inject the OS-reparse metadata equivalent without requiring link privilege.
        def reject(path, **kwargs):
            if path.name == '0.md':
                raise ValueError('Reparse fixture')
            return _ordinary(path, **kwargs)
        with pytest.raises(ValueError):
            accept.verify_inventory(inventory, root, reject)
        return
    elif change == 'hardlink':
        os.link(root / 'wiki/0.md', root.parent / 'external-fixture-link.md')
    elif change == 'duplicate':
        inventory['files'][1] = copy.deepcopy(inventory['files'][2])
    elif change == 'traversal':
        inventory['files'][1]['relative'] = 'wiki/../escape.md'
    elif change == 'wrong-target':
        inventory['target_state'] = 'C:\\different'
    elif change == 'wrong-count':
        inventory['documents'] = 86
    with pytest.raises((ValueError, OSError)):
        accept.verify_inventory(inventory, root, _ordinary)


@pytest.mark.parametrize('raw', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_duplicate_and_nonfinite_json_refused(raw):
    with pytest.raises(ValueError):
        accept.strict_json(raw)


def test_state_missing_only_not_permissions_or_existing(tmp_path):
    accept.require_absent(tmp_path / 'absent')
    with pytest.raises(FileExistsError):
        accept.require_absent(tmp_path)
    class Inaccessible:
        def lstat(self):
            raise PermissionError('private path must not leak')
    with pytest.raises(PermissionError):
        accept.require_absent(Inaccessible())


def test_safe_failure_cannot_leak_private_path_and_preserves_error_code():
    error = OSError('C:\\private\\sensitive-title.md')
    error.winerror = 5
    wrapped = RuntimeError('private content')
    wrapped.__cause__ = error
    result = accept.safe_failure('new_index', wrapped)
    assert result == {'phase': 'new_index', 'error_type': 'RuntimeError', 'winerror': 5}
    assert 'private' not in json.dumps(result)
    assert accept.safe_failure('C:\\private\\sensitive.md', error)['phase'] == 'trusted_preflight'


def test_installed_config_pin_matches_readonly_capture():
    assert accept.digest(accept.INSTALL / 'pipeline.json') == accept.CONFIG_SHA


@pytest.mark.parametrize('enabled,state,instances,success', [(False, 1, 0, True), (False, 3, 0, True),
    (True, 3, 0, False), (False, 0, 0, False), (False, 2, 0, False), (False, 4, 0, False), (False, 3, 1, False)])
def test_both_stopped_checks_reject_unknown_or_active_actual_daemons(enabled, state, instances, success):
    fixture = f"""$script:fakeTask=[pscustomobject]@{{Enabled=${str(enabled).lower()};State={state}}};
    $script:fakeTask|Add-Member ScriptMethod GetInstances {{param($flags) [pscustomobject]@{{Count={instances}}}}};
    $script:seen=@();$folder=[pscustomobject]@{{}};$folder|Add-Member ScriptMethod GetTask {{param($name) $script:seen+=$name;$script:fakeTask}};
    """
    wrapper = ps(fixture + "Assert-StoppedDaemons;if('CoChem-4.2.7-Warden' -notin $script:seen -or 'CoChem-4.2.7-Supervisor' -notin $script:seen){throw 'Actual task omitted'}")
    assert (wrapper.returncode == 0) is success, wrapper.stderr
    captured = []
    accept.require_stopped(SimpleNamespace(_powershell=lambda body, names: captured.append((body, names))))
    code, names = captured[0]
    # Replace COM construction only; exercise exact production PowerShell guard.
    code = code[code.index('foreach ($name in $data)'):]
    native = ps(fixture + '$data=' + quote(json.dumps(names)) + '|ConvertFrom-Json;' + code)
    assert (native.returncode == 0) is success, native.stderr


@pytest.mark.parametrize('hresult,success', [(-2147024894, True), (-2147024891, False), (-2147216625, False), (-2147216629, False)])
def test_task_lookup_accepts_only_exact_absence(hresult, success):
    result = ps(f"$folder=[pscustomobject]@{{}};$folder|Add-Member ScriptMethod GetTask {{param($n) throw [Runtime.InteropServices.COMException]::new('fixture',{hresult})}};Get-ExactTaskOrAbsent 'fixture'")
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize('state,instances,success', [(1,0,True),(3,0,True),(0,0,False),(2,0,False),(4,0,False),(3,1,False)])
def test_receipt_requires_terminal_task_and_no_instances(state, instances, success):
    result = ps(f"$t=[pscustomobject]@{{State={state}}};$t|Add-Member ScriptMethod GetInstances {{param($flags) [pscustomobject]@{{Count={instances}}}}};Assert-CompletedKnowledgeTask $t")
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize('hresult,success', [(-2147216629,True),(-2147024891,False),(-2147024894,False)])
def test_refresh_completion_hresult_ends_wait_only(hresult, success):
    result = ps(f"$t=[pscustomobject]@{{State=4}};$t|Add-Member ScriptMethod Refresh {{throw [Runtime.InteropServices.COMException]::new('fixture',{hresult})}};Wait-KnowledgeTask $t -TimeoutSeconds 1")
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize('path,success', [('wiki/a.md',True),('.sources/legacy/sha256/a.md',True),('../a.md',False),('wiki/../a.md',False),('wiki/a.md:stream',False),('wiki/CON.md',False),('wiki/trailing./a.md',False),('wiki/a.txt',False)])
def test_windows_ambiguous_or_unregistered_copy_path_rejected(path, success):
    result = ps(f'Assert-RelativeCorpusPath {quote(path)}')
    assert (result.returncode == 0) is success, result.stderr


def test_private_acl_constructor_has_exact_private_trustees_before_creation():
    result = ps("(New-PrivateAcl $true).GetSecurityDescriptorSddlForm('All');(New-PrivateAcl $false).GetSecurityDescriptorSddlForm('All')")
    assert result.returncode == 0, result.stderr
    rows = result.stdout.strip().splitlines()
    assert rows == ['O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)', 'O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)']


def test_reviewed_copy_handle_refuses_actual_hardlink(tmp_path):
    path = tmp_path / 'fixture.txt'; path.write_bytes(b'inert')
    os.link(path, tmp_path / 'other.txt')
    result = ps(f"Initialize-FileIdentity;$s=Open-VerifiedFile {quote(path)} '{hashlib.sha256(b'inert').hexdigest()}' 5;$s.Dispose()", helpers=True)
    assert result.returncode != 0
    assert 'hardlink' in result.stderr.lower() or 'hard link' in result.stderr.lower() or 'link count' in result.stderr.lower()


def test_wrapper_acceptance_pin_tracks_exact_source_and_uses_isolated_no_bytecode():
    source = (HERE / 'accept-private-knowledge.py').read_bytes()
    wrapper = SCRIPT.read_text(encoding='utf-8')
    assert f"$sourceHash='{hashlib.sha256(source).hexdigest()}'" in wrapper
    assert "'-I -B " in wrapper
    assert "New-PrivateDirectory $corpusRoot -Root" in wrapper
    assert not any(line.strip().startswith('Copy-VerifiedPayload ') for line in wrapper.splitlines())


@pytest.mark.parametrize('change,success', [('',True),('home',False),('executable',False),('version',False),('site',False),('duplicate',False)])
def test_virtual_environment_requires_exact_protected_binding(change, success):
    raw = (accept.INSTALL / '.venv/pyvenv.cfg').read_text()
    if change == 'home':
        raw = raw.replace('home = ' + str(accept.BASE), 'home = C:\\untrusted')
    elif change == 'executable':
        raw = raw.replace('executable = ' + str(accept.BASE / 'python.exe'), 'executable = C:\\untrusted\\python.exe')
    elif change == 'version':
        raw = raw.replace('version = 3.12.13', 'version = 3.14.0')
    elif change == 'site':
        raw = raw.replace('include-system-site-packages = false', 'include-system-site-packages = true')
    elif change == 'duplicate':
        raw += '\nhome = ' + str(accept.BASE)
    result = ps(f'$basePythonRoot={quote(accept.BASE)};$basePython={quote(accept.BASE / "python.exe")};Assert-VenvBinding {quote(raw)}')
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize('change', ['none','base-prefix','base-executable','version','not-isolated','bytecode','venv-bytes','base-bytes'])
def test_actual_interpreter_binding_must_match_before_system_work(monkeypatch, change):
    runtime = SimpleNamespace(base_prefix=str(accept.BASE), _base_executable=str(accept.BASE / 'python.exe'),
                              version_info=(3,12,13), flags=SimpleNamespace(isolated=1), dont_write_bytecode=True)
    if change == 'base-prefix': runtime.base_prefix = 'C:\\untrusted'
    if change == 'base-executable': runtime._base_executable = 'C:\\untrusted\\python.exe'
    if change == 'version': runtime.version_info = (3,14,0)
    if change == 'not-isolated': runtime.flags.isolated = 0
    if change == 'bytecode': runtime.dont_write_bytecode = False
    monkeypatch.setattr(accept, 'sys', runtime)
    hashes = {accept.INSTALL / '.venv/pyvenv.cfg': accept.VENV_SHA, accept.BASE / 'python.exe': accept.BASE_SHA}
    if change == 'venv-bytes': hashes[accept.INSTALL / '.venv/pyvenv.cfg'] = '0'*64
    if change == 'base-bytes': hashes[accept.BASE / 'python.exe'] = '0'*64
    monkeypatch.setattr(accept, 'digest', lambda path: hashes[path])
    if change == 'none':
        accept.verify_runtime_binding()
    else:
        with pytest.raises(ValueError): accept.verify_runtime_binding()
