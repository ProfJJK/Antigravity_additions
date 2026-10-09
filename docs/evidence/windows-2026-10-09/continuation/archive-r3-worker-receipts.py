"""Archive exact public SYSTEM receipts without opening tested targets or tasks."""
import datetime
import hashlib
import json
from pathlib import Path

PINS = [
    'b9e9fd56c77ac54f97f136b562881a554ed18f6f4f216ccacc0fdf43c51f2c85',
    '7ebac1cedc48cf60c52af7c9b5ff4e883b4325c8d92a6826c935a9e5f7b5322d',
    'c8518fb7c3f126c1c550e36072441eb520a3e76d91f75735d5f3ac832af954ee',
    'f81613cb152f9b5369908f796f55572e3f5779d2253515233b98aafc9e2b1344',
    '84d7043f2044d52e2cfd31bdd373d424821ed3e3a234cef74590b2cfb427283b',
    '5f987ba0f47d27417c0724277d70c52105dd1a96f04dd0a102fe7175d55f2970',
]
INSTALL_SHA = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
HELPER_SHA = 'b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
DEST = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06\worker-denials-r3-system-2026-10-07')

support = (Path(__file__).parent / 'worker-denial-acceptance-r3.py').read_bytes()
if hashlib.sha256(support).hexdigest() != HELPER_SHA:
    raise ValueError('Frozen receipt validator differs')
namespace = {'__name__': 'receipt_validation_only'}
exec(compile(support, 'frozen-worker-receipt-validator', 'exec'), namespace)
pending, rows = [], []
for number, pin in enumerate(PINS, 1):
    slot = f'slot{number}'
    path = Path(r'C:\Program Files\CoChem') / f'WorkerDenial4.2.7-windows-20261007-r3-{slot}' / 'worker-denial-acceptance.json'
    raw = path.read_bytes()
    if len(raw) > 32768 or hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError(f'{slot} receipt changed')
    value = json.loads(raw)
    expected = {
        'schema': 'cochem-worker-denial-acceptance/1',
        'status': 'HANDLE_DENIALS_VERIFIED', 'slot': slot,
        'system_sid': 'S-1-5-18', 'helper_sha256': HELPER_SHA,
        'install_receipt_sha256': INSTALL_SHA,
        'cleanup_verified': True, 'worker_released': True,
        'daemon_states_verified_before_and_after': True,
        'ioctls_sent': 0, 'tested_target_contents_read': 0, 'pipeline_started': False,
    }
    if any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in expected.items()):
        raise ValueError(f'{slot} receipt contract differs')
    sid = 'S-1-5-21-4108184938-3023548017-2507294638-' + str(1005 + number)
    if value['process']['token_sid'] != sid:
        raise ValueError(f'{slot} SID differs')
    _, passed = namespace['validate_child_result'](
        json.dumps(value['worker_result']).encode(), value['nonce'], slot,
        sid, value['process']['pid'])
    if not passed:
        raise ValueError(f'{slot} child handle results did not pass')
    pending.append((slot, raw))
    rows.append({'slot': slot, 'receipt_sha256': pin, 'nonce': value['nonce'],
                 'status': value['status'], 'worker_sid': sid,
                 'pid': value['process']['pid'],
                 'creation_time_filetime': value['process']['creation_time_filetime'],
                 'cleanup_verified': True, 'child_handle_results_verified': True})

DEST.mkdir(exist_ok=False)
for slot, raw in pending:
    with (DEST / f'{slot}.json').open('xb') as stream:
        stream.write(raw)
summary = {
    'schema': 'cochem-six-worker-r3-receipt-observation/1',
    'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'host': 'AETHERDESK', 'status': 'SIX_SYSTEM_RECEIPTS_AND_CHILD_RESULTS_VERIFIED',
    'scope': 'Ordinary-user read-only archival of actual SYSTEM receipts; frozen child-result validator reused without invoking its entrypoint',
    'install_receipt_sha256': INSTALL_SHA, 'helper_sha256': HELPER_SHA,
    'slots_verified': 6, 'receipts': rows,
    'protected_task_terminal_state_read_by_this_collector': False,
    'owner_batch_summary_pending_at_capture': True,
    'workers_reexecuted': 0, 'tested_targets_opened': 0,
    'credentials_accessed': False, 'pipeline_started': False,
    'linux_results_used': False, 'activation_ready': False,
}
with (DEST / 'summary.json').open('x', encoding='utf-8') as stream:
    json.dump(summary, stream, indent=2)
print(json.dumps({'path': str(DEST / 'summary.json'), 'slots_verified': 6,
                  'sha256': hashlib.sha256((DEST / 'summary.json').read_bytes()).hexdigest()}))
