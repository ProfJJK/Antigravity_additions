"""Ordinary operator, explicit live opt-in. Default prints a plan and does no I/O.

Two fixed workflow IDs; a durable attempt record precedes each possible POST.
An interrupted/ambiguous submission is observed only, never resubmitted. A
finite observation window does not cancel, retry, reset or fail server work.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import ctypes as C
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PACKAGES = INSTALL / '.venv/Lib/site-packages'
PYTHON = INSTALL / '.venv/Scripts/python.exe'
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
OPERATOR = Path(r'C:\Users\ansac\CoChem427')
ROOT = OPERATOR / 'live-commissioning-r3-v1'
CLIENT = OPERATOR / 'pipeline-client-r3.json'
TOKEN = OPERATOR / 'controller.token'
STARTUP = Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json')
PLANNING_VALIDATOR = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\verify_pipeline_acceptance.py')
PINS = {
    'config': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
    'install': '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6',
    'manifest': '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1',
    'revision': '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4',
    'windows': 'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba',
    'python': '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e',
    'base': 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa',
    'venv': '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d',
    'client': '9059f6e65d3259dff6e595f3f4f131983fd827088034db283913a7285411ea5b',
    'planning_validator': '206cbeea48774042fa676ca71350ac3c17cd468619cc2cfbd7ba5bc999f6dca0',
    'coding_validator': '77369f7585970ab7ab7e8e965de9d711635c7dc63724b5101dc04884ed852a7f',
}
PLANNING = {
    'objective': 'Produce a complete six-chapter SRS and WBS for a local study-note application covering import, search, spaced review, privacy, backup and acceptance validation.',
    'requirements': ['REQ-IMPORT', 'REQ-SEARCH', 'REQ-REVIEW', 'REQ-PRIVACY', 'REQ-BACKUP', 'REQ-VALIDATE'],
    'chapter_count': 6,
}
CODING = {
    'project_id': 'windows-acceptance',
    'objective': 'Complete the disposable calculator project on its registered pipeline/accepted branch. Implement src/calculator.py add(left, right), replacing the existing return None with correct addition. Preserve the function signature. Add and execute pytest tests under tests for positive, negative, zero, and finite fractional inputs. Use the canonical planning, RED/GREEN, review and Git integration workflow; no external dependency, network operation or unrelated feature.',
    'requirements': ['REQ-ADD: add(left, right) returns the sum of numeric inputs without changing the signature.',
                     'REQ-TEST: pytest covers positive, negative, zero and finite fractional values, including a proven failing test before implementation.',
                     'REQ-INTEGRATE: all tests pass and only the registered disposable project branch receives the reviewed implementation.'],
}
LIMIT = 32 * 1024 * 1024


class Held(ValueError):
    """Arguments are fixed diagnostic enums, never native/HTTP output."""


class HTTPHeld(Held):
    def __init__(self, status):
        self.http_status = int(status)
        super().__init__('http_rejected_or_missing_is_not_resubmission_authority')


def require(value, code):
    if not value:
        raise Held(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Held('nonfinite_json')))


def requests():
    identity = digest({'purpose': 'windows427-r3-commissioning-v1', 'config': PINS['config'],
                       'planning': PLANNING, 'coding': CODING})[:20]
    return {
        'planning': {'workflow_id': 'windows427-r3-plan-' + identity, **PLANNING},
        'coding': {'workflow_id': 'windows427-r3-code-' + identity, **CODING},
    }


def ordinary(path, *, missing=False):
    path = Path(path)
    require(path.is_absolute(), 'absolute_path_required')
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing and item == path:
                return None
            raise
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'reparse_path_refused')
        if item == path:
            require(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'ordinary_single_link_required')
    return info


@contextmanager
def held_file(path, *, write=False, create=False, exclusive=False):
    """Exact Win32 handle, no write/delete sharing, checked against path identity."""
    import msvcrt
    path = Path(path)
    before = ordinary(path, missing=create)
    k = C.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p]
    k.CreateFileW.restype = C.c_void_p
    access = 0xC0000000 if write else 0x80000000
    handle = k.CreateFileW(str(path), access, 0 if exclusive else 1, None, 1 if create else 3, 0x200080, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if write else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        k.CloseHandle.argtypes = [C.c_void_p]; k.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'r+b' if write else 'rb') as stream:
        actual, now = os.fstat(stream.fileno()), ordinary(path)
        require(actual.st_nlink == 1 and (actual.st_dev, actual.st_ino) == (now.st_dev, now.st_ino), 'file_identity_changed')
        if before is not None:
            require((before.st_dev, before.st_ino) == (actual.st_dev, actual.st_ino), 'file_replaced_during_open')
        yield stream
        final = ordinary(path)
        require((actual.st_dev, actual.st_ino) == (final.st_dev, final.st_ino), 'file_replaced_while_held')


def pinned(stack, path, pin=None, maximum=LIMIT):
    stream = stack.enter_context(held_file(path))
    before = os.fstat(stream.fileno())
    require(before.st_size <= maximum, 'file_size_bound')
    raw = stream.read(maximum + 1)
    require(len(raw) <= maximum and len(raw) == before.st_size, 'file_read_size_changed')
    actual = hashlib.sha256(raw).hexdigest()
    require(pin is None or actual == pin, 'file_digest_changed')
    return raw, actual


class WindowsPrivate:
    """Operator-private output only; no ACL repair or privileged state access."""
    def __init__(self, win, sid):
        self.win, self.sid = win, sid
        self.trusted = {sid, win.SYSTEM_SID, win.ADMIN_SID}

    def validate(self, path):
        info = ordinary(path)
        owner, _, rules = self.win._acl(Path(path))
        require(owner in self.trusted and rules, 'private_owner_acl')
        effective = [(sid, mask, flags) for sid, mask, flags in rules if not flags & 8]
        require(not any(sid not in self.trusted for sid, _, _ in rules), 'private_extra_trustee')
        require(all(not flags & 8 and mask == self.win.FULL_CONTROL for _, mask, flags in effective), 'private_rights')
        require({sid for sid, _, _ in effective} == self.trusted, 'private_effective_grants')
        return info

    def validate_ancestry(self, path):
        ordinary(path)
        trusted = self.trusted | {self.win.TRUSTED_INSTALLER_SID}
        for item in (Path(path), *Path(path).parents):
            owner, _, rules = self.win._acl(item)
            require(owner in trusted and not any(sid not in trusted and not flags & 8 and mask & 0xD0040
                                                for sid, mask, flags in rules), 'operator_parent_replaceable')

    def create_directory(self, path):
        self.validate_ancestry(path.parent); self.validate(path.parent)
        require(ordinary(path, missing=True) is None, 'private_root_already_exists')
        api = self.win._api(); descriptor = self.win.HANDLE()
        sddl = f'O:{self.sid}G:{self.sid}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{self.sid})'
        self.win._check(api['advapi32'].ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None), 'Build operator evidence ACL')
        k = api['kernel32']; k.CreateDirectoryW.argtypes = [C.c_wchar_p, C.c_void_p]; k.CreateDirectoryW.restype = C.c_int
        try:
            attributes = self.win._SECURITY_ATTRIBUTES(C.sizeof(self.win._SECURITY_ATTRIBUTES), descriptor, False)
            self.win._check(k.CreateDirectoryW(str(path), C.byref(attributes)), 'Create new operator evidence directory')
        finally:
            k.LocalFree(descriptor)
        self.validate(path)


class Journal:
    def __init__(self, root, private):
        self.root, self.private = Path(root), private

    def inventory(self):
        self.private.validate(self.root)
        rows = list(self.root.iterdir())
        require(len(rows) <= 1024, 'evidence_file_count_bound')
        total = 0
        for path in rows:
            info = self.private.validate(path)
            require(stat.S_ISREG(info.st_mode), 'unexpected_evidence_subdirectory')
            total += info.st_size
        require(total <= 512 * 1024 * 1024, 'evidence_total_size_bound')
        return rows

    def exists(self, name):
        return ordinary(self.root / name, missing=True) is not None

    def read(self, name):
        path = self.root / name; self.private.validate(path)
        with ExitStack() as stack:
            raw, _ = pinned(stack, path)
            return strict_json(raw)

    def write(self, name, value):
        require(re.fullmatch('[a-z0-9_-]+[.]json', name) is not None, 'journal_name')
        self.inventory(); raw = encoded(value)
        require(len(raw) <= LIMIT, 'journal_record_size')
        # Parent was created with final OI+CI grants before any private bytes.
        with held_file(self.root / name, write=True, create=True, exclusive=True) as stream:
            self.private.validate(self.root / name)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())

    def observe(self, phase, workflow):
        sha = digest(workflow)
        name = 'snapshot-' + phase + '-' + sha + '.json'
        if self.exists(name):
            require(self.read(name) == workflow, 'snapshot_digest_collision')
        else:
            self.write(name, workflow)
        return {'path': str(self.root / name), 'sha256': sha}

    @contextmanager
    def locked(self):
        path = self.root / 'invocation.lock'
        self.private.validate(self.root)
        fresh = ordinary(path, missing=True) is None
        with held_file(path, write=True, create=fresh, exclusive=True):
            self.private.validate(path)
            yield


class BoundedClient:
    """Fixed loopback only, no redirects/retries; token remains in memory only."""
    def __init__(self, token, port=47824):
        self.token, self.port = token, port

    def call(self, operation, data=None):
        require(operation in ('/health', '/submit', '/coding/submit') or
                re.fullmatch(r'/(?:coding/)?workflow/windows427-r3-(?:plan|code)-[0-9a-f]{20}', operation), 'operation_not_allowed')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        try:
            connection.request('POST' if data is not None else 'GET', operation,
                               encoded(data) if data is not None else None,
                               {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'Connection': 'close'})
            response = connection.getresponse()
            deadline = time.monotonic() + 20
            parts, count = [], 0
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'controller_response_deadline')
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, LIMIT + 1 - count))
                if not chunk:
                    break
                parts.append(chunk); count += len(chunk)
                require(count <= LIMIT, 'controller_response_size')
            raw = b''.join(parts)
            require(len(raw) <= LIMIT, 'controller_response_size')
            if response.status >= 400:
                raise HTTPHeld(response.status)
            require(response.status in (200, 202), 'unexpected_http_success_code')
            return strict_json(raw)
        finally:
            connection.close()


def startup_binding(value):
    expected = {'schema': 'cochem-warden-commissioning/1', 'status': 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED',
                'runtime_root': str(INSTALL), 'install_receipt_sha256': PINS['install'], 'config_sha256': PINS['config'],
                'source_manifest_sha256': PINS['manifest'], 'revision_sha256': PINS['revision'],
                'authenticated_profiles_verified': 12, 'task_name': 'CoChem-4.2.7-Warden',
                'exactly_one_start_requested': True, 'monitoring_started': True, 'full_srs_acceptance': False,
                'model_jobs_submitted': 0}
    require(isinstance(value, dict) and all(type(value.get(k)) is type(v) and value[k] == v for k, v in expected.items()), 'startup_receipt_not_verified')
    controller = value.get('controller', {})
    require(type(controller.get('pid')) is int and controller['pid'] > 0 and
            isinstance(controller.get('instance_id'), str) and re.fullmatch('[0-9a-f]{32}', controller['instance_id']),
            'startup_controller_identity')
    return {'pid': controller['pid'], 'instance_id': controller['instance_id']}


def health_binding(health, controller, *, ready=False, phase='planning'):
    require(isinstance(health, dict) and health.get('service_identity') == 'SYSTEM' and
            health.get('pid') == controller['pid'] and health.get('instance_id') == controller['instance_id'] and
            health.get('hardware', {}).get('max_agents') == 4 and
            health.get('source_root') == str(INSTALL / '.venv/Lib'), 'controller_identity_or_capacity_changed')
    if ready:
        require(type(health.get('admission_capacity')) is int and 0 < health['admission_capacity'] <= 4 and
                health.get('knowledge', {}).get('ready') is True and health.get('quarantined_slots') == {}, 'controller_not_ready')
        components = health.get('components')
        required = {'knowledge', 'warden_controller'} | ({'docker_engine', 'containers'} if phase == 'coding' else set())
        require(isinstance(components, dict) and required <= components.keys() and
                all(components[key].get('state') == 'healthy' for key in required) and all(isinstance(item, dict) and
                (item.get('required') is not True or item.get('state') == 'healthy') for item in components.values()), 'required_component_not_healthy')
    return health


def request_binding(workflow, request, phase):
    require(isinstance(workflow, dict) and workflow.get('workflow_id') == request['workflow_id'], 'workflow_identity_changed')
    root = workflow.get('root', {})
    payload = root.get('payload', {})
    require(payload.get('objective') == request['objective'] and payload.get('requirements') == request['requirements'], 'workflow_request_changed')
    if phase == 'planning':
        require(root.get('kind') == 'MACRO_PLANNING_REQUEST' and payload.get('chapter_count') == 6, 'planning_request_shape')
    else:
        require(root.get('kind') == 'CODE_REQUEST' and payload.get('project_id') == 'windows-acceptance', 'coding_request_shape')
        state = workflow.get('coding', {})
        require(state.get('baseline_commit') == 'c52a3a97eb085e6bafbbd14bd6a75f3274288530' and
                state.get('project', {}).get('repository') == r'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance' and
                state.get('project', {}).get('branch') == 'pipeline/accepted', 'coding_disposable_baseline_changed')


def run_workflows(client, journal, intent, fresh, validate, *, observe_seconds=1800, poll_seconds=5,
                  clock=time.monotonic, sleep=time.sleep):
    """Only these two POST sites submit; persisted markers always precede them."""
    deadline = clock() + observe_seconds
    controller = intent['controller']
    passes = {}
    for phase in ('planning', 'coding'):
        request = intent['requests'][phase]
        if phase == 'coding' and clock() >= deadline:
            return {'status': 'PENDING', 'phase': phase, 'workflow_id': request['workflow_id'],
                    'planning_verified': True, 'observation_window_ended': True,
                    'coding_submission_deferred': True, 'server_work_cancelled': False}
        marker = phase + '-post-attempt.json'
        attempted = journal.exists(marker)
        if attempted:
            require(journal.read(marker) == {'schema': 'cochem-live-post-attempt/1', 'intent_sha256': digest(intent),
                    'phase': phase, 'request_sha256': digest(request), 'workflow_id': request['workflow_id']}, 'post_attempt_binding_changed')
        can_submit = not attempted and (fresh if phase == 'planning' else 'planning' in passes)
        health = health_binding(client.call('/health'), controller, ready=can_submit, phase=phase)
        if can_submit:
            journal.write(marker, {'schema': 'cochem-live-post-attempt/1', 'intent_sha256': digest(intent),
                          'phase': phase, 'request_sha256': digest(request), 'workflow_id': request['workflow_id']})
            workflow = client.call('/submit' if phase == 'planning' else '/coding/submit', request)
        else:
            # A 400/404/transport error never proves that an earlier POST did not happen.
            workflow = client.call(('/workflow/' if phase == 'planning' else '/coding/workflow/') + request['workflow_id'])
        while True:
            request_binding(workflow, request, phase)
            saved = journal.observe(phase, workflow)
            state = workflow.get('status')
            if state == 'COMPLETED':
                health = health_binding(client.call('/health'), controller)
                result = validate(phase, workflow, health)
                require(result.get('verified') is True if phase == 'planning' else result.get('accepted') is True, 'independent_acceptance_failed')
                confirmation = client.call(('/workflow/' if phase == 'planning' else '/coding/workflow/') + request['workflow_id'])
                request_binding(confirmation, request, phase)
                require(digest(confirmation) == digest(workflow), 'completed_workflow_changed')
                passes[phase] = {'workflow_id': request['workflow_id'], 'snapshot': saved, 'validation': result}
                name = phase + '-pass-' + digest(workflow) + '.json'
                # Revalidation timestamps can differ, so reuse only exact snapshot commitment.
                if journal.exists(name):
                    prior = journal.read(name)
                    require(prior.get('workflow_id') == request['workflow_id'] and prior.get('snapshot') == saved, 'prior_acceptance_binding_changed')
                else:
                    journal.write(name, passes[phase])
                break
            if state in ('FAILED', 'CANCELLED'):
                return {'status': 'HELD_WORKFLOW_TERMINAL', 'phase': phase, 'workflow_id': request['workflow_id'], 'snapshot': saved, 'server_status': state}
            require(isinstance(state, str) and 0 < len(state) <= 64, 'malformed_workflow_state')
            if clock() >= deadline:
                return {'status': 'PENDING', 'phase': phase, 'workflow_id': request['workflow_id'], 'snapshot': saved,
                        'observation_window_ended': True, 'server_work_cancelled': False}
            sleep(min(poll_seconds, max(0, deadline - clock())))
            health_binding(client.call('/health'), controller)
            workflow = client.call(('/workflow/' if phase == 'planning' else '/coding/workflow/') + request['workflow_id'])
    return {'status': 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED', 'workflows': passes, 'entire_host_certified': False,
            'monitoring_48h_acceptance_claimed': False}


def validate_coding_routes(workflow, routing, verifier):
    decisions = []
    for job in workflow.get('jobs', []):
        if job.get('status') == 'COMPLETED' and job.get('kind') not in ('CODE_REQUEST', 'CODE_TEST', 'CODE_INTEGRATE'):
            require(job.get('receipt', {}).get('execution_kind') == 'native_cli', 'coding_native_execution_required')
            decisions.append({'job_id': job['job_id'], **verifier(job, routing)})
    require(decisions, 'coding_native_routes_missing')
    return decisions


def code_acl(win, path):
    path = Path(path); ordinary(path)
    trusted = {win.SYSTEM_SID, win.ADMIN_SID, win.TRUSTED_INSTALLER_SID}
    base = Path(r'C:\Program Files')
    require(path == base or base in path.parents, 'protected_code_root')
    for item in (path, *path.parents):
        owner, _, rules = win._acl(item)
        require(owner in trusted and not any(sid not in trusted and not flags & 8 and mask & 0x500D0116
                                            for sid, mask, flags in rules), 'untrusted_protected_code_writer')
        if item == base:
            break


def prepare_runtime(stack):
    require(os.name == 'nt' and sys.version_info[:3] == (3, 12, 13) and sys.flags.isolated and sys.dont_write_bytecode
            and Path(sys.executable) == PYTHON and Path(sys._base_executable) == BASE, 'exact_isolated_r3_python_required')
    for path, pin in ((PYTHON, PINS['python']), (BASE, PINS['base']), (INSTALL / '.venv/pyvenv.cfg', PINS['venv']),
                      (PACKAGES / 'cochem_pipeline/windows.py', PINS['windows'])):
        pinned(stack, path, pin)
    from cochem_pipeline import windows as win
    api = win._api(); token = win.HANDLE()
    win._check(api['advapi32'].OpenProcessToken(api['kernel32'].GetCurrentProcess(), 8, C.byref(token)), 'Read operator identity')
    try:
        sid = win._sid_text(win._SID_AND_ATTRIBUTES.from_buffer(win._token_info(token, 1)).Sid)
        elevation = win._token_info(token, 20)
        require(C.c_uint32.from_buffer(elevation).value == 0, 'ordinary_operator_required')
    finally:
        win._close(token)
    require(sid == win._sid_text(win._account_sid(r'AETHERDESK\ansac')), 'expected_operator_required')
    private = WindowsPrivate(win, sid); private.validate_ancestry(OPERATOR); private.validate(OPERATOR)
    config_raw, _ = pinned(stack, INSTALL / 'pipeline.json', PINS['config'])
    config = strict_json(config_raw)
    _, _ = pinned(stack, INSTALL / 'install-after.json', PINS['install'])
    manifest_raw, _ = pinned(stack, INSTALL / 'source-manifest.json', PINS['manifest'])
    manifest = strict_json(manifest_raw)
    require(len(manifest.get('files', [])) == 166, 'source_manifest_count')
    expected = {}
    for row in manifest['files']:
        relative = Path(row['relative'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'source_manifest_path')
        source = INSTALL / 'source' / relative
        code_acl(win, source)
        raw, _ = pinned(stack, source, row['sha256'])
        require(len(raw) == row['length'], 'source_manifest_length')
        if relative.parts[0] == 'src' and relative.parts[1] in ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor') and relative.suffix in ('.py', '.md', '.json', '.xml'):
            asset = Path(*relative.parts[1:]); code_acl(win, PACKAGES / asset)
            pinned(stack, PACKAGES / asset, row['sha256']); expected[asset.as_posix()] = row['sha256']
    require(len(expected) == 109 and digest(expected) == PINS['revision'], 'installed_revision_digest')
    actual, queue, seen = set(), [PACKAGES / name for name in ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor')], 0
    while queue:
        directory = queue.pop(); ordinary(directory); code_acl(win, directory)
        for path in directory.iterdir():
            seen += 1; require(seen <= 2048, 'installed_inventory_bound')
            info = ordinary(path); code_acl(win, path)
            if stat.S_ISDIR(info.st_mode):
                queue.append(path)
            elif '__pycache__' not in path.parts and path.suffix in ('.py', '.md', '.json', '.xml'):
                actual.add(path.relative_to(PACKAGES).as_posix())
    require(actual == set(expected), 'installed_asset_inventory_changed')
    for path in (PYTHON, BASE, INSTALL / '.venv/pyvenv.cfg', INSTALL / 'pipeline.json', INSTALL / 'install-after.json', INSTALL / 'source-manifest.json'):
        code_acl(win, path)
    require(set(config['workers']) == {'slot' + str(n) for n in range(1, 7)} and config['max_execution_slots'] == 4, 'six_identity_four_capacity_required')
    project = config['coding_projects']['windows-acceptance']
    require(project['repository'] == r'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance' and project['branch'] == 'pipeline/accepted', 'registered_disposable_project')
    startup_raw, startup_sha = pinned(stack, STARTUP)
    code_acl(win, STARTUP); controller = startup_binding(strict_json(startup_raw))
    client_raw, _ = pinned(stack, CLIENT, PINS['client'], 4096)
    client = strict_json(client_raw)
    require(client == {'port': 47824, 'token_file': str(TOKEN)}, 'fixed_client_endpoint')
    validator_raw, _ = pinned(stack, PLANNING_VALIDATOR, PINS['planning_validator'])
    namespace = {'__name__': 'cochem_live_planning_validator', '__file__': str(PLANNING_VALIDATOR)}
    exec(compile(validator_raw, str(PLANNING_VALIDATOR), 'exec'), namespace)
    pinned(stack, PACKAGES / 'cochem_pipeline/coding_acceptance.py', PINS['coding_validator'])
    from cochem_pipeline.coding_acceptance import validate_coding_workflow
    from cochem_supervisor.probes import verify_routing_assignment
    def validate(phase, workflow, health):
        if phase == 'planning':
            admission = namespace['capture_admission'](config, health)
            return namespace['validate_workflow'](workflow, config['providers'], config.get('routing', {}), admission=admission)
        result = validate_coding_workflow(workflow)
        result['chapter06_routing_decisions'] = validate_coding_routes(workflow, config.get('routing', {}), verify_routing_assignment)
        return result
    return win, private, controller, startup_sha, validate


def run_live(observe_seconds, poll_seconds):
    with ExitStack() as stack:
        win, private, controller, startup_sha, validate = prepare_runtime(stack)
        raw_self, self_sha = pinned(stack, Path(__file__).absolute())
        del raw_self
        expected = {'schema': 'cochem-live-commissioning-intent/1', 'driver_sha256': self_sha,
                    'runtime_root': str(INSTALL), 'pins': PINS, 'startup_receipt_sha256': startup_sha,
                    'controller': controller, 'requests': requests(), 'no_automatic_resubmission': True}
        fresh = ordinary(ROOT, missing=True) is None
        if fresh:
            private.create_directory(ROOT)
        journal = Journal(ROOT, private)
        with journal.locked():
            if fresh:
                journal.write('intent.json', expected)
            else:
                require(journal.read('intent.json') == expected, 'existing_intent_not_exact')
            # Validate only metadata before reading this one token into memory.
            ordinary(TOKEN)
            owner, protected, rules = win._acl(TOKEN)
            require(owner == win.SYSTEM_SID and protected and set(rules) == {(win.SYSTEM_SID, win.FULL_CONTROL, 0),
                    (win.ADMIN_SID, win.FULL_CONTROL, 0), (private.sid, 0x120089, 0)}, 'controller_token_acl_changed')
            token_raw, _ = pinned(stack, TOKEN, maximum=256)
            token = token_raw.decode('ascii').strip(); del token_raw
            require(32 <= len(token) <= 256 and re.fullmatch('[A-Za-z0-9_-]+', token), 'controller_token_format')
            client = BoundedClient(token)
            try:
                result = run_workflows(client, journal, expected, fresh, validate,
                                       observe_seconds=observe_seconds, poll_seconds=poll_seconds)
            except (Exception, KeyboardInterrupt) as error:
                result = {'status': 'HELD_OBSERVATION_OR_SUBMISSION_UNCERTAIN', 'error_type': type(error).__name__,
                          'code': str(error) if isinstance(error, Held) else 'bounded_operation_failed',
                          'http_status': getattr(error, 'http_status', None), 'automatic_resubmission_allowed': False,
                          'server_work_cancelled': False}
            finally:
                client.token = ''; token = None
            number = len(journal.inventory())
            record = {'schema': 'cochem-live-commissioning-observation/1', 'intent_sha256': digest(expected),
                      'recorded_at': time.time(), 'result': result, 'full_srs_acceptance': False}
            journal.write(f'observation-{number:04d}.json', record)
            # Private snapshots and generated contents stay on this workstation.
            return {'status': result['status'], 'evidence_root': str(ROOT), 'workflow_ids': {k: v['workflow_id'] for k, v in requests().items()},
                    'phase': result.get('phase'), 'error_type': result.get('error_type'), 'code': result.get('code'),
                    'http_status': result.get('http_status'), 'server_work_cancelled': False,
                    'automatic_resubmission_allowed': False, 'entire_host_certified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true')
    parser.add_argument('--observe-seconds', type=float, default=1800)
    parser.add_argument('--poll-seconds', type=float, default=5)
    args = parser.parse_args(argv)
    require(math.isfinite(args.observe_seconds) and 1 <= args.observe_seconds <= 7200 and
            math.isfinite(args.poll_seconds) and 1 <= args.poll_seconds <= 60, 'observation_bounds')
    if not args.run_live:
        print(json.dumps({'schema': 'cochem-live-commissioning-plan/1', 'mode': 'PREVIEW_NO_IO',
                          'requests': requests(), 'runtime': str(INSTALL), 'pins': PINS,
                          'evidence_root': str(ROOT), 'startup_receipt_required': str(STARTUP),
                          'native_or_http_calls': 0, 'tokens_read': 0, 'files_created': 0,
                          'shared_capacity': 4, 'identities': 6, 'routing': 'unchanged Chapter 06',
                          'observation_seconds': args.observe_seconds, 'automatic_resubmission': False,
                          'server_timeout_cancellation': False, 'activation_claimed': False}, indent=2))
        return 0
    try:
        result = run_live(args.observe_seconds, args.poll_seconds)
    except (Exception, KeyboardInterrupt) as error:
        result = {'status': 'HELD_DRIVER_PREPARATION_OR_OBSERVATION_UNCERTAIN', 'error_type': type(error).__name__,
                  'code': str(error) if isinstance(error, Held) else 'private_custody_or_preflight_failed',
                  'server_work_cancelled': False, 'automatic_resubmission_allowed': False}
    print(json.dumps(result))
    return 0 if result['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED' else 3 if result['status'] == 'PENDING' else 2


if __name__ == '__main__':
    raise SystemExit(main())
