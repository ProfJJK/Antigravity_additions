"""One fixed authenticated health GET; persist only allowlisted numeric/enumerated metadata."""
import hashlib
import json
import math
from contextlib import ExitStack
from pathlib import Path
import re
import time
import types

HERE = Path(__file__).absolute().parent
SOURCE = HERE / 'run-live-commissioning-r3-v2.py'
OUTPUT = HERE / 'post-cache-live-admission-safe-20261008.json'
raw = SOURCE.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363'
m = types.ModuleType('fixed_live_admission_readonly')
m.__file__ = str(SOURCE)
exec(compile(raw, str(SOURCE), 'exec'), m.__dict__)

def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e12 else None

def word(value):
    return value if isinstance(value, str) and re.fullmatch('[A-Za-z_]{1,64}', value) else None

reason_codes = {
    'Warden resident memory exceeds its strict 50 MiB budget': 'CONTROLLER_RSS_ABOVE_50_MIB',
    'Warden CPU utilization exceeds its strict 5% budget': 'CONTROLLER_CPU_ABOVE_5_PERCENT',
    'Warden resident memory telemetry is unavailable': 'CONTROLLER_RSS_TELEMETRY_UNAVAILABLE',
    'Warden CPU utilization telemetry is unavailable': 'CONTROLLER_CPU_TELEMETRY_UNAVAILABLE',
}

def reason_projection(values):
    if not isinstance(values, list):
        return {'known_codes': [], 'unrecognized_count': None}
    return {'known_codes': [reason_codes[x] for x in values if isinstance(x, str) and x in reason_codes],
            'unrecognized_count': sum(not isinstance(x, str) or x not in reason_codes for x in values)}

with ExitStack() as stack:
    win, private, controller, startup_sha, _ = m.prepare_runtime(stack)
    m.require(m.ordinary(m.ROOT, missing=True) is None, 'live_attempt_root_present')
    m.ordinary(m.TOKEN)
    owner, protected, rules = win._acl(m.TOKEN)
    m.require(owner == win.SYSTEM_SID and protected and set(rules) == {
        (win.SYSTEM_SID, win.FULL_CONTROL, 0), (win.ADMIN_SID, win.FULL_CONTROL, 0),
        (private.sid, 0x120089, 0)}, 'controller_token_acl_changed')
    token_raw, _ = m.pinned(stack, m.TOKEN, maximum=256)
    token = token_raw.decode('ascii').strip()
    del token_raw
    m.require(32 <= len(token) <= 256 and re.fullmatch('[A-Za-z0-9_-]+', token), 'controller_token_format')
    client = m.BoundedClient(token)
    try:
        health = client.call('/health')
        m.health_binding(health, controller)
    finally:
        client.token = ''
        token = None
    component = health.get('components', {}).get('warden_controller', {})
    measurement = component.get('measurements', {})
    hardware = health.get('hardware', {})
    measured = hardware.get('measurements', {})
    policy = hardware.get('policy', {})
    admission = health.get('admission', {})
    observed = time.time()
    result = {
        'schema': 'cochem-live-admission-safe-readonly/1', 'recorded_at': observed,
        'controller': controller, 'startup_receipt_sha256': startup_sha,
        'admission_capacity': number(health.get('admission_capacity')),
        'controller_guard': {
            'state': word(component.get('state')), 'ready': component.get('ready') is True,
            'action': word(component.get('action')),
            'checked_at': number(component.get('checked_at')),
            'sample_age_seconds': number(observed - component['checked_at']) if number(component.get('checked_at')) is not None else None,
            'rss_mb': number(measurement.get('rss_mb')), 'private_mb': number(measurement.get('private_mb')),
            'cpu_percent': number(measurement.get('cpu_percent')),
            'allowed_logical_processor_count': len(measurement['allowed_logical_processors']) if isinstance(measurement.get('allowed_logical_processors'), list) else None,
            'memory_limit_mb': number(component.get('memory_limit_mb')),
            'cpu_limit_percent': number(component.get('cpu_limit_percent')),
            'reasons': reason_projection(component.get('reasons')),
        },
        'hardware_guard': {
            'state': word(hardware.get('state')), 'action': word(hardware.get('action')),
            'capacity': number(hardware.get('capacity')), 'measured_at': number(hardware.get('measured_at')),
            'sample_age_seconds': number(observed - hardware['measured_at']) if number(hardware.get('measured_at')) is not None else None,
            'reason_count': len(hardware['reasons']) if isinstance(hardware.get('reasons'), list) else None,
            'critical_reason_count': len(hardware['critical_reasons']) if isinstance(hardware.get('critical_reasons'), list) else None,
            'cpu': {key: number(measured.get('cpu', {}).get(key)) for key in ('percent', 'count', 'physical_count', 'temperature_celsius')},
            'cpu_temperature_available': measured.get('cpu', {}).get('temperature_available') is True,
            'memory': {key: number(measured.get('memory', {}).get(key)) for key in ('total_mb', 'available_mb')},
            'commit': {key: number(measured.get('commit', {}).get(key)) for key in ('free_mb', 'limit_mb')},
            'policy': {key: number(policy.get(key)) for key in ('cpu_temperature_pause_c', 'cpu_temperature_critical_c', 'min_free_memory_mb', 'min_free_commit_mb', 'per_agent_memory_mb')},
        },
        'admission': {'state': word(admission.get('state')), 'action': word(admission.get('action')),
                      'capacity': number(admission.get('capacity')), 'reasons': reason_projection(admission.get('reasons'))},
        'v2_root_absent': True, 'http_get_calls': 1, 'http_post_calls': 0, 'model_jobs_submitted': 0,
        'tasks_changed': 0, 'production_acl_changes': 0, 'token_contents_retained': False,
    }
    del health
with OUTPUT.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(result, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps(result, sort_keys=True))
