"""Ordinary Windows fixtures: monitoring setup cannot authenticate or first-start."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

W=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('post_commissioning_baseline',W/'test_protected_acceptance_setup_r3_v1.py')
baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
baseline.SCRIPT=W/'run-post-commissioning-setup-r3-v1.ps1'
for name in dir(baseline):
    if name.startswith('test_') and name not in ('test_only_fresh_commissioning_can_launch_old_series','test_real_phase_flow_stops_and_preserves_first_failed_intent','test_frozen_oracle_postconditions_conflict_with_frozen_first_start'):
        globals()['test_preserved_'+name[5:]]=getattr(baseline,name)

@pytest.mark.parametrize('staged',[False,True])
def test_exact_post_commissioning_disposition_never_launches_first_start(staged):
    result=baseline.run_ps(f"@(Get-SetupDisposition commissioned ${str(staged).lower()})|ConvertTo-Json")
    assert [x['mode'] for x in result]==['deferred_maintenance','reuse_verified_receipt' if staged else 'execute','reattest_current_instance','execute','execute']

@pytest.mark.parametrize('branch',['fresh','unknown',''])
def test_missing_commissioning_is_not_a_startup_authority(branch):
    result=baseline.run_ps(f"$refused=$false;try{{Get-SetupDisposition '{branch}' $false|Out-Null}}catch{{$refused=$_.Exception.Message -ceq 'SETUP_COMMISSIONING_REQUIRED_NO_FIRST_START'}};@{{refused=$refused}}|ConvertTo-Json")
    assert result['refused']

def test_even_an_injected_execute_mode_cannot_replay_first_start():
    result=baseline.run_ps(r'''
    $script:calls=0;$script:records=0;$script:witnesses=0;$refused=$false
    try{Invoke-SetupPhases @([pscustomobject]@{phase='first_start';mode='execute'}) {
      param($n)$script:calls++;0
    } {param($n)$script:calls++} {param($n,$s,$p)$script:records++} {$script:witnesses++}}
    catch{$refused=$_.Exception.Message -ceq 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}
    @{refused=$refused;calls=$calls;records=$records;witnesses=$witnesses}|ConvertTo-Json
    ''')
    assert result=={'refused':True,'calls':0,'records':0,'witnesses':0}

@pytest.mark.parametrize('failure',['','independent_staging','held_observation','resource_observation','first_start_verify'])
def test_actual_monitoring_phase_flow_preserves_failure_and_skips_every_later_action(tmp_path,failure):
    value=baseline.run_ps(f"$fixture='{tmp_path}';$failure='{failure}';"+r'''
    $script:events=[Collections.Generic.List[string]]::new();$script:calls=[Collections.Generic.List[string]]::new();$failed=$false
    try{Invoke-SetupPhases @(Get-SetupDisposition commissioned $false) {
      param($n)$script:calls.Add($n);if($failure -ceq $n){2}else{0}
    } {param($n)if($failure -ceq 'first_start_verify' -and $n -ceq 'first_start'){throw 'inert stale identity'};@{phase=$n}} {
      param($n,$s,$p)$script:events.Add($n+':'+$s)
      $file=Join-Path $fixture ($n+'-'+$s+'.json');$f=[IO.File]::Open($file,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$f.WriteByte(1)}finally{$f.Dispose()}
    } { }
    }catch{$failed=$true}
    @{failed=$failed;calls=@($script:calls.ToArray());events=@($script:events.ToArray())}|ConvertTo-Json
    ''')
    expected=['independent_staging','held_observation','resource_observation']
    if failure=='first_start_verify':expected=expected[:1]
    elif failure:expected=expected[:expected.index(failure)+1]
    assert value['calls']==expected and value['failed']==bool(failure)
    assert 'first_start:STARTED' not in value['events']
    if failure in ('independent_staging','held_observation','resource_observation'):
        assert value['events'][-1]==failure+':STARTED'

def test_final_phase_helpers_and_manifest_are_bound_to_current_successors():
    text=baseline.SCRIPT.read_text()
    for name in ('install-resource-observer-r3-v3.ps1','install-independent-supervisor-staging-r3-v2.ps1','install-held-supervisor-observation-r3-v2.ps1','run-pipeline-commissioning-r3-v4.ps1'):
        assert name in text and hashlib.sha256((W/name).read_bytes()).hexdigest() in text
    manifest=W/'resource-observer-r3-v3/source-manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() in text
    assert 'ResourceObservation4.2.7-windows-20261008-r3-v3' in text
    assert 'ResourceObservation4.2.7-windows-20261008-r3-v2' not in text
    assert " $branch='commissioned'" in text
    assert "$scheduler.GetFolder('\\')" in text and "$scheduler.GetFolder('')" not in text
    assert "if($name -ceq 'first_start'){throw 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}" in text

def test_actual_default_preview_leaves_uncommissioned_host_unchanged():
    result=subprocess.run([baseline.PS,'-NoProfile','-NonInteractive','-File',str(baseline.SCRIPT)],capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert value['mode']=='READ_ONLY_PLAN' and value['branch']=='commissioned'
    assert value['commissioning_required_before_apply'] and not value['authentication_or_first_start_invoked']
    assert not value['current_commissioned_instance_reattested']
    assert any(x['code']=='COMMISSIONING_REQUIRED_NO_AUTHENTICATION_OR_STARTUP_IN_THIS_BATCH' for x in value['holds'])
    assert value['shared_capacity']==4 and value['identities']==6
    assert not Path(value['series_root']).exists()
    with (W/'post-commissioning-setup-r3-v1-preview-final.json').open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2);stream.write('\n')
