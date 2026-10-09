"""Ordinary inert filesystem/native-read fixtures, never real RAM provisioning."""
import ast
import copy
import ctypes
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import io
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

HERE = Path(__file__).parent
SOURCE = HERE / 'commission-first-warden-r3-v5.py'
FROZEN = HERE / 'commission-first-warden-r3-v3.py'
spec = importlib.util.spec_from_file_location('commission_reboot_v5_fixture', SOURCE)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

# Load declarations only. Native/System paths are not invoked by these tests.
RAM_SOURCE = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\src\cochem_pipeline\ramdisk.py')
spec = importlib.util.spec_from_file_location('commission_reboot_ram_fixture', RAM_SOURCE)
RAM = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = RAM
spec.loader.exec_module(RAM)


def physical_receipt():
    rows = []
    for phase in ('red', 'green'):
        entries = {key[len(phase)+1:]: {'sha256': pin, 'size': length, 'executable': False}
                   for key, (pin, length) in M.FIXTURE_FILES.items() if key.startswith(phase+'/')}
        digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        rows.append({'phase': phase, 'source_sha256': digest, 'input_and_output_source_verified': True})
    return {'executions': rows}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    events = []
    root = tmp_path / 'protected-first-start'
    private = tmp_path / 'private'
    volume_root = tmp_path / 'volume'
    for path in (root, private, volume_root): path.mkdir()
    boundary = volume_root / 'CoChem427-windows-20261007'
    monkeypatch.setattr(M, 'ROOT', root)
    monkeypatch.setattr(M, 'QUEUE', private / 'queue-first-start')
    monkeypatch.setattr(M, 'PRESERVATION_ROOT', private / 'same-boot-retention')
    monkeypatch.setattr(M, 'RAM_WORKSPACE_ROOT', boundary)
    canonical = RAM.RamdiskConfig(enabled=True, lifecycle='adopt_existing', workspace_subdirectory='CoChem427-windows-20261007')
    policy = SimpleNamespace(**canonical.as_dict(), adopted_drive=True, workspace_root=boundary,
                             as_dict=canonical.as_dict)
    workers = {f'slot{i}': {'name': f'fixture-worker{i}', 'credential_target': f'fixture/{i}'} for i in range(1, 7)}
    slots = {slot: tmp_path / 'ssd' / slot for slot in workers}
    for path in slots.values(): path.mkdir(parents=True)
    config = SimpleNamespace(ramdisk=policy, private_root=private, workers=workers, slot_roots=slots,
                             token_file=private/'token-placeholder', operator_name='fixture-owner', port=12345)
    old_volume = {'volume_serial': 3400714210, 'device_number': 0, 'size_bytes': 8589934592,
                  'filesystem': 'NTFS', 'target': r'\Device\ImDisk0', 'backing': 'vm'}
    current_volume = {**old_volume, 'volume_serial': 3256441993}
    prior = {'schema': 1, 'state': 'READY', 'config': policy.as_dict(), 'mount_root': str(Path(policy.mount_root)),
             'workspace_root': str(boundary), 'slots': sorted(workers), 'adopted_existing_drive': True,
             'volume_root_metadata_preserved': True, 'boot_id': 100, 'observed': old_volume,
             'backup': None, 'lifecycle_action': 'ADOPT', 'checked_at': 123.5}
    # Preserve whitespace/newline differences to verify byte-exact retention.
    raw = (json.dumps(prior, indent=3) + '\r\n').encode()
    ledger = private/'ramdisk-state.json'
    ledger.write_bytes(raw)
    before = {'task_xml_sha256': M.RAM_TASK_SHA, 'root_security_sha256': 'a'*64,
              'root_file_id': 5, 'root_device': 7, 'root_attributes': 22, 'filesystem_bytes': 8589930496,
              'volume': current_volume, 'imdisk_query_ioctl_used': True, 'ram_modified': False}
    old_before = {**before, 'volume': old_volume, 'root_device': 1}
    foundation = {'ram_ledger': copy.deepcopy(prior), 'ram_ledger_sha256': hashlib.sha256(raw).hexdigest(),
                  'ram_before': old_before, 'ram_after': copy.deepcopy(old_before),
                  'registry': {'owner_sha256': 'b'*64}}
    state = SimpleNamespace(boot=200, before=before, ensured=None, ensure_calls=0, inspect_calls=0,
                            ensure_failure=None, inspect_failure=None, root_extra=False, registry_drift=False,
                            task_failure=False, port_failure=False, extra_exclusion=False,
                            observed_after_change=False, ensured_change=None, capacity_failure=False)
    exclusions = {str(boundary/slot).casefold() for slot in workers}

    # Actual Windows CreateFile on fixture paths only. This exercises the
    # no-write/no-delete sharing and, critically, closure before os.replace.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    native_create = kernel.CreateFileW
    native_create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    native_create.restype = ctypes.c_void_p
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    def create(path, *args):
        assert Path(path).is_relative_to(tmp_path)
        assert args[:2] == (0x80000000, 1)
        events.append('ledger-read-handle')
        return native_create(path, *args)

    def guarded(path):
        assert Path(path).is_relative_to(tmp_path)
        RAM.ordinary_tree(Path(path))

    def layout(*args, **kwargs):
        events.append('layout')
        return {'slots': workers}

    def token(*args): events.append('token')

    def defender():
        events.append('defender')
        return exclusions | ({'unexpected-fixture-root'} if state.extra_exclusion and state.ensure_calls else set())

    win = SimpleNamespace(WorkerIdentity=lambda **spec: SimpleNamespace(**spec), validate_layout=layout,
                          validate_controller_token=token, validate_private_path=guarded, validate_code_path=guarded,
                          current_boot_identity=lambda: state.boot, defender_exclusions=defender,
                          _api=lambda: {'kernel32': SimpleNamespace(CreateFileW=create)},
                          _close=lambda handle: close_handle(handle) if handle else None)

    class FakeManager:
        def __init__(self, cfg, private_root, identities):
            assert cfg is policy and private_root == private and set(identities) == set(workers)

        def ensure(self):
            events.append('ensure')
            state.ensure_calls += 1
            assert state.ensure_calls == 1
            assert (root/M.RAM_RECOVERY_INTENT).is_file()
            assert (root/M.RAM_RECOVERY_BACKUP).read_bytes() == raw
            assert (root/M.RAM_RECOVERY_MANIFEST).is_file()
            if state.ensure_failure: raise state.ensure_failure
            boundary.mkdir()
            for slot in workers: (boundary/slot).mkdir()
            if state.root_extra: (boundary/'unexpected').write_bytes(b'preserve')
            ensured = {**prior, 'boot_id': state.boot, 'observed': copy.deepcopy(state.before['volume'])}
            if state.ensured_change: ensured.update(state.ensured_change)
            replacement = private/'replacement-ledger'
            replacement.write_text(json.dumps(ensured), encoding='utf-8')
            # A retained no-delete-share read handle makes this fail on Windows.
            os.replace(replacement, ledger)
            events.append('ledger-replaced')
            state.ensured = ensured
            return ensured

        def inspect(self, *, require_capacity):
            events.append('inspect')
            state.inspect_calls += 1
            assert require_capacity is True
            if state.inspect_failure: raise state.inspect_failure
            if state.ensure_calls == 0 and state.before['volume'] != old_volume:
                raise ValueError('FIXTURE_SAME_BOOT_REPLACED_VOLUME')
            return copy.deepcopy(state.ensured or prior)

        def workspace(self, slot): return SimpleNamespace(root=boundary/slot)

    package = ModuleType('cochem_pipeline')
    ram_module = ModuleType('cochem_pipeline.ramdisk')
    ram_module.RamdiskManager = FakeManager
    ram_module.ordinary_tree = RAM.ordinary_tree
    ram_module.mount_recovery_action = RAM.mount_recovery_action
    package.ramdisk = ram_module
    monkeypatch.setitem(sys.modules, 'cochem_pipeline', package)
    monkeypatch.setitem(sys.modules, 'cochem_pipeline.ramdisk', ram_module)

    @contextmanager
    def lock(win_arg, mount):
        assert win_arg is win and mount == policy.mount_root
        events.append('ram-lock-enter')
        try: yield
        finally: events.append('ram-lock-exit')

    def observation(*args):
        events.append('ram-observation')
        if state.observed_after_change and state.ensure_calls:
            return {**state.before, 'root_security_sha256': 'f'*64}
        return copy.deepcopy(state.before)

    registry_result = {'sha256': 'c'*64, 'retained_removed_rows': 2, 'capacity': 4, 'unknown_owned': 0}

    def registry(*args):
        events.append('registry')
        return {**registry_result, 'sha256': 'd'*64} if state.registry_drift and state.ensure_calls else dict(registry_result)

    def task(win_arg, mode):
        events.append('task-'+mode)
        assert mode == 'stopped'
        if state.task_failure: raise ValueError('FIXTURE_STOPPED_TASK_FAILURE')
        return {'disabled': True, 'instances': 0}

    def port(port_arg):
        events.append('port')
        assert port_arg == config.port
        if state.port_failure: raise OSError('fixture-port-busy')

    monkeypatch.setattr(M, 'production_ram_lock', lock)
    monkeypatch.setattr(M, 'ram_observation', observation)
    monkeypatch.setattr(M, 'registry_preflight', registry)
    monkeypatch.setattr(M, 'task_control', task)
    monkeypatch.setattr(M, 'assert_loopback_available', port)
    def capacity(config_arg):
        assert config_arg is config
        events.append('capacity')
        if state.capacity_failure: raise ValueError('RAM_RECOVERY_CAPACITY')
    monkeypatch.setattr(M, 'assert_ram_capacity', capacity)
    real_write = M.write_new

    def write(path, value):
        events.append('write-'+path.name)
        return real_write(path, value)

    monkeypatch.setattr(M, 'write_new', write)
    return SimpleNamespace(tmp=tmp_path, root=root, private=private, boundary=boundary, config=config,
                           win=win, state=state, events=events, prior=prior, raw=raw, ledger=ledger,
                           before=before, foundation=foundation, physical=physical_receipt(),
                           manager_type=FakeManager, exclusions=exclusions)


