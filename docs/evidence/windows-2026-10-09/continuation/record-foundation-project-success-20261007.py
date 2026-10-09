"""Bounded receipt/owner-result archival only; no deployment or state queries."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE = REPO / 'docs/evidence/windows-2026-10-06'
ROOT = Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2')
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PROJECT = Path(r'C:\Program Files\CoChem\ProjectImport4.2.7-windows-20261007-r3')
ATTACHMENT = Path(r'C:\Users\ansac\.codex\attachments\c52f37d0-9ad2-4c5b-a2ba-c6d72611efea\Pasted text.txt')
ARCHIVE = EVIDENCE / 'foundation-project-continuation-owner-success-2026-10-07'
FOUNDATION_SHA = '9aea1e8f382ea8600cbd75d679f76acadc05da32774d9d8742cad08967ce1b41'
PROJECT_SHA = '9e31fba6cbacb975970653a9230d9093afa0adc6993fbe6b8c98805653a26283'
PACKET_SHA = '308705b045c93e7da9fd5e9ef969b5375dd06d98c2efc3530980e82e7186f94e'
INSTALL_SHA = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
CONFIG_SHA = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, pin=None, maximum=1048576):
    with path.open('rb') as stream:
        before = path.stat()
        raw = stream.read(maximum + 1)
        after = path.stat()
    assert len(raw) <= maximum and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), path.name
    assert pin is None or sha(raw) == pin, path.name
    return raw


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def create(name, raw):
    with (ARCHIVE / name).open('xb') as stream:
        stream.write(raw)


def main():
    foundation_raw = read(ROOT / 'execution-foundation.json', FOUNDATION_SHA)
    packet_raw = read(ROOT / 'inputs.json', PACKET_SHA)
    foundation, packet = json.loads(foundation_raw), json.loads(packet_raw)
    attachment_raw = read(ATTACHMENT)
    text = attachment_raw.decode('utf-8-sig')
    offset = text.index('{')
    assert text[:offset].splitlines() == ['Running reviewed continuation phase: foundation', 'Running reviewed continuation phase: project']
    owner = json.loads(text[offset:])
    assert owner['schema'] == 'cochem-return-continuation-series/2' and owner['status'] == 'TWO_CONTINUATION_PHASES_VERIFIED'
    assert [row['phase'] for row in owner['results']] == ['foundation', 'project']
    assert owner['worker_identities'] == 6 and owner['max_shared_slots'] == 4
    for key in ('automatic_retry_allowed', 'pipeline_started', 'activation_ready'):
        assert owner[key] is False
    assert owner['provider_calls_executed'] == owner['model_jobs_executed'] == 0
    first, project = [row['report'] for row in owner['results']]
    assert first['receipt_sha256'] == FOUNDATION_SHA and first['receipt_path'] == str(ROOT / 'execution-foundation.json')
    assert first['status'] == foundation['status'] == 'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED' and first['last_task_result'] == 0
    assert project['schema'] == 'cochem-private-project-import-result/1' and project['status'] == 'PRIVATE_BARE_PROJECT_VERIFIED'
    assert project['receipt_sha256'] == PROJECT_SHA and project['receipt_path'] == str(PROJECT / 'project-import.json')
    assert project['target'] == r'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance'
    assert project['r3_install_receipt_sha256'] == INSTALL_SHA and project['config_sha256'] == CONFIG_SHA
    assert project['baseline_commit'] == 'c52a3a97eb085e6bafbbd14bd6a75f3274288530' and project['baseline_tree'] == '0e7fe3edd935e4a839d17cb99b30b08db18d8ae9'
    assert project['objects'] == 7 and project['files'] == 3 and project['git_custody_paths'] == 12 and project['private_entries'] == 14 and project['activation_ready'] is False
    project_read = {'status': 'ACCESS_DENIED', 'absent': False, 'raw_receipt_archived': False}
    try:
        read(PROJECT / 'project-import.json', PROJECT_SHA, 16384)
    except PermissionError as error:
        project_read['winerror'] = error.winerror
    else:
        raise AssertionError('Project access changed; review archival scope before proceeding')
    runtime = json.loads(read(INSTALL / 'install-after.json', INSTALL_SHA))
    config = json.loads(read(INSTALL / 'pipeline.json', CONFIG_SHA))
    assert foundation['schema'] == 'cochem-execution-foundation/2' and foundation['system_sid'] == 'S-1-5-18'
    assert foundation['runtime_root'] == str(INSTALL) and foundation['install_receipt_sha256'] == INSTALL_SHA
    assert foundation['nonce'] == 'adfb396a9a9140509ec0e06225aa37ca' and foundation['packet_sha256'] == PACKET_SHA
    assert foundation['revision'] == runtime['verification']['revision']
    assert packet['schema'] == 'cochem-execution-foundation-inputs/2' and packet['install_receipt_sha256'] == INSTALL_SHA
    assert set(packet['workers']) == {'slot' + str(n) for n in range(1, 7)} and foundation['worker_receipt_sha256'] == packet['workers']
    prior_paths = {
        'preflight_receipt_sha256': (Path(r'C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3') / 'execution-prerequisites.json', '3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'),
        'failed_foundation_receipt_sha256': (Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3') / 'execution-foundation.json', 'bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'),
        'failed_foundation_packet_sha256': (Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3') / 'inputs.json', 'a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6'),
        'diagnostic_receipt_sha256': (Path(r'C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1') / 'foundation-diagnostic.json', 'cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9'),
        'diagnostic_packet_sha256': (Path(r'C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1') / 'inputs.json', '8744b6a0b569b820404734a491dd7b1671ab2a439ccc27f7251dccbf75a7550d')}
    priors = {}
    for key, (path, pin) in prior_paths.items():
        assert foundation[key] == packet[key] == pin
        priors[key] = json.loads(read(path, pin))
    diagnostic = priors['diagnostic_receipt_sha256']
    assert packet == dict(priors['diagnostic_packet_sha256'], schema='cochem-execution-foundation-inputs/2', diagnostic_receipt_sha256=prior_paths['diagnostic_receipt_sha256'][1], diagnostic_packet_sha256=prior_paths['diagnostic_packet_sha256'][1])
    for flag in ('activation_ready', 'automatic_retry_allowed', 'credentials_or_native_profiles_modified', 'existing_tasks_modified_or_run', 'general_readiness_called', 'knowledge_service_constructed_or_refreshed', 'legacy_databases_or_budgets_modified', 'original_failure_cause_established', 'ram_volume_or_startup_task_modified'):
        assert foundation[flag] is False, flag
    for flag in ('ram_provision_started', 'registry_provision_started', 'ram_ledger_created_new', 'existing_defender_exclusions_preserved', 'partial_outputs_preserved'):
        assert foundation[flag] is True, flag
    assert 'failure' not in foundation and foundation['containers_created'] == foundation['warm_pool_created'] == foundation['native_model_jobs_executed'] == 0
    assert foundation['ram_scoped_roots_verified'] == 6 and foundation['defender_added_exact_roots_count'] == 6
    assert foundation['ram_before'] == foundation['ram_after'] == diagnostic['ram_after']
    assert foundation['docker_before'] == foundation['docker_before_registry'] == diagnostic['docker']
    assert [row['observation'] for row in foundation['docker_traces']] == ['before_ram', 'before_registry']
    for row in foundation['docker_traces']:
        trace = row['trace']
        assert trace['commands_started'] == trace['commands_returned'] == 4 and trace['retry_attempts'] == 0
        assert trace['last_stage'] == 'docker_pipe_denials_after' and trace['original_guard_decisions_unchanged'] is True
        assert len(trace['events']) == 18 and all(e['completed'] is True and 'failure' not in e for e in trace['events'])
        assert [e['stage'] for e in trace['events'] if e['kind'] == 'bounded_cli'] == ['docker_info', 'docker_image', 'docker_owner_census', 'docker_name_census']
    ledger = foundation['ram_ledger']
    assert ledger['state'] == 'READY' and ledger['lifecycle_action'] == 'ADOPT' and ledger['adopted_existing_drive'] is True
    assert ledger['workspace_root'] == r'R:\CoChem427-windows-20261007' and ledger['slots'] == sorted(packet['workers'])
    assert ledger['config'] == config['ramdisk'] and ledger['observed'] == foundation['ram_before']['volume']
    assert ledger['volume_root_metadata_preserved'] is True and ledger['backup'] is None
    registry = foundation['registry']
    assert registry['capacity'] == 4 and registry['work_rows'] == registry['unknown_owned'] == 0 and registry['registry_created_new'] is True
    assert registry['integrity_check'] == 'ok' and registry['journal_mode'] == 'wal' and registry['table_count'] == 5 and registry['database_bytes'] == 49152
    preparations = {
        'foundation-preparation.json': ('execution-foundation-r3-v2-continuation-preparation-2026-10-07/preparation.json', '5265820aa096715bc02f8a894df12a506f2f0ba4ccb07c963f769ce5eff99f83'),
        'project-preparation.json': ('disposable-project-import-preparation-2026-10-07/preparation.json', None)}
    refbytes = {name: read(EVIDENCE / rel, pin) for name, (rel, pin) in preparations.items()}
    proof = json.loads(refbytes['foundation-preparation.json'])
    source_records = []
    for row in proof['source_records']:
        if row['path'] in ('provision-execution-foundation-r3-v2.py', 'diagnose-foundation-docker-r3-v1.py', 'inspect-execution-prerequisites-r3.py', 'worker-denial-acceptance-r3.py'):
            read(ROOT / row['path'], row['sha256'])
            source_records.append(row)
    assert len(source_records) == 4 and foundation['helper_sha256'] == '620c43db688bac8fca2c7870197c92a7cb4b54f0a1c6b81bc3453ab6b2a3ff0c'
    observed_path = REPO / 'config/windows/aetherdesk-427.observed-20261007.json'
    handoff_path = REPO / 'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
    old_observed, old_handoff = observed_path.read_bytes(), handoff_path.read_bytes()
    ARCHIVE.mkdir(exist_ok=False)
    files = {'execution-foundation.json': foundation_raw, 'foundation-inputs.json': packet_raw, 'owner-attachment.txt': attachment_raw,
        'owner-result-parsed.json': encoded(owner), 'project-receipt-access.json': encoded(project_read),
        'observed-before.json': old_observed, 'handoff-before.md': old_handoff,
        'collector.py': Path(__file__).read_bytes(), **refbytes}
    for name, data in files.items():
        create(name, data)
    now = datetime.now(timezone.utc).isoformat()
    relative = ARCHIVE.relative_to(REPO).as_posix()
    summary = {'schema': 'cochem-foundation-project-owner-observation/1', 'status': owner['status'], 'captured_utc': now,
        'host': 'AETHERDESK', 'foundation_receipt_sha256': FOUNDATION_SHA, 'foundation_inputs_sha256': PACKET_SHA,
        'owner_attachment_sha256': sha(attachment_raw), 'project_owner_reported_receipt_sha256': PROJECT_SHA,
        'provenance': {'foundation': 'Direct bounded read of existing protected public SYSTEM receipt/input bytes; exact expected hashes and bindings verified.',
            'project': 'Exact raw owner attachment and parsed result. Frozen Administrator importer verified private receipt/hash, runtime/bundle and private-entry custody; ordinary direct receipt read returned AccessDenied, not absence.',
            'task_result': 'Foundation task result0 is verified by the owner wrapper and present in the exact owner result; current COM task state was not queried by this ordinary collector.',
            'private_state': 'No RAM ledger/container DB/private project contents were reopened; their values are attributed to the validated producer receipt/result.'},
        'foundation': {'nonce': foundation['nonce'], 'helper_sha256': foundation['helper_sha256'], 'runtime_root': foundation['runtime_root'],
            'install_receipt_sha256': INSTALL_SHA, 'revision': foundation['revision'], 'started_at_unix_ms': foundation['started_at_unix_ms'], 'finished_at_unix_ms': foundation['finished_at_unix_ms'],
            'scoped_ram_roots': 6, 'new_ram_ledger_sha256': foundation['ram_ledger_sha256'], 'defender_added_exact_roots_count': 6,
            'existing_defender_exclusions_preserved': True, 'registry': registry, 'trace_observations': 2, 'trace_events': 36, 'read_only_docker_commands': 8,
            'failed_events': 0, 'ram_before_after': foundation['ram_after'], 'ram_ledger': ledger, 'containers_created': 0, 'warm_pool_created': 0},
        'project': {**project, 'direct_private_receipt_read_status': project_read['status'], 'direct_private_receipt_archived': False},
        'preserved_prior_evidence': {k: {'path': str(p), 'sha256': h, 'bytes_verified_unchanged': True} for k, (p, h) in prior_paths.items()},
        'protected_foundation_sources_verified': source_records, 'original_failure_cause': 'UNKNOWN',
        'collector_scope': 'Receipt/input/owner-output and source hashes only. No deployment/task/Docker/auth/model calls, database queries, credentials, private project traversal or ACL modification.',
        'exact_integer_handling': 'Python arbitrary-precision JSON parsing; raw source receipt/input/owner bytes archived unchanged. No JavaScript Number conversion.',
        'physical_docker_acceptance_executed': False, 'pipeline_activated': False, 'native_model_jobs_executed': 0,
        'files': {name: {'sha256': sha(data), 'bytes': len(data)} for name, data in files.items()}}
    summary_raw = encoded(summary)
    create('summary.json', summary_raw)
    observed = json.loads(old_observed)
    observed['status'] = 'STOPPED_PIPELINE_R3_FOUNDATION_AND_PRIVATE_PROJECT_VERIFIED_DOCKER_PHYSICAL_PENDING'
    actual = observed['actual_windows']
    actual['execution_foundation_r3_v2'] = {**summary['foundation'], 'status': foundation['status'], 'receipt_sha256': FOUNDATION_SHA,
        'input_packet_sha256': PACKET_SHA, 'actual_system_receipt_read_directly': True, 'owner_wrapper_reported_task_result': 0,
        'current_com_task_state_independently_verified': False, 'evidence': relative + '/summary.json', 'evidence_sha256': sha(summary_raw),
        'existing_credentials_budgets_R_volume_startup_task_preserved': True, 'activation_ready': False}
    actual['private_disposable_project_r3'] = {**summary['project'], 'provenance': summary['provenance']['project'], 'evidence': relative + '/owner-result-parsed.json'}
    actual['foundation_docker_diagnostic_r3_v1'].update(foundation_continuation_pending=False, foundation_continuation_execution_pending=False)
    prepared = observed['prepared_not_applied']['foundation_project_continuation_r3_v2']
    prepared.update(status='OWNER_EXECUTED_TWO_CONTINUATION_PHASES_VERIFIED', apply_performed=True, foundation_provisioning_verified=True,
        project_import_verified=True, project_success_provenance='Owner wrapper verified private receipt; ordinary direct receipt access denied.',
        actual_result_evidence=relative + '/summary.json', actual_result_sha256=sha(summary_raw), automatic_retry_allowed=False)
    observed['prepared_not_applied']['docker_physical_r3_v2'].update(owner_action_status='PREREQUISITE_PHASES_VERIFIED_AWAITING_PHYSICAL_ACCEPTANCE', prerequisite_foundation_and_project_owner_results_verified=True)
    observed['return_instructions'].update(current_action='Foundation-v2/private-project continuation completed. Do not rerun it. Next pending phase is the reviewed physical Docker-v2 acceptance.',
        current_continuation_status='OWNER_EXECUTED_TWO_CONTINUATION_PHASES_VERIFIED', foundation_continuation_pending=False,
        foundation_continuation_execution_pending=False, completed_current_commands=[1], next_command_number=2,
        current_guide_first_command_must_not_be_repeated=True, current_guide_requires_completion_notice_update=True)
    observed['latest_owner_result_recorded_utc'] = now
    observed['remaining'][0] = 'Actual foundation-v2 and private-project continuation succeeded. Preserve its receipts/root/task and do not rerun it or the original failed/successful historical phases.'
    observed['remaining'][1] = 'Run separately reviewed Docker-v2 physical RED/GREEN acceptance next; it has not executed. Empty registry capacity4 is provisioned, six scoped RAM roots exist, and the private bare project is owner-wrapper verified. No model or daemon activation is authorized by these passes.'
    top = '''# Windows setup handoff - 7 October 2026

## Current state - foundation and private project verified; physical Docker pending

The owner returned `TWO_CONTINUATION_PHASES_VERIFIED`. The new
[exact receipt/input/owner-result archive](evidence/windows-2026-10-06/foundation-project-continuation-owner-success-2026-10-07/summary.json)
records two completed phases with distinct provenance:

- **Foundation:** the actual protected SYSTEM receipt was read directly and
  matched SHA256 `9aea1e8f382ea8600cbd75d679f76acadc05da32774d9d8742cad08967ce1b41`.
  It verifies six scoped RAM slot roots, a new private adoption ledger and a
  new empty container registry with capacity **four**, zero work rows and SQLite
  integrity OK. Six missing exact Defender exclusions were added; prior exclusions,
  the 8 GiB R: volume/root metadata and startup task were preserved. Two current
  Docker observations completed 36 events/eight read-only commands without failure.
  No containers or warm pool were created. Foundation task result 0 comes from
  the owner wrapper; this ordinary collector did not query current COM task state.
- **Private project:** the owner wrapper verified the bare project at
  `C:\\ProgramData\\CoChemPipeline427\\projects\\windows-acceptance`, seven Git
  objects and three files at baseline `c52a3a97eb085e6bafbbd14bd6a75f3274288530`.
  Its reported private receipt SHA256 is
  `9e31fba6cbacb975970653a9230d9093afa0adc6993fbe6b8c98805653a26283`.
  Direct receipt access by this ordinary process was denied, not absent. The exact
  raw owner result is archived; no private project or database contents were read.

**Do not rerun the completed continuation, old return series, original failed
foundation, successful inspection or diagnostic.** Preserve every earlier root,
task and receipt. The original foundation failure cause remains unknown.

The next pending phase is the [reviewed Docker-v2 physical acceptance](evidence/windows-2026-10-06/docker-execution-r3-v2-preparation-2026-10-07/preparation.json).
It has **not** run. Foundation/series preparation passed 108 ordinary Windows
tests; Docker-v2 preparation passed 58. Those tests remain separate from the
actual SYSTEM foundation result and from future physical Docker/model acceptance.
The owner guide is maintained separately; its first command is now completed.

The pipeline remains stopped. Six identities share four slots; all model jobs
retain Chapter 06 routing. Existing credentials, databases and repair budgets
remain preserved, and no provider sign-in/model job or daemon activation occurred.
This archival update performed only bounded evidence reads and documentation
writes. Linux results remain separate. Historical preparation tables below retain
their earlier preparation-time scope.

'''
    marker = b'## Actual Windows deployment evidence'
    assert old_handoff.count(marker) == 1
    suffix = old_handoff[old_handoff.index(marker):]
    new_handoff = top.replace('\n', '\r\n').encode('utf-8') + suffix
    assert observed_path.read_bytes() == old_observed and handoff_path.read_bytes() == old_handoff
    observed_path.write_bytes(encoded(observed))
    handoff_path.write_bytes(new_handoff)
    assert json.loads(observed_path.read_bytes())['observed_through_utc'] == json.loads(old_observed)['observed_through_utc']
    print(json.dumps({'archive': str(ARCHIVE), 'summary_sha256': sha(summary_raw), 'foundation_sha256': FOUNDATION_SHA,
        'foundation_inputs_sha256': PACKET_SHA, 'owner_attachment_sha256': sha(attachment_raw), 'project_receipt_direct_read': project_read,
        'observed_sha256': sha(observed_path.read_bytes()), 'handoff_sha256': sha(new_handoff)}))


if __name__ == '__main__':
    main()
