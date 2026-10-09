"""Archive prepared observer assets/tests and a read-only preview; no Apply."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

WORK = Path(__file__).parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-installer-r3-v2-preparation-2026-10-08'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def data(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def suite(name):
    entries = ET.parse(WORK / name).getroot().findall('testsuite')
    value = {key: sum(int(s.get(key, 0)) for s in entries) for key in ('tests', 'failures', 'errors', 'skipped')}
    value['seconds'] = sum(float(s.get('time', 0)) for s in entries)
    value['path'] = name
    return value


def main():
    source = WORK / 'resource-observer-r3-v2'
    manifest = json.loads((source / 'source-manifest.json').read_bytes())
    for row in manifest['files']:
        raw = (source / row['path']).read_bytes()
        assert len(raw) == row['size'] and sha(raw) == row['sha256']
    preview = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
                              str(WORK / 'install-resource-observer-r3-v2.ps1')],
                             capture_output=True, check=True, timeout=60)
    plan = json.loads(preview.stdout)
    assert preview.stderr == b'' and plan['holds'] == ['Successful first-instance commissioning receipt is required.']
    assert plan['administrator'] is False and plan['full_dependency_custody_deferred'] is True
    save(WORK / 'resource-observer-r3-v2-install-readonly-preview.json', preview.stdout)
    tests = [suite('resource-observer-r3-v2-python-final.xml'), suite('resource-observer-r3-v2-powershell-final.xml')]
    assert [value['tests'] for value in tests] == [58, 46]
    assert all(not any(value[x] for x in ('failures', 'errors', 'skipped')) for value in tests)
    names = ['install-resource-observer-r3-v2.ps1', 'resource-observer-r3-v2-dependencies.json',
             'test_resource_observer_r3_v2.py', 'test_resource_observer_installer_r3_v2.py',
             'resource-observer-r3-v2-python-initial.xml', 'resource-observer-r3-v2-python-followup.xml',
             'resource-observer-r3-v2-python-custody.xml', 'resource-observer-r3-v2-python-final.xml',
             'resource-observer-r3-v2-powershell-initial.xml', 'resource-observer-r3-v2-powershell-final.xml',
             'resource-observer-r3-v2-install-readonly-preview.json', Path(__file__).name]
    names.extend('resource-observer-r3-v2/' + row['path'] for row in manifest['files'])
    names.append('resource-observer-r3-v2/source-manifest.json')
    ARCHIVE.mkdir(exist_ok=False)
    files = []
    for name in names:
        raw = (WORK / name).read_bytes()
        save(ARCHIVE / name, raw)
        files.append({'path': name, 'bytes': len(raw), 'sha256': sha(raw)})
    original = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-adapter-preparation-2026-10-08/preparation.json'
    review = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-root-review-20261008.json'
    value = {
        'schema': 'cochem-resource-observer-install-preparation/1',
        'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'PROTECTED_INSTALLER_AND_SINGLE_START_PREPARED_NOT_APPLIED',
        'source_manifest_sha256': sha((source / 'source-manifest.json').read_bytes()),
        'dependency_manifest_sha256': sha((WORK / 'resource-observer-r3-v2-dependencies.json').read_bytes()),
        'files': files, 'tests': tests, 'distinct_current_tests': 104,
        'historical_inactive_candidate': {'path': str(original.relative_to(REPO)), 'sha256': sha(original.read_bytes())},
        'inactive_candidate_root_review': {'path': str(review.relative_to(REPO)), 'sha256': sha(review.read_bytes())},
        'review_scope': 'Root reviewed current successor source during preparation; final exact-pin review is recorded separately. Earlier peer native/import review was of inactive candidate only.',
        'operational_interface': {
            'default': 'Read-only preview. Actual preview has only missing first-instance commissioning receipt hold.',
            'Apply_Operation_Register': 'Fresh code/private state; create disabled SYSTEM task, no process execution.',
            'Apply_Operation_RegisterAndStart': 'Same disabled registration/readback, then durable CreateNew intent and one Enable/Run with initial real sample verification.',
            'Apply_Operation_StartRegistered': 'Only exact previous successful disabled never-run registration, same source/dependency/commissioning pins and empty state; refuse all prior start/failure markers.',
            'no_owner_command_issued': True,
        },
        'custody': {
            'base_noncache_original_pins': 2777, 'psutil_files': 11,
            'initial_installer': 'Full protected base/psutil ACL, reparse and single-link census; every executable/source/dependency byte pin held before registration/start.',
            'observer_initial_and_final': 'Fresh complete dependency byte/custody verification plus exact seven-source manifest, before accepting completion.',
            'cache_policy': 'Historical .pyc bytes preserved and not executed. Fixed private empty pycache_prefix plus -B. Ordinary base-Python proof confirmed no cache writes.',
            'new_state_acl': 'Atomic CreateDirectoryW creates SYSTEM/Admin-only protected inheriting ACL before metadata bytes.',
            'source_write_locations': 'Only fresh observer code root/private sibling root. No existing pipeline/worker/R/history/budget/config updates.',
        },
        'lifecycle': {
            'task_limit': 'PT49H', 'duration_seconds': 172800, 'interval_seconds': 1,
            'triggers': 0, 'restarts': 0, 'automatic_resume': False, 'automatic_retry': False,
            'binding': 'Actual successful first-start receipt/current exact Warden task instance plus held native controller PID/creation FILETIME/SYSTEM/image proof.',
            'after_reboot_or_controller_restart': 'Existing window fails; separately reviewed new attestation and namespace required. No arbitrary current PID substitution.',
            'initial_start_wait': 'Bounded360s, task/evidence preserved on failure or timeout, never Stop/kill/retry.',
            'failure_diagnostics': 'Fixed-code CreateNew public bootstrap/final projections; parent retains sanitized bound task failure when Python cannot establish custody. Private samples unchanged.',
            'owner_supervision_required': False,
        },
        'actual_windows_evidence': [
            'Ordinary native process handles/creation/CPU/handles, SYSTEM refusal, ACL reads and protected dependency byte/custody census.',
            'Actual isolated/no-site import boundary and fresh pycache-prefix behavior without production writes.',
            'Windows PowerShell5.1 real temporary control copies and simulated task API lifecycle: disabled, no-overwrite, single-start intent, receipt/namespace drift refusal.',
            'Actual unregistered in-memory TaskDefinition accepted PT49H and Enabled=false; no RegisterTaskDefinition was invoked.',
        ],
        'not_actual_acceptance': {
            'administrator_Apply': False, 'SYSTEM_observer_execution': False, 'scheduled_task_registered': False,
            'model_or_provider_execution': False, 'controller_api_calls': 0, 'original_database_opens': 0,
            'actual_continuous_48h': False, 'native_desktop_heap_available': False, 'independent_recovery_timing_available': False,
            'full_srs_acceptance': False, 'frozen_runtime_or_published_startup_changed': False,
        },
        'remaining_gates': [
            'Successful first-instance commissioning and live matching native controller before installation/start.',
            'Independent final review of exact successor pins before inclusion in a future consolidated elevated batch.',
            'Actual protected installation and unattended observation have not occurred.',
            'Verified native desktop/session/window-station used/allocation acquisition and independent recovery-timing evidence remain engineering gaps.',
            'Bounded descendant snapshots do not independently prove every native worker lifetime or desktop-heap use.',
        ],
    }
    raw = data(value)
    save(ARCHIVE / 'preparation.json', raw)
    print(json.dumps({'archive': str(ARCHIVE), 'preparation_sha256': sha(raw), 'source_manifest_sha256': value['source_manifest_sha256'],
                      'wrapper_sha256': sha((WORK / 'install-resource-observer-r3-v2.ps1').read_bytes()),
                      'entry_sha256': sha((source / 'observe_resources.py').read_bytes()), 'tests': tests}, indent=2))


if __name__ == '__main__':
    main()
