"""Archive reviewed ordinary-Windows preparation, never private deployment state."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W = Path(__file__).resolve().parent
OUT = W / 'pending-warden-recovery-v7-preparation-20261008.json'
ARCHIVE = W / 'pending-warden-recovery-v7-evidence-20261008'
PIN_FILE = 'pending-warden-v7-reviewed-pins.json'
TESTS = ['task-private-acl-v8-tests-final.xml', 'pending-warden-recovery-v7-tests-final.xml',
         'task-acl-downstream-v7-tests-final.xml']
FOLLOWUP_TESTS = ['task-acl-downstream-v7-tests-initial.xml',
                  'task-acl-downstream-v7-preview-tests-final.xml']
EVIDENCE = [
    'task-scheduler-acl-roundtrip-r3-v1-bbd9bf8061764e6ba8cdefa47fb6a149-final.json',
    'task-scheduler-acl-convert16-r3-v1-bbd9bf8061764e6ba8cdefa47fb6a149.json',
    'pending-warden-recovery-v7-preview-final.json',
    'pending-warden-launcher-v7-preview-final.json',
]
EXTRAS = [PIN_FILE, 'RETURN_SETUP_TASK_RECOVERY_V7.txt', 'record-pending-warden-recovery-v7.py',
          'prepare-pending-warden-launcher-v7.py', 'prepare-task-acl-downstream-v7.py',
          'test_task_private_acl_v8.py', 'test_pending_warden_recovery_v7.py',
          'test_task_acl_downstream_v7.py', 'test-task-scheduler-acl-roundtrip-r3-v1.ps1',
          'test-task-scheduler-acl-convert16-r3-v1.ps1', 'inspect-pending-warden-acl-v7.ps1',
          'task-private-acl-v7.ps1', 'test_task_private_acl_v7.py']


def metadata(name):
    raw = (W / name).read_bytes()
    return {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def main():
    if OUT.exists() or ARCHIVE.exists():
        raise FileExistsError('Existing preparation must be preserved')
    pins = json.loads((W / PIN_FILE).read_text(encoding='utf-8'))
    sources = []
    for name, expected in pins.items():
        row = metadata(name)
        if row['sha256'] != expected:
            raise ValueError('Reviewed source changed: ' + name)
        sources.append(row)
    suites = []
    for name in TESTS:
        root = ET.fromstring((W / name).read_bytes())
        cases = list(root.iter('testcase'))
        if not cases or any(list(case) for case in cases):
            raise ValueError('Final fixture suite is not all passing: ' + name)
        suites.append({**metadata(name), 'passed': len(cases), 'scope': 'Ordinary Windows PowerShell with inert IO/task doubles; no SYSTEM deployment Apply'})
    initial = ET.fromstring((W / FOLLOWUP_TESTS[0]).read_bytes())
    followup = ET.fromstring((W / FOLLOWUP_TESTS[1]).read_bytes())
    initial_cases = list(initial.iter('testcase'))
    followup_cases = list(followup.iter('testcase'))
    if (metadata(FOLLOWUP_TESTS[0])['sha256'] != 'a9fc684dd2e2cc16c988827f333f7dbf930db885cf488c683de854acd6a96ee1'
            or metadata(FOLLOWUP_TESTS[1])['sha256'] != '5cf4efc9adfe3b090d1aa0c08d1d6bb81d78aa821da0bfbca09ebc02d64bbdb5'
            or len(initial_cases) != 77 or sum(not list(case) for case in initial_cases) != 76
            or len(followup_cases) != 1 or any(list(case) for case in followup_cases)):
        raise ValueError('Downstream fixture follow-up provenance changed')
    suites[-1]['execution_provenance'] = {
        'initial': {**metadata(FOLLOWUP_TESTS[0]), 'passed': 76, 'fixture_invocation_failed': 1},
        'targeted_followup': {**metadata(FOLLOWUP_TESTS[1]), 'passed': 1, 'deselected': 76},
        'correction': 'Added ExecutionPolicy Bypass to the test-only PowerShell launcher invocation; production sources unchanged',
        'composed_verdict': '76 unchanged initial passing cases plus one corrected preview rerun; not one 77-case execution',
    }
    roundtrip = json.loads((W / EVIDENCE[0]).read_bytes())
    conversion = json.loads((W / EVIDENCE[1]).read_bytes())
    if (metadata(EVIDENCE[0])['sha256'] != '00bd4af0b3d05de9d072e16cd562e105f43060f78df3ea099eb0b0d6ff015bd9'
            or metadata(EVIDENCE[1])['sha256'] != 'b193ac95c4a75d76f5184afa3bdf70a2d00931822c44a84ca2bdb4219678a7ae'
            or roundtrip.get('administrator') is not False or roundtrip.get('task_run_calls') != 0
            or roundtrip.get('system_tasks_created') != 0):
        raise ValueError('Actual ordinary task API evidence changed')
    for name, expected in [(EVIDENCE[2], '907dee566af461743c7f5dadfc37ee816f8841d22a29b4ed324ed76d2d581ebe'),
                           (EVIDENCE[3], '622b594013dc7e2fa0cdeb4b2c451d16cd419162e98af5fb04c1e38b87fbcd14')]:
        if metadata(name)['sha256'] != expected:
            raise ValueError('Read-only preview changed: ' + name)
    launcher_preview = json.loads((W / EVIDENCE[3]).read_bytes())
    if (launcher_preview.get('source_custody_verified') is not True
            or any(launcher_preview.get(key) != 0 for key in
                   ('child_processes_started', 'tasks_changed', 'model_jobs_submitted'))):
        raise ValueError('Launcher preview was not read-only')
    report = {
        'schema': 'cochem-pending-warden-recovery-preparation/1',
        'status': 'PREPARED_NOT_APPLIED', 'prepared_utc': datetime.now(timezone.utc).isoformat(),
        'host': 'AETHERDESK', 'owner_local_date': '2026-10-08',
        'owner_reported_system_authentication': {
            'evidence_source': 'Sanitized metadata from owner console output; private completion chain awaits elevated revalidation',
            'attempt': '8ba7ce50d32c4a96a3c3fd5947a6cd4b',
            'status': 'CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED',
            'profiles': {'codex': 6, 'claude': 6}, 'codex_sessions_reused': 6,
            'claude_login_commands': 6, 'model_jobs_executed': 0,
            'completed_utc': '2026-10-09T01:47:28.5235251Z',
            'codex_receipt_sha256': '0f222ee111a1dabacba5fac3444922a0e74f9e444aea16966830d7589405c630',
            'claude_receipt_sha256': '8b767e67741771b7f3044eb40a90f3f99723e89bc396ddfd434a88f5d409c0a8',
            'provider_account_identity_verified': False, 'serving_model_verified': False,
        },
        'owner_reported_pending_task': {
            'evidence_source': 'Owner executed inspect-pending-warden-acl-v7.ps1 read-only as administrator',
            'enabled': False, 'state': 1, 'instances': 0, 'last_result': 267011,
            'owner_sid': 'S-1-5-32-544', 'group_sid': 'S-1-5-32-544',
            'service_dacl_protected': False,
            'service_aces': [{'sid': 'S-1-5-18', 'mask': 2032127},
                             {'sid': 'S-1-5-32-544', 'mask': 2032127},
                             {'sid': 'S-1-5-18', 'mask': 1179785}],
            'all_ace_types_and_flags_zero': True, 'commissioning_task_present': False,
            'controller_start_reached': False,
        },
        'defect': 'TASK_CREATE alone appended principal FR to the service descriptor and omitted its P flag; old validator expected exactly two protected ACEs',
        'repair': {
            'pending_recovery': 'Bind saved authentication/original four files/never-run daemon; prove protected backing task file, preserve descriptor/XML and durable intent; one SetSecurityDescriptor(P SDDL,16); require old exact protected two-ACE guard and unchanged XML/bytes; register missing commissioning task with18 and Run once',
            'downstream': 'Unprotected exact trusted three-ACE Scheduler projection requires root task-name-bound held-file custody and independent protected two-ACE SYSTEM/Administrators backing-file ACL proof',
            'runtime_or_commissioning_python_changed': False,
            'observer_python_or_source_manifests_changed': False,
            'reauthentication_or_completed_deployment_tests_repeated': False,
            'canonical_filesystem_protection_requirement_removed': False,
        },
        'sources': sources, 'ordinary_windows_fixture_suites': suites,
        'archive_scope': 'Reviewed changed sources, selected frozen dependencies, tests and evidence; other existing fixture/deployment dependencies remain preserved in the workspace',
        'actual_ordinary_windows_api_evidence': [metadata(name) for name in EVIDENCE[:2]],
        'read_only_previews': [metadata(name) for name in EVIDENCE[2:]],
        'ordinary_fixture_tasks_created': 2, 'ordinary_fixture_tasks_preserved': True,
        'fixture_tasks_ever_run': 0, 'deployment_tasks_changed_during_preparation': 0,
        'administrator_or_system_apply_during_preparation': False,
        'credentials_modified': False, 'databases_modified': False, 'repair_budgets_modified': False,
        'ram_or_owner_startup_task_modified': False,
        'configuration_sha256': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        'worker_identities': 6, 'shared_capacity': 4, 'routing': 'Chapter 06 unchanged',
        'private_pending_state_independently_read': False, 'controller_started_by_this_preparation': False,
        'linux_results_counted_as_windows': False, 'full_srs_acceptance': False,
        'next': 'Owner runs run-pending-warden-setup-r3-v7.ps1 -Apply -Interactive once; no browser approvals expected',
    }
    names = list(dict.fromkeys([row['name'] for row in sources] + TESTS + FOLLOWUP_TESTS + EVIDENCE + EXTRAS))
    inventory = [metadata(name) for name in names]
    ARCHIVE.mkdir()
    for row in inventory:
        with (ARCHIVE / row['name']).open('xb') as stream:
            stream.write((W / row['name']).read_bytes())
        if hashlib.sha256((ARCHIVE / row['name']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Archive readback differs')
    raw = (json.dumps(report, indent=2, allow_nan=False) + '\n').encode()
    for path in (OUT, ARCHIVE / OUT.name):
        with path.open('xb') as stream:
            stream.write(raw)
    print(json.dumps({'status': report['status'], 'receipt_path': str(OUT),
                      'receipt_sha256': hashlib.sha256(raw).hexdigest(), 'deployment_applied': False}))


if __name__ == '__main__':
    main()
