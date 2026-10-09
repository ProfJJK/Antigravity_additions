"""WinPS5.1 task/receipt/copy fixtures; no task, Docker, provider or Apply run."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

W=Path(__file__).parent
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
WRAPPER=W/'diagnose-foundation-docker-r3-v1.ps1'
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE=REPO/'docs/evidence/windows-2026-10-06/execution-foundation-failure-2026-10-07'
INSTALL=r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
FOUNDATION='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'
PACKET='a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6'
PREFLIGHT='3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'
HELPER='fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1'


def chain():
    return {'nodes':[{'parent':None,'relation':'root','error_type':'WindowsIsolationError','operation':'pipe_open','winerror':5,'winerror_source':'attribute'}],
            'truncated':False,'raw_exception_text_published':False}


def diagnostic_receipt(kind='held'):
    value={'schema':'cochem-foundation-diagnostic/1','status':'FOUNDATION_DIAGNOSTIC_HELD','nonce':'1'*32,
        'system_sid':'S-1-5-18','helper_sha256':'2'*64,'packet_sha256':'3'*64,'runtime_root':INSTALL,
        'diagnostic_only':True,'native_model_jobs_executed':0,
        'install_receipt_sha256':'3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6',
        'preflight_receipt_sha256':PREFLIGHT,'failed_foundation_receipt_sha256':FOUNDATION,'failed_foundation_packet_sha256':PACKET,
        'failure':{'phase':'docker_owner_census','chain':chain()},
        'docker_trace':{'last_stage':'docker_owner_census','commands_started':2,'commands_returned':2,'retry_attempts':0,
            'original_guard_decisions_unchanged':True,'events':[
                {'stage':'docker_info','kind':'bounded_cli','position':'single_attempt','completed':True},
                {'stage':'docker_owner_census','kind':'server_attestation','position':'before_cli','completed':False,'alias':'selected_linux','failure':chain()}]}}
    for flag in ('activation_ready','ram_provision_started','registry_provision_started','existing_files_or_acls_modified','existing_databases_modified','existing_tasks_modified_or_run','knowledge_service_constructed','docker_runner_constructed','ram_ensure_called','native_provider_authentication_performed','automatic_retry_performed'):
        value[flag]=False
    if kind=='success':
        value.pop('failure');value['status']='FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED';value['preservation_after_observation_verified']=True
        value['docker_trace'].update(last_stage='docker_pipe_denials_after',commands_started=4,commands_returned=4,events=[])
        value['revision']={'verified':True,'source_sha256':'309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'}
    elif kind=='early':
        for key in ('install_receipt_sha256','preflight_receipt_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256'):value.pop(key)
        value['failure']['phase']='runtime';value['docker_trace'].update(last_stage='before_observation',commands_started=0,commands_returned=0,events=[])
    return value


def functions():
    return r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
'''+f"$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{WRAPPER}',[ref]$t,[ref]$e);if($e.Count){{throw $e[0]}};foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}\n"


def constants():
    return rf"""