def execute(f): return M.preflight_state(f.config, f.win, f.foundation, f.physical)


def assert_unmutated(f):
    assert f.state.ensure_calls == 0
    assert list(f.root.iterdir()) == []
    assert f.ledger.read_bytes() == f.raw


def test_verified_reboot_fences_captures_and_adopts_once_after_all_guards(fixture):
    f = fixture
    result = execute(f)
    assert f.state.ensure_calls == 1 and f.state.inspect_calls == 1
    assert f.events.count('registry') == 2 and f.events.count('port') == 2 and f.events.count('task-stopped') == 2
    first_write = f.events.index('write-'+M.RAM_RECOVERY_INTENT)
    for guard in ('layout', 'token', 'registry', 'port', 'task-stopped', 'capacity'):
        assert f.events.index(guard) < first_write
    assert f.events.index('ram-lock-enter') < first_write < f.events.index('ensure') < f.events.index('ram-lock-exit')
    assert f.events.index('write-'+M.RAM_RECOVERY_MANIFEST) < f.events.index('ensure')
    assert 'ledger-replaced' in f.events
    assert (f.root/M.RAM_RECOVERY_BACKUP).read_bytes() == f.raw
    intent = json.loads((f.root/M.RAM_RECOVERY_INTENT).read_bytes())
    assert intent['prior_boot_id'] == 100 and intent['current_boot_id'] == 200
    assert intent['prior_ledger_sha256'] == hashlib.sha256(f.raw).hexdigest()
    recovery = result['verified_reboot_ram_recovery']
    assert recovery['ram_ensure_calls'] == 1 and recovery['ram_lifecycle_action'] == 'ADOPT'
    assert recovery['volatile_fixture_unavailable_after_verified_reboot'] is True
    assert recovery['docker_retested'] is False and recovery['current_boot_physical_test_verified'] is False
    assert recovery['drive_created_formatted_or_resized'] is False
    assert recovery['ledger_backup_unchanged'] is True and result['ram_inspection_only'] is False
    assert len(result['empty_scratch_roots']) == 12
    assert not (f.boundary/'slot1'/M.FIXTURE_NAME).exists()
    assert all(not list((f.boundary/slot).iterdir()) for slot in f.config.workers)


