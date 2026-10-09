"""Read-only installer planning and refusal; never install or load a driver."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_aetherdesk_pawnio.ps1"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
STAGE = Path(r"C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows PowerShell and installed host metadata")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", *args],
                          capture_output=True, text=True, timeout=45)


def test_default_plan_does_not_launch_install_or_write_reports() -> None:
    before = set(STAGE.glob("pawnio-2.1.0-install-*"))
    result = run("-File", str(SCRIPT))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mode"] == "READ_ONLY_PLAN"
    assert data["install_invoked"] is False
    assert data["exit_code"] is None and data["automatic_reboot"] is False
    assert "task_xml_backup" not in data
    assert set(STAGE.glob("pawnio-2.1.0-install-*")) == before
    if "verified_source" in data:
        assert data["verified_source"]["signature"] == "Valid"
        assert data["verified_source"]["sha256"] == data["expected_sha256"]
    else:
        assert data["holds"], "Missing source attestation must hold the plan"


def test_install_switch_refuses_nonadministrator_before_any_write() -> None:
    # Never exercise this switch from an elevated test process.
    identity = run("-Command", "([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
    assert identity.returncode == 0, identity.stderr
    if identity.stdout.strip() != "False":
        pytest.skip("Refusal proof requires an ordinary-user process; no install invocation is permitted here")
    before = set(STAGE.glob("pawnio-2.1.0-install-*"))
    result = run("-File", str(SCRIPT), "-Install")
    assert result.returncode != 0
    assert "-Install requires the existing AETHERDESK" in result.stderr
    assert "nothing was launched or written" in result.stderr
    assert set(STAGE.glob("pawnio-2.1.0-install-*")) == before


def test_private_task_xml_backup_preserves_encoding_and_refuses_overwrite(tmp_path: Path) -> None:
    # Load only file/ACL utilities; never evaluate installer dispatch.
    harness = tmp_path / "backup-test.ps1"
    harness.write_text(r'''
param([string]$Source,[string]$TestStage)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$stage=Join-Path $TestStage 'private'; $operatorSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$functions=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Assert-PlainPath','Hash-Bytes','Get-PrivateDirectoryEvidence','Write-NewPrivateFile')},$true))
if ($functions.Count -ne 4) { throw 'Expected exactly the four file/ACL utilities.' }
foreach ($function in $functions) { . ([ScriptBlock]::Create($function.Extent.Text)) }
$directory=[IO.DirectoryInfo]::new($stage)
$acl=[Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true,$false)
$acl.SetOwner([Security.Principal.SecurityIdentifier]::new($operatorSid))
foreach ($sid in @($operatorSid,'S-1-5-18','S-1-5-32-544')) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
}
$directory.Create($acl)
$path=Join-Path $stage 'task.xml'
$xml='<?xml version="1.0" encoding="UTF-16"?><Task><Name>R preservation</Name></Task>'
Write-NewPrivateFile $path $xml -TaskXml
$bytes=[IO.File]::ReadAllBytes($path)
if ($bytes[0] -ne 255 -or $bytes[1] -ne 254) { throw 'Expected UTF-16 LE BOM.' }
if ([IO.File]::ReadAllText($path) -cne $xml) { throw 'XML text changed.' }
$document=[xml]([IO.File]::ReadAllText($path))
if ($document.Task.Name -ne 'R preservation') { throw 'XML could not be parsed.' }
$refused=$false
try { Write-NewPrivateFile $path 'overwrite' } catch { $refused=$true }
if (-not $refused -or [IO.File]::ReadAllText($path) -cne $xml) { throw 'Existing backup was overwritten.' }
$outside=Join-Path (Split-Path -Parent $stage) 'outside-private-stage.json'
$refused=$false
try { Write-NewPrivateFile $outside '{}' } catch { $refused=$true }
if (-not $refused -or (Test-Path -LiteralPath $outside)) { throw 'Outside report path was accepted.' }
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','ContainerInherit,ObjectInherit','None','Allow'))
$unsafeDirectory=Join-Path $stage 'world-readable'
[IO.DirectoryInfo]::new($unsafeDirectory).Create($acl)
$refused=$false; $unsafe=Join-Path $unsafeDirectory 'must-not-create.json'
try { Write-NewPrivateFile $unsafe '{}' } catch { $refused=$true }
if (-not $refused -or (Test-Path -LiteralPath $unsafe)) { throw 'Unrelated directory read grant was accepted.' }
[ordered]@{parse_errors=0;utf16_preserved=$true;overwrite_refused=$true;outside_refused=$true;private_acl_refused=$true;installer_executed=$false} | ConvertTo-Json
''', encoding="utf-8")
    result = run("-File", str(harness), "-Source", str(SCRIPT), "-TestStage", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "parse_errors": 0, "utf16_preserved": True, "overwrite_refused": True,
        "outside_refused": True, "private_acl_refused": True, "installer_executed": False,
    }
