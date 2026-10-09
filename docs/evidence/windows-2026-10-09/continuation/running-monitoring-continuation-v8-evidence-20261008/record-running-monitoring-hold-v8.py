"""Record bounded, nonsecret observations without changing running deployment."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

W = Path(__file__).resolve().parent
HELD = Path(r'C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1')
CONTROLLER = Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json')
PINS = {
    HELD / 'installer-failure.json': 'b2012c0ec8bbfaaa2982020915ec693321bc858ff71f4839c19bb5ba4e5b5d0b',
    HELD / 'provisioning.json': 'beff7c79325f1c123ba7ae01aba4e810c033769063ba4aae5f9a9c5a67656e50',
    HELD / 'start-intent.json': '3800a4a09d7513d074bb3d0928f15d216d03b48bb4d86a349d3b32e952e65c1b',
    CONTROLLER: '4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3',
}


def read(path):
    raw = path.read_bytes()
    if not 0 < len(raw) <= 65536:
        raise ValueError('Metadata bound')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def main():
    fixed = []
    for path, pin in PINS.items():
        _, actual = read(path)
        if actual != pin:
            raise ValueError('Fixed metadata changed')
        fixed.append({'path': str(path), 'sha256': actual})
    value, runtime_sha = read(HELD / 'observation-runtime.json')
    expected = {'schema': 'cochem-held-supervisor-runtime/1',
                'status': 'HELD_SUPERVISOR_OBSERVATION_RUNNING', 'pid': 24608,
                'instance_id': 'a0136f033e6b4045a484cbd3c9828cad',
                'process_creation_filetime': 134359860348047610,
                'service_identity': 'SYSTEM', 'observed_controller_state': 'healthy',
                'paid_repair_enabled': False, 'component_recovery_enabled': False,
                'warden_actuation_enabled': False, 'full_srs_acceptance': False}
    if any(type(value.get(key)) is not type(item) or value[key] != item for key, item in expected.items()):
        raise ValueError('Runtime metadata differs')
    observed = time.time()
    if abs(observed - value['checked_at']) > 30:
        raise ValueError('Actual runtime is stale')
    report = {'schema': 'cochem-running-monitoring-hold-observation/1',
              'status': 'RUNNING_HELD_OBSERVER_REPORTED_HEALTHY_AFTER_FALSE_STALE_FAILURE',
              'observed_utc': datetime.now(timezone.utc).isoformat(),
              'fixed_metadata': fixed, 'runtime_sha256_at_observation': runtime_sha,
              'runtime': {**expected, 'sequence': value['sequence'], 'checked_at': value['checked_at']},
              'correct_utc_age_seconds': observed - value['checked_at'],
              'task_metadata_independently_accessible': False,
              'process_token_independently_attested_by_this_observation': False,
              'controller_health_and_mcp_independently_verified': True,
              'live_bridge_evidence': 'post-start-codex-live-bridge-20261008.json',
              'activation_receipt_present': (HELD / 'activation.json').exists(),
              'tasks_changed': 0, 'processes_started_or_stopped': 0, 'model_jobs_submitted': 0,
              'credentials_or_databases_modified': False, 'linux_results_counted_as_windows': False,
              'next': 'Attest exact already-running observer under elevated native task/process witnesses; publish missing activation receipt without restarting it'}
    path = W / 'running-monitoring-hold-observation-20261008.json'
    raw = (json.dumps(report, indent=2, allow_nan=False) + '\n').encode()
    with path.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'receipt_path': str(path),
                      'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                      'observer_sequence': value['sequence'], 'correct_utc_age_seconds': report['correct_utc_age_seconds']}))


if __name__ == '__main__':
    main()
