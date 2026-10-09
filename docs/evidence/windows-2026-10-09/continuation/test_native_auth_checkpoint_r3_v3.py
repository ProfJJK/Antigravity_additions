"""Fresh-attempt and attended-readiness tests; no live provider or Scheduler actions."""
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
PY=W/'worker-native-auth-status-six-r3-v3.py'
LEAF=W/'check-worker-native-auth-status-six-r3-v3.ps1'
SERIES=W/'login-six-workers-status-first-r3-v3.ps1'
OUTER=W/'authenticate-native-profiles-status-first-r3-v3.ps1'
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
ATTEMPT='a'*32

@pytest.mark.parametrize('attempt', ['0'*32, 'A'*32, '../escape', '', None])
def test_python_invalid_attempt_refuses_before_any_runtime_access(attempt):
    with pytest.raises(ValueError, match='attempt'):
        status.run('slot1','codex','b'*32,'before',attempt)

def test_native_safety_and_status_parsers_are_unchanged():
    old=ast.parse((W/'worker-native-auth-status-six-r3-v2.py').read_text())
    new=ast.parse(PY.read_text())
    before={n.name:ast.dump(n,include_attributes=False) for n in old.body if isinstance(n,ast.FunctionDef)}
    after={n.name:ast.dump(n,include_attributes=False) for n in new.body if isinstance(n,ast.FunctionDef)}
    for name,body in before.items():
        if name not in ('run','main'):assert after[name]==body,name

@pytest.mark.parametrize('answer,ready', [('READY',True),('PAUSE',False),('ready',False)])
def test_actual_readiness_is_deliberate_and_never_starts_a_login(answer,ready):
    code=functions(SERIES)+f"function Read-Host{{param($Prompt)'{answer}'}};$v=Confirm-AttendedLoginReady slot5 codex 6>$null;$v|ConvertTo-Json"
    assert success(ps(code)) is ready

@pytest.mark.parametrize('mode', ['all_valid','ready','pause','login_failure','unreadable'])
def test_readiness_occurs_only_after_current_logout_and_pause_starts_no_timer(mode):
    code=functions(SERIES)+f"$mode='{mode}';$script:events=[Collections.Generic.List[string]]::new();$errorSeen=$false;$complete=$false;"
    code+=r'''
    try{$complete=Invoke-SixStatusFirst {
      param($slot,$stage);$script:events.Add("STATUS:$slot/$stage")
      if($slot -ceq 'slot5' -and $mode -ceq 'unreadable'){throw 'inert status error'}
      [pscustomobject]@{decision=if($slot -ceq 'slot5' -and $stage -ceq 'before' -and $mode -cne 'all_valid'){'ATTENDED_LOGIN_REQUIRED'}else{'REUSE_VERIFIED_SESSION'}}
    } {
      param($slot);$script:events.Add("LOGIN:$slot");if($mode -ceq 'login_failure'){1}else{0}
    } {
      param($slot,$state,$proof);$script:events.Add("RECORD:$slot/$state")
    } {
      param($slot);$script:events.Add("READY:$slot");$mode -cne 'pause'
    }}catch{$errorSeen=$true}
    [ordered]@{complete=$complete;error=$errorSeen;events=@($script:events.ToArray())}|ConvertTo-Json -Compress
    '''
    value=success(ps(code));events=value['events']
    assert value['error']==(mode in ('login_failure','unreadable'))
    assert value['complete']==(mode in ('all_valid','ready'))
    if mode in ('all_valid','unreadable'):
        assert not any(x.startswith(('READY:','LOGIN:')) for x in events)
    elif mode=='pause':
        assert 'RECORD:slot5/PAUSED_BEFORE_LOGIN' in events
        assert not any(x.startswith('LOGIN:') or x.startswith('STATUS:slot6') for x in events)
        assert 'RECORD:slot5/LOGIN_STARTED' not in events
    else:
        assert events.index('STATUS:slot5/before')<events.index('READY:slot5')<events.index('RECORD:slot5/LOGIN_STARTED')<events.index('LOGIN:slot5')