@pytest.mark.parametrize('mutation', [
    'missing-ledger', 'ledger-shape', 'ledger-preparing', 'ledger-config', 'ledger-slots',
    'foundation-hash', 'foundation-ledger', 'foundation-volume', 'foundation-root-baseline',
    'boot-unknown', 'boot-earlier', 'boot-boolean', 'same-volume', 'ordinary-volume', 'wrong-size',
    'wrong-filesystem', 'startup-task', 'partial-subtree', 'intent-exists', 'backup-exists', 'manifest-exists',
    'dirty-ssd', 'primary-exists', 'queue-exists', 'invalid-physical-manifest', 'task-not-stopped', 'port-busy', 'capacity'])
def test_unproved_reboot_or_earlier_guard_never_mutates(fixture, mutation):
    f = fixture
    original_ledger = f.raw
    preexisting = None
    if mutation == 'missing-ledger': f.ledger.unlink()
    elif mutation in {'ledger-shape', 'ledger-preparing', 'ledger-config', 'ledger-slots'}:
        prior = copy.deepcopy(f.prior)
        if mutation == 'ledger-shape': prior['schema'] = True
        elif mutation == 'ledger-preparing': prior['state'] = 'PREPARING'
        elif mutation == 'ledger-config': prior['config']['size_mb'] = 4096
        else: prior['slots'] = ['slot1']
        f.ledger.write_text(json.dumps(prior), encoding='utf-8')
        original_ledger = f.ledger.read_bytes()
        f.foundation['ram_ledger'] = prior
        f.foundation['ram_ledger_sha256'] = hashlib.sha256(original_ledger).hexdigest()
    elif mutation == 'foundation-hash': f.foundation['ram_ledger_sha256'] = 'f'*64
    elif mutation == 'foundation-ledger': f.foundation['ram_ledger']['checked_at'] = 999
    elif mutation == 'foundation-volume': f.foundation['ram_before']['volume'] = {'unknown': True}
    elif mutation == 'foundation-root-baseline': f.foundation['ram_after']['root_attributes'] = 999
    elif mutation == 'boot-unknown': f.state.boot = 0
    elif mutation == 'boot-earlier': f.state.boot = 99
    elif mutation == 'boot-boolean': f.state.boot = True
    elif mutation == 'same-volume': f.state.before['volume'] = copy.deepcopy(f.prior['observed'])
    elif mutation == 'ordinary-volume': f.state.before['volume']['target'] = r'\Device\HarddiskVolume8'
    elif mutation == 'wrong-size': f.state.before['volume']['size_bytes'] = 4294967296
    elif mutation == 'wrong-filesystem': f.state.before['volume']['filesystem'] = 'FAT32'
    elif mutation == 'startup-task': f.state.before['task_xml_sha256'] = 'f'*64
    elif mutation == 'partial-subtree':
        f.boundary.mkdir();preexisting = f.boundary/'keep';preexisting.write_bytes(b'preserve')
    elif mutation.endswith('-exists') and mutation.split('-')[0] in {'intent', 'backup', 'manifest'}:
        name = {'intent-exists': M.RAM_RECOVERY_INTENT, 'backup-exists': M.RAM_RECOVERY_BACKUP,
                'manifest-exists': M.RAM_RECOVERY_MANIFEST}[mutation]
        preexisting = f.root/name;preexisting.write_bytes(b'preserve')
    elif mutation == 'dirty-ssd':
        preexisting = next(iter(f.config.slot_roots.values()))/'.keep';preexisting.write_bytes(b'preserve')
    elif mutation == 'primary-exists': preexisting = f.private/'job_board.db';preexisting.write_bytes(b'preserve')
    elif mutation == 'queue-exists': M.QUEUE.mkdir();preexisting = M.QUEUE/'keep';preexisting.write_bytes(b'preserve')
    elif mutation == 'invalid-physical-manifest': f.physical['executions'][0]['source_sha256'] = 'f'*64
    elif mutation == 'task-not-stopped': f.state.task_failure = True
    elif mutation == 'port-busy': f.state.port_failure = True
    elif mutation == 'capacity': f.state.capacity_failure = True
    with pytest.raises((ValueError, OSError, RAM.RamdiskError)): execute(f)
    assert f.state.ensure_calls == 0
    assert not any(event.startswith('write-') for event in f.events)
    assert not any(event == 'task-start' for event in f.events)
    if mutation != 'missing-ledger': assert f.ledger.read_bytes() == original_ledger
    if preexisting: assert preexisting.read_bytes() == b'preserve'


