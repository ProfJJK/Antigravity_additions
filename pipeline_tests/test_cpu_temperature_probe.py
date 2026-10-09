"""CPU protocol counterexamples; fixture JSON is not hardware acceptance."""
import copy
import json
import os
from pathlib import Path

import pytest

from cochem_pipeline.cpu_temperature import parse_cpu_temperature, validate_probe_path, verify_probe_bundle
from cochem_pipeline.hardware_guard import HardwarePolicy, assess_resources
from cochem_pipeline.resource_telemetry import collect_resources

NONCE = 'a' * 32


def receipt():
    return {'schema': 'cochem-cpu-temperature/1', 'provider': 'LibreHardwareMonitorLib',
            'library_version': '0.9.6.0', 'nonce': NONCE, 'sampled_at_unix_ms': 1000000,
            'sensors': [{'identifier': '/intelcpu/0/temperature/0', 'name': 'P-Core #1', 'celsius': 46.5},
                        {'identifier': '/intelcpu/0/temperature/1', 'name': 'CPU Package', 'celsius': 62.0}]}


def parse(value):
    return parse_cpu_temperature(json.dumps(value).encode(), NONCE, 1000.1)


def test_hottest_identifiable_cpu_sensor_controls_decision():
    assert parse(receipt()) == 62.0
    value = receipt()
    value['sensors'][1]['name'] = 'E-Core #1'
    assert parse(value) == 62.0


@pytest.mark.parametrize('field,value', [
    ('schema', 'old'), ('provider', 'fake'), ('library_version', '0.9.4.0'),
    ('nonce', 'b'*32), ('sampled_at_unix_ms', 990000),
    ('sampled_at_unix_ms', 1002000), ('sampled_at_unix_ms', True),
    ('sampled_at_unix_ms', 10**400),
    ('sensors', []), ('sensors', '46'), ('sensors', [None]),
])
def test_invalid_or_replayed_response_is_rejected(field, value):
    result = receipt()
    result[field] = value
    with pytest.raises(ValueError):
        parse(result)


@pytest.mark.parametrize('identity', ['/gpu/0/temperature/0', '/acpi/0/temperature/0',
                                      '/intelcpu/0/voltage/1', '/intelcpu/../temperature/0'])
def test_unrelated_sensors_cannot_establish_cpu_temperature(identity):
    result = receipt()
    result['sensors'][0]['identifier'] = identity
    with pytest.raises(ValueError, match='identity'):
        parse(result)


@pytest.mark.parametrize('temperature', [None, True, '40', float('nan'), float('inf'), -51, 151, 10**400])
def test_invalid_readings_never_become_zero(temperature):
    result = receipt()
    result['sensors'][0]['celsius'] = temperature
    with pytest.raises(ValueError, match='temperature'):
        parse(result)


@pytest.mark.parametrize('name', ['Core Max', 'Core Average', 'P-Core #1 Distance to TjMax', None])
def test_headroom_and_potentially_stale_aggregates_are_not_cpu_readings(name):
    result = receipt()
    result['sensors'][0]['name'] = name
    with pytest.raises(ValueError, match='direct core/package'):
        parse(result)


def test_duplicate_sensor_and_json_keys_are_rejected():
    result = receipt()
    result['sensors'].append(copy.deepcopy(result['sensors'][0]))
    with pytest.raises(ValueError, match='duplicated'):
        parse(result)
    raw = json.dumps(receipt()).replace('"provider":', '"provider":"fake", "provider":').encode()
    with pytest.raises(ValueError, match='duplicate fields'):
        parse_cpu_temperature(raw, NONCE, 1000)


def test_output_and_sensor_count_bounds():
    with pytest.raises(ValueError, match='bound'):
        parse_cpu_temperature(b' ' * 32769, NONCE, 1000)
    result = receipt()
    result['sensors'] = result['sensors'] * 129
    with pytest.raises(ValueError, match='bounded'):
        parse(result)


@pytest.mark.parametrize('path', ['', 'probe.exe', '/usr/bin/probe', r'\\server\share\probe.exe',
                                   r'C:\x\..\probe.exe', r'C:\x\probe.exe:stream',
                                   'C:\\x\\probe.exe --flag', 'C:\\x\\a\n.exe', 12])
def test_probe_path_rejects_shell_arguments_network_and_ambiguous_paths(path):
    with pytest.raises(ValueError, match='cpu_temperature_probe'):
        validate_probe_path(path)
    with pytest.raises(ValueError, match='cpu_temperature_probe'):
        HardwarePolicy(cpu_temperature_probe=path)


def test_policy_preserves_required_temperature_with_explicit_probe():
    value = r'C:\Program Files\CoChem\Sensors\cochem-cpu-temperature.exe'
    policy = HardwarePolicy(cpu_temperature_probe=value)
    assert policy.cpu_temperature_required is True
    assert HardwarePolicy.from_dict(policy.as_dict()).cpu_temperature_probe == value
    validate_probe_path(None)


def test_unprotected_or_missing_probe_reports_unavailable_and_keeps_guard_closed(tmp_path):
    probe = r'C:\Program Files\CoChem\AbsentProbe\cochem-cpu-temperature.exe'
    result = collect_resources([tmp_path], sample_seconds=.01, cpu_temperature_probe=probe)
    assert result['cpu']['temperature_available'] is False
    assert result['cpu']['temperature_celsius'] is None
    assert result['cpu']['temperature_error']
    assessment = assess_resources(result, HardwarePolicy(cpu_temperature_probe=probe))
    assert assessment['capacity'] == 0
    assert any('temperature' in reason.lower() for reason in assessment['reasons'])
