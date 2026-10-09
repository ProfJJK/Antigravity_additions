"""Ordinary PS5.1 fixtures; no provider, protected session, task mutation or Apply."""
import base64
import copy
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
HELPER = W / 'claude-session-history-v5.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
NONCE = 'a' * 32
SID = 'S-1-5-21-111-222-333-1001'
ROOT = rf'C:\Program Files\CoChem\InteractiveClaude427-r3-slot1-{NONCE}'
INSTALL = r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
INSTALL_PIN = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
SOURCE_PIN = '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
CONFIG_PIN = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
REVISION_PIN = '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
LAYOUT_PIN = '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
BRIDGE_PIN = 'b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'
SUPPORT_PIN = 'c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'
RESOURCE_PIN = 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f'


def ps(code):
    prefix = rf"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){{Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}}
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{HELPER}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw 'Helper parse failed'}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    encoded = base64.b64encode((prefix + code).encode('utf-16-le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def literal(value):
    data = base64.b64encode(json.dumps(value).encode()).decode()
    return f"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}'))|ConvertFrom-Json)"


def revision():
    return dict(schema='cochem-installed-revision/1', verified=True, read_only=True, files=109,
                source_sha256=REVISION_PIN, acceptance_files=0, acceptance_sha256=None)


def runtime():
    return dict(install_receipt_sha256=INSTALL_PIN, source_manifest_sha256=SOURCE_PIN,
                configuration_sha256=CONFIG_PIN, revision=revision())


def receipt(kind='success'):
    value = dict(schema='cochem-interactive-claude-login/1', slot='slot1', nonce=NONCE,
                 system_sid='S-1-5-18', helper_sha256=BRIDGE_PIN,
                 status='LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED', cleanup_verified=True,
                 one_line_submitted=False, input_file_deleted=False, operator_cancelled=False,
                 login_commands_executed=1, model_jobs_executed=0, authentication_verified=False,
                 activation_ready=False, secret_published=False, native_output_after_input_published=False,
                 started_at_unix_ms=1000, finished_at_unix_ms=2000, revision=revision(), runtime_root=INSTALL,
                 install_receipt_sha256=INSTALL_PIN, source_manifest_sha256=SOURCE_PIN,
                 resource_limits_sha256=RESOURCE_PIN, layout_sha256=LAYOUT_PIN, config_sha256=CONFIG_PIN,
                 process=dict(pid=1234, creation_time_filetime=134000000000000000, token_sid=SID,
                              token_matches_selected_worker=True, image_matches_reviewed_executable=True,
                              owned_job_membership_verified=True, profile_directory_sha256='b'*64,
                              source='owned_windows_process_handle_and_child_token'), native_exit_code=0)
    if kind == 'native_failure':
        value.update(status='LOGIN_COMMAND_FAILED', native_exit_code=1)
    if kind == 'pre_native':
        value.update(status='LOGIN_FAILED_OR_CANCELLED', cleanup_verified=False, login_commands_executed=0,
                     failure=dict(phase='native_custody', error_type='ValueError', winerror=None))
        del value['process'], value['native_exit_code']
    return value


def task_code(last=0):
    return rf"""
    $script:instances=0;
    $action=[pscustomobject]@{{Type=0;Path='{INSTALL}\.venv\Scripts\python.exe';Arguments='-I -B "{ROOT}\worker_claude_login_bridge_r3.py" --slot slot1 --nonce {NONCE}';WorkingDirectory='{ROOT}'}};
    $actions=[pscustomobject]@{{Count=1;Value=$action}};$actions|Add-Member ScriptMethod Item {{param($i)return $this.Value}};
    $definition=[pscustomobject]@{{Principal=[pscustomobject]@{{UserId='SYSTEM';LogonType=5;RunLevel=1}};Triggers=[pscustomobject]@{{Count=0}};Actions=$actions;Settings=[pscustomobject]@{{MultipleInstances=2;ExecutionTimeLimit='PT15M';RestartCount=0}}}};
    $task=[pscustomobject]@{{Name='CoChem-4.2.7-InteractiveClaude-r3-slot1-{NONCE}';State=3;LastTaskResult={last};Definition=$definition}};
    $task|Add-Member ScriptMethod GetInstances {{param($flags)[pscustomobject]@{{Count=$script:instances}}}};
    """


@pytest.mark.parametrize('kind,last,classification', [
    ('success', 0, 'TERMINAL_NATIVE_LOGIN_CLEANUP_VERIFIED'),
    ('native_failure', 2, 'TERMINAL_NATIVE_LOGIN_CLEANUP_VERIFIED'),
    ('pre_native', 2, 'TERMINAL_PRE_NATIVE_CUSTODY_FAILURE_NO_CHILD_LAUNCHED'),
])
def test_valid_receipts_are_terminal_history_and_never_auth_authority(kind, last, classification):
    code=task_code(last)+f"$receipt={literal(receipt(kind))};$runtime={literal(runtime())};"
    code+=f"Assert-ClaudeHistoryTask $task '{ROOT}' slot1 '{NONCE}';Assert-ClaudeHistoryReceipt $receipt $task slot1 '{NONCE}' $runtime '{SID}'|ConvertTo-Json -Compress"
    value=json.loads(ps(code))
    assert value['classification']==classification and value['authentication_authorized_by_history'] is False


def test_current_runtime_ordered_dictionary_is_supported_without_case_coercion():
    code=task_code()+f"$receipt={literal(receipt())};$decoded={literal(runtime())};"
    code+=f"$runtime=[ordered]@{{install_receipt_sha256=$decoded.install_receipt_sha256;source_manifest_sha256=$decoded.source_manifest_sha256;configuration_sha256=$decoded.configuration_sha256;revision=$decoded.revision}};Assert-ClaudeHistoryReceipt $receipt $task slot1 '{NONCE}' $runtime '{SID}'|ConvertTo-Json -Compress"
    assert json.loads(ps(code))['authentication_authorized_by_history'] is False


def mutate(value, key, changed):
    parts=key.split('.')
    for part in parts[:-1]:value=value[part]
    if changed=='__delete__':del value[parts[-1]]
    else:value[parts[-1]]=changed


@pytest.mark.parametrize('key,changed', [
    ('schema','wrong'),('slot','slot2'),('nonce','A'*32),('system_sid','S-1-5-32-544'),('helper_sha256','0'*64),
    ('runtime_root',INSTALL.replace('-r3','-r2')),('install_receipt_sha256','0'*64),('source_manifest_sha256','0'*64),
    ('resource_limits_sha256','0'*64),('layout_sha256','0'*64),('config_sha256','0'*64),
    ('revision.schema','wrong'),('revision.source_sha256','0'*64),('revision.verified',1),('revision.read_only',False),
    ('revision.files',True),('revision.files',109.0),('revision.acceptance_files',False),('revision.acceptance_sha256','a'*64),
    ('cleanup_verified',False),('cleanup_verified',1),('one_line_submitted',1),('input_file_deleted',1),('operator_cancelled',1),
    ('authentication_verified',True),('activation_ready',True),('secret_published',True),('native_output_after_input_published',True),
    ('login_commands_executed',True),('login_commands_executed',1.0),('login_commands_executed',0),('model_jobs_executed',False),
    ('model_jobs_executed',1),('started_at_unix_ms',True),('started_at_unix_ms',0),('finished_at_unix_ms',999),
    ('native_exit_code',True),('native_exit_code',0.0),('native_exit_code',1),('native_exit_code',4294967296),
    ('process','__delete__'),('process.pid',True),('process.pid',0),('process.creation_time_filetime',False),
    ('process.token_sid','S-1-5-21-111-222-333-1002'),('process.source','pid_lookup'),
    ('process.profile_directory_sha256','B'*64),('process.token_matches_selected_worker',1),
    ('process.image_matches_reviewed_executable',False),('process.owned_job_membership_verified',False),
    ('native_exit_code','__delete__'),('finished_at_unix_ms','__delete__'),('unexpected_raw_message','synthetic-private-value'),
    ('operator_cancelled',True),
])
def test_inconsistent_or_unproven_receipts_hold_without_echoing_raw_fields(key, changed):
    value=receipt();mutate(value,key,changed)
    code=task_code()+f"$receipt={literal(value)};$runtime={literal(runtime())};"
    code+=f"$refused=$false;$safe=$null;try{{$null=Assert-ClaudeHistoryReceipt $receipt $task slot1 '{NONCE}' $runtime '{SID}'}}catch{{$refused=$true;$safe=$_.Exception.Data['CoChemClaudeHistorySafeMetadata']}};[ordered]@{{refused=$refused;safe=$safe}}|ConvertTo-Json -Depth 5 -Compress"
    text=ps(code);result=json.loads(text)
    assert result['refused'] is True and 'synthetic-private-value' not in text


@pytest.mark.parametrize('phase', ['runtime_custody','layout','native_launch','native_attestation','native_wait','native_cleanup','trusted_preflight'])
def test_pre_native_exception_never_admits_launch_or_incomplete_binding_phases(phase):
    value=receipt('pre_native');value['failure']['phase']=phase
    code=task_code(2)+f"$receipt={literal(value)};$runtime={literal(runtime())};"
    code+=f"$ok=$true;try{{$null=Assert-ClaudeHistoryReceipt $receipt $task slot1 '{NONCE}' $runtime '{SID}'}}catch{{$ok=$false}};$ok|ConvertTo-Json"
    assert json.loads(ps(code)) is False


@pytest.mark.parametrize('key,changed', [
    ('process',dict(pid=1)),('native_exit_code',0),('one_line_submitted',True),('input_file_deleted',True),('operator_cancelled',True),
    ('failure.error_type','secret@email.invalid'),('failure.winerror',True),('failure.winerror',-1),('config_sha256','__delete__'),
])
def test_pre_native_exception_requires_no_child_and_complete_bindings(key, changed):
    value=receipt('pre_native');mutate(value,key,changed)
    code=task_code(2)+f"$receipt={literal(value)};$runtime={literal(runtime())};"
    code+=f"$ok=$true;try{{$null=Assert-ClaudeHistoryReceipt $receipt $task slot1 '{NONCE}' $runtime '{SID}'}}catch{{$ok=$false}};$ok|ConvertTo-Json"
    assert json.loads(ps(code)) is False


@pytest.mark.parametrize('change', [
    "$task=$null", "$task.Name+='-wrong'", "$task.State=0", "$task.State=2", "$task.State=4", "$script:instances=1",
    "$definition.Principal.UserId='owner'", "$definition.Principal.LogonType=3", "$definition.Principal.RunLevel=0",
    "$definition.Triggers.Count=1", "$actions.Count=2", "$action.Type=5", "$action.Path='C:\\different.exe'",
    "$action.Arguments+=' --extra'", "$action.WorkingDirectory='C:\\different'",
    "$definition.Settings.MultipleInstances=0", "$definition.Settings.ExecutionTimeLimit='PT30M'", "$definition.Settings.RestartCount=1",
    "$task.LastTaskResult=$true", "$task.LastTaskResult=-1",
])
def test_exact_terminal_system_task_and_no_scheduled_restart_required(change):
    code=task_code()+change+f";$ok=$true;try{{Assert-ClaudeHistoryTask $task '{ROOT}' slot1 '{NONCE}'}}catch{{$ok=$false}};$ok|ConvertTo-Json"
    assert json.loads(ps(code)) is False


@pytest.mark.parametrize('state', [1,3])
def test_only_conclusive_terminal_task_states_are_accepted(state):
    code=task_code()+f"$task.State={state};Assert-ClaudeHistoryTask $task '{ROOT}' slot1 '{NONCE}';'true'"
    assert json.loads(ps(code)) is True


def test_held_safe_projection_is_allowlisted_and_never_contains_raw_messages():
    value=dict(status='untrusted-secret-status', login_commands_executed=True, model_jobs_executed=0,
               failure=dict(phase='native_launch', error_type='secret@email.invalid', winerror=True, message='synthetic-private-value'),
               unexpected_raw_message='another-private-value')
    result=json.loads(ps(f"Get-ClaudeHistorySafeReceiptMetadata {literal(value)}|ConvertTo-Json -Depth 5 -Compress"))
    assert result==dict(model_jobs_executed=0,failure=dict(phase='native_launch'))


def acl_code():
    return r"""
    $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetAccessRuleProtection($true,$false);
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'));
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))};
    """


@pytest.mark.parametrize('allow_inherited,protected,ok', [(False,True,True),(True,True,True),(True,False,True),(False,False,False)])
def test_receipt_can_inherit_private_file_acl_but_copied_sources_are_protected(allow_inherited, protected, ok):
    code=acl_code()+f"$acl.SetAccessRuleProtection(${str(protected).lower()},$true);$ok=$true;try{{Assert-ClaudeHistoryPrivateAcl $acl"+(' -AllowInherited' if allow_inherited else '')+"}catch{$ok=$false};$ok|ConvertTo-Json"
    assert json.loads(ps(code)) is ok


@pytest.mark.parametrize('change', [
    "$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'))",
    "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'Read','Allow'))",
    "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-18'),'Read','Deny'))",
    "$acl.RemoveAccessRuleAll([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-18'),'FullControl','Allow'))",
])
def test_private_file_acl_never_accepts_untrusted_owner_extra_grant_deny_or_missing_system(change):
    code=acl_code()+change+";$ok=$true;try{Assert-ClaudeHistoryPrivateAcl $acl -AllowInherited}catch{$ok=$false};$ok|ConvertTo-Json"
    assert json.loads(ps(code)) is False


@pytest.mark.parametrize('change,ok', [('',True),('$acl.SetAccessRuleProtection($false,$true)',False),
    ("$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'Read','ContainerInherit,ObjectInherit','None','Allow'))",False)])
def test_session_root_requires_protected_ba_system_directory_acl(change,ok):
    code=r"""$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false);$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'));
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))};"""
    code+=change+r""";$ok=$true;try{Assert-ClaudeHistoryPrivateAcl $acl -Directory}catch{$ok=$false};$ok|ConvertTo-Json"""
    assert json.loads(ps(code)) is ok


def main_fixture(value=None):
    value=receipt() if value is None else value
    layout=dict(slots=dict(slot1=dict(sid=SID)))
    return task_code()+acl_code()+f"$receipt={literal(value)};$runtime={literal(runtime())};$layout={literal(layout)};"+rf"""
    $script:queries=[Collections.Generic.List[string]]::new();$script:reads=[Collections.Generic.List[object]]::new();$script:taskReads=0;
    $script:old=$false;$script:roots=@([pscustomobject]@{{Name='InteractiveClaude427-r3-slot1-{NONCE}';FullName='{ROOT}'}});$script:orphan=$false;
    $folder=[pscustomobject]@{{}};$folder|Add-Member ScriptMethod GetTasks {{param($flags)if($flags -ne 1){{throw 'Hidden tasks omitted'}};if($script:orphan){{[pscustomobject]@{{Name='CoChem-4.2.7-InteractiveClaude-r3-slot1-'+('b'*32)}}}}else{{$task}}}};
    function Assert-NoReparseAncestors {{param($Path)}}
    function Assert-ProtectedPath {{param($Path)}}
    function Assert-ClaudeHistoryPrivateRoot {{param($Path)$script:queries.Add('private-root')}}
    function Get-Acl {{param($LiteralPath,$ErrorAction)return $acl}}
    function Get-ChildItem {{param($LiteralPath,[switch]$Directory,[switch]$Force,$Filter,$ErrorAction)
      $script:queries.Add($Filter);if($Filter -ceq 'InteractiveClaude427-slot1-*'){{if($script:old){{[pscustomobject]@{{Name='old'}}}}}}else{{$script:roots}}
    }}
    function Get-ExactTaskOrAbsent {{param($Folder,$Name)$script:taskReads++;return $task}}
    function Read-R3Control {{param($Path,$Hash='',[long]$Maximum=16777216)
      $script:reads.Add([pscustomobject]@{{path=$Path;hash=$Hash;maximum=$Maximum}})
      if($Path.EndsWith('windows-layout.json')){{return [pscustomobject]@{{Sha256=$Hash;Text=($layout|ConvertTo-Json -Depth 5)}}}}
      if($Path.EndsWith('receipt.json')){{return [pscustomobject]@{{Sha256=('c'*64);Text=($receipt|ConvertTo-Json -Depth 9)}}}}
      if($Path.EndsWith('worker_claude_login_bridge_r3.py') -and $Hash -cne '{BRIDGE_PIN}'){{throw 'Bridge pin differs'}}
      if($Path.EndsWith('worker_native_status_support.py') -and $Hash -cne '{SUPPORT_PIN}'){{throw 'Support pin differs'}}
      return [pscustomobject]@{{Sha256=$Hash;Text=''}}
    }}
    function Read-R3Text {{param($Control)return $Control.Text}}
    """


def test_main_scans_both_prefixes_rechecks_task_and_reads_only_three_nonsecret_controls():
    code=main_fixture()+r"""$proof=@(Assert-ReviewedClaudeHistory -Slot slot1 -Folder $folder -Runtime $runtime);[ordered]@{proof=$proof;queries=@($script:queries.ToArray());reads=@($script:reads.ToArray());task_reads=$script:taskReads}|ConvertTo-Json -Depth 6 -Compress"""
    result=json.loads(ps(code))
    assert result['queries']==['InteractiveClaude427-slot1-*','InteractiveClaude427-r3-slot1-*','private-root']
    assert result['task_reads']==2 and len(result['proof'])==1
    assert result['proof'][0]['prior_state_preserved'] is True
    assert result['proof'][0]['current_logged_out_status_required'] is True
    assert result['proof'][0]['authentication_authorized_by_history'] is False
    reads=result['reads'];assert len(reads)==4
    assert [Path(r['path']).name for r in reads]==['windows-layout.json','worker_claude_login_bridge_r3.py','worker_native_status_support.py','receipt.json']
    assert reads[-1]['maximum']==32768 and reads[-1]['hash']==''


@pytest.mark.parametrize('change', [
    "$script:old=$true", "$script:roots[0].Name='InteractiveClaude427-r3-slot1-'+('A'*32)",
    "$script:roots[0].Name='InteractiveClaude427-r3-slot1-'+('0'*32)", "$script:roots[0].FullName='C:\\different'",
    "$script:roots=@($script:roots[0],$script:roots[0])", "$script:orphan=$true", "$script:roots=@()",
    "function Get-ExactTaskOrAbsent{param($Folder,$Name)$null}",
    "function Get-ExactTaskOrAbsent{param($Folder,$Name)throw [UnauthorizedAccessException]::new('private evidence')}",
    "function Read-R3Control{param($Path,$Hash,$Maximum)throw 'synthetic custody drift'}",
])
def test_main_unknown_partial_missing_inaccessible_or_changed_history_holds_before_login(change):
    code=main_fixture()+change+r""";$held=$false;try{$null=Assert-ReviewedClaudeHistory -Slot slot1 -Folder $folder -Runtime $runtime}catch{$held=$true};$held|ConvertTo-Json"""
    assert json.loads(ps(code)) is True


def test_main_known_failed_receipt_projects_only_safe_diagnostic_then_holds():
    value=receipt('pre_native');value['failure']['phase']='native_launch';value['failure']['message']='synthetic-private-value'
    code=main_fixture(value)+r"""$held=$false;try{$null=Assert-ReviewedClaudeHistory -Slot slot1 -Folder $folder -Runtime $runtime}catch{$held=$true};$held|ConvertTo-Json"""
    text=ps(code);lines=text.splitlines()
    assert json.loads(lines[-1]) is True and 'synthetic-private-value' not in text
    metadata=json.loads(next(line.split(' ',1)[1] for line in lines if line.startswith('COCHEM_CLAUDE_HISTORY_HOLD ')))
    assert metadata==dict(status='LOGIN_FAILED_OR_CANCELLED',login_commands_executed=0,model_jobs_executed=0,
                         failure=dict(phase='native_launch',error_type='ValueError',winerror=None))


def test_source_contains_no_runtime_action_or_private_channel_read():
    text=HELPER.read_text()
    for banned in ('Start-Process','RegisterTaskDefinition','Write-PrivatePacket','Write-CancelRequest','Stop-Process','Remove-Item','operator.log','input.once','cancel.request'):
        assert banned not in text
    assert "Read-R3Control $receiptPath '' 32768" in text
    assert "-AllowInherited" in text
    assert 'native_launch is deliberately excluded' in text
    assert '$Folder.GetTasks(1)' in text and 'RestartCount -ne 0' in text