def test_same_boot_missing_subtree_and_replaced_volume_never_adopts(fixture):
    f = fixture
    f.state.boot = 100
    with pytest.raises(ValueError, match='FIXTURE_SAME_BOOT_REPLACED_VOLUME'): execute(f)
    assert_unmutated(f)
    f.state.before['volume'] = copy.deepcopy(f.prior['observed'])
    with pytest.raises(RAM.RamdiskError): execute(f)
    assert_unmutated(f)


def test_same_boot_keeps_original_fixture_preservation_path(fixture, monkeypatch):
    f = fixture
    f.state.boot = 100
    f.state.before['volume'] = copy.deepcopy(f.prior['observed'])
    f.boundary.mkdir()
    for slot in f.config.workers: (f.boundary/slot).mkdir()
    known = f.boundary/'slot1'/M.FIXTURE_NAME
    known.mkdir()
    calls = []
    monkeypatch.setattr(M, 'fixture_shape', lambda path, ordinary: calls.append(('shape', path)))

    def preserve(source, destination, physical, win, ordinary):
        assert source == known and destination == M.PRESERVATION_ROOT and physical is f.physical and win is f.win
        calls.append(('preserve', source))
        assert f.events.count('registry') == 1 and f.events.count('port') == 1 and f.events.count('task-stopped') == 1
        return {'files': 4, 'bytes': 3392, 'source_deleted_by_helper': False}

    monkeypatch.setattr(M, 'preserve_fixture', preserve)
    result = execute(f)
    assert calls == [('shape', known), ('preserve', known)]
    assert result['ram_inspection_only'] is True and 'verified_reboot_ram_recovery' not in result
    assert len(result['empty_scratch_roots']) == 11
    assert f.state.ensure_calls == 0 and f.ledger.read_bytes() == f.raw
    assert not list(f.root.iterdir())


