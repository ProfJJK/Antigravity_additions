"""Additive Windows evidence; never alters the frozen monitoring preparation."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

here = Path(__file__).absolute().parent
names = [
    'running-monitoring-continuation-v8-preparation-20261008.json',
    'running-held-observer-recovery-v1-preview-final.json',
    'wsl-clean-page-cache-release-result-20261008.json',
    'live-admission-readonly-window-20261008.json',
    'await-live-admission-readonly-r3-v1.py',
    'post-existing-root-live-readiness-safe-20261009.json',
    'prepost-journal-r3-v2-readonly-reconciliation-20261008.json',
    'continue-live-prepost-r3-v1.py',
    'live-prepost-continuation-r3-v1-tests-passed-final.xml',
    'live-prepost-continuation-r3-v1-inspection-final.json',
    'RETURN_SETUP_RUNNING_MONITOR_V8.txt',
]
evidence = {name: {'sha256': hashlib.sha256((here / name).read_bytes()).hexdigest(),
                   'bytes': (here / name).stat().st_size} for name in names}
window = json.loads((here / 'live-admission-readonly-window-20261008.json').read_text(encoding='utf-8'))
assert window['status'] == 'READINESS_NOT_ESTABLISHED'
assert window['http_post_calls'] == 0 and window['model_jobs_submitted'] == 0
assert window['private_journal_modified'] is False
assert evidence['continue-live-prepost-r3-v1.py']['sha256'] == 'ecd0ff981cff06b5abdc4ac3ceae81e55a9557c7d313c1829850d1eb33053068'
suite = ET.parse(here / 'live-prepost-continuation-r3-v1-tests-passed-final.xml').getroot().find('testsuite')
assert int(suite.attrib['tests']) == 24 and int(suite.attrib['errors']) == 0 and int(suite.attrib['failures']) == 0
result = {
    'schema': 'cochem-autonomous-windows-continuation-outcome/1',
    'status': 'MONITORING_RECOVERY_PREPARED_LIVE_ADMISSION_PAUSED',
    'host': 'AETHERDESK',
    'controller_and_held_observer_started_before_this_preparation': True,
    'clock_verification_defect_repaired_in_prepared_continuation': True,
    'monitoring_recovery_apply_performed': False,
    'resource_observer_started': False,
    'owner_action': 'Run run-running-monitoring-continuation-r3-v8.ps1 -Apply -Interactive once in Administrator Windows PowerShell',
    'owner_action_reason': 'Private task and SYSTEM process attestation, protected activation receipt publication, resource recorder task creation',
    'additional_browser_logins_required_for_monitoring': 0,
    'provider_profiles_previously_authenticated': 12,
    'live_codex_mcp_connectivity_verified': True,
    'bounded_live_readiness_status': window['status'],
    'readiness_sample_count': len(window['samples']),
    'temperature_celsius_range': [min(row['temperature_celsius'] for row in window['samples']),
                                  max(row['temperature_celsius'] for row in window['samples'])],
    'positive_capacity_samples': sum(row['admission_capacity'] > 0 for row in window['samples']),
    'live_model_jobs_submitted': 0,
    'original_live_attempt_stopped_before_post': True,
    'explicit_prepost_continuation_prepared_but_not_executed': True,
    'prepost_continuation_windows_cases_passed': 24,
    'prepost_tests_http_and_model_providers': 'inert fixtures; actual Windows ACLs and journal handles',
    'configured_shared_capacity': 4,
    'worker_identities': 6,
    'pipeline_model_routing': 'Chapter 06 unchanged',
    'existing_ram_drive_startup_credentials_databases_budgets_preserved': True,
    'temperature_or_performance_thresholds_changed': False,
    'continuous_48h_acceptance_complete': False,
    'full_srs_acceptance': False,
    'linux_results_counted_as_windows': False,
    'evidence': evidence,
}
target = here / 'autonomous-continuation-outcome-20261008.json'
with target.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(result, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps({'status': result['status'], 'path': str(target),
                  'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}, sort_keys=True))
