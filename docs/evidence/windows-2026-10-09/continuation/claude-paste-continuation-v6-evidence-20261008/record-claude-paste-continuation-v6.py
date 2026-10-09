"""Archive reviewed preparation; no protected deployment state is opened."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W = Path(__file__).resolve().parent
OUT = W / 'claude-paste-continuation-v6-preparation-20261008.json'
ARCHIVE = W / 'claude-paste-continuation-v6-evidence-20261008'
PINS = {
    'claude-session-history-v6.ps1': '36cac78f54d9a9370991df3a83bfdf36144e7e9f8ed2dabe4da3105e5edd4636',
    'claude-code-input-v6.ps1': '74c41bf1b118a62d2decae8d7d9c2ffeb2c19d977033435267f8155fac1f7bf9',
    'login_pipeline_worker_interactive_r3_v6.ps1': '837f567c5cae00d30620e313d746df6acf98259c9ffa1ef43ebc217b7cf9c46d',
    'login-six-workers-status-first-r3-v6.ps1': '9e45266bdccbaa7765245eb23091211b669c92504c374dd7ded6eab0699a4665',
    'authenticate-native-profiles-status-first-r3-v6.ps1': 'cb91573102e6e37d362d0ce80578fe1ddb8db540f6756c7f5181517f64d1e4b0',
    'run-pipeline-commissioning-r3-v6.ps1': '42cec356b787f3edd1529f8f9c81859459f932b0630494636477a0b2e1c7e8f7',
    'run-post-commissioning-setup-r3-v3.ps1': '31ad291744eda4a2a453912b64c1b969c943b3fee70b387a503def6d6cd6589d',
    'run-reboot-setup-r3-v6.ps1': 'dd74da76d96676bc0c1f3d013d7c11661bb04f760759af91c96ec04b72622041',
    'worker_claude_login_bridge_r3.py': 'b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73',
    'commission-first-warden-r3-v5.py': '8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e',
    'commission-first-warden-r3-v5.ps1': 'a40e093839f8482937a814a236b4a50775e741f30f2514c3ae9715fab371363e',
    'install-resource-observer-r3-v4.ps1': '106d95dd5b22613cb871f3649d4376dc0a75745b6fce38469ddf94d2fcb9ec65',
}
EXTRA = [
    'prepare-claude-paste-continuation-v6.py', 'test_claude_code_input_v6.py',
    'test_claude_session_history_v6.py', 'test_claude_paste_continuation_v6.py',
    'test-claude-console-native-v6.ps1', 'RETURN_SETUP_PASTE_FIX_V6.txt',
    'record-claude-paste-continuation-v6.py',
]
TESTS = [
    ('claude-session-history-v6-tests-final.xml', 190, '3b8539c8fc9445c43464c54498620869442f941f7ba746e7bee90247f4165b7b', '121 unchanged baseline paths plus 69 new cases before the additional raw-JSON type guard'),
    ('claude-session-history-v6-strict-final.xml', 114, PINS['claude-session-history-v6.ps1'], 'All affected cancellation/timeout cases, including 45 additional array coercion refusals; unchanged baseline path source preserved exactly'),
    ('claude-code-input-v6-tests-final.xml', 46, PINS['claude-code-input-v6.ps1'], 'Actual exported PS5 functions with synthetic keys, masks, disposal and bounded console seams'),
    ('claude-paste-continuation-v6-tests-final.xml', 225, PINS['login_pipeline_worker_interactive_r3_v6.ps1'], 'Active v6 source chain, actual inert input loop and unchanged authentication/first-start/launcher protocols'),
]


def metadata(name):
    raw = (W / name).read_bytes()
    result = {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    if name in PINS and result['sha256'] != PINS[name]:
        raise ValueError('Reviewed source changed: ' + name)
    return result


def main():
    if OUT.exists() or ARCHIVE.exists():
        raise FileExistsError('Preparation archive already exists; preserve it')
    evidence = ['claude-paste-continuation-v6-preview-final.json', 'reboot-setup-launcher-v6-preview-final.json',
                'claude-console-native-v6-d13e9a78ee23436f8539d4e0d453af9b.json']
    tests = []
    for name, expected, source_hash, scope in TESTS:
        cases = list(ET.fromstring((W / name).read_bytes()).iter('testcase'))
        if not cases or (expected is not None and len(cases) != expected) or any(list(case) for case in cases):
            raise ValueError('Final fixture results are not all passing: ' + name)
        tests.append({**metadata(name), 'passed': len(cases), 'source_sha256': source_hash, 'scope': scope})
    native = json.loads((W / evidence[-1]).read_bytes())
    if (native.get('status') != 'OWNED_WINDOWS_PS5_NATIVE_AND_MANAGED_CLEAR_VERIFIED'
            or native.get('helper_sha256') != PINS['claude-code-input-v6.ps1']
            or native.get('separate_owned_console_verified') is not True
            or native.get('user_console_accessed') is not False
            or metadata(evidence[-1])['sha256'] != '103b4f3e0011234bc7ea6fa0a0e3136273b69990c67b40cbdd78ee732f2f923b'):
        raise ValueError('Actual owned console evidence is not verified')
    sources = [metadata(name) for name in PINS] + [metadata(name) for name in EXTRA]
    report = {
        'schema': 'cochem-claude-paste-continuation-preparation/1',
        'prepared_utc': datetime.now(timezone.utc).isoformat(), 'host': 'AETHERDESK',
        'status': 'PREPARED_NOT_APPLIED',
        'owner_run': {'attempt': '79dc08c4325c426f8e95b59320834dfa',
                      'codex_profiles_verified': 6, 'existing_codex_sessions_reused': 6,
                      'codex_login_commands_executed': 0,
                      'codex_completed_utc': '2026-10-09T01:09:20.5281427Z',
                      'claude_slot1_cancelled_while_owner_pasted_code': True,
                      'controller_or_monitoring_start_reached': False,
                      'private_cleanup_receipt_read_by_this_preparation': False},
        'defect': 'Old wrapper intercepted individual P/Q characters while displaying the native code-paste prompt; a pasted Q requested cancellation',
        'repairs': ['Nonblocking masked SecureString input accepts ordinary P/Q; Enter submits once, Escape cancels',
                    'Native prompt split across log reads is retained/replaced; no blocked Read-Host code prompt',
                    'Current console queue is validated, drained and flushed before returning to later prompts',
                    'Prior cancelled/timeout sessions require exact protected terminal task/receipt/owned-Job cleanup proof before any new READY/login'],
        'configuration_sha256': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        'worker_identities': 6, 'shared_slots': 4, 'model_routing': 'Chapter 06 unchanged',
        'sources': sources, 'ordinary_windows_fixtures': tests,
        'history_distinct_case_coverage': 235,
        'history_provenance': '121 byte-identical baseline paths from initial run plus final 114 affected-branch cases; the 69 repeated cases are not double-counted',
        'actual_ordinary_windows_evidence': [metadata(name) for name in evidence],
        'independent_source_review': 'PASS_NO_CONCRETE_FINDINGS',
        'archive_scope': 'Changed sources, selected pinned dependencies and preparation evidence; other frozen dependencies remain in the workspace',
        'linux_results_counted_as_windows': False, 'windows_administrator_or_system_execution_during_preparation': False,
        'deployment_tasks_changed': 0, 'provider_logins_executed': 0, 'model_jobs_submitted': 0,
        'credentials_modified': False, 'databases_modified': False, 'repair_budgets_modified': False,
        'ram_or_owner_startup_task_modified': False, 'controller_started': False,
        'current_private_cleanup_verified': False, 'full_srs_acceptance': False,
        'next': 'Owner runs run-reboot-setup-r3-v6.ps1 -Apply -Interactive once; paste NEW browser code at masked Code prompt and press Enter; monitoring follows verified first start',
    }
    inventory = sources + [metadata(name) for name, *_ in TESTS] + [metadata(name) for name in evidence]
    ARCHIVE.mkdir()
    for row in inventory:
        destination = ARCHIVE / row['name']
        with destination.open('xb') as stream:
            stream.write((W / row['name']).read_bytes())
        if hashlib.sha256(destination.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Archive readback differs')
    raw = (json.dumps(report, indent=2, allow_nan=False) + '\n').encode()
    for destination in (OUT, ARCHIVE / OUT.name):
        with destination.open('xb') as stream:
            stream.write(raw)
    print(json.dumps({'status': report['status'], 'receipt_path': str(OUT),
                      'receipt_sha256': hashlib.sha256(raw).hexdigest(), 'deployment_applied': False}))


if __name__ == '__main__':
    main()
