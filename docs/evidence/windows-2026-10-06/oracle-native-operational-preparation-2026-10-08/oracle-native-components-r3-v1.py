"""SOURCE-ONLY candidate: no operational SYSTEM entry point is enabled.

The later reviewed wrapper must bind/copy this source, r3, configuration,
previous acceptance, fresh private/RAM paths and stopped-task state before
calling the component core. It composes actual Runtime.trip/cancel methods in
an isolated fixture; it does not construct or accept a complete service.
No provider is executed. Importing this module performs no native operations.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import MethodType, SimpleNamespace

INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
BASE_SHA256 = 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
FIXTURE_SHA256 = '137ffae58caee103a8025b7325f64580026ad6aefb10e95c3feba8f4494f0205'
ROOT = Path(r'C:\Program Files\CoChem\OracleNativeAcceptance4.2.7-windows-20261008-r3-v1')
PRIVATE = Path(r'C:\ProgramData\CoChemPipeline427\private\oracle-native-acceptance-20261008-r3-v1')
SCRATCH = Path(r'R:\CoChem427-windows-20261007\slot1\.cochem-scratch\oracle-native-acceptance-20261008-r3-v1')
MODULE_PINS = {
    'oracle': '11e593a6da0adba876b41ba979311b53af9d175dedd57b4c0b3946370eaa96bf',
    'runtime': '4fa4c09ceb749ffa5e220c5e59dd5f04a441672e049062902595a19dd14e5db0',
    'worker': '476f83a3f4e19b826d62bd02c0bacce19530de442d047ed9f66a25d0ace0284c',
    'windows': 'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba',
    'resource_limits': 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f',
    'store': 'ba55f367a5c625ee1bfda0015c0dd69123b8cc4a52019b8d1cb76ac5c8d7d287',
}
IGNORED_EVENTS = frozenset(('opened', 'closed', 'closed_no_write'))
MAX_OUTPUT = 4096
MAX_EVENTS = 8192


def require(value, code):
    if not value:
        raise ValueError(code)


def nonce_valid(nonce):
    return isinstance(nonce, str) and re.fullmatch('[0-9a-f]{32}', nonce) is not None


class EventWitness:
    """Bounded timings around unchanged SlotMonitor calls; no event invention."""
    def __init__(self):
        self.samples = []
        self.thread_ids = set()
        self.lock = threading.Lock()

    def append(self, began, ended):
        require(all(type(v) in (int, float) and math.isfinite(v) for v in (began, ended)), 'EVENT_CLOCK')
        require(0 <= began <= ended, 'EVENT_CLOCK')
        with self.lock:
            require(len(self.samples) < MAX_EVENTS, 'EVENT_COUNT_BOUND')
            require(not self.samples or began >= self.samples[-1][1], 'EVENT_ORDER')
            self.samples.append((began, ended))
            self.thread_ids.add(threading.get_ident())

    def storm_window(self):
        # 501 entire call intervals within <1s proves their internal Oracle
        # monotonic reads also lie inside a real one-second window.
        with self.lock:
            rows = tuple(self.samples)
        for end in range(500, len(rows)):
            elapsed = rows[end][1] - rows[end - 500][0]
            if elapsed < 1:
                return {'event_count': 501, 'first_call_started': rows[end - 500][0],
                        'last_call_completed': rows[end][1], 'elapsed_seconds': elapsed}
        raise ValueError('PHYSICAL_STORM_THRESHOLD_NOT_OBSERVED')


def validate_result(report):
    require(isinstance(report, dict) and report.get('schema') == 'cochem-oracle-native-components/1', 'REPORT_SCHEMA')
    require(report.get('scope') == 'isolated_oracle_runtime_trip_method_integration', 'REPORT_SCOPE')
    require(report.get('full_service_acceptance') is False and report.get('runtime_constructed') is False, 'OVERCLAIM')
    require(report.get('runtime_trip_fencing_verified') is True, 'METHOD_FENCING')
    durable = report.get('durable_fencing', {})
    require(isinstance(durable, dict) and durable.get('status') == 'FAILED'
            and durable.get('scope') == 'actual_Runtime_trip_and_cancel_methods_on_fresh_real_JobStore',
            'DURABLE_METHOD_SCOPE')
    for field in ('fencing_token_advanced', 'stale_completion_rejected_without_mutation',
                  'unrelated_workflow_preserved', 'exact_cleanup_telemetry'):
        require(durable.get(field) is True, 'DURABLE_' + field.upper())
    window = report.get('storm_window', {})
    require(type(window.get('event_count')) is int and window['event_count'] == 501, 'EVENT_COUNT')
    first, last, elapsed = (window.get(k) for k in ('first_call_started', 'last_call_completed', 'elapsed_seconds'))
    require(all(type(v) in (int, float) and math.isfinite(v) for v in (first, last, elapsed)), 'EVENT_CLOCK')
    require(0 <= first <= last and 0 <= elapsed < 1 and abs(last - first - elapsed) < 1e-9, 'EVENT_CLOCK')
    require(type(report.get('callback_count')) is int and report['callback_count'] == 1, 'CALLBACK_COUNT')
    for field in ('callback_async', 'tripped_task', 'no_tripped_context', 'unrelated_context_preserved',
                  'watcher_stopped', 'native_cleanup_verified', 'parent_exit_verified',
                  'grandchild_exit_verified', 'selected_identity_reacquired'):
        require(report.get(field) is True, 'PROOF_' + field.upper())
    require(type(report.get('job_active_processes')) is int and report['job_active_processes'] == 0, 'JOB_NOT_EMPTY')
    clocks = [report.get(k) for k in ('trip_started_at', 'cleanup_completed_at', 'durable_trip_completed_at', 'identity_reacquired_at')]
    require(all(type(v) in (float, int) and math.isfinite(v) for v in clocks)
            and 0 <= clocks[0] <= clocks[1] <= clocks[2] <= clocks[3], 'CLEANUP_ORDER')
    for field in ('parent', 'grandchild'):
        row = report.get(field, {})
        require(type(row.get('pid')) is int and row['pid'] > 0, 'PROCESS_ID')
        require(type(row.get('creation_time_filetime')) is int and row['creation_time_filetime'] > 116444736000000000, 'PROCESS_CREATION')
        require(row.get('token_matches_worker') is True and row.get('image_matches_base') is True
                and row.get('owned_job_member') is True, 'PROCESS_ATTESTATION')
    require(report['parent']['pid'] != report['grandchild']['pid'], 'PROCESS_ID')
    return True


class IsolatedRuntimeTrip:
    """Real Runtime.trip/cancel methods, real store; no Runtime constructor.

    This structural context is explicitly a method-level integration fixture.
    The normal dispatch, admission, hardware loop, and service are not claimed.
    NativeRunner and the active node are supplied by the physical component
    owner; no reaper is substituted in the future SYSTEM component case.
    """
    def __init__(self, store, runner, node, unrelated_workflow, cleaned):
        from cochem_pipeline.runtime import Runtime
        require(Path(store.path) == PRIVATE / 'job_board.db', 'ISOLATED_JOBSTORE_PATH')
        require(node.get('status') == 'IN_PROGRESS' and node.get('worker_slot') == 'slot1', 'ACTIVE_FIXTURE_NODE')
        self.store, self.runner = store, runner
        self.config = SimpleNamespace(job_db=store.path)
        self.lock = threading.RLock()
        self.quarantined = {}
        self.active = {node['job_id']: {'node': dict(node), 'slot': 'slot1', 'pid': None,
            'cleaned': cleaned, 'cleanup_verified': False, 'cancel_event': threading.Event()}}
        self.cancel = MethodType(Runtime.cancel, self)
        self.trip = MethodType(Runtime.trip, self)
        self.node = dict(node)
        self.unrelated_workflow = unrelated_workflow
        self.unrelated_before = store.workflow(unrelated_workflow)

    def confirm_physical_cleanup(self, proof):
        require(proof.get('native_cleanup_verified') is True and proof.get('watcher_stopped') is True
                and proof.get('parent_exit_verified') is True and proof.get('grandchild_exit_verified') is True
                and type(proof.get('job_active_processes')) is int and proof['job_active_processes'] == 0,
                'PHYSICAL_CLEANUP_REQUIRED_BEFORE_DURABLE_RELEASE')
        node = self.node
        require(self.store.get(node['job_id'])['status'] == 'IN_PROGRESS', 'JOB_CHANGED_BEFORE_CLEANUP')
        require(self.store.clear_execution_quarantine(node['job_id'], node['attempt_id'], node['fencing_token']),
                'EXACT_EXECUTION_GUARD_NOT_CLEARED')
        with self.lock:
            active = self.active[node['job_id']]
            active['cleanup_verified'] = True
            active['cleaned'].set()

    def verify_durable_result(self):
        """Inspect this fixture only; submit a hostile stale completion to it."""
        from cochem_pipeline.store import output_digest
        node = self.node
        current = self.store.get(node['job_id'])
        # This exact production trip uses cancel(), whose disposition is FAILED,
        # not PENDING_RETRY. Do not relax it to a set of convenient statuses.
        require(current['status'] == 'FAILED' and current['attempt_id'] is None
                and current['fencing_token'] > node['fencing_token']
                and current['lease_owner'] is None and current['lease_expires_at'] is None,
                'DURABLE_FAILED_FENCE')
        require(not self.quarantined and not self.store.execution_quarantines(), 'CLEANUP_GUARD_REMAINS')
        require(self.store.workflow(self.unrelated_workflow) == self.unrelated_before, 'UNRELATED_WORKFLOW_CHANGED')
        before = self.store.workflow(node['workflow_id'])
        output = {'untrusted_stale_fixture_output': True}
        receipt = {'provider': 'codex', 'pid': 1, 'exit_code': 0, 'session_id': 'stale-fixture',
                   'output_sha256': output_digest(output), 'execution_kind': 'hostile-fixture-not-model'}
        try:
            self.store.complete(node['job_id'], node['attempt_id'], node['fencing_token'], output, receipt)
        except ValueError as error:
            require(str(error) == 'Stale or unowned attempt cannot complete a job', 'WRONG_STALE_REJECTION')
        else:
            raise ValueError('STALE_COMPLETION_ACCEPTED')
        require(self.store.workflow(node['workflow_id']) == before, 'STALE_COMPLETION_MUTATED_STATE')
        with self.store._connection() as db:
            rows = db.execute("SELECT details FROM recovery_telemetry WHERE action='velocity_circuit_breaker'").fetchall()
        require(len(rows) == 1, 'EXACT_TRIP_TELEMETRY')
        event = json.loads(rows[0][0])
        require(event.get('job_id') == node['job_id'] and event.get('attempt_id') == node['attempt_id']
                and event.get('cleanup_verified') is True and event.get('run_as') == 'SYSTEM'
                and event.get('threshold_per_second') == 500, 'TRIP_TELEMETRY_BINDING')
        return {'status': current['status'], 'fencing_token_advanced': True,
                'stale_completion_rejected_without_mutation': True,
                'unrelated_workflow_preserved': True, 'exact_cleanup_telemetry': True,
                'scope': 'actual_Runtime_trip_and_cancel_methods_on_fresh_real_JobStore'}


def observe_handle(win, handle, job, sid):
    """Observe already owned process/job handles; PID reuse cannot change them."""
    api = win._api()
    kernel = api['kernel32']
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = [win.HANDLE, win.DWORD, win.LPWSTR, C.POINTER(win.DWORD)]
    query.restype = win.BOOL
    member = kernel.IsProcessInJob
    member.argtypes = [win.HANDLE, win.HANDLE, C.POINTER(win.BOOL)]
    member.restype = win.BOOL
    get_pid = kernel.GetProcessId
    get_pid.argtypes, get_pid.restype = [win.HANDLE], win.DWORD
    token = win.HANDLE()
    win._check(api['advapi32'].OpenProcessToken(handle, 8, C.byref(token)), 'Observe fixture token')
    try:
        actual_sid = win._sid_text(win._SID_AND_ATTRIBUTES.from_buffer(win._token_info(token, 1)).Sid)
        size = win.DWORD(32768)
        image = C.create_unicode_buffer(size.value)
        win._check(query(handle, 0, image, C.byref(size)), 'Observe fixture executable')
        inside = win.BOOL()
        win._check(member(handle, job, C.byref(inside)), 'Observe fixture Job membership')
        require(actual_sid == sid and actual_sid not in (win.SYSTEM_SID, win.ADMIN_SID), 'FIXTURE_TOKEN')
        require(Path(image.value).resolve() == BASE.resolve() and inside.value, 'FIXTURE_IMAGE_JOB')
        pid = int(get_pid(handle))
        require(pid > 0, 'FIXTURE_PID')
        return {'pid': pid, 'creation_time_filetime': win.process_creation_filetime(handle),
                'token_matches_worker': True, 'image_matches_base': True, 'owned_job_member': True}
    finally:
        win._close(token)


def duplicate_owned(win, handle):
    value = win.HANDLE()
    api = win._api()['kernel32']
    current = api.GetCurrentProcess()
    win._check(api.DuplicateHandle(current, handle, current, C.byref(value), 0, False, 2), 'Retain fixture witness handle')
    return value


def job_census(win, job):
    row = win._BASIC_ACCOUNTING()
    win._check(win._api()['kernel32'].QueryInformationJobObject(job, 1, C.byref(row), C.sizeof(row), None), 'Observe fixture Job census')
    return int(row.ActiveProcesses)


def validate_launch_limits(limits, observed):
    require(limits.memory_limit_mb == 2048 and limits.cpu_rate_percent == 20
            and limits.max_processes == 16 and limits.e_core_policy == 'required', 'FIXED_LAUNCH_LIMITS')
    require(isinstance(observed, dict) and observed.get('native_limits_verified') is True
            and observed.get('kill_on_job_close') is True and observed.get('topology_verified') is True
            and type(observed.get('affinity_mask')) is int and observed['affinity_mask'] > 0,
            'NATIVE_LIMITS_NOT_ATTESTED')
    require(all(observed.get(key) == value for key, value in limits.as_dict().items()
                if key != 'affinity_mask'), 'NATIVE_LIMITS_DRIFT')


def require_fixture_read_access(win, fixture, sid):
    """Conservative ACL precheck; the gated actual child proves executable read."""
    for path in (ROOT, fixture):
        win.validate_code_path(path)
        owner, _, rows = win._acl(path)
        require(owner in (win.SYSTEM_SID, win.ADMIN_SID, win.TRUSTED_INSTALLER_SID), 'FIXTURE_CODE_OWNER')
        # These universal/principal grants avoid guessing local group membership.
        # Future staging can grant the exact selected worker RX on this fresh
        # fixture/root if the shared protected copy ACL lacks a universal grant.
        readable = any(account in (sid, 'S-1-1-0', 'S-1-5-11') and not flags & 8
                       and (mask & 0x80000000 or mask & 0x120089 == 0x120089)
                       for account, mask, flags in rows)
        require(readable, 'FIXTURE_WORKER_READ_NOT_PROVEN')


@contextmanager
def worker_reservations(win, workers):
    """Six reservations; explicit slot1 handoff to the unchanged launcher."""
    handles = {}
    def take(slot):
        require(slot not in handles, 'RESERVATION_ALREADY_OWNED')
        sid = win._sid_text(win._account_sid(workers[slot]['name']))
        C.set_last_error(0)
        handle = win._check(win._api()['kernel32'].CreateMutexW(None, False, 'Global\\CoChemPipeline422-' + sid), 'Reserve acceptance identity')
        existed = C.get_last_error() == 183
        if existed:
            win._close(handle)
            raise ValueError('WORKER_ALREADY_RESERVED')
        handles[slot] = handle
    def release_selected():
        require(set(handles) == {f'slot{i}' for i in range(1, 7)}, 'SIX_RESERVATIONS_REQUIRED')
        win._close(handles.pop('slot1'))
    try:
        require(set(workers) == {f'slot{i}' for i in range(1, 7)}, 'SIX_WORKERS_REQUIRED')
        for slot in sorted(workers):
            take(slot)
        yield release_selected, lambda: take('slot1')
    finally:
        for handle in reversed(tuple(handles.values())):
            win._close(handle)


def _component_core(config, nonce, *, require_stopped, assert_preserved_baseline):
    """Future SYSTEM composition point, intentionally unreachable from CLI.

    Caller must supply reviewed real stopped-task / baseline guards, stage the
    exact protected source and create ONLY the fresh empty PRIVATE and SCRATCH
    directories with inherited correct ACLs. The future wrapper is not written.
    """
    import importlib
    from cochem_pipeline import windows as win
    from cochem_pipeline.ramdisk import ordinary_tree, RamdiskManager
    from cochem_pipeline.oracle import Oracle, ContextEngine, Rule
    from cochem_pipeline.runtime import SlotMonitor
    from cochem_pipeline.worker import NativeRunner
    from cochem_pipeline.store import JobStore
    from watchdog.observers import Observer
    win.require_system()
    require(nonce_valid(nonce), 'NONCE')
    require(Path(sys.executable).resolve() == (INSTALL / '.venv/Scripts/python.exe').resolve(), 'RUNTIME')
    require(Path(__file__).resolve() == ROOT / 'oracle-native-components-r3-v1.py', 'PROTECTED_HELPER')
    for name, pin in MODULE_PINS.items():
        module = importlib.import_module('cochem_pipeline.' + name)
        path = Path(module.__file__)
        ordinary_tree(path); win.validate_code_path(path)
        require(path.resolve() == INSTALL / '.venv/Lib/site-packages/cochem_pipeline' / (name + '.py'), 'MODULE_PATH')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == pin, 'MODULE_HASH')
    require(config.max_execution_slots == 4 and set(config.workers) == {f'slot{i}' for i in range(1, 7)}, 'CAPACITY')
    # This core never constructs Runtime, opens its production JobStore, changes profiles,
    # provisions a RAM disk, or invokes a provider. The launcher alone owns logon.
    for directory in (PRIVATE, SCRATCH):
        ordinary_tree(directory)
        require(directory.is_dir() and not any(directory.iterdir()), 'FIXTURE_MUST_BE_FRESH_EMPTY')
    win.validate_private_directory(PRIVATE)
    identity = win.WorkerIdentity(**config.workers['slot1'])
    sid = win._sid_text(win._account_sid(identity.name))
    fixture = ROOT / 'oracle-rogue-fixture-r3-v1.py'
    for path, pin in ((BASE, BASE_SHA256), (fixture, FIXTURE_SHA256)):
        ordinary_tree(path); win.validate_code_path(path)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == pin, 'FIXTURE_LAUNCH_BYTES')
    require_fixture_read_access(win, fixture, sid)
    win._validate_worker_directory(SCRATCH, sid)
    manager = RamdiskManager(config.ramdisk, config.private_root,
        {key: win.WorkerIdentity(**row) for key, row in config.workers.items()})
    manager.inspect()
    require(SCRATCH.parent == manager.workspace('slot1').scratch, 'EXACT_RAM_FIXTURE')
    assert_preserved_baseline()
    require_stopped()
    report = {'schema': 'cochem-oracle-native-components/1', 'nonce': nonce,
              'scope': 'isolated_oracle_runtime_trip_method_integration',
              'runtime_trip_fencing_verified': False, 'full_service_acceptance': False, 'runtime_constructed': False}
    runner = NativeRunner(config)
    job_id = None
    trip_fixture = None
    witness = EventWitness()
    callback_done, cleaned = threading.Event(), threading.Event()
    callbacks = []
    owner_thread = threading.get_ident()
    callback_error = []
    monitor_error = []
    observer = Observer()
    parent_witness = grandchild = job_witness = None
    def trip(value):
        try:
            require(value == job_id, 'TRIP_JOB')
            callbacks.append(threading.get_ident())
            report['trip_started_at'] = time.monotonic()
            trip_fixture.trip(value)
            report['durable_trip_completed_at'] = time.monotonic()
        except BaseException as error:
            callback_error.append(type(error).__name__)
            raise
        finally:
            callback_done.set()
    engine = ContextEngine([Rule('acceptance-core', 'Fixed acceptance core rule.', (), core=True)], 4096)
    with worker_reservations(win, config.workers) as (handoff, reacquire), ExitStack() as resources:
        # The only JobStore constructed is new and private to this acceptance.
        store = JobStore(PRIVATE / 'job_board.db', routing_policy=config.routing,
                         cleanup_boot_id=win.current_boot_identity())
        resources.callback(store.close)
        store.submit('Isolated non-inference Oracle reaper fixture', ['S427-ORACLE-005'], 1,
                     workflow_id='oracle-fixture-' + nonce)
        node = store.claim('oracle-fixture-controller', worker_slot='slot1', requires_cleanup=True,
                           cleanup_boot_id=win.current_boot_identity(), containment_id=nonce, max_workers=4)
        require(node is not None and node['workflow_id'] == 'oracle-fixture-' + nonce, 'FRESH_FIXTURE_CLAIM')
        job_id = node['job_id']
        unrelated = 'oracle-unrelated-' + nonce
        store.submit('Unrelated disposable workflow', ['S427-ORACLE-005'], 1, workflow_id=unrelated)
        trip_fixture = IsolatedRuntimeTrip(store, runner, node, unrelated, cleaned)
        with Oracle(engine, PRIVATE / 'tracking.db', trip) as oracle:
            class WatchedMonitor(SlotMonitor):
                def on_any_event(self, event):
                    if event.event_type in IGNORED_EVENTS:
                        return super().on_any_event(event)
                    began = time.monotonic()
                    try:
                        result = super().on_any_event(event)
                        witness.append(began, time.monotonic())
                        return result
                    except BaseException as error:
                        monitor_error.append(type(error).__name__)
                        raise
            observer.schedule(WatchedMonitor(oracle, job_id), str(SCRATCH), recursive=True)
            read_fd, write_fd = os.pipe()
            try:
                # Private exclusive outputs contain fixed fixture metadata only.
                with os.fdopen(read_fd, 'rb', buffering=0) as reader, os.fdopen(write_fd, 'wb', buffering=0) as writer:
                    observer.start()
                    require_stopped(); assert_preserved_baseline(); handoff()
                    process = runner._launch_worker(identity,
                        [str(BASE), '-I', '-B', str(ROOT / 'oracle-rogue-fixture-r3-v1.py'), nonce],
                        SCRATCH, reader, PRIVATE / 'stdout.json', PRIVATE / 'stderr.txt',
                        worker_slot='slot1', limits=config.execution_limits)
                    with runner._managed(job_id, process):
                        validate_launch_limits(config.execution_limits, process.resource_limits_evidence)
                        report['native_limits'] = process.resource_limits_evidence
                        trip_fixture.active[job_id]['pid'] = process.pid
                        parent_witness = duplicate_owned(win, process._process)
                        job_witness = duplicate_owned(win, process._job)
                        report['parent'] = observe_handle(win, parent_witness, job_witness, sid)
                        writer.write((nonce + ':SPAWN\n').encode())
                        deadline = time.monotonic() + 20
                        child = None
                        while time.monotonic() < deadline:
                            with (PRIVATE / 'stdout.json').open('rb') as output:
                                raw = output.read(MAX_OUTPUT + 1)
                            require(len(raw) <= MAX_OUTPUT and (PRIVATE / 'stderr.txt').stat().st_size <= MAX_OUTPUT, 'OUTPUT_BOUND')
                            if raw.endswith(b'\n'):
                                child = json.loads(raw)
                                break
                            require(process.poll() is None, 'FIXTURE_EXITED_BEFORE_ATTESTATION')
                            time.sleep(.02)
                        require(isinstance(child, dict) and set(child) == {'nonce', 'grandchild_pid'}
                                and child['nonce'] == nonce and type(child['grandchild_pid']) is int
                                and child['grandchild_pid'] > 0, 'GRANDCHILD_HANDSHAKE')
                        api = win._api()['kernel32']
                        opener = api.OpenProcess
                        opener.argtypes, opener.restype = [win.DWORD, win.BOOL, win.DWORD], win.HANDLE
                        grandchild = win._check(opener(0x101000, False, child['grandchild_pid']), 'Retain fixture grandchild')
                        report['grandchild'] = observe_handle(win, grandchild, job_witness, sid)
                        # A real sentinel verifies watcher delivery before burst.
                        with (SCRATCH / 'watcher-ready').open('xb'):
                            pass
                        deadline = time.monotonic() + 5
                        while not witness.samples and time.monotonic() < deadline:
                            time.sleep(.02)
                        require(bool(witness.samples), 'WATCHER_NOT_DELIVERING')
                        time.sleep(1.1)
                        require_stopped(); assert_preserved_baseline()
                        writer.write((nonce + ':STORM\n').encode())
                        process.wait(timeout=20)
                    report['native_cleanup_verified'] = True
                    observer.stop(); observer.join(timeout=10)
                    require(not observer.is_alive() and not monitor_error, 'WATCHER_NOT_STOPPED_CLEANLY')
                    report['watcher_stopped'] = True
                    report['job_active_processes'] = job_census(win, job_witness)
                    for label, handle in (('parent', parent_witness), ('grandchild', grandchild)):
                        report[label + '_exit_verified'] = (win._api()['kernel32'].WaitForSingleObject(handle, 0) == 0
                            and win.process_creation_filetime(handle) == report[label]['creation_time_filetime'])
                    report['cleanup_completed_at'] = time.monotonic()
                    trip_fixture.confirm_physical_cleanup(report)
                require(callback_done.wait(5) and not callback_error, 'REAPER_NOT_VERIFIED')
                observer.stop(); observer.join(timeout=10)
                require(not observer.is_alive(), 'WATCHER_NOT_STOPPED')
                require(not monitor_error, 'MONITOR_CALLBACK_FAILED')
                report['watcher_stopped'] = True
                report['storm_window'] = witness.storm_window()
                report['callback_count'] = len(callbacks)
                report['callback_async'] = all(value != owner_thread and value not in witness.thread_ids for value in callbacks)
                report['tripped_task'] = oracle.tripped_tasks == frozenset((job_id,))
                report['no_tripped_context'] = not oracle.has_pending(job_id)
                report['durable_fencing'] = trip_fixture.verify_durable_result()
                report['runtime_trip_fencing_verified'] = True
                reacquire(); report['selected_identity_reacquired'] = True
                report['identity_reacquired_at'] = time.monotonic()
                oracle.record('unrelated-fixture', 'fixed healthy event')
                time.sleep(.51)
                outputs = oracle.drain()
                report['unrelated_context_preserved'] = (len(outputs) == 1 and outputs[0]['task_id'] == 'unrelated-fixture')
                require_stopped(); assert_preserved_baseline()
                validate_result(report)
            finally:
                # Managed native cleanup precedes this on every launched path.
                # Failure does not permit fixture deletion or an automatic retry.
                observer.stop()
                if observer.ident is not None:
                    observer.join(timeout=10)
                for handle in (grandchild, parent_witness, job_witness):
                    win._close(handle)
    return report


if __name__ == '__main__':
    print(json.dumps({'status': 'SOURCE_ONLY_NOT_OPERATIONAL', 'worker_launches': 0,
        'missing': ['reviewed protected staging/receipt wrapper', 'stopped or maintenance window'],
        'full_runtime_trip_acceptance': False}))
