"""Ordinary Windows fixtures only: no native provider, SYSTEM or live task calls."""
from contextlib import contextmanager
from copy import deepcopy
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

W=Path(__file__).parent
PY=W/'worker-native-auth-status-six-r3-v1.py'
LEAF=W/'check-worker-native-auth-status-six-r3-v1.ps1'
SERIES=W/'login-six-workers-status-first-r3-v1.ps1'
OUTER=W/'authenticate-native-profiles-status-first-r3-v1.ps1'
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')

def module(path):
    spec=importlib.util.spec_from_file_location(path.stem.replace('-','_'),path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

status=module(PY)

def functions(path):
    return f"$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e);if($e.Count){{throw $e[0]}};foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};"

def ps(code,timeout=30):
    prefix="$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME \"Modules\\$m\\$m.psd1\")};"
    return subprocess.run([PS,'-NoProfile','-NonInteractive','-Command',prefix+code],capture_output=True,text=True,timeout=timeout)

def success(result):
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(result.stdout)

def auth(provider,kind):
    if provider=='codex':
        return status.parse_status(provider,b'Logged in using ChatGPT' if kind=='reuse' else b'Not logged in' if kind=='missing' else b'unknown private-value',b'',0 if kind=='reuse' else 1)
    value={'loggedIn':kind!='missing','authMethod':'none' if kind=='missing' else 'api_key' if kind=='other' else 'claude.ai','apiProvider':'firstParty','subscriptionType':'max','email':'private@example.invalid'}
    return status.parse_status(provider,json.dumps(value).encode(),b'',1 if kind=='missing' else 0)

@pytest.mark.parametrize('provider,kind,expected',[
    ('codex','reuse','REUSE_VERIFIED_SESSION'),('codex','missing','ATTENDED_LOGIN_REQUIRED'),
    ('codex','other','HOLD'),('claude','reuse','REUSE_VERIFIED_SESSION'),
    ('claude','missing','ATTENDED_LOGIN_REQUIRED'),('claude','other','HOLD')])
def test_real_parser_only_explicit_logged_out_offers_login(provider,kind,expected):
    result=auth(provider,kind)
    assert status.authentication_decision(result)==expected
    assert 'private' not in json.dumps(result)

@pytest.mark.parametrize('mutation',[
    {'protocol_valid':False},{'logged_in':0},{'logged_in':True,'auth_kind':'none'},
    {'native_subscription_authentication_verified':0},{'protocol_valid':1},
    {'native_subscription_authentication_verified':False,'logged_in':True}])
def test_ambiguous_status_does_not_authorize_login(mutation):
    value=auth('codex','missing');value.update(mutation)
    assert status.authentication_decision(value)=='HOLD'

def test_frozen_parser_and_runtime_primitives_are_unchanged():
    old=ast.parse((W/'worker-native-status-r3.py').read_text());new=ast.parse(PY.read_text())
    functions_old={x.name:ast.dump(x,include_attributes=False) for x in old.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}
    functions_new={x.name:ast.dump(x,include_attributes=False) for x in new.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}
    for name,value in functions_old.items():
        if name not in ('run','main'):assert functions_new[name]==value,name

@pytest.mark.parametrize('provider,kind,stage',[('codex','reuse','before'),('codex','missing','before'),('codex','other','before'),('claude','reuse','after'),('claude','missing','after')])
def test_full_python_run_keeps_real_parser_receipt_and_one_command(tmp_path,monkeypatch,provider,kind,stage):
    """Actual helper run, real bounded temporary output and receipt writes; native custody/launch simulated."""
    from cochem_pipeline import windows as win
    from cochem_pipeline import ramdisk
    m=module(PY);base=tmp_path/'protected-fixture';base.mkdir()
    root=base/f'NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-slot1-{provider}-{stage}';root.mkdir()
    copied=root/PY.name;copied.write_bytes(PY.read_bytes())
    private=tmp_path/'private';private.mkdir()
    config=json.loads((INSTALL/'pipeline.json').read_bytes());layout=json.loads((INSTALL/'windows-layout.json').read_bytes())
    config['private_root']=layout['private_root']=str(private)
    original_path=Path
    monkeypatch.setattr(m,'Path',lambda x:base if str(x)==r'C:\Program Files\CoChem' else original_path(x))
    monkeypatch.setattr(m,'__file__',str(copied));monkeypatch.setattr(m,'sys',SimpleNamespace(executable=str(m.PYTHON)))
    monkeypatch.setattr(m,'validate_interpreter',lambda:None)
    monkeypatch.setattr(m,'verify_r3_runtime',lambda *a:{'verified':True,'source_sha256':m.REVISION_SHA256})
    monkeypatch.setattr(m,'protected_json',lambda _,p:(layout,m.LAYOUT_SHA256) if p.name=='windows-layout.json' else (config,m.CONFIG_SHA256))
    def digest(p):
        if p==Path(win.__file__):return m.WINDOWS_SHA256
        if p.name==provider+'.exe':return m.CONTRACTS[provider]['sha256']
        return hashlib.sha256(p.read_bytes()).hexdigest()
    monkeypatch.setattr(m,'digest_file',digest)
    for name in ('require_system','validate_code_path','validate_private_directory','_validate_control_ancestors','_validate_boundary'):
        monkeypatch.setattr(win,name,lambda *a,**kw:None)
    monkeypatch.setattr(ramdisk,'ordinary_tree',lambda *a:None)
    monkeypatch.setattr(win,'_api',lambda:{'kernel32':SimpleNamespace(CreateMutexW=lambda *a:17)})
    monkeypatch.setattr(win,'_check',lambda value,*a:value)
    monkeypatch.setattr(win,'_close',lambda *a:None)
    monkeypatch.setattr(win,'_account_sid',lambda *a:layout['slots']['slot1']['sid'])
    monkeypatch.setattr(win,'_sid_text',lambda x:x)
    monkeypatch.setattr(win,'_layout_paths',lambda p,*a:(p,{}))
    monkeypatch.setattr(win,'_layout_boundaries',lambda *a:[tmp_path/'boundary'])
    monkeypatch.setattr(m,'require_stopped',lambda *a:None)
    calls=[]
    @contextmanager
    def launch(_win,identity,argv,cwd,empty,output,errors,limits,report):
        calls.append(argv)
        if provider=='codex':raw=b'Logged in using ChatGPT' if kind=='reuse' else b'Not logged in' if kind=='missing' else b'private-unknown'
        else:raw=json.dumps({'loggedIn':kind=='reuse','authMethod':'claude.ai' if kind=='reuse' else 'none','apiProvider':'firstParty','subscriptionType':'max'}).encode()
        output.write(raw);output.flush()
        yield SimpleNamespace(resource_limits_evidence={'fixture':True},poll=lambda:0 if kind=='reuse' else 1)
    monkeypatch.setattr(m,'launch_status_process',launch)
    monkeypatch.setattr(m,'observe_child',lambda *a:{'token_matches_selected_worker':True,'image_matches_reviewed_executable':True,'owned_job_membership_verified':True})
    code=m.run('slot1',provider,'a'*32,stage)
    report=json.loads((root/'worker-native-status.json').read_bytes())
    decision=status.authentication_decision(auth(provider,kind))
    assert report['decision']==decision and code==(2 if decision=='HOLD' else 0)
    assert report['stage']==stage and report['cleanup_verified'] and report['daemon_states_verified_before_and_after']
    assert calls==[[str(m.NATIVE/(provider+'.exe')),*m.CONTRACTS[provider]['arguments']]]
    assert report['native_model_jobs_executed']==report['login_commands_executed']==0
    assert 'private-unknown' not in (root/'worker-native-status.json').read_text()

@pytest.mark.parametrize('missing,fail',[([],None),([2,5],None),([1,2,3,4,5,6],None),([2],'status_before'),([2],'login'),([2],'status_after')])
def test_actual_six_stage_machine_skips_valid_and_stops_first_failure(missing,fail):
    ms=','.join(map(str,missing))
    code=functions(SERIES)+f"$missing=@({ms});$failure='{fail or ''}';$script:events=[Collections.Generic.List[string]]::new();$script:logins=[Collections.Generic.List[string]]::new();$failed=$false;"
    code+=r"""
    try{Invoke-SixStatusFirst {
      param($slot,$stage);$script:events.Add("$slot|$stage")
      if($slot -ceq 'slot2' -and (($failure -ceq 'status_before' -and $stage -ceq 'before') -or ($failure -ceq 'status_after' -and $stage -ceq 'after'))){throw 'inert unreadable status'}
      [pscustomobject]@{decision=if($stage -ceq 'before' -and [int]$slot.Substring(4) -in $missing){'ATTENDED_LOGIN_REQUIRED'}else{'REUSE_VERIFIED_SESSION'}}
    } {
      param($slot);$script:logins.Add($slot);if($slot -ceq 'slot2' -and $failure -ceq 'login'){7}else{0}
    } {param($slot,$state,$proof)}}catch{$failed=$true}
    [ordered]@{events=@($script:events.ToArray());logins=@($script:logins.ToArray());failed=$failed}|ConvertTo-Json -Compress
    """
    result=success(ps(code))
    if fail:
        assert result['failed'] and all(not e.startswith('slot3|') for e in result['events'])
        assert result['logins']==([] if fail=='status_before' else ['slot2'])
    else:
        assert not result['failed'];assert result['logins']==[f'slot{i}' for i in missing]
        assert result['events']==[event for i in range(1,7) for event in ([f'slot{i}|before',f'slot{i}|after'] if i in missing else [f'slot{i}|before'])]

@pytest.mark.parametrize('decision',['HOLD','UNKNOWN',''])
def test_unknown_before_result_never_invokes_login(decision):
    code=functions(SERIES)+f"$script:logins=0;try{{Invoke-SixStatusFirst {{[pscustomobject]@{{decision='{decision}'}}}} {{$script:logins++;0}} {{param($s,$p,$r)}}}}catch{{}};$script:logins|ConvertTo-Json"
    assert success(ps(code))==0

def test_repeat_or_partial_series_refused_and_all_twelve_targets_checked():
    code=functions(SERIES)+r"""$Provider='codex';$seriesRoot='inert-existing-series';$script:checks=[Collections.Generic.List[string]]::new();function Test-Path{param($LiteralPath,$ErrorAction)$script:checks.Add($LiteralPath);return $LiteralPath -ceq $seriesRoot};function Get-ExactTaskOrAbsent{param($Folder,$Name)$script:checks.Add($Name);return $null};$holds=@(Get-SeriesFreshHolds $null);[ordered]@{holds=$holds;checks=@($script:checks.ToArray())}|ConvertTo-Json -Compress"""
    result=success(ps(code));assert len(result['holds'])==1 and 'no automatic repeat' in result['holds'][0]
    assert len(result['checks'])==25 and any('slot6-codex-after' in x for x in result['checks'])

@pytest.mark.parametrize('path',[LEAF,SERIES,OUTER])
def test_real_apply_refuses_ordinary_noninteractive_token_before_mutation(path):
    args=[PS,'-NoProfile','-NonInteractive','-File',str(path),'-Apply']
    if path!=OUTER:args+=['-Provider','codex']
    if path==LEAF:args+=['-Slot','slot1','-Stage','before']
    result=subprocess.run(args,capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and ('Administrator' in result.stderr or 'elevated interactive' in result.stderr)

def test_source_pins_namespaces_and_no_four_config_dependency():
    pysha=hashlib.sha256(PY.read_bytes()).hexdigest();leafsha=hashlib.sha256(LEAF.read_bytes()).hexdigest()
    assert pysha in LEAF.read_text() and pysha in SERIES.read_text() and leafsha in SERIES.read_text()
    assert leafsha in OUTER.read_text() and hashlib.sha256(SERIES.read_bytes()).hexdigest() in OUTER.read_text()
    assert 'pipeline-four-active' not in SERIES.read_text() and 'Assert-FourActiveProposal' not in SERIES.read_text()
    assert 'range(1, 7)' in PY.read_text() and "foreach($n in 1..6)" in SERIES.read_text()
    for p in (LEAF,SERIES,OUTER):
        assert success(ps(functions(p)+"'parsed'|ConvertTo-Json"))=='parsed'

def test_rejected_four_entrypoints_cannot_run_even_plan():
    for name in ('login-four-workers-status-first-r3-v1.ps1','check-worker-native-auth-status-r3-v1.ps1'):
        assert "throw 'DRAFT REJECTED:" in (W/name).read_text()

def receipt_fixture(provider='codex',kind='reuse',stage='before'):
    a=auth(provider,kind);decision=status.authentication_decision(a)
    return {'schema':'cochem-worker-native-auth-status/1','nonce':'a'*32,'stage':stage,'slot':'slot1','provider':provider,
        'system_sid':'S-1-5-18','helper_sha256':hashlib.sha256(PY.read_bytes()).hexdigest(),'runtime_root':str(INSTALL),
        'install_receipt_sha256':status.INSTALL_RECEIPT_SHA256,'source_manifest_sha256':status.MANIFEST_SHA256,
        'resource_limits_sha256':status.RESOURCE_SHA256,'status':'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED' if kind=='reuse' else 'AUTHENTICATION_NOT_VERIFIED',
        'config_sha256':status.CONFIG_SHA256,'layout_sha256':status.LAYOUT_SHA256,
        'revision':{'verified':True,'source_sha256':status.REVISION_SHA256},'cleanup_verified':True,'runtime_custody_verified':True,
        'daemon_states_verified_before_and_after':True,'native_model_jobs_executed':0,'login_commands_executed':0,
        'process':{'token_matches_selected_worker':True,'image_matches_reviewed_executable':True,'owned_job_membership_verified':True},
        'native_executable_sha256_before':status.CONTRACTS[provider]['sha256'],'native_executable_sha256_after':status.CONTRACTS[provider]['sha256'],
        'native_status_commands_executed':1,'authentication':a,'decision':decision}

def runtime_setup():
    return f"$runtime=[pscustomobject]@{{install_receipt_sha256='{status.INSTALL_RECEIPT_SHA256}';source_manifest_sha256='{status.MANIFEST_SHA256}';configuration_sha256='{status.CONFIG_SHA256}';revision=[pscustomobject]@{{verified=$true;source_sha256='{status.REVISION_SHA256}'}}}};"

@pytest.mark.parametrize('provider,kind,change',[('codex','reuse',''),('codex','missing',''),('claude','reuse',''),('claude','missing',''),
    ('codex','reuse','wrong_provider_kind'),('codex','missing','string_bool'),('codex','missing','cleanup'),('codex','missing','stage'),
    ('codex','missing','image'),('codex','missing','runtime'),('codex','missing','model'),('codex','missing','contradiction')])
def test_powershell_receipt_decision_requires_actual_bound_evidence(tmp_path,provider,kind,change):
    value=receipt_fixture(provider,kind)
    if change=='wrong_provider_kind':value['authentication']['auth_kind']='claude.ai'
    if change=='string_bool':value['authentication']['logged_in']='false'
    if change=='cleanup':value['cleanup_verified']=False
    if change=='stage':value['stage']='after'
    if change=='image':value['process']['image_matches_reviewed_executable']=False
    if change=='runtime':value['install_receipt_sha256']='0'*64
    if change=='model':value['native_model_jobs_executed']=1
    if change=='contradiction':value['decision']='REUSE_VERIFIED_SESSION'
    path=tmp_path/'receipt.json';path.write_text(json.dumps(value))
    code=functions(LEAF)+runtime_setup()+f"$v=Get-Content -LiteralPath '{path}' -Raw|ConvertFrom-Json;$task=[pscustomobject]@{{LastTaskResult=0}};$ok=$false;$d=$null;try{{$d=Assert-AuthStatusReceipt $v $task $runtime slot1 {provider} before ('a'*32) '{hashlib.sha256(PY.read_bytes()).hexdigest()}';$ok=$true}}catch{{}};[ordered]@{{accepted=$ok;decision=$d}}|ConvertTo-Json -Compress"
    result=success(ps(code));assert result['accepted']==(not change)
    if not change:assert result['decision']==status.authentication_decision(auth(provider,kind))

@pytest.mark.parametrize('kind,stage',[('reuse','before'),('missing','before'),('reuse','after')])
def test_real_leaf_apply_tail_copies_exact_source_then_simulated_task(tmp_path,kind,stage):
    text=LEAF.read_text();tail=text[text.index('$installedEntries=Assert-CodeTreeOnce'):text.rfind('}finally{foreach($stream in $held)')]
    tailfile=tmp_path/'tail.ps1';tailfile.write_text(tail)
    fixture=tmp_path/'report.json';fixture.write_text(json.dumps(receipt_fixture('codex',kind,stage)))
    pin=hashlib.sha256(PY.read_bytes()).hexdigest()
    code=functions(LEAF)+runtime_setup()+f"$fixture='{tmp_path}';$fixtureReport='{fixture}';$tail='{tailfile}';$Slot='slot1';$Provider='codex';$Stage='{stage}';$installRoot='{INSTALL}';$basePythonRoot='inert-base';$native='inert-native\\codex.exe';$source='{PY}';$sourceHash='{pin}';$sourceLength=(Get-Item -LiteralPath $source).Length;$targetRoot=Join-Path $fixture 'status';$python=Join-Path $installRoot '.venv\\Scripts\\python.exe';$taskName='inert-task';$held=[Collections.Generic.List[IO.FileStream]]::new();"
    code+=r"""
    function Assert-CodeTreeOnce{param($Path) 1}
    function Assert-ProtectedPath{param($Path)}
    function New-ProtectedDirectory{param($Path)$null=[IO.Directory]::CreateDirectory($Path)}
    function Open-VerifiedFile{param($Path,$Hash,$Length)
      if((Get-Item -LiteralPath $Path).Length -ne $Length -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Hash){throw 'Inert fixture content drift'}
      [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    }
    function Copy-VerifiedPayload{param($Row)$i=Open-VerifiedFile $Row.source $Row.sha256 $Row.length;try{$o=[IO.File]::Open($Row.destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$i.CopyTo($o)}finally{$o.Dispose()}}finally{$i.Dispose()}}
    function Start-Sleep{param($Milliseconds)}
    $script:ran=0;$script:registered=0
    $instance=[pscustomobject]@{State=3};$instance|Add-Member ScriptMethod Refresh{return}
    $task=[pscustomobject]@{State=3;LastTaskResult=0};$task|Add-Member ScriptMethod GetInstances{param($Flags)[pscustomobject]@{Count=0}}
    $task|Add-Member ScriptMethod Run{param($x)
      $script:ran++;$v=Get-Content -LiteralPath $fixtureReport -Raw|ConvertFrom-Json;$v.nonce=$nonce
      [IO.File]::WriteAllText((Join-Path $targetRoot 'worker-native-status.json'),($v|ConvertTo-Json -Depth 10));return $instance
    }
    $scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod NewTask{param($Flags)
      $script:action=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''}
      $actions=[pscustomobject]@{};$actions|Add-Member ScriptMethod Create{param($Kind)$script:action}
      [pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;ExecutionTimeLimit=''};Actions=$actions}
    }
    $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod RegisterTaskDefinition{param($Name,$Definition,$Flags,$User,$Password,$Logon,$Acl)
      if($Flags -ne 2 -or $User -cne 'SYSTEM' -or $Logon -ne 5 -or $null -ne $Password -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT3M' -or -not $script:action.Arguments.EndsWith(' --stage '+$Stage)){throw 'Simulated action is not the exact reviewed status stage'}
      if((Get-FileHash -LiteralPath (Join-Path $targetRoot 'worker-native-auth-status-six-r3-v1.py')).Hash.ToLowerInvariant() -cne $sourceHash){throw 'Copied helper differs'}
      $script:registered++;$task
    }
    try{. ([scriptblock]::Create([IO.File]::ReadAllText($tail)));if($script:ran -ne 1 -or $script:registered -ne 1){throw 'Expected one simulated task'}}finally{foreach($s in $held){$s.Dispose()}}
    """
    result=success(ps(code));assert result['decision']==status.authentication_decision(auth('codex',kind))
    assert result['stage']==stage and result['last_task_result']==0
    assert (tmp_path/'status'/PY.name).read_bytes()==PY.read_bytes()


@pytest.mark.parametrize('kind', ['reuse','missing','failed','wrong_task','wrong_receipt_hash','double_summary'])
def test_actual_status_child_binds_task_receipt_and_projects_only_safe_failure(tmp_path,kind):
    value=receipt_fixture()
    failed=kind in ('failed','double_summary')
    if failed:
        value.update(status='STATUS_CHECK_FAILED',decision='HOLD',cleanup_verified=False,
                     failure={'phase':'native_launch','error_type':'WindowsIsolationError','winerror':5,'message':'secret-receipt-output'},native_exit_code=2)
    elif kind=='missing':value=receipt_fixture(kind='missing')
    receipt=tmp_path/'receipt.json';receipt.write_text(json.dumps(value))
    code=functions(LEAF)+functions(SERIES)+runtime_setup()+f"$fixtureReceipt='{receipt}';$fixturePython='{PY}';$Provider='codex';$Slot='slot1';$Stage='before';$installRoot='{INSTALL}';$statusPythonHash='{hashlib.sha256(PY.read_bytes()).hexdigest()}';$statusLeaf='inert-leaf';$folder=$null;$kind='{kind}';$powershell='Invoke-InertStatus';"
    code+=r'''
    $root="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-slot1-codex-before"
    $path=Join-Path $root 'worker-native-status.json'
    $pin=(Get-FileHash -LiteralPath $fixtureReceipt).Hash.ToLowerInvariant()
    $script:summary=[ordered]@{schema='cochem-worker-native-auth-status-task-result/1';slot='slot1';provider='codex';stage='before';nonce=('a'*32);receipt_sha256=$pin;receipt_path=$path;decision=if($kind -ceq 'missing'){'ATTENDED_LOGIN_REQUIRED'}elseif($kind -in @('failed','double_summary')){'HOLD'}else{'REUSE_VERIFIED_SESSION'};secret='do-not-forward'}
    if($kind -ceq 'wrong_receipt_hash'){$script:summary.receipt_sha256=('0'*64)}
    function Invoke-InertStatus {
      $global:LASTEXITCODE=if($kind -in @('failed','double_summary')){1}else{0}
      if($global:LASTEXITCODE){
        'untrusted provider stderr private@example.invalid'
        'COCHEM_NATIVE_AUTH_STATUS_RESULT '+($script:summary|ConvertTo-Json -Compress)
        if($kind -ceq 'double_summary'){'COCHEM_NATIVE_AUTH_STATUS_RESULT '+($script:summary|ConvertTo-Json -Compress)}
      }else{$script:summary|ConvertTo-Json -Compress}
    }
    function Get-ExactTaskOrAbsent {
      param($Folder,$Name)
      $action=[pscustomobject]@{Path=(Join-Path $installRoot '.venv\Scripts\python.exe');Arguments=('-I -B "'+(Join-Path $root 'worker-native-auth-status-six-r3-v1.py')+'" --slot slot1 --provider codex --nonce '+('a'*32)+' --stage before');WorkingDirectory=$root}
      if($kind -ceq 'wrong_task'){$action.Arguments+=' --bad'}
      $actions=[pscustomobject]@{Count=1;Action=$action};$actions|Add-Member ScriptMethod Item{param($i)$this.Action}
      $t=[pscustomobject]@{State=3;LastTaskResult=if($kind -in @('failed','double_summary')){2}else{0};Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions;Settings=[pscustomobject]@{MultipleInstances=2;ExecutionTimeLimit='PT3M'}}}
      $t|Add-Member ScriptMethod GetInstances{param($f)[pscustomobject]@{Count=0}};$t
    }
    function Read-R3Control {
      param($Path,$Hash,$Maximum=16777216)
      $p=if($Path.EndsWith('.py')){$fixturePython}else{$fixtureReceipt}
      $h=(Get-FileHash -LiteralPath $p).Hash.ToLowerInvariant()
      if($h -cne $Hash -or (Get-Item -LiteralPath $p).Length -gt $Maximum){throw 'Fixture pin mismatch'}
      [pscustomobject]@{Path=$p;Sha256=$h}
    }
    function Read-R3Text{param($Control)[IO.File]::ReadAllText($Control.Path)}
    $rejected=$false;$decision=$null
    try{$decision=(Invoke-AuthStatusChild slot1 before).decision}catch{$rejected=$true}
    [ordered]@{fixture=$true;rejected=$rejected;decision=$decision;preference=[string]$ErrorActionPreference}|ConvertTo-Json -Compress
    '''
    r=ps(code);assert r.returncode==0,r.stdout+r.stderr
    result=json.loads(next(x for x in r.stdout.splitlines() if x.startswith('{"fixture"')))
    assert result['rejected']==(kind not in ('reuse','missing'))
    assert result['preference']=='Stop'
    assert 'private@example' not in r.stdout and 'secret-receipt' not in r.stdout and 'do-not-forward' not in r.stdout
    tagged=[x for x in r.stdout.splitlines() if x.startswith('COCHEM_NATIVE_AUTH_STATUS_RESULT ')]
    assert len(tagged)==int(kind=='failed')
    if tagged:
        safe=json.loads(tagged[0].split(' ',1)[1]);assert safe['failure']=={'phase':'native_launch','error_type':'WindowsIsolationError','winerror':5}
        assert safe['receipt_sha256']==hashlib.sha256(receipt.read_bytes()).hexdigest() and safe['native_exit_code']==2


def ordinary_record_setup():
    return r'''
    function Assert-SeriesPrivateRoot{param($Path)if(-not [IO.Directory]::Exists($Path)){throw 'fixture directory absent'}}
    function New-PrivateAcl{
      $a=[Security.AccessControl.FileSecurity]::new();$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false);$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));$a
    }
    function Initialize-SeriesDirectory{
      Add-Type -TypeDefinition 'using System.IO;public static class CoChemNativeLoginSeriesDirectory {public static void Create(string p){if(Directory.Exists(p))throw new IOException("exists");Directory.CreateDirectory(p);}}'
    }
    function Assert-CodeTreeOnce{param($Path)1}
    function Assert-SeriesDaemonsStopped{param($Folder)}
    function Get-SeriesFreshHolds{param($Folder)}
    function Get-SeriesWorkerRootHolds{param($Layout)}
    function Assert-ProtectedPath{param($Path)}
    '''


@pytest.mark.parametrize('provider,fail_at',[('codex',''),('claude',''),('codex','slot3'),('claude','after')])
def test_real_provider_apply_tail_journals_exact_attended_login_arguments(tmp_path,provider,fail_at):
    text=SERIES.read_text();start=text.index(' if($holds.Count){throw ($holds -join')
    tail=text[start:text.index('}finally{foreach($stream in $held)',start)]
    # Retain the real catch and exclusive failure journal as well as the action branch.
    tail='try{\n'+tail+'}'
    tailfile=tmp_path/'tail.ps1';tailfile.write_text(tail)
    series=tmp_path/'series'
    setup=functions(SERIES)+runtime_setup()+f"$seriesRoot='{series}';$Provider='{provider}';$installRoot='{INSTALL}';$basePythonRoot='base';$nativeRoot='native';$codexHelper='protected-codex.ps1';$claudeHelper='reviewed-claude.ps1';$holds=@();$layout=$null;$folder=$null;$ownsRoot=$false;$phase='preflight';$nonce=$null;$failAt='{fail_at}';$script:calls=[Collections.Generic.List[object]]::new();"
    setup+=ordinary_record_setup()+r'''
    function Assert-FreshClaudeLogin{param($Slot)}
    function Invoke-AuthStatusChild{
      param($Slot,$Stage)
      if($Stage -ceq 'after' -and $failAt -ceq 'after'){throw 'inert postcheck failure'}
      [pscustomobject]@{decision=if($Slot -ceq 'slot1' -or $Stage -ceq 'after'){'REUSE_VERIFIED_SESSION'}else{'ATTENDED_LOGIN_REQUIRED'};stage=$Stage;receipt_path='inert';receipt_sha256=('a'*64)}
    }
    function Invoke-SeriesConsoleChild{
      param([string[]]$Arguments)
      $script:calls.Add(@($Arguments));$slot=$Arguments[[Array]::IndexOf($Arguments,'-Slot')+1]
      if($slot -ceq $failAt){return 2};return 0
    }
    $failed=$false
    '''
    setup+=f"try{{. ([scriptblock]::Create([IO.File]::ReadAllText('{tailfile}')))}}catch{{$failed=$true}};[ordered]@{{fixture=$true;failed=$failed;calls=@($script:calls.ToArray())}}|ConvertTo-Json -Depth 8 -Compress"
    r=ps(setup);assert r.returncode==0,r.stdout+r.stderr
    record=json.loads(next(x for x in r.stdout.splitlines() if x.startswith('{"fixture"')))
    assert record['failed']==bool(fail_at)
    assert len(record['calls'])==({'slot3':2,'after':1}.get(fail_at,5))
    for n,args in enumerate(record['calls'],2):
        assert args[args.index('-Slot')+1]==f'slot{n}'
        if provider=='codex':
            assert args[args.index('-File')+1]=='protected-codex.ps1'
            assert args[args.index('-InstallRoot')+1]==str(INSTALL)
            assert args[args.index('-Executable')+1]==r'native\codex.exe'
        else:assert args[args.index('-File')+1]=='reviewed-claude.ps1' and args[-2:]==['-Apply','-Interactive']
    assert (series/'series-start.json').exists()
    assert (series/'series-complete.json').exists()==(not fail_at)
    assert (series/'series-failed.json').exists()==bool(fail_at)
    assert (series/'slot1-REUSED_VERIFIED_SESSION.json').exists()
    assert not (series/'slot1-LOGIN_STARTED.json').exists()
    if not fail_at:
        final=json.loads((series/'series-complete.json').read_bytes())
        assert final['selected_workers_verified']==6 and final['login_commands_executed']==5 and final['existing_sessions_reused']==1
        assert not final['activation_ready'] and final['model_jobs_executed']==0
    else:assert not (series/'slot4-STATUS_BEFORE_STARTED.json').exists()


@pytest.mark.parametrize('failure',['','codex','claude','verification'])
def test_real_combined_apply_tail_orders_both_providers_and_preserves_failure(tmp_path,failure):
    text=OUTER.read_text();start=text.index(' if($holds.Count){throw ($holds -join')
    tail='try{\n'+text[start:text.index('}finally{foreach($stream in $held)',start)]+'}'
    tailfile=tmp_path/'tail.ps1';tailfile.write_text(tail)
    series=tmp_path/'combined'
    code=functions(SERIES)+functions(OUTER)+ordinary_record_setup()+f"$seriesRoot='{series}';$providerSeries='reviewed-six-providers.ps1';$holds=@();$folder=$null;$ownsRoot=$false;$phase='preflight';$nonce=$null;$failure='{failure}';$script:calls=[Collections.Generic.List[object]]::new();"
    code+=r'''
    function Invoke-SeriesConsoleChild{param([string[]]$Arguments)$script:calls.Add(@($Arguments));$provider=$Arguments[[Array]::IndexOf($Arguments,'-Provider')+1];if($provider -ceq $failure){return 2};return 0}
    function Read-CompletedProviderAuth{param($Provider)if($failure -ceq 'verification'){throw 'inert incomplete receipt'};[pscustomobject]@{provider=$Provider;profiles_verified=6;login_commands_executed=2;existing_sessions_reused=4;receipt_path='fixture';receipt_sha256=('a'*64)}}
    $failed=$false
    '''
    code+=f"try{{. ([scriptblock]::Create([IO.File]::ReadAllText('{tailfile}')))}}catch{{$failed=$true}};[ordered]@{{fixture=$true;failed=$failed;calls=@($script:calls.ToArray())}}|ConvertTo-Json -Depth 8 -Compress"
    r=ps(code);assert r.returncode==0,r.stdout+r.stderr
    record=json.loads(next(x for x in r.stdout.splitlines() if x.startswith('{"fixture"')))
    assert record['failed']==bool(failure) and len(record['calls'])==(1 if failure in ('codex','verification') else 2)
    for provider,args in zip(('codex','claude'),record['calls']):assert args==['-NoProfile','-File','reviewed-six-providers.ps1','-Provider',provider,'-Apply','-Interactive']
    assert (series/'series-complete.json').exists()==(not failure)
    assert (series/'series-failed.json').exists()==bool(failure)
    if not failure:
        v=json.loads((series/'series-complete.json').read_bytes());assert v['browser_login_commands_executed']==4 and v['existing_sessions_reused']==8
        assert not v['activation_ready'] and v['agy_integration_hold_preserved']


@pytest.mark.parametrize('change',['','duplicate','hash','stage','count','runtime'])
def test_combined_completion_reads_and_hash_binds_six_retained_status_proofs(tmp_path,change):
    runtime={'install_receipt_sha256':status.INSTALL_RECEIPT_SHA256,'configuration_sha256':status.CONFIG_SHA256,'revision':{'source_sha256':status.REVISION_SHA256}}
    rows=[]
    for n in range(1,7):
        path=tmp_path/f'slot{n}.json';path.write_text(json.dumps({'fixture':n}))
        rows.append({'slot':f'slot{n}','status':'REUSED_VERIFIED_SESSION','proof':{'decision':'REUSE_VERIFIED_SESSION','stage':'before','receipt_path':rf'C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-slot{n}-codex-before\worker-native-status.json','receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}})
    value={'schema':'cochem-six-worker-status-first-result/1','status':'SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED','provider':'codex','nonce':'a'*32,'selected_workers_verified':6,'runtime':runtime,'configuration_applied':False,'model_jobs_executed':0,'activation_ready':False,'series_preserved':True,'login_commands_executed':0,'existing_sessions_reused':6,'status_receipts':rows}
    if change=='duplicate':rows[-1]['slot']='slot1'
    if change=='hash':rows[-1]['proof']['receipt_sha256']='0'*64
    if change=='stage':rows[-1]['proof']['stage']='after'
    if change=='count':value['existing_sessions_reused']=5
    if change=='runtime':runtime['configuration_sha256']='0'*64
    receipt=tmp_path/'complete.json';receipt.write_text(json.dumps(value))
    code=functions(OUTER)+f"$fixture='{tmp_path}';$reads=[Collections.Generic.List[string]]::new();"
    code+=r'''
    function Assert-SeriesPrivateRoot{param($Path)}
    function Read-R3Control{param($Path,$Hash,$Maximum)
      $p=if($Path.EndsWith('series-complete.json')){Join-Path $fixture 'complete.json'}else{$slot=[regex]::Match($Path,'r3-v1-(slot[1-6])-').Groups[1].Value;Join-Path $fixture ($slot+'.json')}
      $h=(Get-FileHash -LiteralPath $p).Hash.ToLowerInvariant();if($Hash -and $h -cne $Hash){throw 'fixture hash mismatch'}
      $reads.Add($p);[pscustomobject]@{Path=$p;Sha256=$h}
    }
    function Read-R3Text{param($Control)[IO.File]::ReadAllText($Control.Path)}
    $ok=$false;try{$v=Read-CompletedProviderAuth codex;$ok=$true}catch{}
    [ordered]@{accepted=$ok;reads=$reads.Count}|ConvertTo-Json -Compress
    '''
    result=success(ps(code));assert result['accepted']==(not change)
    if not change:assert result['reads']==7