@pytest.mark.parametrize('which',['provider','both'])
def test_actual_pause_tail_exits20_preserves_journals_and_skips_following_phase(tmp_path,which):
    path=SERIES if which=='provider' else OUTER
    source=path.read_text();start=source.index(' if($holds.Count){throw ($holds -join')
    tail='try{\n'+source[start:source.index('}finally{foreach($stream in $held)',start)]+'}'
    tailpath=tmp_path/'tail.ps1';tailpath.write_text(tail)
    root=tmp_path/'attempt';calls=tmp_path/'native-calls.txt'
    code=functions(SERIES)+functions(OUTER)+runtime_setup()+ordinary_record_setup()+f"$seriesRoot='{root}';$calls='{calls}';$holds=@();$folder=$null;$layout=$null;$ownsRoot=$false;$nonce=$null;$phase='preflight';$Provider='codex';$providerSeries='inert-provider';$installRoot='{INSTALL}';$basePythonRoot='inert-base';$nativeRoot='inert-native';$codexHelper='inert-codex';$claudeHelper='inert-claude';"
    code+=r'''
    function Confirm-AttendedLoginReady{param($Slot,$Provider)$false}
    function Invoke-AuthStatusChild{param($Slot,$Stage)[pscustomobject]@{decision='ATTENDED_LOGIN_REQUIRED';stage=$Stage;receipt_path='inert';receipt_sha256=('b'*64)}}
    function Invoke-SeriesConsoleChild{param($Arguments)[IO.File]::AppendAllText($calls,'CALL');20}
    function Read-CompletedProviderAuth{throw 'A paused child must not be accepted as completion'}
    function Read-PausedProviderAuth{param($Provider)[pscustomobject]@{attempt=$Attempt;provider=$Provider;status='AUTHENTICATION_PAUSED_BEFORE_LOGIN';paused_slot='slot1';receipt_path='inert';receipt_sha256=('b'*64);exit_code=20}}
    '''
    result=ps(code+f". ([scriptblock]::Create([IO.File]::ReadAllText('{tailpath}')))")
    assert result.returncode==20,result.stdout+result.stderr
    paused=json.loads((root/'series-paused.json').read_bytes())
    assert paused['attempt']==ATTEMPT and paused['status']=='AUTHENTICATION_PAUSED_BEFORE_LOGIN'
    assert paused['exit_code']==20 and not paused['pipeline_started']
    assert not (root/'series-failed.json').exists() and not (root/'series-complete.json').exists()
    if which=='provider':
        assert not calls.exists() and not (root/'slot1-LOGIN_STARTED.json').exists()
        assert (root/'slot1-PAUSED_BEFORE_LOGIN.json').exists()
    else:
        assert calls.read_text()=='CALL' and not (root/'claude-STARTED.json').exists()
        assert paused['paused_provider']=='codex'

@pytest.mark.parametrize('change',['','attempt','provider','cleanup','hash','exit_type'])
def test_pause_reread_requires_bound_provider_and_fresh_status(tmp_path,change):
    root=rf'C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v3-{ATTEMPT}-codex'
    leafpath=rf'C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-{ATTEMPT}-slot5-codex-before\worker-native-status.json'
    leaf=receipt_fixture(kind='missing');leaf.update(slot='slot5')
    if change=='cleanup':leaf['cleanup_verified']=False
    raw=json.dumps(leaf).encode();(tmp_path/'leaf.json').write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
    value={'schema':'cochem-six-worker-status-first-paused/1','status':'AUTHENTICATION_PAUSED_BEFORE_LOGIN','attempt':ATTEMPT,'provider':'codex','nonce':'b'*32,'exit_code':20,'paused_slot':'slot5','runtime':{'install_receipt_sha256':status.INSTALL_RECEIPT_SHA256,'configuration_sha256':status.CONFIG_SHA256,'revision':{'source_sha256':status.REVISION_SHA256}},'configuration_applied':False,'pipeline_started':False,'activation_ready':False,'automatic_retry_allowed':False,'series_preserved':True,'model_jobs_executed':0,'login_commands_executed':0,'existing_sessions_reused':4,'before_status_proof':{'stage':'before','decision':'ATTENDED_LOGIN_REQUIRED','receipt_path':leafpath,'receipt_sha256':digest}}
    if change=='attempt':value['attempt']='c'*32
    if change=='provider':value['provider']='claude'
    if change=='hash':value['before_status_proof']['receipt_sha256']='f'*64
    if change=='exit_type':value['exit_code']='20'
    (tmp_path/'pause.json').write_text(json.dumps(value))
    code=functions(OUTER)+f"$Attempt='{ATTEMPT}';$fixture='{tmp_path}';$pausePath='{root}\\series-paused.json';$leafPath='{leafpath}';function Assert-SeriesPrivateRoot{{param($Path)}};"
    code+=r'''
    function Read-R3Control{
      param($Path,$Hash,$Maximum)
      if($Path -ceq $pausePath){$file=Join-Path $fixture 'pause.json'}elseif($Path -ceq $leafPath){$file=Join-Path $fixture 'leaf.json'}else{throw 'unexpected path'}
      $actual=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant();if($Hash -and $Hash -cne $actual){throw 'digest mismatch'}
      [pscustomobject]@{text=[IO.File]::ReadAllText($file);Sha256=$actual}
    }
    function Read-R3Text{param($Control)$Control.text}
    try{$v=Read-PausedProviderAuth codex;[ordered]@{accepted=$true;proof=$v}|ConvertTo-Json -Depth 5}catch{[ordered]@{accepted=$false}|ConvertTo-Json}
    '''
    value=success(ps(code));assert value['accepted']==(not change)

