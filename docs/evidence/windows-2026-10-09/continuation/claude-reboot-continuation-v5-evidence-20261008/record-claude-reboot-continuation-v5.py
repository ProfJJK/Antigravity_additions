"""Record final preparation evidence only; never inspect private deployment state."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W = Path(__file__).resolve().parent
OUT = W / 'claude-reboot-continuation-v5-preparation-20261008.json'
ARCHIVE = W / 'claude-reboot-continuation-v5-evidence-20261008'
PINS = {
    'claude-session-history-v5.ps1': '82f66a4c135454ad406ad47cc8b6abce25fdad55951dbd6dc70f30a0c68634d4',
    'commission-first-warden-r3-v5.py': '8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e',
    'login_pipeline_worker_interactive_r3_v5.ps1': '8e3545109c2bbef9a53763f8af8575ac1ff7376d3bea6c27a5ecd8454af182ec',
    'login-six-workers-status-first-r3-v5.ps1': '6e18cf7d8a816dc36464593fdb9eaed51c7bd45963accb16481befa67df10d58',
    'authenticate-native-profiles-status-first-r3-v5.ps1': '89e6efcad31ff05f94aea905abb15db73b5c85e69eb9ac3508b2b33d03c91d04',
    'commission-first-warden-r3-v5.ps1': 'a40e093839f8482937a814a236b4a50775e741f30f2514c3ae9715fab371363e',
    'run-pipeline-commissioning-r3-v5.ps1': '46f2f8ce1d2f30ccf25bb37e9b6f97decd34a45e07ecdd3cc3f61c6544e210c0',
    'run-reboot-setup-r3-v5.ps1': '99c3a415bf3f2b1acb1a7103aae5bec440fb9b2c6549f7590a1626e3c5b693cc',
    'install-resource-observer-r3-v4.ps1': '106d95dd5b22613cb871f3649d4376dc0a75745b6fce38469ddf94d2fcb9ec65',
    'run-post-commissioning-setup-r3-v2.ps1': 'dd2b8119f8f1c7a206c8ef12e6c2ef45634695fd83fe36bc8b361154044125f2',
    'resource-observer-r3-v4/source-manifest.json': '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49',
    'protected-code-inspection-v4.ps1': '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d',
    'check-worker-native-auth-status-six-r3-v4.ps1': '4448e76eaeb30f2d2905f5aa6d3e14f0cc29b3d7e703321bb3df55afcba58e81',
    'worker-native-auth-status-six-r3-v3.py': '7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1',
    'worker_claude_login_bridge_r3.py': 'b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73',
    'resource-observer-r3-v2-dependencies.json': 'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429',
}
SOURCES = list(PINS) + [
    'prepare-claude-continuation-v5.py', 'prepare-first-start-reboot-r3-v5.py',
    'prepare-post-reboot-observation-v4.py', 'test_claude_session_history_v5.py',
    'test_first_start_reboot_r3_v5.py', 'test_claude_continuation_v5.py',
    'test_post_reboot_observation_v4.py', 'test_reboot_setup_launcher_v5.py',
    'RETURN_SETUP_AFTER_REBOOT_V5.txt',
    'record-claude-reboot-continuation-v5.py',
]
TESTS = [
    ('claude-session-history-v5-tests-final.xml', 121),
    ('first-start-reboot-r3-v5-tests-final.xml', 58),
    ('claude-continuation-v5-tests-final.xml', 169),
    ('post-reboot-observation-v4-tests-final.xml', 50),
    ('reboot-setup-launcher-v5-tests-final.xml', 23),
]
EVIDENCE = ['post-crash-observation-20261008.json', 'claude-continuation-v5-preview-final.json',
            'reboot-setup-launcher-v5-preview-final.json']


def metadata(name):
    raw = (W / name).read_bytes()
    pin = hashlib.sha256(raw).hexdigest()
    if name in PINS and pin != PINS[name]:
        raise ValueError('Frozen source changed: ' + name)
    return {'name': name, 'sha256': pin, 'bytes': len(raw)}


def main():
    if OUT.exists() or ARCHIVE.exists():
        raise FileExistsError('Final preparation evidence already exists; preserve it')
    manifest_name = 'resource-observer-r3-v4/source-manifest.json'
    manifest = json.loads((W / manifest_name).read_bytes())
    payloads = [manifest_name]
    for row in manifest['files']:
        name = 'resource-observer-r3-v4/' + row['path']
        value = metadata(name)
        if value['sha256'] != row['sha256']:
            raise ValueError('Observer source manifest differs')
        payloads.append(name)
    sources = [metadata(name) for name in dict.fromkeys(SOURCES + payloads)]
    tests = []
    for name, expected in TESTS:
        root = ET.fromstring((W / name).read_bytes())
        cases = list(root.iter('testcase'))
        count = len(cases)
        if not count or (expected is not None and count != expected):
            raise ValueError('Unexpected test count: ' + name)
        if any(list(case) for case in cases):
            raise ValueError('Final fixture results are not all passing: ' + name)
        tests.append({**metadata(name), 'passed': count, 'failures': 0, 'errors': 0, 'skipped': 0,
                      'scope': 'ordinary Windows fixtures; no real provisioning or provider login'})
    report = {
        'schema': 'cochem-claude-reboot-continuation-preparation/1',
        'prepared_utc': datetime.now(timezone.utc).isoformat(),
        'host': 'AETHERDESK', 'status': 'PREPARED_NOT_APPLIED',
        'reported_failures': [
            'Claude login stopped by a directory-only earlier-session guard before new login',
            'Owner-reported crash/reboot removed the volatile dedicated RAM workspace',
        ],
        'repairs': [
            'Both Claude guards review protected terminal task/receipt evidence before READY and again before login',
            'Proved new-boot recovery retains original ledger bytes and a durable intent before one production ADOPT',
            'Future resource observer accepts the reviewed new first-start helper without replaying authentication or startup',
            'A hash-held attended launcher starts monitoring setup only after commissioning exits successfully; PAUSE and failures prevent later phases',
        ],
        'configuration_sha256': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        'configured_worker_identities': 6, 'shared_execution_slots': 4,
        'model_routing': 'Chapter 06 unchanged',
        'archive_scope': 'Changed sources, selected pinned dependencies and test evidence; older baseline dependencies remain preserved in the workspace',
        'sources': sources, 'ordinary_windows_tests': tests,
        'windows_evidence': [metadata(name) for name in EVIDENCE],
        'independent_reboot_source_review': 'PASS_NO_CONCRETE_FINDINGS',
        'independent_launcher_source_review': 'PASS_NO_CONCRETE_FINDINGS',
        'claude_prior_private_receipt_read_by_this_preparation': False,
        'current_boot_system_provisioning_verified': False,
        'current_boot_docker_physical_acceptance_verified': False,
        'linux_results_counted_as_windows_evidence': False,
        'administrator_or_system_execution_during_preparation': False,
        'deployment_tasks_changed': 0, 'provider_logins_executed': 0, 'model_jobs_submitted': 0,
        'controller_started': False, 'credentials_modified': False, 'databases_modified': False,
        'repair_budgets_modified': False, 'ram_or_startup_task_modified': False,
        'full_srs_acceptance': False,
        'owner_next_step': 'Run run-reboot-setup-r3-v5.ps1 once in Administrator Windows PowerShell with -Apply -Interactive; complete only requested provider browser approvals; monitoring setup follows verified startup in the same console',
        'after_success': 'Codex performs prepared ordinary MCP/workflow checks; completed resource/stability observation and remaining acceptance evidence are still required',
    }
    # Validate the complete final inventory before creating its fresh archive.
    inventory = sources + [metadata(name) for name, _ in TESTS] + [metadata(name) for name in EVIDENCE]
    ARCHIVE.mkdir()
    for row in inventory:
        destination = ARCHIVE / row['name']
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write((W / row['name']).read_bytes())
        if hashlib.sha256(destination.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Archive readback differs')
    raw = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    with OUT.open('xb') as stream:
        stream.write(raw)
    with (ARCHIVE / OUT.name).open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'receipt_path': str(OUT),
                      'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                      'ordinary_windows_tests_passed': sum(row['passed'] for row in tests),
                      'archive': str(ARCHIVE), 'deployment_applied': False}))


if __name__ == '__main__':
    main()
