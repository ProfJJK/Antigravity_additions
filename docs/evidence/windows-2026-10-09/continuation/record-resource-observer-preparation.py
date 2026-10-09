"""CreateNew source/evidence archive only; never starts the observer."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

WORK = Path(__file__).parent
SOURCE = WORK / 'resource-observer-r3-v1'
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE = REPO / 'docs/evidence/windows-2026-10-06/resource-observer-adapter-preparation-2026-10-08'
FILES = ['observe_resources.py', 'detector_bootstrap.py', 'cochem_supervisor/__init__.py',
         'cochem_supervisor/windows.py', 'cochem_supervisor/resource_observation.py',
         'cochem_supervisor/performance_acceptance.py', 'cochem_supervisor/shared_io.py']


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def main():
    rows = []
    for name in FILES:
        raw = (SOURCE / name).read_bytes()
        rows.append({'path': name, 'size': len(raw), 'sha256': digest(raw)})
    manifest = encoded({'schema': 'cochem-external-observer-source-manifest/1', 'files': rows})
    write_new(SOURCE / 'source-manifest.json', manifest)
    preview = subprocess.run([sys.executable, '-I', '-S', '-B', str(SOURCE / 'observe_resources.py')],
                             capture_output=True, check=True, timeout=15)
    plan = json.loads(preview.stdout)
    assert plan['prepared_only'] is True and plan['full_srs_acceptance'] is False and len(plan['holds']) == 4
    assert preview.stderr == b''
    write_new(WORK / 'resource-observer-r3-readonly-preview.json', preview.stdout)
    archive_files = [(SOURCE / name, 'package/' + name) for name in FILES]
    archive_files.append((SOURCE / 'source-manifest.json', 'package/source-manifest.json'))
    for name in ('test_resource_observer_r3.py', 'resource-observer-r3-initial.xml',
                 'resource-observer-r3-followup.xml', 'resource-observer-r3-boundary.xml',
                 'resource-observer-r3-timing.xml', 'resource-observer-r3-preparation-tests.xml',
                 'resource-observer-r3-readonly-preview.json', Path(__file__).name):
        archive_files.append((WORK / name, name))
    ARCHIVE.mkdir(exist_ok=False)
    archived = []
    for source, destination in archive_files:
        raw = source.read_bytes()
        write_new(ARCHIVE / destination, raw)
        archived.append({'path': destination, 'size': len(raw), 'sha256': digest(raw)})
    suites = ET.parse(WORK / 'resource-observer-r3-preparation-tests.xml').getroot().findall('testsuite')
    tests = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    tests['seconds'] = sum(float(s.get('time', 0)) for s in suites)
    assert tests['tests'] == 49 and not any(tests[x] for x in ('failures', 'errors', 'skipped'))
    heap = REPO / 'docs/evidence/windows-2026-10-06/desktop-heap-acquisition-bounded-audit-20261008.json'
    result = {
        'schema': 'cochem-external-resource-observer-preparation/1',
        'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'INACTIVE_ADAPTER_TESTED_PROTECTED_INSTALLER_NOT_RELEASED',
        'source_manifest_sha256': digest(manifest), 'files': archived, 'tests': tests,
        'independent_review': 'Root reviewed adapter/entry source; separate final review record pending exact freeze.',
        'actual_windows_evidence': [
            'Ordinary-user query/synchronize handles: native PID creation FILETIME, handle count, CPU-time counters and exited-process refusal.',
            'Actual -I -S -B interpreter imports only standard library, exact observer package and protected psutil; pipeline/MCP/requests imports refused.',
            'Ordinary temporary-file shared reads, exact byte commitments, replacement after reader close, bounded JSON and alias refusal.',
            'Actual ordinary token and ACL query; SYSTEM enforcement refuses ordinary token.',
        ],
        'disposable_evidence': [
            'Exact copied performance loop: simulated 48h and suspension/gap failures; no actual 48h elapsed.',
            'Native/heartbeat binding mutations, 15s monotonic sequence stall, post-collection identity check, slow-final-call census budget.',
            'CreateNew failure receipt before copied loop catch, output collision/no retry, positive short adapter flow with simulated process boundary.',
            'Exact seven-source manifest, changes/extra directories/files/hardlinks/outside-path refusal and unreleased entry refusal.',
        ],
        'scope': {
            'system_execution': False, 'protected_installation': False, 'scheduled_tasks_created_or_changed': False,
            'providers_or_models_executed': False, 'controller_calls': 0, 'original_database_opens': 0,
            'frozen_runtime_or_startup_modified': False, 'real_48h_acceptance': False, 'full_srs_acceptance': False,
            'previous_failed_fixture_reports_preserved': True,
        },
        'future_read_scope': {
            'held_controller_query_handle': 'Commissioned PID/native creation FILETIME/SYSTEM/physical base-Python identity; never terminate or adjust.',
            'heartbeat': 'Private bounded atomic snapshot; instance/source/freshness/nondecreasing sequence with <=15s advancement interval.',
            'resources': 'Exact copied native root-process RSS/normalized CPU/handles each second; bounded descendant native FILETIME/handles/RSS/cumulative CPU ticks every30s.',
            'descendant_limit': '64-process and0.5s census budget; incomplete/late/disappearing processes remain incomplete, not zero.',
            'cpu_temperature': 'Allowlisted controller-published fresh sensor metadata, no duplicate probe; not independent thermal acceptance.',
            'queue': 'Committed bounded snapshot and six-identity/four-slot topology only; no job text or task creation.',
            'desktop_heap': 'Unavailable/failed. No fabricated used bytes, capacity-only substitutes or invasive exhaustion probes.',
        },
        'heap_acquisition_audit': {'path': str(heap.relative_to(REPO)), 'sha256': digest(heap.read_bytes())},
        'next_implementation': [
            'Review/freeze a fresh-only administrator installer which verifies complete protected base Python and psutil dependency trees and copies exactly this manifest into a new code root.',
            'Create the new SYSTEM/Admin-only observation parent with final private inherited ACL before any metadata bytes; preserve all existing outputs and deny repeats.',
            'Bind the actual successful commissioning receipt bytes, controller creation identity and fixed source/runtime/config pins before task registration.',
            'Register a separate single no-trigger SYSTEM task with -I -S -B, bounded49h runtime/no restart, durable intent and strict task readback; release the currently disabled entry only in that reviewed successor.',
            'Start once and return an honest monitoring-started receipt after real initial samples; observe unattended, no synchronous48h wait and no automatic completion certificate.',
        ],
        'remaining_engineering_gates': [
            'Protected installer/complete dependency custody and task lifecycle not implemented or approved in this candidate.',
            'Actual native desktop/session/window-station used/capacity source unavailable.',
            'Independent monitor/recovery timing evidence absent; resource observer is not a recovery actuator or the full sterile SRE detector.',
            'Native-child snapshots may miss processes between samples; actual workload and provenance still required for broader acceptance.',
        ],
        'owner_required_later': 'One reviewed elevated observer installation/start can be included in a later owner batch. Continuous observation is unattended; no owner heap measurements or repeated diagnostic chores are requested.',
    }
    raw = encoded(result)
    write_new(ARCHIVE / 'preparation.json', raw)
    print(json.dumps({'archive': str(ARCHIVE), 'preparation_sha256': digest(raw),
                      'source_manifest_sha256': digest(manifest), 'tests': tests, 'source_files': rows}, indent=2))


if __name__ == '__main__':
    main()
