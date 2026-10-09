"""Observed status-shape regressions; no CLI launches or authentication claims."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from cochem_pipeline.agy_status import AgyStatusError, parse_quota_table, parse_selected_model, summarize_status


MODEL = 'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n'
USAGE = ('Gemini Models\tWeekly Limit Remaining\t98%\t2026-10-14T02:23:46Z\n'
         'Gemini Models\tFive Hour Limit Remaining\t98%\t2026-10-07T08:40:28Z\n'
         'Claude and GPT models\tWeekly Limit Remaining\t0%\t2026-10-10T12:31:21Z\n'
         'Claude and GPT models\tFive Hour Limit Remaining\tdisabled\t\n')
NOW = '2026-10-07T04:35:38.027092Z'
SHA = '38f30c7dd1ed808f5cf98fe2014de3d30903035a4f0df02d3eb72a9ff8993741'


def summary(**changes):
    arguments = dict(model_stdout=MODEL, usage_stdout=USAGE, observed_at_utc=NOW,
                     executable_sha256_before=SHA, executable_sha256_after=SHA, version='1.3.1')
    return summarize_status(**(arguments | changes))


def test_quota_diagnostic_preserves_zero_and_disabled_without_claiming_auth_or_model_execution():
    result = summary()
    assert result['selected_model'] == {'model_id': 'gemini-3.8-flash-high',
        'display_name': 'Gemini 3.8 Flash (High)', 'evidence_kind': 'selected_configuration'}
    quotas = result['native_quotas']
    assert [(q['enabled'], q['remaining_percent']) for q in quotas] == [(True, 98), (True, 98), (True, 0), (False, None)]
    assert quotas[2]['native_group'] == 'Claude and GPT models'
    assert quotas[3]['resets_at_utc'] is None
    assert quotas[3]['reset_relation_at_capture'] == 'not_applicable'
    assert result['subscription_authentication'] == 'unverified'
    assert result['subscription_verified'] is False
    assert result['isolated_worker_verified'] is False
    assert result['actual_serving_model'] is None
    assert result['reported_effort'] is None
    assert result['inference_readiness'] == 'not_established'
    assert result['routing_or_provider_contract_changed'] is False


def test_actual_windows_line_endings_and_no_final_newline_are_supported():
    assert parse_selected_model(MODEL.replace('\n', '\r\n')) == parse_selected_model(MODEL.rstrip('\n'))
    assert parse_quota_table(USAGE.replace('\n', '\r\n'), observed_at_utc=NOW) == parse_quota_table(USAGE.rstrip('\n'), observed_at_utc=NOW)


@pytest.mark.parametrize('when,relation', [
    ('2026-10-14T02:23:45.999999Z', 'future'),
    ('2026-10-14T02:23:46Z', 'due_or_past'),
    ('2026-10-14T02:23:47Z', 'due_or_past'),
])
def test_reset_clock_relation_is_diagnostic_and_does_not_restore_quota(when, relation):
    row = parse_quota_table(USAGE, observed_at_utc=when)[0]
    assert row['reset_relation_at_capture'] == relation
    assert row['remaining_percent'] == 98


@pytest.mark.parametrize('value', ['-1%', '101%', '98.5%', '098%', '+98%', 'nan%', '98', '', 'DISABLED'])
def test_invalid_remaining_values_are_rejected(value):
    with pytest.raises(AgyStatusError):
        parse_quota_table(USAGE.replace('98%', value, 1), observed_at_utc=NOW)


@pytest.mark.parametrize('value', ['', 'tomorrow', '2026-02-30T02:23:46Z', '2026-10-14T02:23:46',
                                   '2026-10-14T02:23:46+00:00', '2026-10-14T02:23:60Z'])
def test_enabled_quota_requires_valid_unambiguous_utc_reset(value):
    with pytest.raises(AgyStatusError):
        parse_quota_table(USAGE.replace('2026-10-14T02:23:46Z', value), observed_at_utc=NOW)


@pytest.mark.parametrize('change', [
    lambda text: text + text.splitlines()[0] + '\n',
    lambda text: text.splitlines()[0] + '\n' + text.splitlines()[0] + '\n',
    lambda text: text.replace('disabled\t', 'disabled\t2026-10-14T02:23:46Z'),
    lambda text: text.replace('Weekly Limit Remaining', 'Monthly Limit Remaining'),
    lambda text: text.replace('Gemini Models', 'Unknown provider'),
    lambda text: text.replace('\t98%', ' 98%', 1),
    lambda text: text.replace('\n', '\n\n', 1),
])
def test_ambiguous_changed_or_duplicate_table_records_do_not_produce_partial_success(change):
    with pytest.raises(AgyStatusError):
        parse_quota_table(change(USAGE), observed_at_utc=NOW)


@pytest.mark.parametrize('text', ['', MODEL + MODEL, 'auth required\n', MODEL.replace('\t', ' '),
                                  MODEL.replace('gemini-', '../gemini-'), MODEL + '\n', '\x1b[31m' + MODEL])
def test_selected_model_parser_rejects_errors_injection_and_ambiguous_records(text):
    with pytest.raises(AgyStatusError):
        parse_selected_model(text)


@pytest.mark.parametrize('changes', [
    {'model_exit_code': 1}, {'usage_exit_code': False},
    {'model_stderr': 'authentication required'}, {'usage_stderr': 'update warning'},
    {'model_stdout': '\ud800'}, {'usage_stdout': USAGE + 'x' * 16384},
    {'observed_at_utc': '2026-10-07T04:35:38-05:00'},
    {'executable_sha256_after': 'c1b0001989c4051a41f5104484de182db5014fa155053c8088baacf1663063bf'},
    {'executable_sha256_before': 'missing'}, {'version': '1.3.0'},
    {'executable_sha256_before': 'a' * 64, 'executable_sha256_after': 'a' * 64},
])
def test_command_errors_unsupported_versions_and_binary_drift_are_not_success(changes):
    with pytest.raises(AgyStatusError):
        summary(**changes)


@pytest.fixture
def captured_files(tmp_path):
    path = tmp_path / 'capture.json'
    capture = {'schema': 'cochem-agy-owner-status-capture/1', 'isolated_worker_account': False,
               'authentication_asserted': False, 'executable_sha256': SHA,
               'observed_at_utc': NOW, 'results': []}
    for probe, args, output in [('version', ['--version'], '1.3.1\n'), ('help', ['--help'], 'fixture help\n'),
                               ('model', ['-p', '/model'], MODEL), ('usage', ['-p', '/usage'], USAGE)]:
        row = {'probe': probe, 'argv': [r'C:\reviewed\agy.exe', *args], 'failures': [], 'exit_code': 0,
               'executable_sha256_before': SHA, 'executable_sha256_after': SHA}
        for stream, value in [('stdout', output), ('stderr', '')]:
            target = tmp_path / f'{probe}.{stream}.txt'
            target.write_bytes(value.encode('utf-8'))
            content = target.read_bytes()
            row[stream] = {'path': str(target), 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        capture['results'].append(row)
    path.write_text(json.dumps(capture), encoding='utf-8')
    spec = importlib.util.spec_from_file_location('agy_capture_parser', Path(__file__).resolve().parents[1] / 'scripts/parse_agy_status_capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, capture, module.parse_capture


def test_file_capture_verifies_bytes_and_retains_owner_only_scope(captured_files):
    path, _, parse = captured_files
    result = parse(path)
    assert result['capture_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['scope'] == 'owner_account_status_only'
    assert result['native_model_jobs_executed'] == 0
    assert result['parser_launches_processes'] is False
    assert result['subscription_verified'] is False
    assert len(result['probe_evidence']) == 4


@pytest.mark.parametrize('change', [
    lambda data: data['results'][0].pop('executable_sha256_after'),
    lambda data: data['results'][2].update(executable_sha256_before='c' * 64),
    lambda data: data['results'][3].update(failures=['timeout']),
    lambda data: data['results'][2].update(argv=[r'C:\reviewed\agy.exe', '-p', 'a model prompt']),
    lambda data: data['results'][2]['stdout'].update(path=r'C:\private\credential.json'),
    lambda data: data['results'][2]['stdout'].update(bytes=99999),
    lambda data: data['results'].__setitem__(2, copy.deepcopy(data['results'][3])),
    lambda data: data.update(isolated_worker_account=True),
    lambda data: data.update(authentication_asserted=True),
    lambda data: data.update(observed_at_utc='2026-10-07T04:35:38'),
])
def test_file_capture_rejects_unbound_mixed_prompt_redirected_and_mislabelled_evidence(captured_files, change):
    path, capture, parse = captured_files
    change(capture)
    path.write_text(json.dumps(capture), encoding='utf-8')
    with pytest.raises(ValueError):
        parse(path)


def test_file_capture_rejects_same_length_tampering(captured_files):
    path, _, parse = captured_files
    output = path.parent / 'usage.stdout.txt'
    output.write_bytes(output.read_bytes().replace(b'98%', b'99%'))
    with pytest.raises(ValueError, match='recorded digest'):
        parse(path)