@pytest.mark.parametrize('phase', ['intent', 'backup', 'manifest', 'ensure', 'inspect', 'post-ledger',
                                  'root-contents', 'exclusions', 'root-task-drift', 'registry-drift', 'dirty-ram'])
def test_recovery_failures_are_fenced_preserved_and_never_start(fixture, monkeypatch, phase):
    f = fixture
    real_write = M.write_new
    if phase in {'intent', 'manifest'}:
        failure_name = M.RAM_RECOVERY_INTENT if phase == 'intent' else M.RAM_RECOVERY_MANIFEST

        def fail_write(path, value):
            if path.name == failure_name:
                path.write_bytes(b'partial-preserve')
                raise OSError('fixture-persist-failure')
            return real_write(path, value)

        monkeypatch.setattr(M, 'write_new', fail_write)
    elif phase == 'backup':
        original_open = Path.open

        def fail_open(path, *args, **kwargs):
            if path.name == M.RAM_RECOVERY_BACKUP and args and args[0] == 'xb':
                path.write_bytes(b'partial-preserve')
                raise OSError('fixture-backup-failure')
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', fail_open)
    elif phase == 'ensure': f.state.ensure_failure = RuntimeError('fixture-ambiguous-ensure')
    elif phase == 'inspect': f.state.inspect_failure = ValueError('fixture-inspect-refusal')
    elif phase == 'post-ledger': f.state.ensured_change = {'lifecycle_action': 'VERIFY'}
    elif phase == 'root-contents': f.state.root_extra = True
    elif phase == 'exclusions': f.state.extra_exclusion = True
    elif phase == 'root-task-drift': f.state.observed_after_change = True
    elif phase == 'registry-drift': f.state.registry_drift = True
    elif phase == 'dirty-ram':
        real_empty = M.empty_directory

        def dirty(path, ordinary):
            if path == f.boundary/'slot4': (path/'keep').write_bytes(b'preserve')
            return real_empty(path, ordinary)

        monkeypatch.setattr(M, 'empty_directory', dirty)
    with pytest.raises((ValueError, OSError, RuntimeError)): execute(f)
    assert (f.root/M.RAM_RECOVERY_INTENT).is_file()
    expected_calls = int(phase not in {'intent', 'backup', 'manifest'})
    assert f.state.ensure_calls == expected_calls
    assert all(event != 'task-start' for event in f.events)
    files_before = {path: path.read_bytes() for path in f.root.iterdir()}
    with pytest.raises((ValueError, OSError, RuntimeError)): execute(f)
    assert f.state.ensure_calls == expected_calls
    assert {path: path.read_bytes() for path in f.root.iterdir()} == files_before
    if phase != 'backup' and (f.root/M.RAM_RECOVERY_BACKUP).exists():
        assert (f.root/M.RAM_RECOVERY_BACKUP).read_bytes() == f.raw


