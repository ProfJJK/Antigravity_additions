"""CreateNew preparation archive only; no deployment or task/provider calls."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import xml.etree.ElementTree as ET

W = Path(__file__).resolve().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
OUT = REPO / 'docs/evidence/windows-2026-10-06/protected-acceptance-setup-r3-v1-preparation-2026-10-08'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def main():
    names = [
        'run-protected-acceptance-setup-r3-v1.ps1',
        'test_protected_acceptance_setup_r3_v1.py',
        'PROTECTED_ACCEPTANCE_SETUP_R3.txt',
        'protected-acceptance-setup-r3-v1-tests-final.xml',
        'protected-acceptance-setup-r3-v1-preview-final.json',
        'protected-acceptance-setup-r3-v1-tests-draft.xml',
        'protected-acceptance-setup-r3-v1-tests-bindings.xml',
        'protected-acceptance-setup-r3-v1-oracle-ordering.xml',
        'run-reviewed-full-setup-r3-v1.ps1',
        'test_reviewed_full_setup_r3_v1.py',
        'reviewed-full-setup-r3-v1-tests-initial.xml',
        'reviewed-full-setup-r3-v1-tests-followup.xml',
        'reviewed-full-setup-r3-v1-tests-fixture-correction.xml',
        'reviewed-full-setup-r3-v1-preview-draft.json',
        Path(__file__).name,
    ]
    suite = ET.parse(W / 'protected-acceptance-setup-r3-v1-tests-final.xml').getroot().find('testsuite')
    counts = {k: int(suite.attrib[k]) for k in ('tests', 'failures', 'errors', 'skipped')}
    assert counts == {'tests': 56, 'failures': 0, 'errors': 0, 'skipped': 0}, counts
    preview = json.loads((W / 'protected-acceptance-setup-r3-v1-preview-final.json').read_text(encoding='utf-8-sig'))
    assert preview['schema'] == 'cochem-protected-acceptance-setup-plan/1'
    assert preview['operationally_released'] is True and preview['full_srs_acceptance'] is False
    assert preview['branch'] == 'fresh' and preview['administrator'] is False
    assert preview['oracle_deferred_maintenance'] is True
    assert len(preview['holds']) == 12
    assert all(v['phase'] == 'first_start' and v['code'] == 'PHASE_PREFLIGHT_HOLD'
        and 'Worker slot' in v['detail'] and 'metadata is inaccessible' in v['detail'] for v in preview['holds'])
    refs = [
        REPO / 'docs/evidence/windows-2026-10-06/oracle-operational-root-review-20261008.json',
        REPO / 'docs/evidence/windows-2026-10-06/held-supervisor-observation-preparation-2026-10-07/preparation.json',
        REPO / 'docs/evidence/windows-2026-10-06/resource-observer-installer-r3-v2-preparation-2026-10-08/preparation.json',
        REPO / 'docs/evidence/windows-2026-10-06/resource-observer-v2-root-review-20261008.json',
    ]
    phase_files = [
        'install-independent-supervisor-staging-r3-v1.ps1',
        'independent-supervisor-staging-r3-v1.manifest.json',
        'run-pipeline-commissioning-r3-v1.ps1',
        'commission-first-warden-r3-v1.py',
        'install-held-supervisor-observation-r3-v1.ps1',
        'held-supervisor-observation-r3-v1.py',
        'install-resource-observer-r3-v2.ps1',
        'check-oracle-native-acceptance-r3-v1.ps1',
        'run-oracle-native-acceptance-r3-v1.py',
    ]
    records = [inventory(W / name) for name in names]
    report = {
        'schema': 'cochem-protected-acceptance-setup-preparation/1',
        'prepared_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'REVIEWED_OPTIONAL_BATCH_PREPARED_NOT_EXECUTED',
        'files': records,
        'phase_dependencies': [inventory(W / name) for name in phase_files],
        'prior_reviews_and_preparations': [inventory(path) for path in refs],
        'actual_ordinary_windows_evidence': {
            'final_distinct_tests': counts,
            'junit_seconds': float(suite.attrib['time']),
            'scope': ['WinPS5.1 phase sequencing and failures', 'source-held bytes and CreateNew metadata',
                'ordinary own-process native PID/creation/image/SID witness',
                'atomic ordinary directory collision/ACL preservation',
                'strict held observation dependency and receipt binding',
                'real disposable Oracle byte lock with exact frozen first-start predicates'],
            'preview_branch': preview['branch'], 'preview_holds': preview['holds'],
            'private_system_preflight_completed': False,
            'earlier_xmls_preserved_not_additive': True,
        },
        'integration_review': {
            'reviewer': '/root/host_inspection',
            'candidate_sha256': '4186e405d8411422d32320a22dbf3a0fcfa437c4b4e2f449c4860764e28eb089',
            'outcome': 'No remaining concrete source finding.',
            'subsequent_delta': 'Root-authorized release metadata/comments only; final focused suite and preview recorded.',
            'reviewer_executed_apply_or_tests': False,
        },
        'oracle_sequencing_correction': {
            'supersedes_only_fresh_sequence_guidance_in': str(refs[0]),
            'oracle_internal_source_and_test_review_preserved': True,
            'oracle_physical_acceptance': 'PENDING',
            'both_branches': 'DEFERRED_MAINTENANCE',
            'not_global_first_start_prerequisite': True,
            'frozen_oracle_preserves': ['private/warden.lock', 'slot1/.cochem-scratch/oracle-native-acceptance-20261008-r3-v1'],
            'frozen_first_start_rejects': ['EXISTING_FIRST_START_STATE', 'SLOT1_EXPECTED_PHYSICAL_FIXTURE'],
            'no_deletion_or_startup_successor': True,
        },
        'scope': {
            'original_basic_first_start_independent_and_unchanged': True,
            'already_running_controller_restarted_or_replayed': False,
            'current_instance_must_be_native_reattested': True,
            'held_supervisor_can_repair_or_recover': False,
            'paid_repair_enabled': False,
            'legacy_budget_authority_resolved': False,
            'workflow_acceptance_completed': False,
            'desktop_heap_available': False,
            'continuous_48h_complete': False,
            'full_srs_acceptance': False,
            'actual_protected_writes_or_tasks_or_provider_calls': 0,
            'owner_command_issued': False,
            'no_automatic_retry_stop_cleanup_or_resume': True,
        },
    }
    OUT.mkdir(parents=False, exist_ok=False)
    for name in names:
        (OUT / name).open('xb').write((W / name).read_bytes())
    raw = (json.dumps(report, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    (OUT / 'preparation.json').open('xb').write(raw)
    print(json.dumps({'path': str(OUT / 'preparation.json'), 'sha256': sha(raw),
        'script': inventory(W / names[0]), 'tests': counts, 'junit_seconds': report['actual_ordinary_windows_evidence']['junit_seconds']}))


if __name__ == '__main__':
    main()
