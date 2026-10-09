"""Preserve reviewed recovery and Windows evidence; refresh only setup guides."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

here = Path(__file__).absolute().parent
pins = {
    'resume-partial-resource-observer-r3-v1.ps1': '6d15a2e11b971d61fa202eb00448c25f70f084a444ebc6f53a6781aeb28c75f1',
    'resource-observer-duration-r3-v1.ps1': '3dd63e3db9ed65210571dd2336b1d5f67e3464415bb04bc11aa916dc9e17dd9c',
    'install-resource-observer-r3-v5.ps1': 'ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f',
    'partial-resource-observer-recovery-v1-tests-passed-final.xml': 'd00f2f9108d6e3e5ed9ee40a334b77c8e6e3b27fb310b799a3322eff7a801875',
    'partial-resource-observer-recovery-v1-tests-metadata-final.xml': 'bc4dd30e18266f1e9b586bc7f96e66f9118021f0556af2daf62315e359dbc73e',
    'resource-observer-duration-r3-v1-tests-final.xml': 'e89b15fc1563b3de8d43efb0940c61b605058beecb3032522c9a8f4c75c9ea54',
    'partial-resource-observer-recovery-v1-preview-final.json': '7ce69cc53a631caca5cfb873c3d0af7436edeea20d2e2d8516536c54f82da70a',
    'RETURN_SETUP_RESOURCE_RECOVERY_20261009.txt': '0effc8e1778b235b8867a3755722be56a5c19513db8c5dd6f02179c7802d9958',
}

def checked(path, expected=None):
    if path.lstat().st_file_attributes & 0x400:
        raise ValueError('Reparse evidence refused')
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected is not None and sha != expected:
        raise ValueError('Frozen evidence changed: ' + path.name)
    return raw, sha

blobs = {name: checked(here / name, pin) for name, pin in pins.items()}
for name, count in [('partial-resource-observer-recovery-v1-tests-passed-final.xml', 34),
                    ('partial-resource-observer-recovery-v1-tests-metadata-final.xml', 2),
                    ('resource-observer-duration-r3-v1-tests-final.xml', 38)]:
    suite = ET.fromstring(blobs[name][0]).find('testsuite')
    assert int(suite.attrib['tests']) == count and int(suite.attrib['failures']) == 0 and int(suite.attrib['errors']) == 0
activation_path = Path(r'C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1\activation.json')
_, activation_sha = checked(activation_path, '5599e920e8b457e6389e5d7677a4eca6e5479e8dc72bba2f41ccb5d46e640171')
partial = Path(r'C:\Program Files\CoChem\ResourceObservation4.2.7-windows-20261008-r3-v4')
assert sorted(item.name for item in partial.iterdir()) == ['dependencies.json', 'package', 'registration-intent.json']
_, intent_sha = checked(partial / 'registration-intent.json', '3fde035d1aed9f06b950284953a5737ba089cf7e5ab2e12ea5a24a06425537bd')
manifest_path = here / 'resource-observer-r3-v4' / 'source-manifest.json'
manifest_raw, _ = checked(manifest_path, '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49')
manifest = json.loads(manifest_raw)
for row in manifest['files']:
    raw, _ = checked(partial / 'package' / row['path'], row['sha256'])
    assert len(raw) == row['size']
checked(partial / 'package' / 'source-manifest.json', '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49')
assert len(list((partial / 'package').rglob('*.*'))) == 8
checked(partial / 'dependencies.json', 'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429')
archive = here / 'partial-resource-recovery-evidence-20261009'
archive.mkdir()
inventory = []
for name, (raw, sha) in blobs.items():
    with (archive / name).open('xb') as stream:
        stream.write(raw)
    inventory.append({'file': name, 'sha256': sha, 'bytes': len(raw)})
for name in ['LIVE_WORKFLOW_TEST_SCOPE_20261009.json', 'live-model-tests-review-held-20261009.json',
             'post-monitoring-v8-live-readiness-safe-20261009.json',
             'partial-resource-observer-recovery-v1-tests-initial.xml',
             'partial-resource-observer-recovery-v1-tests-scalar-candidate.xml']:
    raw, sha = checked(here / name)
    with (archive / name).open('xb') as stream:
        stream.write(raw)
    inventory.append({'file': name, 'sha256': sha, 'bytes': len(raw)})
old_guide_sha = '5a01d01523453e5b40f45a953264590dc88478e9ca8f9850458578e088ea0409'
for name in ['RETURN_SETUP.txt', 'RETURN_SETUP_CONTINUE.txt']:
    raw, _ = checked(here / name, old_guide_sha)
    with (here / (name[:-4] + '.before-resource-recovery-20261009.txt')).open('xb') as stream:
        stream.write(raw)
    (here / name).write_bytes(blobs['RETURN_SETUP_RESOURCE_RECOVERY_20261009.txt'][0])
report = {
    'schema': 'cochem-partial-resource-observer-recovery-preparation/1',
    'status': 'PREPARED_NOT_APPLIED', 'prepared_utc': datetime.now(timezone.utc).isoformat(),
    'owner_local_date': '2026-10-09', 'host': 'AETHERDESK',
    'successful_held_supervisor_activation_sha256': activation_sha,
    'existing_partial_registration_intent_sha256': intent_sha,
    'copied_package_files_byte_verified': 8,
    'copied_dependencies_byte_verified': True,
    'registered_resource_task_actual_fields': 'inaccessible to ordinary process; administrator projection and strict conditional preflight required',
    'raw_registered_duration_known': False,
    'duration_representation_repair': 'Only semantically exact 176400 seconds; no calendar units, changed limits or other task-guard exceptions',
    'existing_resource_task_recreated': False,
    'recovery_apply_performed': False,
    'resource_observer_started': False,
    'windows_tests': {'duration_and_original_task_guards': 38, 'distinct_recovery_and_projection_cases': 34,
                      'final_reporting_rename_cases_run_separately': 2},
    'test_scope': 'Windows PowerShell 5.1; native in-memory COM duration and inert task/provider recovery fixtures; no host task registration or start',
    'newest_read_only_controller_health': 'ready at one actual Windows sample: same controller, 57 Celsius, capacity 2, no active jobs or quarantines',
    'model_jobs_submitted_by_this_turn': 0,
    'external_model_acceptance': 'exact scope prepared; explicit owner approval pending after automatic review rejected process creation',
    'preserved': ['provider sign-ins', 'credentials', 'databases', 'repair budgets', '8 GiB R: and startup task',
                  'same running controller and held supervisor', 'four shared slots', 'Chapter 06 routing'],
    'linux_results_counted_as_windows': False,
    'continuous_48h_complete': False, 'full_srs_acceptance': False,
    'next': 'Run resume-partial-resource-observer-r3-v1.ps1 -Apply once in Administrator Windows PowerShell; answer exact model-test scope approval separately',
    'archive_inventory': inventory,
}
target = here / 'partial-resource-recovery-preparation-20261009.json'
with target.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(report, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'status': report['status'], 'protected_task_actions': 0}))