def test_backup_ledger_is_byte_exact_and_original_read_handle_closed_before_replace(fixture):
    f = fixture
    execute(f)
    assert f.ledger.read_bytes() != f.raw
    assert (f.root/M.RAM_RECOVERY_BACKUP).read_bytes() == f.raw
    assert f.events.count('ledger-read-handle') == 3
    assert f.events.index('ledger-replaced') > f.events.index('write-'+M.RAM_RECOVERY_MANIFEST)


def test_read_ledger_refuses_duplicate_json_and_synthetic_multiple_link_metadata(fixture, monkeypatch):
    f = fixture
    f.ledger.write_bytes(b'{"boot_id":100,"boot_id":200}')
    with pytest.raises(ValueError): M.read_ram_ledger(f.win, f.ledger)
    f.ledger.write_bytes(f.raw)
    actual_stat = Path.stat
    class LinkedStat:
        st_nlink = 2
        def __init__(self, value): self.value = value
        def __getattr__(self, name): return getattr(self.value, name)
    monkeypatch.setattr(Path, 'stat', lambda path, *args, **kwargs:
                        LinkedStat(actual_stat(path, *args, **kwargs)) if path == f.ledger else actual_stat(path, *args, **kwargs))
    with pytest.raises(ValueError, match='RAM_LEDGER_BOUNDS'): M.read_ram_ledger(f.win, f.ledger)


@pytest.mark.parametrize('raw', [b'', b' '*1048577, b'[]'], ids=['empty', 'oversized', 'array'])
def test_read_ledger_requires_bounded_nonempty_object(fixture, raw):
    f = fixture
    f.ledger.write_bytes(raw)
    with pytest.raises(ValueError): M.read_ram_ledger(f.win, f.ledger)


