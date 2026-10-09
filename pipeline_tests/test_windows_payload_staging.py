"""Real Windows read-handle and planner checks; no protected installation or ACL mutation."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/stage_aetherdesk_427_payloads.ps1'
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Windows handle custody semantics')


def ps(code):
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code],
                          text=True, capture_output=True, timeout=60)


def literal(path):
    return "'" + str(path).replace("'", "''") + "'"


def functions():
    return f"""$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null;
    $ast=[Management.Automation.Language.Parser]::ParseFile({literal(SCRIPT)},[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($function in $ast.FindAll({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($function.Extent.Text))}};
    Initialize-FileIdentity;
    """


def inventory(tmp_path):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'exact reviewed payload\n')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    roots = ['Toolchain4.2.7-windows-20261006', 'CpuSensors4.2.7-windows-20261006']
    result = dict(schema='cochem-protected-payload-inventory/1', host='AETHERDESK', roots=roots,
                  files=[dict(source=str(source), destination=root + '/fixture.bin',
                              length=source.stat().st_size, sha256=digest) for root in roots])
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps(result), encoding='utf-8')
    return path, result, source, digest


def destination_metadata(roots):
    result = {}
    for root in roots:
        path = Path(os.environ['ProgramFiles']) / 'CoChem' / root
        try:
            item = path.lstat()
        except FileNotFoundError:
            result[root] = None
        else:
            result[root] = (item.st_dev, item.st_ino, item.st_mtime_ns, item.st_file_attributes)
    return result


def test_default_is_read_only_even_with_fully_valid_inventory(tmp_path):
    path, contents, _, _ = inventory(tmp_path)
    before = destination_metadata(contents['roots'])
    result = ps(f"& {literal(SCRIPT)} -Manifest {literal(path)} -ManifestSha256 '{hashlib.sha256(path.read_bytes()).hexdigest()}'")
    assert destination_metadata(contents['roots']) == before
    if any(value is not None for value in before.values()):
        assert result.returncode != 0 and 'Preserve existing payload destination' in result.stderr
        return  # Existing installations are the expected preservation branch.
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['mode'] == 'READ_ONLY_PLAN'
    assert value['files_verified'] == 2
    assert value['files_copied'] == value['payloads_executed'] == value['tasks_changed'] == value['accounts_changed'] == 0
    assert not value['driver_installed'] and not value['activation_ready']
    assert all(not Path(root).exists() for root in value['protected_roots'])


def test_read_handle_blocks_writes_and_detects_changed_bytes(tmp_path):
    _, _, source, digest = inventory(tmp_path)
    code = functions() + f"""
    $path={literal(source)}; $stream=Open-VerifiedFile $path '{digest}' {source.stat().st_size};
    $blocked=$false; try {{ [IO.File]::WriteAllText($path,'mutated') }} catch {{ $blocked=$true }};
    $stream.Dispose(); [IO.File]::WriteAllText($path,'mutated');
    $changed=$false; try {{ $s=Open-VerifiedFile $path '{digest}' 7; $s.Dispose() }} catch {{ $changed=$true }};
    [ordered]@{{blocked=$blocked;changed=$changed}}|ConvertTo-Json -Compress
    """
    result = ps(code)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'blocked': True, 'changed': True}


def test_source_hardlink_is_refused(tmp_path):
    _, _, source, digest = inventory(tmp_path)
    os.link(source, tmp_path / 'alias.bin')
    result = ps(functions() + f"Open-VerifiedFile {literal(source)} '{digest}' {source.stat().st_size}")
    assert result.returncode != 0
    assert 'Hardlinks and reparse files are forbidden' in result.stderr


def test_apply_refuses_non_elevated_session_before_any_destination(tmp_path):
    elevated = ps("([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
    if elevated.stdout.strip() == 'True':
        pytest.skip('Never execute the Apply branch in an elevated test session')
    path, value, _, _ = inventory(tmp_path)
    before = destination_metadata(value['roots'])
    result = ps(f"& {literal(SCRIPT)} -Apply -Manifest {literal(path)} -ManifestSha256 '{hashlib.sha256(path.read_bytes()).hexdigest()}'")
    assert result.returncode != 0
    expected = 'Preserve existing payload destination' if any(v is not None for v in before.values()) else 'requires an elevated administrator'
    assert expected in result.stderr
    assert destination_metadata(value['roots']) == before


def test_code_acl_has_only_protected_system_admin_write_and_user_read():
    result = ps(functions() + """
    $acl=New-CodeAcl $true;
    [ordered]@{owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value;
    protected=$acl.AreAccessRulesProtected;
    rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|ForEach-Object{
    [ordered]@{sid=$_.IdentityReference.Value;rights=[string]$_.FileSystemRights;inherited=$_.IsInherited}})}|ConvertTo-Json -Depth 5 -Compress
    """)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['owner'] == 'S-1-5-32-544' and value['protected'] is True
    assert {row['sid']: row['rights'] for row in value['rules']} == {
        'S-1-5-18': 'FullControl', 'S-1-5-32-544': 'FullControl', 'S-1-5-32-545': 'ReadAndExecute, Synchronize'}
    assert all(not row['inherited'] for row in value['rules'])


@pytest.mark.parametrize('destination', [
    '../escape.bin', 'Toolchain4.2.7-windows-20261006/../escape.bin',
    'Toolchain4.2.7-windows-20261006/file:stream',
    'Toolchain4.2.7-windows-20261006/sub./file',
    'Toolchain4.2.7-windows-20261006/NUL.txt',
    'Toolchain4.2.7-windows-20261006/with*wildcard',
    'Native4.2.7-windows-20261006/agy.exe',
])
def test_manifest_cannot_escape_or_add_native_payloads(tmp_path, destination):
    path, value, _, _ = inventory(tmp_path)
    value['files'][0]['destination'] = destination
    path.write_text(json.dumps(value), encoding='utf-8')
    result = ps(functions() + f"$script:base='C:\\Program Files\\CoChem'; Get-PayloadPlan (Get-Content -LiteralPath {literal(path)} -Raw|ConvertFrom-Json)")
    assert result.returncode != 0
    assert 'payload destination' in result.stderr


def test_manifest_digest_and_duplicate_destinations_are_rejected(tmp_path):
    path, value, _, _ = inventory(tmp_path)
    changed = ps(functions() + f"Read-Inventory {literal(path)} ('0'*64)")
    assert changed.returncode != 0 and 'Source digest changed' in changed.stderr
    value['files'][1]['destination'] = value['files'][0]['destination'].upper()
    path.write_text(json.dumps(value), encoding='utf-8')
    duplicate = ps(functions() + f"$script:base='C:\\Program Files\\CoChem'; Get-PayloadPlan (Get-Content -LiteralPath {literal(path)} -Raw|ConvertFrom-Json)")
    assert duplicate.returncode != 0 and 'duplicate payload destination' in duplicate.stderr
