"""Ordinary Windows fixtures. No administrator, provider, task or controller effects."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'run-pipeline-commissioning-r3-v1.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
INSTALL = r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'


def ps(code):
    loader = ("$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"
              "$tokens=$null;$errors=$null;"
              f"$ast=[Management.Automation.Language.Parser]::ParseFile('{SOURCE}',[ref]$tokens,[ref]$errors);"
              "if($errors.Count){throw 'Source parse error'};"
              "foreach($node in $ast.EndBlock.Statements){if($node -is [Management.Automation.Language.FunctionDefinitionAst]){. ([scriptblock]::Create($node.Extent.Text))}};"
              f"$installRoot='{INSTALL}';")
    result = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', loader+code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize('failure', ['', 'auth_exit', 'activation_exit', 'auth_verify', 'activation_verify', 'string_exit', 'float_exit'])
def test_phase_order_and_stop_at_first_unverified_step(failure):
    value = ps(f"$failure='{failure}';"+r'''
    $script:events=[Collections.Generic.List[string]]::new();$failed=$false
    try{Invoke-CommissioningPhases {
      param($phase)
      $script:events.Add('invoke:'+ $phase)
      if($failure -ceq 'auth_exit' -and $phase -ceq 'native_authentication'){return 2}
      if($failure -ceq 'activation_exit' -and $phase -ceq 'first_controller_start'){return 2}
      if($failure -ceq 'string_exit'){return '0'}
      if($failure -ceq 'float_exit'){return [double]0}
      return 0
    } {
      param($phase)
      $script:events.Add('verify:'+ $phase)
      if($failure -ceq 'auth_verify' -and $phase -ceq 'native_authentication'){throw 'inert verification failure'}
      if($failure -ceq 'activation_verify' -and $phase -ceq 'first_controller_start'){throw 'inert verification failure'}
      [pscustomobject]@{phase=$phase}
    } {
      param($phase,$state,$proof)
      $script:events.Add('record:'+ $phase+':'+$state)
    }}catch{$failed=$true}
    [ordered]@{failed=$failed;events=@($script:events.ToArray())}|ConvertTo-Json -Compress
    ''')
    assert value['failed'] == bool(failure)
    events=value['events']
    assert events[:2] == ['record:native_authentication:STARTED', 'invoke:native_authentication']
    if failure in ('auth_exit', 'auth_verify', 'string_exit', 'float_exit'):
        assert not any('first_controller_start' in item for item in events)
    if not failure:
        assert events == [part for phase in ('native_authentication','first_controller_start')
                          for part in (f'record:{phase}:STARTED',f'invoke:{phase}',f'verify:{phase}',f'record:{phase}:VERIFIED')]


def running_receipt():
    return {'schema':'cochem-warden-commissioning/1', 'status':'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED',
            'runtime_root':INSTALL, 'install_receipt_sha256':'3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6',
            'config_sha256':'135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
            'source_manifest_sha256':'6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1',
            'revision_sha256':'309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4',
            'authenticated_profiles_verified':12,'task_name':'CoChem-4.2.7-Warden',
            'exactly_one_start_requested':True, 'model_jobs_submitted':0, 'full_srs_acceptance':False,
            'queue_launch_output':r'C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1',
            'monitoring_started':True,'monitoring_scope':'heartbeat_and_queue_only',
            'system_sid':'S-1-5-18','automatic_repair_enabled':False,'automatic_retry_allowed':False,
            'controller':{'pid':12345,'creation_filetime':134358000000000000,'instance_id':'a'*32,
                          'first_sequence':1,'final_sequence':3}}


@pytest.mark.parametrize('change', ['', 'configuration', 'false_monitor', 'string_monitor', 'float_count',
                                   'full_acceptance', 'string_full_acceptance', 'zero_pid', 'string_pid',
                                   'pid_outside_dword', 'missing_creation', 'no_progress', 'instance',
                                   'wrong_queue', 'model_jobs', 'wrong_task','repair_enabled','string_repair',
                                   'retry_enabled','monitoring_scope','system_sid'])
def test_running_receipt_requires_exact_typed_identity_and_progress(tmp_path,change):
    value=running_receipt()
    if change=='configuration':value['config_sha256']='0'*64
    if change=='false_monitor':value['monitoring_started']=False
    if change=='string_monitor':value['monitoring_started']='True'
    if change=='float_count':value['authenticated_profiles_verified']=12.0
    if change=='full_acceptance':value['full_srs_acceptance']=True
    if change=='string_full_acceptance':value['full_srs_acceptance']='False'
    if change=='zero_pid':value['controller']['pid']=0
    if change=='string_pid':value['controller']['pid']='12345'
    if change=='pid_outside_dword':value['controller']['pid']=2**32
    if change=='missing_creation':del value['controller']['creation_filetime']
    if change=='no_progress':value['controller']['final_sequence']=1
    if change=='instance':value['controller']['instance_id']='not-an-instance'
    if change=='wrong_queue':value['queue_launch_output']+=r'\other'
    if change=='model_jobs':value['model_jobs_submitted']=1
    if change=='wrong_task':value['task_name']='other'
    if change=='repair_enabled':value['automatic_repair_enabled']=True
    if change=='string_repair':value['automatic_repair_enabled']='False'
    if change=='retry_enabled':value['automatic_retry_allowed']=True
    if change=='monitoring_scope':value['monitoring_scope']='full_resource_acceptance'
    if change=='system_sid':value['system_sid']='S-1-5-32-544'
    path=tmp_path/'receipt.json';path.write_text(json.dumps(value))
    actual=ps(f"$value=Get-Content -LiteralPath '{path}' -Raw|ConvertFrom-Json;"
              "$ok=$false;try{$null=Assert-CommissioningRunningEvidence $value;$ok=$true}catch{};"
              "[ordered]@{accepted=$ok}|ConvertTo-Json -Compress")
    assert actual['accepted'] == (not change)


@pytest.mark.parametrize('failure', ['', 'auth_exit', 'activation_exit', 'auth_verify', 'activation_verify'])
def test_actual_apply_tail_retains_journals_and_exact_console_arguments(tmp_path,failure):
    text=SOURCE.read_text()
    start=text.index(' if($holds.Count){throw ($holds -join')
    tail='try{\n'+text[start:text.index('}finally{foreach($stream in $held)',start)]+'}'
    tailfile=tmp_path/'tail.ps1';tailfile.write_text(tail)
    root=tmp_path/'series'
    code=(f"$seriesRoot='{root}';$authSource='reviewed-auth.ps1';$activationSource='reviewed-activation.ps1';"
          f"$authHash='auth-pin';$activationHash='activation-pin';$failure='{failure}';"
          "$runtime=[pscustomobject]@{fixture=$true};$authPlan=[pscustomobject]@{fixture='auth'};$activationPlan=[pscustomobject]@{fixture='start'};$holds=@();$ownsRoot=$false;$phase='preflight';$nonce=$null;")
    code+=r'''
    $script:calls=[Collections.Generic.List[object]]::new()
    function Assert-ProtectedPath{param($Path)}
    function Assert-SeriesPrivateRoot{param($Path)if(-not [IO.Directory]::Exists($Path)){throw 'fixture directory missing'}}
    function Initialize-SeriesDirectory{
      Add-Type -TypeDefinition 'using System.IO;public static class CoChemNativeLoginSeriesDirectory{public static void Create(string p){if(Directory.Exists(p))throw new IOException("exists");Directory.CreateDirectory(p);}}'
    }
    function Write-SeriesRecord{param($Path,$Value)$s=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$b=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 15));$s.Write($b,0,$b.Length);$s.Flush($true)}finally{$s.Dispose()}}
    function Invoke-SeriesConsoleChild{
      param([string[]]$Arguments)
      $script:calls.Add(@($Arguments))
      if(($failure -ceq 'auth_exit' -and $Arguments -contains 'reviewed-auth.ps1') -or ($failure -ceq 'activation_exit' -and $Arguments -contains 'reviewed-activation.ps1')){return 2}
      return 0
    }
    function Read-CommissioningAuthEvidence{if($failure -ceq 'auth_verify'){throw 'inert auth proof failure'};[pscustomobject]@{status='fixture-auth'}}
    function Read-CommissioningRunningEvidence{if($failure -ceq 'activation_verify'){throw 'inert controller proof failure'};[pscustomobject]@{status='fixture-start'}}
    $failed=$false
    '''
    # Suppress the product's intended console output in the fixture result only.
    code+=f"try{{$null=. ([scriptblock]::Create([IO.File]::ReadAllText('{tailfile}')))}}catch{{$failed=$true}};"
    code+="[ordered]@{failed=$failed;calls=@($script:calls.ToArray())}|ConvertTo-Json -Depth 8 -Compress"
    # Write-Host is information output, so silence it while retaining all native effects inert.
    value=ps("$InformationPreference='SilentlyContinue';function Write-Host{param($Object)};"+code)
    assert value['failed']==bool(failure)
    assert value['calls'][0]==['-NoProfile','-File','reviewed-auth.ps1','-Apply','-Interactive']
    if failure not in ('auth_exit','auth_verify'):
        assert value['calls'][1]==['-NoProfile','-File','reviewed-activation.ps1','-Apply']
    else:assert len(value['calls'])==1
    assert (root/'series-start.json').exists()
    assert (root/'preflight-authentication.json').exists()
    assert (root/'preflight-first-start.json').exists()
    assert (root/'series-complete.json').exists()==(not failure)
    assert (root/'series-failed.json').exists()==bool(failure)
    if failure in ('auth_exit','auth_verify'):
        assert not (root/'first_controller_start-STARTED.json').exists()
    if not failure:
        completed=json.loads((root/'series-complete.json').read_text(encoding='utf-8-sig'))
        assert completed['shared_capacity']==4 and completed['identity_count']==6
        assert not completed['automatic_repair_enabled'] and not completed['full_srs_acceptance']
        assert completed['model_jobs_submitted']==0


@pytest.mark.parametrize('change',['','hash','duplicate','parse'])
def test_source_import_rejects_drift_and_keeps_verified_source_locked(tmp_path,change):
    path=tmp_path/'support.ps1';path.write_text('function Support-Fn { return 1 }')
    pin=hashlib.sha256(path.read_bytes()).hexdigest()
    if change=='hash':pin='0'*64
    if change=='duplicate':path.write_text('function Support-Fn{}; function Support-Fn{}');pin=hashlib.sha256(path.read_bytes()).hexdigest()
    if change=='parse':path.write_text('function Support-Fn {');pin=hashlib.sha256(path.read_bytes()).hexdigest()
    code=("$held=[Collections.Generic.List[IO.FileStream]]::new();$ok=$false;$locked=$false;try{"
          f"$defs=@(Import-CommissioningFunctions '{path}' '{pin}' @('Support-Fn'));$ok=$true;"
          f"try{{$writer=[IO.File]::Open('{path}',[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$writer.Dispose()}}catch{{$locked=$true}}"
          "}catch{}finally{foreach($s in $held){$s.Dispose()}};[ordered]@{accepted=$ok;locked=$locked}|ConvertTo-Json -Compress")
    value=ps(code)
    assert value['accepted']==(not change)
    if not change:assert value['locked']


@pytest.mark.parametrize('change',['','other_hold','absent_missing_auth_hold','duplicate_expected_hold','auth_present_with_hold','native_hold'])
def test_preflight_only_defers_the_exact_pending_authentication_prerequisite(change):
    auth={'holds':[]}
    activation={'auth_completion_present':False,
                'holds':['Completed status-first authentication is required before first start.']}
    if change=='other_hold':activation['holds'].append('Private state collision')
    if change=='absent_missing_auth_hold':activation['holds']=[]
    if change=='duplicate_expected_hold':activation['holds']*=2
    if change=='auth_present_with_hold':activation['auth_completion_present']=True
    if change=='native_hold':auth['holds'].append('Worker root inaccessible')
    code=(f"$a=ConvertFrom-Json '{json.dumps(auth)}';$b=ConvertFrom-Json '{json.dumps(activation)}';"
          "$h=@(Get-CommissioningPreviewHolds $a $b);[ordered]@{holds=$h}|ConvertTo-Json -Compress")
    value=ps(code)
    assert bool(value['holds'])==bool(change)
    if change=='other_hold':assert 'Private state collision' in value['holds'][0]


@pytest.mark.parametrize('change',['','capacity','not_deferred','string_deferred','acceptance','repair','jobs','scope','mode'])
def test_first_start_preview_distinguishes_deferred_system_checks_and_limited_monitoring(change):
    value={'schema':'cochem-warden-commissioning-plan/1','mode':'READ_ONLY_PLAN',
           'runtime_root':INSTALL,'identities':6,'shared_slots':4,
           'auth_completion_present':False,'auth_completion_required':True,
           'system_preflight_deferred':True,'monitoring_scope':'heartbeat_and_queue_only',
           'model_jobs_submitted':0,'full_srs_acceptance':False,
           'automatic_repair_enabled':False,'automatic_retry_allowed':False,'holds':[]}
    if change=='capacity':value['shared_slots']=6
    if change=='not_deferred':value['system_preflight_deferred']=False
    if change=='string_deferred':value['system_preflight_deferred']='True'
    if change=='acceptance':value['full_srs_acceptance']=True
    if change=='repair':value['automatic_repair_enabled']=True
    if change=='jobs':value['model_jobs_submitted']=1
    if change=='scope':value['monitoring_scope']='full_resource_acceptance'
    if change=='mode':value['mode']='APPLY'
    code=(f"$value=ConvertFrom-Json '{json.dumps(value)}';"
          "$ok=$false;try{$null=Assert-CommissioningPreview 'first_controller_start' $value;$ok=$true}catch{};"
          "[ordered]@{accepted=$ok}|ConvertTo-Json -Compress")
    assert ps(code)['accepted']==(not change)
