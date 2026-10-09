"""Record a source review; never launch the Oracle fixture or application."""
from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

WORK = Path(__file__).resolve().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE = REPO / 'docs/evidence/windows-2026-10-06/oracle-native-method-preparation-2026-10-08'
PINS = {
    'oracle-native-components-r3-v1.py': 'e32634f56bd67199cde3a7b6501030da892f540fa40e184ac0206639b847d1a0',
    'oracle-rogue-fixture-r3-v1.py': '137ffae58caee103a8025b7325f64580026ad6aefb10e95c3feba8f4494f0205',
    'test_oracle_native_components_r3_v1.py': 'efdfadfecd12db051000b900ac5d570e4e8cbf3bdb6b254462d41264f81326b7',
    'oracle-native-method-integration-reviewed-tests.xml': 'f7f3b06c2aefd7e4f8af8449af0c48a9bfe0fb091a5dc87995e4e0da5644241e',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    for name, expected in PINS.items():
        current, archived = (WORK / name).read_bytes(), (ARCHIVE / name).read_bytes()
        if current != archived or digest(current) != expected:
            raise ValueError('SOURCE_OR_ARCHIVE_CHANGED')
    suites = list(ET.fromstring((WORK / 'oracle-native-method-integration-reviewed-tests.xml').read_bytes()).iter('testsuite'))
    totals = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if totals != {'tests': 38, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise ValueError('TEST_SCOPE_CHANGED')
    tree = ast.parse((WORK / 'oracle-native-components-r3-v1.py').read_text())
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id in ('_component_core', 'Runtime') for node in ast.walk(tree)):
        raise ValueError('OPERATIONAL_ENTRY_OR_SERVICE_CONSTRUCTION')
    report = {
        'schema': 'cochem-oracle-method-root-review/1',
        'status': 'SOURCE_ONLY_REVIEW_PASSED_NOT_OPERATIONAL',
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'files': PINS,
        'test_evidence': totals,
        'test_scope': 'ordinary Windows; disposable SQLite; native boundaries inert',
        'reviewed_boundaries': [
            'Actual installed Runtime.trip and Runtime.cancel are bound to a fresh real JobStore; no Runtime constructor.',
            'Physical candidate retains parent/grandchild/Job handles and requires native empty-Job and exit evidence before durable cleanup release.',
            'Actual launcher, NativeRunner managed cleanup and Job Object ownership remain unchanged.',
            'Durable FAILED/fence advance, stale completion rejection and unrelated workflow preservation are required.',
            'Real monotonic 501 complete Oracle calls must fit within one second; no invented Oracle timestamp.',
            'Six existing worker exclusion names are retained; selected identity handoff is bounded by stopped/maintenance guards.',
            'Fixed protected fixture cannot dispatch from the ordinary preparation location; no provider invocation.',
        ],
        'operational_dependencies': [
            'Fresh protected installation and source/config/dependency custody.',
            'One-shot SYSTEM wrapper with preserved failure receipt and no automatic retry.',
            'Real daemon exclusion held through execution; task/API snapshots alone do not exclude admission.',
            'Exact R volume/startup baseline and fresh private/RAM path custody.',
            'Actual SYSTEM/native execution and evidence review, separately from ordinary fixture tests.',
        ],
        'full_service_acceptance': False,
        'physical_acceptance_verified': False,
        'worker_processes_executed_by_review': 0,
        'system_tasks_executed_by_review': 0,
        'provider_jobs_executed_by_review': 0,
        'production_state_modified': False,
        'next_action': 'Prepare protected operational wrapper for consolidated privileged setup; no owner command yet.',
    }
    target = REPO / 'docs/evidence/windows-2026-10-06/oracle-native-method-root-review-20261008.json'
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'status': report['status'], 'path': str(target), 'sha256': digest(raw)}))


if __name__ == '__main__':
    main()
