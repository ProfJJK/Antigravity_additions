"""Record reviewed source and archive commitments; never apply the installer."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE = REPO / 'docs/evidence/windows-2026-10-06'
ARCHIVE = EVIDENCE / 'held-supervisor-observation-preparation-2026-10-07'
PREPARATION = 'edf540123f475bee0c4ce89e4bf5dac93d62d5d89e29d9efaa7dc6ae521bd3aa'
SUPPORT = {
    'install-independent-supervisor-staging-r3-v1.ps1': '4f8d3afa512ce81a73c114a384e208c39006ab3abe4771512a121ebf7ae7b098',
    'install-stopped-runtime-r3.ps1': '372a0fe352124e81a2d097ebe7c075369f6f20275a8a2858f78f41febbc723e4',
    'install-private-knowledge-candidate.ps1': '5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a',
    'register-stopped-warden-r3.ps1': 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198',
    'stage-independent-supervisor-holds-r3-v1.py': '215af8db414c7bbc4352c825dbf250b7bbc244a8a2dbc2d4addadc72cf1acb74',
    'independent-supervisor-staging-r3-v1.manifest.json': '6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4',
}
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def main():
    raw = (ARCHIVE / 'preparation.json').read_bytes()
    if sha(raw) != PREPARATION:
        raise ValueError('PREPARATION_CHANGED')
    preparation = json.loads(raw)
    for row in preparation['files']:
        raw = (WORK / row['name']).read_bytes()
        if len(raw) != row['bytes'] or sha(raw) != row['sha256'] or raw != (ARCHIVE / row['name']).read_bytes():
            raise ValueError('SOURCE_OR_ARCHIVE_CHANGED')
    for name, pin in SUPPORT.items():
        if sha((WORK / name).read_bytes()) != pin:
            raise ValueError('SUPPORT_CHANGED')
    if sha((REPO / 'scripts/stage_aetherdesk_427_payloads.ps1').read_bytes()) != '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b':
        raise ValueError('PAYLOAD_SUPPORT_CHANGED')
    xml = (WORK / 'held-supervisor-observation-r3-v1-tests-final.xml').read_bytes()
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in ET.fromstring(xml).iter('testsuite'))
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if totals != {'tests': 43, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise ValueError('TEST_SCOPE_CHANGED')
    preview = json.loads((WORK / 'held-supervisor-observation-r3-v1-preview-final.json').read_bytes())
    if preview['holds'] != ['STAGING_NOT_INSTALLED', 'COMMISSIONING_NOT_COMPLETE']:
        raise ValueError('PREVIEW_SCOPE_CHANGED')
    report = {
        'schema': 'cochem-held-observation-root-review/1',
        'status': 'SOURCE_REVIEW_PASSED_PROTECTED_INSTALLATION_PENDING',
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'preparation_sha256': PREPARATION,
        'source_sha256': 'd2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640',
        'wrapper_sha256': '88c285591b579a432718dc5ab383b844e597299f5372c6a6f4dd98e9efdd38e5',
        'test_xml_sha256': sha(xml), 'tests': totals, 'support_sha256': SUPPORT,
        'reviewed_boundaries': [
            'SYSTEM account/credential presence and existing token validation precede provisioning; mixed or unverified states preserve credentials.',
            'Only a fresh independent code/data/workspace namespace, exact closed held-pair copy and its own observation configuration/pointer are provisioned.',
            'Both UNKNOWN authority holds must remain; this entrypoint permanently forbids paid, component and Warden actuation even if a hold disappears.',
            'New tasks require trusted owner/DACL, exact action, disabled initial state, zero instances and no automatic retries.',
            'Returned task instance, retained native process handle, exact creation FILETIME, SYSTEM SID and protected image bind startup evidence.',
            'Atomic runtime snapshot parsing and hashing use the same bounded bytes; public failures contain fixed status metadata only.',
            'Existing credentials, legacy history, pipeline configuration, Warden tasks/pointers and R: startup remain preserved.',
        ],
        'test_scope': '43 ordinary Windows tests; real disposable SQLite/constructor logic and own-process native witness, with explicit inert privileged boundaries. No SYSTEM or isolated repair-account execution.',
        'first_use_prerequisite': False, 'oracle_acceptance_prerequisite': False,
        'paid_repair_enabled': False, 'component_recovery_enabled': False,
        'automatic_recovery_accepted': False, 'full_srs_acceptance': False,
        'apply_executed': False, 'human_action_now': None,
    }
    target = EVIDENCE / 'held-supervisor-observation-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'path': str(target), 'sha256': sha(raw)}))


if __name__ == '__main__':
    main()
