"""Synthetic security/real filesystem regressions, not physical R: acceptance."""
from __future__ import annotations

import ctypes as C
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cochem_pipeline import ramdisk as ram, windows as win


@pytest.mark.parametrize('name', ['..', '.', '../escape', r'a\b', 'a/b', 'a:stream',
                                 'NUL', 'COM1', 'trailing.', 'trailing ', 'wild*', True, None])
def test_subtree_names_cannot_escape_alias_or_redirect(name):
    with pytest.raises(ValueError, match='subdirectory'):
        ram.RamdiskConfig(workspace_subdirectory=name)


def test_subtree_is_explicit_adopted_only_and_ledger_bound():
    old = ram.RamdiskConfig(enabled=True)
    scoped = replace(old, workspace_subdirectory='CoChem427')
    assert scoped.workspace_root == Path('R:\\') / 'CoChem427'
    assert ram.RamWorkspace(scoped, 'slot1', 'Worker1', 1, 7, Path('private')).root == scoped.workspace_root / 'slot1'
    with pytest.raises(ValueError, match='subdirectory'):
        ram.RamdiskConfig(mount_root=r'C:\dedicated\ram\mount', workspace_subdirectory='CoChem427')
    prior = {'schema': 1, 'state': 'READY', 'mount_root': str(Path(old.mount_root)),
             'config': old.as_dict(), 'boot_id': 100,
             'observed': {'target': r'\Device\ImDisk7', 'device_number': 7, 'volume_serial': 1}}
    del prior['config']['workspace_subdirectory']
    assert ram.mount_recovery_action(prior, old, 100, r'\Device\ImDisk7') == 'VERIFY'
    with pytest.raises(ram.RamdiskError, match='differs'):
        ram.mount_recovery_action(prior, scoped, 100, r'\Device\ImDisk7')
    with pytest.raises(ram.RamdiskError, match='never be created'):
        ram.imdisk_create_argv(scoped)


def test_loaded_configuration_and_topology_bind_subtree_without_changing_mount(tmp_path):
    from cochem_pipeline.config import load_config
    from cochem_pipeline.deployment import topology_configuration
    from pipeline_tests.test_config import config_document, write_config
    raw = config_document(tmp_path)
    raw['ramdisk'] = {'enabled': True, 'mount_root': 'R:\\', 'workspace_subdirectory': 'CoChem427-windows-20261007'}
    config = load_config(write_config(tmp_path, raw))
    topology = topology_configuration(config)
    assert topology['ram_mount_root'] == 'R:\\'
    assert topology['ram_workspace_subdirectory'] == 'CoChem427-windows-20261007'
    assert config.ramdisk.workspace_root == Path('R:\\') / 'CoChem427-windows-20261007'
    old = replace(config, ramdisk=replace(config.ramdisk, workspace_subdirectory=''))
    assert topology_configuration(old) != topology


@pytest.mark.parametrize('mask,flags,allowed', [
    (0x1301BF, 0, True),  # Actual AETHERDESK root AU rights: DELETE != DELETE_CHILD.
    (0x1200A9, 0, True), (0x40000000, 0, True),
    (0x40, 0, False), (0x40000, 0, False), (0x80000, 0, False),
    (0x10000000, 0, False), (0x10000000, 8, True),
])
def test_adopted_parent_preserves_ordinary_owner_use_but_blocks_child_replacement(monkeypatch, mask, flags, allowed):
    monkeypatch.setattr(win, 'require_system', lambda: None)
    monkeypatch.setattr(ram, 'ordinary_tree', lambda path: None)
    monkeypatch.setattr(win, '_acl', lambda path: (win.SYSTEM_SID, False,
        [(win.SYSTEM_SID, win.FULL_CONTROL, 0), ('S-1-5-11', mask, flags)]))
    if allowed:
        ram._validate_adopted_parent(Path('R:\\'))
    else:
        with pytest.raises(ram.RamdiskError, match='replacement'):
            ram._validate_adopted_parent(Path('R:\\'))


def test_adopted_parent_rejects_untrusted_owner_and_nonroot(monkeypatch):
    monkeypatch.setattr(win, 'require_system', lambda: None)
    monkeypatch.setattr(ram, 'ordinary_tree', lambda path: None)
    monkeypatch.setattr(win, '_acl', lambda path: ('S-1-5-21-999', True, [(win.SYSTEM_SID, win.FULL_CONTROL, 0)]))
    with pytest.raises(ram.RamdiskError, match='replacement'):
        ram._validate_adopted_parent(Path('R:\\'))
    with pytest.raises(ram.RamdiskError, match='exact volume root'):
        ram._validate_adopted_parent(Path(r'R:\somewhere'))


