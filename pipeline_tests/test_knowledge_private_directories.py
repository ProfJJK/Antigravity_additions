"""Directory creation regressions; Windows fixtures are not SYSTEM acceptance."""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from cochem_pipeline import knowledge, windows


@pytest.fixture
def creation_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(knowledge, 'os', SimpleNamespace(name='nt'))
    def protected(path, *, directory=False, private=False):
        calls.append((path, directory, private))
        return knowledge._ordinary(path, directory=directory)
    monkeypatch.setattr(knowledge, '_protected', protected)
    return calls


@pytest.mark.parametrize('sid,mask,flags', [
    (windows.SYSTEM_SID, windows.FULL_CONTROL, 0),
    (windows.SYSTEM_SID, windows.FULL_CONTROL, 1),
    (windows.SYSTEM_SID, windows.FULL_CONTROL, 2),
    (windows.SYSTEM_SID, windows.FULL_CONTROL, 3 | 4),
    (windows.SYSTEM_SID, windows.FULL_CONTROL, 3 | 8),
    (windows.SYSTEM_SID, 0x120089, 3),
    (windows.ADMIN_SID, windows.FULL_CONTROL, 3),
    ('S-1-3-4', windows.FULL_CONTROL, 3),
])
def test_missing_effective_system_inheritance_refuses_before_creation(tmp_path, monkeypatch, creation_policy, sid, mask, flags):
    monkeypatch.setattr(windows, '_acl', lambda _: (windows.SYSTEM_SID, True, [(sid, mask, flags)]))
    target = tmp_path / 'must-not-exist'
    with pytest.raises(knowledge.KnowledgeError, match='inherit SYSTEM'):
        knowledge._private_directory(target)
    assert not target.exists()
    assert creation_policy == [(tmp_path, True, True)]


def test_untrusted_parent_refuses_before_acl_lookup_or_creation(tmp_path, monkeypatch):
    def reject(*_, **__):
        raise windows.WindowsIsolationError('Fixture parent is not private')
    monkeypatch.setattr(knowledge, '_protected', reject)
    target = tmp_path / 'must-not-exist'
    with pytest.raises(windows.WindowsIsolationError, match='not private'):
        knowledge._private_directory(target)
    assert not target.exists()


@pytest.mark.parametrize('platform,expected_mode', [('nt', 0o777), ('posix', 0o700)])
def test_platform_creation_mode_and_child_validation(tmp_path, monkeypatch, creation_policy, platform, expected_mode):
    monkeypatch.setattr(knowledge, 'os', SimpleNamespace(name=platform))
    monkeypatch.setattr(windows, '_acl', lambda _: (windows.SYSTEM_SID, True, [(windows.SYSTEM_SID, windows.FULL_CONTROL, 3)]))
    original = Path.mkdir
    modes = []
    def mkdir(path, mode=0o777, parents=False, exist_ok=False):
        modes.append(mode)
        return original(path, mode=mode, parents=parents, exist_ok=exist_ok)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    target = tmp_path / 'new'
    knowledge._private_directory(target)
    assert modes == [expected_mode]
    assert creation_policy == [(tmp_path, True, True), (target, True, True)]


@pytest.mark.parametrize('existing', ['directory', 'file'])
def test_existing_paths_are_preserved_without_acl_repair(tmp_path, monkeypatch, creation_policy, existing):
    monkeypatch.setattr(windows, '_acl', lambda _: (windows.SYSTEM_SID, True, [(windows.SYSTEM_SID, windows.FULL_CONTROL, 3)]))
    target = tmp_path / 'existing'
    if existing == 'directory':
        target.mkdir(); sentinel = target / 'sentinel'; sentinel.write_bytes(b'preserve')
        knowledge._private_directory(target, exist_ok=True)
        assert sentinel.read_bytes() == b'preserve'
    else:
        target.write_bytes(b'preserve')
        with pytest.raises(FileExistsError):
            knowledge._private_directory(target, exist_ok=True)
        assert target.read_bytes() == b'preserve'


