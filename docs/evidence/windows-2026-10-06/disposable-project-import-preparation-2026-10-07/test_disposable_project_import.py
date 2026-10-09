"""Actual ordinary-user Windows Git plumbing; no protected creation or Apply."""
import hashlib
import importlib.util
from pathlib import Path
import pytest
import json
import subprocess

ROOT=Path(__file__).parent
SPEC=importlib.util.spec_from_file_location('disposable_import',ROOT/'import-disposable-project.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
BUNDLE=Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\disposable-acceptance-c52a3a97.bundle').read_bytes()


@pytest.fixture
def inputs(tmp_path):
    assert hashlib.sha256(M.GIT.read_bytes()).hexdigest()=='77965c1ffd7d5d0f7d55ddbec10ad540efa61e58f36eae565cde0f36fab8fe53'
    repo=tmp_path/'project';repo.mkdir()
    template=tmp_path/'empty-template';template.mkdir()
    temporary=tmp_path/'temporary';temporary.mkdir()
    yield repo,template,temporary
    assert hashlib.sha256(M.GIT.read_bytes()).hexdigest()=='77965c1ffd7d5d0f7d55ddbec10ad540efa61e58f36eae565cde0f36fab8fe53'


def snapshot(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_real_git_import_exact_objects_reference_and_idempotence_refusal(inputs,monkeypatch,tmp_path):
    repo,template,temporary=inputs
    outside=tmp_path/'unrelated';outside.mkdir();marker=outside/'unchanged';marker.write_bytes(b'preserve')
    fake_config=tmp_path/'hostile-global.config'
    fake_config.write_text('[init]\n templateDir = '+str(outside).replace('\\','/')+'\n[core]\n hooksPath = '+str(outside).replace('\\','/')+'\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(fake_config))
    monkeypatch.setenv('GIT_OBJECT_DIRECTORY',str(outside))
    monkeypatch.setenv('GIT_EXEC_PATH',str(outside))
    monkeypatch.setenv('GIT_CONFIG_COUNT','1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0','core.hooksPath')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0',str(outside))
    result=M.import_repository(repo,BUNDLE,template,temporary,M.file_id(repo))
    assert result['bare'] and result['objects']==7 and result['files']==3
    assert result['baseline_commit']==M.BASELINE and result['baseline_tree']==M.TREE
    assert not (repo/'README.md').exists() and not (repo/'src').exists()
    assert snapshot(outside)=={'unchanged':hashlib.sha256(b'preserve').hexdigest()}
    before=snapshot(repo)
    with pytest.raises(M.ImportHeld,match='fresh_target_not_empty'):
        M.import_repository(repo,BUNDLE,template,temporary,M.file_id(repo))
    assert snapshot(repo)==before


@pytest.mark.parametrize('case',['bundle','identity','occupied','template'])
def test_pre_mutation_refusals_preserve_targets(inputs,case):
    repo,template,temporary=inputs
    bundle=BUNDLE;identity=M.file_id(repo)
    if case=='bundle':bundle=bundle[:-1]+bytes([bundle[-1]^1])
    if case=='identity':identity='0:0'
    if case=='occupied':(repo/'preserve').write_bytes(b'original')
    if case=='template':(template/'hook').write_bytes(b'never-executed')
    before=snapshot(repo)
    with pytest.raises(M.ImportHeld):M.import_repository(repo,bundle,template,temporary,identity)
    assert snapshot(repo)==before
    assert not (repo/'objects').exists()


@pytest.mark.parametrize('case',['extra_ref','changed_ref','extra_config','alternate','worktree','extra_object'])
def test_actual_repository_verification_rejects_drift_without_repair(inputs,case,tmp_path):
    repo,template,temporary=inputs
    M.import_repository(repo,BUNDLE,template,temporary,M.file_id(repo))
    git=M.Plumbing(repo,temporary)
    if case=='extra_ref':git.run('update-ref','refs/heads/unexpected',M.BASELINE,'0'*40)
    if case=='changed_ref':git.run('update-ref','-d',M.REF,M.BASELINE)
    if case=='extra_config':git.run('config','--local','core.hooksPath',str(tmp_path/'external'))
    if case=='alternate':(repo/'objects/info/alternates').write_text(str(tmp_path/'outside'))
    if case=='worktree':(repo/'worktrees').mkdir();(repo/'worktrees/extra').mkdir()
    if case=='extra_object':git.run('hash-object','-w','--stdin',data=b'extra unreachable object')
    before=snapshot(repo)
    with pytest.raises(M.ImportHeld):M.verify_repository(git)
    assert snapshot(repo)==before


def test_absent_ref_precondition_rejects_overwrite(inputs):
    repo,template,temporary=inputs
    M.import_repository(repo,BUNDLE,template,temporary,M.file_id(repo))
    before=snapshot(repo)
    with pytest.raises(M.ImportHeld,match='git_nonzero'):
        M.Plumbing(repo,temporary).run('update-ref',M.REF,M.BASELINE,'0'*40)
    assert snapshot(repo)==before


PS=ROOT/'install-disposable-project.ps1'


def powershell(body):
    source=str(PS).replace("'","''")
    prelude=f'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){{Import-Module (Join-Path $PSHOME "Modules\\$m\\$m.psd1")}}
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|Out-String)}}
foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}
Initialize-ProjectNative
'''
    return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',prelude+body],capture_output=True,text=True,timeout=30)


def test_actual_file_id_info_matches_python_312_files_and_directories(tmp_path):
    sample=tmp_path/'sample';sample.write_bytes(b'identity fixture')
    result=powershell(f"@([CoChemProjectNative]::Identity('{tmp_path}'),[CoChemProjectNative]::Identity('{sample}'))|ConvertTo-Json -Compress")
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==[M.file_id(tmp_path),M.file_id(sample)]


def test_actual_programdata_ancestor_custody_read_only():
    helper=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')
    result=powershell(f"$helper='{helper}';"+r'''