def test_transitive_pins_and_attempt_command_binding():
    py=hashlib.sha256(PY.read_bytes()).hexdigest();leaf=hashlib.sha256(LEAF.read_bytes()).hexdigest();provider=hashlib.sha256(SERIES.read_bytes()).hexdigest()
    assert py in LEAF.read_text() and py in SERIES.read_text() and py in OUTER.read_text()
    assert leaf in SERIES.read_text() and leaf in OUTER.read_text() and provider in OUTER.read_text()
    assert "+' --attempt '+$Attempt" in LEAF.read_text() and "+' --attempt '+$Attempt" in SERIES.read_text()
    assert "if($v.attempt -cne $Attempt" in OUTER.read_text()
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


@pytest.mark.parametrize('provider,kind,stage',[('codex','reuse','before'),('codex','missing','before')])
def test_full_python_run_keeps_real_parser_receipt_and_one_command(tmp_path,monkeypatch,provider,kind,stage):
    """Actual helper run, real bounded temporary output and receipt writes; native custody/launch simulated."""
    from cochem_pipeline import windows as win
    from cochem_pipeline import ramdisk
    m=module(PY);base=tmp_path/'protected-fixture';base.mkdir()
    root=base/f'NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-{ATTEMPT}-slot1-{provider}-{stage}';root.mkdir()
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
    code=m.run('slot1',provider,'a'*32,stage,ATTEMPT)
    report=json.loads((root/'worker-native-status.json').read_bytes())
    decision=status.authentication_decision(auth(provider,kind))
    assert report['decision']==decision and code==(2 if decision=='HOLD' else 0)
    assert report['stage']==stage and report['cleanup_verified'] and report['daemon_states_verified_before_and_after']
    assert calls==[[str(m.NATIVE/(provider+'.exe')),*m.CONTRACTS[provider]['arguments']]]
    assert report['native_model_jobs_executed']==report['login_commands_executed']==0
    assert 'private-unknown' not in (root/'worker-native-status.json').read_text()


def receipt_fixture(provider='codex',kind='reuse',stage='before'):
    a=auth(provider,kind);decision=status.authentication_decision(a)
    return {'schema':'cochem-worker-native-auth-status/1','attempt':ATTEMPT,'nonce':'a'*32,'stage':stage,'slot':'slot1','provider':provider,
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
    return "$Attempt='"+ATTEMPT+"';"+f"$runtime=[pscustomobject]@{{install_receipt_sha256='{status.INSTALL_RECEIPT_SHA256}';source_manifest_sha256='{status.MANIFEST_SHA256}';configuration_sha256='{status.CONFIG_SHA256}';revision=[pscustomobject]@{{verified=$true;source_sha256='{status.REVISION_SHA256}'}}}};"


@pytest.mark.parametrize('kind,stage',[('reuse','before'),('missing','before')])
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
      if($Flags -ne 2 -or $User -cne 'SYSTEM' -or $Logon -ne 5 -or $null -ne $Password -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT3M' -or -not $script:action.Arguments.EndsWith(' --stage '+$Stage+' --attempt '+$Attempt)){throw 'Simulated action is not the exact reviewed status stage'}
      if((Get-FileHash -LiteralPath (Join-Path $targetRoot 'worker-native-auth-status-six-r3-v3.py')).Hash.ToLowerInvariant() -cne $sourceHash){throw 'Copied helper differs'}
      $script:registered++;$task
    }
    try{. ([scriptblock]::Create([IO.File]::ReadAllText($tail)));if($script:ran -ne 1 -or $script:registered -ne 1){throw 'Expected one simulated task'}}finally{foreach($s in $held){$s.Dispose()}}
    """
    result=success(ps(code));assert result['decision']==status.authentication_decision(auth('codex',kind))
    assert result['stage']==stage and result['last_task_result']==0
    assert (tmp_path/'status'/PY.name).read_bytes()==PY.read_bytes()


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


@pytest.mark.parametrize('provider,fail_at',[('codex',''),('codex','slot3')])
def test_real_provider_apply_tail_journals_exact_attended_login_arguments(tmp_path,provider,fail_at):
    text=SERIES.read_text();start=text.index(' if($holds.Count){throw ($holds -join')
    tail=text[start:text.index('}finally{foreach($stream in $held)',start)]
    # Retain the real catch and exclusive failure journal as well as the action branch.
    tail='try{\n'+tail+'}'
    tailfile=tmp_path/'tail.ps1';tailfile.write_text(tail)
    series=tmp_path/'series'
    setup=functions(SERIES)+runtime_setup()+f"$seriesRoot='{series}';$Provider='{provider}';$installRoot='{INSTALL}';$basePythonRoot='base';$nativeRoot='native';$codexHelper='protected-codex.ps1';$claudeHelper='reviewed-claude.ps1';$holds=@();$layout=$null;$folder=$null;$ownsRoot=$false;$phase='preflight';$nonce=$null;$failAt='{fail_at}';$script:calls=[Collections.Generic.List[object]]::new();"
    setup+=ordinary_record_setup()+r'''
    function Confirm-AttendedLoginReady{param($Slot,$Provider)return $true}
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

