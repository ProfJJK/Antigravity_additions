"""Actual WinPS5.1 inert wrapper tests; no SYSTEM, Scheduler, provider or install."""
import json
from pathlib import Path
import subprocess

import pytest

WORK=Path(__file__).absolute().parent
SCRIPT=WORK/'install-held-supervisor-observation-r3-v1.ps1'


def run(body):
    prelude=f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){{Import-Module (Join-Path $PSHOME "Modules\\$m\\$m.psd1")}};
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    result=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-Command',prelude+body],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_native_witness_own_ordinary_process_exact_handle_filetime():
    value=run(r'''
      Initialize-HeldProcessWitness;$w=[CoChemHeldObservationProcess]::new($PID)
      try{$w.AssertLive();$result=@{pid_matches=($w.Pid -eq $PID);creation_exact=($w.CreationFiletime -eq (Get-Process -Id $PID).StartTime.ToFileTimeUtc());sid_exact=($w.Sid -ceq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value);image_exists=[IO.File]::Exists($w.Image)}}finally{$w.Dispose()}
      $refused=$false;try{$w.AssertLive()}catch{$refused=$true};$result.disposed_refused=$refused;$result|ConvertTo-Json -Compress
    ''')
    assert all(value.values())


@pytest.mark.parametrize('case',['valid','owner','untrusted_acl','action','demand','enabled','instances','restart','trigger'])
def test_actual_task_creation_and_boundary_checks(case):
    value=run(f"$case='{case}';"+rf'''
      $tokens=$null;$errors=$null;$a=[Management.Automation.Language.Parser]::ParseFile('{WORK/'register-stopped-warden-r3.ps1'}',[ref]$tokens,[ref]$errors)
      $f=@($a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-RegisteredTaskAcl'}},$true))[0];. ([scriptblock]::Create($f.Extent.Text))
    '''+r'''
      $script:python='C:\fixture\python.exe';$script:targetRoot='C:\fixture\new';$script:registered=$false
      $action=[pscustomobject]@{Type=0;Path='';Arguments='';WorkingDirectory=''};$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Create {param($n)$action};$actions|Add-Member ScriptMethod Item {param($n)$action}
      $trigger=[pscustomobject]@{Type=8;Enabled=$false};$triggers=[pscustomobject]@{Count=0};$triggers|Add-Member ScriptMethod Create {param($n)$this.Count=1;$trigger};$triggers|Add-Member ScriptMethod Item {param($n)$trigger}
      $def=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;RestartCount=0;ExecutionTimeLimit=''};Actions=$actions;Triggers=$triggers}
      $scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod NewTask {param($n)$def}
      $task=[pscustomobject]@{Definition=$def;Enabled=$false;State=1};$task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$(if($case -eq 'instances'){1}else{0})}}
      $task|Add-Member ScriptMethod GetSecurityDescriptor {param($n)if($case -eq 'owner'){'O:BUG:BUD:P(A;;FA;;;SY)(A;;FA;;;BA)'}elseif($case -eq 'untrusted_acl'){'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BU)'}else{'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'}}
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$d,$flags,$user,$password,$logon,$sddl)
        if($flags -ne 2 -or $user -cne 'SYSTEM' -or $null -ne $password -or $logon -ne 5 -or $sddl -cne 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'){throw 'bad registration'}
        $script:registered=$true
        switch($case){'action'{$action.Type=5}'demand'{$d.Settings.AllowDemandStart=$false}'enabled'{$task.Enabled=$true}'restart'{$d.Settings.RestartCount=1}'trigger'{$trigger.Type=1}}
        $task
      }
      function Get-StagingTaskOrAbsent {param($Folder,$Name)return $null}
      $refused=$false;try{$null=New-HeldObservationTask $scheduler $folder 'fixture' '-I -B fixed --observe' $true}catch{$refused=$true}
      @{refused=$refused;registered=$script:registered;arguments=$action.Arguments;boot=($trigger.Type -eq 8)}|ConvertTo-Json -Compress
    ''')
    assert value['registered'] and value['refused']==(case!='valid')
    assert value['arguments']=='-I -B fixed --observe'


