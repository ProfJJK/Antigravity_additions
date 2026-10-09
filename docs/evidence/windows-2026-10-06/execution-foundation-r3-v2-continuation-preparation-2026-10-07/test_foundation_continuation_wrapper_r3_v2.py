"""Fresh continuation guards with WinPS5.1 disposable files and fake tasks only."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import test_foundation_diagnostic_wrapper_r3 as D

W=D.W
WRAPPER=W/'provision-execution-foundation-r3-v2.ps1'
ARCHIVE=D.REPO/'docs/evidence/windows-2026-10-06/execution-foundation-diagnostic-system-2026-10-07'
DIAGNOSTIC='cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9'
DIAGNOSTIC_PACKET='8744b6a0b569b820404734a491dd7b1671ab2a439ccc27f7251dccbf75a7550d'
DIAGNOSTIC_HELPER='fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5'


def preamble():
    return D.functions()+D.constants()+f"""
    foreach($d in @(Import-PinnedFunctions '{W/'inspect-execution-prerequisites-r3.ps1'}' '5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887' @('Assert-PassedTask'))){{. ([scriptblock]::Create($d))}}
    $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile('{WRAPPER}',[ref]$t,[ref]$e);if($e.Count){{throw $e[0]}}
    foreach($f in $a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}
    $diagnosticReceiptHash='{DIAGNOSTIC}';$diagnosticPacketHash='{DIAGNOSTIC_PACKET}';$diagnosticHash='{DIAGNOSTIC_HELPER}'
    $diagnosticRoot='C:\\Program Files\\CoChem\\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1'
    """


def run(tmp_path,body):
    script=tmp_path/'fixture-v2.ps1';script.write_text(preamble()+body,encoding='utf-8')
    result=subprocess.run([D.PS,'-NoProfile','-NonInteractive','-File',str(script)],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


def packet_setup(path):
    return f"$rawPacket=[IO.File]::ReadAllText('{path}')|ConvertFrom-Json;"+r'''
    $packet=[ordered]@{};foreach($p in $rawPacket.PSObject.Properties){$packet[$p.Name]=$p.Value}
    $workers=[ordered]@{};foreach($p in $rawPacket.workers.PSObject.Properties){$workers[$p.Name]=$p.Value};$packet.workers=$workers
    '''


@pytest.mark.parametrize('change',['none','diagnostic_pin','diagnostic_nonce','worker','preservation','boolean_number','fresh_state','trace_incomplete','trace_count','helper','runtime'])
def test_exact_actual_diagnostic_required_before_continuation(tmp_path,change):
    actual=ARCHIVE/'foundation-diagnostic.json';assert hashlib.sha256(actual.read_bytes()).hexdigest()==DIAGNOSTIC
    body=packet_setup(ARCHIVE/'inputs.json')+f"$v=[IO.File]::ReadAllText('{actual}')|ConvertFrom-Json;"
    mutation={'none':'','diagnostic_pin':"$v.packet_sha256='0'*64",'diagnostic_nonce':"$v.nonce='0'*32",'worker':"$v.worker_receipt_sha256.slot6='0'*64",'preservation':'$v.preservation_after_observation_verified=$false','boolean_number':'$v.ram_provision_started=0','fresh_state':'$v.fresh_state_after.ram_ledger_absent=$false','trace_incomplete':'$v.docker_trace.events[0].completed=$false','trace_count':'$v.docker_trace.commands_returned=3','helper':"$v.helper_sha256='0'*64",'runtime':"$v.revision.source_sha256='0'*64"}[change]
    body+=mutation+";$ok=$false;try{Assert-VerifiedDiagnosticReceipt $v $runtime $packet;$ok=$true}catch{};$ok|ConvertTo-Json -Compress"
    assert json.loads(run(tmp_path,body)) is (change=='none')


def continuation_fixture(tmp_path):
    old,body=D.packet_fixture(tmp_path)
    body=body.replace('workers=$actualPacket.workers','workers=$fixtureWorkers')
    body+=r'''
    $fixtureWorkers=[ordered]@{};foreach($p in $actualPacket.workers.PSObject.Properties){$fixtureWorkers[$p.Name]=$p.Value}
    '''
    diag=tmp_path/'preserved-diagnostic';diag.mkdir()
    shutil.copyfile(ARCHIVE/'foundation-diagnostic.json',diag/'foundation-diagnostic.json')
    shutil.copyfile(ARCHIVE/'inputs.json',diag/'inputs.json')
    for name in ('diagnose-foundation-docker-r3-v1.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py'):shutil.copyfile(W/name,diag/name)
    body+=f"$diagnosticRoot='{diag}';"
    body+=r'''
    $oldTask=$task
    $diagnosticAction=[pscustomobject]@{Type=0;Path=$python;Arguments=('-I -B "'+$diagnosticRoot+'\diagnose-foundation-docker-r3-v1.py" --nonce 3e2e411d9c274167a703a4308404dbe5 --packet-sha256 '+$diagnosticPacketHash);WorkingDirectory=$diagnosticRoot}
    $diagnosticActions=[pscustomobject]@{Count=1};$diagnosticActions|Add-Member ScriptMethod Item {param($n)$diagnosticAction}
    $diagnosticDefinition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$diagnosticActions}
    $diagnosticTask=[pscustomobject]@{State=3;LastTaskResult=0;Definition=$diagnosticDefinition};$script:diagnosticInstances=0
    $diagnosticTask|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$script:diagnosticInstances}}
    function Get-TaskOrAbsent{param($Name)
      if($Name -ceq 'CoChem-4.2.7-ExecutionFoundation-20261007-r3'){$script:events.Add('failed-task-check');return $oldTask}
      if($Name -ceq 'CoChem-4.2.7-ExecutionFoundationDiagnostic-20261007-r3-v1'){$script:events.Add('diagnostic-task-check');return $diagnosticTask}
      throw 'Unexpected fixture task lookup'
    }
    '''
    return old,diag,body


@pytest.mark.parametrize('change',['none','old_task_running','diagnostic_running','diagnostic_instances','diagnostic_exit','diagnostic_args','diagnostic_principal','diagnostic_trigger','diagnostic_packet_drift','diagnostic_receipt_drift'])
def test_continuation_rechecks_old_failure_and_successful_diagnostic_before_new_namespace(tmp_path,change):
    old,diag,body=continuation_fixture(tmp_path)
    mutations={'none':'','old_task_running':'$oldTask.State=4','diagnostic_running':'$diagnosticTask.State=4','diagnostic_instances':'$script:diagnosticInstances=1','diagnostic_exit':'$diagnosticTask.LastTaskResult=2','diagnostic_args':"$diagnosticAction.Arguments+=' changed'",'diagnostic_principal':"$diagnosticDefinition.Principal.UserId='ansac'",'diagnostic_trigger':'$diagnosticDefinition.Triggers.Count=1'}
    if change.endswith('_drift'):
        filename='inputs.json' if change=='diagnostic_packet_drift' else 'foundation-diagnostic.json'
        with (diag/filename).open('ab') as f:f.write(b' ')
    else:body+=mutations[change]+';'
    body+=r'''
    $ok=$false;$value=$null;try{$value=Get-ContinuationPacket $runtime;$ok=$true}catch{}finally{foreach($s in $held){$s.Dispose()}}
    [ordered]@{ok=$ok;packet=$value;events=@($script:events.ToArray())}|ConvertTo-Json -Depth 8 -Compress
    '''
    value=json.loads(run(tmp_path,body));assert value['ok'] is (change=='none')
    if change=='none':
        assert value['events']==['successful-preflight-check','failed-task-check','diagnostic-task-check']
        assert value['packet']['schema']=='cochem-execution-foundation-inputs/2' and value['packet']['diagnostic_receipt_sha256']==DIAGNOSTIC
    assert not (tmp_path/'fresh-foundation').exists()


def foundation_receipt(kind='success'):
    packet=json.loads((ARCHIVE/'inputs.json').read_bytes());packet.update(schema='cochem-execution-foundation-inputs/2',diagnostic_receipt_sha256=DIAGNOSTIC,diagnostic_packet_sha256=DIAGNOSTIC_PACKET)
    actual=json.loads((ARCHIVE/'foundation-diagnostic.json').read_bytes())
    value={key:packet[key] for key in ('install_receipt_sha256','preflight_receipt_sha256','diagnostic_receipt_sha256','diagnostic_packet_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')}
    value.update(schema='cochem-execution-foundation/2',status='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED',nonce='1'*32,system_sid='S-1-5-18',helper_sha256='2'*64,packet_sha256='3'*64,runtime_root=D.INSTALL,
        partial_outputs_preserved=True,ram_provision_started=True,registry_provision_started=True,containers_created=0,warm_pool_created=0,native_model_jobs_executed=0,
        ram_scoped_roots_verified=6,ram_ledger_created_new=True,registry={'registry_created_new':True,'capacity':4,'work_rows':0,'unknown_owned':0,'integrity_check':'ok'},revision=actual['revision'],
        docker_traces=[{'observation':'before_ram','trace':copy.deepcopy(actual['docker_trace'])},{'observation':'before_registry','trace':copy.deepcopy(actual['docker_trace'])}])
    for flag in ('activation_ready','automatic_retry_allowed','ram_volume_or_startup_task_modified','legacy_databases_or_budgets_modified','credentials_or_native_profiles_modified','knowledge_service_constructed_or_refreshed','general_readiness_called','existing_tasks_modified_or_run'):value[flag]=False
    if kind in ('held','early'):
        value.update(status='EXECUTION_FOUNDATION_HELD',ram_provision_started=False,registry_provision_started=False,failure={'phase':'docker_owner_census','chain':D.chain()})
        value['docker_traces']=value['docker_traces'][:1]
    if kind=='early':
        for key in ('install_receipt_sha256','preflight_receipt_sha256','diagnostic_receipt_sha256','diagnostic_packet_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256'):value.pop(key)
        value['failure']['phase']='runtime';value.pop('docker_traces')
    return value,packet


def summary(tmp_path,value,packet,exit_code=0):
    rp=tmp_path/'result.json';pp=tmp_path/'packet.json';rp.write_text(json.dumps(value));pp.write_text(json.dumps(packet))
    body=packet_setup(pp)+rf"""
    $root='{tmp_path}';$sourceHash='{'2'*64}';$task=[pscustomobject]@{{LastTaskResult={exit_code}}};$v=[IO.File]::ReadAllText('{rp}')|ConvertFrom-Json
    $ok=$false;$safe=$null;try{{$safe=Get-FoundationSummary $v $task $runtime $packet '{'1'*32}' '{'3'*64}' (Join-Path $root 'execution-foundation.json') '{'4'*64}';$ok=$true}}catch{{}}
    [ordered]@{{ok=$ok;summary=$safe}}|ConvertTo-Json -Depth 15 -Compress
    """
    return json.loads(run(tmp_path,body))


@pytest.mark.parametrize('case',['success','held','early','wrong_exit','missing_prior','scope_bool','capacity','work_rows','ram_count','revision','trace_order','trace_bound','failed_event','phase_unsafe','chain_unsafe','native_bound','extras_redacted'])
def test_foundation_v2_summary_honors_complete_success_and_sanitized_failure(tmp_path,case):
    kind='held' if case in ('held','phase_unsafe','chain_unsafe','native_bound','extras_redacted') else 'early' if case=='early' else 'success'
    value,packet=foundation_receipt(kind);exit_code=2 if kind in ('held','early') else 0
    if case=='wrong_exit':exit_code=2
    elif case=='missing_prior':value.pop('diagnostic_receipt_sha256')
    elif case=='scope_bool':value['ram_volume_or_startup_task_modified']=0
    elif case=='capacity':value['registry']['capacity']=6
    elif case=='work_rows':value['registry']['work_rows']=1
    elif case=='ram_count':value['ram_scoped_roots_verified']=4
    elif case=='revision':value['revision']['source_sha256']='0'*64
    elif case=='trace_order':value['docker_traces'].reverse()
    elif case=='trace_bound':value['docker_traces'].append(value['docker_traces'][1])
    elif case=='failed_event':value['docker_traces'][0]['trace']['events'][0].update(completed=False,failure=D.chain())
    elif case=='phase_unsafe':value['failure']['phase']='SECRET@email.invalid'
    elif case=='chain_unsafe':value['failure']['chain']['nodes'][0]['raw_command']='SECRET_VALUE'
    elif case=='native_bound':value['failure']['chain']['nodes'][0]['winerror']=4294967296
    elif case=='extras_redacted':
        value['raw_output']='SECRET_VALUE';value['failure']['raw_output']='SECRET_VALUE';value['docker_traces'][0]['trace']['events'][0]['raw_output']='SECRET_VALUE'
    result=summary(tmp_path,value,packet,exit_code)
    assert result['ok'] is (case in ('success','held','early','extras_redacted'))
    assert 'SECRET_' not in json.dumps(result)
    if case=='success':assert result['summary']['empty_registry_capacity']==4 and result['summary']['ram_scoped_roots_verified']==6


@pytest.mark.parametrize('change',['success','held','early','before-drift','after-drift','root-collision','task-collision'])
def test_full_continuation_flow_copies_four_sources_and_stops_without_rerun(tmp_path,change):
    # Reuse the already-tested real WinPS copy/task fixture, preserving the real
    # new Invoke-Foundation and Get-FoundationSummary functions under test.
    target,sources,body=D.flow_fixture(tmp_path,change)
    value,packet=foundation_receipt('success' if change not in ('held','early') else change)
    (tmp_path/'receipt-template.json').write_text(json.dumps(value))
    packet_path=tmp_path/'v2-packet.json';packet_path.write_text(json.dumps(packet))
    diagnostic=tmp_path/'diagnostic-support.py';diagnostic.write_bytes(b'# synthetic never-executed diagnostic support\n')
    injection=f"$diagnostic='{diagnostic}';$diagnosticHash='{hashlib.sha256(diagnostic.read_bytes()).hexdigest()}';"
    body=injection+body
    old="$packet=[ordered]@{schema='fixture-packet';install_receipt_sha256=$runtime.install_receipt_sha256;fixture=$true}"
    assert old in body;body=body.replace(old,packet_setup(packet_path))
    body=body.replace('Get-DiagnosticPacket','Get-ContinuationPacket').replace('Invoke-FoundationDiagnostic','Invoke-Foundation')
    body=body.replace("Join-Path $root 'foundation-diagnostic.json'","Join-Path $root 'execution-foundation.json'")
    body=body.replace('$held.Count -ne 4','$held.Count -ne 5')
    body=body.replace("if($change -ceq 'success'){$task.LastTaskResult=0}","if($change -cnotin @('held','early')){$task.LastTaskResult=0}")
    # The ordinary fixture's structured result always prints a final compact
    # line; held production summaries print earlier and must remain visible.
    output=run(tmp_path,body)
    result=json.loads(next(line for line in output.splitlines() if line.startswith('{"fixture"')))
    early=change in ('before-drift','root-collision','task-collision')
    assert result['registered']==result['ran']==(0 if early else 1)
    assert result['failed'] is (change!='success')
    if not early:
        for src,name in [(diagnostic,'diagnose-foundation-docker-r3-v1.py'),(sources[0],'provision-execution-foundation-r3-v2.py'),(sources[1],sources[1].name),(sources[2],sources[2].name)]:assert (target/name).read_bytes()==src.read_bytes()
        assert json.loads((target/'inputs.json').read_bytes())==packet
    if change=='success':
        final=json.loads(result['output']);assert final['status']=='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED' and final['empty_registry_capacity']==4
        assert result['events'][-3:]==['stopped','original-failed-denial','evidence-check']
    elif change in ('held','early'):
        assert '"status":  "EXECUTION_FOUNDATION_HELD"' in output
        assert 'Fresh foundation held' in result['message'] and result['events'].count('evidence-check')==1
    if change=='root-collision':assert (target/'sentinel').read_text()=='preserve'