def test_all_unrelated_functions_are_exact_frozen_source_and_new_support_is_reviewed_copy():
    frozen_raw = FROZEN.read_bytes()
    assert hashlib.sha256(frozen_raw).hexdigest() == '9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755'
    old_text, new_text = frozen_raw.decode(), SOURCE.read_text(encoding='utf-8')
    old_functions = {node.name: ast.get_source_segment(old_text, node) for node in ast.parse(old_text).body if isinstance(node, ast.FunctionDef)}
    new_functions = {node.name: ast.get_source_segment(new_text, node) for node in ast.parse(new_text).body if isinstance(node, ast.FunctionDef)}
    for name, source in old_functions.items():
        if name not in {'preflight_state', 'run'}: assert new_functions[name] == source, name
    original_run = old_functions['run']
    expected_run = original_run.replace("        code=getattr(error,'observation_code',None)",
        "        if type(error) is ValueError and str(error) in RAM_RECOVERY_ERROR_CODES:report['failure']['code']=str(error)\n        code=getattr(error,'observation_code',None)")
    assert new_functions['run'] == expected_run
    prerequisite = (HERE/'inspect-execution-prerequisites-r3.py').read_bytes()
    assert hashlib.sha256(prerequisite).hexdigest() == '17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
    reviewed = {node.name: ast.get_source_segment(prerequisite.decode(), node) for node in ast.parse(prerequisite).body if isinstance(node, ast.FunctionDef)}
    for name in ('strict_json', 'sha', 'ram_observation'): assert new_functions[name] == reviewed[name]
    for name in ('validate_auth', 'start_once', 'task_control', 'wait_control_plane', 'health_sample', 'process_proof', 'preserve_fixture'):
        assert new_functions[name] == old_functions[name]


def test_new_recovery_has_no_docker_provider_task_or_volume_creation_calls():
    text = SOURCE.read_text(encoding='utf-8')
    tree = ast.parse(text)
    recovery_functions = {'ram_recovery_plan', 'recover_ram_once', 'read_ram_ledger', 'ram_recovery_outputs_absent'}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in recovery_functions:
            source = ast.get_source_segment(text, node)
            for forbidden in ('subprocess', 'DockerRunner', 'imdisk_create_argv', '_remove_stale_mount', 'launch_worker',
                              'task_control(', '.unlink(', 'rmtree', 'preserve_fixture('):
                assert forbidden not in source
    recovery = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'recover_ram_once')
    ensure = [node for node in ast.walk(recovery) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'ensure']
    assert len(ensure) == 1
    assert not any(isinstance(node, (ast.For, ast.While)) and ensure[0] in list(ast.walk(node)) for node in ast.walk(recovery))


def test_failure_diagnostics_only_allow_explicit_uppercase_codes():
    assert M.RAM_RECOVERY_ERROR_CODES
    assert all(isinstance(code, str) and code.isupper() and all(character.isalpha() or character=='_' for character in code)
               for code in M.RAM_RECOVERY_ERROR_CODES)
    assert 'credentials: secret' not in M.RAM_RECOVERY_ERROR_CODES
    assert 'RAM_RECOVERY_BOOT\nsecret' not in M.RAM_RECOVERY_ERROR_CODES


def test_same_boot_fixture_is_still_mandatory(fixture, monkeypatch):
    f = fixture
    f.state.boot = 100
    f.state.before['volume'] = copy.deepcopy(f.prior['observed'])
    f.boundary.mkdir()
    for slot in f.config.workers: (f.boundary/slot).mkdir()
    monkeypatch.setattr(M, 'preserve_fixture', lambda *args: pytest.fail('Missing physical fixture was accepted'))
    with pytest.raises(ValueError, match='SLOT1_EXPECTED_PHYSICAL_FIXTURE'): execute(f)
    assert_unmutated(f)


def test_access_error_is_not_mistaken_for_absent_volatile_subtree(fixture, monkeypatch):
    f = fixture
    actual = Path.lstat
    def refused(path, *args, **kwargs):
        if path == f.boundary: raise PermissionError('fixture-access-denied')
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', refused)
    with pytest.raises(PermissionError): execute(f)
    assert_unmutated(f)


@pytest.mark.parametrize('free_mb,allowed', [(511, False), (512, True), (1024, True)])
def test_capacity_guard_checks_reserve_before_recovery(free_mb, allowed, monkeypatch):
    config = SimpleNamespace(ramdisk=SimpleNamespace(mount_root='fixture-mount', min_free_mb=512))
    def usage(path):
        assert path == 'fixture-mount'
        return SimpleNamespace(free=free_mb*1024*1024)
    monkeypatch.setattr(M.shutil, 'disk_usage', usage)
    if allowed: M.assert_ram_capacity(config)
    else:
        with pytest.raises(ValueError, match='RAM_RECOVERY_CAPACITY'): M.assert_ram_capacity(config)