class NativeFunction:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


def test_directory_creation_receives_final_protected_acl_atomically_and_refuses_collision(monkeypatch):
    calls = []
    monkeypatch.setattr(win, 'require_system', lambda: None)
    monkeypatch.setattr(ram, 'ordinary_tree', lambda path: calls.append(('ordinary', path)))
    monkeypatch.setattr(win, '_validate_boundary', lambda path, sids: calls.append(('validate', path)))
    def convert(sddl, revision, target, size):
        assert sddl == 'O:SYG:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;0x100020;;;S-1-5-21-1006)'
        C.cast(target, C.POINTER(win.HANDLE))[0] = 1234
        calls.append(('descriptor', sddl)); return 1
    def create(path, attributes):
        security = C.cast(attributes, C.POINTER(win._SECURITY_ATTRIBUTES)).contents
        assert security.lpSecurityDescriptor == 1234 and not security.bInheritHandle
        calls.append(('create', path)); return 1
    kernel = SimpleNamespace(CreateDirectoryW=NativeFunction(create), LocalFree=lambda pointer: calls.append(('free', pointer)))
    api = {'kernel32': kernel, 'advapi32': SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=convert)}
    monkeypatch.setattr(win, '_api', lambda: api)
    ram._create_adopted_boundary(Path(r'R:\CoChem427'), ['S-1-5-21-1006'])
    assert [kind for kind, _ in calls] == ['descriptor', 'create', 'free', 'ordinary', 'validate']
    def collision(*args):
        raise FileExistsError('synthetic collision')
    kernel.CreateDirectoryW = NativeFunction(collision)
    calls.clear()
    with pytest.raises(FileExistsError):
        ram._create_adopted_boundary(Path(r'R:\CoChem427'), ['S-1-5-21-1006'])
    assert [kind for kind, _ in calls] == ['descriptor', 'free']


@pytest.fixture
def adopted_fixture(tmp_path, monkeypatch):
    mount = tmp_path / 'volume'; mount.mkdir()
    other = mount / 'owner-data'; other.mkdir(); (other / 'keep.txt').write_bytes(b'owner bytes')
    private = tmp_path / 'private'; private.mkdir()
    config = ram.RamdiskConfig(enabled=True, workspace_subdirectory='CoChem427')
    # Test-only mounted path; no real volume/security/device API is called.
    object.__setattr__(config, 'mount_root', str(mount))
    monkeypatch.setattr(ram.RamdiskConfig, 'adopted_drive', property(lambda self: True))
    identities = {'slot1': win.WorkerIdentity('Worker1', 'fixture/1'), 'slot2': win.WorkerIdentity('Worker2', 'fixture/2')}
    writes = []; boundaries = {}
    monkeypatch.setattr(win, 'require_system', lambda: None)
    monkeypatch.setattr(win, 'validate_private_directory', lambda path: None)
    monkeypatch.setattr(win, 'validate_private_path', lambda path: None)
    monkeypatch.setattr(win, 'validate_code_path', lambda path: None)
    monkeypatch.setattr(win, '_account_sid', lambda name: name)
    monkeypatch.setattr(win, '_sid_text', lambda sid: sid)
    monkeypatch.setattr(win, 'current_boot_identity', lambda: 100)
    monkeypatch.setattr(win, '_close', lambda handle: None)
    monkeypatch.setattr(win, '_protect_boundary', lambda *args: pytest.fail('Must never rewrite adopted root/boundary ACL'))
    monkeypatch.setattr(win, '_validate_control_ancestors', lambda *args: pytest.fail('Must use adopted-root scoped control proof'))
    monkeypatch.setattr(win, '_set_acl', lambda path, sid: writes.append(('slot_acl', path)))
    monkeypatch.setattr(win, '_validate_worker_directory', lambda *args, **kw: None)
    monkeypatch.setattr(win, '_validate_boundary', lambda path, sids: boundaries[path])
    monkeypatch.setattr(win, 'defender_exclusions', lambda: {str(config.workspace_root / slot).casefold() for slot in identities})
    kernel = SimpleNamespace(CreateMutexW=lambda *args: 1, WaitForSingleObject=lambda *args: 0,
                             ReleaseMutex=NativeFunction(lambda *args: 1))
    monkeypatch.setattr(win, '_api', lambda: {'kernel32': kernel})
    monkeypatch.setattr(ram, '_validate_adopted_parent', lambda path: None)
    monkeypatch.setattr(ram, '_mount_target', lambda config: r'\Device\ImDisk7')
    observed = {'volume_serial': 1, 'device_number': 7, 'target': r'\Device\ImDisk7', 'size_bytes': 8589934592}
    monkeypatch.setattr(ram, '_native_volume', lambda config: observed)
    monkeypatch.setattr(ram, '_no_content_index', lambda path, **kw: writes.append(('index', path)))
    def create(path, sids):
        path.mkdir(); boundaries[path] = True; writes.append(('boundary_created', path))
    monkeypatch.setattr(ram, '_create_adopted_boundary', create)
    manager = ram.RamdiskManager(config, private, identities)
    return SimpleNamespace(manager=manager, config=config, mount=mount, other=other, private=private,
                           writes=writes, boundaries=boundaries, identities=identities, monkeypatch=monkeypatch)


