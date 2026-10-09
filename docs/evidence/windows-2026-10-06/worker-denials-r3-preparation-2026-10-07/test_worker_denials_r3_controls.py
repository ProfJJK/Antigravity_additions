"""R3 Windows wrapper regressions. No privileged execution or real task/device."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).parent
LEAF=ROOT/'check-worker-denials-r3.ps1'
BATCH=ROOT/'check-all-worker-denials-r3.ps1'
PS=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
COPY=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')


def q(value):return "'"+str(value).replace("'","''")+"'"


def definitions(path):
    return r"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
function Read-Functions {
 param([string]$Path,[string[]]$Names=@())
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e)
 if($e.Count){throw 'Parse errors'}
 foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
  if(-not $Names.Count -or $f.Name -in $Names){$f.Extent.Text}
 }
}
"""+f"foreach($f in @(Read-Functions {q(path)})){{. ([scriptblock]::Create($f))}}\n"


def run(tmp_path,body):
    path=tmp_path/'fixture.ps1';path.write_text(body,encoding='utf-8')
    result=subprocess.run([str(PS),'-NoProfile','-File',str(path)],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    return result


@pytest.mark.parametrize('exit_code',[0,2])
def test_actual_native_stderr_does_not_interrupt_explicit_exit_or_hide_summary(tmp_path,exit_code):
    child=tmp_path/'native-child.ps1'
    child.write_text("param($Slot,[switch]$Apply)\n[Console]::Error.WriteLine('SANITIZED_DIAGNOSTIC')\n'{\"status\":\"DENIAL_CHECK_FAILED\",\"failure\":{\"phase\":\"worker_launch\",\"error_type\":\"ResourcePolicyError\",\"winerror\":87}}'\nexit "+str(exit_code),encoding='utf-8')
    result=run(tmp_path,definitions(BATCH)+f"""
$caught=$false
try{{$value=Invoke-NativeDenialWrapper {q(PS)} {q(child)} 'slot1'}}catch{{$caught=$true;'EXPLICIT_FAILURE_CAPTURED'}}
if($ErrorActionPreference -cne 'Stop'){{throw 'Caller error preference was not restored'}}
if(-not $caught){{throw 'Mixed stderr cannot become successful JSON'}}
""")
    if exit_code:
        assert 'worker_launch' in result.stdout and 'ResourcePolicyError' in result.stdout
    assert 'EXPLICIT_FAILURE_CAPTURED' in result.stdout


def test_native_success_json_and_caller_preference_restored(tmp_path):
    child=tmp_path/'native-child.ps1';child.write_text("param($Slot,[switch]$Apply)\n'{\"status\":\"OK\"}'\nexit 0",encoding='utf-8')
    result=run(tmp_path,definitions(BATCH)+f"""
$result=Invoke-NativeDenialWrapper {q(PS)} {q(child)} 'slot1'
if($result.status -cne 'OK' -or $ErrorActionPreference -cne 'Stop'){{throw 'Successful JSON or preference restoration differs'}}
'NATIVE_JSON_VERIFIED'
""")
    assert 'NATIVE_JSON_VERIFIED' in result.stdout


OLD_TASK=r"""
$receipt=[pscustomobject]@{nonce='06478e2f265546cd8257aa09111373f4'}
$oldRoot='C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261006-slot1'
$action=[pscustomobject]@{Type=0;Path='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Scripts\python.exe';Arguments=('-I -B "'+$oldRoot+'\worker-denial-acceptance.py" --slot slot1 --nonce '+$receipt.nonce);WorkingDirectory=$oldRoot}
$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($Index)$action}
$task=[pscustomobject]@{State=3;LastTaskResult=2;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
$instances=0;$task|Add-Member ScriptMethod GetInstances {param($Flags)[pscustomobject]@{Count=$instances}}
"""


@pytest.mark.parametrize('change,accepted',[('',True),("$task=$null",False),("$task.State=4",False),("$task.State=0",False),("$task.LastTaskResult=0",False),("$instances=1",False),("$action.Arguments+='x'",False),("$action.Path='C:\\wrong.exe'",False),("$task.Definition.Triggers.Count=1",False)])
def test_exact_original_failed_task_gate(tmp_path,change,accepted):
    run(tmp_path,definitions(LEAF)+OLD_TASK+change+f"""
$accepted=$true;try{{Assert-OriginalFailedDenialTask $task $receipt}}catch{{$accepted=$false}}
if($accepted -ne ${str(accepted).lower()}){{throw 'Original terminal failed-task gate differs'}}
""")


def test_full_apply_body_real_copy_then_simulated_exact_r3_task_registration(tmp_path):
    source=ROOT/'worker-denial-acceptance-r3.py'
    fixture_native=tmp_path/'fixture-native.exe';fixture_native.write_bytes(b'not executed')
    install=tmp_path/'installed';install.mkdir()
    for name in ('python.exe','layout.json','pipeline.json'):(install/name).write_bytes(b'fixture')
    target=tmp_path/'new-root'
    body=definitions(LEAF)+rf"""
foreach($f in @(Read-Functions {q(COPY)} @('Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){{. ([scriptblock]::Create($f))}}
Initialize-FileIdentity
$source={q(source)};$sourceHash={q(hashlib.sha256(source.read_bytes()).hexdigest())};$sourceLength=(Get-Item $source).Length
$native={q(fixture_native)};$nativeHash={q(hashlib.sha256(fixture_native.read_bytes()).hexdigest())}
$installRoot={q(install)};$targetRoot={q(target)};$python=Join-Path $installRoot 'python.exe';$layout=Join-Path $installRoot 'layout.json';$config=Join-Path $installRoot 'pipeline.json'
$Slot='slot1';$taskName='FIXTURE-NO-REAL-TASK';$Apply=$true;$identity=[pscustomobject]@{{Name='fixture'}};$admin=$false
$held=[Collections.Generic.List[IO.FileStream]]::new()
"""+r"""
function Assert-NoReparseAncestors {param($Path)}
function Assert-ProtectedPath {param($Path)}
function Assert-CodeTreeOnce {param($Path)1}
function New-ProtectedDirectory {param($Path)$null=[IO.Directory]::CreateDirectory($Path)}
function New-CodeAcl {
 param([bool]$Directory)
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$acl=[Security.AccessControl.FileSecurity]::new()
 $acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));$acl
}
$script:priorChecks=0
function Assert-OriginalFailedDenial {param([switch]$RequireTask)if(-not $RequireTask){throw 'Full Apply did not require prior terminal task'};$script:priorChecks++;[ordered]@{old_cleanup_verified=$false}}
function Assert-R3InstalledBindings {[ordered]@{install_receipt_sha256=('d'*64);source_manifest_sha256=('e'*64);configuration_sha256=('f'*64);revision=[pscustomobject]@{source_sha256=('a'*64)}}}
$realTestPath=(Get-Command Test-Path).Name
function Test-Path {param($LiteralPath,$PathType)if($LiteralPath -eq (Join-Path $installRoot 'install-after.json')){return $true};if($PathType){Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath -PathType $PathType}else{Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath}}
$script:action=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''}
$actions=[pscustomobject]@{};$actions|Add-Member ScriptMethod Create {param($Type)if($Type -ne 0){throw 'Not exec action'};$script:action}
$script:definition=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;ExecutionTimeLimit=''};Actions=$actions;Triggers=@()}
$fakeFolder=[pscustomobject]@{}
$fakeFolder|Add-Member ScriptMethod GetTask {param($Name)throw [Runtime.InteropServices.COMException]::new('fixture absent',-2147024894)}
$fakeFolder|Add-Member ScriptMethod RegisterTaskDefinition {
 param($Name,$Definition,$Flags,$User,$Password,$Logon,$Sddl)
 if($script:priorChecks -ne 2 -or $Name -cne $taskName -or $Flags -ne 2 -or $User -cne 'SYSTEM' -or $Logon -ne 5 -or $null -ne $Password -or $Sddl -cne 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'){throw 'Precondition/create-only binding differs'}
 if($Definition.Principal.UserId -cne 'SYSTEM' -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Principal.LogonType -ne 5 -or $Definition.Triggers.Count -ne 0 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT3M' -or $Definition.Settings.MultipleInstances -ne 2){throw 'Task identity/lifetime differs'}
 $prefix='-I -B "'+(Join-Path $targetRoot 'worker-denial-acceptance-r3.py')+'" --slot slot1 --nonce '
 if($script:action.Path -cne $python -or $script:action.WorkingDirectory -cne $targetRoot -or -not $script:action.Arguments.StartsWith($prefix) -or $script:action.Arguments.Substring($prefix.Length) -cnotmatch '^[a-f0-9]{32} --install-receipt-sha256 d{64}$'){throw 'Actual r3 launch argv differs'}
 throw 'SIMULATED_R3_REGISTRATION_BOUNDARY'
}
$fakeScheduler=[pscustomobject]@{};$fakeScheduler|Add-Member ScriptMethod Connect {}
$fakeScheduler|Add-Member ScriptMethod GetFolder {param($Path)$fakeFolder}
$fakeScheduler|Add-Member ScriptMethod NewTask {param($Flags)$script:definition}
function New-Object {param($ComObject)if($ComObject -cne 'Schedule.Service'){throw 'Unexpected COM call'};$fakeScheduler}
"""+f"$leaf={q(LEAF)}\n"+r"""
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($leaf,[ref]$tokens,[ref]$errors)
$blocks=@($ast.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.TryStatementAst]})
if($blocks.Count -ne 2){throw 'Unexpected full Apply block structure'}
$reached=$false
try{. ([scriptblock]::Create($blocks[-1].Extent.Text))}catch{if($_.Exception.Message -notmatch 'SIMULATED_R3_REGISTRATION_BOUNDARY'){throw};$reached=$true}
if(-not $reached -or (Get-FileHash -LiteralPath (Join-Path $targetRoot 'worker-denial-acceptance-r3.py')).Hash.ToLowerInvariant() -cne $sourceHash){throw 'Full Apply did not preserve verified exclusive copy'}
'EXACT_APPLY_REAL_COPY_AND_R3_TASK_ARGV_VERIFIED'
"""
    result=run(tmp_path,body)
    assert 'EXACT_APPLY_REAL_COPY_AND_R3_TASK_ARGV_VERIFIED' in result.stdout
    assert (target/source.name).read_bytes()==source.read_bytes()


def r3_binding_fixture(tmp_path):
    """Actual frozen source/asset bytes; synthetic future receipt, user-only tree."""
    repo=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
    prior=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2')
    root=tmp_path/'r3-fixture';root.mkdir()
    manifest_raw=(ROOT/'stopped-runtime-r3-source-manifest.json').read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest()=='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
    manifest=json.loads(manifest_raw)
    (root/'source-manifest.json').write_bytes(manifest_raw)
    for row in manifest['files']:
        raw=(repo/row['relative']).read_bytes()
        assert len(raw)==row['length'] and hashlib.sha256(raw).hexdigest()==row['sha256']
        dst=root/'source'/row['relative'];dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
        key=Path(row['relative'])
        if len(key.parts)>2 and key.parts[0]=='src' and key.parts[1] in ('cochem_pipeline','cochem_mcp','cochem_supervisor') and key.suffix in ('.py','.md','.json','.xml'):
            dst=root/'.venv/Lib/site-packages'/Path(*key.parts[1:]);dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
    for key in ('pipeline.json','windows-layout.json','.venv/pyvenv.cfg','.venv/Scripts/python.exe'):
        dst=root/key;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(prior/key,dst)
    after=json.loads((prior/'install-after.json').read_bytes())
    after.update(target_root=str(manifest['target_root']),source_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
      previous_runtime_root=str(prior),previous_install_receipt_sha256='d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4',
      previous_source_manifest_sha256='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8',configuration_unchanged=True,
      only_config_change=None,preserved_denial_receipt_sha256='691c70560436f948464c529b9d2d6a3e33ce240749642af86ca79c748791461d',
      preserved_denial_task_terminal_check_deferred=False,preserved_worker_cleanup_verified=False,
      runtime_bindings=dict(pyvenv_sha256='0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d',
        venv_python_sha256='560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e',
        base_python_sha256='d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa',
        base_root=r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'))
    after['verification']['revision']['source_sha256']='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
    (root/'install-after.json').write_text(json.dumps(after),encoding='utf-8')
    return root,after


@pytest.mark.parametrize('change',[None,'source','asset','manifest','receipt_revision','venv'])
def test_real_r3_manifest_source_asset_and_receipt_binding_gate(tmp_path,change):
    root,after=r3_binding_fixture(tmp_path)
    if change in ('source','asset','manifest','venv'):
        relative={'source':'source/src/cochem_pipeline/resource_limits.py','asset':'.venv/Lib/site-packages/cochem_pipeline/resource_limits.py','manifest':'source-manifest.json','venv':'.venv/pyvenv.cfg'}[change]
        with (root/relative).open('ab') as stream:stream.write(b'\ndrift')
    elif change=='receipt_revision':
        after['verification']['revision']['source_sha256']='a'*64
        (root/'install-after.json').write_text(json.dumps(after),encoding='utf-8')
    body=definitions(LEAF)+rf"""
foreach($f in @(Read-Functions {q(COPY)} @('Assert-NoReparseAncestors','Initialize-FileIdentity','Open-VerifiedFile'))){{. ([scriptblock]::Create($f))}}
Initialize-FileIdentity;$held=[Collections.Generic.List[IO.FileStream]]::new()
$fixtureRoot={q(root)};$virtualRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$actualRead=(Get-Command Read-R3Control).ScriptBlock
function Assert-ProtectedPath {{param($Path)}}
function Read-R3Control {{
 param([string]$Path,[string]$Hash='',[long]$Maximum=16777216)
 if(-not $Path.StartsWith($virtualRoot+'\')){{throw 'Fixture refuses non-r3 read'}}
 $mapped=Join-Path $fixtureRoot $Path.Substring($virtualRoot.Length+1)
 & $actualRead $mapped $Hash $Maximum
}}
$accepted=$true;$failure=''
try{{$binding=Assert-R3InstalledBindings}}catch{{$accepted=$false;$failure=$_.Exception.Message}}finally{{foreach($stream in $held){{$stream.Dispose()}}}}
if($accepted -ne ${str(change is None).lower()}){{throw ('R3 source/asset/receipt gate result differs: '+$failure)}}
if($accepted -and ($binding.source_files_verified -ne 166 -or $binding.revision.files -ne 109 -or $binding.install_receipt_sha256 -cne (Get-FileHash (Join-Path $fixtureRoot 'install-after.json')).Hash.ToLowerInvariant())){{throw 'Dynamic receipt commitment differs'}}
"""
    run(tmp_path,body)