@pytest.fixture
def actual_windows_private_fixture(tmp_path, monkeypatch):
    if os.name != 'nt':
        pytest.skip('Actual Windows ACL fixture')
    root = tmp_path / 'controlled'
    root.mkdir()
    original_acl = windows._acl
    owner = original_acl(root)[0]
    api = windows._api()
    descriptor = windows.HANDLE()
    # Only this new disposable test directory: preserve its real owner and give
    # that fixture principal access alongside SYSTEM/Admin, with inheritance.
    sddl = f'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{owner})'
    windows._check(api['advapi32'].ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, C.byref(descriptor), None), 'Build test ACL')
    try:
        windows._check(api['advapi32'].SetFileSecurityW(str(root), 0x80000004, descriptor), 'Apply test ACL')
    finally:
        api['kernel32'].LocalFree(descriptor)
    # Explicit identity-only fixture boundary: the test cannot acquire SYSTEM.
    # Real ACL masks/flags and every unexpected SID (especially OWNER RIGHTS)
    # are retained. Production validation is never changed or bypassed on disk.
    def fixture_acl(path):
        assert Path(path).is_relative_to(root)
        observed_owner, protected, rules = original_acl(path)
        return (windows.SYSTEM_SID if observed_owner == owner else observed_owner, protected,
                [(windows.SYSTEM_SID if sid == owner else sid, mask, flags) for sid, mask, flags in rules])
    monkeypatch.setattr(windows, 'require_system', lambda: None)
    monkeypatch.setattr(windows, '_acl', fixture_acl)
    return SimpleNamespace(root=root, raw_acl=original_acl)


def test_actual_python_mode700_reproduces_owner_rights_rejection(actual_windows_private_fixture):
    fixture = actual_windows_private_fixture
    old = fixture.root / 'old-generation'
    old.mkdir(mode=0o700)
    target = old / 'knowledge_index.db'; target.write_bytes(b'fixture')
    for path in (old, target):
        assert any(sid == 'S-1-3-4' for sid, _, _ in fixture.raw_acl(path)[2])
        with pytest.raises(windows.WindowsIsolationError, match='outside SYSTEM'):
            windows.validate_private_path(path)
    before = fixture.raw_acl(old)
    with pytest.raises(windows.WindowsIsolationError, match='outside SYSTEM'):
        knowledge._private_directory(old, exist_ok=True)
    assert fixture.raw_acl(old) == before and target.read_bytes() == b'fixture'


def test_actual_inheritance_has_no_owner_rights_and_preserves_existing_acl(actual_windows_private_fixture):
    fixture = actual_windows_private_fixture
    state = fixture.root / 'state'
    knowledge._private_directory(state)
    original = fixture.raw_acl(state)
    knowledge._private_directory(state, exist_ok=True)
    assert fixture.raw_acl(state) == original
    generation = state / 'generation'
    knowledge._private_directory(generation)
    target = generation / 'knowledge_index.db'; target.write_bytes(b'fixture')
    for path in (state, generation, target):
        assert not any(sid == 'S-1-3-4' for sid, _, _ in fixture.raw_acl(path)[2])
        windows.validate_private_path(path)


def test_actual_fts_refresh_uses_inheritance_for_new_state_and_generation(actual_windows_private_fixture):
    fixture = actual_windows_private_fixture
    corpus = fixture.root / 'corpus'; corpus.mkdir()
    (corpus / '.sources').mkdir(); (corpus / 'wiki').mkdir()
    entries = []
    for key, text in {'.sources/reference.md': '# Reference\nPrivate fixture corpus.\n',
                      'wiki/00_skeleton.md': '# Catalog\n[Reference](../.sources/reference.md)\n'}.items():
        raw = text.encode(); (corpus / key).write_bytes(raw)
        entries.append({'path': key, 'sha256': hashlib.sha256(raw).hexdigest()})
    manifest = corpus / 'v4.1.2_manifest.json'
    manifest.write_text(json.dumps({'manifest_version': '4.2.7', 'documents': entries,
                                    'srs_documents': ['wiki/00_skeleton.md']}), encoding='utf-8')
    config = knowledge.KnowledgeConfig(enabled=True, source_root=str(corpus / '.sources'),
        wiki_root=str(corpus / 'wiki'), manifest_path=str(manifest), state_root=str(fixture.root / 'state'))
    service = knowledge.KnowledgeService(config)
    try:
        result = service.refresh()
        assert result['document_count'] == 2 and result['index_bytes'] > 0
        with service._reader() as (_, database):
            assert database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        _, index = service._current()
        assert not any(sid == 'S-1-3-4' for sid, _, _ in fixture.raw_acl(index)[2])
        assert service.refresh()['changed_documents'] == 0
    finally:
        service.close()


