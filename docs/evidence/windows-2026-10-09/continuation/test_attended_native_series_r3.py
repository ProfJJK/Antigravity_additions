"""Actual WinPS5.1 fixture flows; no provider, privileged root, or scheduler calls."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).parent
SCRIPT = W / 'login-six-workers-attended-r3.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
INSTALL = r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'


def ps(code, timeout=30):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")};'''
    return subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', prefix+code], capture_output=True, text=True, timeout=timeout)


def functions(path=SCRIPT):
    return f"$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e);if($e.Count){{throw $e[0]}};foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};"


def success(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('exit_code', [0, 7])
def test_real_console_child_argv_exit_and_stderr_not_pipeline_value(tmp_path, exit_code):
    child = tmp_path / 'child with spaces.ps1'
    output = tmp_path / 'args.json'
    child.write_text("param([string]$Receipt,[string]$Value)\n[IO.File]::WriteAllText($Receipt,($Value|ConvertTo-Json -Compress));[Console]::Error.WriteLine('ordinary-fixture-stderr');exit " + str(exit_code))
    result = ps(functions()+f"$powershell='{PS}';$seriesRoot='{tmp_path}';$r=Invoke-SeriesConsoleChild @('-NoProfile','-NonInteractive','-File','{child}','-Receipt','{output}','-Value','spaced literal $value');[ordered]@{{exit=$r;type=$r.GetType().Name}}|ConvertTo-Json -Compress")
    assert success(result) == {'exit': exit_code, 'type': 'Int32'}
    assert json.loads(output.read_text()) == 'spaced literal $value'
    assert 'ordinary-fixture-stderr' in result.stderr


@pytest.mark.parametrize('invalid', ['quote"value', 'line\nvalue', 'return\rvalue'])
def test_console_child_refuses_nonfixed_argument_before_process(invalid):
    value = invalid.replace('"', '`"').replace('\n', '`n').replace('\r', '`r')
    r = ps(functions()+f'function Start-Process {{throw "process must not run"}};try{{Invoke-SeriesConsoleChild @("{value}")}}catch{{$_.Exception.Message|ConvertTo-Json -Compress}}')
    assert success(r) == 'Invalid fixed child argument.'


@pytest.mark.parametrize('failed_slot', [None, 1, 3, 6])
def test_sequence_stops_at_first_nonzero_and_records_actual_order(failed_slot):
    fail = failed_slot or 0
    r = ps(functions()+f"$script:seen=[Collections.Generic.List[string]]::new();$script:records=[Collections.Generic.List[string]]::new();$failed=$false;try{{Invoke-SixAttendedLogins {{param($s)$script:seen.Add($s);if($s -ceq 'slot{fail}'){{7}}else{{0}}}} {{param($s,$state,$exit)$script:records.Add(\"$s|$state|$exit\")}}}}catch{{$failed=$true}};[ordered]@{{seen=@($script:seen.ToArray());records=@($script:records.ToArray());failed=$failed}}|ConvertTo-Json -Compress")
    v = success(r); count = failed_slot or 6
    assert v['seen'] == [f'slot{n}' for n in range(1, count+1)]
    assert v['failed'] == bool(failed_slot)
    assert v['records'][-1] == f'slot{count}|EXITED|{7 if failed_slot else 0}'


def test_sequence_rejects_missing_process_exit_without_later_login():
    r = ps(functions()+"$script:count=0;try{Invoke-SixAttendedLogins {$script:count++;$null} {param($s,$state,$exit)}}catch{[ordered]@{count=$script:count;message=$_.Exception.Message}|ConvertTo-Json -Compress}")
    assert success(r) == {'count': 1, 'message': 'Login helper did not yield a process exit code.'}


@pytest.mark.parametrize('case', ['ok', 'drift', 'missing', 'duplicate'])
def test_function_import_exact_hash_definitions_and_held_write_refusal(tmp_path, case):
    helper = tmp_path/'source.ps1'; helper.write_text('function Expected { 17 }\n' + ('function Expected { 19 }\n' if case == 'duplicate' else ''))
    pin = hashlib.sha256(helper.read_bytes()).hexdigest() if case != 'drift' else '0'*64
    name = 'Missing' if case == 'missing' else 'Expected'
    r = ps(functions()+f"$held=[Collections.Generic.List[IO.FileStream]]::new();$accepted=$false;$blocked=$false;try{{foreach($d in @(Import-SeriesFunctions '{helper}' '{pin}' @('{name}'))){{. ([scriptblock]::Create($d))}};$accepted=$true;try{{$s=[IO.File]::Open('{helper}',[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$s.Dispose()}}catch{{$blocked=$true}}}}catch{{}}finally{{foreach($s in $held){{$s.Dispose()}}}};[ordered]@{{accepted=$accepted;blocked=$blocked}}|ConvertTo-Json -Compress")
    assert success(r) == {'accepted': case == 'ok', 'blocked': case == 'ok'}


@pytest.mark.parametrize('provider,prior', [('codex', False), ('claude', False), ('claude', True)])
def test_fresh_target_precheck_all_six_and_claude_previous_sessions(provider, prior):
    r = ps(functions()+f"$Provider='{provider}';$seriesRoot='fixture';$script:queries=[Collections.Generic.List[string]]::new();function Test-Path{{param($LiteralPath,$ErrorAction)$false}};function Get-AllFreshTargetHolds{{param($Folder)$script:queries.Add('status-all-six')}};function Get-ChildItem{{param($LiteralPath,[switch]$Directory,$Filter,$ErrorAction)$script:queries.Add($Filter);if($Filter -ceq 'InteractiveClaude427-slot6-*' -and ${str(prior).lower()}){{[pscustomobject]@{{Name='prior'}}}}}};$holds=@(Get-SeriesFreshHolds $null);[ordered]@{{holds=$holds;queries=@($script:queries.ToArray())}}|ConvertTo-Json -Compress")
    v = success(r)
    assert len(v['queries']) == (13 if provider == 'claude' else 1)
    assert len(v['holds']) == int(prior)


def test_existing_series_marker_always_refuses_repeat():
    r = ps(functions()+"$Provider='codex';$seriesRoot='fixture';function Test-Path{param($LiteralPath,$ErrorAction)$true};function Get-AllFreshTargetHolds{param($Folder)};@(Get-SeriesFreshHolds $null)|ConvertTo-Json -Compress")
    assert 'no automatic repeat' in success(r)


def test_worker_root_denial_is_reported_as_inaccessible_not_absent():
    r = ps(functions()+"$slots=[ordered]@{};foreach($n in 1..6){$slots[\"slot$n\"]=[pscustomobject]@{root=\"fixture$n\"}};$layout=[pscustomobject]@{slots=[pscustomobject]$slots};function Assert-NoReparseAncestors{param($Path)throw [UnauthorizedAccessException]::new('fixture')};@(Get-SeriesWorkerRootHolds $layout)|ConvertTo-Json -Compress")
    v = success(r); assert len(v) == 6 and all('inaccessible' in x and 'Administrator' in x for x in v)


def test_worker_root_missing_is_not_reclassified_as_denied():
    r = ps(functions()+"$layout=[pscustomobject]@{slots=[pscustomobject]@{slot1=[pscustomobject]@{root='fixture'}}};function Assert-NoReparseAncestors{param($Path)throw [IO.FileNotFoundException]::new('fixture missing')};try{Get-SeriesWorkerRootHolds $layout}catch{$_.Exception.GetType().Name|ConvertTo-Json -Compress}")
    assert success(r) == 'FileNotFoundException'


@pytest.mark.parametrize('state,instances,enabled,accepted', [(3,0,False,True),(1,0,False,True),(4,0,False,False),(3,1,False,False),(0,0,False,False),(3,0,True,False)])
def test_stopped_daemon_state_requires_terminal_disabled_no_instances(state, instances, enabled, accepted):
    r = ps(functions()+f"function Get-ExactTaskOrAbsent{{param($Folder,$Name)$t=[pscustomobject]@{{Enabled=${str(enabled).lower()};State={state}}};$t|Add-Member ScriptMethod GetInstances {{param($f)[pscustomobject]@{{Count={instances}}}}};$t}};$ok=$false;try{{Assert-SeriesDaemonsStopped $null;$ok=$true}}catch{{}};$ok|ConvertTo-Json -Compress")
    assert success(r) is accepted


def test_apply_denied_before_any_body_without_attended_admin_token():
    result = subprocess.run([PS,'-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Provider','codex','-Apply'],capture_output=True,text=True,timeout=15)
    assert result.returncode != 0 and 'owner at an elevated interactive ConsoleHost' in result.stderr


@pytest.mark.parametrize('provider,fail_at', [('codex',None),('claude',None),('codex','slot3'),('claude','status')])
def test_actual_apply_tail_journals_and_calls_exact_helpers_with_simulated_auth(tmp_path, provider, fail_at):
    # Executes the exact action branch, including exclusive records; replaces
    # admin-only custody, private-root creation, scheduler and native effects.
    # Ordinary fixture ACL explicitly substitutes this test user's identity.
    text = SCRIPT.read_text(); start = text.index(' if($holds.Count){throw ($holds -join')
    tail = text[start:text.index('}catch{\n if($ownsRoot)',start)]
    tail_path = tmp_path/'tail.ps1'; tail_path.write_text(tail)
    fixture_root = tmp_path/'series'
    setup = functions()+f"$fixture='{tmp_path}';$seriesRoot='{fixture_root}';$Provider='{provider}';$installRoot='{INSTALL}';$basePythonRoot='base';$nativeRoot='native';$codexHelper='protected-codex.ps1';$claudeHelper='fresh-claude.ps1';$statusBatch='reviewed-status.ps1';$holds=@();$layout=$null;$folder=$null;$ownsRoot=$false;$phase='preflight';$runtime=[pscustomobject]@{{installation='fixture'}};$script:calls=[Collections.Generic.List[object]]::new();$failAt='{fail_at or ''}';"
    setup += r'''
    function Assert-CodeTreeOnce{param($Path)1}
    function Assert-SeriesDaemonsStopped{param($Folder)}
    function Get-SeriesFreshHolds{param($Folder)}
    function Get-SeriesWorkerRootHolds{param($Layout)}
    function Assert-ProtectedPath{param($Path)}
    function Assert-SeriesPrivateRoot{param($Path)if(-not [IO.Directory]::Exists($Path)){throw 'fixture absent'}}
    function New-PrivateAcl{
      $a=[Security.AccessControl.FileSecurity]::new();$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false);$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));$a
    }
    function Initialize-SeriesDirectory{
      Add-Type -TypeDefinition 'using System.IO;public static class CoChemNativeLoginSeriesDirectory { public static void Create(string p) { if(Directory.Exists(p))throw new IOException("exists");Directory.CreateDirectory(p); } }'
    }
    function Invoke-SeriesConsoleChild{param([string[]]$Arguments)
      $script:calls.Add(@($Arguments));$i=[Array]::IndexOf($Arguments,'-Slot');$slot=if($i -ge 0){$Arguments[$i+1]}else{'status'}
      if($slot -ceq $failAt){return 2};return 0
    }
    function Read-SeriesStatusReceipts{foreach($n in 1..6){[pscustomobject]@{slot="slot$n";status='NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED'}}}
    $failed=$false
    '''
    setup += f"try{{. ([scriptblock]::Create([IO.File]::ReadAllText('{tail_path}')))}}catch{{$failed=$true}};[ordered]@{{fixture=$true;failed=$failed;calls=@($script:calls.ToArray())}}|ConvertTo-Json -Depth 8 -Compress"
    result = ps(setup)
    assert result.returncode == 0, result.stdout+result.stderr
    record = json.loads(next(line for line in result.stdout.splitlines() if line.startswith('{"fixture"')))
    calls = record['calls']; assert len(calls) == (3 if fail_at == 'slot3' else 7)
    assert record['failed'] is bool(fail_at)
    for n,args in enumerate(calls[:6],1):
        if '-Slot' not in args: break
        assert args[args.index('-Slot')+1] == f'slot{n}'
        if provider == 'codex':
            assert args[args.index('-File')+1] == 'protected-codex.ps1'
            assert args[args.index('-InstallRoot')+1] == INSTALL
            assert args[args.index('-Executable')+1] == r'native\codex.exe'
        else: assert args[-2:] == ['-Apply','-Interactive']
    assert (fixture_root/'series-start.json').exists()
    assert (fixture_root/'series-complete.json').exists() is (fail_at is None)
    starting = json.loads((fixture_root/'slot1-STARTING.json').read_bytes())
    assert starting['authentication_verified'] is False and starting['raw_provider_output_recorded_by_series'] is False
    if fail_at is None:
        final = json.loads((fixture_root/'series-complete.json').read_bytes())
        assert final['slots_verified'] == 6 and final['activation_ready'] is False
    assert not (fixture_root/'slot4-STARTING.json').exists() if fail_at == 'slot3' else True


def test_record_create_new_preserves_existing_metadata(tmp_path):
    p = tmp_path/'existing.json';p.write_bytes(b'original')
    r = ps(functions()+f"function Assert-SeriesPrivateRoot{{param($Path)}};function New-PrivateAcl{{$a=[Security.AccessControl.FileSecurity]::new();$s=[Security.Principal.WindowsIdentity]::GetCurrent().User;$a.SetOwner($s);$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($s,'FullControl','Allow'));$a}};try{{Write-SeriesRecord '{p}' @{{new='value'}}}}catch{{$_.Exception.GetType().Name|ConvertTo-Json -Compress}}")
    success(r); assert p.read_bytes() == b'original'


@pytest.mark.parametrize('change', [None,'inheritance','extra_grant','inherit_only'])
def test_actual_directory_acl_requires_exact_private_effective_inheritable_grants(tmp_path, change):
    # Test-owner substitution is explicit: ordinary user cannot set Admin owner.
    # The production function is otherwise unchanged and reads a real NTFS DACL.
    code = functions()+f"$fixture='{tmp_path}';"
    code += r'''
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $a=[Security.AccessControl.DirectorySecurity]::new();$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false)
    $inherit='ContainerInherit,ObjectInherit';$prop='None'
    '''
    if change == 'inheritance': code += "$inherit='None';"
    if change == 'inherit_only': code += "$prop='InheritOnly';"
    code += r'''
    foreach($s in @('S-1-5-18',$sid.Value)){$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($s),'FullControl',$inherit,$prop,'Allow'))}
    '''
    if change == 'extra_grant': code += "$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'));"
    code += r'''
    $originalAcl=[IO.Directory]::GetAccessControl($fixture);[IO.Directory]::SetAccessControl($fixture,$a)
    function Assert-NoReparseAncestors{param($Path)}
    $f=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Assert-SeriesPrivateRoot'},$true)[0]
    . ([scriptblock]::Create($f.Extent.Text.Replace('S-1-5-32-544',$sid.Value)))
    $accepted=$false;try{Assert-SeriesPrivateRoot $fixture;$accepted=$true}catch{}finally{[IO.Directory]::SetAccessControl($fixture,$originalAcl)};$accepted|ConvertTo-Json -Compress
    '''
    assert success(ps(code)) is (change is None)


@pytest.mark.parametrize('change', [None,'provider','cleanup','resource','config','status'])
def test_final_status_receipts_require_all_six_bound_auth_success(change):
    mutate = {None:'','provider':"$v.provider='claude'",'cleanup':"$v.cleanup_verified=$false",'resource':"$v.resource_limits_sha256='old'",'config':"$v.config_sha256='old'",'status':"$v.status='LOGIN_EXITED_ZERO'"}[change]
    code = functions()+f"$Provider='codex';$installRoot='{INSTALL}';$runtime=[pscustomobject]@{{install_receipt_sha256='receipt';source_manifest_sha256='manifest';configuration_sha256='config';revision=[pscustomobject]@{{source_sha256='revision'}}}};"
    code += r'''
    function Read-R3Control{param($Path,$Hash,$Maximum)[pscustomobject]@{Path=$Path;Sha256=('a'*64)}}
    function Read-R3Text{param($Control)
      $slot=[regex]::Match($Control.Path,'r3-(slot[1-6])-').Groups[1].Value
      $v=[ordered]@{schema='cochem-worker-native-status/1';slot=$slot;provider='codex';status='NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED';cleanup_verified=$true;system_sid='S-1-5-18';helper_sha256='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5';runtime_root=$installRoot;install_receipt_sha256='receipt';source_manifest_sha256='manifest';resource_limits_sha256='de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f';config_sha256='config';revision=[ordered]@{verified=$true;source_sha256='revision'}}
    '''
    code += f"if($slot -ceq 'slot6'){{{mutate}}};$v|ConvertTo-Json -Depth 5}};$ok=$false;try{{$r=@(Read-SeriesStatusReceipts);$ok=$r.Count -eq 6}}catch{{}};$ok|ConvertTo-Json -Compress"
    assert success(ps(code)) is (change is None)
