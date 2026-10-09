"""CreateNew independent review of the inactive observer candidate only."""
from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).resolve().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
PACKAGE = WORK / 'resource-observer-r3-v1'
ARCHIVE = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-adapter-preparation-2026-10-08'
PREPARATION_SHA = '56706a636aed34f84be5d92fae7107ec54bd22234af3e0d42f58e4d4078d3e10'
MANIFEST_SHA = '82e5a766450be4a4b05092f338f58066cb5c53dab1ef905282158ee73cde3793'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if sha(ARCHIVE / 'preparation.json') != PREPARATION_SHA or sha(PACKAGE / 'source-manifest.json') != MANIFEST_SHA:
        raise RuntimeError('Reviewed preparation or manifest changed')
    manifest = json.loads((PACKAGE / 'source-manifest.json').read_bytes())
    if len(manifest['files']) != 7:
        raise RuntimeError('Reviewed seven-source scope changed')
    for row in manifest['files']:
        local = PACKAGE / row['path']
        archived = ARCHIVE / 'package' / row['path']
        if sha(local) != row['sha256'] or sha(archived) != row['sha256'] or local.stat().st_size != row['size']:
            raise RuntimeError('Reviewed observer source changed')
    tree = ast.parse((PACKAGE / 'observe_resources.py').read_bytes())
    released = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'INSTALLATION_CONTRACT_RELEASED'
                        for target in node.targets) and isinstance(node.value, ast.Constant)]
    if released != [False]:
        raise RuntimeError('The candidate entry is no longer explicitly inactive')
    tests = ARCHIVE / 'resource-observer-r3-preparation-tests.xml'
    xml = ET.fromstring(tests.read_bytes())
    counts = {name: sum(int(suite.attrib[name]) for suite in xml.findall('testsuite'))
              for name in ('tests', 'failures', 'errors', 'skipped')}
    if counts != {'tests': 49, 'failures': 0, 'errors': 0, 'skipped': 0} or len(list(xml.iter('testcase'))) != 49:
        raise RuntimeError('Reviewed focused observer tests changed or failed')
    result = {
        'schema': 'cochem-resource-observer-independent-review/1',
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'INACTIVE_CANDIDATE_REVIEW_PASSED',
        'reviewer': 'root, independent of adapter author',
        'preparation_sha256': PREPARATION_SHA,
        'manifest_sha256': MANIFEST_SHA,
        'verified_sources': manifest['files'],
        'tests': dict(counts, xml_sha256=sha(tests)),
        'review_findings_resolved': [
            'Fresh timestamps cannot conceal a heartbeat sequence stalled for more than 15 monotonic seconds.',
            'The retained native controller handle is rechecked after descendant and queue collection.',
            'A final native call exceeding the descendant collection budget records its actual elapsed time and cannot mark the census complete.',
            'Failures before the copied performance loop catch retain a fixed-code CreateNew outer receipt; retries and existing outputs are refused.',
        ],
        'reviewed_boundaries': [
            'Seven exact source files plus their manifest; fixed protected future entry, Python, psutil and output paths.',
            'Sterile -I -S -B imports limited to standard library, exact observer package and psutil; harmless multiprocessing main alias must have exact module identity.',
            'Native query/synchronize handles only; no process termination, task changes, model/controller calls or database opens in the adapter.',
            'Root resources and bounded descendant metadata remain separate from full native-worker and desktop-heap attestation.',
            'Exact reviewed performance source remains unchanged; missing heap and recovery timing keep full acceptance false even in simulated complete windows.',
        ],
        'remaining_gates': [
            'Protected installer, complete Python/psutil dependency custody, private output creation and task lifecycle are not released.',
            'This candidate binds the first commissioned process instance. A reboot or restart needs a separately reviewed new attestation/window, never silent reuse or resume.',
            'Real SYSTEM execution, actual workload, 48 elapsed hours, desktop-heap acquisition and independent recovery timing remain unverified.',
        ],
        'findings': [],
        'installed_or_started': False,
        'activation_ready': False,
        'full_srs_acceptance': False,
    }
    target = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-root-review-20261008.json'
    raw = (json.dumps(result, indent=2) + '\n').encode('utf-8')
    with target.open('xb') as output:
        output.write(raw)
    print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(raw).hexdigest(), 'status': result['status']}))


if __name__ == '__main__':
    main()
