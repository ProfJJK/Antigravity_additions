"""Ordinary Windows and disposable clocks only; not SYSTEM/48-hour evidence."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).parent / 'resource-observer-r3-v1'
PACKAGE = ROOT / 'cochem_supervisor'
name = '_observer_fixture'
package = types.ModuleType(name)
package.__path__ = [str(PACKAGE)]
sys.modules[name] = package
M = importlib.import_module(name + '.resource_observation')
W = M.win
P = M.performance
entry_spec = importlib.util.spec_from_file_location('_observer_entry_fixture', ROOT / 'observe_resources.py')
E = importlib.util.module_from_spec(entry_spec)
entry_spec.loader.exec_module(E)


def proof():
    return {'pid': 1234, 'creation_filetime': 133000000000000000, 'first_sequence': 1,
            'final_sequence': 2, 'instance_id': 'a' * 32, 'token_sid': W.SYSTEM,
            'launcher_arguments_verified': True, 'launcher': {'token_sid': W.SYSTEM}}


def startup():
    return dict(M.EXPECTED, controller=proof())


def test_exact_repaired_performance_and_import_boundary_source():
    pins = {
        PACKAGE / 'performance_acceptance.py': '8164ca69e6f31ad1e1912d1396df086b2590c103fd3c9a64d7a6031872c56e4c',
        PACKAGE / 'shared_io.py': '4bbf84d5f55a2656e2f3e6e72f85a9564a404330e15bea3977648b0930805fb0',
        ROOT / 'detector_bootstrap.py': '227f4b1ecd3f88b45b53cc53f99f29e359350fb10b7f653314f12043f100b3b5',
    }
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == pin for path, pin in pins.items())


def test_actual_isolated_no_site_package_import_and_forbidden_pipeline():
    psutil = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\psutil')
    script = '''import sys,json
from pathlib import Path
namespace={'__name__':'_bootstrap_definitions'}
exec(compile(Path(sys.argv[1]).read_bytes(),sys.argv[1],'exec'),namespace)
namespace['configure_packages'](Path(sys.argv[2]),Path(sys.argv[3]))
from cochem_supervisor import resource_observation
rejected=[]
for name in ('cochem_pipeline','cochem_mcp','requests'):
 try: __import__(name)
 except ImportError: rejected.append(name)
print(json.dumps({'rejected':rejected,'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,
 'multiprocessing_alias_is_main':sys.modules.get('__mp_main__') is sys.modules['__main__'],
 'roots':sorted(set(x.split('.')[0] for x in sys.modules)-set(sys.stdlib_module_names)-{'__main__'})}))
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(ROOT / 'detector_bootstrap.py'),
                             str(PACKAGE), str(psutil)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['rejected'] == ['cochem_pipeline', 'cochem_mcp', 'requests']
    assert value['roots'] == ['__mp_main__', 'cochem_supervisor', 'psutil']
    assert value['multiprocessing_alias_is_main'] is True
    assert value['isolated'] == value['no_site'] == 1


def test_actual_native_query_handle_creation_cpu_and_close():
    with W.ProcessHandle(os.getpid()) as handle:
        first = handle.snapshot()
        second = handle.snapshot()
        assert first['pid'] == os.getpid()
        assert first['creation_filetime'] == second['creation_filetime'] > 116444736000000000
        assert second['user_cpu_100ns'] >= first['user_cpu_100ns'] >= 0
        assert second['kernel_cpu_100ns'] >= first['kernel_cpu_100ns'] >= 0
        assert first['handles'] > 0 and Path(first['image']).is_file()
    assert handle.handle is None


def test_actual_native_handle_detects_exit_without_pid_reopen():
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import sys;sys.stdin.readline()'],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with W.ProcessHandle(child.pid) as handle:
            assert handle.snapshot()['pid'] == child.pid
            child.communicate(b'finish\n', timeout=10)
            with pytest.raises(W.BoundaryError, match='PROCESS_EXITED'):
                handle.snapshot()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_actual_ordinary_token_is_not_system():
    assert W.process_sid(W.api()[0].GetCurrentProcess()) != W.SYSTEM
    with pytest.raises(W.BoundaryError, match='SYSTEM_REQUIRED'):
        W.require_system()


def test_native_acl_reader_inspects_ordinary_metadata_without_privilege(tmp_path):
    path = tmp_path / 'metadata-only.txt'
    path.write_text('disposable')
    owner, rules = W.acl(path)
    assert owner == W.process_sid(W.api()[0].GetCurrentProcess())
    assert rules and all(type(mask) is int and type(flags) is int for sid, mask, flags in rules)
    with pytest.raises(W.BoundaryError, match='PRIVATE_OWNER'):
        W.private_rules(owner, rules)


def test_entry_actual_preview_is_inert_and_explicitly_unreleased():
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'observe_resources.py')],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['prepared_only'] is True and value['full_srs_acceptance'] is False
    assert value['duration_seconds'] == 172800 and len(value['holds']) == 4
    assert value['no_database_opens'] and value['no_controller_calls']


def test_entry_cannot_run_before_installation_contract_release(monkeypatch, capsys):
    assert E.INSTALLATION_CONTRACT_RELEASED is False
    monkeypatch.setattr(sys, 'argv', ['observe_resources.py', '--run-observer'])
    monkeypatch.setattr(E, 'prepare_imports', lambda *args: pytest.fail('Inactive entry imported native observer'))
    assert E.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result['schema'] == 'cochem-external-resource-observer-bootstrap-failure/1'
    assert result['full_srs_acceptance'] is False


@pytest.fixture
def manifest_tree(tmp_path):
    paths = ['observe_resources.py', 'detector_bootstrap.py', 'cochem_supervisor/__init__.py',
             'cochem_supervisor/windows.py', 'cochem_supervisor/resource_observation.py',
             'cochem_supervisor/performance_acceptance.py', 'cochem_supervisor/shared_io.py']
    rows = []
    for name in paths:
        source = ROOT / name
        destination = tmp_path / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        raw = source.read_bytes()
        rows.append({'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    value = {'schema': 'cochem-external-observer-source-manifest/1', 'files': rows}
    raw = json.dumps(value).encode()
    (tmp_path / 'source-manifest.json').write_bytes(raw)
    return tmp_path, raw


def test_complete_fixed_source_manifest_is_verified(manifest_tree):
    root, raw = manifest_tree
    result = E.verify_sources(root, raw)
    assert len(result['files']) == 7


@pytest.mark.parametrize('change', ['source', 'extra-file', 'extra-directory', 'hardlink', 'row-outside'])
def test_manifest_refuses_source_drift_alias_extra_or_external(manifest_tree, change):
    root, raw = manifest_tree
    if change == 'source': (root / 'observe_resources.py').write_bytes(b'changed')
    elif change == 'extra-file': (root / 'unexpected.py').write_bytes(b'bad')
    elif change == 'extra-directory': (root / 'unexpected').mkdir()
    elif change == 'hardlink': os.link(root / 'observe_resources.py', root / 'alias.py')
    else:
        value = json.loads(raw)
        value['files'][0]['path'] = '../outside.py'
        raw = json.dumps(value).encode()
    with pytest.raises(RuntimeError):
        E.verify_sources(root, raw)


@pytest.mark.parametrize('owner,rules', [
    ('S-1-5-21-1', [(W.SYSTEM, W.FULL, 0)]),
    (W.SYSTEM, [(W.SYSTEM, W.FULL, 8)]),
    (W.SYSTEM, [(W.SYSTEM, W.FULL, 0), ('S-1-1-0', 1, 0)]),
    (W.SYSTEM, [(W.ADMIN, W.FULL, 0)]),
])
def test_private_policy_refuses_inherited_only_or_external_grants(owner, rules):
    with pytest.raises(W.BoundaryError):
        W.private_rules(owner, rules)


def test_inherited_effective_private_policy_is_accepted():
    W.private_rules(W.SYSTEM, [(W.SYSTEM, W.FULL, 16), (W.ADMIN, W.FULL, 16)])


@pytest.fixture
def ordinary_reads(monkeypatch):
    # Explicit ordinary fixture boundary; native custody is never disabled in source.
    monkeypatch.setattr(W, 'validate_private_path', lambda path: W.ordinary(path))
    monkeypatch.setattr(W, 'validate_code_path', lambda path: W.ordinary(path))


def test_exact_byte_shared_read_allows_atomic_replacement(tmp_path, ordinary_reads):
    path = tmp_path / 'snapshot.json'
    raw = b'{\r\n"sequence":1\r\n}'
    path.write_bytes(raw)
    value, digest = M.read_snapshot(path)
    assert value == {'sequence': 1} and digest == hashlib.sha256(raw).hexdigest()
    with M.open_shared_text(path) as held:
        replacement = tmp_path / 'next.json'
        replacement.write_bytes(b'{"sequence":2}')
        try:
            os.replace(replacement, path)
        except OSError as error:
            # Windows may still wait for the short shared reader to close. The
            # publisher's bounded retry can then complete; no truncation used.
            assert error.winerror in (5, 32, 33)
        assert held.buffer.read() == raw
    if replacement.exists():
        os.replace(replacement, path)
    assert M.read_snapshot(path)[0] == {'sequence': 2}


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]'])
def test_snapshot_rejects_ambiguous_json(tmp_path, ordinary_reads, raw):
    path = tmp_path / 'value.json'
    path.write_bytes(raw)
    with pytest.raises(W.BoundaryError):
        M.read_snapshot(path)


def test_snapshot_rejects_oversize_and_hardlink(tmp_path, ordinary_reads):
    path = tmp_path / 'value.json'
    path.write_bytes(b'{"data":"' + b'a' * 100 + b'"}')
    with pytest.raises(W.BoundaryError):
        M.read_snapshot(path, maximum=50)
    os.link(path, tmp_path / 'alias.json')
    with pytest.raises(W.BoundaryError):
        M.read_snapshot(path)


@pytest.mark.parametrize('key,value', [('config_sha256', '0' * 64), ('automatic_retry_allowed', True),
    ('exactly_one_start_requested', 1), ('full_srs_acceptance', True), ('status', 'PREPARED')])
def test_commissioning_mismatch_cannot_select_a_process(key, value):
    receipt = startup()
    receipt[key] = value
    with pytest.raises(W.BoundaryError):
        M.startup_binding(receipt)


def test_exact_commissioning_proof_preserves_filetime_integer():
    receipt = startup()
    assert M.startup_binding(receipt)['creation_filetime'] == 133000000000000000


def test_cpu_projection_explicit_source_scope_no_errors_or_paths():
    heartbeat = {'status': {'hardware': {'measured_at': 100, 'measurements': {'cpu': {
        'temperature_available': True, 'temperature_celsius': 68.5, 'temperature_source': 'protected-lib-probe',
        'temperature_error': 'secret-looking value must not be copied'}}}}}
    value = M.temperature_projection(heartbeat, 101)
    assert value['available'] and value['temperature_celsius'] == 68.5
    assert 'secret' not in json.dumps(value)
    assert M.temperature_projection(heartbeat, 116)['available'] is False
    assert M.temperature_projection({'status': 'bad'}, 100)['available'] is False


def test_actual_bounded_descendant_query_records_cpu_and_identity():
    result = M.sample_descendants(os.getpid(), limit=1, seconds=2)
    assert len(result['samples']) == 1
    row = result['samples'][0]
    assert row['pid'] == os.getpid() and row['creation_filetime'] > 116444736000000000
    assert row['handles'] > 0 and row['rss_bytes'] > 0
    assert row['kernel_cpu_100ns'] >= 0 and row['user_cpu_100ns'] >= 0
    assert 'image' not in row and 'cmdline' not in row
    assert result['actual_collection_seconds'] >= 0
    assert result['within_budget'] == (result['actual_collection_seconds'] <= 2)


def test_slow_final_native_read_cannot_claim_complete_census(monkeypatch):
    clock = types.SimpleNamespace(mono=0.)
    monkeypatch.setattr(M.time, 'monotonic', lambda: clock.mono)
    class FixtureHandle:
        def __init__(self, pid): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def snapshot(self):
            clock.mono += .3
            return {'creation_filetime': 1, 'handles': 3, 'image': 'fixture',
                    'kernel_cpu_100ns': 10, 'user_cpu_100ns': 10}
    monkeypatch.setattr(W, 'ProcessHandle', FixtureHandle)
    monkeypatch.setattr(M.psutil, 'Process', lambda pid: types.SimpleNamespace(
        ppid=lambda: 0, memory_info=lambda: types.SimpleNamespace(rss=1024), children=lambda recursive: []))
    result = M.sample_descendants(1, seconds=.5)
    assert result['actual_collection_seconds'] == .6
    assert result['within_budget'] is False and result['complete'] is False
    assert len(result['samples']) == 1


@pytest.fixture
def boundary(tmp_path, monkeypatch, ordinary_reads):
    heartbeat = tmp_path / 'heartbeat.json'
    queue = tmp_path / 'queue.json'
    output = tmp_path / 'output'
    output.mkdir()
    monkeypatch.setattr(M, 'HEARTBEAT', heartbeat)
    monkeypatch.setattr(M, 'QUEUE', queue)
    monkeypatch.setattr(M, 'sample_descendants', lambda pid: {'samples': [], 'complete': True})
    value = {'pid': 1234, 'instance_id': 'a' * 32, 'source_root': str(M.INSTALL / '.venv/Lib'),
             'sequence': 3, 'timestamp': M.time.time(), 'status': {}}
    heartbeat.write_text(json.dumps(value))
    queue.write_text(json.dumps({'schema': 1, 'kind': 'actual-warden-queue-launch-observation', 'platform': 'nt',
        'controller_pid': 1234, 'configured_slots': 6, 'configured_admission_ceiling': 4,
        'topology_matches_four_worker_acceptance': True, 'sensitive_jobs': ['NOT_LOGGED']}))
    native = dict(pid=1234, creation_filetime=proof()['creation_filetime'], token_sid=W.SYSTEM, image=str(M.BASE), handles=10)
    handle = types.SimpleNamespace(snapshot=lambda: dict(native))
    reader = M.BoundReader(proof(), output, handle)
    yield reader, heartbeat, value, native, output
    reader.close()


def test_bound_reader_records_identity_and_projection(boundary):
    reader, path, value, native, output = boundary
    assert reader(path) == value
    row = json.loads((output / 'native-boundaries.jsonl').read_text())
    assert row['creation_filetime'] == native['creation_filetime']
    assert row['queue_topology']['configured_admission_ceiling'] == 4
    assert 'NOT_LOGGED' not in json.dumps(row)


@pytest.mark.parametrize('change', ['creation', 'sid', 'image', 'sequence', 'instance', 'stale'])
def test_bound_reader_fails_closed_on_native_or_heartbeat_drift(boundary, change):
    reader, path, value, native, output = boundary
    if change == 'creation': native['creation_filetime'] += 1
    elif change == 'sid': native['token_sid'] = W.ADMIN
    elif change == 'image': native['image'] = r'C:\Windows\python.exe'
    elif change == 'sequence': value['sequence'] = 1
    elif change == 'instance': value['instance_id'] = 'b' * 32
    else: value['timestamp'] -= 16
    path.write_text(json.dumps(value))
    with pytest.raises(W.BoundaryError):
        reader(path)
    assert not (output / 'native-boundaries.jsonl').exists()


def test_repeated_sequence_is_tolerated_only_for_fifteen_seconds(boundary, monkeypatch):
    reader, path, value, native, output = boundary
    now = types.SimpleNamespace(mono=100.)
    monkeypatch.setattr(M.time, 'monotonic', lambda: now.mono)
    reader(path)
    now.mono = 114.
    reader(path)
    value['timestamp'] = M.time.time()  # Fresh timestamp cannot hide stalled progress.
    path.write_text(json.dumps(value))
    now.mono = 115.01
    with pytest.raises(W.BoundaryError, match='HEARTBEAT_SEQUENCE_STALLED'):
        reader(path)


def test_progress_sequence_resets_stall_deadline(boundary, monkeypatch):
    reader, path, value, native, output = boundary
    now = types.SimpleNamespace(mono=100.)
    monkeypatch.setattr(M.time, 'monotonic', lambda: now.mono)
    reader(path)
    now.mono = 114.
    value['sequence'] += 1
    path.write_text(json.dumps(value))
    reader(path)
    now.mono = 128.
    reader(path)
    assert reader.count == 3


def test_process_exit_during_queue_collection_fails_before_sample_written(boundary, monkeypatch):
    reader, path, value, native, output = boundary
    original = M.read_snapshot
    def read(target, **kwargs):
        result = original(target, **kwargs)
        if target == M.QUEUE:
            reader.handle.snapshot = lambda: (_ for _ in ()).throw(W.BoundaryError('PROCESS_EXITED'))
        return result
    monkeypatch.setattr(M, 'read_snapshot', read)
    with pytest.raises(W.BoundaryError, match='PROCESS_EXITED'):
        reader(path)
    assert not (output / 'native-boundaries.jsonl').exists()


@pytest.fixture
def clock_observer(monkeypatch, tmp_path):
    clock = types.SimpleNamespace(wall=1000000., mono=0., after_sleep=lambda: None)
    def sleep(seconds):
        clock.wall += seconds
        clock.mono += seconds
        clock.after_sleep()
    monkeypatch.setattr(P.time, 'time', lambda: clock.wall)
    monkeypatch.setattr(P.time, 'monotonic', lambda: clock.mono)
    monkeypatch.setattr(P.time, 'sleep', sleep)
    class Process:
        pid = 1234
        def __init__(self, pid): pass
        def username(self): return r'NT AUTHORITY\SYSTEM'
        def exe(self): return 'fixture-only'
        def create_time(self): return 900000.
        def cpu_percent(self, interval=None): return 2.
        def num_handles(self): return 10
        def memory_info(self): return types.SimpleNamespace(rss=1048576)
        def cpu_affinity(self): return [0, 1]
    monkeypatch.setattr(P.psutil, 'Process', Process)
    for name in ('require_system', 'validate_private_path', 'validate_private_directory', 'validate_code_path'):
        monkeypatch.setattr(W, name, lambda *args: None)
    monkeypatch.setattr(P, '_read_object', lambda *args: {'pid': 1234, 'instance_id': 'fixture-only',
        'timestamp': clock.wall, 'sequence': int(clock.mono)})
    return clock, lambda duration=172800: P.observe_native(tmp_path / 'beat', tmp_path / 'run',
                                        duration_seconds=duration, interval_seconds=10)


def test_exact_copied_loop_simulated_48h_still_fails_missing_heap_and_recovery(clock_observer):
    clock, run = clock_observer
    result = run()
    assert result['continuous_48h_complete'] is True and result['sample_count'] == 17280
    assert result['controller']['passed'] and result['handles']['passed']
    assert result['desktop_heap'] == {'available': False, 'passed': False}
    assert result['supervisor_timing']['monitor_passed'] is False
    assert result['passed'] is False


@pytest.mark.parametrize('kind', ['wall', 'mono', 'reverse'])
def test_clock_gap_or_suspend_cannot_claim_completion(clock_observer, kind):
    clock, run = clock_observer
    def interrupt():
        if kind == 'wall': clock.wall += 30
        elif kind == 'mono': clock.mono += 30
        else: clock.wall -= 15
    clock.after_sleep = interrupt
    result = run(20)
    assert result['passed'] is False and result['continuous_48h_complete'] is False
    assert result['continuity']['passed'] is False
    assert result['sample_count'] == 0


def test_adapter_failure_before_copied_loop_try_is_durable_and_cannot_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(W, 'require_system', lambda: None)
    monkeypatch.setattr(W, 'validate_private_directory', lambda path: None)
    monkeypatch.setattr(W, 'validate_private_path', lambda path: None)
    def read(path, **kwargs):
        if path == M.COMMISSIONING:
            return startup(), '0' * 64
        raise W.BoundaryError('FIXTURE_HEARTBEAT_REFUSAL')
    monkeypatch.setattr(M, 'read_snapshot', read)
    handle = types.SimpleNamespace()
    class FixtureHandle:
        def __init__(self, pid): pass
        def __enter__(self): return handle
        def __exit__(self, *args): pass
    monkeypatch.setattr(W, 'ProcessHandle', FixtureHandle)
    original = P._read_object
    output = tmp_path / 'samples'
    summary = M.run(output, '0' * 64)
    assert output.is_dir() and summary['status'] == 'OBSERVATION_HELD'
    assert summary['failure']['code'] == 'FIXTURE_HEARTBEAT_REFUSAL'
    receipt = tmp_path / 'observation-result.json'
    assert json.loads(receipt.read_text()) == summary
    assert P._read_object is original
    with pytest.raises(W.BoundaryError, match='FRESH_OBSERVATION_REQUIRED'):
        M.run(output, '0' * 64)
    assert json.loads(receipt.read_text()) == summary


def test_adapter_positive_short_flow_uses_real_copied_loop_and_remains_held(clock_observer, tmp_path, monkeypatch):
    clock, _ = clock_observer
    queue = {'schema': 1, 'kind': 'actual-warden-queue-launch-observation', 'platform': 'nt',
             'controller_pid': 1234, 'configured_slots': 6, 'configured_admission_ceiling': 4,
             'topology_matches_four_worker_acceptance': True}
    def read(path, **kwargs):
        if path == M.COMMISSIONING: return startup(), '0' * 64
        if path == M.QUEUE: return queue, '1' * 64
        return {'pid': 1234, 'instance_id': 'a' * 32, 'sequence': int(clock.mono) + 3,
                'timestamp': clock.wall, 'source_root': str(M.INSTALL / '.venv/Lib')}, '2' * 64
    monkeypatch.setattr(M, 'read_snapshot', read)
    monkeypatch.setattr(M, 'sample_descendants', lambda pid: {'samples': [], 'complete': True})
    class FixtureHandle:
        def __init__(self, pid): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def snapshot(self):
            return dict(pid=1234, creation_filetime=proof()['creation_filetime'], token_sid=W.SYSTEM,
                        image=str(M.BASE), handles=10)
    monkeypatch.setattr(W, 'ProcessHandle', FixtureHandle)
    summary = M.run(tmp_path / 'samples', '0' * 64, duration_seconds=1, interval_seconds=1)
    assert summary['status'] == 'SHORT_RESOURCE_DIAGNOSTIC_RECORDED'
    assert summary['native_boundary_samples'] == 2
    assert summary['resource_result']['sample_count'] == 1
    assert summary['resource_result']['passed'] is False
    assert summary['full_srs_acceptance'] is False
    assert summary['resource_result']['desktop_heap'] == {'available': False, 'passed': False}
    assert json.loads((tmp_path / 'observation-result.json').read_text()) == summary
