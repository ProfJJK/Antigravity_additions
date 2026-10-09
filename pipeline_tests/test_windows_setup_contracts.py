"""Setup regressions; never install, authenticate, or create scheduled tasks.

Native PowerShell checks exercise extracted parameter/function blocks only.
They establish parser/argv/preservation behavior, not SYSTEM login acceptance.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from cochem_supervisor.windows import gemini_login_argv


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows local path and exclusive file creation semantics')
def test_prerequisite_inspector_rejects_unsafe_outputs_and_preserves_raced_file():
    # Execute only the output validation/helper prefix, never task/sensor discovery.
    staging = Path(os.environ['LOCALAPPDATA']) / 'CoChem/staging'
    staging.mkdir(parents=True, exist_ok=True)
    destination = staging / ('pytest-inspection-' + uuid4().hex)
    destination.mkdir()
    output = destination / 'report.json'
    script = str(ROOT / 'scripts/inspect_windows_prerequisites.ps1').replace("'", "''")
    encoded_output = str(output).replace("'", "''")
    try:
        result = powershell(f"""
            $ErrorActionPreference='Stop';
            $source=[IO.File]::ReadAllText('{script}');
            $prefix=$source.Substring(0,$source.IndexOf('$identity ='));
            $validate=[scriptblock]::Create($prefix);
            $refused=@();
            foreach ($path in @('C:relative.json','\\\\server\\share\\report.json','C:\\ProgramData\\report.json','{encoded_output}:stream','{encoded_output}\\..\\missing\\report.json')) {{
                try {{ & $validate -OutputPath $path }} catch {{ $refused += $path }}
            }}
            . $validate -OutputPath '{encoded_output}';
            [IO.File]::WriteAllText('{encoded_output}','concurrent evidence');
            $raceRefused=$false;
            try {{ Write-NewInspectionReport -Path '{encoded_output}' -Json 'replacement' }} catch {{ $raceRefused=$true }}
            [ordered]@{{refused=$refused.Count;race_refused=$raceRefused;retained=[IO.File]::ReadAllText('{encoded_output}')}} | ConvertTo-Json -Compress
        """)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'refused': 5, 'race_refused': True, 'retained': 'concurrent evidence'}
    finally:
        if output.exists():
            output.unlink()
        destination.rmdir()


def powershell(code: str) -> subprocess.CompletedProcess:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("Requires real PowerShell parser; no parser emulation")
    return subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
        text=True, capture_output=True, timeout=30,
    )


def parse_script(filename: str) -> str:
    path = str(ROOT / "scripts" / filename).replace("'", "''")
    return (
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
        f"$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors); "
        "if ($errors.Count) { throw ($errors | Out-String) }; "
    )


def test_normal_login_powershell_parser_matches_entire_provisioned_slot_range():
    result = powershell(parse_script("login_pipeline_worker.ps1") + r"""
        $body=$ast.ParamBlock.Extent.Text + '; [ordered]@{slot=$Slot; provider=$Provider; root=$InstallRoot; contract=$LoginContract}';
        $bind=[scriptblock]::Create($body);
        $accepted=@(1..256 | ForEach-Object { & $bind -Slot "slot$_" -Provider gemini -Executable 'C:\reviewed\agy.exe' -LoginContract 'C:\reviewed\login.json' });
        $refused=@();
        foreach ($slot in @('slot0','slot257','slot01','slot-1','repair','slot1x')) {
            try { & $bind -Slot $slot -Provider codex -Executable 'C:\reviewed\codex.exe' | Out-Null }
            catch { $refused += $slot }
        }
        [ordered]@{accepted=$accepted; refused=$refused} | ConvertTo-Json -Depth 5 -Compress
    """)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [row["slot"] for row in data["accepted"]] == [f"slot{i}" for i in range(1, 257)]
    assert all(row["root"].endswith(r"\CoChem\Pipeline4.2.7-r2") for row in data["accepted"])
    assert all(row["provider"] == "gemini" and row["contract"] == r"C:\reviewed\login.json" for row in data["accepted"])
    assert data["refused"] == ["slot0", "slot257", "slot01", "slot-1", "repair", "slot1x"]


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows paths and native ACL observations')
def test_normal_login_refuses_untrusted_installation_before_creating_a_system_task(tmp_path):
    # Execute the real guard against a real user-owned directory and file.
    # Extracting functions avoids the administrator-only scheduling body.
    root = str(tmp_path).replace("'", "''")
    result = powershell(parse_script('login_pipeline_worker.ps1') + r"""
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force -ErrorAction Stop;
        $definitions=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true);
        foreach ($definition in $definitions) { . ([scriptblock]::Create($definition.Extent.Text)) }
    """ + f"$root='{root}'; " + r"""
        $outsideRefused=$false; $ownerRefused=$false;
        try { Assert-ProtectedLoginInstallation -Path $root | Out-Null }
        catch { $outsideRefused=$_.Exception.Message -like '*protected Program Files*' }
        try { Assert-ProtectedItem -Item (Get-Item -LiteralPath $root) }
        catch { $ownerRefused=$_.Exception.Message -like '*Untrusted owner*' -or $_.Exception.Message -like '*Untrusted writes*' }
        [ordered]@{outside_refused=$outsideRefused; user_owned_refused=$ownerRefused} | ConvertTo-Json -Compress
    """)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'outside_refused': True, 'user_owned_refused': True}


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows deployment path validation')
@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_native_login_rejects_unprotected_layout_before_reading_account_fields(tmp_path, monkeypatch, provider):
    from cochem_pipeline import windows
    layout = tmp_path / 'untrusted-layout.json'
    original = b'invalid JSON must never be parsed as SYSTEM authority'
    layout.write_bytes(original)
    # This test isolates the native path guard; it does not assert a SYSTEM
    # identity. The real path guard still rejects this user-directory source.
    monkeypatch.setattr(windows, 'require_system', lambda: None)
    with pytest.raises(windows.WindowsIsolationError, match='protected Program Files/Windows paths'):
        windows.login_worker(layout, 'slot1', provider, str(tmp_path / 'untrusted.exe'), tmp_path / 'login.log')
    assert layout.read_bytes() == original
    assert not (tmp_path / 'login.log').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Read-only native Program Files ACL observation')
def test_normal_login_guard_reads_native_program_files_sids_without_name_translation():
    result = powershell(parse_script('login_pipeline_worker.ps1') + r"""
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force -ErrorAction Stop;
        $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-ProtectedItem'},$true);
        . ([scriptblock]::Create($definition.Extent.Text));
        Assert-ProtectedItem -Item (Get-Item -LiteralPath $env:ProgramFiles -Force);
        [ordered]@{native_program_files_guard=$true} | ConvertTo-Json -Compress
    """)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'native_program_files_guard': True}


@pytest.mark.parametrize("provider, contract, error", [
    ("gemini", [], "Agy login requires --login-contract"),
    ("codex", ["--login-contract", "unreviewed.json"], "only supported for the gemini"),
    ("claude", ["--login-contract", "unreviewed.json"], "only supported for the gemini"),
])
def test_normal_login_parser_rejects_missing_or_wrong_provider_contract(provider, contract, error):
    result = subprocess.run(
        [sys.executable, "-c", "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
         "runpy.run_module('cochem_pipeline.windows',run_name='__main__')", str(ROOT / "src"),
         "login", "--layout", "absent.json",
         "--slot", "slot256", "--provider", provider, "--executable", "absent.exe",
         "--log-path", "absent.log", *contract],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 2
    assert error in result.stderr
    assert "Traceback" not in result.stderr


def test_reviewed_gemini_login_contract_keeps_literal_argv_and_rejects_wrong_identity():
    executable = r"C:\Protected Tools\agy.exe"
    contract = dict(provider="gemini", executable=executable, purpose="subscription-login",
                    arguments=["parser-fixture-only", "literal value"],
                    capability_reference="Parser regression only; not native capability evidence")
    assert gemini_login_argv(executable, contract) == [executable, "parser-fixture-only", "literal value"]
    for changed in ({"provider": "codex"}, {"executable": "different.exe"},
                    {"purpose": "inference"}, {"capability_reference": ""},
                    {"arguments": []}, {"arguments": ["bad\x00argument"]}):
        with pytest.raises(ValueError):
            gemini_login_argv(executable, contract | changed)


@pytest.mark.parametrize("fail", [False, True])
def test_mcp_frozen_command_and_environment_restoration(fail):
    # Capture argv at the native-invocation boundary. No uv/install is executed.
    result = powershell(parse_script("install_mcp_windows.ps1") + r"""
        $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Install-FrozenEnvironment'},$true);
        . ([scriptblock]::Create($function.Extent.Text));
        function Invoke-NativeChecked { param($Executable,$Arguments)
            $script:record=[ordered]@{executable=$Executable; arguments=$Arguments; environment=$env:UV_PROJECT_ENVIRONMENT};
            if ($script:fail) { throw 'intentional invocation failure' }
        }
        $repo='C:\source with spaces'; $venv='C:\client with spaces\.venv-mcp'; $Uv='C:\tools\uv.exe';
        $env:UV_PROJECT_ENVIRONMENT='preserved-existing-environment';
    """ + f"$script:fail=${str(fail).lower()}; " + r"""
        $failed=$false;
        try { Install-FrozenEnvironment -PythonPath 'C:\Python312\python.exe' }
        catch { $failed=$true }
        [ordered]@{record=$record; restored=$env:UV_PROJECT_ENVIRONMENT; failed=$failed} | ConvertTo-Json -Depth 5 -Compress
    """)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["restored"] == "preserved-existing-environment"
    assert data["failed"] is fail
    assert data["record"] == {
        "executable": r"C:\tools\uv.exe",
        "environment": r"C:\client with spaces\.venv-mcp",
        "arguments": ["sync", "--project", r"C:\source with spaces", "--frozen", "--no-editable",
                      "--link-mode", "copy", "--extra", "mcp", "--python", r"C:\Python312\python.exe", "--no-python-downloads"],
    }


@pytest.mark.parametrize('filename',['install_pipeline_windows.ps1','install_supervisor_windows.ps1'])
def test_protected_installers_copy_frozen_files_instead_of_cache_hardlinks(filename):
    result=powershell(parse_script(filename)+r"""
        $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Install-FrozenEnvironment'},$true);
        . ([scriptblock]::Create($function.Extent.Text));
        function Invoke-Checked { param($Executable,$Arguments)
            $script:record=[ordered]@{executable=$Executable;arguments=$Arguments;environment=$env:UV_PROJECT_ENVIRONMENT}
        }
        $sourceRoot='C:\protected\source';$InstallRoot='C:\protected\release';$Uv='C:\protected\uv.exe';$Python='C:\protected\python.exe';
        $env:UV_PROJECT_ENVIRONMENT='preserved';Install-FrozenEnvironment;
        [ordered]@{record=$record;restored=$env:UV_PROJECT_ENVIRONMENT} | ConvertTo-Json -Depth 5 -Compress
    """)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout)
    argv=data['record']['arguments']
    assert argv[argv.index('--link-mode')+1]=='copy'
    assert '--frozen' in argv and '--no-editable' in argv and '--no-python-downloads' in argv
    assert data['restored']=='preserved'


def test_mcp_config_writer_preserves_existing_bytes(tmp_path):
    existing = tmp_path / "client.json"
    content = b'{"preserve":"controller and project mapping"}\n'
    existing.write_bytes(content)
    path = str(existing).replace("'", "''")
    result = powershell(parse_script("install_mcp_windows.ps1") + r"""
        $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-NewJson'},$true);
        . ([scriptblock]::Create($function.Extent.Text));
    """ + f"Write-NewJson -Path '{path}' -Value @{{changed='must not be written'}}")
    assert result.returncode == 0, result.stderr
    assert existing.read_bytes() == content


def test_legacy_checker_is_nonexecuting_even_with_old_arguments_and_no_site_packages(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / "scripts" / "verify_cli_mcp.py"),
         "--provider", "codex", "--config", str(tmp_path / "missing.json"), "--model", "retired-pin"],
        cwd=tmp_path, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 2
    assert "RETIRED" in result.stderr and "No model job was submitted" in result.stderr
    assert "Traceback" not in result.stderr
    assert not list(tmp_path.iterdir())