foreach($d in @(Import-PinnedFunctions $helper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors'))){. ([scriptblock]::Create($d))}
Assert-ControlAncestors 'C:\ProgramData';'passed'
''')
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='passed'


@pytest.mark.parametrize('inherit_only',[False,True])
def test_dotnet_inherit_only_vs_effective_untrusted_grant(tmp_path,inherit_only):
    result=powershell(f"$fixture='{tmp_path}';$inheritOnly=${str(inherit_only).lower()};"+r'''
function Assert-NoReparseAncestors {param($Path)}
$script:fixtureAcl=[Security.AccessControl.DirectorySecurity]::new()
$script:fixtureAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-18'))
$propagation=if($inheritOnly){[Security.AccessControl.PropagationFlags]::InheritOnly}else{[Security.AccessControl.PropagationFlags]::None}
$rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-11'),[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',$propagation,[Security.AccessControl.AccessControlType]::Allow)
$script:fixtureAcl.AddAccessRule($rule)
function Get-Acl {param($LiteralPath)$script:fixtureAcl}
$refused=$false;try{Assert-ControlAncestors $fixture}catch{$refused=$true}
@{refused=$refused;enum_value=[int][Security.AccessControl.PropagationFlags]::InheritOnly}|ConvertTo-Json -Compress
''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'refused':not inherit_only,'enum_value':2}


@pytest.mark.parametrize('case',['valid','hash','identity','alias','acl'])
def test_actual_git_custody_and_in_memory_drift_refusal(case):
    result=powershell(f"$case='{case}';"+r'''
$programFiles='C:\Program Files';$script:held=[Collections.Generic.List[IO.Stream]]::new()
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$helper=Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1'
foreach($d in @(Import-PinnedFunctions $helper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-ProtectedPath'))){. ([scriptblock]::Create($d))}
$evidence=Get-Content -LiteralPath (Join-Path $repo 'docs\evidence\windows-2026-10-06\git-custody-followup-2026-10-07\git-static-custody.json') -Raw|ConvertFrom-Json
switch($case){'hash'{$evidence.binaries[0].sha256='a'*64}'identity'{$evidence.binaries[0].metadata.volume=0}'alias'{$evidence.binaries[0].hardlink_aliases=@('C:\Program Files\Git\cmd\git.exe')}'acl'{$evidence.acl_entries[0].sddl='O:SYD:'}}
$refused=$false;try{Assert-GitRuntime $evidence}catch{$refused=$true}finally{foreach($stream in $script:held){$stream.Dispose()}}
@{refused=$refused}|ConvertTo-Json -Compress
''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['refused']==(case!='valid')


@pytest.mark.parametrize('exit_code',[0,2])
def test_actual_ps51_native_stderr_is_withheld_and_exit_handled(exit_code):
    result=powershell(f"$exitCode={exit_code};"+r'''
$python='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$code="import sys; print('private-bootstrap-fixture',file=sys.stderr); sys.exit("+$exitCode+")"
$result=Invoke-PrivateImporter $python @('-I','-S','-B','-c',$code)
@{exit=$result.exit_code;unexpected=$result.unexpected_output;preference=[string]$ErrorActionPreference}|ConvertTo-Json -Compress
''')
    assert result.returncode==0,result.stderr
    assert 'private-bootstrap-fixture' not in result.stdout+result.stderr
    assert json.loads(result.stdout)=={'exit':exit_code,'unexpected':True,'preference':'Stop'}


@pytest.mark.parametrize('case',['expected','secret','success_without_failure'])
def test_failure_metadata_is_strictly_sanitized(case):
    result=powershell(f"$case='{case}';"+r'''
$receipt=[pscustomobject]@{status='HELD';failure=[pscustomobject]@{error_type='ImportHeld';code='git_nonzero_128'}}
if($case -eq 'secret'){$receipt.failure.error_type='SecretFixture';$receipt.failure.code='private contents here'}
if($case -eq 'success_without_failure'){$receipt=[pscustomobject]@{status='PRIVATE_BARE_PROJECT_VERIFIED'}}
Get-SanitizedImportFailure $receipt|ConvertTo-Json -Compress
''')
    assert result.returncode==0,result.stderr
    expected={'error_type':'ImportHeld','code':'git_nonzero_128'} if case=='expected' else {'error_type':'UnexpectedException','code':'receipt_or_child_outcome'}
    assert json.loads(result.stdout)==expected


def test_nonadministrator_apply_refuses_before_mutation():
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(PS),'-Apply'],capture_output=True,text=True,timeout=10)
    assert result.returncode!=0 and 'Apply requires owner Administrator.' in result.stderr
