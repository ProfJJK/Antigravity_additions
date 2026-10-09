"""One bounded read-only admission window; never writes the private journal."""
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import re
import time
import types

HERE = Path(__file__).absolute().parent
SOURCE = HERE / 'run-live-commissioning-r3-v2.py'
OUTPUT = HERE / 'live-admission-readonly-window-20261008.json'
raw = SOURCE.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363'
m = types.ModuleType('read_only_admission_window_v2')
m.__file__ = str(SOURCE)
exec(compile(raw, str(SOURCE), 'exec'), m.__dict__)

with ExitStack() as stack:
    m.pinned(stack, SOURCE, hashlib.sha256(raw).hexdigest())
    win, private, controller, startup_sha, _ = m.prepare_runtime(stack)
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
    deadline = time.monotonic() + 120
    rows, stable, previous, failure = [], 0, None, None
    try:
        while time.monotonic() < deadline:
            health = client.call('/health')
            m.health_binding(health, controller)
            try:
                m.health_binding(health, controller, ready=True)
                ready = True
            except m.Held as error:
                m.require(str(error) in ('controller_not_ready', 'required_component_not_healthy'), 'unexpected_readiness_failure')
                ready = False
            component = health.get('components', {}).get('warden_controller', {})
            hardware = health.get('hardware', {})
            stamps = (component.get('checked_at'), hardware.get('measured_at'))
            now = time.time()
            fresh = all(type(value) in (int, float) and math.isfinite(value) and 0 <= now-value <= 5 for value in stamps)
            advanced = previous is None or (fresh and all(a > b for a, b in zip(stamps, previous)))
            stable = stable + 1 if ready and fresh and advanced else 0
            previous = stamps if stable else None
            cpu = hardware.get('measurements', {}).get('cpu', {})
            temp = cpu.get('temperature_celsius')
            rows.append({'observed_at': now, 'admission_capacity': health.get('admission_capacity'),
                         'ready': ready, 'samples_fresh': fresh, 'consecutive_ready_samples': stable,
                         'temperature_celsius': temp if type(temp) in (int, float) and math.isfinite(temp) else None})
            if len(rows) % 4 == 1 or stable >= 3:
                print(json.dumps(rows[-1]), flush=True)
            if stable >= 3:
                break
            time.sleep(min(5, max(0, deadline-time.monotonic())))
    except Exception as error:
        failure = {'error_type': type(error).__name__,
                   'code': str(error) if isinstance(error, m.Held) else 'bounded_read_failed'}
    finally:
        client.token = ''
        token = None
    result = {'schema': 'cochem-live-readonly-admission-window/1',
              'status': 'THREE_FRESH_READY_SAMPLES' if stable >= 3 and failure is None else 'READINESS_NOT_ESTABLISHED',
              'window_seconds': 120, 'controller': controller,
              'startup_receipt_sha256': startup_sha, 'samples': rows, 'failure': failure,
              'http_get_calls': len(rows), 'http_post_calls': 0, 'model_jobs_submitted': 0,
              'private_journal_modified': False, 'tasks_or_thresholds_changed': False,
              'automatic_repeat': False}
with OUTPUT.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(result, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps({'status': result['status'], 'path': str(OUTPUT),
                  'sha256': hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}), flush=True)
