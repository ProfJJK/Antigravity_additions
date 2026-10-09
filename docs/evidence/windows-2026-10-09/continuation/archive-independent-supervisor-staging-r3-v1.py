"""CreateNew public preparation evidence; no protected task or installation."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
DEST = REPO / 'docs/evidence/windows-2026-10-06/independent-supervisor-staging-preparation-2026-10-07'
SHA = lambda raw: hashlib.sha256(raw).hexdigest()
MANIFEST = WORK / 'independent-supervisor-staging-r3-v1.manifest.json'

if __name__ == '__main__':
    if DEST.exists(): raise FileExistsError('Preserve existing preparation archive')
    run = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoProfile', '-NonInteractive', '-File',
        str(WORK / 'install-independent-supervisor-staging-r3-v1.ps1'), '-Manifest', str(MANIFEST), '-ManifestSha256', SHA(MANIFEST.read_bytes())], capture_output=True, timeout=60)
    if run.returncode or run.stderr: raise RuntimeError('Read-only preview did not succeed')
    preview = json.loads(run.stdout)
    if preview['mode'] != 'READ_ONLY_PLAN' or preview['holds'] or preview['daemon_definitions_observed']:
        raise ValueError('Preview differs from ordinary non-administrator scope')
    with (WORK / 'independent-supervisor-staging-r3-v1-preview-final.json').open('xb') as stream: stream.write(run.stdout)
    xml = ET.parse(WORK / 'independent-supervisor-staging-r3-v1-tests-final.xml').getroot()[0].attrib
    if any(int(xml[k]) for k in ('failures','errors','skipped')) or int(xml['tests']) != 47: raise ValueError('Final test result differs')
    names = ['stage-independent-supervisor-holds-r3-v1.py', 'install-independent-supervisor-staging-r3-v1.ps1',
        'test_independent_supervisor_staging_r3_v1.py', 'freeze-independent-supervisor-staging-r3-v1.py',
        'prepare-independent-supervisor-staging-r3-v1.py', 'acceptance-pytest-staging-r3-v1.ini',
        'independent-supervisor-staging-r3-v1.plan.json', 'independent-supervisor-staging-r3-v1.manifest.json',
        'independent-supervisor-staging-r3-v1-preview-final.json', 'independent-supervisor-staging-r3-v1-tests-initial.xml',
        'independent-supervisor-staging-r3-v1-tests-followup.xml', 'independent-supervisor-staging-r3-v1-tests-followup2.xml',
        'independent-supervisor-staging-r3-v1-tests-final.xml']
    DEST.mkdir()
    files = {}
    for name in names:
        raw = (WORK / name).read_bytes()
        with (DEST / name).open('xb') as stream: stream.write(raw)
        files[name] = {'sha256': SHA(raw), 'bytes': len(raw)}
    report = {'schema': 'cochem-independent-supervisor-staging-preparation/1', 'status': 'PREPARED_NOT_APPLIED_INDEPENDENT_REVIEW_PENDING',
        'files': files, 'tests': {**xml, 'platform': 'actual Windows ordinary ansac Python3.12.13 / Windows PowerShell5.1',
            'SYSTEM_acceptance': False, 'real_temp_sqlite_and_native_file_copy': True,
            'privileged_acl_identity_task_build_boundaries': 'explicit inert fixture replacements',
            'source_assets': 'actual166 source +126 acceptance files +109 installed package assets in disposable copy'},
        'earlier_results': {'initial': '36 passed; positive AST fixture lacked script-root context',
            'followup': '41 passed; four fixture context/module import failures; no Apply',
            'followup2': '45 passed before two added strict JSON scalar fixtures'},
        'permitted_on_future_reviewed_apply': ['Fresh protected source/acceptance/uv venv/build files under new supervisor root',
            'Fresh private unpublished paired unresolved-authority ledgers; no authority release API',
            'One new no-trigger SYSTEM staging task and bounded sanitized receipt; preserve success or partial outputs'],
        'forbidden': ['Existing Warden/supervisor task changes', 'Existing pipeline config/pointer/account/profile/credential changes',
            'Legacy ledger reads, resets or inferred fresh allowance', 'Provider/model/Docker calls or pipeline provisioning'],
        'preserved_pipeline_configuration_sha256': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        'target': preview['target_root'], 'unpublished_pair_root': preview['unpublished_pair_root'],
        'source_only_plan_superseded_detail': 'Concrete private pair is inside the fresh code-root private child; unused ProgramData proposal was not created.',
        'remaining_activation': 'Separate reviewed repair identity/workspace, held config and observation activation; no automatic Warden restart outside immutable authority.',
        'human_action_now': None, 'full_srs_acceptance': False, 'no_apply_or_live_execution': True}
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with (DEST / 'preparation.json').open('xb') as stream: stream.write(raw)
    print(json.dumps({'path': str(DEST / 'preparation.json'), 'sha256': SHA(raw), 'tests': xml, 'preview_sha256': SHA(run.stdout)}))
