"""Ordinary Windows fixtures only; no login, scheduled task or controller runs."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import uuid

import pytest

HERE=Path(__file__).resolve().parent
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
ATTEMPT='c'*32

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

first=load('first_start_continuation_baseline_tests',HERE/'test_first_warden_commissioning.py')
first.M=load('first_start_continuation',HERE/'commission-first-warden-r3-v3.py')
validate_auth=first.M.validate_auth
first.M.validate_auth=lambda read:validate_auth(read,ATTEMPT)
first.M.AUTH_ROOT=first.M.ROOT.parent/(first.M.AUTH_PREFIX+ATTEMPT)
first.WRAPPER=HERE/'commission-first-warden-r3-v3.ps1'
original_first_ps=first.run_ps
first.run_ps=lambda body:original_first_ps(f"$Attempt='{ATTEMPT}';"+body)
old_fixture=first.auth_fixture

def rebind(value):
    if isinstance(value,str):
        for kind in ('Both','Six','StatusSix'):
            value=value.replace(f'NativeAuth{kind}4.2.7-windows-20261007-r3-v1',f'NativeAuth{kind}4.2.7-windows-20261008-r3-v3-{ATTEMPT}')
        return value
    if isinstance(value,Path):return Path(rebind(str(value)))
    if isinstance(value,dict):
        result={rebind(k):rebind(v) for k,v in value.items()}
        if result.get('schema') in ('cochem-both-providers-status-first-result/1','cochem-six-worker-status-first-result/1','cochem-worker-native-auth-status/1'):result['attempt']=ATTEMPT
        return result
    if isinstance(value,list):return [rebind(v) for v in value]
    if isinstance(value,tuple):return tuple(rebind(v) for v in value)
    return value

first.auth_fixture=lambda:rebind(old_fixture())
for name in dir(first):
    if name.startswith('test_') and name!='test_duplicate_profile_and_alternate_receipt_path_refused':globals()['test_first_'+name[5:]]=getattr(first,name)

series=load('continuation_series_baseline_tests',HERE/'test_pipeline_commissioning_series_r3.py')
series.SOURCE=HERE/'run-pipeline-commissioning-r3-v3.ps1'
original_series_ps=series.ps
def series_ps(body):
    value=original_series_ps(f"$Attempt='{ATTEMPT}';$commissionGate=$null;$priorCustody=[pscustomobject]@{{fixture=$true}};"+body)
    # Verify the added attempt argument before applying the unchanged baseline
    # assertions to the other exact console arguments.
    if isinstance(value,dict) and 'calls' in value:
        for call in value['calls']:
            assert call.count('-Attempt')==1
            index=call.index('-Attempt');assert call[index+1]==ATTEMPT
            del call[index:index+2]
    return value
series.ps=series_ps
for name in dir(series):
    if name.startswith('test_'):globals()['test_series_'+name[5:]]=getattr(series,name)

@pytest.mark.parametrize('attempt',['0'*32,'C'*32,'c'*31,'../other',None,1])
def test_first_start_rejects_unbound_or_preview_attempt(attempt):
    with pytest.raises(ValueError,match='AUTH_ATTEMPT'):validate_auth(first.reader(first.auth_fixture()),attempt)

@pytest.mark.parametrize('level',['outer','provider','leaf','old_path','old_pin','duplicate'])
def test_first_start_requires_exact_attempt_on_all_authentication_levels(level):
    controls=first.auth_fixture()
    outer=controls[first.M.AUTH_ROOT/'series-complete.json'][0]
    provider_path=first.M.ROOT.parent/f'NativeAuthSix4.2.7-windows-20261008-r3-v3-{ATTEMPT}-codex/series-complete.json'
    provider=controls[provider_path][0]
    leaf=next(iter(controls.values()))[0]
    if level=='outer':outer['attempt']='d'*32
    if level=='provider':provider['attempt']='d'*32
    if level=='leaf':leaf['attempt']='d'*32
    if level=='old_path':provider['status_receipts'][0]['proof']['receipt_path']=provider['status_receipts'][0]['proof']['receipt_path'].replace(f'v3-{ATTEMPT}','v2')
    if level=='old_pin':leaf['helper_sha256']='98d6e5400041fc51692703c0fd8d8ea2ade6e59bc3de49fb3efdd421dcd5f945'
    if level=='duplicate':provider['status_receipts'][5]=copy.deepcopy(provider['status_receipts'][0])
    with pytest.raises(ValueError):validate_auth(first.reader(controls),ATTEMPT)

def test_launch_and_preservation_functions_unchanged_from_reviewed_first_start():
    old=ast.parse((HERE/'commission-first-warden-r3-v2.py').read_text())
    new=ast.parse((HERE/'commission-first-warden-r3-v3.py').read_text())
    old_functions={n.name:ast.dump(n,include_attributes=False) for n in old.body if isinstance(n,ast.FunctionDef)}
    new_functions={n.name:ast.dump(n,include_attributes=False) for n in new.body if isinstance(n,ast.FunctionDef)}
    assert old_functions.keys()==new_functions.keys()
    for name in old_functions:
        if name not in ('validate_auth','run'):assert old_functions[name]==new_functions[name],name
    assert first.M.AUTH_HELPER_HASH==hashlib.sha256((HERE/'worker-native-auth-status-six-r3-v3.py').read_bytes()).hexdigest()

def test_authentication_pause_skips_first_start_and_retains_a_bound_pause_event():
    result=series.ps(r'''
    $script:events=[Collections.Generic.List[string]]::new()
    function Read-CommissioningPausedAuthEvidence{[pscustomobject]@{attempt=$Attempt;status='AUTHENTICATION_PAUSED_BEFORE_LOGIN'}}
    $out=Invoke-CommissioningPhases {
      param($phase)$script:events.Add('invoke:'+$phase);return 20
    } {param($phase)throw 'Completion verifier must not run on pause'} {
      param($phase,$state,$proof)$script:events.Add('record:'+$phase+':'+$state)
      if($state -ceq 'PAUSED' -and $proof.attempt -cne $Attempt){throw 'Pause binding changed'}
    }
    @{exit_code=$out;events=@($script:events.ToArray())}|ConvertTo-Json -Compress
    ''')
    assert result=={'exit_code':20,'events':['record:native_authentication:STARTED','invoke:native_authentication','record:native_authentication:PAUSED']}

def test_native_mutex_rejects_concurrent_attempt_and_releases_without_changing_production_object():
    name=r'Global\CoChem427-FixtureAuth-'+uuid.uuid4().hex
    result=series.ps("$fixtureName='"+name+"';"+r'''
    $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('''+"'"+str(series.SOURCE)+"'"+r''',[ref]$t,[ref]$e)
    $fn=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Enter-AttendedCommissioning'},$true))[0].Extent.Text
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $fn=$fn.Replace('O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)',('O:'+$sid+'G:'+$sid+'D:P(A;;GA;;;'+$sid+')')).Replace('Global\CoChemPipeline427-AttendedCommissioning',$fixtureName)
    . ([scriptblock]::Create($fn))
    $first=Enter-AttendedCommissioning;$blocked=$false
    try{try{$second=Enter-AttendedCommissioning;$second.Dispose()}catch{$blocked=$true}}
    finally{$first.ReleaseMutex();$first.Dispose()}
    $third=Enter-AttendedCommissioning;try{@{concurrent_blocked=$blocked;fresh_after_release=$true}|ConvertTo-Json -Compress}finally{$third.ReleaseMutex();$third.Dispose()}
    ''')
    assert result=={'concurrent_blocked':True,'fresh_after_release':True}

@pytest.mark.parametrize('slot,version',[('slot1','v1'),('slot5','v2')])
def test_reported_prior_failures_have_versioned_slot_binding(tmp_path,slot,version):
    fixtures=load('prior_recovery_fixtures',HERE/'test_login_recovery_commissioning_r3_v2.py')
    value=fixtures.prior_fixture()
    for name in ('Before','Login'):value[name]['slot']=slot
    if version=='v2':value['Before']['proof']['receipt_path']=value['Before']['proof']['receipt_path'].replace('20261007-r3-v1-slot1','20261008-r3-v2-slot5')
    path=tmp_path/'prior.json';path.write_text(json.dumps(value))
    result=series.ps(f"$v=Get-Content -LiteralPath '{path}' -Raw|ConvertFrom-Json;$records=@{{}};foreach($p in $v.PSObject.Properties){{$records[$p.Name]=$p.Value}};Assert-RecoveryPriorRecords @records -FailedSlot {slot} -Version {version};@{{accepted=$true}}|ConvertTo-Json -Compress")
    assert result['accepted']

def test_actual_preview_is_read_only_with_absent_first_start():
    run=subprocess.run([PS,'-NoProfile','-NonInteractive','-File',str(series.SOURCE)],capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
    value=json.loads(run.stdout)
    assert value['mode']=='READ_ONLY_PLAN' and value['tasks_registered']==value['model_jobs_submitted']==0
    assert value['identity_count']==6 and value['shared_capacity']==4
    assert not Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1').exists()
    assert not Path(r'C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v3-'+'0'*32).exists()
    (HERE/'login-continuation-commissioning-r3-v3-preview.json').write_text(json.dumps(value,indent=2)+'\n')
