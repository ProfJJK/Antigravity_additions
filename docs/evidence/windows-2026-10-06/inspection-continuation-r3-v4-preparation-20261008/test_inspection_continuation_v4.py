"""Successor wrapper checks; ordinary Windows only, no provider/task execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

W=Path(__file__).resolve().parent
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,W/file)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

auth=load('v4_auth_baseline','test_native_auth_checkpoint_r3_v3.py')
auth.LEAF=W/'check-worker-native-auth-status-six-r3-v4.ps1'
auth.SERIES=W/'login-six-workers-status-first-r3-v4.ps1'
auth.OUTER=W/'authenticate-native-profiles-status-first-r3-v4.ps1'
for name in dir(auth):
    if name.startswith('test_'):globals()['test_auth_'+name[5:]]=getattr(auth,name)

commission=load('v4_commission_baseline','test_login_continuation_commissioning_r3_v3.py')
commission.first.WRAPPER=W/'commission-first-warden-r3-v4.ps1'
commission.series.SOURCE=W/'run-pipeline-commissioning-r3-v4.ps1'
for name in dir(commission):
    if name.startswith('test_') and name!='test_actual_preview_is_read_only_with_absent_first_start':
        globals()['test_commission_'+name[5:]]=getattr(commission,name)

def digest(name):return hashlib.sha256((W/name).read_bytes()).hexdigest()

def test_all_active_scanner_bindings_use_the_exact_new_helper():
    helper=digest('protected-code-inspection-v4.ps1')
    for name in ('check-worker-native-auth-status-six-r3-v4.ps1','login-six-workers-status-first-r3-v4.ps1','login_pipeline_worker_interactive_r3_v4.ps1','commission-first-warden-r3-v4.ps1'):
        text=(W/name).read_text()
        assert 'protected-code-inspection-v4.ps1' in text and helper in text,name
        assert 'AddMinutes(4)' not in text and 'Protected code inspection exceeded its bound' not in text,name
        assert 'Assert-CodeTreeOnce' in text,name
    assert "'Assert-VenvBinding','Assert-CodeTreeOnce'" not in auth.SERIES.read_text()
    assert "@('Assert-CodeTreeOnce','Assert-VenvBinding'" not in (W/'login_pipeline_worker_interactive_r3_v4.ps1').read_text()

def test_claude_successor_and_entire_transitive_wrapper_chain_are_pinned():
    claude=digest('login_pipeline_worker_interactive_r3_v4.ps1')
    for file in (auth.SERIES,auth.OUTER,commission.series.SOURCE):
        text=file.read_text();assert 'login_pipeline_worker_interactive_r3_v4.ps1' in text and claude in text
    assert '$held.Add((Open-VerifiedFile $inspectionPath' in (W/'login_pipeline_worker_interactive_r3_v4.ps1').read_text()
    top=commission.series.SOURCE.read_text()
    assert digest(auth.OUTER.name) in top and digest(commission.first.WRAPPER.name) in top and digest(auth.SERIES.name) in top
    assert digest('commission-first-warden-r3-v3.py') in commission.first.WRAPPER.read_text()
    assert digest('worker-native-auth-status-six-r3-v3.py') in auth.LEAF.read_text()

def test_authentication_protocol_python_and_host_configuration_remain_frozen():
    assert digest('worker-native-auth-status-six-r3-v3.py')=='7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1'
    assert digest('commission-first-warden-r3-v3.py')=='9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755'
    assert digest('run-pipeline-commissioning-r3-v3.ps1')=='dda02eeee0006509fc8c32319f74e089e4a79fbfdda659bbb8a443eaa8640aa0'
    config=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\pipeline.json')
    assert hashlib.sha256(config.read_bytes()).hexdigest()=='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'

def test_actual_v4_preview_is_read_only_and_preserves_first_start_fences():
    run=subprocess.run([PS,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',f"& '{commission.series.SOURCE}'"],capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stdout+run.stderr
    value=json.loads(run.stdout)
    assert value['mode']=='READ_ONLY_PLAN' and value['tasks_registered']==value['model_jobs_submitted']==0
    assert value['identity_count']==6 and value['shared_capacity']==4
    assert not Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1').exists()
    assert not Path(r'C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v3-'+'0'*32).exists()
    with (W/'inspection-continuation-v4-preview-final.json').open('x',encoding='utf-8') as f:
        json.dump(value,f,indent=2);f.write('\n')