def test_actual_parent_junction_refuses_before_child_creation(tmp_path, monkeypatch, creation_policy):
    if os.name != 'nt':
        pytest.skip('Actual Windows junction fixture')
    real = tmp_path / 'real'; real.mkdir()
    alias = tmp_path / 'alias'
    result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(alias), str(real)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with pytest.raises(knowledge.KnowledgeError, match='without links'):
        knowledge._private_directory(alias / 'must-not-exist')
    assert not (real / 'must-not-exist').exists()


def test_actual_operations_nested_creation_and_read_only_refusal(actual_windows_private_fixture):
    from cochem_pipeline import operations_policy as operations
    fixture = actual_windows_private_fixture
    missing = fixture.root / 'absent' / 'nested'
    with pytest.raises(FileNotFoundError):
        operations._private(missing, create=False)
    assert not missing.parent.exists()
    ledger = operations.WorkloadObjectives(fixture.root / 'operations')
    try:
        ledger.record('background', 'fixture', 1, True, 'fixture-only')
        assert ledger.snapshot()['workloads']['background']['samples'] == 1
        windows.validate_private_path(ledger.database)
        nested = operations._private(missing)
        assert nested == missing
        for path in (missing.parent, missing):
            windows.validate_private_directory(path)
            assert not any(sid == 'S-1-3-4' for sid, _, _ in fixture.raw_acl(path)[2])
        original = fixture.raw_acl(missing)
        operations._private(missing)
        assert fixture.raw_acl(missing) == original
    finally:
        ledger.close()


def test_actual_operations_nested_archive_inherits_private_acl(actual_windows_private_fixture):
    from cochem_pipeline import operations_policy as operations
    fixture = actual_windows_private_fixture
    nested = operations._private(fixture.root / 'telemetry' / 'daily')
    source = nested / 'actual.receipt.json'
    source.write_bytes(b'{"fixture":true}'); os.utime(source, (1, 1))
    result = operations.archive_evidence(fixture.root, ['telemetry/daily/actual.receipt.json'])
    copy = Path(result['archive_directory']) / 'telemetry/daily/actual.receipt.json'
    assert source.read_bytes() == copy.read_bytes()
    for path in (copy.parent.parent, copy.parent, copy):
        windows.validate_private_path(path)
        assert not any(sid == 'S-1-3-4' for sid, _, _ in fixture.raw_acl(path)[2])


@pytest.mark.parametrize('flags', [0, 1, 2, 7, 11])
def test_operations_refuses_missing_system_inheritance_before_new_ancestor(tmp_path, monkeypatch, flags):
    from cochem_pipeline import operations_policy as operations
    monkeypatch.setattr(operations, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(windows, 'require_system', lambda: None)
    monkeypatch.setattr(windows, 'validate_private_directory', lambda _: None)
    monkeypatch.setattr(windows, '_acl', lambda _: (windows.SYSTEM_SID, True, [(windows.SYSTEM_SID, windows.FULL_CONTROL, flags)]))
    with pytest.raises(PermissionError, match='inherit SYSTEM'):
        operations._private(tmp_path / 'new-parent' / 'new-child')
    assert not (tmp_path / 'new-parent').exists()
