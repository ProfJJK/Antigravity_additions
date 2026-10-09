"""Record this review from current candidate bytes; no application execution."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).resolve().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
INSTALLED = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\cochem_supervisor')
EXPECTED = {
    'engine.py': '9ec21d808bfbb1c5dfe01a6dd41b0dc969f9fc92df4ff5de010c4890c1dddeea',
    'replay.py': 'd0a10b30e3f16d44a2906d855d46f44dc0fbad7249b8b43d6b67b98e4d94d60d',
}
STARTUP = {
    'run-pipeline-commissioning-r3-v1.ps1': '44ed0b7d79b33afee6e35b36ddf2d37056e1e51f31806f05a21c2038c98d9bf1',
    'commission-first-warden-r3-v1.ps1': '75efe248449fa9be0317a85feb277d82a961d05594ee754d11b3b9d0ec4dfd1e',
    'commission-first-warden-r3-v1.py': 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    candidates = {}
    for name, expected in EXPECTED.items():
        path = REPO / 'src/cochem_supervisor' / name
        if digest(path) != expected:
            raise RuntimeError('Candidate changed after the independent source review')
        candidates[name] = {'candidate_sha256': expected, 'installed_before_sha256': digest(INSTALLED / name)}
    for name, expected in STARTUP.items():
        if digest(WORK / name) != expected:
            raise RuntimeError('Published startup package changed')
    test_file = WORK / 'supervision-authority-candidate-tests-final.xml'
    root = ET.fromstring(test_file.read_bytes())
    suites = root.findall('testsuite')
    counts = {name: sum(int(suite.attrib[name]) for suite in suites)
              for name in ('tests', 'failures', 'errors', 'skipped')}
    if counts != {'tests': 86, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise RuntimeError('The reviewed focused Windows checks are not green')
    groups = dict(Counter(case.attrib['classname'] for case in root.iter('testcase')))
    if sum(groups.values()) != 86:
        raise RuntimeError('Test case inventory does not match the reported result')
    report = {
        'schema': 'cochem-supervision-authority-independent-review/1',
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'CANDIDATE_SOURCE_REVIEW_PASSED_NOT_INSTALLED',
        'reviewer': 'root, independent of the candidate author',
        'source': candidates,
        'source_review': [
            'Compared installed r3 and candidate source directly, independent of older intentional Git changes.',
            'Held tick reads sterile detector output and authenticated controller health, publishes the hold, then returns before lease recovery, component actions, release recovery, CLI probes, repair routing or paid reservation.',
            'Observation failures cannot fall through to an actuator; existing failure reporting remains in force.',
            'Replay checks model and component unresolved authority through stable disposable snapshots; malformed or inaccessible authority remains unknown.',
            'Historical verification and supervisor activation are explicitly false; diagnostic projection never authorizes execution.',
            'Limits, historical charges, reservation guards, routing, identities and installed runtime are unchanged.',
        ],
        'test_evidence': {'file': str(test_file), 'sha256': digest(test_file),
                          'counts': counts, 'groups': groups,
                          'scope': 'Ordinary Windows Python, disposable SQLite, fixture actuator/identity boundaries and bounded local HTTP tests; no live SYSTEM task or provider inference.'},
        'known_scope_limits': [
            'Existing authority mode=ro reads can create normal empty WAL/SHM coordination sidecars; they do not alter ledger rows or existing main database/history bytes.',
            'No protected supervisor installation, task registration, historical migration, recovery allowance or paid repair was performed.',
            'Real SYSTEM observation/recovery timing and full Windows SRS acceptance remain open.',
        ],
        'published_startup_hashes_verified_unchanged': STARTUP,
        'findings': [],
        'activation_ready': False,
        'model_jobs_executed': 0,
        'existing_legacy_databases_opened': False,
    }
    target = REPO / 'docs/evidence/windows-2026-10-06/supervision-authority-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode('utf-8')
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(raw).hexdigest(), 'status': report['status']}))


if __name__ == '__main__':
    main()