@pytest.mark.parametrize('case',['success','provision_failure','copy_failure'])
def test_assembled_apply_order_and_partial_preservation(tmp_path,case):
    source=tmp_path/'helper.py';source.write_text('# inert exact helper\n')
    result=run(f"$fixture='{tmp_path}';$case='{case}';"+r'''
      $script:targetRoot=Join-Path $fixture 'new';$script:dataRoot=Join-Path $fixture 'data';$script:source=Join-Path $fixture 'helper.py';$script:sourceHash=('a'*64);$script:provisionTask='fixture-provision';$script:observerTask='fixture-observer';$script:python='C:\fixture\python.exe';$script:held=[Collections.Generic.List[IO.Stream]]::new();$script:events=[Collections.Generic.List[string]]::new()
      $inputValue=[pscustomobject]@{Packet=[ordered]@{schema='cochem-held-supervisor-inputs/1';staging_receipt_sha256=('b'*64);commissioning_receipt_sha256=('c'*64)}}
      function Assert-AbsentStagingPath {param($Path)if(Test-Path -LiteralPath $Path){throw 'exists'}}
      function Get-StagingTaskOrAbsent {param($Folder,$Name)return $null}
      function Assert-HeldObservationPriorTasks {param($Folder,$Inputs)$script:events.Add('prior')}
      function Get-PreservedDaemonDefinitions {param($Folder)@{warden='unchanged'}}
      function Assert-DaemonDefinitionsUnchanged {param($Before,$Folder)$script:events.Add('preserved')}
      function Assert-HeldObservationRuntime {param([switch]$FullCustody)if(-not $FullCustody){throw 'no custody'};$script:events.Add('custody')}
      function Get-HeldObservationInputs {$inputValue}
      function New-ProtectedDirectory {param($Path)$null=[IO.Directory]::CreateDirectory($Path)}
      function Copy-VerifiedPayload {param($Record)$src=[IO.File]::OpenRead($Record.source);try{$dst=[IO.File]::Open($Record.destination,[IO.FileMode]::CreateNew);try{$src.CopyTo($dst)}finally{$dst.Dispose()}}finally{$src.Dispose()};$script:events.Add('copy');if($case -eq 'copy_failure'){throw 'fixture failure'}}
      function Open-VerifiedFile {param($Path,$Hash,$Length)[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)}
      function Write-HeldObservationControl {param($Path,$Value)$bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 12));$s=[IO.File]::Open($Path,[IO.FileMode]::CreateNew);try{$s.Write($bytes,0,$bytes.Length)}finally{$s.Dispose()};$script:events.Add([IO.Path]::GetFileName($Path));return ('d'*64)}
      $task=[pscustomobject]@{Enabled=$false;LastTaskResult=0};$task|Add-Member ScriptMethod Run {param($x)$script:events.Add('run');[pscustomobject]@{InstanceGuid='{12345678-1234-1234-1234-123456789012}'}}
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($n)$task}
      function New-HeldObservationTask {param($Scheduler,$Folder,$Name,$Arguments,$Daemon)$script:events.Add($(if($Daemon){'register_observer'}else{'register_provision'}));$task}
      function Wait-KnowledgeTask {param($Instance,$TimeoutSeconds)if($TimeoutSeconds -ne 370){throw 'bad wait'}}
      function Assert-CompletedKnowledgeTask {param($Task)}
      function Assert-HeldTaskDefinition {param($Task,$Arguments,$Daemon)}
      function Read-HeldObservationControl {param($Path)[pscustomobject]@{Sha256=('e'*64);Value=[pscustomobject]@{nonce=('f'*32);inputs_sha256=('d'*64);configuration_sha256=('1'*64)}}}
      function Assert-HeldProvisionReceipt {param($Receipt,$Inputs,$Nonce,$InputsHash,$Task)$script:events.Add('provision_accepted');if($case -eq 'provision_failure'){throw 'fixture failure'}}
      function Wait-HeldObservationRunning {param($Folder,$Arguments,$Provision,$InstanceGuid)if($InstanceGuid -cne '{12345678-1234-1234-1234-123456789012}'){throw 'instance not retained'};$script:events.Add('running_witness');[pscustomobject]@{Control=[pscustomobject]@{Sha256=('2'*64)};NativeProcess=@{pid=42}}}
      $failed=$false;try{$null=Invoke-HeldObservationInstall ([pscustomobject]@{}) $folder $inputValue}catch{$failed=$true}finally{foreach($s in $script:held){$s.Dispose()}}
      @{failed=$failed;events=@($script:events);preserved=(Test-Path -LiteralPath (Join-Path $script:targetRoot 'held-supervisor-observation-r3-v1.py'));activation=(Test-Path -LiteralPath (Join-Path $script:targetRoot 'activation.json'))}|ConvertTo-Json -Compress
    ''')
    assert result['failed']==(case!='success') and result['preserved']
    assert result['activation']==(case=='success')
    if case=='success':
        assert result['events'].index('provision_accepted')<result['events'].index('register_observer')<result['events'].index('start-intent.json')<result['events'].index('running_witness')
        assert result['events'].count('run')==2
    else:assert 'register_observer' not in result['events']


@pytest.mark.parametrize('field,value',[('phase','bindings'),('phase','secret private detail'),('code','REPAIR_IDENTITY_MIXED_STATE'),('code','secret private detail'),('winerror',5),('winerror',-1)])
def test_safe_failure_allowlist(field,value):
    failure={'phase':'bindings','error_type':'ObservationHeld','code':None,'winerror':None};failure[field]=value
    raw=json.dumps(failure).replace("'","''")
    result=run(f"$value='{raw}'|ConvertFrom-Json;"+r'''
      $refused=$false;$safe=$null;try{$safe=Get-HeldObservationFailure $value}catch{$refused=$true}
      @{refused=$refused;safe=$safe}|ConvertTo-Json -Compress
    ''')
    assert result['refused']==(value in ('secret private detail',-1))
