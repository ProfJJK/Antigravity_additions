"""Record the completed one-time cache diagnostic from preserved evidence."""
import hashlib
import json
from pathlib import Path

here = Path(__file__).absolute().parent
names = (
    'wsl-clean-page-cache-release-intent-20261008.json',
    'host-memory-read-only-census-20261008.json',
    'post-start-live-admission-safe-20261008.json',
    'post-cache-live-admission-safe-20261008.json',
    'prepost-journal-r3-v2-readonly-reconciliation-20261008.json',
)
evidence = {name: {'sha256': hashlib.sha256((here / name).read_bytes()).hexdigest(),
                   'bytes': (here / name).stat().st_size} for name in names}
before = json.loads((here / names[2]).read_text(encoding='utf-8-sig'))
after = json.loads((here / names[3]).read_text(encoding='utf-8-sig'))
prepost = json.loads((here / names[4]).read_text(encoding='utf-8-sig'))
assert prepost['status'] == 'EXACT_PRE_POST_FAILURE_VERIFIED'
assert prepost['post_attempt_markers'] == 0
result = {
    'schema': 'cochem-one-time-cache-diagnostic-result/1',
    'status': 'CACHE_RELEASE_COMPLETED_WORKFLOW_NOT_SUBMITTED',
    'cache_release': {'distribution': 'Ubuntu', 'sync_exit_code': 0,
                      'drop_clean_page_cache_exit_code': 0, 'drop_caches_value': 1,
                      'automatic_repeat_allowed': False},
    'commit_free_mib_before': before['hardware_guard']['commit']['free_mb'],
    'commit_free_mib_after': after['hardware_guard']['commit']['free_mb'],
    'cpu_celsius_before': before['hardware_guard']['cpu']['temperature_celsius'],
    'cpu_celsius_after': after['hardware_guard']['cpu']['temperature_celsius'],
    'admission_at_after_sample': after['admission_capacity'],
    'later_workflow_readiness_check': 'controller_not_ready before first POST',
    'model_jobs_submitted': 0,
    'running_apps_tasks_wsl_stopped': False,
    'persistent_memory_configuration_changed': False,
    'ram_drive_or_startup_changed': False,
    'credentials_or_repair_budgets_changed': False,
    'safety_thresholds_changed': False,
    'linux_results_counted_as_windows': False,
    'evidence': evidence,
}
target = here / 'wsl-clean-page-cache-release-result-20261008.json'
with target.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(result, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'status': result['status']}, sort_keys=True))
