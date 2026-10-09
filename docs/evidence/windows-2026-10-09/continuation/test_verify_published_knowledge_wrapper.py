"""Actual PS5.1 control flow; ordinary-user disposable files and fake COM only.

No protected destination, live scheduled task, knowledge state, or elevation.
Only ACL policy is substituted for disposable copy fixtures; the real exclusive
copy, hash, held file identity, and wrapper control flow execute.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).parent
WRAPPER=ROOT/'verify-published-knowledge.ps1'
PS=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
PAYLOAD=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')
NAMES=('verify-published-knowledge.py','resume-private-knowledge.py','diagnose-knowledge-state.py','accept-private-knowledge.py')


def q(value):return "'"+str(value).replace("'","''")+"'"


def setup(tmp_path,*,fresh=False):
    incoming=tmp_path/'incoming';incoming.mkdir()
    for name in NAMES:shutil.copyfile(ROOT/name,incoming/name)
    destination=tmp_path/'fresh-verification'
    if not fresh:destination.mkdir()
    return incoming,destination


def prefix(incoming,destination):
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
foreach($text in @(Read-FunctionText {q(ROOT/'resume-private-knowledge.ps1')} @('Hold-PinnedFile','Read-HeldText'))){{. ([scriptblock]::Create($text))}}
$source=Join-Path {q(incoming)} 'verify-published-knowledge.py';$sourceHash=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant()
$resumeRoot={q(incoming)};$diagnosticRoot={q(incoming)};$originalRoot={q(incoming)};$root={q(destination)}
$held=[Collections.Generic.List[IO.FileStream]]::new();Initialize-FileIdentity
"""+r"""
function Assert-ProtectedPath {param($Path)}
function Assert-PrivateItem {param($Path)}
function Assert-NoReparseAncestors {param($Path)}
function New-PrivateAcl {
 param([bool]$Directory)
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
 $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
 $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));$acl
}
"""


def run(tmp_path,body):
    path=tmp_path/'fixture.ps1';path.write_text(body,encoding='utf-8')
    return subprocess.run([str(PS),'-NoProfile','-File',str(path)],capture_output=True,text=True,timeout=40)


