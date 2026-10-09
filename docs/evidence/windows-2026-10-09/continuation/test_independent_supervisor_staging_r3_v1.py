"""Ordinary Windows fixtures only; no SYSTEM, installation, account or live task.

Private permission gates are explicitly substituted for bounded tmp_path paths.
Ledger SQLite and frozen bootstrap logic are real. PowerShell privileged effects
are inert while actual copy, task definition and orchestration code is exercised.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
PS = WORK / 'install-independent-supervisor-staging-r3-v1.ps1'
PY = WORK / 'stage-independent-supervisor-holds-r3-v1.py'
MANIFEST = WORK / 'independent-supervisor-staging-r3-v1.manifest-draft.json'
sys.path.insert(0, str(REPO / 'src'))
spec = importlib.util.spec_from_file_location('independent_staging', PY)
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
REAL_RUN_PATH = runpy.run_path


def digest(raw): return hashlib.sha256(raw).hexdigest()


def ps(body):
    prelude = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;$script:workspaceRoot='{WORK}';
    foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){{Import-Module (Join-Path $PSHOME "Modules\\$m\\$m.psd1")}};
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{PS}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    proc = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoProfile', '-NonInteractive', '-Command', prelude + body], capture_output=True, text=True, timeout=40)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize('case', ['valid', 'incomplete', 'bool_string', 'activation', 'bytes_bool', 'bytes_text', 'duplicate', 'escape', 'reserved', 'changed_source', 'missing_test'])
def test_actual_inventory_strict_ps51(case):
    result = ps(f"$case='{case}';$inventory=Get-Content -LiteralPath '{MANIFEST}' -Raw|ConvertFrom-Json;" + r'''
      $script:targetRoot=$inventory.target_root;$script:pairRoot=$inventory.unpublished_pair_root;$script:configHash=$inventory.pipeline_config_sha256;
      $script:repo='D:\repo';$script:sourceRoot=$script:targetRoot+'\source';$script:acceptanceRoot=$script:targetRoot+'\acceptance';
      $prior=Get-Content -LiteralPath 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\source-manifest.json' -Raw|ConvertFrom-Json;
      switch($case){
        'incomplete'{$inventory.complete_source_freeze=$false}'bool_string'{$inventory.complete_source_freeze='true'}
        'activation'{$inventory.activation_authorized=$true}'bytes_bool'{$inventory.source_files[0].bytes=$true}
        'bytes_text'{$inventory.source_files[0].bytes='123'}'duplicate'{$inventory.source_files[1].relative=$inventory.source_files[0].relative.ToUpper()}
        'escape'{$inventory.source_files[0].relative='../secret'}'reserved'{$inventory.source_files[0].relative='src/AUX.py'}
        'changed_source'{$inventory.source_files[0].sha256=('a'*64)}
        'missing_test'{$inventory.acceptance_files=@($inventory.acceptance_files|Where-Object {$_.relative -ne 'knowledge/v4.1.2_manifest.json'})}
      }
      $refused=$false;$count=0;try{$rows=@(Get-StagingFiles $inventory $prior);$count=$rows.Count}catch{$refused=$true};
      @{refused=$refused;count=$count}|ConvertTo-Json -Compress
    ''')
    assert result == {'refused': case != 'valid', 'count': 292 if case == 'valid' else 0}


@pytest.mark.parametrize('case', ['valid', 'unsafe', 'duplicate', 'bytes_bool', 'source_hash', 'authority', 'not_frozen'])
def test_python_manifest_binding(case):
    value = json.loads(MANIFEST.read_bytes())
    if case == 'unsafe': value['source_files'][0]['relative'] = 'src/../private'
    if case == 'duplicate': value['source_files'][1]['relative'] = value['source_files'][0]['relative'].upper()
    if case == 'bytes_bool': value['source_files'][0]['bytes'] = True
    if case == 'source_hash': next(r for r in value['source_files'] if r['relative'].endswith('/engine.py'))['sha256'] = '0' * 64
    if case == 'authority': value['activation_authorized'] = True
    if case == 'not_frozen': value['complete_source_freeze'] = 'true'
    if case == 'valid': assert len(M.manifest_rows(value)) == 292
    else:
        with pytest.raises(ValueError): M.manifest_rows(value)


@pytest.fixture
def bounded(tmp_path, monkeypatch):
    from cochem_pipeline import windows as win
    root = tmp_path / 'new'; root.mkdir(); control = root / 'controls'; control.mkdir()
    private = root / 'unpublished-private'; private.mkdir()
    for attr, value in [('ROOT', root), ('CONTROL', control), ('PRIVATE', private), ('PAIR', private / 'unresolved-pair')]:
        monkeypatch.setattr(M, attr, value)
    def boundary(path, *args, **kwargs):
        path = Path(path).absolute()
        assert tmp_path == path or tmp_path in path.parents
        M.ordinary(path, directory=path.is_dir())
    monkeypatch.setattr(win, 'require_system', lambda: None)
    monkeypatch.setattr(win, 'validate_code_path', boundary)
    monkeypatch.setattr(win, 'validate_private_directory', boundary)
    monkeypatch.setattr(win, '_acl', lambda path: (win.SYSTEM_SID, True, [(win.SYSTEM_SID, win.FULL_CONTROL, 3)]))
    for old, new in [('bootstrap-unresolved-budget-pair.py', 'bootstrap-unresolved-budget-pair.py'), ('unresolved-budget-evidence.proposed.json', 'unresolved-budget-evidence.json')]:
        shutil.copyfile(WORK / old, control / new)
    def fixture_definitions(path, **kwargs):
        assert Path(path) == control / 'bootstrap-unresolved-budget-pair.py'
        definitions = REAL_RUN_PATH(str(path), **kwargs)
        definitions['bootstrap_pair'].__globals__['private'] = boundary
        return definitions
    monkeypatch.setattr(M.runpy, 'run_path', fixture_definitions)
    monkeypatch.setattr(M, 'verify_runtime', lambda value: ({}, {'verified': True, 'files': 109, 'source_sha256': '1' * 64}))
    return tmp_path


def snapshot(root): return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_real_new_sqlite_pair_complete_without_authority_and_repeat_refused(bounded):
    old = bounded / 'preserve'; old.mkdir(); (old / 'opaque-legacy.db').write_bytes(b'never opened')
    before = snapshot(old)
    result = M.run('a' * 32, 'b' * 64)
    assert result['status'] == 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED'
    assert result['paired_evidence_sha256'] == '1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177'
    assert set(result['ledger_sha256']) == {'supervisor.db', 'component-recovery.db'}
    assert all(result[k] is False for k in ('activation_ready', 'paid_repair_enabled', 'component_recovery_enabled', 'old_ledgers_opened', 'active_warden_changed', 'pipeline_configuration_changed'))
    retained = snapshot(M.ROOT)
    with pytest.raises(FileExistsError): M.run('a' * 32, 'b' * 64)
    assert snapshot(M.ROOT) == retained and snapshot(old) == before


@pytest.mark.parametrize('case', ['runtime', 'private', 'inheritance', 'partial_pair', 'post_verify'])
def test_failures_preserve_partial_outputs_without_retry(bounded, monkeypatch, case):
    from cochem_pipeline import windows as win
    original = M.verify_runtime; calls = []
    def fail(*args, **kwargs): raise PermissionError('private token text MUST NOT appear')
    if case == 'runtime': monkeypatch.setattr(M, 'verify_runtime', fail)
    if case == 'private': monkeypatch.setattr(win, 'validate_private_directory', fail)
    if case == 'inheritance': monkeypatch.setattr(win, '_acl', lambda p: (win.SYSTEM_SID, True, [(win.SYSTEM_SID, win.FULL_CONTROL, 11)]))
    if case == 'partial_pair':
        from cochem_supervisor.component_recovery import RecoveryLedger
        monkeypatch.setattr(RecoveryLedger, '__init__', fail)
    if case == 'post_verify':
        def verify(value):
            calls.append(value)
            if len(calls) > 1: fail()
            return original(value)
        monkeypatch.setattr(M, 'verify_runtime', verify)
    result = M.run('a' * 32, 'b' * 64)
    assert result['status'] == 'STAGING_HELD' and result['partial_outputs_preserved'] is True
    assert 'private token' not in json.dumps(result)
    assert result['pair_creation_started'] == (case in ('partial_pair', 'post_verify'))
    prior = snapshot(M.ROOT)
    with pytest.raises(FileExistsError): M.run('a' * 32, 'b' * 64)
    assert snapshot(M.ROOT) == prior


def test_real_hardlink_rejected_before_receipt(bounded):
    path = M.CONTROL / 'unresolved-budget-evidence.json'
    os.link(path, bounded / 'alias')
    result = M.run('a' * 32, 'b' * 64)
    assert result['status'] == 'STAGING_HELD' and result['pair_creation_started'] is False
    assert not M.PAIR.exists()


def test_existing_private_child_preserved(bounded):
    (M.PRIVATE / 'unrelated').write_bytes(b'preserve')
    before = snapshot(M.PRIVATE)
    result = M.run('a' * 32, 'b' * 64)
    assert result['failure']['phase'] == 'fresh_private_boundary'
    assert snapshot(M.PRIVATE) == before


def test_default_python_preview_has_no_io(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', [str(PY)])
    monkeypatch.setattr(M, 'read', lambda *a, **k: pytest.fail('preview read a file'))
    assert M.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'READ_ONLY_PREVIEW_NO_IO'


def test_actual_full_source_acceptance_runtime_verifier(tmp_path, monkeypatch):
    """Real166 source +126 acceptance files and109 installed assets; ACL gate fixture."""
    from cochem_pipeline import windows as win
    root = tmp_path / 'new'; control = root / 'controls'; control.mkdir(parents=True)
    manifest = json.loads(MANIFEST.read_bytes())
    manifest.update(target_root=str(root), unpublished_pair_root=str(root / 'unpublished-private/unresolved-pair'))
    for name, dest in [('source_files', root / 'source'), ('acceptance_files', root / 'acceptance')]:
        for row in manifest[name]:
            src = WORK / 'acceptance-pytest-staging-r3-v1.ini' if row['relative'] == 'pytest.ini' else REPO / row['relative']
            target = dest / row['relative']; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, target)
    for package in ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor'):
        shutil.copytree(root / 'source/src' / package, root / '.venv/Lib/site-packages' / package)
    exe = root / '.venv/Scripts/python.exe'; exe.parent.mkdir(parents=True); exe.write_bytes(b'inert launcher')
    base = tmp_path / 'base.exe'; base.write_bytes(b'inert base')
    own = control / PY.name; shutil.copyfile(PY, own)
    raw = json.dumps(manifest).encode(); (control / 'staging-manifest.json').write_bytes(raw)
    pipeline = tmp_path / 'preserved'; pipeline.mkdir()
    shutil.copyfile(Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\pipeline.json'), pipeline / 'pipeline.json')
    for key, value in [('ROOT', root), ('CONTROL', control), ('PAIR', root / 'unpublished-private/unresolved-pair'), ('PIPELINE', pipeline), ('__file__', str(own))]: monkeypatch.setattr(M, key, value)
    monkeypatch.setattr(sys, 'executable', str(exe)); monkeypatch.setattr(sys, '_base_executable', str(base))
    monkeypatch.setattr(win, 'require_system', lambda: None)
    def ordinary_boundary(path):
        assert tmp_path in Path(path).parents
        M.ordinary(path, Path(path).is_dir())
    monkeypatch.setattr(win, 'validate_code_path', ordinary_boundary)
    _, revision = M.verify_runtime(digest(raw))
    assert revision['verified'] is True and revision['files'] == 109
    target = root / '.venv/Lib/site-packages/cochem_supervisor/engine.py'
    target.write_bytes(target.read_bytes() + b'\n# drift\n')
    with pytest.raises(ValueError, match='Installed canonical revision differs'):
        M.verify_runtime(digest(raw))


@pytest.mark.parametrize('case', ['success', 'held', 'bad_pair_hash', 'invented_allowance', 'extra_ledger', 'unsafe_error', 'string_flag', 'string_count'])
def test_bound_receipt_projection_preserves_safe_failure_details(tmp_path, case):
    value = {'schema': 'cochem-independent-supervisor-staging/1', 'nonce': 'a' * 32,
        'root': str(tmp_path), 'helper_sha256': 'b' * 64, 'manifest_sha256': 'c' * 64,
        'status': 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED', 'phase': 'complete',
        'activation_ready': False, 'paid_repair_enabled': False, 'component_recovery_enabled': False,
        'old_ledgers_opened': False, 'provider_or_model_calls': 0, 'active_warden_changed': False,
        'pipeline_configuration_changed': False, 'partial_outputs_preserved': True,
        'pair_creation_started': True, 'revision': {'verified': True, 'files': 109},
        'paired_receipt_sha256': 'd' * 64, 'paired_evidence_sha256': '1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177',
        'ledger_sha256': {'supervisor.db': 'e' * 64, 'component-recovery.db': 'f' * 64}}
    if case in ('held', 'unsafe_error'):
        value.update(status='STAGING_HELD', phase='paired_hold_creation', failure={'phase': 'paired_hold_creation', 'error_type': 'PermissionError' if case == 'held' else 'private secret text', 'winerror': 5})
    if case == 'bad_pair_hash': value['paired_evidence_sha256'] = '0' * 64
    if case == 'invented_allowance': value['paid_repair_enabled'] = True
    if case == 'extra_ledger': value['ledger_sha256']['old.db'] = 'a' * 64
    if case == 'string_flag': value['activation_ready'] = 'False'
    if case == 'string_count': value['provider_or_model_calls'] = '0'
    (tmp_path / 'staging-receipt.json').write_text(json.dumps(value), encoding='utf-8')
    result = ps(f"$script:controlRoot='{tmp_path}';$script:targetRoot='{tmp_path}';$exitCode={2 if case in ('held','unsafe_error') else 0};" + r'''
      function Assert-ProtectedPath {param($Path)}
      function Open-VerifiedFile {param($Path,$Hash,$Length)if((Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant() -cne $Hash){throw 'hash differs'};[IO.File]::OpenRead($Path)}
      function Read-HeldText {param($Stream)$r=[IO.StreamReader]::new($Stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$r.ReadToEnd()}finally{$r.Dispose()}}
      $refused=$false;$safe=$null;try{$safe=Read-StagingResult ('a'*32) ('c'*64) ('b'*64) ([pscustomobject]@{LastTaskResult=$exitCode})}catch{$refused=$true}
      @{refused=$refused;safe=$safe}|ConvertTo-Json -Depth 6 -Compress
    ''')
    assert result['refused'] == (case not in ('success', 'held'))
    if case == 'held': assert result['safe']['failure'] == {'phase': 'paired_hold_creation', 'error_type': 'PermissionError', 'winerror': 5}


def test_reviewed_native_copy_function_createnew_identity_and_hardlinks(tmp_path):
    (tmp_path / 'source').write_bytes(b'exact ordinary fixture')
    result = ps(f"$fixture='{tmp_path}';" + r'''
      $path='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
      foreach($definition in @(Import-StagingFunctions $path '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Get-LocalPath','Assert-NoReparseAncestors','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){. ([scriptblock]::Create($definition))}
      Initialize-FileIdentity
      function Assert-ProtectedPath {param($Path)if(-not [IO.Path]::GetFullPath($Path).StartsWith($fixture,[StringComparison]::OrdinalIgnoreCase)){throw 'outside fixture'};Assert-NoReparseAncestors $Path}
      function New-CodeAcl {param([bool]$Directory)$acl=[Security.AccessControl.FileSecurity]::new();$acl.SetAccessRuleProtection($true,$false);$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$acl.SetOwner($sid);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow));$acl}
      $source=Join-Path $fixture 'source';$record=[pscustomobject]@{source=$source;destination=(Join-Path $fixture 'copied');sha256=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant();length=(Get-Item -LiteralPath $source).Length}
      Copy-VerifiedPayload $record;$repeat=$false;try{Copy-VerifiedPayload $record}catch{$repeat=$true}
      $null=New-Item -ItemType HardLink -Path (Join-Path $fixture 'alias') -Target $source
      $record.destination=Join-Path $fixture 'blocked';$hardlink=$false;try{Copy-VerifiedPayload $record}catch{$hardlink=$true}
      @{exact=([IO.File]::ReadAllText((Join-Path $fixture 'copied')) -ceq 'exact ordinary fixture');repeat_refused=$repeat;hardlink_refused=$hardlink;newtarget_absent=(-not(Test-Path -LiteralPath $record.destination))}|ConvertTo-Json -Compress
    ''')
    assert result == {'exact': True, 'repeat_refused': True, 'hardlink_refused': True, 'newtarget_absent': True}


@pytest.mark.parametrize('case', ['absent', 'exists', 'denied', 'wrong_missing'])
def test_task_absence_is_not_permission_denial(case):
    result = ps(f"$case='{case}';" + r'''
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)
        if($case -eq 'exists'){return [pscustomobject]@{Name=$name}}
        $code=switch($case){'absent'{-2147024894}'denied'{-2147024891}default{-2147216625}}
        throw [Runtime.InteropServices.COMException]::new('fixture',$code)
      }
      $held=$false;$exists=$false;try{$exists=$null -ne (Get-StagingTaskOrAbsent $folder 'fixed')}catch{$held=$true}
      @{held=$held;exists=$exists}|ConvertTo-Json -Compress
    ''')
    assert result == {'held': case not in ('absent', 'exists'), 'exists': case == 'exists'}


@pytest.mark.parametrize('case', ['success', 'build_failure', 'changed_daemon'])
def test_full_ps51_staging_apply_flow_uses_new_copy_then_private_task(tmp_path, case):
    source = tmp_path / 'source'; source.write_bytes(b'copy exact')
    result = ps(f"$case='{case}';$fixture='{tmp_path}';" + r'''
      $script:targetRoot=Join-Path $fixture 'new';$script:sourceRoot=Join-Path $script:targetRoot 'source';$script:acceptanceRoot=Join-Path $script:targetRoot 'acceptance';$script:controlRoot=Join-Path $script:targetRoot 'controls';$script:privateRoot=Join-Path $script:targetRoot 'unpublished-private';
      $script:python=Join-Path $fixture 'python.exe';$script:uv=Join-Path $fixture 'uv.exe';$script:taskName='fixture-only-no-scheduler';$script:events=[Collections.Generic.List[string]]::new();$script:checks=0
      function Get-StagingTaskOrAbsent {param($Folder,$Name) return $null}
      function Assert-DaemonDefinitionsUnchanged {param($Before,$Folder) $script:checks++;$script:events.Add('daemon');if($case -eq 'changed_daemon' -and $script:checks -eq 2){throw 'fixture drift'}}
      function Assert-CodeTreeOnce {param($Path) $script:events.Add('codecheck')}
      function Assert-ProtectedPath {param($Path)}
      function New-ProtectedDirectory {param($Path) $null=[IO.Directory]::CreateDirectory($Path)}
      function Copy-VerifiedPayload {param($File) $src=[IO.File]::OpenRead($File.source);try{$dst=[IO.File]::Open($File.destination,[IO.FileMode]::CreateNew);try{$src.CopyTo($dst)}finally{$dst.Dispose()}}finally{$src.Dispose()};$script:events.Add('copy')}
      function Invoke-FrozenRuntimeBuild {$script:events.Add('build');if($case -eq 'build_failure'){throw 'fixture failure'}}
      function Protect-NewRuntimeTree {param($Path)$script:events.Add('protect');if(Test-Path -LiteralPath $script:privateRoot){throw 'Private bytes existed before final code ACL'}}
      function Assert-PostBuildFrozenFiles {param($Files)foreach($f in $Files){if([IO.File]::ReadAllText($f.destination) -cne 'copy exact'){throw 'copy corrupt'}}}
      function Assert-NewRuntimeInterpreter {param($Root)$script:events.Add('interpreter')}
      function New-PrivateDirectory {param($Path,[switch]$Root)$script:events.Add('private');$null=[IO.Directory]::CreateDirectory($Path)}
      function Invoke-StagingTask {param($Folder,$Nonce,$ManifestHash)$script:events.Add('task');if(-not (Test-Path -LiteralPath $script:privateRoot)){throw 'no private boundary'};return [pscustomobject]@{LastTaskResult=0}}
      function Read-StagingResult {param($Nonce,$ManifestHash,$HelperHash,$Task)@{status='INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED'}}
      $file=[pscustomobject]@{source=(Join-Path $fixture 'source');destination=(Join-Path $script:sourceRoot 'one.py');relative='one.py';sha256=('a'*64);length=10}
      $control=[pscustomobject]@{source=$file.source;destination=(Join-Path $script:controlRoot 'stage-independent-supervisor-holds-r3-v1.py');relative='stage-independent-supervisor-holds-r3-v1.py';sha256=('b'*64);length=10}
      $failed=$false;try{$null=Invoke-IndependentStaging @($file) @($control) ([pscustomobject]@{}) @{} ('c'*64)}catch{$failed=$true}
      @{failed=$failed;events=@($script:events);source_retained=(Test-Path -LiteralPath $file.destination);private=(Test-Path -LiteralPath $script:privateRoot)}|ConvertTo-Json -Compress
    ''')
    assert result['failed'] == (case != 'success') and result['source_retained']
    assert result['events'].count('copy') == 2
    if case == 'success':
        assert result['events'].index('protect') < result['events'].index('private') < result['events'].index('task')
        assert result['events'].count('daemon') == 3
    else: assert 'task' not in result['events']


@pytest.mark.parametrize('case', ['success', 'bad_working_directory', 'bad_runlevel'])
def test_real_task_definition_argv_registration_and_readback(case):
    result = ps(f"$case='{case}';" + r'''
      $script:targetRoot='C:\fixture\fresh';$script:controlRoot=$script:targetRoot+'\controls';$script:taskName='fixture';$script:registered=$false
      $action=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''};$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Create {param($n)$action};$actions|Add-Member ScriptMethod Item {param($n)$action}
      $def=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;ExecutionTimeLimit='';MultipleInstances=0};Actions=$actions;Triggers=@()}
      $script:scheduler=[pscustomobject]@{};$script:scheduler|Add-Member ScriptMethod NewTask {param($n)$def}
      $task=[pscustomobject]@{Definition=$def;LastTaskResult=0};$task|Add-Member ScriptMethod Run {param($x)if($case -eq 'bad_working_directory'){$action.WorkingDirectory='wrong'};if($case -eq 'bad_runlevel'){$def.Principal.RunLevel=0};[pscustomobject]@{State=3}}
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)$task};$folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$definition,$flags,$user,$password,$logon,$sddl)if($flags -ne 2 -or $user -cne 'SYSTEM' -or $null -ne $password -or $logon -ne 5 -or $sddl -cne 'D:P(A;;FA;;;SY)(A;;FA;;;BA)'){throw 'bad registration'};$script:registered=$true;$task}
      function Get-StagingTaskOrAbsent {param($Folder,$Name)return $null}
      function Wait-KnowledgeTask {param($Instance,$TimeoutSeconds)if($TimeoutSeconds -ne 190){throw 'bad bounded wait'}}
      function Assert-CompletedKnowledgeTask {param($Task)}
      $refused=$false;try{$null=Invoke-StagingTask $folder ('a'*32) ('b'*64)}catch{$refused=$true}
      @{refused=$refused;registered=$script:registered;arguments=$action.Arguments;path=$action.Path}|ConvertTo-Json -Compress
    ''')
    assert result['registered'] and result['refused'] == (case != 'success')
    assert result['arguments'] == '-I -B "C:\\fixture\\fresh\\controls\\stage-independent-supervisor-holds-r3-v1.py" --execute --nonce ' + 'a' * 32 + ' --manifest-sha256 ' + 'b' * 64
