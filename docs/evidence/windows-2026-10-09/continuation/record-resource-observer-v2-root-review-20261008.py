"""Record final source/custody review without registering or running an observer."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-installer-r3-v2-preparation-2026-10-08'
PREPARATION_PIN = '4ad77ded450eaadb5383551b60da9000dc37eefab732e49b4cc3b4470cfae390'
SOURCE_PIN = 'b2f1329893ce13662271070a36821cb7129410bc7d5160ce37d17b9552f55f52'
DEPENDENCY_PIN = 'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429'
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def main():
    preparation_raw = (ARCHIVE / 'preparation.json').read_bytes()
    if sha(preparation_raw) != PREPARATION_PIN:
        raise ValueError('PREPARATION_CHANGED')
    preparation = json.loads(preparation_raw)
    for row in preparation['files']:
        raw = (WORK / row['path']).read_bytes()
        if len(raw) != row['bytes'] or sha(raw) != row['sha256'] or raw != (ARCHIVE / row['path']).read_bytes():
            raise ValueError('SOURCE_OR_ARCHIVE_CHANGED')
    xml_proofs = []
    for name, expected in (('resource-observer-r3-v2-python-final.xml', 58),
                           ('resource-observer-r3-v2-powershell-final.xml', 46)):
        suites = list(ET.fromstring((WORK / name).read_bytes()).iter('testsuite'))
        totals = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
                  for key in ('tests', 'failures', 'errors', 'skipped')}
        if totals != {'tests': expected, 'failures': 0, 'errors': 0, 'skipped': 0}:
            raise ValueError('TEST_SCOPE_CHANGED')
        xml_proofs.append({'path': name, 'sha256': sha((WORK / name).read_bytes()), **totals})
    source_raw = (WORK / 'resource-observer-r3-v2/source-manifest.json').read_bytes()
    dependency_raw = (WORK / 'resource-observer-r3-v2-dependencies.json').read_bytes()
    if sha(source_raw) != SOURCE_PIN or sha(dependency_raw) != DEPENDENCY_PIN:
        raise ValueError('MANIFEST_CHANGED')
    dependency = json.loads(dependency_raw)
    dependency_count = 0
    for key, root_key, expected in (('base_files', 'base_root', 2777), ('psutil_files', 'psutil_root', 11)):
        if len(dependency[key]) != expected:
            raise ValueError('DEPENDENCY_SCOPE_CHANGED')
        for row in dependency[key]:
            raw = (Path(dependency[root_key]) / row['relative']).read_bytes()
            if len(raw) != row['length'] or sha(raw) != row['sha256']:
                raise ValueError('CURRENT_DEPENDENCY_BYTES_CHANGED')
            dependency_count += 1
    frozen = {
        'run-pipeline-commissioning-r3-v1.ps1': '44ed0b7d79b33afee6e35b36ddf2d37056e1e51f31806f05a21c2038c98d9bf1',
        'commission-first-warden-r3-v1.py': 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c',
    }
    if any(sha((WORK / name).read_bytes()) != digest for name, digest in frozen.items()):
        raise ValueError('PUBLISHED_FIRST_START_CHANGED')
    report = {
        'schema': 'cochem-resource-observer-v2-root-review/1',
        'status': 'SOURCE_REVIEW_PASSED_PROTECTED_INSTALLATION_NOT_APPLIED',
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'preparation_sha256': PREPARATION_PIN,
        'installer_sha256': '5030df59a14fef74b1147660978becf95a839e9d6e7f9e403708ac5b319153f2',
        'source_manifest_sha256': SOURCE_PIN, 'dependency_manifest_sha256': DEPENDENCY_PIN,
        'current_noncache_dependency_byte_pins_verified': dependency_count,
        'tests': xml_proofs, 'current_distinct_tests': 104,
        'reviewed_boundaries': [
            'Independent sterile -I -S -B interpreter with exact seven-module closure, fixed psutil package and protected dependency inventory.',
            'Historical bytecode is preserved; a fresh private empty cache prefix redirects cache reads and -B prevents cache writes.',
            'New code and private state roots only; existing pipeline state, worker profiles, R:, credentials and budgets are unchanged.',
            'Registration is disabled and never-run; one explicit start has durable CreateNew intent and exact task-instance readback.',
            'Existing successful frozen first-start receipt and current exact Warden instance bind held native PID plus creation FILETIME.',
            'Source and dependency bytes/custody are validated initially and finally; drift invalidates the observation.',
            'Bounded sanitized public bootstrap/completion reports, with parent task-result failure reporting when child custody cannot be established.',
            'Restart, reboot, ambiguous start and partial output never authorize automatic reuse, PID substitution, retry or state deletion.',
        ],
        'actual_test_scope': 'Ordinary Windows native/file/import observations plus explicitly inert privileged registration/start fixtures; no SYSTEM task was registered or executed.',
        'known_limits': [
            'Full 48-hour observation has not started or completed.',
            'Genuine desktop-heap used/capacity and independent recovery-timing evidence remain unavailable.',
            'Bounded descendant snapshots do not prove all worker lifetimes or every containment outcome.',
            'This candidate binds the original reviewed r3-v1 first-start contract; a different startup path needs a separately reviewed binding.',
        ],
        'observer_installed': False, 'observer_started': False,
        'actual_48h_complete': False, 'full_srs_acceptance': False,
        'frozen_startup_unchanged': True, 'human_action_now': None,
        'next_action': 'Include the reviewed installer and explicit start in the consolidated privileged setup after actual successful controller commissioning.',
    }
    target = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-v2-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'path': str(target), 'sha256': sha(raw),
                      'current_tests': 104, 'dependency_files_verified': dependency_count}))


if __name__ == '__main__':
    main()
