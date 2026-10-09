"""Record final optional-series source/closure review without deployment."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).absolute().parent
EVIDENCE = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06')
ARCHIVE = EVIDENCE / 'protected-acceptance-setup-r3-v1-preparation-2026-10-08'
PIN = '2d3ce58608245b0b0ae08db812459a27deffd19bd475d13f24d57c86a80e8009'
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def main():
    raw = (ARCHIVE / 'preparation.json').read_bytes()
    if sha(raw) != PIN:
        raise ValueError('PREPARATION_CHANGED')
    preparation = json.loads(raw)
    for group in ('files', 'phase_dependencies', 'prior_reviews_and_preparations'):
        for row in preparation[group]:
            path = Path(row['path']);raw = path.read_bytes()
            if len(raw) != row['bytes'] or sha(raw) != row['sha256']:
                raise ValueError('COMMITMENT_CHANGED')
            if group == 'files' and raw != (ARCHIVE / path.name).read_bytes():
                raise ValueError('ARCHIVE_CHANGED')
    xml = (WORK / 'protected-acceptance-setup-r3-v1-tests-final.xml').read_bytes()
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in ET.fromstring(xml).iter('testsuite'))
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if totals != {'tests': 56, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise ValueError('TEST_SCOPE_CHANGED')
    preview = json.loads((WORK / 'protected-acceptance-setup-r3-v1-preview-final.json').read_bytes())
    if (preview['operationally_released'] is not True or preview['branch'] != 'fresh'
            or preview['oracle_deferred_maintenance'] is not True or len(preview['holds']) != 12
            or any(row['phase'] != 'first_start' or row['code'] != 'PHASE_PREFLIGHT_HOLD'
                   or 'root metadata is inaccessible in this token' not in row['detail'] for row in preview['holds'])):
        raise ValueError('PREVIEW_SCOPE_CHANGED')
    originals = {
        'run-pipeline-commissioning-r3-v1.ps1': '44ed0b7d79b33afee6e35b36ddf2d37056e1e51f31806f05a21c2038c98d9bf1',
        'commission-first-warden-r3-v1.ps1': '75efe248449fa9be0317a85feb277d82a961d05594ee754d11b3b9d0ec4dfd1e',
        'commission-first-warden-r3-v1.py': 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c',
        'RETURN_SETUP.txt': '9758ac01bc307a0e053419bcdd1dde15a8bd277bb24b2067d16476c2b1b546e2',
    }
    for name, pin in originals.items():
        if sha((WORK / name).read_bytes()) != pin:
            raise ValueError('ORIGINAL_FIRST_START_CHANGED')
    report = {
        'schema': 'cochem-protected-acceptance-root-review/1',
        'status': 'OPTIONAL_SERIES_SOURCE_REVIEW_PASSED_DEPLOYMENT_PENDING',
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'preparation_sha256': PIN,
        'runner_sha256': '0f5b7f7fd4938f49b9c364694c8a9591b61d47e40d1b338239179f74087647fc',
        'test_xml_sha256': sha(xml), 'tests': totals,
        'original_first_start_commitments': originals,
        'reviewed_boundaries': [
            'Oracle remains deferred on both branches; actual frozen lock and scratch predicates have a disposable cross-phase regression.',
            'Fresh branch invokes unchanged first-start once; commissioned branch requires current native process/task proof and never replays startup or authentication.',
            'Pinned phase helpers and receipts retain exact runtime/configuration/identity bindings; later phases stop on drift or the first failure.',
            'New public metadata uses an exclusive fresh root and fixed failure projections; partial outputs and running processes are preserved.',
            'Held monitoring has no repair/recovery authority; resource sampling startup is not completed 48-hour or desktop-heap acceptance.',
            'Six chapter identities, four shared slots, Chapter 06 routing, existing credentials, databases, budgets and R: setup remain preserved.',
        ],
        'test_scope': '56 ordinary Windows checks, including native own-process proof and disposable physical lock/path tests; privileged boundaries remain explicit inert fixtures.',
        'optional_before_first_use': True,
        'oracle_physical_acceptance': False, 'workflow_acceptance': False,
        'automatic_recovery_accepted': False, 'paid_repair_enabled': False,
        'desktop_heap_available': False, 'continuous_48h_complete': False,
        'full_srs_acceptance': False, 'apply_executed': False,
        'human_action_now': None,
    }
    target = EVIDENCE / 'protected-acceptance-setup-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'path': str(target), 'sha256': sha(raw)}))


if __name__ == '__main__':
    main()