@pytest.mark.parametrize('safe_code', [True, False])
def test_full_inert_run_cannot_start_after_preflight_failure_and_redacts_diagnostic(fixture, monkeypatch, safe_code):
    f = fixture
    # Inert support and module facades never run real System/runtime/provider
    # code. The actual production run() controls error handling and start order.
    entry = f.root/'commission-first-warden-r3-v1.py'
    entry.write_bytes(b'# fixture entrypoint, not executed\n')
    monkeypatch.setattr(M, '__file__', str(entry))
    monkeypatch.setattr(M, 'PYTHON', Path(sys.executable).resolve())
    f.win.require_system = lambda: f.events.append('inert-system-check')
    package = sys.modules['cochem_pipeline']
    package.windows = f.win
    config_module = ModuleType('cochem_pipeline.config')
    config_module.load_config = lambda path: f.config
    monkeypatch.setitem(sys.modules, 'cochem_pipeline.windows', f.win)
    monkeypatch.setitem(sys.modules, 'cochem_pipeline.config', config_module)
    support = SimpleNamespace(verify_r3_runtime=lambda *args: f.events.append('inert-runtime-custody'),
                              digest_file=lambda path: hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(M, 'load_support', lambda path: support)
    packet = {'schema': 'cochem-warden-commissioning-inputs/1', 'nonce': 'a'*32,
              'config_sha256': M.CONFIG_HASH, 'source_manifest_sha256': M.MANIFEST_HASH,
              'auth_attempt': 'c'*32, 'auth_receipt_sha256': 'd'*64}
    def controls(win, support_arg, path, pin=None):
        if path == f.root/'inputs.json': return packet, 'e'*64
        if path == f.root/'pipeline.json': return {}, M.CONFIG_HASH
        for name, (prior_path, prior_pin) in M.PRIORS.items():
            if path == prior_path:
                return {'foundation': f.foundation, 'docker': f.physical}.get(name, {}), prior_pin
        raise AssertionError('Unexpected fixture control path')
    monkeypatch.setattr(M, 'read_control', controls)
    monkeypatch.setattr(M, 'validate_auth', lambda read, attempt: {'receipt_sha256': 'd'*64, 'profiles': [{}]*12})
    @contextmanager
    def reserve(*args):
        f.events.append('inert-worker-reservation')
        yield
    monkeypatch.setattr(M, 'reserve_workers', reserve)
    if safe_code: f.state.capacity_failure = True
    else:
        def fail(*args): raise ValueError('credentials: fixture-secret-not-for-receipt')
        monkeypatch.setattr(M, 'preflight_state', fail)
    assert M.run('a'*32, 'e'*64) == 2
    report_raw = (f.root/'commissioning.json').read_bytes()
    report = json.loads(report_raw)
    assert report['status'] == 'WARDEN_COMMISSIONING_HELD'
    assert report['failure']['phase'] == 'state_preflight'
    assert report['exactly_one_start_requested'] is False and report['automatic_retry_allowed'] is False
    assert not (f.root/'start-intent.json').exists() and f.state.ensure_calls == 0
    assert 'task-start' not in f.events
    assert b'fixture-secret' not in report_raw
    assert report['failure'].get('code') == ('RAM_RECOVERY_CAPACITY' if safe_code else None)


def test_generator_reproduces_final_bytes_without_writing(monkeypatch):
    original = Path.open
    captures = []
    @contextmanager
    def capture():
        with io.StringIO() as stream:
            yield stream
            captures.append(stream.getvalue().encode('utf-8'))
    def opened(path, *args, **kwargs):
        if path == SOURCE and args and args[0] == 'x': return capture()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', opened)
    runpy.run_path(str(HERE/'prepare-first-start-reboot-r3-v5.py'), run_name='fixture_generator_no_writes')
    assert captures == [SOURCE.read_bytes()]
