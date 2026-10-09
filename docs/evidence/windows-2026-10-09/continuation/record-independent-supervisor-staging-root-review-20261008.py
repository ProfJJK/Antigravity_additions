"""Record a bounded source review, without installing or opening live ledgers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE = REPO / 'docs/evidence/windows-2026-10-06/independent-supervisor-staging-preparation-2026-10-07'
PINS = {
    'install-independent-supervisor-staging-r3-v1.ps1': '4f8d3afa512ce81a73c114a384e208c39006ab3abe4771512a121ebf7ae7b098',
    'stage-independent-supervisor-holds-r3-v1.py': '215af8db414c7bbc4352c825dbf250b7bbc244a8a2dbc2d4addadc72cf1acb74',
    'independent-supervisor-staging-r3-v1.manifest.json': '6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4',
    'test_independent_supervisor_staging_r3_v1.py': 'c6344ffee0dbc182a4e93736c1a3fa8543b822a163cb4735094c825467019b17',
    'independent-supervisor-staging-r3-v1-tests-final.xml': 'fde58b710ee7c47e1f800fa16853901665541ccd03bd1cb97e91a8d837b0a653',
}
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def main():
    for name, expected in PINS.items():
        raw = (WORK / name).read_bytes()
        if sha(raw) != expected or (ARCHIVE / name).read_bytes() != raw:
            raise ValueError('SOURCE_OR_ARCHIVE_CHANGED')
    manifest = json.loads((WORK / 'independent-supervisor-staging-r3-v1.manifest.json').read_bytes())
    if (manifest['activation_authorized'] is not False or manifest['complete_source_freeze'] is not True
            or len(manifest['source_files']) != 166 or len(manifest['acceptance_files']) != 126
            or len(manifest['control_files']) != 3):
        raise ValueError('MANIFEST_SCOPE_CHANGED')
    for kind in ('source_files', 'acceptance_files', 'control_files'):
        for row in manifest[kind]:
            if kind == 'control_files':
                name = 'unresolved-budget-evidence.proposed.json' if row['relative'] == 'unresolved-budget-evidence.json' else row['relative']
                path = WORK / name
            elif row['relative'] == 'pytest.ini':
                path = WORK / 'acceptance-pytest-staging-r3-v1.ini'
            else:
                path = REPO / row['relative']
            raw = path.read_bytes()
            if sha(raw) != row['sha256'] or len(raw) != row['bytes']:
                raise ValueError('FROZEN_INPUT_CHANGED')
    suites = ET.fromstring((WORK / 'independent-supervisor-staging-r3-v1-tests-final.xml').read_bytes()).iter('testsuite')
    suites = list(suites)
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if totals != {'tests': 47, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise ValueError('TEST_SCOPE_CHANGED')
    report = {
        'schema': 'cochem-independent-supervisor-staging-root-review/1',
        'status': 'SOURCE_REVIEW_PASSED_STAGING_ONLY_NOT_OPERATIONAL',
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'files': PINS, 'tests': totals,
        'scope': 'Ordinary Windows preparation; real disposable SQLite and copy fixtures; privileged identity, ACL, task and build effects are inert.',
        'reviewed_boundaries': [
            'Fresh protected source, acceptance closure and independent frozen uv venv; no current runtime replacement.',
            'Exactly three reviewed source deltas: performance observer support, held supervision diagnosis and held replay authority.',
            'Existing daemon task XML is observed and rechecked, never modified by staging.',
            'Fresh SYSTEM-only private pair created after the code tree receives its public-read protection.',
            'Both new ledgers carry immutable unresolved authority; no historical allowance is guessed and no old ledger is opened.',
            'One no-trigger SYSTEM staging task; no worker provisioning, pipeline daemon, repair supervisor or provider invocation.',
            'Completed receipt binds nonce, manifest, helper, installed revision and paired hold hashes; bounded failure metadata preserves partial outputs.',
        ],
        'remaining_engineering': [
            'Concrete fresh ProgramData supervisor boundary, dedicated repair workspace/account validation and held observation configuration.',
            'Reviewed protected pointer and independent observation task without modifying the current Warden task or granting off-ledger restart authority.',
            'Consolidated privileged execution and actual SYSTEM observation evidence.',
        ],
        'operational_caveat': 'An early interpreter/bootstrap failure or task timeout may precede the helper receipt; the later consolidated runner must report bounded parent/task metadata without requiring private report retrieval.',
        'installed': False, 'supervisor_started': False, 'pipeline_started': False,
        'paid_repair_enabled': False, 'historical_spending_verified': False,
        'full_srs_acceptance': False, 'human_action_now': None,
    }
    target = REPO / 'docs/evidence/windows-2026-10-06/independent-supervisor-staging-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'path': str(target), 'sha256': sha(raw)}))


if __name__ == '__main__':
    main()
