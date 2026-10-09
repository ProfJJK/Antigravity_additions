"""Windows native payload custody checks; never install, copy protected files or run CLIs."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/stage_aetherdesk_427_native.ps1'
NATIVE_ROOT = 'Native4.2.7-windows-20261006'
NAMES = ['codex.exe', 'codex-code-mode-host.exe', 'claude.exe', 'agy.exe', 'native-contract-agy-1.3.1-followup.json']
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Windows handle and path semantics')


def quote(path):
    return "'" + str(path).replace("'", "''") + "'"


def run(code):
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code],
                          text=True, capture_output=True, timeout=60)


def inventory(tmp_path):
    files = []
    for name in NAMES:
        source = tmp_path / name
        source.write_bytes(b'not an executable: custody fixture\n')
        files.append(dict(source=str(source), destination=NATIVE_ROOT + '/' + name,
                          length=source.stat().st_size, sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    value = dict(schema='cochem-native-payload-inventory/1', host='AETHERDESK', roots=[NATIVE_ROOT], files=files)
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


def invoke(path, apply=False):
    return run(f"& {quote(SCRIPT)} -Manifest {quote(path)} -ManifestSha256 '{hashlib.sha256(path.read_bytes()).hexdigest()}'" + (' -Apply' if apply else ''))


def destination_metadata():
    path = Path(os.environ['ProgramFiles']) / 'CoChem' / NATIVE_ROOT
    try:
        item = path.lstat()
    except FileNotFoundError:
        return None
    return item.st_dev, item.st_ino, item.st_mtime_ns, item.st_file_attributes


def test_native_preview_never_executes_files_or_changes_hold(tmp_path):
    path, _ = inventory(tmp_path)
    before = destination_metadata()
    result = invoke(path)
    assert destination_metadata() == before
    if before is not None:
        assert result.returncode != 0 and 'Preserve existing payload destination' in result.stderr
        return
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['schema'] == 'cochem-native-payload-stage/1'
    assert receipt['mode'] == 'READ_ONLY_PLAN' and receipt['files_verified'] == 5
    assert receipt['files_copied'] == receipt['payloads_executed'] == receipt['tasks_changed'] == receipt['accounts_changed'] == 0
    assert receipt['supervisor_history'] == 'UNRESOLVED_UNTOUCHED'
    assert receipt['activation_ready'] is False
    assert not Path(receipt['protected_roots'][0]).exists()


@pytest.mark.parametrize('change', ['missing_helper', 'old_agy_evidence', 'profile', 'outside_root', 'duplicate', 'drift', 'hardlink'])
def test_native_inventory_refuses_incomplete_or_mutated_payload(tmp_path, change):
    if destination_metadata() is not None:
        pytest.skip('Fresh native destination is unavailable; keep the actual installation and its preservation guard intact')
    path, value = inventory(tmp_path)
    if change == 'missing_helper':
        del value['files'][1]
    elif change == 'old_agy_evidence':
        value['files'][-1]['destination'] = NATIVE_ROOT + '/native-contract-followup.json'
    elif change == 'profile':
        value['files'][-1]['destination'] = NATIVE_ROOT + '/.claude/credentials.json'
    elif change == 'outside_root':
        value['files'][0]['destination'] = NATIVE_ROOT + '/../codex.exe'
    elif change == 'duplicate':
        value['files'][1]['destination'] = value['files'][0]['destination'].upper()
    elif change == 'drift':
        Path(value['files'][0]['source']).write_bytes(b'changed')
    elif change == 'hardlink':
        os.link(value['files'][0]['source'], tmp_path / 'alias.bin')
    path.write_text(json.dumps(value), encoding='utf-8')
    result = invoke(path)
    assert result.returncode != 0
    assert not (Path(os.environ['ProgramFiles']) / 'CoChem' / NATIVE_ROOT).exists()


def test_native_apply_requires_elevation_before_destination(tmp_path):
    elevated = run("([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
    if elevated.stdout.strip() == 'True':
        pytest.skip('Never execute Apply in elevated test session')
    path, _ = inventory(tmp_path)
    before = destination_metadata()
    result = invoke(path, apply=True)
    expected = 'Preserve existing payload destination' if before is not None else 'requires an elevated administrator'
    assert result.returncode != 0 and expected in result.stderr
    assert destination_metadata() == before