$installRoot='{INSTALL}';$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$failedRoot='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3'
$failedReceiptHash='{FOUNDATION}';$failedPacketHash='{PACKET}';$failedHelperHash='{HELPER}';$actualPreflightHash='{PREFLIGHT}'
$preflightHash='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a';$supportHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$runtime=[pscustomobject]@{{install_receipt_sha256='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';revision=[pscustomobject]@{{verified=$true;source_sha256='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'}}}}
"""


def run(tmp_path, body):
    script=tmp_path/'fixture.ps1';script.write_text(functions()+constants()+body)
    result=subprocess.run([PS,'-NoProfile','-NonInteractive','-File',str(script)],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


TASK=r'''
$action=[pscustomobject]@{Type=0;Path=$python;Arguments=('-I -B "'+$failedRoot+'\provision-execution-foundation-r3.py" --nonce a92fe71e71224573a3738b5131ecfb36 --packet-sha256 '+$failedPacketHash);WorkingDirectory=$failedRoot}
$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($n)$action}
$definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}
$task=[pscustomobject]@{State=3;LastTaskResult=2;Definition=$definition};$script:instances=0
$task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$script:instances}}
'''


@pytest.mark.parametrize('change,accepted', [('',True),("$task.State=1",True),("$definition.Principal.UserId='S-1-5-18'",True),
    ('$task=$null',False),('$task.State=4',False),('$task.State=0',False),('$script:instances=1',False),
    ('$task.LastTaskResult=0',False),("$definition.Principal.UserId='ansac'",False),('$definition.Principal.LogonType=2',False),
    ('$definition.Principal.RunLevel=0',False),('$definition.Triggers.Count=1',False),('$actions.Count=2',False),
    ('$action.Type=5',False),("$action.Path='wrong'",False),("$action.Arguments+=' changed'",False),("$action.WorkingDirectory='wrong'",False)])
def test_exact_preserved_task_terminal_identity_and_action(tmp_path,change,accepted):
    output=run(tmp_path,TASK+change+";$ok=$false;try{Assert-HeldFoundationTask $task $python;$ok=$true}catch{};$ok|ConvertTo-Json -Compress")
    assert json.loads(output) is accepted


@pytest.mark.parametrize('change', ['',"$v.nonce='0'*32","$v.packet_sha256='0'*64","$v.helper_sha256='0'*64", "$v.preflight_receipt_sha256='0'*64",
    "$v.system_sid='not-system'", "$v.failure.phase='ram_provision'", "$v.failure.error_type='ValueError'",'$v.failure.winerror=5',
    '$v.ram_provision_started=$true','$v.registry_provision_started=$true','$v.containers_created=1','$v.native_model_jobs_executed=1',
    '$v.credentials_or_native_profiles_modified=$true','$v.legacy_databases_or_budgets_modified=$true','$v.automatic_retry_allowed=$true',
    "$v.revision.source_sha256='0'*64",'$v.activation_ready=$true'])
def test_actual_failed_receipt_and_narrow_mutations(tmp_path,change):
    receipt=ARCHIVE/'execution-foundation.json'
    assert hashlib.sha256(receipt.read_bytes()).hexdigest()==FOUNDATION
    out=run(tmp_path,f"$v=[IO.File]::ReadAllText('{receipt}')|ConvertFrom-Json;"+change+";$ok=$false;try{Assert-HeldFoundationReceipt $v $runtime;$ok=$true}catch{};$ok|ConvertTo-Json -Compress")
    assert json.loads(out) is (change=='')


def packet_fixture(tmp_path):
    preserved=tmp_path/'preserved';preserved.mkdir()
    shutil.copyfile(ARCHIVE/'execution-foundation.json',preserved/'execution-foundation.json')
    shutil.copyfile(ARCHIVE/'foundation-inputs.json',preserved/'inputs.json')
    for name in ('provision-execution-foundation-r3.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py'):
        shutil.copyfile(W/name,preserved/name)
    body=f"$failedRoot='{preserved}';$held=[Collections.Generic.List[IO.FileStream]]::new();"
    body+=r'''
    $script:events=[Collections.Generic.List[string]]::new()
    function Read-R3Control {param($Path,$Hash,$Maximum)
      $raw=[IO.File]::ReadAllBytes($Path);if($raw.Length -gt $Maximum -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Hash){throw 'Fixture exact pin differs'}
      $s=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$held.Add($s);[pscustomobject]@{Stream=$s;Sha256=$Hash;Length=$raw.Length}
    }
    function Read-R3Text {param($Control)$Control.Stream.Position=0;$r=[IO.StreamReader]::new($Control.Stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$r.ReadToEnd()}finally{$r.Dispose()}}
    $actualPacket=[IO.File]::ReadAllText((Join-Path $failedRoot 'inputs.json'))|ConvertFrom-Json
    function Get-FoundationPacket {param($Runtime)
      $script:events.Add('successful-preflight-check')
      [ordered]@{schema=$actualPacket.schema;install_receipt_sha256=$actualPacket.install_receipt_sha256;knowledge_receipt_sha256=$actualPacket.knowledge_receipt_sha256;workers=$actualPacket.workers;preflight_receipt_sha256=$actualPacket.preflight_receipt_sha256}
    }
    '''
    body+=TASK
    body+=r'''
    function Get-TaskOrAbsent {param($Name)$script:events.Add('failed-task-check');if($Name -cne 'CoChem-4.2.7-ExecutionFoundation-20261007-r3'){throw 'Unexpected task'};$task}
    '''
    return preserved,body


@pytest.mark.parametrize('change',['none','preflight_pin','packet_drift','failed_task_running','worker_mismatch'])
def test_packet_uses_exact_preserved_bytes_and_task_before_new_creation(tmp_path,change):
    root,body=packet_fixture(tmp_path)
    if change=='preflight_pin':body+="$actualPacket.preflight_receipt_sha256='0'*64;"
    elif change=='packet_drift':
        with (root/'inputs.json').open('ab') as out:out.write(b' ')
    elif change=='failed_task_running':body+='$task.State=4;'
    elif change=='worker_mismatch':body+="$actualPacket.workers.slot6='0'*64;"
    body+=r'''
    $ok=$false;$v=$null;try{$v=Get-DiagnosticPacket $runtime;$ok=$true}catch{}finally{foreach($s in $held){$s.Dispose()}}
    [ordered]@{ok=$ok;packet=$v;events=@($script:events.ToArray())}|ConvertTo-Json -Depth 8 -Compress
    '''
    value=json.loads(run(tmp_path,body))
    assert value['ok'] is (change=='none')
    if change=='none':
        assert value['packet']['schema']=='cochem-foundation-diagnostic-inputs/1'
        assert value['packet']['failed_foundation_receipt_sha256']==FOUNDATION
        assert value['events']==['successful-preflight-check','failed-task-check']
    elif change in ('preflight_pin','packet_drift','worker_mismatch'):
        assert 'failed-task-check' not in value['events']


def flow_fixture(tmp_path,change):
    source_paths=[]
    for name in ('diagnose-foundation-docker-r3-v1.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py'):
        p=tmp_path/name;p.write_bytes(('# ordinary unexecuted copy fixture '+name+'\n').encode());source_paths.append(p)
    target=tmp_path/'fresh-diagnostic'
    template=tmp_path/'receipt-template.json';template.write_text(json.dumps(diagnostic_receipt('success' if change=='success' else 'early' if change=='early' else 'held')))
    body=rf"""
    $root='{target}';$source='{source_paths[0]}';$preflight='{source_paths[1]}';$support='{source_paths[2]}';$basePython='C:\fixture-base\python.exe'
    $sourceHash='{hashlib.sha256(source_paths[0].read_bytes()).hexdigest()}';$preflightHash='{hashlib.sha256(source_paths[1].read_bytes()).hexdigest()}';$supportHash='{hashlib.sha256(source_paths[2].read_bytes()).hexdigest()}'
    $taskName='FIXTURE-NO-REAL-TASK';$change='{change}';$held=[Collections.Generic.List[IO.FileStream]]::new()
    $template='{template}'
    """
    # Exact reviewed copy and exclusive packet implementation, with only the
    # privileged ACL identity substituted for this ordinary disposable fixture.
    copy=REPO/'scripts/stage_aetherdesk_427_payloads.ps1'
    pre=W/'inspect-execution-prerequisites-r3.ps1'
    body+=f"""
    foreach($d in @(Import-PinnedFunctions '{copy}' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){{. ([scriptblock]::Create($d))}};Initialize-FileIdentity
    foreach($d in @(Import-PinnedFunctions '{pre}' '5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887' @('Write-NewPacket'))){{. ([scriptblock]::Create($d))}}
    """
    body+=r'''
    $script:events=[Collections.Generic.List[string]]::new();$script:packetReads=0;$script:registered=0;$script:ran=0
    $packet=[ordered]@{schema='fixture-packet';install_receipt_sha256=$runtime.install_receipt_sha256;fixture=$true}
    function Assert-NoReparseAncestors{param($Path)};function Assert-ProtectedPath{param($Path)}
    function Assert-Stopped{$script:events.Add('stopped')}
    function Assert-OriginalFailedDenial{param([switch]$RequireTask)if(-not $RequireTask){throw 'Original evidence task check was omitted'};$script:events.Add('original-failed-denial')}
    function Assert-DockerNativeCustody{$script:events.Add('docker-custody')}
    function Assert-CodeTreeOnce{param($Path)$script:events.Add('code-custody');1}
    function New-ProtectedDirectory{param($Path)$script:events.Add('create');if([IO.Directory]::Exists($Path)){throw 'Fixture collision'};$null=[IO.Directory]::CreateDirectory($Path)}
    function New-CodeAcl{param([bool]$Directory)$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$a=[Security.AccessControl.FileSecurity]::new();$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false);$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));$a}
    function Get-DiagnosticPacket{param($Runtime)
      $script:packetReads++;$script:events.Add('evidence-check')
      if(($change -ceq 'before-drift' -and $script:packetReads -eq 1) -or ($change -ceq 'after-drift' -and $script:packetReads -eq 2)){return [ordered]@{changed=$true}}
      $packet
    }
    function Get-TaskOrAbsent{param($Name)if($change -ceq 'task-collision'){return [pscustomobject]@{State=3}};$null}
    function Read-R3Control{param($Path,$Hash,$Maximum)
      $raw=[IO.File]::ReadAllBytes($Path);if($raw.Length -gt $Maximum){throw 'Fixture bound'};if(-not $Hash){$Hash=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
      $s=Open-VerifiedFile $Path $Hash $raw.Length;$held.Add($s);[pscustomobject]@{Stream=$s;Sha256=$Hash;Length=$raw.Length}
    }
    function Read-R3Text{param($Control)$Control.Stream.Position=0;$r=[IO.StreamReader]::new($Control.Stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$r.ReadToEnd()}finally{$r.Dispose()}}
    function Wait-Inspection{param($Instance,$Seconds)if($Seconds -ne 490){throw 'Fixture wait/task-limit mismatch'};$script:events.Add('wait-only')}
    $action=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''}
    $actions=[pscustomobject]@{};$actions|Add-Member ScriptMethod Create {param($n)$action}
    $definition=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;ExecutionTimeLimit=''};Actions=$actions;Triggers=@()}
    $scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod NewTask {param($n)$definition}
    $task=[pscustomobject]@{State=3;LastTaskResult=2};$task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=0}}
    $task|Add-Member ScriptMethod Run {param($x)
      $script:ran++;$script:events.Add('run-once')
      if($change -ceq 'no-receipt'){return [pscustomobject]@{State=3}}
      $report=[IO.File]::ReadAllText($template)|ConvertFrom-Json
      $report.nonce=$nonce;$report.helper_sha256=$sourceHash;$report.packet_sha256=$packetHash
      if($change -ceq 'success'){$task.LastTaskResult=0}
      if($change -ceq 'receipt-nonce'){$report.nonce='0'*32}
      if($change -ceq 'receipt-scope'){$report.existing_files_or_acls_modified=$true}
      [IO.File]::WriteAllText((Join-Path $root 'foundation-diagnostic.json'),($report|ConvertTo-Json -Depth 10))
      if($change -ceq 'running'){$task.State=4}
      [pscustomobject]@{State=3}
    }
    $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod RegisterTaskDefinition {
      param($Name,$Definition,$Flags,$User,$Password,$Logon,$Sddl)
      $script:registered++;$script:events.Add('register-once')
      if($Name -cne $taskName -or $Flags -ne 2 -or $User -cne 'SYSTEM' -or $null -ne $Password -or $Logon -ne 5 -or $Definition.Principal.UserId -cne 'SYSTEM' -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Principal.LogonType -ne 5 -or $Definition.Triggers.Count -ne 0 -or $Definition.Settings.MultipleInstances -ne 2 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT8M'){throw 'Fixture task contract differs'}
      if($action.Path -cne $python -or $action.WorkingDirectory -cne $root -or $action.Arguments -cnotmatch '^-I -B ".+" --nonce [a-f0-9]{32} --packet-sha256 [a-f0-9]{64}$'){throw 'Fixture action differs'}
      if($held.Count -ne 4 -or @(Get-ChildItem -LiteralPath (Join-Path $root 'docker-client-config') -Force).Count -ne 0){throw 'Three copies/input packet were not held before registration'}
      $task
    }
    if($change -ceq 'root-collision'){$null=[IO.Directory]::CreateDirectory($root);[IO.File]::WriteAllText((Join-Path $root 'sentinel'),'preserve')}
    $failed=$false;$message='';$output=$null
    try{$output=Invoke-FoundationDiagnostic $runtime $packet}catch{$failed=$true;$message=$_.Exception.Message}finally{foreach($s in $held){$s.Dispose()}}
    [ordered]@{fixture=$true;failed=$failed;message=$message;registered=$script:registered;ran=$script:ran;events=@($script:events.ToArray());output=$output}|ConvertTo-Json -Depth 10 -Compress
    '''
    return target,source_paths,body


@pytest.mark.parametrize('change',['none','success','early','before-drift','after-drift','root-collision','task-collision','no-receipt','receipt-nonce','receipt-scope','running'])
def test_actual_diagnostic_copy_and_apply_control_flow_preserves_failure(tmp_path,change):
    target,sources,body=flow_fixture(tmp_path,change)
    value=json.loads(run(tmp_path,body))
    expected_early=change in ('before-drift','root-collision','task-collision')
    assert value['registered']==value['ran']==(0 if expected_early else 1)
    assert value['failed'] is (change not in ('none','success','early'))
    if not expected_early:
        for source in sources:assert (target/source.name).read_bytes()==source.read_bytes()
        assert json.loads((target/'inputs.json').read_bytes())['fixture'] is True
        assert value['events'][:3]==['stopped','original-failed-denial','evidence-check']
        assert value['events'].count('stopped') >= 2
    if change in ('none','success','early'):
        report=json.loads(value['output'])
        assert report['status']==('FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED' if change=='success' else 'FOUNDATION_DIAGNOSTIC_HELD')
        assert report['last_task_result']==(0 if change=='success' else 2)
        if change!='success':assert report['failure']['phase']==('runtime' if change=='early' else 'docker_owner_census')
        assert report['automatic_retry_allowed'] is False
        assert value['events'][-2:]==['stopped','evidence-check']
    if change=='root-collision':assert (target/'sentinel').read_text()=='preserve'
    if change=='after-drift':assert (target/'foundation-diagnostic.json').exists()
    if change=='before-drift':assert not target.exists()


def project_summary(tmp_path,receipt,task_exit=2):
    source=tmp_path/'receipt.json';source.write_text(json.dumps(receipt))
    body=rf"""
    $root='{tmp_path}';$sourceHash='{'2'*64}';$task=[pscustomobject]@{{LastTaskResult={task_exit}}}
    $v=[IO.File]::ReadAllText('{source}')|ConvertFrom-Json
    $ok=$false;$summary=$null
    try{{$summary=Get-DiagnosticSummary $v $task $runtime '{'1'*32}' '{'3'*64}' (Join-Path $root 'foundation-diagnostic.json') '{'4'*64}';$ok=$true}}catch{{}}
    [ordered]@{{accepted=$ok;summary=$summary}}|ConvertTo-Json -Depth 15 -Compress
    """
    return json.loads(run(tmp_path,body))


@pytest.mark.parametrize('case,accepted',[('success',True),('held',True),('early',True),('held_exit0',False),('success_exit2',False),('unknown_status',False),('scope_bool_as_number',False),('wrong_optional_pin',False)])
def test_receipt_projection_status_exit_and_optional_early_bindings(tmp_path,case,accepted):
    value=diagnostic_receipt('success' if case.startswith('success') else 'early' if case=='early' else 'held')
    exit_code=0 if case in ('success','held_exit0') else 2
    if case=='unknown_status':value['status']='UNRECOGNIZED'
    if case=='scope_bool_as_number':value['ram_provision_started']=0
    if case=='wrong_optional_pin':value['install_receipt_sha256']='0'*64
    assert project_summary(tmp_path,value,exit_code)['accepted'] is accepted


@pytest.mark.parametrize('case',['chain_type','chain_operation','chain_extra','chain_raw','chain_parent','chain_number','trace_stage','trace_position','trace_alias','trace_retry','trace_incomplete','failure_phase'])
def test_unsafe_chain_and_trace_metadata_is_refused(tmp_path,case):
    value=diagnostic_receipt();node=value['failure']['chain']['nodes'][0];trace=value['docker_trace'];event=trace['events'][1]
    if case=='chain_type':node['error_type']='PrivateType SECRET'
    elif case=='chain_operation':node['operation']='SECRET_COMMAND'
    elif case=='chain_extra':node['raw_message']='SECRET_VALUE'
    elif case=='chain_raw':value['failure']['chain']['raw_exception_text_published']=True
    elif case=='chain_parent':node['parent']=0
    elif case=='chain_number':node['winerror']='SECRET_VALUE'
    elif case=='trace_stage':event['stage']='SECRET_COMMAND'
    elif case=='trace_position':event['position']='secret_position'
    elif case=='trace_alias':event['alias']='secret_pipe'
    elif case=='trace_retry':trace['retry_attempts']=1
    elif case=='trace_incomplete':event.pop('failure')
    elif case=='failure_phase':value['failure']['phase']='secret@invalid'
    result=project_summary(tmp_path,value)
    assert result['accepted'] is False and 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('native,accepted',[(0,True),(4294967295,True),(4294967296,False),(-1,False),(True,False),(1.5,False)])
def test_native_error_is_integer_uint32_only(tmp_path,native,accepted):
    value=diagnostic_receipt();value['failure']['chain']['nodes'][0]['winerror']=native
    result=project_summary(tmp_path,value)
    assert result['accepted'] is accepted
    if accepted:assert result['summary']['failure']['chain']['nodes'][0]['winerror']==native


def test_projection_omits_extra_fields_and_successful_trace_event_payload(tmp_path):
    value=diagnostic_receipt();value['raw_native_stdout']='SECRET_RECEIPT';value['failure']['raw_exception']='SECRET_FAILURE'
    value['docker_trace']['events'][0]['raw_output']='SECRET_COMPLETED_EVENT';value['docker_trace']['events'][1]['raw_output']='SECRET_FAILED_EVENT'
    result=project_summary(tmp_path,value)
    assert result['accepted'] is True and 'SECRET_' not in json.dumps(result)
    trace=result['summary']['docker_trace']
    assert trace['events_recorded']==2 and len(trace['failed_events'])==1 and trace['commands_started']==2


@pytest.mark.parametrize('case',['missing_pin','revision','incomplete','count','last_stage'])
def test_verified_claim_needs_complete_final_evidence(tmp_path,case):
    value=diagnostic_receipt('success')
    if case=='missing_pin':value.pop('failed_foundation_packet_sha256')
    elif case=='revision':value['revision']['source_sha256']='0'*64
    elif case=='incomplete':value['docker_trace']['events']=diagnostic_receipt()['docker_trace']['events']
    elif case=='count':value['docker_trace']['commands_returned']=3
    elif case=='last_stage':value['docker_trace']['last_stage']='docker_name_census'
    assert project_summary(tmp_path,value,0)['accepted'] is False
