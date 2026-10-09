"""Ordinary Windows fixtures: no provider login, task execution or deployment."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

HERE=Path(__file__).resolve().parent
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

first=load('first_start_recovery_baseline_tests',HERE/'test_first_warden_commissioning.py')
first.M=load('first_start_recovery',HERE/'commission-first-warden-r3-v2.py')
first.WRAPPER=HERE/'commission-first-warden-r3-v2.ps1'
old_fixture=first.auth_fixture

def rebind(value):
    if isinstance(value,str):
        for kind in ('Both','Six','StatusSix'):
            value=value.replace(f'NativeAuth{kind}4.2.7-windows-20261007-r3-v1',f'NativeAuth{kind}4.2.7-windows-20261008-r3-v2')
        return value
    if isinstance(value,Path):return Path(rebind(str(value)))
    if isinstance(value,dict):return {rebind(k):rebind(v) for k,v in value.items()}
    if isinstance(value,list):return [rebind(v) for v in value]
    if isinstance(value,tuple):return tuple(rebind(v) for v in value)
    return value

first.auth_fixture=lambda:rebind(old_fixture())
for name in dir(first):
    if name.startswith('test_') and name!='test_duplicate_profile_and_alternate_receipt_path_refused':globals()['test_first_start_'+name[5:]]=getattr(first,name)

series=load('commissioning_recovery_baseline_tests',HERE/'test_pipeline_commissioning_series_r3.py')
series.SOURCE=HERE/'run-pipeline-commissioning-r3-v2.ps1'
old_ps=series.ps
series.ps=lambda code:old_ps('$priorCustody=[pscustomobject]@{fixture=$true};'+code)
for name in dir(series):
    if name.startswith('test_'):globals()['test_series_'+name[5:]]=getattr(series,name)

def prior_fixture():
    specs=(('Commission','cochem-pipeline-commissioning-series/1','cochem-pipeline-commissioning-series-failure/1','reviewed_series'),
           ('Auth','cochem-both-providers-status-first/1','cochem-both-providers-status-first-failure/1','attended_provider_series'),
           ('Worker','cochem-six-worker-status-first/1','cochem-six-worker-status-first-failure/1','status_first_authentication'))
    values={}
    for i,(key,start_schema,failure_schema,phase) in enumerate(specs):
        nonce=str(i+1)*32
        values[key+'Start']=dict(schema=start_schema,status='IN_PROGRESS',nonce=nonce)
        values[key+'Failure']=dict(schema=failure_schema,nonce=nonce,phase=phase,error_type='RuntimeException')
    values['CommissionFailure'].update(automatic_repeat_allowed=False,partial_outputs_preserved=True,full_srs_acceptance=False)
    values['AuthFailure'].update(automatic_repeat_allowed=False,activation_ready=False)
    values['WorkerFailure'].update(automatic_resume_allowed=False,configuration_applied=False,activation_ready=False,provider='codex')
    values['WorkerStart'].update(provider='codex',runtime={'install_receipt_sha256':first.M.INSTALL_HASH,'configuration_sha256':first.M.CONFIG_HASH,'revision':{'source_sha256':first.M.REVISION_HASH}})
    for key,state in [('Before','STATUS_BEFORE_COMPLETE'),('Login','LOGIN_STARTED')]:
        values[key]=dict(schema='cochem-six-worker-auth-event/1',provider='codex',slot='slot1',nonce=values['WorkerStart']['nonce'],state=state)
    values['Before']['proof']=dict(decision='ATTENDED_LOGIN_REQUIRED',stage='before',receipt_path=r'C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-slot1-codex-before\worker-native-status.json',receipt_sha256='a'*64)
    return values

@pytest.mark.parametrize('change',['','nonce','phase','provider','slot','state','prior_decision','prior_path','repeat','string_repeat','configuration','revision'])
def test_preserved_failure_requires_the_reported_attempt_without_authorizing_login(tmp_path,change):
    value=prior_fixture()
    if change=='nonce':value['WorkerFailure']['nonce']='f'*32
    if change=='phase':value['CommissionFailure']['phase']='first_controller_start'
    if change=='provider':value['WorkerFailure']['provider']='claude'
    if change=='slot':value['Login']['slot']='slot2'
    if change=='state':value['Login']['state']='LOGIN_EXITED_ZERO'
    if change=='prior_decision':value['Before']['proof']['decision']='REUSE_VERIFIED_SESSION'
    if change=='prior_path':value['Before']['proof']['receipt_path']+='-alternate'
    if change=='repeat':value['AuthFailure']['automatic_repeat_allowed']=True
    if change=='string_repeat':value['AuthFailure']['automatic_repeat_allowed']='False'
    if change=='configuration':value['WorkerStart']['runtime']['configuration_sha256']='f'*64
    if change=='revision':value['WorkerStart']['runtime']['revision']['source_sha256']='f'*64
    path=tmp_path/'prior.json';path.write_text(json.dumps(value))
    actual=series.ps(f"$v=Get-Content -LiteralPath '{path}' -Raw|ConvertFrom-Json;$records=@{{}};foreach($p in $v.PSObject.Properties){{$records[$p.Name]=$p.Value}};"
       "$accepted=$false;try{Assert-RecoveryPriorRecords @records;$accepted=$true}catch{};@{accepted=$accepted}|ConvertTo-Json -Compress")
    assert actual['accepted']==(not change)

def test_first_start_only_rebinds_authentication_preserving_all_launch_guards():
    old=(HERE/'commission-first-warden-r3-v1.py').read_text()
    expected=rebind(old).replace("AUTH_HELPER_HASH = 'cd93a330a2d4c578871b4d479067cc5f65f5ddd49bf2a6154c2910f63b5d1ea5'",f"AUTH_HELPER_HASH = '{hashlib.sha256((HERE/'worker-native-auth-status-six-r3-v2.py').read_bytes()).hexdigest()}'")
    assert (HERE/'commission-first-warden-r3-v2.py').read_text()==expected
    old=(HERE/'commission-first-warden-r3-v1.ps1').read_text()
    expected=rebind(old).replace("$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v1.py';$sourceHash='bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c'",f"$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v2.py';$sourceHash='{hashlib.sha256((HERE/'commission-first-warden-r3-v2.py').read_bytes()).hexdigest()}'")
    assert (HERE/'commission-first-warden-r3-v2.ps1').read_text()==expected

@pytest.mark.parametrize('change',['duplicate','old_path','old_leaf_pin'])
def test_first_start_rejects_duplicate_or_prior_namespace_authentication(change):
    controls=first.auth_fixture()
    path=first.M.ROOT.parent/'NativeAuthSix4.2.7-windows-20261008-r3-v2-codex/series-complete.json'
    if change=='duplicate':controls[path][0]['status_receipts'][5]=copy.deepcopy(controls[path][0]['status_receipts'][0])
    if change=='old_path':controls[path][0]['status_receipts'][0]['proof']['receipt_path']=controls[path][0]['status_receipts'][0]['proof']['receipt_path'].replace('20261008-r3-v2','20261007-r3-v1')
    if change=='old_leaf_pin':next(iter(controls.values()))[0]['helper_sha256']='cd93a330a2d4c578871b4d479067cc5f65f5ddd49bf2a6154c2910f63b5d1ea5'
    with pytest.raises(ValueError):first.M.validate_auth(first.reader(controls))

def test_actual_preview_runs_no_native_commands_or_controller_and_preserves_fresh_roots():
    roots=[Path(r'C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v2'),Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1')]
    before=[p.exists() for p in roots]
    run=subprocess.run([PS,'-NoProfile','-NonInteractive','-File',str(series.SOURCE)],capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
    value=json.loads(run.stdout)
    assert value['mode']=='READ_ONLY_PLAN' and value['tasks_registered']==value['model_jobs_submitted']==0
    assert value['identity_count']==6 and value['shared_capacity']==4
    assert [p.exists() for p in roots]==before==[False,False]
    (HERE/'login-recovery-commissioning-r3-v2-preview.json').write_text(json.dumps(value,indent=2)+'\n')
