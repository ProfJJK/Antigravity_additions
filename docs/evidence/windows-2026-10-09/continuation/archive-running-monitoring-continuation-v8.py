"""Archive reviewed receipt recovery; no task, process or model operations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W = Path(__file__).resolve().parent
OUT = W / 'running-monitoring-continuation-v8-preparation-20261008.json'
ARCHIVE = W / 'running-monitoring-continuation-v8-evidence-20261008'
PINS = {
    'resume-running-held-observer-r3-v1.ps1': 'd234514ff3b0689047ae66074d6304ab712ed3e6627628840ef3c0892bccaa7a',
    'run-running-monitoring-continuation-r3-v8.ps1': 'c3f5bc81a6ff99c8e318b3285f18db0b76f59673b39c7c12c024c93b80938119',
    'install-held-supervisor-observation-r3-v3.ps1': 'f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6',
    'run-post-commissioning-setup-r3-v4.ps1': '933aac1f6ca8ab9bf959aa4578a18284fb0aef5d57e93fc2c834da557e1e13c4',
    'install-resource-observer-r3-v5.ps1': 'ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f',
    'task-private-acl-v8.ps1': 'f48d47e33c6a8885d35199456e24f1e606c771ee215c92037686e4399e1f1c47',
}
TESTS = {
    'windows-utc-epoch-regression-tests-final.xml': 14,
    'running-held-wait-utc-tests-final.xml': 15,
    'running-held-observer-recovery-v1-tests-final.xml': 31,
    'running-monitoring-v8-tests-initial.xml': 20,
    'running-monitoring-v8-source-match-tests-final.xml': 1,
}
EXTRAS = [
    'archive-running-monitoring-continuation-v8.py', 'prepare-running-monitoring-continuation-v8.py',
    'record-running-monitoring-hold-v8.py', 'test_running_monitoring_continuation_v8.py',
    'test_resume_running_held_observer_r3_v1.py', 'test_running_held_wait_utc.py',
    'test_windows_utc_epoch_regression.py', 'RETURN_SETUP_RUNNING_MONITOR_V8.txt',
    'windows-utc-epoch-evidence-20261008.json', 'running-monitoring-hold-observation-20261008.json',
    'post-start-codex-live-bridge-20261008.json', 'post-start-ordinary-os-census-20261008.json',
    'running-held-observer-recovery-v1-tests-initial.xml',
    'running-held-wait-utc-tests-instance-fixture-initial.xml',
]


def meta(name):
    raw = (W / name).read_bytes()
    return {'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def main():
    if OUT.exists() or ARCHIVE.exists():
        raise FileExistsError('Existing preparation must be preserved')
    for name, pin in PINS.items():
        if meta(name)['sha256'] != pin:
            raise ValueError('Reviewed source changed: ' + name)
    suites = []
    for name, count in TESTS.items():
        cases = list(ET.fromstring((W / name).read_bytes()).iter('testcase'))
        if len(cases) != count or any(list(case) for case in cases):
            raise ValueError('Passing fixture verdict changed: ' + name)
        suites.append({**meta(name), 'passed': count, 'scope': 'ordinary Windows; inert deployment IO except explicitly read-only native clocks'})
    clock = json.loads((W / 'windows-utc-epoch-evidence-20261008.json').read_bytes())
    bridge = json.loads((W / 'post-start-codex-live-bridge-20261008.json').read_bytes())
    running = json.loads((W / 'running-monitoring-hold-observation-20261008.json').read_bytes())
    if (abs(clock['observed_legacy_bias_seconds'] - 21600) > 0.01
            or bridge['status'] != 'LIVE_MCP_READ_ONLY_BRIDGE_VERIFIED'
            or bridge['model_jobs_submitted'] != 0
            or running['runtime']['pid'] != 24608 or running['activation_receipt_present'] is not False):
        raise ValueError('Observed host evidence differs')
    report = {
        'schema': 'cochem-running-monitoring-continuation-preparation/1',
        'status': 'PREPARED_NOT_APPLIED', 'prepared_utc': datetime.now(timezone.utc).isoformat(),
        'owner_local_date': '2026-10-08', 'host': 'AETHERDESK',
        'observed_deployment': {'controller_running': True, 'controller_pid': 5788,
            'controller_instance_id': 'c867588c063c4362beca10358ebf79ef',
            'controller_native_first_start_scope': 'owner-reported protected commissioning receipt, separately corroborated by ordinary OS listener and live MCP',
            'controller_commissioning_sha256': '4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3',
            'held_observer_reported_running': True, 'held_observer_pid': 24608,
            'held_observer_instance_id': 'a0136f033e6b4045a484cbd3c9828cad',
            'held_observer_activation_missing': True, 'resource_observer_not_started': True,
            'ordinary_task_metadata_access': 'denied; exact elevated native task/process verification remains required'},
        'defect': 'PowerShell cast of Z epoch becomes Local DateTime; subtracting it from UtcNow introduces +21600 seconds and falsely rejects a fresh heartbeat',
        'repair': 'Correct only the imported Wait epoch expression to DateTimeOffset UTC; attest current observer/controller and two progressing heartbeats; publish missing activation without task action; new one-shot batch then starts unchanged resource recorder',
        'source_pins': PINS, 'ordinary_windows_test_runs': suites,
        'test_provenance': '31 recovery cases passed after a fixture-only exception wrapper expectation correction; 15 imported-Wait cases passed after a fixture-only variable-shadow correction; parent 20 inert-child cases and one actual-source reproduction case ran separately',
        'system_deployment_apply_during_preparation': False,
        'existing_controller_or_held_observer_restarted': False,
        'model_jobs_submitted_by_monitoring_preparation': 0,
        'live_read_only_mcp_verified': True, 'controller_get_telemetry_writes_possible': True,
        'credentials_modified_by_monitoring_preparation': False,
        'databases_modified_by_monitoring_preparation': False,
        'existing_ram_drive_or_startup_task_modified': False,
        'worker_identities': 6, 'shared_capacity': 4, 'routing': 'Chapter 06 unchanged',
        'paid_repair_enabled': False, 'component_recovery_enabled': False,
        'continuous_48h_complete': False, 'live_model_workflows_accepted_by_this_preparation': False,
        'linux_results_counted_as_windows': False, 'full_srs_acceptance': False,
        'next': 'Owner runs run-running-monitoring-continuation-r3-v8.ps1 -Apply -Interactive once; no browser authentication or earlier setup replay',
    }
    inventory = [meta(name) for name in dict.fromkeys([*PINS, *TESTS, *EXTRAS])]
    report['archive_inventory'] = inventory
    ARCHIVE.mkdir()
    for row in inventory:
        raw = (W / row['file']).read_bytes()
        with (ARCHIVE / row['file']).open('xb') as stream:
            stream.write(raw)
        if meta(row['file'])['sha256'] != row['sha256'] or hashlib.sha256((ARCHIVE / row['file']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Archive source/readback changed')
    raw = (json.dumps(report, indent=2, allow_nan=False) + '\n').encode()
    for path in (OUT, ARCHIVE / OUT.name):
        with path.open('xb') as stream:
            stream.write(raw)
    print(json.dumps({'status': report['status'], 'receipt_path': str(OUT),
                      'receipt_sha256': hashlib.sha256(raw).hexdigest(), 'deployment_applied': False}))


if __name__ == '__main__':
    main()
