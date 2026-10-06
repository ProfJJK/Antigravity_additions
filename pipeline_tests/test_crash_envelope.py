import json
import os
import time

from cochem_pipeline.crash import crash_envelope, record_crash, MAX_ENVELOPE_BYTES


def test_physical_exception_has_pep657_columns_without_source_locals_or_message(tmp_path):
    private_value = 'PRIVATE-PROMPT-AND-CREDENTIAL-EXAMPLE'
    try:
        raise ValueError(private_value)
    except ValueError as exc:
        path = record_crash(tmp_path, exc, 'compatibility')
    raw = path.read_bytes()
    report = json.loads(raw)
    assert private_value.encode() not in raw
    assert b'raise ValueError' not in raw
    assert len(raw) <= MAX_ENVELOPE_BYTES
    assert report['schema'] == 1 and report['category'] == 'compatibility'
    assert report['pid'] == os.getpid()
    assert report['process_started_at'] <= report['timestamp'] <= time.time()
    frame = report['frames'][-1]
    assert frame['filename'] == 'test_crash_envelope.py'
    assert frame['lineno'] > 0 and frame['end_lineno'] >= frame['lineno']
    assert frame['colno'] is not None and frame['end_colno'] > frame['colno']
    assert set(tmp_path.iterdir()) == {path}


def test_atomic_latest_envelope_and_bounded_traceback(tmp_path):
    def recurse(count):
        if count:
            return recurse(count - 1)
        raise RuntimeError('must-not-be-copied')
    try:
        recurse(80)
    except RuntimeError as exc:
        record_crash(tmp_path, exc)
        assert len(crash_envelope(exc)['frames']) == 32
    path = record_crash(tmp_path, MemoryError('private'), 'resource')
    report = json.loads(path.read_bytes())
    assert report['exception_type'] == 'MemoryError'
    assert report['category'] == 'resource' and report['frames'] == []
    assert len(list(tmp_path.iterdir())) == 1
