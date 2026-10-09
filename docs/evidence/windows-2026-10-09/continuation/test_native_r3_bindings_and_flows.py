"""Read-only installed binding tests and disposable simulated task/auth flows only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

W = Path(__file__).parent
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

def module(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),W/name)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

status=module('worker-native-status-r3.py')
bridge=module('worker_claude_login_bridge_r3.py')

def ps(code,timeout=45):
    prefix="$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME \"Modules\\$m\\$m.psd1\")};"
    return subprocess.run([PS,'-NoProfile','-NonInteractive','-Command',prefix+code],capture_output=True,text=True,timeout=timeout)

def functions(path):
    return f"$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors);if($errors.Count){{throw $errors[0]}};foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};"

def test_actual_r3_installed_rows_and_config_are_exact():
    manifest=json.loads((INSTALL/'source-manifest.json').read_bytes())
    receipt=json.loads((INSTALL/'install-after.json').read_bytes())
    assert hashlib.sha256((INSTALL/'install-after.json').read_bytes()).hexdigest()==status.INSTALL_RECEIPT_SHA256
    seen=[]
    def check(path,pin,length):
        raw=path.read_bytes();assert len(raw)==length and hashlib.sha256(raw).hexdigest()==pin;seen.append(path)
    status.verify_r3_source(manifest,check)
    assert len(seen)==166
    assert receipt['verification']['revision']['files']==109
    assert receipt['verification']['revision']['source_sha256']==status.REVISION_SHA256
    assert hashlib.sha256((INSTALL/'.venv/Lib/site-packages/cochem_pipeline/resource_limits.py').read_bytes()).hexdigest()==status.RESOURCE_SHA256
    cfg=json.loads((INSTALL/'pipeline.json').read_bytes())
    assert len(cfg['workers'])==6 and cfg['max_execution_slots']==4
    assert cfg['ramdisk']['workspace_subdirectory']=='CoChem427-windows-20261007'
    assert hashlib.sha256((INSTALL/'pipeline.json').read_bytes()).hexdigest()==status.CONFIG_SHA256

@pytest.mark.parametrize('change',['duplicate','escape','count','length','root'])
def test_source_freeze_refuses_malformed_or_wrong_target(change):
    value=json.loads((INSTALL/'source-manifest.json').read_bytes())
    if change=='duplicate':value['files'][-1]=value['files'][0]
    elif change=='escape':value['files'][0]['relative']='src/../outside'
    elif change=='count':value['files'].pop()
    elif change=='length':value['files'][0]['length']=True
    elif change=='root':value['target_root']=str(INSTALL).replace('-r3','-r2')
    with pytest.raises(ValueError):status.verify_r3_source(value,lambda *a:None)

@pytest.mark.parametrize('change',['none','old','base','site','version','extra'])
def test_exact_uv_schema_against_actual_installed_bytes(change):
    code=functions(W/'check-worker-native-status-r3.ps1')
    code+=f"$basePythonRoot='{status.BASE_PYTHON.parent}';$text=[IO.File]::ReadAllText('{INSTALL/'.venv/pyvenv.cfg'}');"
    changes={'none':'','old':"$text='home = elsewhere`nversion = 3.12.13`nexecutable = old';",'base':"$text=$text.Replace('Python312','different');",'site':"$text=$text.Replace('packages = false','packages = true');",'version':"$text=$text.Replace('3.12.13','3.12.12');",'extra':"$text+=\"`nexecutable = extra\";"}
    result=ps(code+changes[change]+'Assert-VenvBinding $text')
    assert (result.returncode==0)==(change=='none'),result.stderr

def test_positive_status_launch_passes_limits_and_exact_argv_without_running_provider():
    calls=[]
    win=SimpleNamespace(_powershell=lambda *a:None,launch_worker=lambda *a,**kw:calls.append((a,kw)) or 'simulated')
    limits=object();args=[str(status.NATIVE/'codex.exe'),*status.CONTRACTS['codex']['arguments']]
    assert status.launch_status_process(win,'identity',args,'cwd',None,None,None,limits,{})=='simulated'
    assert calls[0][0][1]==args and calls[0][1]['limits'] is limits

def test_batch_native_stderr_is_not_forwarded_and_exit_is_inspected(tmp_path):
    child=tmp_path/'child.ps1';child.write_text("param($Slot,$Provider,[switch]$Apply)\n[Console]::Error.WriteLine('synthetic-private-value');exit 2\n")
    result=ps(functions(W/'check-all-worker-native-status-r3.ps1')+f"try{{Invoke-StatusWrapper '{PS}' '{child}' slot1 codex;throw 'should fail'}}catch{{[ordered]@{{message=$_.Exception.Message;preference=[string]$ErrorActionPreference}}|ConvertTo-Json -Compress}}")
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert 'did not complete successfully' in value['message'] and value['preference']=='Stop'
    assert 'synthetic-private-value' not in result.stdout+result.stderr

@pytest.mark.parametrize('kind',['status','claude'])
def test_actual_apply_tail_copies_helpers_then_registers_and_accepts_simulated_task(tmp_path,kind):
    # Full action tail is executed, with only scheduler/auth and admin custody
    # boundaries substituted. Copies and hashes use real ordinary fixture files.
    path=W/('check-worker-native-status-r3.ps1' if kind=='status' else 'login_pipeline_worker_interactive_r3.ps1')
    text=path.read_text();start=text.index('$installedEntries=Assert-CodeTreeOnce') if kind=='status' else text.index('    $null=Assert-CodeTreeOnce $installRoot;')
    tail=text[start:text.rfind('}finally{foreach($stream in $held)')]
    if kind=='status':tail=tail.rstrip()  # status outer try's closing brace is in suffix
    else:
        old='$root="C:\\Program Files\\CoChem\\InteractiveClaude427-r3-$Slot-$nonce"'
        assert old in tail
        tail=tail.replace(old,'$root=(Join-Path $fixture "claude-$nonce")',1)
    fixture=tmp_path/'tail.ps1';fixture.write_text(tail)
    source=W/('worker-native-status-r3.py' if kind=='status' else 'worker_claude_login_bridge_r3.py')
    support=W/'worker-native-status-r3.py'
    code=functions(path)+f"$fixture='{tmp_path}';$tail='{fixture}';$kind='{kind}';$Slot='slot1';$Provider='codex';$installRoot='{INSTALL}';$python=Join-Path $installRoot '.venv\\Scripts\\python.exe';$basePythonRoot='{status.BASE_PYTHON.parent}';$native='{status.NATIVE/'codex.exe'}';$nativeRoot='{status.NATIVE}';$source='{source}';$support='{support}';$sourceHash='{hashlib.sha256(source.read_bytes()).hexdigest()}';$supportHash='{hashlib.sha256(support.read_bytes()).hexdigest()}';$sourceLength=(Get-Item $source).Length;$targetRoot=Join-Path $fixture 'status';$taskName='CoChem-4.2.7-NativeStatus-r3-slot1-codex';$held=[Collections.Generic.List[IO.FileStream]]::new();$runtime=[pscustomobject]@{{install_receipt_sha256='{status.INSTALL_RECEIPT_SHA256}';source_manifest_sha256='{status.MANIFEST_SHA256}';configuration_sha256='{status.CONFIG_SHA256}';revision=[pscustomobject]@{{verified=$true;source_sha256='{status.REVISION_SHA256}'}}}};"
    code+=r"""
    function Assert-CodeTreeOnce {param($Path) 1}
    function Assert-ProtectedPath {param($Path)}
    function Get-ExactTaskOrAbsent {param($Folder,$Name)return $null}
    function New-ProtectedDirectory {param($Path)$null=[IO.Directory]::CreateDirectory($Path)}
    function New-PrivateAcl {param([switch]$Directory)
      if($Directory){$a=[Security.AccessControl.DirectorySecurity]::new()}else{$a=[Security.AccessControl.FileSecurity]::new()}
      $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false)
      if($Directory){$r=[Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')}else{$r=[Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow')};$a.AddAccessRule($r);$a
    }
    function Open-VerifiedFile {param($Path,$Hash,$Length)
      if((Get-Item -LiteralPath $Path).Length -ne $Length -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Hash){throw 'Fixture byte drift'}
      [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    }
    function Copy-VerifiedPayload {param($Row)$s=Open-VerifiedFile $Row.source $Row.sha256 $Row.length;try{$o=[IO.File]::Open($Row.destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$s.CopyTo($o)}finally{$o.Dispose()}}finally{$s.Dispose()}}
    function Start-Sleep {param($Milliseconds)}
    $script:ran=0;$script:registered=0
    $instance=[pscustomobject]@{State=3};$instance|Add-Member ScriptMethod Refresh {return}
    $task=[pscustomobject]@{State=3;LastTaskResult=0};$task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=0}}
    $task|Add-Member ScriptMethod Run {param($x)
      $script:ran++;$r=if($kind -eq 'status'){$targetRoot}else{$root}
      $report=[ordered]@{schema=if($kind -eq 'status'){'cochem-worker-native-status/1'}else{'cochem-interactive-claude-login/1'};nonce=$nonce;slot=$Slot;provider=$Provider;system_sid='S-1-5-18';helper_sha256=$sourceHash;status=if($kind -eq 'status'){'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED'}else{'LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED'};cleanup_verified=$true;runtime_root=$installRoot;install_receipt_sha256=$runtime.install_receipt_sha256;source_manifest_sha256=$runtime.source_manifest_sha256;resource_limits_sha256='de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f';config_sha256=$runtime.configuration_sha256;layout_sha256='8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4';revision=$runtime.revision}
      $name=if($kind -eq 'status'){'worker-native-status.json'}else{'receipt.json'}
      [IO.File]::WriteAllText((Join-Path $r $name),($report|ConvertTo-Json -Depth 8));return $instance
    }
    $scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod NewTask {param($flags)
      $script:action=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''}
      $actions=[pscustomobject]@{};$actions|Add-Member ScriptMethod Create {param($kind)return $script:action}
      [pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;ExecutionTimeLimit=''};Actions=$actions}
    }
    $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$definition,$flags,$user,$password,$logon,$acl)
      if($flags -ne 2 -or $user -cne 'SYSTEM' -or $null -ne $password -or $definition.Actions.Create(0).Path -cne $python -or -not $script:action.Arguments.StartsWith('-I -B ')){throw 'Unexpected simulated task contract'}
      $script:registered++;return $task
    }
    try{. ([scriptblock]::Create([IO.File]::ReadAllText($tail)));if($script:ran -ne 1 -or $script:registered -ne 1){throw 'Simulated apply did not reach exactly one task'}}finally{foreach($s in $held){$s.Dispose()}}
    """
    result=ps(code)
    assert result.returncode==0,result.stderr
    folders=[p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(folders)==1
    copied=folders[0]/source.name
    assert copied.read_bytes()==source.read_bytes()
    if kind=='claude':assert (folders[0]/'worker_native_status_support.py').read_bytes()==support.read_bytes()
    assert 'STATUS_REQUIRED' in result.stdout if kind=='claude' else 'AUTHENTICATION_VERIFIED' in result.stdout


def failure_row():
    return {'schema':'cochem-worker-native-status-task-result/1','slot':'slot1','provider':'codex',
      'runtime_root':str(INSTALL),'install_receipt_sha256':status.INSTALL_RECEIPT_SHA256,
      'source_manifest_sha256':status.MANIFEST_SHA256,
      'receipt_path':r'C:\Program Files\CoChem\NativeStatus4.2.7-windows-20261007-r3-slot1-codex\worker-native-status.json',
      'receipt_sha256':'a'*64,'cleanup_verified':True,'last_task_result':2,'native_exit_code':1,
      'status':'AUTHENTICATION_NOT_VERIFIED','failure':{'phase':'native_result','error_type':'ValueError','winerror':None},
      'unexpected_raw_provider_text':'synthetic-private-value'}

@pytest.mark.parametrize('change',['none','path','slot','status','unsafe_failure','duplicate'])
def test_failed_leaf_summary_is_bound_and_allowlisted(change):
    row=failure_row()
    if change=='path':row['receipt_path']='C:\\different\\receipt.json'
    if change=='slot':row['slot']='slot2'
    if change=='status':row['status']='unreviewed secret'
    if change=='unsafe_failure':row['failure']['phase']='secret@email.invalid'
    text='COCHEM_NATIVE_STATUS_RESULT '+json.dumps(row)
    if change=='duplicate':text+='\n'+text
    text=text.replace("'","''")
    result=ps(functions(W/'check-all-worker-native-status-r3.ps1')+f"$r=Get-SanitizedStatusFailure '{text}' slot1 codex;if($null -eq $r){{'null'}}else{{$r|ConvertTo-Json -Depth 8 -Compress}}")
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    if change=='none':
        assert value['receipt_sha256']=='a'*64 and value['cleanup_verified'] is True
        assert value['failure']==row['failure'] and value['native_exit_code']==1
    else:assert value is None
    assert 'synthetic-private-value' not in result.stdout+result.stderr

def test_nonzero_native_wrapper_preserves_only_bound_failure_summary(tmp_path):
    child=tmp_path/'child.ps1'
    payload='COCHEM_NATIVE_STATUS_RESULT '+json.dumps(failure_row())
    child.write_text("param($Slot,$Provider,[switch]$Apply)\nWrite-Host '"+payload+"'\n[Console]::Error.WriteLine('synthetic-private-value');exit 2\n")
    result=ps(functions(W/'check-all-worker-native-status-r3.ps1')+f"try{{Invoke-StatusWrapper '{PS}' '{child}' slot1 codex;throw 'should fail'}}catch{{[ordered]@{{held=$true;message=$_.Exception.Message}}|ConvertTo-Json -Compress}}")
    assert result.returncode==0,result.stderr
    rows=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    assert rows[0]['schema']=='cochem-native-status-failure/1'
    assert rows[0]['receipt_sha256']=='a'*64 and rows[1]['held'] is True
    assert 'synthetic-private-value' not in result.stdout+result.stderr
