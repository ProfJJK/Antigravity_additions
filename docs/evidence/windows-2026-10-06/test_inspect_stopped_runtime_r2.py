"""No r2 inspection/installation; function-only fixtures and old stdlib identity."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SCRIPT = ROOT / 'inspect-stopped-runtime-r2.ps1'


def run(body):
    source = str(SCRIPT).replace("'", "''")
    prefix = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', prefix + body],
                          capture_output=True, text=True, timeout=40)


@pytest.mark.parametrize('case', ['valid', 'failed', 'wrong_config', 'tasks_changed', 'missing_assets', 'unverified'])
def test_successful_r2_receipt_required(case):
    result = run(f"$case='{case}';" + r'''
    $script:install='C:\reviewed-r2';$script:manifestHash=('a'*64);$script:configHash=('b'*64);
    $r=[pscustomobject]@{schema='cochem-stopped-runtime-update/1';mode='FRESH_STOPPED_RUNTIME_READY';target_root=$script:install;source_manifest_sha256=$script:manifestHash;configuration_sha256=$script:configHash;source_files=166;holds=@();accounts_provisioned=0;tasks_changed=0;credentials_modified=$false;databases_modified=$false;ram_modified=$false;pipeline_started=$false;activation_ready=$false;verified_entries=16000;verification=[pscustomobject]@{revision=[pscustomobject]@{verified=$true};configuration_parsed=$true;ram_workspace_root='R:\CoChem427-windows-20261007';no_system_or_model_execution=$true}};
    switch($case){'failed'{$r.mode='FAILED'}'wrong_config'{$r.configuration_sha256=('c'*64)}'tasks_changed'{$r.tasks_changed=1}'missing_assets'{$r.source_files=165}'unverified'{$r.verification.revision.verified=$false}};
    $refused=$false;try{Assert-R2Receipt $r}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] is (case != 'valid')


@pytest.mark.parametrize('case', ['uv', 'stdlib', 'duplicate', 'wrong_base', 'system_site', 'wrong_version'])
def test_venv_binding_is_exact_and_duplicate_keys_rejected(case):
    result = run(f"$case='{case}';" + r'''
    $script:baseRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';
    $versionKey=if($case -eq 'stdlib'){'version'}else{'version_info'};
    $text="home = $script:baseRoot`ninclude-system-site-packages = false`n$versionKey = 3.12.13`n";
    switch($case){'duplicate'{$text+="HOME = D:\other`n"}'wrong_base'{$text=$text.Replace($script:baseRoot,'D:\other')}'system_site'{$text=$text.Replace('false','true')}'wrong_version'{$text=$text.Replace('3.12.13','3.11.0')}};
    $refused=$false;try{Assert-R2Venv $text}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] is (case not in ('uv', 'stdlib'))


def test_actual_ps51_isolated_no_site_stdlib_identity_of_preserved_runtime():
    result = run(r'''
    $script:install='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006';
    $script:baseRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';
    Invoke-InterpreterIdentity (Join-Path $script:install '.venv\Scripts\python.exe')|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['version'] == '3.12.13'
