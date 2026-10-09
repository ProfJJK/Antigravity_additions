"""One protected, non-inference status command in one real provisioned profile.

Standalone follow-up artifact; never import source from the checkout. Native
execution is permitted only under the pinned installed interpreter as SYSTEM.
The companion staging wrapper is read-only unless the operator supplies -Apply.
"""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

INSTALL = Path(r"C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3")
NATIVE = Path(r"C:\Program Files\CoChem\Native4.2.7-windows-20261006")
PYTHON = INSTALL / '.venv/Scripts/python.exe'
BASE_PYTHON = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
VENV_CONFIG_SHA256 = '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'
WINDOWS_SHA256 = 'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba'
INSTALL_RECEIPT_SHA256 = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
RESOURCE_SHA256 = 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f'
MANIFEST_SHA256 = '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
CONFIG_SHA256 = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
LAYOUT_SHA256 = '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
REVISION_SHA256 = '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
ORIGINAL_FAILED_SHA256 = '691c70560436f948464c529b9d2d6a3e33ce240749642af86ca79c748791461d'
PYTHON_SHA256 = '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'
VENV_SHA256 = VENV_CONFIG_SHA256
CONTRACTS = {
    'codex': {'sha256': 'fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d',
              'version_at_pin': '0.160.0', 'arguments': ['login', 'status']},
    'claude': {'sha256': '0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469',
               'version_at_pin': '2.1.280', 'arguments': ['--setting-sources', '', 'auth', 'status', '--json']},
}
MAX_OUTPUT = 65536
STATUS_SECONDS = 30
DAEMON_TASK_NAMES = ('CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor',
                     'CoChem-4.2.2-Warden', 'CoChem-4.2.3-Supervisor')


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError('Nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def parse_status(provider, stdout, stderr, exit_code):
    """Return only allowlisted classifications, never email/account/token text."""
    if provider not in CONTRACTS:
        raise ValueError('Only reviewed Codex and Claude status contracts are supported')
    result = {'protocol_valid': False, 'logged_in': False, 'auth_kind': 'unverified',
              'native_subscription_authentication_verified': False,
              'subscription_type': 'unreported', 'plan_entitlement_verified': False,
              'provider_account_identity_verified': False}
    if (type(exit_code) is not int or not isinstance(stdout, bytes) or not isinstance(stderr, bytes)
            or len(stdout) + len(stderr) > MAX_OUTPUT):
        return result
    try:
        out, err = stdout.decode('utf-8', 'strict'), stderr.decode('utf-8', 'strict')
    except UnicodeError:
        return result
    if provider == 'codex':
        lines = [line.strip() for line in (out + '\n' + err).splitlines() if line.strip()]
        if exit_code == 0 and lines == ['Logged in using ChatGPT']:
            result.update(protocol_valid=True, logged_in=True, auth_kind='chatgpt',
                          native_subscription_authentication_verified=True)
        elif exit_code != 0 and lines == ['Not logged in']:
            result.update(protocol_valid=True, auth_kind='none')
        return result
    # A warning/error stream is not silently interpreted as successful auth.
    if err.strip():
        return result
    try:
        value = strict_json(out)
    except (ValueError, TypeError, RecursionError):
        return result
    if not isinstance(value, dict) or type(value.get('loggedIn')) is not bool:
        return result
    known = ('none', 'claude.ai', 'oauth_token', 'api_key', 'api_key_helper', 'third_party')
    kind = value.get('authMethod')
    if kind not in known or exit_code not in (0, 1):
        return result
    if value['loggedIn'] != (exit_code == 0) or (value['loggedIn'] and kind == 'none'):
        return result
    plan = value.get('subscriptionType')
    if plan not in ('pro', 'max', 'team', 'enterprise'):
        plan = 'unreported' if plan is None else 'other'
    result.update(protocol_valid=True, logged_in=value['loggedIn'], auth_kind=kind,
                  subscription_type=plan)
    result['native_subscription_authentication_verified'] = (
        value['loggedIn'] and kind == 'claude.ai' and value.get('apiProvider') == 'firstParty'
        and plan in ('pro', 'max', 'team', 'enterprise'))
    # Reported plan text is not independent evidence of a current paid entitlement.
    return result


def digest_file(path, maximum=1024 * 1024 * 1024):
    before = path.stat()
    if before.st_size > maximum:
        raise ValueError('Reviewed file exceeds its size bound')
    digest = hashlib.sha256()
    count = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > maximum:
                raise ValueError('Reviewed file grew beyond its size bound')
            digest.update(chunk)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Reviewed file changed during hash capture')
    return digest.hexdigest()


def protected_json(win, path):
    win.validate_code_path(path)
    from cochem_pipeline.ramdisk import ordinary_tree
    ordinary_tree(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Protected configuration exceeds its byte bound')
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('Protected configuration exceeds its byte bound')
    value = strict_json(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Protected configuration requires an object')
    return value, hashlib.sha256(raw).hexdigest()


def verify_r3_runtime(win, install_receipt_sha256):
    """Bind current source bytes before comparing installed package revision."""
    from cochem_pipeline.deployment_revision import verify_installed_revision
    from cochem_pipeline import resource_limits
    for pin in (RESOURCE_SHA256, MANIFEST_SHA256, install_receipt_sha256, VENV_SHA256):
        if not re.fullmatch('[a-f0-9]{64}', pin):
            raise ValueError('Actual reviewed r3 bindings are not frozen')
    if (Path(sys.base_prefix).resolve() != BASE_PYTHON.parent or Path(sys._base_executable).resolve() != BASE_PYTHON
            or sys.version_info[:3] != (3, 12, 13) or not sys.flags.isolated or not sys.dont_write_bytecode):
        raise ValueError('Exact isolated protected Python binding differs')
    for path, pin in ((PYTHON, PYTHON_SHA256), (INSTALL/'.venv/pyvenv.cfg', VENV_SHA256),
                      (Path(resource_limits.__file__), RESOURCE_SHA256)):
        win.validate_code_path(path)
        if digest_file(path) != pin:raise ValueError('Pinned r3 runtime control differs')
    installed, digest = protected_json(win, INSTALL/'install-after.json')
    if digest != install_receipt_sha256:raise ValueError('Actual r3 receipt pin differs')
    expected = {'schema':'cochem-stopped-runtime-update/1','mode':'FRESH_STOPPED_RUNTIME_READY',
        'target_root':str(INSTALL),'source_files':166,'source_manifest_sha256':MANIFEST_SHA256,
        'previous_runtime_root':r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2',
        'previous_install_receipt_sha256':'d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4',
        'previous_source_manifest_sha256':'df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8',
        'configuration_unchanged':True,'only_config_change':None,'preserved_denial_receipt_sha256':ORIGINAL_FAILED_SHA256,
        'preserved_denial_task_terminal_check_deferred':False,'preserved_worker_cleanup_verified':False,
        'configuration_sha256':CONFIG_SHA256,'accounts_provisioned':0,'tasks_changed':0,
        'credentials_modified':False,'databases_modified':False,'ram_modified':False,
        'pipeline_started':False,'activation_ready':False}
    if any(type(installed.get(k)) is not type(v) or installed.get(k)!=v for k,v in expected.items()) or installed.get('holds')!=[]:
        raise ValueError('Complete stopped r3 installation binding missing')
    manifest, digest = protected_json(win, INSTALL/'source-manifest.json')
    if digest != MANIFEST_SHA256:raise ValueError('Frozen r3 manifest pin differs')
    verify_r3_source(manifest, lambda path,pin,length: verified_source(win,path,pin,length))
    revision=verify_installed_revision(INSTALL/'source',INSTALL/'.venv/Lib/site-packages',INSTALL/'source')
    if (revision.get('verified') is not True or revision.get('files') != 109
            or revision.get('source_sha256') != REVISION_SHA256
            or revision != installed.get('verification',{}).get('revision')):
        raise ValueError('Actual installed r3 revision differs from pinned receipt/source')
    return revision


def verified_source(win,path,pin,length):
    win.validate_code_path(path)
    if path.stat().st_size!=length or digest_file(path)!=pin:raise ValueError('Frozen source bytes differ')


def verify_r3_source(value, read):
    if (value.get('schema')!='cochem-stopped-runtime-source/1' or value.get('target_root')!=str(INSTALL)
            or value.get('complete_source_freeze') is not True or not isinstance(value.get('files'),list)
            or len(value['files'])!=166):raise ValueError('Frozen r3 source inventory differs')
    seen=set()
    for row in value['files']:
        key=row.get('relative')
        if (not isinstance(key,str) or '\\' in key or ':' in key or key.startswith('/')
            or any(part in ('','.','..') for part in key.split('/')) or key.casefold() in seen
            or not (key.startswith('src/') or key in ('README.md','pyproject.toml','uv.lock'))
            or not re.fullmatch('[a-f0-9]{64}',str(row.get('sha256')))
            or type(row.get('length')) is not int or not 0<=row['length']<=16777216):
            raise ValueError('Unsafe frozen r3 source record')
        seen.add(key.casefold());read(INSTALL/'source'/key,row['sha256'],row['length'])
    if not {'pyproject.toml','uv.lock','readme.md'}<=seen:raise ValueError('Project controls missing from r3 manifest')


def require_stopped(win):
    # Do not alter tasks, accept unknown scheduler errors, or inspect legacy tasks.
    win._powershell(r"""
        $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');
        foreach ($name in $data) {
            $task=$null;
            try {$task=$folder.GetTask($name)} catch {
                $missing=$false;$exception=$_.Exception;
                while ($null -ne $exception) {if ($exception.HResult -eq -2147024894) {$missing=$true;break};$exception=$exception.InnerException}
                if (-not $missing) {throw 'Cannot establish task state.'}
            }
            if ($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)) {throw 'Protected daemons must remain stopped and disabled.'}
        }
    """, list(DAEMON_TASK_NAMES))


def launch_status_process(win, identity, argv, cwd, empty, output, errors, limits, report=None):
    # Recheck actual host task names at the launch boundary, not only while
    # planning/copying the package. No daemon is stopped or disabled here.
    require_stopped(win)
    if report is not None:
        report['native_status_dispatch_attempted'] = True
        report['native_status_commands_executed'] = None  # Launch failures can occur after resume.
    return win.launch_worker(identity, argv, cwd, empty, output, errors, limits=limits)


def observe_child(win, process, expected_sid, executable):
    """Observe the actual child token/image/job through the owned process handle."""
    api = win._api()
    token = win.HANDLE()
    win._check(api['advapi32'].OpenProcessToken(process._process, 0x8, C.byref(token)), 'Open owned status-process token')
    try:
        user = win._SID_AND_ATTRIBUTES.from_buffer(win._token_info(token, 1))
        sid = win._sid_text(user.Sid)
        if sid != expected_sid or sid in {win.SYSTEM_SID, win.ADMIN_SID}:
            raise ValueError('Observed status process has the wrong identity')
        count = win.DWORD()
        api['userenv'].GetUserProfileDirectoryW(token, None, C.byref(count))
        if not 1 <= count.value <= 32768:
            raise ValueError('Observed profile path has an invalid bound')
        profile = C.create_unicode_buffer(count.value)
        win._check(api['userenv'].GetUserProfileDirectoryW(token, profile, C.byref(count)), 'Observe worker token profile')
        query = api['kernel32'].QueryFullProcessImageNameW
        query.argtypes = [win.HANDLE, win.DWORD, win.LPWSTR, C.POINTER(win.DWORD)]
        query.restype = win.BOOL
        size = win.DWORD(32768)
        image = C.create_unicode_buffer(size.value)
        win._check(query(process._process, 0, image, C.byref(size)), 'Observe native process image')
        if Path(image.value).resolve() != executable.resolve():
            raise ValueError('Observed status process image differs from the reviewed executable')
        in_job = win.BOOL()
        check_job = api['kernel32'].IsProcessInJob
        check_job.argtypes = [win.HANDLE, win.HANDLE, C.POINTER(win.BOOL)]
        check_job.restype = win.BOOL
        win._check(check_job(process._process, process._job, C.byref(in_job)), 'Observe owned Job membership')
        if not in_job.value:
            raise ValueError('Status process is not in its owned Job Object')
        return {'pid': process.pid, 'creation_time_filetime': process.creation_time_filetime,
                'token_sid': sid, 'token_matches_selected_worker': True,
                'image_matches_reviewed_executable': True, 'owned_job_membership_verified': True,
                'profile_directory_sha256': hashlib.sha256(profile.value.casefold().encode()).hexdigest(),
                'source': 'owned_windows_process_handle_and_child_token'}
    finally:
        win._close(token)


def bounded_wait(process, output, errors):
    deadline = time.monotonic() + STATUS_SECONDS
    while True:
        if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > MAX_OUTPUT:
            raise ValueError('Native status output exceeded its bound')
        code = process.poll()
        if code is not None:
            return code
        if time.monotonic() >= deadline:
            raise TimeoutError('Native status deadline expired')
        time.sleep(.1)


def safe_failure(error, phase):
    allowed = {'trusted_preflight', 'reservation', 'runtime_custody', 'layout', 'boundaries',
               'native_custody', 'native_launch', 'native_attestation', 'native_wait',
               'native_cleanup', 'native_result', 'daemon_postcheck'}
    kind = re.sub('[^A-Za-z0-9_]', '', type(error).__name__)[:80] or 'Exception'
    code, current = None, error
    for _ in range(5):
        value = getattr(current, 'winerror', None)
        if type(value) is int and 0 <= value <= 0xFFFFFFFF:
            code = value
            break
        current = current.__cause__ or current.__context__
        if current is None:
            break
    return {'phase': phase if phase in allowed else 'trusted_preflight', 'error_type': kind, 'winerror': code}


def validate_interpreter():
    if (Path(sys.executable).resolve() != PYTHON or Path(sys.base_prefix).resolve() != BASE_PYTHON.parent
            or Path(sys._base_executable).resolve() != BASE_PYTHON
            or sys.version_info[:3] != (3, 12, 13) or not sys.flags.isolated or not sys.dont_write_bytecode
            or digest_file(INSTALL / '.venv/pyvenv.cfg') != VENV_CONFIG_SHA256):
        raise ValueError('Protected isolated interpreter binding changed')


def authentication_decision(value):
    """Only explicit logged-out status authorizes offering a browser login."""
    if not isinstance(value,dict) or value.get('protocol_valid') is not True:
        return 'HOLD'
    if (value.get('native_subscription_authentication_verified') is True
            and value.get('logged_in') is True and value.get('auth_kind') in ('chatgpt','claude.ai')):
        return 'REUSE_VERIFIED_SESSION'
    if (value.get('native_subscription_authentication_verified') is False
            and value.get('logged_in') is False and value.get('auth_kind') == 'none'):
        return 'ATTENDED_LOGIN_REQUIRED'
    return 'HOLD'


def run(slot, provider, nonce, stage):
    if slot not in {"slot1", "slot2", "slot3", "slot4"} or stage not in {"before", "after"}:
        raise ValueError("Only four selected identities and fixed authentication stages are permitted")
    from cochem_pipeline import windows as win
    from cochem_pipeline.resource_limits import ResourceLimits
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    root = Path(r'C:\Program Files\CoChem') / f'NativeAuthStatus4.2.7-windows-20261007-r3-v1-{slot}-{provider}-{stage}'
    if Path(__file__).resolve() != root / 'worker-native-auth-status-r3-v1.py' or Path(sys.executable).resolve() != PYTHON:
        raise ValueError('Use only the reviewed protected helper and installed interpreter')
    for path in (root, Path(__file__), PYTHON, Path(win.__file__)):
        ordinary_tree(path)
        win.validate_code_path(path)
    report = {'schema': 'cochem-worker-native-auth-status/1', 'nonce': nonce, 'provider': provider,
        'stage': stage, 'decision': 'HOLD',
        'slot': slot, 'status': 'UNVERIFIED', 'system_sid': win.SYSTEM_SID,
        'started_at_unix_ms': int(time.time() * 1000), 'helper_sha256': digest_file(Path(__file__)),
        'runtime_root': str(INSTALL), 'install_receipt_sha256': INSTALL_RECEIPT_SHA256,
        'source_manifest_sha256': MANIFEST_SHA256, 'resource_limits_sha256': RESOURCE_SHA256,
        'windows_runtime_sha256': WINDOWS_SHA256, 'layout_sha256': None, 'config_sha256': None,
        'native_executable_sha256_before': None, 'native_version_at_reviewed_pin': CONTRACTS[provider]['version_at_pin'],
        'native_version_command_executed': False, 'native_status_commands_executed': 0,
        'native_status_dispatch_attempted': False,
        'native_model_jobs_executed': 0, 'login_commands_executed': 0, 'owner_credentials_read': False,
        'native_auth_store_read_by_helper': False, 'raw_status_published': False,
        'cleanup_verified': False, 'provider_account_identity_verified': False,
        'serving_model_verified': False, 'serving_effort_verified': False, 'ready_for_inference': False,
        'reported_model': None, 'reported_effort': None, 'requested_model': None,
        'pipeline_started': False, 'daemon_states_verified_before_and_after': False,
        'native_cache_refresh_possible': True}
    report_path = root / 'worker-native-status.json'
    # Preserve one-shot evidence even if execution is interrupted. The wrapper
    # refuses an existing root/task; it never retries or replaces this receipt.
    with report_path.open('x', encoding='utf-8') as receipt:
        phase, lock = 'reservation', None
        try:
            C.set_last_error(0)
            lock = win._api()['kernel32'].CreateMutexW(None, False, 'Global\\CoChem427-NativeStatusSetup')
            win._check(lock, 'Reserve exclusive native status check')
            if C.get_last_error() == 183:
                raise ValueError('Another native status check owns the reservation')
            phase = 'runtime_custody'
            validate_interpreter()
            if digest_file(Path(win.__file__)) != WINDOWS_SHA256:
                raise ValueError('Installed Windows launch implementation differs from the reviewed pin')
            report['revision'] = verify_r3_runtime(win, INSTALL_RECEIPT_SHA256)
            phase = 'layout'
            layout, layout_hash = protected_json(win, INSTALL / 'windows-layout.json')
            config, config_hash = protected_json(win, INSTALL / 'pipeline.json')
            if layout_hash != LAYOUT_SHA256 or config_hash != CONFIG_SHA256:
                raise ValueError('Exact r3 layout/configuration binding differs')
            expected_slots = {f'slot{i}' for i in range(1, 7)}
            if (set(layout['slots']) != expected_slots or set(config['workers']) != expected_slots
                    or set(config['slot_roots']) != expected_slots or config.get('max_execution_slots') != 4
                    or layout['private_root'] != config['private_root']
                    or layout['operator_name'] != config['operator_name']):
                raise ValueError('Protected layout differs from the reviewed six-identity/four-slot configuration')
            for key in expected_slots:
                row = layout['slots'][key]
                worker = config['workers'][key]
                if (row['identity'] != worker['name'] or row['credential_target'] != worker['credential_target']
                        or row['root'] != config['slot_roots'][key]
                        or worker['name'] != 'CoChem422Worker' + key[4:]
                        or worker['credential_target'] != 'CoChem422/' + key):
                    raise ValueError('Installed identity mapping differs between layout and configuration')
            spec = layout['slots'][slot]
            identity = win.WorkerIdentity(spec['identity'], spec['credential_target'])
            expected_sid = win._sid_text(win._account_sid(identity.name))
            if expected_sid != spec['sid']:
                raise ValueError('Provisioned worker SID changed')
            phase = 'boundaries'
            private = Path(config['private_root'])
            ordinary_tree(private)
            win.validate_private_directory(private)
            identities = {key: win.WorkerIdentity(row['name'], row['credential_target'])
                          for key, row in config['workers'].items()}
            private, roots = win._layout_paths(private, config['slot_roots'], identities)
            boundaries = win._layout_boundaries(private, roots)
            win._validate_control_ancestors(boundaries[0].parent)
            worker_sids = [win._sid_text(win._account_sid(value.name)) for value in identities.values()]
            for boundary in boundaries:
                win._validate_boundary(boundary, worker_sids)
            phase = 'native_custody'
            executable = NATIVE / (provider + '.exe')
            if Path(config['providers'][provider]['executable']) != executable:
                raise ValueError('Protected provider path differs from the reviewed native executable')
            ordinary_tree(executable)
            win.validate_code_path(executable)
            before = digest_file(executable)
            if before != CONTRACTS[provider]['sha256']:
                raise ValueError('Native executable differs from the reviewed status contract')
            limits = ResourceLimits.from_dict(config['execution_limits'])
            report.update(layout_sha256=layout_hash, config_sha256=config_hash,
                          native_executable_sha256_before=before, runtime_custody_verified=True)
            with tempfile.TemporaryFile(mode='w+b', dir=private) as empty, \
                    tempfile.TemporaryFile(mode='w+b', dir=private) as output, \
                    tempfile.TemporaryFile(mode='w+b', dir=private) as errors:
                phase = 'native_launch'
                argv = [str(executable), *CONTRACTS[provider]['arguments']]
                with launch_status_process(win, identity, argv, spec['root'], empty, output, errors, limits, report) as process:
                    report['native_status_commands_executed'] = 1
                    phase = 'native_attestation'
                    report['process'] = observe_child(win, process, expected_sid, executable)
                    report['process']['native_resource_limits'] = process.resource_limits_evidence
                    phase = 'native_wait'
                    code = bounded_wait(process, output, errors)
                    phase = 'native_cleanup'
                report['cleanup_verified'] = True
                phase = 'native_result'
                output.seek(0); errors.seek(0)
                stdout, stderr = output.read(MAX_OUTPUT + 1), errors.read(MAX_OUTPUT + 1)
                report['output_bytes'] = {'stdout': len(stdout), 'stderr': len(stderr)}
                if len(stdout) + len(stderr) > MAX_OUTPUT:
                    raise ValueError('Native status output exceeded its bound')
                report['native_exit_code'] = code
                authentication = parse_status(provider, stdout, stderr, code)
                del stdout, stderr  # Raw provider text is never serialized or displayed.
            phase = 'native_custody'
            after = digest_file(executable)
            report['native_executable_sha256_after'] = after
            if after != before:
                raise ValueError('Native executable changed during status capture')
            phase = 'daemon_postcheck'
            require_stopped(win)
            report['daemon_states_verified_before_and_after'] = True
            report['authentication'] = authentication
            report['decision'] = authentication_decision(authentication)
            report['status'] = ('NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED'
                if report['authentication']['native_subscription_authentication_verified'] else 'AUTHENTICATION_NOT_VERIFIED')
        except BaseException as exc:
            report['status'] = 'STATUS_CHECK_FAILED'
            # Exception messages may contain native output; disclose a fixed
            # classification only. Failed/unverified cleanup blocks acceptance.
            report['failure_kind'] = ('cleanup_unverified' if isinstance(exc, win.WindowsCleanupError)
                                      else 'timeout' if isinstance(exc, TimeoutError) else 'verification_failed')
            report['operator_review_required'] = True
            report['failure'] = safe_failure(exc, 'native_cleanup' if isinstance(exc, win.WindowsCleanupError) else phase)
        finally:
            if lock:
                win._close(lock)
            report['finished_at_unix_ms'] = int(time.time() * 1000)
            json.dump(report, receipt, ensure_ascii=True, sort_keys=True, indent=2)
            receipt.flush(); os.fsync(receipt.fileno())
    return 0 if report['decision'] in ('REUSE_VERIFIED_SESSION', 'ATTENDED_LOGIN_REQUIRED') else 2



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', choices=[f'slot{i}' for i in range(1, 5)], required=True)
    parser.add_argument('--provider', choices=sorted(CONTRACTS), required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--stage', choices=('before','after'), required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{32}', args.nonce):
        parser.error('A 32-hex one-shot receipt nonce is required')
    try:
        return run(args.slot, args.provider, args.nonce, args.stage)
    except BaseException as exc:
        print(json.dumps({'status': 'PRE_RECEIPT_FAILURE', 'failure': safe_failure(exc, 'trusted_preflight')}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
