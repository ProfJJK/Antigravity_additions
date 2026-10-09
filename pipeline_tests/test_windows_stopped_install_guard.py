"""Stopped-install guard tests. No installer, SYSTEM task, account or credential call runs."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Windows PowerShell 5.1 parser and ACL semantics')


def ps(code):
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code],
                          text=True, capture_output=True, timeout=30)


def definitions(script):
    path = str(ROOT / 'scripts' / script).replace("'", "''")
    return f"""$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest;
    Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($function in $ast.FindAll({{param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($function.Extent.Text))}};
    """


def test_installer_checks_actual_program_files_without_name_translation():
    result = ps(definitions('install_pipeline_windows.ps1') + 'Assert-ProtectedItem $env:ProgramFiles; "verified"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'verified'


@pytest.mark.parametrize('rights,accepted', [('ReadAndExecute', True), ('Modify', False)])
def test_unresolved_sid_retains_exact_writer_check(rights, accepted):
    result = ps(definitions('install_pipeline_windows.ps1') + f"""
    $script:fixture=[Security.AccessControl.DirectorySecurity]::new();
    $fixture.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'));
    $fixture.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-21-1999999991-1999999992-1999999993-29999'),[Security.AccessControl.FileSystemRights]::{rights},[Security.AccessControl.AccessControlType]::Allow));
    function Get-Item {{param($LiteralPath,[switch]$Force) [pscustomobject]@{{Attributes=[IO.FileAttributes]::Directory}}}};
    function Get-Acl {{param($LiteralPath) $script:fixture}};
    Assert-ProtectedItem 'C:\\fixture'
    """)
    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert 'Untrusted writes or replacement' in result.stderr


@pytest.mark.parametrize('error,expected', [(-2147024894, True), (-2147216625, False), (-2147024891, False), (-2147467259, False)])
def test_task_missing_is_distinguished_from_denied_or_uncertain(error, expected):
    result = ps(definitions('install_aetherdesk_427_stopped.ps1') + f"""
    $folder=New-Object psobject;
    $folder|Add-Member ScriptMethod GetTask {{param($name) throw [Runtime.InteropServices.COMException]::new('fixture',{error})}};
    Assert-TaskAbsent $folder 'CoChem-4.2.2-Provision'
    """)
    assert (result.returncode == 0) is expected, result.stderr
    if not expected:
        assert 'not absence' in result.stderr


def test_existing_fixed_task_is_refused_and_installer_arguments_do_not_activate():
    result = ps(definitions('install_aetherdesk_427_stopped.ps1') + """
    $folder=New-Object psobject;$folder|Add-Member ScriptMethod GetTask {param($name) [pscustomobject]@{Name=$name}};
    $refused=$false;try {Assert-TaskAbsent $folder 'CoChem-4.2.2-Provision'} catch {$refused=$true};
    [ordered]@{refused=$refused;arguments=@(Get-StoppedInstallerArguments)}|ConvertTo-Json -Depth 3 -Compress
    """)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['refused'] is True
    args = value['arguments']
    assert args[args.index('-Slots') + 1] == '6'
    assert args[args.index('-DataRoot') + 1] == r'C:\ProgramData\CoChemPipeline427'
    assert '-RefuseProvisionTaskOverwrite' in args
    assert '-RegisterDaemon' not in args and '-RegisterSupervisor' not in args and '-Config' not in args


def test_actual_powershell_binding_passes_named_parameters_without_activation(tmp_path):
    # This inert script is the invocation boundary, not an installer replacement
    # on disk. Real PowerShell parameter binding must retain paths with spaces.
    stub = tmp_path / 'inert installer.ps1'
    stub.write_text('param($Python,$Uv,$OperatorName,$InstallRoot,$DataRoot,$TokenFile,$WardenTaskName,$SupervisorTaskName,[int]$Slots,[switch]$RefuseProvisionTaskOverwrite,[switch]$RegisterDaemon)\n'
                    '[ordered]@{python=$Python;slots=$Slots;refuse=$RefuseProvisionTaskOverwrite.IsPresent;daemon=$RegisterDaemon.IsPresent;keys=@($PSBoundParameters.Keys)}|ConvertTo-Json -Depth 3 -Compress', encoding='utf-8')
    literal = str(stub).replace("'", "''")
    result = ps(definitions('install_aetherdesk_427_stopped.ps1') + f"Invoke-StoppedInstaller '{literal}'")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['python'] == r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
    assert value['slots'] == 6 and value['refuse'] is True and value['daemon'] is False
    assert 'RegisterDaemon' not in value['keys']


@pytest.mark.parametrize('refuse', [False, True])
def test_guarded_provision_registration_never_forces_task_replacement(refuse):
    result = ps(definitions('install_pipeline_windows.ps1') + f"$RefuseProvisionTaskOverwrite=${str(refuse).lower()};" + """
    function Register-ScheduledTask {param($TaskName,$Action,$Principal,$Settings,$ErrorAction,[switch]$Force)
        $script:registered=[ordered]@{name=$TaskName;force=$Force.IsPresent;action=$Action;principal=$Principal}}
    Register-IdentityProvisionTask 'inert-action' 'SYSTEM-fixture' 'inert-settings';
    $registered|ConvertTo-Json -Compress
    """)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(name='CoChem-4.2.2-Provision', force=not refuse,
                                           action='inert-action', principal='SYSTEM-fixture')


@pytest.mark.parametrize('change', ["$r.nonce='stale'", "$r.system_sid='S-1-5-32-544'", "$r.credential_targets_checked=5", "$r.status='EXISTING'", "$r.credential_blobs_dereferenced=$true"])
def test_identity_receipt_must_match_fresh_system_scope(change):
    result = ps(definitions('install_aetherdesk_427_stopped.ps1') + """
    $r=[pscustomobject]@{schema='cochem-fresh-identity-precheck/1';nonce='fresh';system_sid='S-1-5-18';status='FRESH_TARGETS_ABSENT';local_accounts_checked=6;credential_targets_checked=6;credential_blobs_dereferenced=$false};
    Assert-IdentityReceipt $r 'fresh';
    """ + change + "; Assert-IdentityReceipt $r 'fresh'")
    assert result.returncode != 0 and 'stale or inconsistent' in result.stderr


def test_credential_precheck_refuses_ordinary_user_before_store_access():
    # The actual script rejects this account before any credential P/Invoke.
    path = str(ROOT / 'scripts/check_aetherdesk_427_fresh_identities.ps1').replace("'", "''")
    result = ps(f"& '{path}' -Nonce ('1'*32)")
    assert result.returncode != 0 and 'requires actual SYSTEM' in result.stderr


@pytest.mark.parametrize('collision', ['account', 'data', 'token', 'install', 'guard'])
def test_fresh_guard_preserves_every_existing_identity_or_state_boundary(collision):
    result = ps(definitions('install_aetherdesk_427_stopped.ps1') + f"$script:collision='{collision}';" + """
    $script:installRoot='C:\\fixture\\install';$script:guardRoot='C:\\fixture\\guard';
    $script:dataRoot='C:\\fixture\\data';$script:tokenFile='C:\\fixture\\token';
    $folder=New-Object psobject;
    $folder|Add-Member ScriptMethod GetTask {param($name) throw [Runtime.InteropServices.COMException]::new('missing',-2147024894)};
    function Test-Path {param($LiteralPath,$ErrorAction) $LiteralPath -eq ('C:\\fixture\\'+$script:collision)};
    function Get-CimInstance {param($ClassName,$Filter,$ErrorAction) if($script:collision -eq 'account'){[pscustomobject]@{Name='CoChem422Worker1'}}};
    Assert-FreshDeployment $folder
    """)
    assert result.returncode != 0
    assert 'Existing worker accounts' in result.stderr or 'refuses existing state' in result.stderr
