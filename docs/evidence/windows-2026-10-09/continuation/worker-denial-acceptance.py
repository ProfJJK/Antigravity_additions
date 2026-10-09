"""One reviewed worker's handle-only denial check. No IOCTL or content reads.

The operator wrapper stages this under a fresh protected root. Both modes refuse
other paths/interpreters. Only SYSTEM mode imports the installed pipeline.
"""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
PYTHON = INSTALL / '.venv/Scripts/python.exe'
BASE_PYTHON = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
BASE_SHA256 = 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
WINDOWS_SHA256 = 'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba'
DRIVER = Path(r'C:\Windows\System32\DriverStore\FileRepository\pawnio.inf_amd64_a92d2610b0f4c4c8\PawnIO.sys')
DRIVER_SHA256 = 'f92de04e5a02256e86ffa1b4252fe669282198b7329227f40e51bca9267d133d'
DEVICE = r'\\?\GLOBALROOT\Device\PawnIO'
DAEMON_TASK_NAMES = ('CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor',
                     'CoChem-4.2.2-Warden', 'CoChem-4.2.3-Supervisor')
MAX_OUTPUT = 65536
STATUS_SECONDS = 30
DEVICE_CASES = {'lhm_mask3': (3, 0x80), 'generic_read_write': (0xC0000000, 0x80),
                'zero_access': (0, 0x80), 'zero_access_overlapped': (0, 0x40000000)}


class SidAndAttributes(C.Structure):
    _fields_ = [('sid', W.LPVOID), ('attributes', W.DWORD)]


class TokenGroups(C.Structure):
    _fields_ = [('count', W.DWORD), ('groups', SidAndAttributes * 1)]


