"""Source-only preparation. No installer, task, credential, ledger or live API calls.

Default prints a candidate manifest and phased write-set. --save creates only
the fixed new ordinary-workspace plan file. Neither mode authorizes Apply.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
WORK = Path(r'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next')
INSTALL = r'C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1'
PRIVATE = r'C:\ProgramData\CoChemSupervisor427-windows-20261007-staging-v1'
PIPELINE = r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
R3_MANIFEST = '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
REQUIRED = {
    'src/cochem_supervisor/engine.py': '9ec21d808bfbb1c5dfe01a6dd41b0dc969f9fc92df4ff5de010c4890c1dddeea',
    'src/cochem_supervisor/replay.py': 'd0a10b30e3f16d44a2906d855d46f44dc0fbad7249b8b43d6b67b98e4d94d60d',
    'src/cochem_supervisor/budget_authority.py': '36157718119f518756619b5222be9e930dba82a1726fd4b1bed23d153a217852',
    'src/cochem_supervisor/state.py': '404abfa2971a8f4a865171fc8fee3fe2c51d7837b6d49975346e6bac3740ff74',
    'src/cochem_supervisor/component_recovery.py': '3f1336f3ec7d9a2e14289c1f032f2baa9cfb1ab0bed06648e39f9ff016ac871d',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ordinary(path, *, directory=False):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Candidate source must not traverse a reparse point')
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Candidate source directory required')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Candidate source requires an ordinary single-link file')
    return info


def read(path):
    before = ordinary(path)
    if before.st_size > 16 * 1024 * 1024:
        raise ValueError('Candidate file exceeds 16 MiB')
    with Path(path).open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(16 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    end = ordinary(path)
    if len({(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
            for item in (before, opened, after, end)}) != 1 or len(raw) != before.st_size:
        raise ValueError('Source changed during candidate capture')
    return raw


def tree(root):
    pending = [root]; count = 0
    while pending:
        folder = pending.pop(); ordinary(folder, directory=True)
        for entry in sorted(folder.iterdir()):
            if entry.name in ('__pycache__', '.pytest_cache') or entry.name.endswith('.egg-info'):
                continue
            info = entry.lstat(); count += 1
            if count > 10000:
                raise ValueError('Candidate tree exceeds its traversal bound')
            if stat.S_ISDIR(info.st_mode):
                ordinary(entry, directory=True); pending.append(entry)
            else:
                ordinary(entry)
                if entry.suffix not in ('.pyc', '.pyo'):
                    yield entry


def rows(paths):
    result = []; seen = set(); size = 0
    for path in sorted(paths):
        relative = path.relative_to(REPO).as_posix()
        if relative.casefold() in seen:
            raise ValueError('Duplicate Windows candidate path')
        seen.add(relative.casefold())
        raw = read(path); size += len(raw)
        if size > 128 * 1024 * 1024:
            raise ValueError('Candidate inventory exceeds 128 MiB')
        result.append({'relative': relative, 'sha256': digest(raw), 'bytes': len(raw)})
    return result


def plan():
    prior_raw = read(WORK / 'stopped-runtime-r3-source-manifest.json')
    if digest(prior_raw) != R3_MANIFEST:
        raise ValueError('Preserved r3 manifest binding changed')
    prior = {row['relative']: row['sha256'] for row in json.loads(prior_raw)['files']}
    source = rows([*tree(REPO / 'src'), *(REPO / name for name in ('README.md', 'pyproject.toml', 'uv.lock'))])
    by_path = {row['relative']: row['sha256'] for row in source}
    if any(by_path.get(name) != expected for name, expected in REQUIRED.items()):
        raise ValueError('Reviewed supervision correction or immutable guard changed')
    tests = rows([*tree(REPO / 'pipeline_tests'), *tree(REPO / 'mcp_tests'), *tree(REPO / 'supervisor_tests')])
    support = rows([*tree(REPO / 'knowledge'), *(REPO / 'scripts' / name for name in
        ('verify_pipeline_acceptance.py', 'parse_agy_status_capture.py', 'install_aetherdesk_pawnio.ps1'))])
    pytest = b'[pytest]\r\naddopts = -ra\r\n'
    proposal = json.loads(read(REPO / 'config/windows/aetherdesk-427.supervisor.proposed.json'))
    targets = proposal['test_targets']
    available = {row['relative'] for row in tests}
    if any(target not in available for target in targets):
        raise ValueError('Prior selected protected acceptance target is missing')
    fresh = []
    for target in (INSTALL, PRIVATE):
        try:
            Path(target).lstat()
        except FileNotFoundError:
            state = 'ABSENT_BY_ORDINARY_METADATA'
        except OSError as error:
            state = 'UNKNOWN_' + type(error).__name__
        else:
            state = 'PRESENT_DO_NOT_REUSE'
        fresh.append({'path': target, 'state': state, 'admin_revalidation_required': True})
    return {
        'schema': 'cochem-independent-supervisor-staging-plan/1',
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'SOURCE_ONLY_CANDIDATE_NOT_AN_INSTALLER',
        'complete_source_freeze': False, 'apply_available': False, 'activation_ready': False,
        'source_repository': str(REPO), 'proposed_new_roots': fresh,
        'preserved_pipeline_root': PIPELINE,
        'preserved_pipeline_configuration_sha256': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        'preserved_r3_source_manifest_sha256': R3_MANIFEST,
        'source_changes_since_r3': {
            'added': sorted(set(by_path) - set(prior)), 'removed': sorted(set(prior) - set(by_path)),
            'changed': [{'relative': key, 'r3_sha256': prior[key], 'candidate_sha256': by_path[key]}
                        for key in sorted(set(prior) & set(by_path)) if prior[key] != by_path[key]]},
        'source_files': source, 'test_files': tests, 'public_test_support_files': support,
        'selected_regression_targets': targets,
        'generated_acceptance_pytest_ini': {'encoding': 'utf-8-no-bom',
            'literal': pytest.decode(), 'sha256': digest(pytest)},
        'test_scope': 'Protected regression diagnostics plus separately protected outer assertions. Candidate-written JUnit is not promotion authority. Keep true SYSTEM-only fixture gates and explicit Git binding; do not relabel skips as native acceptance.',
        'future_build_argv': ['--no-config', '--no-cache', 'sync', '--project', INSTALL + r'\source',
            '--frozen', '--no-editable', '--link-mode', 'copy', '--extra', 'mcp', '--extra', 'dev',
            '--python', r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe',
            '--no-python-downloads'],
        'future_build_environment': {'UV_PROJECT_ENVIRONMENT': INSTALL + r'\.venv',
            'policy': 'Reviewed fixed child environment, exact pinned uv/base-Python custody, scoped stderr/exit handling and restoration; never invoke a shell build command.'},
        'permitted_future_write_set': [
            INSTALL + r'\source (new protected source and dependency-policy snapshot)',
            INSTALL + r'\.venv (new independent copy-mode interpreter/dependencies)',
            INSTALL + r'\acceptance (protected suites, public corpus and fixed support)',
            INSTALL + r'\controls (pinned bootstrap/provenance and one-shot receipt)',
            PRIVATE + r'\private (new SYSTEM/Admin private parent)',
            PRIVATE + r'\private\unresolved-pair (new held ledgers and immutable bootstrap controls)',
            'One fresh no-trigger finite SYSTEM bootstrap task, only after separate wrapper review; no daemon task registration or activation',
        ],
        'phases': [
            {'phase': 'preflight_only', 'requirements': [
                'Freeze and review this exact complete source/test/public-support manifest and all source deltas; this candidate manifest does not grant execution authority.',
                'Bind preserved r3 install/config and unchanged active Warden task action; do not require or cause a Warden stop for unrelated unpublished staging.',
                'Assert both fresh target roots and the unique bootstrap task absent; permission failures are not absence. Reject links/hardlinks/untrusted write ancestors; hold every source/control/executable stream through copy and readback.',
                'Validate exact protected uv and base Python tree custody and existing immutable bootstrap module pins. Do not open old ledgers, copy old DB files, invoke migration or read provider/controller tokens.']},
            {'phase': 'fresh_code_only', 'requirements': [
                'Create code roots with trusted owner/writer ACL before any executable bytes. Copy typed exact manifest rows with CreateNew and held destination hashes; preserve partial outputs.',
                'Build only the new venv from the protected snapshot with fixed frozen uv arguments; include dev for protected pytest. Rebind source and installed module bytes to manifest after build.',
                'Include public knowledge corpus/catalog under acceptance so the actual committed-corpus regression has its dependency. Do not copy the private 126 captures or production knowledge index.',
                'Keep candidate source, protected tests and later repair workspace disjoint. Verify supervisor interpreter is distinct from the active pipeline venv.']},
            {'phase': 'new_unpublished_hold_pair', 'requirements': [
                'Use a fresh bounded no-trigger SYSTEM helper with exact protected source/interpreter/control bindings; no daemon/action/task migration.',
                'Create private parent with effective inheritable SYSTEM FullControl OI+CI and optional Admin FullControl before private bytes. Do not use Python mkdir(mode=0700) on Windows or rewrite existing ACLs.',
                'Call only the reviewed bootstrap-unresolved-budget-pair.bootstrap_pair in a new absent child; preserve its exact module pins and proposed uncertainty evidence.',
                'Verify both immutable rows, evidence hashes, durable SQL denial triggers, integrity and exact allowed filenames before publishing its one-time completion receipt. On any partial failure preserve everything and stop; no automatic retry/reset/cleanup.']},
            {'phase': 'stop_unpublished', 'requirements': [
                'No supervisor.json, current release pointer, enabled supervisor task, repair account/login, live health call, provider probe, RAM provisioning or Docker call in this staging scope.',
                'Existing Warden/config/pointer/task/R startup task/worker accounts and all current or historical databases remain unchanged. Four shared seats and Chapter 06 routing remain captured in the preserved pipeline config.',
                'A later reviewed commission plan must establish independent layout/repair identity and task ownership, validate every ledger hold, and keep actuators disabled while authority is unresolved. Code staging does not attest full supervisor or host acceptance.']},
        ],
        'existing_stale_proposal_used_only_for_target_list': 'config/windows/aetherdesk-427.supervisor.proposed.json',
        'do_not_execute': ['scripts/install_supervisor_windows.ps1', 'provision-execution', 'migrate-ledger',
            'Supervisor constructor/daemon', 'ReleaseStore bootstrap/activate', 'RamdiskManager.ensure',
            'DockerRunner constructor', 'native login/model commands'],
        'remaining_implementation': ['Fresh installation wrapper with held custody and exact typed copy records',
            'Pinned SYSTEM paired-bootstrap launcher and safe receipt projection',
            'Meaningful inert PowerShell Apply-tail fixtures and readback/partial-preservation tests',
            'Independent review and complete source freeze before an owner command'],
        'prepared_bootstrap': {'source': str(WORK / 'bootstrap-unresolved-budget-pair.py'),
            'sha256': digest(read(WORK / 'bootstrap-unresolved-budget-pair.py')),
            'proposed_evidence_sha256': digest(read(WORK / 'unresolved-budget-evidence.proposed.json'))},
        'legacy_spend_remaining': None, 'paid_or_component_recovery_enabled': False,
        'no_privileged_or_live_execution_performed': True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args()
    value = plan()
    raw = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()
    if args.save:
        output = WORK / 'independent-supervisor-staging-r3-v1.plan.json'
        with output.open('xb') as stream:
            stream.write(raw)
        print(json.dumps({'path': str(output), 'sha256': digest(raw),
            'source_files': len(value['source_files']), 'test_files': len(value['test_files']),
            'public_support_files': len(value['public_test_support_files']),
            'source_changes_since_r3': value['source_changes_since_r3'],
            'status': value['status'], 'apply_available': False}, indent=2))
    else:
        print(raw.decode(), end='')

if __name__ == '__main__':
    main()
