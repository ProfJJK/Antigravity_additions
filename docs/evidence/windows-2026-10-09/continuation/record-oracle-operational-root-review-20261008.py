"""Record the exact protected-wrapper source review; never run the fixture."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE = REPO / 'docs/evidence/windows-2026-10-06'
ARCHIVE = EVIDENCE / 'oracle-native-operational-preparation-2026-10-08'
PREPARATION_PIN = 'f41e600f35eb850b3660e8bb946d346ed6778b5850dfed4429567112540bf23e'
SUPPORT = {
    'worker-native-status-r3.py': ('c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5',
                                 'first-warden-commissioning-preparation-2026-10-07'),
    'inspect-execution-prerequisites-r3.py': ('17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a',
                                            'execution-prerequisites-r3-preparation-2026-10-07'),
    'check-worker-native-status-r3.ps1': ('18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7',
                                       'first-warden-commissioning-preparation-2026-10-07'),
    'register-stopped-warden-r3.ps1': ('eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198',
                                    'first-warden-commissioning-preparation-2026-10-07'),
}
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def main():
    raw = (ARCHIVE / 'preparation.json').read_bytes()
    if sha(raw) != PREPARATION_PIN:
        raise ValueError('PREPARATION_CHANGED')
    preparation = json.loads(raw)
    for row in preparation['files']:
        raw = (WORK / row['name']).read_bytes()
        if len(raw) != row['bytes'] or sha(raw) != row['sha256'] or (ARCHIVE / row['name']).read_bytes() != raw:
            raise ValueError('SOURCE_OR_ARCHIVE_CHANGED')
    for name, (expected, previous_archive) in SUPPORT.items():
        raw = (WORK / name).read_bytes()
        if sha(raw) != expected or raw != (EVIDENCE / previous_archive / name).read_bytes():
            raise ValueError('SUPPORT_SOURCE_OR_ARCHIVE_CHANGED')
    payload_source = REPO / 'scripts/stage_aetherdesk_427_payloads.ps1'
    if sha(payload_source.read_bytes()) != '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b':
        raise ValueError('PAYLOAD_SUPPORT_CHANGED')
    daemon = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\cochem_pipeline\__main__.py')
    if sha(daemon.read_bytes()) != '6d9886bfa131aa4bcf72c7debbfaae5e36117d41f5c85b17b29572a0554c8705':
        raise ValueError('INSTALLED_SERVICE_LOCK_SOURCE_CHANGED')
    xml = (WORK / 'oracle-native-operational-final-tests.xml').read_bytes()
    suites = list(ET.fromstring(xml).iter('testsuite'))
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if totals != {'tests': 69, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise ValueError('TEST_SCOPE_CHANGED')
    report = {
        'schema': 'cochem-oracle-operational-root-review/1',
        'status': 'SOURCE_REVIEW_PASSED_NATIVE_EXECUTION_PENDING',
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'preparation_sha256': PREPARATION_PIN,
        'wrapper_sha256': '196a696c4f6c5626a34b83d77ae28444edfa88aeac8274b0e69de8096987760c',
        'entry_sha256': '56e78536c2ce85e04d519b13a3347ea620bdf0ab105ed537a0cc9b4afedd9a0d',
        'test_xml_sha256': sha(xml), 'tests': totals,
        'support_closure': {name: {'sha256': value[0], 'previous_archive': value[1]}
                            for name, value in SUPPORT.items()},
        'reviewed_boundaries': [
            'Only fresh protected helper/task, private disposable SQLite and exact nested RAM fixture paths are created.',
            'Existing modern/legacy daemon launch authority must be absent or match the narrowly supported disabled, terminal, no-trigger definitions.',
            'Successful consumed first-start commissioning task is accepted only with exact protected helper, input nonce/hash, config and receipt binding.',
            'Independent metadata census plus the same maintained byte-zero lock used by the installed service excludes concurrent Runtime construction.',
            'Existing service-lock bytes/file identity/mtime/security are preserved; absent lock is CreateNew and contains the same single zero byte as production initialization.',
            'Actual installed native launcher and Job containment, Runtime trip/cancel methods and real disposable JobStore fencing are composed without constructing a full Runtime.',
            'Parent/grandchild/Job handles must prove cleanup before durable FAILED/fence release; uncertain cleanup remains held and preserves outputs.',
            'Bounded public child failure metadata and parent task-result receipts cover pre-child or missing-receipt failures without owner private-report retrieval.',
            'No task stop/delete, provider job, old database read, budget change or R: startup-task change is performed by preparation.',
        ],
        'test_scope': '69 ordinary/inert Windows checks, including actual service-lock interoperability on disposable files; no SYSTEM or isolated-worker execution.',
        'integration_constraints': [
            'Run while maintenance is safely established, before held independent supervisor activation.',
            'Never stop an already-running owner workload merely to execute this fixture.',
            'The fixture remains separate from first-start prerequisites and full-service acceptance.',
        ],
        'native_physical_acceptance_complete': False,
        'full_service_acceptance': False, 'full_srs_acceptance': False,
        'apply_executed': False, 'human_action_now': None,
        'next_action': 'Include in the consolidated maintenance-capable privileged batch, with explicit deferred status if the controller is already active.',
    }
    target = EVIDENCE / 'oracle-operational-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'path': str(target), 'sha256': sha(raw)}))


if __name__ == '__main__':
    main()