class Kernel:
    """Initialize only in an explicitly reviewed live check; no import effects."""
    def __init__(self):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        declarations = (
            (self.k, 'CreateFileW', W.HANDLE, [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]),
            (self.k, 'CloseHandle', W.BOOL, [W.HANDLE]),
            (self.k, 'GetCurrentProcess', W.HANDLE, []),
            (self.k, 'LocalFree', W.HANDLE, [W.HANDLE]),
            (self.a, 'OpenProcessToken', W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
            (self.a, 'GetTokenInformation', W.BOOL, [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]),
            (self.a, 'ConvertSidToStringSidW', W.BOOL, [W.LPVOID, C.POINTER(W.LPWSTR)]),
            (self.a, 'GetKernelObjectSecurity', W.BOOL, [W.HANDLE, W.DWORD, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]),
            (self.a, 'IsValidSecurityDescriptor', W.BOOL, [W.LPVOID]),
            (self.a, 'GetSecurityDescriptorDacl', W.BOOL, [W.LPVOID, C.POINTER(W.BOOL), C.POINTER(W.LPVOID), C.POINTER(W.BOOL)]),
            (self.a, 'GetSecurityDescriptorOwner', W.BOOL, [W.LPVOID, C.POINTER(W.LPVOID), C.POINTER(W.BOOL)]),
            (self.a, 'GetSecurityDescriptorControl', W.BOOL, [W.LPVOID, C.POINTER(W.WORD), C.POINTER(W.DWORD)]),
            (self.a, 'GetAce', W.BOOL, [W.LPVOID, W.DWORD, C.POINTER(W.LPVOID)]),
            (self.a, 'ConvertSecurityDescriptorToStringSecurityDescriptorW', W.BOOL,
             [W.LPVOID, W.DWORD, W.DWORD, C.POINTER(W.LPWSTR), C.POINTER(W.DWORD)]),
        )
        for dll, name, result, args in declarations:
            fn = getattr(dll, name); fn.restype = result; fn.argtypes = args

    def checked(self, ok):
        if not ok:
            raise C.WinError(C.get_last_error())

    def sid(self, address):
        text = W.LPWSTR()
        self.checked(self.a.ConvertSidToStringSidW(address, C.byref(text)))
        try:
            return text.value
        finally:
            self.k.LocalFree(C.cast(text, W.HANDLE))

    def token_bytes(self, token, kind):
        length = W.DWORD()
        self.a.GetTokenInformation(token, kind, None, 0, C.byref(length))
        if not 1 <= length.value <= 65536:
            raise ValueError('Unexpected token metadata bound')
        data = C.create_string_buffer(length.value)
        self.checked(self.a.GetTokenInformation(token, kind, data, length, C.byref(length)))
        return data

    def token(self, process):
        token = W.HANDLE()
        self.checked(self.a.OpenProcessToken(process, 8, C.byref(token)))
        try:
            data = self.token_bytes(token, 1)
            sid = self.sid(SidAndAttributes.from_buffer(data).sid)
            elevated = bool(W.DWORD.from_buffer(self.token_bytes(token, 20)).value)
            groups = self.token_bytes(token, 2)
            count = W.DWORD.from_buffer(groups).value
            offset = TokenGroups.groups.offset
            if count > 4096 or offset + count * C.sizeof(SidAndAttributes) > C.sizeof(groups):
                raise ValueError('Unexpected token group bound')
            enabled_admin = False
            for i in range(count):
                row = SidAndAttributes.from_buffer(groups, offset + i * C.sizeof(SidAndAttributes))
                if self.sid(row.sid) == 'S-1-5-32-544' and row.attributes & 4 and not row.attributes & 16:
                    enabled_admin = True
            return {'sid': sid, 'elevated': elevated, 'administrators_enabled': enabled_admin}
        finally:
            self.checked(self.k.CloseHandle(token))


def require_worker_token(record, expected):
    if (not isinstance(record, dict) or record.get('sid') != expected
            or expected in {'S-1-5-18', 'S-1-5-32-544'}
            or record.get('elevated') is not False or record.get('administrators_enabled') is not False):
        raise ValueError('Actual worker token is wrong or privileged')


def attempt_handle(api, path, access, flags):
    """OPEN_EXISTING only; no data/IOCTL calls, including after unexpected success."""
    C.set_last_error(0)
    handle = api.k.CreateFileW(path, access, 3, None, 3, flags, None)
    error = C.get_last_error()
    if handle == W.HANDLE(-1).value:
        return {'opened': False, 'winerror': error, 'classification': 'DENIED' if error == 5 else 'INCONCLUSIVE'}
    api.checked(api.k.CloseHandle(handle))
    return {'opened': True, 'winerror': 0, 'classification': 'OPENED_AND_CLOSED'}


def acl_acceptable(record):
    rules = record.get('aces')
    return (record.get('owner_sid') in ('S-1-5-18', 'S-1-5-32-544')
            and record.get('dacl_present') is True and record.get('protected') is True
            and isinstance(rules, list) and 1 <= len(rules) <= 64
            and all(isinstance(row, dict) and row.get('type') in ('allow', 'deny')
                    and type(row.get('mask')) is int and type(row.get('flags')) is int
                    and (row['type'] != 'allow' or row.get('sid') in ('S-1-5-18', 'S-1-5-32-544'))
                    for row in rules)
            and any(row.get('type') == 'allow' and row.get('sid') == 'S-1-5-18' for row in rules))


def device_acl(api):
    """Bounded SYSTEM owner/DACL query; never requests SACL or hardware IOCTL."""
    handle = api.k.CreateFileW(DEVICE, 0x20000, 3, None, 3, 0x80, None)
    if handle == W.HANDLE(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        size = W.DWORD()
        api.a.GetKernelObjectSecurity(handle, 5, None, 0, C.byref(size))
        if not 1 <= size.value <= 65536:
            raise ValueError('Device descriptor size unavailable or out of bounds')
        data = C.create_string_buffer(size.value)
        api.checked(api.a.GetKernelObjectSecurity(handle, 5, data, len(data), C.byref(size)))
        api.checked(api.a.IsValidSecurityDescriptor(data))
        owner, owner_defaulted = W.LPVOID(), W.BOOL()
        api.checked(api.a.GetSecurityDescriptorOwner(data, C.byref(owner), C.byref(owner_defaulted)))
        owner_sid = api.sid(owner.value) if owner.value else None
        present, defaulted, acl = W.BOOL(), W.BOOL(), W.LPVOID()
        api.checked(api.a.GetSecurityDescriptorDacl(data, C.byref(present), C.byref(acl), C.byref(defaulted)))
        control, revision = W.WORD(), W.DWORD()
        api.checked(api.a.GetSecurityDescriptorControl(data, C.byref(control), C.byref(revision)))
        rows = []
        if present.value and acl.value:
            header = C.string_at(acl.value, 8)
            count = int.from_bytes(header[4:6], 'little')
            if not 1 <= count <= 64:
                raise ValueError('Device ACE count out of bounds')
            for index in range(count):
                ace = W.LPVOID()
                api.checked(api.a.GetAce(acl, index, C.byref(ace)))
                head = C.string_at(ace.value, 8)
                if head[0] not in (0, 1) or int.from_bytes(head[2:4], 'little') < 16:
                    raise ValueError('Unsupported device ACE requires review')
                rows.append({'type': 'allow' if head[0] == 0 else 'deny', 'flags': head[1],
                             'mask': int.from_bytes(head[4:8], 'little'), 'sid': api.sid(ace.value + 8)})
        text, text_size = W.LPWSTR(), W.DWORD()
        api.checked(api.a.ConvertSecurityDescriptorToStringSecurityDescriptorW(data, 1, 5, C.byref(text), C.byref(text_size)))
        try:
            if text_size.value > 65536:
                raise ValueError('Device SDDL exceeds bound')
            sddl = text.value
        finally:
            api.k.LocalFree(C.cast(text, W.HANDLE))
        result = {'owner_sid': owner_sid, 'dacl_present': bool(present.value and acl.value), 'protected': bool(control.value & 0x1000),
                  'aces': rows, 'sddl': sddl, 'sddl_sha256': hashlib.sha256(sddl.encode()).hexdigest(),
                  'query': 'READ_CONTROL/GetKernelObjectSecurity owner+DACL; no SACL/IOCTL'}
        result['acceptable'] = acl_acceptable(result)
        return result
    finally:
        api.checked(api.k.CloseHandle(handle))


def targets_for(slot):
    base = r'C:\ProgramData\CoChemPipeline427'
    rows = {'own_workspace': (base + '\\workers\\' + slot, 1, 0x02000000),
            'private': (base + r'\private', 1, 0x02000000),
            'controller': (r'C:\Users\ansac\CoChem427\controller.token', 0x80000000, 0x80)}
    rows.update({'sibling_' + other: (base + '\\workers\\' + other, 1, 0x02000000)
                 for other in (f'slot{i}' for i in range(1, 7)) if other != slot})
    return rows


def validate_child_result(raw, nonce, slot, expected_sid, expected_pid):
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT:
        raise ValueError('Child receipt size invalid')
    value = strict_json(raw.decode('utf-8'))
    if (not isinstance(value, dict) or set(value) != {'schema', 'nonce', 'slot', 'pid', 'token', 'handles', 'ioctls_sent', 'contents_read'}
            or value['schema'] != 'cochem-worker-handle-denial/1' or value['nonce'] != nonce
            or value['slot'] != slot or value['pid'] != expected_pid
            or type(value['pid']) is not int
            or type(value['ioctls_sent']) is not int or value['ioctls_sent'] != 0
            or type(value['contents_read']) is not int or value['contents_read'] != 0):
        raise ValueError('Child receipt binding differs')
    require_worker_token(value['token'], expected_sid)
    rows = value['handles']
    expected = set(targets_for(slot)) | {'device_' + key for key in DEVICE_CASES}
    if not isinstance(rows, dict) or set(rows) != expected:
        raise ValueError('Missing or extra handle test')
    passed = True
    for key, row in rows.items():
        if (not isinstance(row, dict) or set(row) != {'opened', 'winerror', 'classification'}
                or type(row['opened']) is not bool or type(row['winerror']) is not int
                or not 0 <= row['winerror'] <= 0xFFFFFFFF):
            raise ValueError('Invalid handle observation')
        expected_class = 'OPENED_AND_CLOSED' if row['opened'] else ('DENIED' if row['winerror'] == 5 else 'INCONCLUSIVE')
        if row['classification'] != expected_class or (row['opened'] and row['winerror'] != 0):
            raise ValueError('Contradictory handle observation')
        passed &= row['opened'] if key == 'own_workspace' else (not row['opened'] and row['winerror'] == 5)
    return value, bool(passed)


def worker_mode(slot, nonce):
    root = Path(r'C:\Program Files\CoChem') / f'WorkerDenial4.2.7-windows-20261006-{slot}'
    if Path(__file__).resolve() != root / 'worker-denial-acceptance.py' or Path(sys.executable).resolve() != BASE_PYTHON:
        raise ValueError('Use only protected denial child and base interpreter')
    # No device API before parent verifies token/image/Job and releases this line.
    raw = sys.stdin.buffer.readline(4097)
    if len(raw) > 4096:
        raise ValueError('Parent release exceeds bound')
    release = strict_json(raw)
    if (not isinstance(release, dict) or set(release) != {'nonce', 'slot', 'expected_sid'}
            or release['nonce'] != nonce or release['slot'] != slot
            or not re.fullmatch(r'S-1-5-21-(?:[0-9]+-){3}[0-9]+', release['expected_sid'])):
        raise ValueError('Parent release binding differs')
    api = Kernel()
    token = api.token(api.k.GetCurrentProcess())
    require_worker_token(token, release['expected_sid'])
    rows = {}
    for key, (path, access, flags) in targets_for(slot).items():
        rows[key] = attempt_handle(api, path, access, flags)
    for key, (access, flags) in DEVICE_CASES.items():
        rows['device_' + key] = attempt_handle(api, DEVICE, access, flags)
    result = {'schema': 'cochem-worker-handle-denial/1', 'nonce': nonce, 'slot': slot,
              'pid': os.getpid(), 'token': token, 'handles': rows, 'ioctls_sent': 0, 'contents_read': 0}
    print(json.dumps(result, allow_nan=False), flush=True)
    return 0


# Functions below copied from reviewed native status helper SHA256 a5a54c00...00ab4d.
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


def validate_layout(layout, config):
    slots = {f'slot{i}' for i in range(1, 7)}
    if (set(layout['slots']) != slots or set(config['workers']) != slots or set(config['slot_roots']) != slots
            or config['max_execution_slots'] != 4 or layout['private_root'] != r'C:\ProgramData\CoChemPipeline427\private'
            or config['private_root'] != layout['private_root'] or layout['operator_name'] != 'AETHERDESK\\ansac'
            or config['operator_name'] != layout['operator_name']
            or config['token_file'] != r'C:\Users\ansac\CoChem427\controller.token'):
        raise ValueError('Installed six-identity/four-slot mapping differs')
    for slot in slots:
        row, worker = layout['slots'][slot], config['workers'][slot]
        if (row['identity'] != worker['name'] or worker['name'] != 'CoChem422Worker' + slot[4:]
                or row['credential_target'] != worker['credential_target'] or worker['credential_target'] != 'CoChem422/' + slot
                or row['root'] != config['slot_roots'][slot] or row['root'] != targets_for(slot)['own_workspace'][0]):
            raise ValueError('Installed worker identity/root differs')
    if len({layout['slots'][slot]['sid'] for slot in slots}) != 6:
        raise ValueError('Installed worker identities are not distinct')


def safe_failure(error, phase):
    """Bounded diagnostic metadata only; never exception messages/paths/output."""
    allowed = {'trusted_preflight', 'reservation', 'runtime_custody', 'layout', 'boundaries',
               'daemon_precheck', 'device_query', 'worker_launch', 'worker_attestation',
               'worker_wait', 'worker_cleanup', 'worker_result', 'daemon_postcheck', 'worker_entry'}
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
    return {'phase': phase if phase in allowed else 'trusted_preflight',
            'error_type': kind, 'winerror': code}


def run(slot, nonce):
    from cochem_pipeline import windows as win
    from cochem_pipeline.ramdisk import ordinary_tree
    from cochem_pipeline.resource_limits import ResourceLimits
    win.require_system()
    root = Path(r'C:\Program Files\CoChem') / f'WorkerDenial4.2.7-windows-20261006-{slot}'
    if Path(__file__).resolve() != root / 'worker-denial-acceptance.py' or Path(sys.executable).resolve() != PYTHON:
        raise ValueError('Use only protected supervisor and installed interpreter')
    # Establish a trusted receipt destination before runtime/config/device work.
    # Failure before this point must not write into an unverified directory.
    for path in (root, Path(__file__), PYTHON, Path(win.__file__)):
        ordinary_tree(path); win.validate_code_path(path)
    report = {'schema': 'cochem-worker-denial-acceptance/1', 'nonce': nonce, 'slot': slot,
              'system_sid': win.SYSTEM_SID, 'status': 'UNVERIFIED', 'cleanup_verified': False,
              'helper_sha256': digest_file(Path(__file__)), 'windows_runtime_sha256': WINDOWS_SHA256,
              'base_interpreter_sha256': BASE_SHA256, 'layout_sha256': None, 'config_sha256': None,
              'installed_driver_file_sha256': DRIVER_SHA256,
              'runtime_custody_verified': False,
              'logon_uses_existing_system_worker_credential': True,
              'started_at_unix_ms': int(time.time() * 1000), 'device': DEVICE, 'ioctls_sent': 0,
              'tested_target_contents_read': 0, 'worker_dispatch_attempted': False, 'worker_released': False,
              'pipeline_started': False, 'docker_denial_tested': False, 'repair_identity_tested': False}
    with (root / 'worker-denial-acceptance.json').open('x', encoding='utf-8') as receipt:
        phase = 'reservation'
        lock = None
        try:
            C.set_last_error(0)
            lock = win._api()['kernel32'].CreateMutexW(None, False, 'Global\\CoChem427-WorkerDenialSetup')
            win._check(lock, 'Reserve denial acceptance')
            if C.get_last_error() == 183:
                raise ValueError('Another denial check is active')
            phase = 'runtime_custody'
            for path in (BASE_PYTHON, DRIVER):
                ordinary_tree(path); win.validate_code_path(path)
            if (digest_file(Path(win.__file__)) != WINDOWS_SHA256 or digest_file(BASE_PYTHON) != BASE_SHA256
                    or digest_file(DRIVER) != DRIVER_SHA256):
                raise ValueError('Reviewed launch runtime or base interpreter drift')
            report['runtime_custody_verified'] = True
            phase = 'layout'
            layout, layout_hash = protected_json(win, INSTALL / 'windows-layout.json')
            config, config_hash = protected_json(win, INSTALL / 'pipeline.json')
            report.update(layout_sha256=layout_hash, config_sha256=config_hash)
            validate_layout(layout, config)
            identities = {key: win.WorkerIdentity(row['name'], row['credential_target']) for key, row in config['workers'].items()}
            for key, identity in identities.items():
                if win._sid_text(win._account_sid(identity.name)) != layout['slots'][key]['sid']:
                    raise ValueError('Actual worker SID differs from installed mapping')
            phase = 'boundaries'
            private, roots = win._layout_paths(config['private_root'], config['slot_roots'], identities)
            win.validate_private_directory(private)
            boundaries = win._layout_boundaries(private, roots)
            win._validate_control_ancestors(boundaries[0].parent)
            for boundary in boundaries:
                win._validate_boundary(boundary, [layout['slots'][key]['sid'] for key in identities])
            win.validate_controller_token(config['token_file'], config['operator_name'], identities)
            for path, _, _ in targets_for(slot).values():
                ordinary_tree(Path(path))
                if not Path(path).exists():
                    raise ValueError('Actual denial target is absent')
            expected_sid = layout['slots'][slot]['sid']
            limits = ResourceLimits.from_dict(config['execution_limits'])
            phase = 'daemon_precheck'
            require_stopped(win)
            phase = 'device_query'
            api = Kernel()
            report['device_acl'] = device_acl(api)
            if not report['device_acl']['acceptable']:
                raise ValueError('Installed device ACL needs review')
            with tempfile.TemporaryFile(mode='w+b', dir=private) as output, tempfile.TemporaryFile(mode='w+b', dir=private) as errors:
                read_fd, write_fd = os.pipe()
                with os.fdopen(read_fd, 'rb', buffering=0) as reader, os.fdopen(write_fd, 'wb', buffering=0) as writer:
                    phase = 'daemon_precheck'
                    require_stopped(win)
                    phase = 'worker_launch'
                    report['worker_dispatch_attempted'] = True
                    argv = [str(BASE_PYTHON), '-I', '-B', str(Path(__file__)), '--worker', '--slot', slot, '--nonce', nonce]
                    with win.launch_worker(identities[slot], argv, roots[slot], reader, output, errors, limits=limits) as process:
                        phase = 'worker_attestation'
                        report['process'] = observe_child(win, process, expected_sid, BASE_PYTHON)
                        actual_token = api.token(process._process)
                        require_worker_token(actual_token, expected_sid)
                        report['process']['token_details'] = actual_token
                        report['process']['native_resource_limits'] = process.resource_limits_evidence
                        writer.write(json.dumps({'nonce': nonce, 'slot': slot, 'expected_sid': expected_sid}).encode() + b'\n')
                        writer.close(); report['worker_released'] = True
                        phase = 'worker_wait'
                        code = bounded_wait(process, output, errors)
                        phase = 'worker_cleanup'
                    report['cleanup_verified'] = True
                phase = 'worker_result'
                output.seek(0); errors.seek(0)
                raw, err = output.read(MAX_OUTPUT + 1), errors.read(MAX_OUTPUT + 1)
                if code != 0 or err or len(raw) + len(err) > MAX_OUTPUT:
                    raise ValueError('Worker exit/output invalid')
                child, passed = validate_child_result(raw, nonce, slot, expected_sid, report['process']['pid'])
                report['worker_result'] = child
            phase = 'daemon_postcheck'
            require_stopped(win)
            report['daemon_states_verified_before_and_after'] = True
            report['status'] = 'HANDLE_DENIALS_VERIFIED' if passed else 'HANDLE_DENIALS_FAILED'
        except BaseException as exc:
            report['status'] = 'DENIAL_CHECK_FAILED'
            report['failure_kind'] = ('cleanup_unverified' if isinstance(exc, win.WindowsCleanupError)
                                      else 'timeout' if isinstance(exc, TimeoutError) else 'verification_failed')
            report['operator_review_required'] = True
            report['failure'] = safe_failure(exc, 'worker_cleanup' if isinstance(exc, win.WindowsCleanupError) else phase)
        finally:
            if lock:
                win._close(lock)
            report['finished_at_unix_ms'] = int(time.time() * 1000)
            json.dump(report, receipt, sort_keys=True, indent=2, allow_nan=False)
            receipt.flush(); os.fsync(receipt.fileno())
    return 0 if report['status'] == 'HANDLE_DENIALS_VERIFIED' and report['cleanup_verified'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', choices=[f'slot{i}' for i in range(1, 7)], required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{32}', args.nonce):
        parser.error('A reviewed nonce is required')
    try:
        if args.worker:
            return worker_mode(args.slot, args.nonce)
        return run(args.slot, args.nonce)
    except BaseException as exc:
        # No untrusted-root writes. The child stream, when used, is SYSTEM-private;
        # an untrusted bootstrap failure emits only bounded diagnostic metadata.
        print(json.dumps({'status': 'PRE_RECEIPT_FAILURE', 'failure': safe_failure(
            exc, 'worker_entry' if args.worker else 'trusted_preflight')}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
