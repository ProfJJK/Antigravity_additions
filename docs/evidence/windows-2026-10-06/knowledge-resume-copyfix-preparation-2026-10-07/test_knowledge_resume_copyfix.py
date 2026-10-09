"""Actual PS5.1 recovery-copy control flow using only disposable user fixtures.

The private ACL policy is explicitly replaced by current-user fixture ACLs in
copy tests. No Administrator token, real COM tasks, protected target or KB state.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).parent
WRAPPER=ROOT/'resume-private-knowledge-copyfix.ps1'
PS=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
PAYLOAD=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')


def q(path):
    return "'"+str(path).replace("'", "''")+"'"


def setup(tmp_path):
    incoming=tmp_path/'incoming';incoming.mkdir()
    for name in ('resume-private-knowledge.py','diagnose-knowledge-state.py','accept-private-knowledge.py'):
        shutil.copyfile(ROOT/name,incoming/name)
    destination=tmp_path/'existing-empty';destination.mkdir()
    return incoming,destination


def script_prefix(incoming,destination):
    return r"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
function Read-FunctionText {
 param([string]$Path,[string[]]$Names)
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'Fixture source parse failed'}
 foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
  if($Names.Count -eq 0 -or $f.Name -in $Names){$f.Extent.Text}
 }
}
"""+f"""
$wrapper={q(WRAPPER)}
foreach($text in @(Read-FunctionText $wrapper @())){{. ([scriptblock]::Create($text))}}
foreach($text in @(Read-FunctionText {q(PAYLOAD)} @('Initialize-FileIdentity','Open-VerifiedFile'))){{. ([scriptblock]::Create($text))}}
foreach($text in @(Read-FunctionText {q(ROOT/'install-private-knowledge-candidate.ps1')} @('Copy-PrivateFile'))){{. ([scriptblock]::Create($text))}}
$source=Join-Path {q(incoming)} 'resume-private-knowledge.py'
$sourceHash='780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b'
$diagnosticRoot={q(incoming)};$originalRoot={q(incoming)};$root={q(destination)}
$held=[Collections.Generic.List[IO.FileStream]]::new()
Initialize-FileIdentity
"""+r"""
# Disposable ordinary-user ACL fixture only. Exact production copy loop,
# CreateNew FileStream, held hash and Win32 file identity code remain intact.
function Assert-ProtectedPath {param($Path)}
function Assert-PrivateItem {param($Path)}
function Assert-ResumeRootInheritance {param($Path)}
function Assert-NoReparseAncestors {param($Path)}
function New-PrivateAcl {
 param([bool]$Directory)
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
 $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
 $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'))
 $acl
}
$stamp=(Get-Item -LiteralPath $root -Force).CreationTimeUtc
[IO.Directory]::SetLastWriteTimeUtc($root,$stamp);$rootObservedUtc=$stamp.ToString('o')
"""


def run(tmp_path,body):
    path=tmp_path/'fixture.ps1';path.write_text(body,encoding='utf-8')
    return subprocess.run([str(PS),'-NoProfile','-File',str(path)],capture_output=True,text=True,timeout=35)


