"""Real parsers/SQLite and optional physical Win32 handles; no model inference."""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from cochem_mcp.providers import parse_result
from cochem_pipeline.native_evidence import native_usage
from cochem_pipeline.store import JobStore, output_digest
from cochem_pipeline.worker import parse_gemini
from cochem_pipeline.windows import filetime_unix_seconds, WindowsIsolationError


@pytest.mark.parametrize('provider,reported,expected', [
    ('codex', {'input_tokens': 19, 'output_tokens': 7, 'cached_input_tokens': 3}, (19, 7, 3)),
    ('claude', {'input_tokens': 11, 'output_tokens': 4, 'cache_read_input_tokens': 2,
                'cache_creation_input_tokens': 9}, (11, 4, 2)),
    ('gemini', {'input': 27, 'prompt': 25, 'candidates': 6, 'cached': 8,
                'thoughts': 3, 'total': 36}, (27, 6, 8)),
])
def test_success_envelope_parser_and_numeric_usage_projection(provider, reported, expected):
    # Literal native protocol records are parser fixtures, not provider receipts.
    if provider == 'codex':
        raw = '\n'.join(json.dumps(item) for item in [
            {'type': 'thread.started', 'thread_id': 'parser-fixture'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
            {'type': 'turn.completed', 'usage': {**reported, 'account_secret': 'PRIVATE'}}])
        parsed = parse_result(provider, raw)
    elif provider == 'claude':
        raw = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
            'session_id': 'parser-fixture', 'result': '{}',
            'usage': {**reported, 'account_secret': 'PRIVATE'}})
        parsed = parse_result(provider, raw)
    else:
        raw = json.dumps({'session_id': 'parser-fixture', 'response': '{}',
            'stats': {'models': {'gemini-3.1-pro': {'tokens': {**reported,
                'account_secret': 'PRIVATE'}}}}})
        parsed = parse_gemini(raw, 'gemini-json', 'gemini-3.1-pro')
    result = native_usage(provider, parsed['usage'])
    assert (result['input_tokens'], result['output_tokens'], result['cached_input_tokens']) == expected
    assert result['native_fields'] == reported
    assert result['source'] == 'native_cli' and result['currency_cost'] is None
    assert 'PRIVATE' not in json.dumps(result) and 'account_secret' not in json.dumps(result)
    if provider == 'claude':
        assert result['cache_creation_input_tokens'] == 9 and result['total_tokens'] is None
    if provider == 'gemini':
        assert result['prompt_tokens'] == 25 and result['reasoning_tokens'] == 3


def test_registered_gemini_terminal_protocol_retains_only_structured_numeric_usage():
    parsed = parse_gemini(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
        'session_id': 'parser-fixture', 'model': 'gemini-3.1-pro', 'result': '{}',
        'usage': {'input_tokens': 8, 'output_tokens': 0}}), 'terminal-json', 'gemini-3.1-pro')
    result = native_usage('gemini', parsed['usage'])
    assert result['input_tokens'] == 8 and result['output_tokens'] == 0
    assert 'output_tokens' not in result['unavailable']


@pytest.mark.parametrize('value', [True, -1, 2**63, 1.5, float('nan'), float('inf'),
                                   'PRIVATE', {'token': 'PRIVATE'}, ['PRIVATE'], None])
def test_invalid_native_counters_are_unknown_never_zero_or_arbitrary_metadata(value):
    result = native_usage('codex', {'input_tokens': value, 'output_tokens': 0,
                                  'unknown_secret': 'PRIVATE'})
    assert result['input_tokens'] is None and result['output_tokens'] == 0
    assert result['unavailable']['input_tokens'] == 'invalid_reported_value'
    assert result['native_fields'] == {'output_tokens': 0}
    assert 'PRIVATE' not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('value', [None, {}, 'PRIVATE'])
def test_unreported_usage_remains_explicitly_unknown(value):
    result = native_usage('claude', value)
    assert result['reported'] is False and result['input_tokens'] is None
    assert result['unavailable']['input_tokens'] == 'not_reported'
    assert result['currency_cost_unavailable'] == 'subscription_currency_not_reported'


def test_conflicting_counter_aliases_remain_unknown_with_bounded_physical_values():
    result = native_usage('gemini', {'input_tokens': 8, 'input': 10, 'total': 17})
    assert result['input_tokens'] is None
    assert result['unavailable']['input_tokens'] == 'conflicting_reported_values'
    assert result['native_fields'] == {'input_tokens': 8, 'input': 10, 'total': 17}


def test_real_sqlite_completion_retains_usage_atomically_with_transition(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    store.submit('Persist numeric native metadata', ['REQ-1'], 1)
    node = store.claim('metadata-fixture')
    output = {'chapters': [{'chapter_id': 'one', 'title': 'One', 'requirements': ['REQ-1']}]}
    # This actual Python child establishes only the storage receipt fixture PID.
    process = subprocess.run([sys.executable, '-c', 'import os; print(os.getpid())'],
                             capture_output=True, text=True, check=True)
    route = node['route']
    usage = native_usage(route['provider'], {'input_tokens': 23, 'output_tokens': 9})
    receipt = {'provider': route['provider'], 'pid': int(process.stdout), 'exit_code': process.returncode,
               'session_id': 'python-storage-fixture', 'execution_kind': 'storage-contract-test',
               'output_sha256': output_digest(output), 'usage': usage,
               'requested_model': route['model'], 'reported_model': route['model'],
               'requested_effort': route.get('reasoning_effort'), 'selected_route': route,
               'route_reservation_id': route['reservation_id'], 'attempt_id': node['attempt_id'],
               'fencing_token': node['fencing_token'], 'worker_slot': node['worker_slot'],
               'job_id': node['job_id'], 'workflow_id': node['workflow_id']}
    with pytest.raises(RuntimeError, match='rollback metadata'):
        with store._write() as conn:
            store._event(conn, node, 'METADATA_ROLLBACK', fixture=True)
            raise RuntimeError('rollback metadata')
    store.complete(node['job_id'], node['attempt_id'], node['fencing_token'], output, receipt)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM pipeline_events WHERE event='METADATA_ROLLBACK'").fetchone()[0] == 0
        details = json.loads(conn.execute("SELECT details_json FROM pipeline_events WHERE job_id=? AND event='COMPLETED'",
                                          (node['job_id'],)).fetchone()[0])
    assert details['telemetry']['native_usage'] == usage
    assert details['telemetry']['native_usage_unavailable'] is None
    assert store.get(node['job_id'])['receipt']['usage'] == usage
    store.close()


def test_filetime_conversion_preserves_windows_epoch_without_boolean_timestamps():
    ticks = 116444736000000000 + 1_700_000_000 * 10_000_000 + 1_250_000
    assert filetime_unix_seconds(ticks) == 1_700_000_000.125
    for value in (True, 0, 116444736000000000, 2**64, 'PRIVATE'):
        with pytest.raises(WindowsIsolationError, match='timestamp'):
            filetime_unix_seconds(value)


@pytest.mark.skipif(os.name != 'nt', reason='Requires a real Win32 process handle; no emulation')
def test_owned_windows_process_handle_retains_creation_identity_after_exit():
    from cochem_pipeline.windows import process_creation_filetime
    before = time.time()
    with subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(0.05)'],
                          creationflags=0x08000000 | 0x00000200) as process:
        first = process_creation_filetime(process._handle)
        assert process.wait(10) == 0
        second = process_creation_filetime(process._handle)
        assert first == second
        assert before - 1 <= filetime_unix_seconds(first) <= time.time()