def test_initial_adoption_only_mutates_new_subtree_and_private_ledger(adopted_fixture):
    f = adopted_fixture
    evidence = f.manager.ensure()
    assert evidence['lifecycle_action'] == 'ADOPT'
    assert evidence['volume_root_metadata_preserved'] is True
    assert evidence['workspace_root'] == str(f.config.workspace_root)
    assert (f.other / 'keep.txt').read_bytes() == b'owner bytes'
    assert all(path == f.config.workspace_root or f.config.workspace_root in path.parents for _, path in f.writes)
    assert set(f.manager.execution_roots.values()) == {f.config.workspace_root / name for name in f.identities}
    assert (f.private / 'ramdisk-state.json').is_file()
    f.writes.clear()
    assert f.manager.ensure()['lifecycle_action'] == 'VERIFY'
    assert not any(kind == 'boundary_created' for kind, _ in f.writes)


def test_existing_unowned_subtree_is_preserved_before_any_metadata_write(adopted_fixture):
    f = adopted_fixture
    f.config.workspace_root.mkdir(); target = f.config.workspace_root / 'keep'; target.write_bytes(b'preserve')
    with pytest.raises(ram.RamdiskError, match='no ownership ledger'):
        f.manager.ensure()
    assert f.writes == [] and target.read_bytes() == b'preserve'
    assert not (f.private / 'ramdisk-state.json').exists()


def test_untrusted_adopted_root_blocks_creation_before_writes(adopted_fixture):
    f = adopted_fixture
    def reject(path):
        raise ram.RamdiskError('untrusted parent replacement')
    f.monkeypatch.setattr(ram, '_validate_adopted_parent', reject)
    with pytest.raises(ram.RamdiskError, match='replacement'):
        f.manager.ensure()
    assert f.writes == [] and not f.config.workspace_root.exists()


def test_subtree_link_cannot_redirect_system_creation(adopted_fixture):
    f = adopted_fixture
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(f.other), str(f.config.workspace_root))
    else:
        f.config.workspace_root.symlink_to(f.other, target_is_directory=True)
    with pytest.raises(ram.RamdiskError, match='reparse'):
        f.manager.ensure()
    assert f.writes == [] and (f.other / 'keep.txt').read_bytes() == b'owner bytes'


def test_runtime_rechecks_scoped_boundary_and_inspect_uses_subtree_indexing(adopted_fixture):
    f = adopted_fixture
    f.manager.ensure(); f.writes.clear()
    checked = []
    f.monkeypatch.setattr(ram, '_validate_adopted_parent', lambda path: checked.append(('parent', path)))
    def acl(path):
        checked.append(('acl', path))
        assert path == f.config.workspace_root
        return (win.SYSTEM_SID, True, [(win.SYSTEM_SID, win.FULL_CONTROL, 3),
            (win.ADMIN_SID, win.FULL_CONTROL, 3), ('Worker1', 0x100020, 0), ('Worker2', 0x100020, 0)])
    f.monkeypatch.setattr(win, '_acl', acl)
    result = f.manager.inspect(require_capacity=False)
    assert result['inspection_only'] is True
    assert checked.count(('parent', f.mount)) == 2
    assert not any(path == f.mount for _, path in f.writes)
    assert ('index', f.config.workspace_root) in f.writes
    def unsafe(path):
        raise ram.RamdiskError('untrusted replacement after admission')
    f.monkeypatch.setattr(ram, '_validate_adopted_parent', unsafe)
    with pytest.raises(ram.RamdiskError, match='after admission'):
        f.manager.workspace('slot1').validate(identity='Worker1', require_capacity=False)