def test_exact_ps51_three_record_copy_loop_with_real_exclusive_file_copy(tmp_path):
    incoming,destination=setup(tmp_path)
    body=script_prefix(incoming,destination)+r"""
try{
 Assert-ExistingEmptyResumeRoot $root
 $records=@(Get-ResumeCopyRecords)
 if($records.Count -ne 3){throw 'Not three typed records'}
 Confirm-ResumeCopyRecords $records
 if(@(Get-ChildItem -LiteralPath $root -Force).Count -ne 0){throw 'Source checking wrote destination'}
 Copy-ResumeRecords $records
 if(@(Get-ChildItem -LiteralPath $root -Force).Count -ne 3){throw 'Not three copied helpers'}
 foreach($record in $records){
   if($record.Source -eq 'C' -or -not [IO.Path]::IsPathRooted($record.Source)){throw 'Flattened source path'}
   if((Get-FileHash -LiteralPath (Join-Path $root $record.Name)).Hash.ToLowerInvariant() -cne $record.Sha256){throw 'Copied bytes differ'}
 }
 'THREE_COPIES_VERIFIED'
}finally{foreach($stream in $held){$stream.Dispose()}}
"""
    result=run(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'THREE_COPIES_VERIFIED' in result.stdout
    for source in incoming.iterdir():assert source.read_bytes()==(destination/source.name).read_bytes()


@pytest.mark.parametrize('change',['third_bytes','first_path_C','missing_record','duplicate_record'])
def test_every_record_checked_before_any_destination_copy(tmp_path,change):
    incoming,destination=setup(tmp_path)
    changes={
      'third_bytes':"[IO.File]::WriteAllText((Join-Path $originalRoot 'accept-private-knowledge.py'),'changed')",
      'first_path_C':"$records[0].Source='C'",
      'missing_record':"$records=@($records[0],$records[1])",
      'duplicate_record':"$records[2]=$records[1]",
    }
    body=script_prefix(incoming,destination)+"\n$records=@(Get-ResumeCopyRecords)\n"+changes[change]+r"""
try{
 $refused=$false
 try{Confirm-ResumeCopyRecords $records;Copy-ResumeRecords $records}catch{$refused=$true}
 if(-not $refused -or @(Get-ChildItem -LiteralPath $root -Force).Count -ne 0){throw 'Invalid source was not stopped before copying'}
 'REFUSED_WITH_EMPTY_ROOT'
}finally{foreach($stream in $held){$stream.Dispose()}}
"""
    result=run(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
    assert not list(destination.iterdir())


@pytest.mark.parametrize('change',['child','wrong_creation_time','wrong_path','private_acl_rejected','file_not_directory'])
def test_preserved_root_gate_refuses_changes_without_writes(tmp_path,change):
    incoming,destination=setup(tmp_path)
    changes={
      'child':"[IO.File]::WriteAllText((Join-Path $root 'resume-acceptance.json'),'preserved');[IO.Directory]::SetLastWriteTimeUtc($root,$stamp)",
      'wrong_creation_time':"$rootObservedUtc=$stamp.AddSeconds(1).ToString('o')",
      'wrong_path':"$testedPath=$root+'-different'",
      'private_acl_rejected':"function Assert-PrivateItem {param($Path)throw 'Private ACL rejected'}",
      'file_not_directory':"$testedPath=Join-Path $diagnosticRoot 'diagnose-knowledge-state.py';$root=$testedPath",
    }
    body=script_prefix(incoming,destination)+"\n$testedPath=$root\n"+changes[change]+r"""
$refused=$false;try{Assert-ExistingEmptyResumeRoot $testedPath}catch{$refused=$true}
if(-not $refused){throw 'Unsafe preserved root accepted'}
'REFUSED'
"""
    result=run(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
    if change=='child':assert (destination/'resume-acceptance.json').read_text()=='preserved'


def test_exact_apply_body_reaches_simulated_task_only_after_three_verified_copies(tmp_path):
    incoming,destination=setup(tmp_path)
    body=script_prefix(incoming,destination)+r"""
$Apply=$true;$python='C:\fixture-runtime\python.exe';$basePython='C:\fixture-runtime\base.exe'
$basePythonRoot='C:\fixture-runtime\base';$venvConfig='C:\fixture-runtime\pyvenv.cfg'
$installRoot='C:\fixture-runtime';$oldInstall='C:\fixture-old';$receiptHash='receipt';$manifestHash='manifest';$configHash='config'
$originalHash='original';$diagnosticHash='diagnostic';$taskName='FIXTURE-NO-TASK-WILL-RUN'
$actualHold=(Get-Command Hold-PinnedFile).ScriptBlock
function Hold-PinnedFile {
 param([string]$Path,[string]$Hash,[long]$Maximum=1048576,[switch]$LocalSource)
 if($Path -in @($source,(Join-Path $diagnosticRoot 'diagnose-knowledge-state.py'),(Join-Path $originalRoot 'accept-private-knowledge.py')) -or $Path.StartsWith($root+'\')){
   & $actualHold $Path $Hash $Maximum -LocalSource:$LocalSource;return
 }
 $text='{}'
 if($Path.EndsWith('install-after.json')){$text='{"mode":"FRESH_STOPPED_RUNTIME_READY","source_files":166,"source_manifest_sha256":"manifest","configuration_sha256":"config","verification":{"revision":{"verified":true}}}'}
 [pscustomobject]@{Text=$text}
}
function Read-HeldText {param($Stream)$Stream.Text}
function Assert-R2VenvBinding {param($Text)}
function Assert-ScopedConfigDelta {param($Original,$Candidate)}
function Get-RuntimeFiles {param($Manifest)for($n=0;$n -lt 166;$n++){[pscustomobject]@{destination=('C:\fixture-source\'+$n);sha256='fixture'}}}
function Get-ExactTaskOrAbsent {param($Name)$null}
function Assert-StoppedDaemons {}
function Assert-CodeTreeOnce {param($Path)}
function Assert-PreservedTask {param($Task,$Root,$File,$Arguments,$Result)}
$fakeScheduler=[pscustomobject]@{}
$fakeScheduler|Add-Member ScriptMethod Connect {}
$fakeScheduler|Add-Member ScriptMethod GetFolder {param($Path)[pscustomobject]@{}}
$fakeScheduler|Add-Member ScriptMethod NewTask {param($Flags)throw 'SIMULATED_TASK_BOUNDARY_REACHED'}
function New-Object {param($ComObject)if($ComObject -ne 'Schedule.Service'){throw 'Unexpected COM fixture'};$fakeScheduler}
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($wrapper,[ref]$tokens,[ref]$errors)
$main=@($ast.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.TryStatementAst]})
if($main.Count -ne 1){throw 'Exactly one full Apply try body expected'}
$reached=$false
try{. ([scriptblock]::Create($main[0].Extent.Text))}catch{if($_.Exception.Message -notmatch 'SIMULATED_TASK_BOUNDARY_REACHED'){throw};$reached=$true}
if(-not $reached -or @(Get-ChildItem -LiteralPath $root -Force).Count -ne 3){throw 'Full Apply path failed before task boundary'}
foreach($record in @(Get-ResumeCopyRecords)){
 if((Get-FileHash -LiteralPath (Join-Path $root $record.Name)).Hash.ToLowerInvariant() -cne $record.Sha256){throw 'Full Apply copied wrong bytes'}
}
'EXACT_APPLY_BODY_REACHED_SIMULATED_TASK_WITH_THREE_FILES'
"""
    result=run(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'EXACT_APPLY_BODY_REACHED_SIMULATED_TASK_WITH_THREE_FILES' in result.stdout


def test_frozen_prior_helpers_untouched_and_python_target_unchanged():
    expected={'resume-private-knowledge.py':'780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b',
              'resume-private-knowledge.ps1':'fdfbd0e313acdb75047d5ef51768654c58c9193991607e04d08c7dc35590bdfa'}
    for name,pin in expected.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==pin
    raw=WRAPPER.read_text()
    assert 'New-PrivateDirectory $root' not in raw and 'Remove-Item' not in raw
    assert '$root=\'C:\\Program Files\\CoChem\\KnowledgeResume4.2.7-windows-20261007-r2\'' in raw
    assert 'existing_root_private_empty_check_deferred=$true' in raw


@pytest.mark.parametrize('flags,accepted',[('ContainerInherit, ObjectInherit',True),('None',False)])
def test_native_acl_rule_inheritance_gate(tmp_path,flags,accepted):
    incoming,destination=setup(tmp_path)
    body=script_prefix(incoming,destination)+f"""
foreach($text in @(Read-FunctionText $wrapper @('Assert-ResumeRootInheritance'))){{. ([scriptblock]::Create($text))}}
$fixtureAcl=[Security.AccessControl.DirectorySecurity]::new()
foreach($sidText in @('S-1-5-18','S-1-5-32-544')){{
 $sid=[Security.Principal.SecurityIdentifier]::new($sidText)
 $fixtureAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',[Security.AccessControl.InheritanceFlags]'{flags}',[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
}}
function Get-Acl {{param($LiteralPath)$fixtureAcl}}
$accepted=$true;try{{Assert-ResumeRootInheritance $root}}catch{{$accepted=$false}}
if($accepted -ne ${str(accepted).lower()}){{throw 'Wrong ACL inheritance result'}}
'ACL_RULES_VERIFIED'
"""
    result=run(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
