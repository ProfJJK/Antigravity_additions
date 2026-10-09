"""Native PowerShell completion/receipt regressions; never run tasks or installation."""
import json
import subprocess
from pathlib import Path

import pytest

WORK = Path(__file__).parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def functions(path):
    return f"""$tokens=$null;$errors=$null;
    $ast=[Management.Automation.Language.Parser]::ParseFile({quote(path)},[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """


def run(body):
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                           "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;" +
                           functions(REPO / 'scripts/install_aetherdesk_427_stopped.ps1') +
                           functions(WORK / 'resume_stopped_pipeline.ps1') + body],
                          text=True, capture_output=True, timeout=30)


@pytest.mark.parametrize('case,success', [
    ('fast_complete', True), ('normal_complete', True), ('wrong_nonce', False),
    ('missing_receipt', False), ('failed_result', False), ('still_running', False),
    ('other_instance', False), ('task_missing', False), ('access_denied', False),
    ('account_information_missing', False), ('malformed_receipt', False),
])
def test_completion_race_requires_stopped_success_and_exact_receipt(tmp_path, case, success):
    receipt = tmp_path / 'receipt.json'
    value = dict(schema='cochem-fresh-identity-precheck/1', nonce='fresh', system_sid='S-1-5-18',
                 status='FRESH_TARGETS_ABSENT', local_accounts_checked=6,
                 credential_targets_checked=6, credential_blobs_dereferenced=False)
    if case == 'wrong_nonce':
        value['nonce'] = 'stale'
    if case != 'missing_receipt':
        receipt.write_text('{bad' if case == 'malformed_receipt' else json.dumps(value), encoding='utf-8')
    error = {'task_missing': -2147024894, 'access_denied': -2147024891,
             'account_information_missing': -2147216625}.get(case, -2147216629)
    refresh = '' if case == 'normal_complete' else f"throw [Runtime.InteropServices.COMException]::new('captured task completion',{error})"
    result = run(f"""
    function Start-Sleep {{param($Milliseconds)}};
    function Assert-ProtectedPath {{param($Path)}};
    $instance=[pscustomobject]@{{State=3}};$instance|Add-Member ScriptMethod Refresh {{{refresh}}};
    $task=[pscustomobject]@{{State={4 if case == 'still_running' else 3};LastTaskResult={1 if case == 'failed_result' else 0}}};
    $task|Add-Member ScriptMethod GetInstances {{param($flags) [pscustomobject]@{{Count={1 if case == 'other_instance' else 0}}}}};
    Wait-CompletedPrecheck $instance $task {quote(receipt)} 'fresh';
    'PASSED'
    """)
    assert (result.returncode == 0) is success, result.stderr
    if success:
        assert result.stdout.strip() == 'PASSED'


@pytest.mark.parametrize('change,success', [('', True),
    ("$task.Definition.Principal.UserId='ansac'", False),
    ("$task.Definition.Triggers.Count=1", False),
    ("$action.Arguments+=' stale'", False),
    ("$task.LastTaskResult=1", False),
    ("$task.State=4", False),
])
def test_original_task_must_match_actual_preserved_scope(change, success):
    result = run(r"""
    $script:guardRoot='C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006';
    $action=[pscustomobject]@{Type=0;Path="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe";WorkingDirectory=$guardRoot;
    Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $guardRoot 'identity-precheck.ps1')+'" -Nonce 27c861adcbd741798cf66d7c9bb1c8b4'};
    $actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($index) $action};
    $task=[pscustomobject]@{State=3;LastTaskResult=0;Definition=[pscustomobject]@{
      Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}};
    $task|Add-Member ScriptMethod GetInstances {param($flags) [pscustomobject]@{Count=0}};
    $task|Add-Member ScriptMethod GetSecurityDescriptor {param($flags) 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'};
    """ + change + '; Assert-OriginalPrecheckTask $task')
    assert (result.returncode == 0) is success, result.stderr


def test_resume_imports_reject_changed_frozen_helper(tmp_path):
    source = tmp_path / 'helper.ps1'
    source.write_text('function NoAction { "changed" }', encoding='utf-8')
    result = run(f"Get-FrozenFunctions {quote(source)} ('0'*64) @('NoAction')")
    assert result.returncode != 0 and 'Frozen helper changed' in result.stderr
