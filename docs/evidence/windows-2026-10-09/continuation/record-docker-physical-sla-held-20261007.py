"""Archive existing public Docker acceptance evidence; no operational calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE = REPO / 'docs/evidence/windows-2026-10-06'
ROOT = Path(r'C:\Program Files\CoChem\DockerExecutionAcceptance4.2.7-windows-20261007-r3-v2')
FOUNDATION = Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2')
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
ARCHIVE = EVIDENCE / 'docker-physical-sla-held-system-2026-10-07'
RECEIPT_SHA = '2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74'
PACKET_SHA = 'a6f5798ffb22cb386fb216cff49280032f4163d5ca8275b1a413a7736ca55da2'
FOUNDATION_SHA = '9aea1e8f382ea8600cbd75d679f76acadc05da32774d9d8742cad08967ce1b41'
INSTALL_SHA = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, pin, maximum=1048576):
    with path.open('rb') as stream:
        before = path.stat()
        raw = stream.read(maximum + 1)
        after = path.stat()
    assert len(raw) <= maximum and sha(raw) == pin, path.name
    assert (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) == (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size), path.name
    return raw


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def create(name, raw):
    with (ARCHIVE / name).open('xb') as stream:
        stream.write(raw)


def main():
    raw = read(ROOT / 'docker-physical-acceptance.json', RECEIPT_SHA)
    packet_raw = read(ROOT / 'inputs.json', PACKET_SHA)
    value, packet = json.loads(raw), json.loads(packet_raw)
    foundation = json.loads(read(FOUNDATION / 'execution-foundation.json', FOUNDATION_SHA))
    foundation_packet = json.loads(read(FOUNDATION / 'inputs.json', foundation['packet_sha256']))
    install = json.loads(read(INSTALL / 'install-after.json', INSTALL_SHA))
    assert value['schema'] == 'cochem-docker-physical-acceptance/2' and value['status'] == 'DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_SLA_HELD'
    assert value['system_sid'] == 'S-1-5-18' and value['nonce'] == 'dcc1ef58803546c082b656b1baeea849'
    assert value['helper_sha256'] == '157549c5806e5486085d0d941ca619c5218e668653309ce6bd55dc75eb4a944f'
    assert value['packet_sha256'] == PACKET_SHA and value['runtime_root'] == str(INSTALL) and value['install_receipt_sha256'] == INSTALL_SHA
    assert value['foundation_receipt_sha256'] == FOUNDATION_SHA and value['revision'] == foundation['revision'] == install['verification']['revision']
    assert packet == dict(foundation_packet, schema='cochem-docker-physical-inputs/2', foundation_receipt_sha256=FOUNDATION_SHA)
    preserved = {}
    for key, folder, name in (
        ('failed_foundation_receipt_sha256', 'ExecutionFoundation4.2.7-windows-20261007-r3', 'execution-foundation.json'),
        ('failed_foundation_packet_sha256', 'ExecutionFoundation4.2.7-windows-20261007-r3', 'inputs.json'),
        ('diagnostic_receipt_sha256', 'ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1', 'foundation-diagnostic.json'),
        ('diagnostic_packet_sha256', 'ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1', 'inputs.json')):
        assert value[key] == packet[key] == foundation[key]
        read(Path(r'C:\Program Files\CoChem') / folder / name, value[key])
        preserved[key] = value[key]
    for flag in ('activation_ready', 'automatic_retry_allowed', 'chapter06_coding_workflow_tested', 'credentials_or_native_profiles_modified', 'existing_tasks_modified_or_run', 'knowledge_service_constructed_or_refreshed', 'legacy_databases_or_budgets_modified', 'ram_ensure_called', 'ram_volume_or_startup_task_modified', 'startup_sla_met'):
        assert value[flag] is False, flag
    for flag in ('all_six_worker_identities_excluded', 'cleanup_verified', 'container_creation_started', 'final_physical_census_empty', 'foundation_registry_mutation_started', 'physical_boundary_verified', 'ram_fixture_creation_started', 'ram_ledger_preserved', 'registry_owner_preserved'):
        assert value[flag] is True, flag
    assert value['prepared_count'] == value['maximum_owned_observed'] == 2 and value['shared_capacity'] == 4
    assert value['native_model_jobs_executed'] == value['native_occupancy'] == 0 and 'failure' not in value
    assert value['final_registry_counts'] == {'active': 0, 'owned': 0, 'preparing': 0, 'published_capacity': 4, 'quarantined': 0, 'unknown_owned': 0, 'warm': 0}
    assert [row['phase'] for row in value['executions']] == ['red', 'green']
    assert len({row['container_id_sha256'] for row in value['executions']}) == 2
    for row, failed in zip(value['executions'], (True, False)):
        assert row['expected_test_failure'] is failed and row['failures'] == int(failed) and row['tests'] == 2
        assert row['input_and_output_source_verified'] is True and row['cleanup_verified'] is True and row['startup_sla_met'] is False
        assert type(row['startup_seconds']) is float and row['startup_seconds'] > 0 and row['test_cycle_seconds'] > 0
    prep_raw = read(EVIDENCE / 'docker-execution-r3-v2-preparation-2026-10-07/preparation.json', '30c6f7d6493913cfffaaf69a75f3631ee8238a9e82eb2c9515d4d66b11641832')
    preparation = json.loads(prep_raw)
    support_names = {'accept-docker-execution-r3-v2.py', 'provision-execution-foundation-r3-v2.py', 'diagnose-foundation-docker-r3-v1.py'}
    support_records = [row for row in preparation['source_records'] if row['path'] in support_names]
    for row in support_records:
        read(ROOT / row['path'], row['sha256'])
    for name, pin in [('inspect-execution-prerequisites-r3.py', '17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'), ('worker-denial-acceptance-r3.py', 'b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77')]:
        read(ROOT / name, pin)
        support_records.append({'path': name, 'sha256': pin})
    assert len(support_records) == 5
    safe = {'schema': 'cochem-docker-physical-task-result/2', 'status': value['status'], 'receipt_path': str(ROOT / 'docker-physical-acceptance.json'), 'receipt_sha256': RECEIPT_SHA,
        'last_task_result': 2, 'runtime_root': str(INSTALL), 'install_receipt_sha256': INSTALL_SHA,
        'activation_ready': False, 'automatic_retry_allowed': False, 'chapter06_coding_workflow_tested': False,
        'physical_boundary_verified': True, 'cleanup_verified': True, 'prepared_count': 2,
        'container_creation_started': True, 'ram_fixture_creation_started': True, 'foundation_registry_mutation_started': True, 'startup_sla_met': False}
    observed_path = REPO / 'config/windows/aetherdesk-427.observed-20261007.json'
    handoff_path = REPO / 'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
    old_observed, old_handoff = observed_path.read_bytes(), handoff_path.read_bytes()
    ARCHIVE.mkdir(exist_ok=False)
    files = {'docker-physical-acceptance.json': raw, 'inputs.json': packet_raw, 'safe-owner-result.json': encode(safe),
        'preparation.json': prep_raw, 'observed-before.json': old_observed, 'handoff-before.md': old_handoff, 'collector.py': Path(__file__).read_bytes()}
    for name, data in files.items():
        create(name, data)
    now = datetime.now(timezone.utc).isoformat()
    relative = ARCHIVE.relative_to(REPO).as_posix()
    measures = [{key: row[key] for key in ('phase', 'tests', 'failures', 'expected_test_failure', 'startup_seconds', 'startup_sla_met', 'test_cycle_seconds', 'cleanup_verified', 'input_and_output_source_verified')} for row in value['executions']]
    summary = {'schema': 'cochem-docker-physical-system-observation/1', 'status': value['status'], 'captured_utc': now, 'host': 'AETHERDESK',
        'receipt_sha256': RECEIPT_SHA, 'inputs_sha256': PACKET_SHA, 'safe_owner_result_sha256': sha(encode(safe)),
        'provenance': 'Exact bounded reads of public protected SYSTEM receipt/input bytes and helper hashes. Safe owner result is an allowlisted reconstruction, not a raw console capture; task result2 comes from the owner wrapper report, not current COM inspection.',
        'task': {'last_result_reported_by_owner_wrapper': 2, 'current_com_state_queried_by_collector': False},
        'bindings': {'nonce': value['nonce'], 'helper_sha256': value['helper_sha256'], 'runtime_root': str(INSTALL), 'install_receipt_sha256': INSTALL_SHA, 'foundation_receipt_sha256': FOUNDATION_SHA, 'revision': value['revision']},
        'actual_windows_measures': measures, 'physical_boundary_verified': True, 'cleanup_verified': True, 'startup_sla_met': False,
        'prepared_count': 2, 'maximum_owned_observed': 2, 'shared_capacity': 4, 'native_occupancy': 0,
        'final_physical_census_empty': True, 'final_registry_counts': value['final_registry_counts'],
        'final_counts_provenance': 'Explicit SYSTEM receipt claims at completion, not a new private registry query or current Docker census.',
        'started_at_unix_ms': value['started_at_unix_ms'], 'finished_at_unix_ms': value['finished_at_unix_ms'],
        'registry_history_preserved': True, 'registry_claim_scope': 'Acceptance intentionally recorded its own two leases/receipts/history in the previously new empty registry. No cleanup/reset by this collector.',
        'prior_receipts_and_inputs_verified_unchanged': preserved, 'protected_helper_pins_verified': support_records,
        'collector_actions': {'docker_commands': 0, 'task_calls': 0, 'private_database_reads': 0, 'private_project_reads': 0, 'credential_reads': 0, 'deployments': 0},
        'automatic_retry_allowed': False, 'pipeline_activated': False, 'native_model_jobs_executed': 0, 'chapter06_coding_workflow_tested': False,
        'next_action': 'Review startup timing hold without rerunning this fresh-only acceptance, resetting registry/history/quarantine, or activating the pipeline.',
        'linux_results_are_separate': True, 'original_bytes_preserved': True,
        'files': {name: {'sha256': sha(data), 'bytes': len(data)} for name, data in files.items()}}
    summary_raw = encode(summary)
    create('summary.json', summary_raw)
    observed = json.loads(old_observed)
    observed['status'] = 'STOPPED_PIPELINE_R3_DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_STARTUP_SLA_HELD'
    actual = {'status': value['status'], 'actual_system_receipt_read_directly': True, 'receipt_sha256': RECEIPT_SHA, 'input_packet_sha256': PACKET_SHA,
        'evidence': relative + '/summary.json', 'evidence_sha256': sha(summary_raw), 'nonce': value['nonce'],
        'helper_sha256': value['helper_sha256'], 'runtime_root': str(INSTALL), 'install_receipt_sha256': INSTALL_SHA, 'foundation_receipt_sha256': FOUNDATION_SHA,
        'physical_boundary_verified': True, 'cleanup_verified': True, 'startup_sla_met': False, 'prepared_count': 2,
        'actual_windows_measures': measures, 'shared_capacity': 4, 'final_physical_census_empty': True, 'final_registry_counts': value['final_registry_counts'],
        'final_counts_provenance': summary['final_counts_provenance'], 'owner_wrapper_reported_task_result': 2,
        'current_com_task_state_independently_verified': False, 'finished_at_unix_ms': value['finished_at_unix_ms'],
        'ram_ledger_and_registry_owner_preserved': True, 'activation_ready': False, 'automatic_retry_allowed': False, 'chapter06_coding_workflow_tested': False, 'native_model_jobs_executed': 0}
    observed['actual_windows']['docker_physical_r3_v2'] = actual
    observed['prepared_not_applied']['docker_physical_r3_v2'].update(status='OWNER_EXECUTED_PHYSICAL_CORRECTNESS_VERIFIED_STARTUP_SLA_HELD',
        owner_action_status='COMPLETED_WITH_STARTUP_SLA_HOLD', privileged_phase_executed=True, physical_acceptance_verified=False,
        physical_correctness_verified=True, cleanup_verified=True, startup_sla_met=False, actual_result_evidence=relative + '/summary.json', actual_result_sha256=sha(summary_raw))
    observed['return_instructions'].update(status='PHASE_2_CORRECTNESS_PASSED_STARTUP_SLA_HELD_DO_NOT_RERUN', current_action='Preserve the completed Docker acceptance and review startup timing. Do not rerun/reset or continue later commands while held.',
        completed_current_commands=[1], attempted_current_commands=[1, 2], held_command_number=2, next_command_number=None,
        all_later_steps_held=True, current_sequence_must_not_be_followed=True, current_guide_requires_completion_notice_update=True,
        current_guide_docker_command_must_not_be_repeated=True)
    observed['remaining'][0] = 'Actual physical Docker correctness and cleanup passed, but both startup timings failed the SLA. Preserve the receipt, registry/history, tasks and all earlier evidence; no automatic rerun/reset or activation.'
    observed['remaining'][1] = 'Diagnose startup timing from bounded preserved evidence before authorizing any fresh performance acceptance. The fixed RED/GREEN trial is not Chapter06 routed model-workflow acceptance.'
    observed['latest_owner_result_recorded_utc'] = now
    top = '''# Windows setup handoff - 7 October 2026

## Current state - Docker physical correctness passed; startup SLA held

The actual protected SYSTEM receipt returned
`DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_SLA_HELD`. The
[exact receipt, inputs and reconstructed safe owner result](evidence/windows-2026-10-06/docker-physical-sla-held-system-2026-10-07/summary.json)
are archived; receipt SHA256 is
`2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74`.

**Physical boundary checks and cleanup passed; startup timing did not.** Two
distinct single-use containers ran the fixed RED/GREEN fixtures. RED produced
its expected one failing test; GREEN passed both tests. Source verification and
cleanup passed for each. Actual Windows measurements were:

| Trial | Startup seconds | Test-cycle seconds | Startup SLA |
| --- | ---: | ---: | --- |
| RED | 2.696504831314087 | 4.562000000005355 | Failed |
| GREEN | 2.658830165863037 | 4.23499999998603 | Failed |

The receipt explicitly reports final physical census empty and zero owned,
active, preparing, warm, quarantined and unknown-owned registry counts, with
published capacity four. These are completion-time SYSTEM receipt claims, not
a fresh private database query or current Docker census. Registry ownership,
RAM ledger, R: volume/startup task, credentials and repair budgets were preserved.
The acceptance's new lease/receipt history remains in the registry.

Task result 2 comes from the owner wrapper report. This ordinary collector
verified public receipt/input bytes and bindings without querying COM task state,
opening private databases or invoking Docker. The safe owner result is an
allowlisted reconstruction, not a captured raw console transcript.

**Do not rerun acceptance, clear quarantine, reset the registry or skip the hold.**
Preserve every task/root/receipt and review startup timing before a separately
reviewed next action. The owner guide is maintained separately and its Docker
command now records an attempted held phase; later commands remain held.

The [foundation/private-project continuation](evidence/windows-2026-10-06/foundation-project-continuation-owner-success-2026-10-07/summary.json)
remains complete: six scoped RAM roots and a new four-slot registry were verified
by the directly read foundation receipt; private bare-project success is verified
by the owner wrapper (ordinary private receipt access is denied, not absent).
The original foundation failure remains preserved and its exact cause unknown.

The pipeline remains stopped. No provider sign-in, model job or Chapter 06 routed
coding workflow was tested. Six identities still share four slots and Chapter 06
routing remains unchanged. Ordinary Windows preparation tests and prior Linux
results remain separate from this actual physical correctness result. Historical
preparation tables below retain their earlier scope.

'''
    marker = b'## Actual Windows deployment evidence'
    assert old_handoff.count(marker) == 1
    new_handoff = top.replace('\n', '\r\n').encode('utf-8') + old_handoff[old_handoff.index(marker):]
    assert observed_path.read_bytes() == old_observed and handoff_path.read_bytes() == old_handoff
    observed_path.write_bytes(encode(observed))
    handoff_path.write_bytes(new_handoff)
    assert json.loads(old_observed)['observed_through_utc'] == observed['observed_through_utc']
    print(json.dumps({'archive': str(ARCHIVE), 'summary_sha256': sha(summary_raw), 'receipt_sha256': RECEIPT_SHA, 'inputs_sha256': PACKET_SHA,
        'safe_owner_result_sha256': sha(encode(safe)), 'observed_sha256': sha(observed_path.read_bytes()), 'handoff_sha256': sha(new_handoff)}))


if __name__ == '__main__':
    main()
