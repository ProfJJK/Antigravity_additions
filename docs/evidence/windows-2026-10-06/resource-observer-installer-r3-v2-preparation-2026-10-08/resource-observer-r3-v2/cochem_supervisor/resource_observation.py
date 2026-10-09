"""Adapter for exact preserved performance code; no runtime, DB or client calls."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time

import psutil

from . import windows as win
from . import performance_acceptance as performance
from .shared_io import open_shared_text

INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
COMMISSIONING = Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json')
HEARTBEAT = Path(r'C:\ProgramData\CoChemPipeline427\private\supervisor_status.json')
QUEUE = Path(r'C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1\queue-launch.json')
EXPECTED = {
    'schema': 'cochem-warden-commissioning/1', 'status': 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED',
    'helper_sha256': 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c',
    'runtime_root': str(INSTALL),
    'install_receipt_sha256': '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6',
    'config_sha256': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
    'source_manifest_sha256': '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1',
    'revision_sha256': '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4',
    'monitoring_scope': 'heartbeat_and_queue_only', 'monitoring_started': True,
    'exactly_one_start_requested': True, 'automatic_retry_allowed': False,
    'automatic_repair_enabled': False, 'full_srs_acceptance': False,
}


def strict_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            win.require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def constant(value):
        raise win.BoundaryError('NONFINITE_JSON')
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    win.require(isinstance(result, dict), 'JSON_OBJECT_REQUIRED')
    return result


def read_snapshot(path, *, private=True, maximum=1048576):
    (win.validate_private_path if private else win.validate_code_path)(path)
    with open_shared_text(Path(path)) as stream:
        stat = os.fstat(stream.fileno())
        win.require(stat.st_nlink == 1 and 0 < stat.st_size <= maximum, 'SNAPSHOT_SIZE_OR_LINK')
        raw = stream.buffer.read(maximum + 1)
        win.require(len(raw) <= maximum, 'SNAPSHOT_BOUND')
        after = os.fstat(stream.fileno())
        win.require((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'SNAPSHOT_CHANGED')
    return strict_object(raw), hashlib.sha256(raw).hexdigest()


def startup_binding(receipt):
    for key, expected in EXPECTED.items():
        win.require(type(receipt.get(key)) is type(expected) and receipt[key] == expected, 'COMMISSIONING_BINDING')
    value = receipt.get('controller')
    win.require(isinstance(value, dict), 'CONTROLLER_PROOF')
    for name in ('pid', 'creation_filetime', 'first_sequence', 'final_sequence'):
        win.require(type(value.get(name)) is int and value[name] > 0, 'CONTROLLER_PROOF_INTEGER')
    win.require(value['final_sequence'] > value['first_sequence'], 'CONTROLLER_SEQUENCE_PROOF')
    win.require(isinstance(value.get('instance_id'), str) and len(value['instance_id']) == 32 and
                all(c in '0123456789abcdef' for c in value['instance_id']), 'INSTANCE_PROOF')
    win.require(value.get('token_sid') == win.SYSTEM and value.get('launcher_arguments_verified') is True, 'CONTROLLER_SYSTEM_PROOF')
    win.require(isinstance(value.get('launcher'), dict) and value['launcher'].get('token_sid') == win.SYSTEM,
                'CONTROLLER_LAUNCHER_PROOF')
    return dict(value)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def temperature_projection(heartbeat, now):
    """Controller-published metadata, explicitly not a second native probe."""
    value = {'available': False, 'source_scope': 'controller_published_cpu_sensor_metadata'}
    status = heartbeat.get('status')
    hardware = status.get('hardware') if isinstance(status, dict) else None
    if not isinstance(hardware, dict):
        return value
    measured = hardware.get('measured_at')
    measurements = hardware.get('measurements')
    cpu = measurements.get('cpu') if isinstance(measurements, dict) else None
    if not (finite(measured) and 0 <= now - measured <= 15 and isinstance(cpu, dict)):
        return value
    temperature = cpu.get('temperature_celsius')
    if cpu.get('temperature_available') is True and finite(temperature) and -50 <= temperature <= 150:
        # Do not copy unconstrained error/reason/source text into observation logs.
        value.update(available=True, measured_at=measured, temperature_celsius=temperature,
                     reported_source_sha256=hashlib.sha256(str(cpu.get('temperature_source', '')).encode()).hexdigest())
    return value


def queue_projection(queue, pid):
    expected = {'schema': 1, 'kind': 'actual-warden-queue-launch-observation', 'platform': 'nt',
                'controller_pid': pid, 'configured_slots': 6, 'configured_admission_ceiling': 4,
                'topology_matches_four_worker_acceptance': True}
    win.require(all(type(queue.get(k)) is type(v) and queue[k] == v for k, v in expected.items()), 'QUEUE_BINDING')
    # Full queue contents may include jobs/paths. Retain only topology and a commitment.
    return expected


def sample_descendants(pid, *, limit=64, seconds=.5):
    started = time.monotonic()
    pending = [(pid, None)]
    seen = set()
    result = []
    incomplete = False
    while pending and len(seen) < limit and time.monotonic() - started <= seconds:
        child_pid, expected_parent = pending.pop(0)
        if child_pid in seen:
            continue
        seen.add(child_pid)
        try:
            process = psutil.Process(child_pid)
            with win.ProcessHandle(child_pid) as handle:
                before = handle.snapshot()
                parent = process.ppid()
                win.require(expected_parent is None or parent == expected_parent, 'DESCENDANT_REPARENTED')
                memory = process.memory_info().rss
                children = process.children(recursive=False)
                after = handle.snapshot()
                win.require(before['creation_filetime'] == after['creation_filetime'], 'DESCENDANT_IDENTITY')
                result.append({'pid': child_pid, 'parent_pid': parent, 'creation_filetime': before['creation_filetime'],
                               'handles': after['handles'], 'rss_bytes': memory,
                               'kernel_cpu_100ns': after['kernel_cpu_100ns'], 'user_cpu_100ns': after['user_cpu_100ns'],
                               'image_path_sha256': hashlib.sha256(before['image'].casefold().encode()).hexdigest()})
                pending.extend((child.pid, child_pid) for child in children)
        except (OSError, psutil.Error, win.BoundaryError):
            incomplete = True  # A disappearing/denied process is never reported as zero usage.
    elapsed = time.monotonic() - started
    within_budget = 0 <= elapsed <= seconds
    return {'samples': result, 'complete': not incomplete and not pending and within_budget,
            'limit': limit, 'maximum_collection_seconds': seconds,
            'actual_collection_seconds': elapsed, 'within_budget': within_budget}


class BoundReader:
    """One held native process identity; metadata stream contains no job text."""
    def __init__(self, proof, output, handle):
        self.proof, self.output, self.handle = proof, Path(output), handle
        self.last_sequence = proof['final_sequence']
        self.last_advancement = time.monotonic()
        self.last_topology = float('-inf')
        self.stream = None
        self.count = 0
        self.complete_descendants = True
        self.cpu_temperature_available = True

    def __call__(self, path, maximum=1048576):
        win.require(Path(path) == HEARTBEAT, 'READ_SCOPE')
        heartbeat, digest = read_snapshot(HEARTBEAT, maximum=maximum)
        native = self.handle.snapshot()
        win.require(native['pid'] == self.proof['pid'] and native['creation_filetime'] == self.proof['creation_filetime'], 'CONTROLLER_NATIVE_IDENTITY')
        win.require(native['token_sid'] == win.SYSTEM and Path(native['image']) == BASE, 'CONTROLLER_NATIVE_CUSTODY')
        win.require(heartbeat.get('pid') == self.proof['pid'] and heartbeat.get('instance_id') == self.proof['instance_id'], 'HEARTBEAT_IDENTITY')
        win.require(heartbeat.get('source_root') == str(INSTALL / '.venv/Lib'), 'HEARTBEAT_SOURCE')
        sequence = heartbeat.get('sequence')
        win.require(type(sequence) is int and sequence >= self.last_sequence, 'HEARTBEAT_SEQUENCE_REVERSAL')
        if sequence > self.last_sequence:
            self.last_advancement = time.monotonic()
        win.require(0 <= time.monotonic() - self.last_advancement <= 15, 'HEARTBEAT_SEQUENCE_STALLED')
        self.last_sequence = sequence
        now = time.time()
        win.require(finite(heartbeat.get('timestamp')) and 0 <= now - heartbeat['timestamp'] <= 15, 'HEARTBEAT_FRESHNESS')
        row = {'schema': 'cochem-resource-native-boundary/1', 'timestamp': now, 'heartbeat_sha256': digest,
               'pid': native['pid'], 'creation_filetime': native['creation_filetime'], 'handles': native['handles'],
               'instance_id': self.proof['instance_id'], 'heartbeat_sequence': sequence}
        if now - self.last_topology >= 30:
            row['descendants'] = sample_descendants(native['pid'])
            self.complete_descendants &= row['descendants']['complete']
            row['cpu_temperature'] = temperature_projection(heartbeat, now)
            self.cpu_temperature_available &= row['cpu_temperature']['available']
            queue, queue_hash = read_snapshot(QUEUE)
            row['queue_topology'] = queue_projection(queue, native['pid'])
            row['queue_sha256'] = queue_hash
            self.last_topology = now
        after = self.handle.snapshot()
        win.require(all(after[key] == native[key] for key in ('pid', 'creation_filetime', 'token_sid', 'image')),
                    'CONTROLLER_CHANGED_DURING_COLLECTION')
        if self.stream is None:
            self.stream = (self.output / 'native-boundaries.jsonl').open('x', encoding='utf-8')
        self.stream.write(json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n')
        self.stream.flush()
        self.count += 1
        return heartbeat

    def close(self):
        if self.stream is not None:
            self.stream.close()


def run(output, commissioning_sha256, *, duration_seconds=172800, interval_seconds=1):
    win.require_system()
    output = Path(output)
    win.validate_private_directory(output.parent)
    win.require(not output.exists(), 'FRESH_OBSERVATION_REQUIRED')
    receipt_path = output.parent / 'observation-result.json'
    win.require(not receipt_path.exists(), 'OBSERVATION_RECEIPT_COLLISION')
    summary = {'schema': 'cochem-external-resource-observation/1', 'status': 'OBSERVATION_HELD',
               'commissioning_sha256': commissioning_sha256, 'runtime_root': str(INSTALL),
               'desktop_heap_acquisition': 'unavailable_no_verified_nonintrusive_collector',
               'supervisor_recovery_timing': 'unavailable_no_authoritative_event_source',
               'descendant_scope': 'bounded PID-parent snapshots; not complete native-worker or desktop-heap attestation',
               'process_or_task_changes': False, 'controller_api_calls': 0, 'database_opens': 0,
               'full_srs_acceptance': False, 'automatic_restart_or_resume': False}
    phase = 'commissioning_binding'
    try:
        receipt, digest = read_snapshot(COMMISSIONING, private=False)
        win.require(digest == commissioning_sha256, 'COMMISSIONING_HASH')
        proof = startup_binding(receipt)
        summary['controller'] = proof
        phase = 'native_observation'
        with win.ProcessHandle(proof['pid']) as handle:
            reader = BoundReader(proof, output, handle)
            original = performance._read_object
            # Only this external, exact-source package instance is adapted. The
            # live r3 is neither imported nor changed. Reads are heartbeat-only.
            performance._read_object = reader
            try:
                result = performance.observe_native(HEARTBEAT, output, duration_seconds=duration_seconds,
                           interval_seconds=interval_seconds, supervisor_database=None, desktop_heap_report=None)
            finally:
                performance._read_object = original
                reader.close()
        summary.update(resource_result=result, native_boundary_samples=reader.count,
                       bounded_descendant_collection_complete=reader.complete_descendants,
                       controller_cpu_temperature_available=reader.cpu_temperature_available)
        if result['continuous_48h_complete']:
            summary['status'] = 'CONTINUOUS_RESOURCE_WINDOW_RECORDED_FULL_SRS_HELD'
        elif result['error'] is None:
            summary['status'] = 'SHORT_RESOURCE_DIAGNOSTIC_RECORDED'
    except BaseException as error:
        code = str(error) if isinstance(error, win.BoundaryError) else None
        summary['failure'] = {'phase': phase, 'error_type': type(error).__name__, 'code': code,
                              'winerror': getattr(error, 'winerror', None)}
    raw = (json.dumps(summary, indent=2, allow_nan=False) + '\n').encode('utf-8')
    with receipt_path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return summary