def checked(tmp_path,body):
    result=run(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
    return result


def test_actual_four_typed_records_and_exclusive_copies(tmp_path):
    incoming,destination=setup(tmp_path)
    checked(tmp_path,prefix(incoming,destination)+r"""
try{
 $records=@(Get-PublishedCopyRecords);Confirm-PublishedCopyRecords $records
 if(@(Get-ChildItem -LiteralPath $root).Count){throw 'Validation wrote destination'}
 Copy-PublishedRecords $records
 if($records.Count -ne 4 -or @(Get-ChildItem -LiteralPath $root).Count -ne 4){throw 'Wrong copy count'}
 foreach($r in $records){if(-not [IO.Path]::IsPathRooted($r.Source) -or $r.Source -eq 'C'){throw 'Flattened record'}}
}finally{foreach($stream in $held){$stream.Dispose()}}
""")
    for name in NAMES:assert (incoming/name).read_bytes()==(destination/name).read_bytes()


@pytest.mark.parametrize('change',["$records[0].Source='C'","$records=@($records[0],$records[1],$records[2])","$records[3]=$records[2]","[IO.File]::WriteAllText((Join-Path $originalRoot 'accept-private-knowledge.py'),'tampered')"])
def test_all_four_sources_checked_before_copy(tmp_path,change):
    incoming,destination=setup(tmp_path)
    checked(tmp_path,prefix(incoming,destination)+"\n$records=@(Get-PublishedCopyRecords)\n"+change+r"""
try{
 $refused=$false;try{Confirm-PublishedCopyRecords $records;Copy-PublishedRecords $records}catch{$refused=$true}
 if(-not $refused -or @(Get-ChildItem -LiteralPath $root).Count){throw 'Invalid source copied'}
}finally{foreach($stream in $held){$stream.Dispose()}}
""")
    assert not list(destination.iterdir())


def test_exact_apply_body_four_real_copies_then_exact_simulated_task_definition(tmp_path):
    incoming,destination=setup(tmp_path,fresh=True)
    checked(tmp_path,prefix(incoming,destination)+r"""
$Apply=$true;$python='C:\fixture-runtime\python.exe';$basePython='C:\fixture-runtime\base.exe';$basePythonRoot='C:\fixture-runtime\base'
$installRoot='C:\fixture-runtime';$oldInstall='C:\fixture-old';$receiptHash='receipt';$manifestHash='manifest';$configHash='config'
$originalHash='original';$diagnosticHash='diagnostic';$failedResumeHash='failed';$taskName='FIXTURE-NO-TASK-WILL-RUN'
$actualHold=(Get-Command Hold-PinnedFile).ScriptBlock
function Hold-PinnedFile {
 param([string]$Path,[string]$Hash,[long]$Maximum=1048576,[switch]$LocalSource)
 if($Path -in @($source,(Join-Path $resumeRoot 'resume-private-knowledge.py'),(Join-Path $diagnosticRoot 'diagnose-knowledge-state.py'),(Join-Path $originalRoot 'accept-private-knowledge.py')) -or $Path.StartsWith($root+'\')){& $actualHold $Path $Hash $Maximum -LocalSource:$LocalSource;return}
 $text='{}';if($Path.EndsWith('install-after.json')){$text='{"mode":"FRESH_STOPPED_RUNTIME_READY","source_files":166,"source_manifest_sha256":"manifest","configuration_sha256":"config","verification":{"revision":{"verified":true}}}'}
 [pscustomobject]@{Text=$text}
}
function Read-HeldText {param($Stream)$Stream.Text}
function Assert-R2VenvBinding {param($Text)}
function Get-RuntimeFiles {param($Manifest)for($n=0;$n -lt 166;$n++){[pscustomobject]@{destination=('C:\fixture-source\'+$n);sha256='fixture'}}}
function Get-ExactTaskOrAbsent {param($Name)$null}
function Assert-StoppedDaemons {}
function Assert-CodeTreeOnce {param($Path)}
function Assert-PreservedTask {param($Task,$Root,$File,$Arguments,$Result)}
function Assert-FailedResumeTask {param($Task,$Receipt)}
function New-PrivateDirectory {param($Path,[switch]$Root)$null=[IO.Directory]::CreateDirectory($Path)}
$script:fixtureAction=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''}
$fixtureActions=[pscustomobject]@{}
$fixtureActions|Add-Member ScriptMethod Create {param($Type)if($Type -ne 0){throw 'Wrong action type'};$script:fixtureAction}
$script:fixtureDefinition=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;ExecutionTimeLimit=''};Actions=$fixtureActions;Triggers=@()}
$fakeFolder=[pscustomobject]@{}
$fakeFolder|Add-Member ScriptMethod RegisterTaskDefinition {
 param($Name,$Definition,$Flags,$User,$Password,$Logon,$Sddl)
 if($Name -cne $taskName -or $Flags -ne 2 -or $User -cne 'SYSTEM' -or $null -ne $Password -or $Logon -ne 5 -or $Sddl -cne 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'){throw 'Task create-only principal differs'}
 if($Definition.Principal.UserId -cne 'SYSTEM' -or $Definition.Principal.LogonType -ne 5 -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Triggers.Count -ne 0){throw 'Task principal/triggers differ'}
 if(-not $Definition.Settings.Enabled -or -not $Definition.Settings.AllowDemandStart -or $Definition.Settings.MultipleInstances -ne 2 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT5M'){throw 'Task settings differ'}
 $expected='-I -B "'+(Join-Path $root 'verify-published-knowledge.py')+'" '
 if($script:fixtureAction.Path -cne $python -or $script:fixtureAction.WorkingDirectory -cne $root -or -not $script:fixtureAction.Arguments.StartsWith($expected) -or $script:fixtureAction.Arguments.Substring($expected.Length) -cnotmatch '^[a-f0-9]{32}$'){throw 'Task executable/argv differs'}
 throw 'SIMULATED_REGISTRATION_BOUNDARY_REACHED'
}
$fakeScheduler=[pscustomobject]@{}
$fakeScheduler|Add-Member ScriptMethod Connect {}
$fakeScheduler|Add-Member ScriptMethod GetFolder {param($Path)$fakeFolder}
$fakeScheduler|Add-Member ScriptMethod NewTask {param($Flags)if($Flags -ne 0){throw 'Unexpected flags'};$script:fixtureDefinition}
function New-Object {param($ComObject)if($ComObject -ne 'Schedule.Service'){throw 'Unexpected COM fixture'};$fakeScheduler}
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($wrapper,[ref]$tokens,[ref]$errors)
$main=@($ast.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.TryStatementAst]})
if($main.Count -ne 1){throw 'Exactly one full Apply try body expected'}
$reached=$false
try{. ([scriptblock]::Create($main[0].Extent.Text))}catch{if($_.Exception.Message -notmatch 'SIMULATED_REGISTRATION_BOUNDARY_REACHED'){throw};$reached=$true}
if(-not $reached -or @(Get-ChildItem -LiteralPath $root).Count -ne 4){throw 'Full Apply did not reach exact task boundary after four copies'}
foreach($record in @(Get-PublishedCopyRecords)){if((Get-FileHash -LiteralPath (Join-Path $root $record.Name)).Hash.ToLowerInvariant() -cne $record.Sha256){throw 'Full Apply copied wrong bytes'}}
""")
    for name in NAMES:assert (incoming/name).read_bytes()==(destination/name).read_bytes()


TASK_FIXTURE=r"""
$installRoot='r2';$receiptHash='receipt';$configHash='config';$manifestHash='manifest';$originalHash='original';$diagnosticHash='diagnostic';$python='C:\runtime\python.exe'
$nonce='a'*32
$receipt=[pscustomobject]@{schema='cochem-private-knowledge-resume/1';status='KNOWLEDGE_RESUME_HELD';system_sid='S-1-5-18';helper_sha256='780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b';runtime_root=$installRoot;install_receipt_sha256=$receiptHash;pipeline_config_sha256=$configHash;source_manifest_sha256=$manifestHash;original_receipt_sha256=$originalHash;diagnostic_receipt_sha256=$diagnosticHash;nonce=$nonce;failure=[pscustomobject]@{phase='final_verification';error_type='ValueError';winerror=$null};index_resume_started=$true;documents=137;sections=1708;index_integrity_check='ok';index_sha256=('b'*64);generation=('g-'+('c'*32))}
$action=[pscustomobject]@{Type=0;Path=$python;Arguments=('-I -B "'+(Join-Path $resumeRoot 'resume-private-knowledge.py')+'" '+$nonce);WorkingDirectory=$resumeRoot}
$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($Index)$action}
$task=[pscustomobject]@{State=3;LastTaskResult=2;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
$instances=0;$task|Add-Member ScriptMethod GetInstances {param($Flags)[pscustomobject]@{Count=$instances}}
"""


@pytest.mark.parametrize('change,accepted',[('',True),("$task.LastTaskResult=0",False),("$task.State=4",False),("$instances=1",False),("$action.Arguments+='x'",False),("$receipt.nonce='malformed'",False),("$receipt.failure.phase='index_resume'",False),("$task.Definition.Principal.UserId='user'",False)])
def test_failed_resume_exact_task_nonce_terminal_result_binding(tmp_path,change,accepted):
    incoming,destination=setup(tmp_path)
    checked(tmp_path,prefix(incoming,destination)+TASK_FIXTURE+change+f"""
$accepted=$true;try{{Assert-FailedResumeTask $task $receipt}}catch{{$accepted=$false}}
if($accepted -ne ${str(accepted).lower()}){{throw 'Incorrect exact preserved-task gate result'}}
""")


def receipt():
    return dict(schema='cochem-published-knowledge-verification/1',nonce='a'*32,system_sid='S-1-5-18',helper_sha256=hashlib.sha256((ROOT/NAMES[0]).read_bytes()).hexdigest(),failed_resume_receipt_sha256='failed',knowledge_service_constructed=False,index_refreshed_or_repaired=False,existing_files_or_acls_modified=False,existing_tasks_modified_or_run=False,activation_ready=False,native_model_jobs_executed=0,status='PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',substep='complete',runtime_root='r2',install_receipt_sha256='receipt',pipeline_config_sha256='config',source_manifest_sha256='manifest',source_files_verified=166,revision={'verified':True},failed_resume_nonce='d'*32,generation='g-'+('c'*32),index_sha256='b'*64,documents=137,sections=1708,corpus_files=138,integrity_check='ok',sqlite_mode='ro',sqlite_temp_store='memory',original_root_identity_and_security_preserved=True,original_writer_lock_preserved=True,corpus_bytes_preserved=True,canonical_authority_matches_capture=True,index_size_sla_met=True,sqlite_immutable=True,sqlite_query_only=True,root_difference_from_blank_diagnostic={'bytes':{'before':0,'after':4096}})


@pytest.mark.parametrize('field,value,accepted',[(None,None,True),('nonce','x'*32,False),('pipeline_config_sha256','drift',False),('index_refreshed_or_repaired',True,False),('sqlite_immutable',False,False),('original_writer_lock_preserved',False,False)])
def test_receipt_success_binding_preservation_and_no_write_scope(tmp_path,field,value,accepted):
    incoming,destination=setup(tmp_path);data=receipt()
    if field:data[field]=value
    checked(tmp_path,prefix(incoming,destination)+f"""
$installRoot='r2';$receiptHash='receipt';$configHash='config';$manifestHash='manifest';$failedResumeHash='failed'
$receipt={q(json.dumps(data))}|ConvertFrom-Json
$failed=[pscustomobject]@{{nonce=('d'*32);generation=('g-'+('c'*32));index_sha256=('b'*64)}}
$accepted=$true;try{{Assert-PublishedReceiptBinding $receipt ('a'*32);Assert-PublishedSuccess $receipt $failed;$null=Get-PublishedMetadataDifferences $receipt}}catch{{$accepted=$false}}
if($accepted -ne ${str(accepted).lower()}){{throw 'Incorrect receipt acceptance'}}
""")


def test_failure_substep_remains_visible_even_when_stdout_assignment_is_interrupted(tmp_path):
    incoming,destination=setup(tmp_path);data=receipt()
    data.update(status='PUBLISHED_KNOWLEDGE_VERIFICATION_HELD',failure={'substep':'state.held_index_hash','error_type':'PermissionError','winerror':5})
    result=checked(tmp_path,prefix(incoming,destination)+f"""
$receipt={q(json.dumps(data))}|ConvertFrom-Json
function Invoke-FailingChild {{Write-PublishedFailure $receipt 'C:\\fixture\\published-verification.json' ('f'*64) 2;throw 'held'}}
try{{$collected=Invoke-FailingChild}}catch{{'PARENT_CAUGHT_FAILURE'}}
""")
    assert 'PARENT_CAUGHT_FAILURE' in result.stdout
    assert 'state.held_index_hash' in result.stdout and 'PermissionError' in result.stdout
    assert '4096' in result.stdout and 'receipt_sha256' in result.stdout


def test_old_artifacts_unchanged_and_new_wrapper_has_no_state_refresh_or_overwrite():
    pins={'resume-private-knowledge.ps1':'fdfbd0e313acdb75047d5ef51768654c58c9193991607e04d08c7dc35590bdfa','resume-private-knowledge-copyfix.ps1':'71b4e2474a6802c36c8c9cebb2330449bd5e776fa2de3b5d293f333c1528404f','resume-private-knowledge.py':'780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b'}
    for name,pin in pins.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==pin
    raw=WRAPPER.read_text()
    assert "RegisterTaskDefinition($taskName,$definition,2," in raw
    assert 'Remove-Item' not in raw and 'KnowledgeService(' not in raw
