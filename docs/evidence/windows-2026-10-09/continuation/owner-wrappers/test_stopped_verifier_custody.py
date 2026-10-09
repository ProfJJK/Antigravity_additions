"""Native metadata-only verifier checks; no tasks, databases or installation."""
import json
import os
import subprocess
from pathlib import Path

import pytest

WORK = Path(__file__).parent
HELPER = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')
DB = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Lib\site-packages\mendeleev\elements.db')


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
                           "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"
                           "Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');" +
                           functions(HELPER) + functions(WORK / 'verify_stopped_pipeline.ps1') +
                           "$programFiles='C:\\Program Files';$TreeDeadlineSeconds=30;Initialize-FileIdentity;" + body],
                          text=True, capture_output=True, timeout=45)


def test_actual_bundled_database_uses_metadata_without_stream_read():
    assert DB.is_file(), 'Expected installed dependency is missing'
    result = run(f"""
    Assert-ProtectedPath {quote(DB)};
    $s=[IO.File]::Open({quote(DB)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);
    try{{[CoChemStagedFileIdentity]::Check($s,{quote(DB)});if($s.Position -ne 0){{throw 'Unexpected content read.'}}}}finally{{$s.Dispose()}};
    Inspect-CodeTree {quote(DB)} | ConvertTo-Json -Compress
    """)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'CUSTODY_VERIFIED'


@pytest.mark.parametrize('name', ['resource.db', 'resource.sqlite3', 'resource.db-wal'])
def test_database_like_resource_keeps_hardlink_refusal(tmp_path, name):
    source = tmp_path / name
    source.write_bytes(b'opaque non-database fixture')
    os.link(source, tmp_path / 'alias')
    result = run(f"""
    function Assert-ProtectedPath {{param($Path)}};
    Inspect-CodeTree {quote(source)} | ConvertTo-Json -Compress
    """)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'UNVERIFIED'
    assert 'Hardlinks and reparse files are forbidden' in report['reason']
