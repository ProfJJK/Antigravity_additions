"""Ordinary Windows/private disposable fixtures; never contact the controller."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).parent
SOURCE = W / 'run-live-commissioning-r3.py'
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


m = load(SOURCE)


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class MemoryJournal:
    def __init__(self): self.records = {}; self.crash_before = None; self.crash_after = None
    def exists(self, name): return name in self.records
    def read(self, name): return deepcopy(self.records[name])
    def write(self, name, value):
        if name == self.crash_before: raise RuntimeError('inert crash before durable marker')
        assert name not in self.records
        self.records[name] = deepcopy(value)
        if name == self.crash_after: raise RuntimeError('inert crash after durable marker')
    def observe(self, phase, workflow):
        name = 'snapshot-' + phase + '-' + m.digest(workflow)
        self.records.setdefault(name, deepcopy(workflow))
        return {'path': name, 'sha256': m.digest(workflow)}


def health():
    return {'service_identity': 'SYSTEM', 'pid': 501, 'instance_id': 'a'*32,
            'hardware': {'max_agents': 4}, 'source_root': str(m.INSTALL / '.venv/Lib'),
            'admission_capacity': 4, 'knowledge': {'ready': True}, 'quarantined_slots': {},
            'components': {key: {'state': 'healthy', 'required': True} for key in
                           ('knowledge', 'warden_controller', 'docker_engine', 'containers')}}


def intent():
    return {'schema': 'fixture-intent', 'controller': {'pid': 501, 'instance_id': 'a'*32}, 'requests': m.requests()}


def workflow(phase, status='COMPLETED'):
    request = m.requests()[phase]
    payload = {key: value for key, value in request.items() if key != 'workflow_id'}
    result = {'workflow_id': request['workflow_id'], 'status': status,
            'root': {'kind': 'MACRO_PLANNING_REQUEST' if phase == 'planning' else 'CODE_REQUEST', 'payload': payload},
            'jobs': [], 'fixture_only': True}
    if phase == 'coding':
        result['coding'] = {'baseline_commit': 'c52a3a97eb085e6bafbbd14bd6a75f3274288530',
                            'project': {'repository': r'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance', 'branch': 'pipeline/accepted'}}
    return result


class Client:
    def __init__(self):
        self.calls = []; self.saved = {}; self.post_failure = None; self.pending = set(); self.health = health()
    def call(self, path, data=None):
        self.calls.append((path, deepcopy(data)))
        if path == '/health': return deepcopy(self.health)
        phase = 'coding' if path.startswith('/coding/') else 'planning'
        if data is not None:
            if self.post_failure == 'before': raise TimeoutError('inert unknown transport outcome')
            self.saved[phase] = workflow(phase, 'IN_PROGRESS' if phase in self.pending else 'COMPLETED')
            if self.post_failure == 'after': raise TimeoutError('inert response lost after accepted POST')
        if phase not in self.saved: raise m.HTTPHeld(400)
        return deepcopy(self.saved[phase])
    @property
    def posts(self): return [path for path, data in self.calls if data is not None]


def fixture_validation(phase, value, state):
    assert value.get('fixture_only') is True  # control-flow evidence, not native acceptance
    return {'verified': True} if phase == 'planning' else {'accepted': True}


def execute(client, journal, fresh=True, validate=fixture_validation, seconds=10):
    clock = Clock()
    return m.run_workflows(client, journal, intent(), fresh, validate,
                           observe_seconds=seconds, poll_seconds=2, clock=clock, sleep=clock.sleep)


def test_default_preview_has_no_file_network_token_or_job_access(monkeypatch, capsys):
    monkeypatch.setattr(m, 'run_live', lambda *a: pytest.fail('live entry called by preview'))
    monkeypatch.setattr(Path, 'open', lambda *a, **kw: pytest.fail('preview file I/O'))
    monkeypatch.setattr(m.http.client, 'HTTPConnection', lambda *a, **kw: pytest.fail('preview network'))
    assert m.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'PREVIEW_NO_IO' and result['tokens_read'] == result['files_created'] == 0
    assert result['identities'] == 6 and result['shared_capacity'] == 4


def test_success_submits_two_exact_requests_once_and_resume_only_gets():
    client, journal = Client(), MemoryJournal()
    first = execute(client, journal)
    assert first['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
    assert client.posts == ['/submit', '/coding/submit']
    assert [data for _, data in client.calls if data is not None] == list(m.requests().values())
    before = len(client.calls)
    again = execute(client, journal, False)
    assert again['status'] == first['status'] and all(data is None for _, data in client.calls[before:])
    assert set(name for name in journal.records if name.endswith('post-attempt.json')) == {'planning-post-attempt.json', 'coding-post-attempt.json'}


@pytest.mark.parametrize('when', ['before_marker', 'after_marker', 'before_post_delivery', 'after_post_delivery'])
def test_crashes_never_resubmit_planning_even_when_get_reports400(when):
    client, journal = Client(), MemoryJournal()
    if when == 'before_marker': journal.crash_before = 'planning-post-attempt.json'
    if when == 'after_marker': journal.crash_after = 'planning-post-attempt.json'
    if when == 'before_post_delivery': client.post_failure = 'before'
    if when == 'after_post_delivery': client.post_failure = 'after'
    with pytest.raises((RuntimeError, TimeoutError)): execute(client, journal)
    prior_posts = len(client.posts)
    journal.crash_before = journal.crash_after = client.post_failure = None
    if when == 'after_post_delivery':
        result = execute(client, journal, False)
        assert result['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
        assert client.posts == ['/submit', '/coding/submit']
    else:
        with pytest.raises(m.HTTPHeld) as error: execute(client, journal, False)
        assert error.value.http_status == 400 and len(client.posts) == prior_posts


@pytest.mark.parametrize('when', ['before_marker', 'after_marker', 'after_post_delivery'])
def test_coding_submit_gate_and_ambiguous_attempt_never_repeated(when):
    client, journal = Client(), MemoryJournal()
    if when == 'before_marker': journal.crash_before = 'coding-post-attempt.json'
    if when == 'after_marker': journal.crash_after = 'coding-post-attempt.json'
    original = client.call
    def call(path, data=None):
        if path == '/coding/submit' and when == 'after_post_delivery':
            answer = original(path, data)
            raise TimeoutError('inert coding response lost')
        return original(path, data)
    client.call = call
    with pytest.raises((RuntimeError, TimeoutError)): execute(client, journal)
    client.call = original; journal.crash_before = journal.crash_after = None
    if when == 'after_marker':
        with pytest.raises(m.HTTPHeld): execute(client, journal, False)
        assert client.posts == ['/submit']
    else:
        assert execute(client, journal, False)['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
        assert client.posts == ['/submit', '/coding/submit']


def test_planning_validation_failure_cannot_release_coding():
    client, journal = Client(), MemoryJournal()
    def reject(*args): raise ValueError('inert invalid native receipt')
    with pytest.raises(ValueError): execute(client, journal, validate=reject)
    assert client.posts == ['/submit'] and not journal.exists('coding-post-attempt.json')


def test_observation_timeout_is_pending_without_cancellation_or_budget_mutation():
    client, journal = Client(), MemoryJournal(); client.pending.add('planning')
    result = execute(client, journal, seconds=3)
    assert result['status'] == 'PENDING' and result['server_work_cancelled'] is False
    assert client.posts == ['/submit'] and all('cancel' not in path and 'resume' not in path for path, _ in client.calls)
    previous = len(client.posts); assert execute(client, journal, False, seconds=2)['status'] == 'PENDING'
    assert len(client.posts) == previous


def test_expired_window_defers_coding_without_new_attempt():
    client, journal, clock = Client(), MemoryJournal(), Clock()
    def validate(*args): clock.now = 20; return {'verified': True}
    result = m.run_workflows(client, journal, intent(), True, validate, observe_seconds=10, clock=clock, sleep=clock.sleep)
    assert result['status'] == 'PENDING' and result['coding_submission_deferred']
    assert client.posts == ['/submit'] and not journal.exists('coding-post-attempt.json')


@pytest.mark.parametrize('change', ['instance','capacity','source','required_empty','knowledge_missing','docker_missing','paused'])
def test_current_controller_identity_and_nonvacuous_phase_readiness(change):
    state = health(); phase = 'planning'
    if change == 'instance': state['instance_id'] = 'b'*32
    if change == 'capacity': state['hardware']['max_agents'] = 6
    if change == 'source': state['source_root'] = 'other'
    if change == 'required_empty': state['components'] = {}
    if change == 'knowledge_missing': del state['components']['knowledge']
    if change == 'docker_missing': del state['components']['containers']; phase = 'coding'
    if change == 'paused': state['admission_capacity'] = 0
    with pytest.raises(m.Held): m.health_binding(state, intent()['controller'], ready=True, phase=phase)


def test_paused_existing_work_is_still_observed_without_post():
    client, journal = Client(), MemoryJournal(); client.pending.add('planning')
    execute(client, journal, seconds=1); previous = len(client.posts)
    client.health['admission_capacity'] = 0
    assert execute(client, journal, False, seconds=1)['status'] == 'PENDING' and len(client.posts) == previous


@pytest.mark.parametrize('field', ['workflow_id','objective','project_id','chapter_count'])
def test_same_id_must_bind_the_original_request(field):
    phase = 'coding' if field == 'project_id' else 'planning'; value = workflow(phase)
    if field == 'workflow_id': value[field] = 'other'
    else: value['root']['payload'][field] = 'other'
    with pytest.raises(m.Held): m.request_binding(value, m.requests()[phase], phase)


@pytest.fixture
def private(tmp_path, monkeypatch):
    from cochem_pipeline import windows as win
    sid = win._sid_text(win._account_sid(r'AETHERDESK\ansac'))
    # Test/checkout ancestors have extra legitimate sandbox trustees. Production
    # uses the separately checked operator root. Only this fixture's ancestor
    # boundary is substituted; final native directory/file ACLs remain real.
    script = f"$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$a=[Security.AccessControl.DirectorySecurity]::new();$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false);foreach($s in @($sid.Value,'S-1-5-18','S-1-5-32-544')){{$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($s),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}};[IO.Directory]::SetAccessControl('{tmp_path}',$a)"
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    value = m.WindowsPrivate(win, sid); value.fixture_root = tmp_path
    monkeypatch.setattr(value, 'validate_ancestry', lambda path: value.validate(path))
    return value


def test_real_windows_private_create_new_lock_snapshot_and_collision(tmp_path, private):
    root = private.fixture_root / 'evidence'; private.create_directory(root)
    journal = m.Journal(root, private)
    with journal.locked():
        journal.write('intent.json', intent())
        saved = journal.observe('planning', workflow('planning'))
        assert journal.read('intent.json') == intent()
        assert len(journal.inventory()) == 3 and saved['sha256'] == m.digest(workflow('planning'))
        with pytest.raises(OSError):
            with journal.locked(): pytest.fail('duplicate invocation acquired')
        with pytest.raises(OSError): journal.write('intent.json', {'replace': True})
    assert journal.read('intent.json') == intent()
    with pytest.raises(m.Held): private.create_directory(root)


def test_held_file_refuses_write_delete_and_keeps_actual_hash(tmp_path, private):
    path = tmp_path / 'retained.json'; path.write_bytes(b'original')
    with ExitStack() as stack:
        raw, sha = m.pinned(stack, path)
        assert raw == b'original' and sha == hashlib.sha256(raw).hexdigest()
        with pytest.raises(OSError): path.write_bytes(b'changed')
        with pytest.raises(OSError): path.unlink()
    assert path.read_bytes() == b'original'


def test_hardlinks_and_real_directory_junction_refused(tmp_path, private):
    path = tmp_path / 'original.json'; path.write_bytes(b'original')
    alias = tmp_path / 'alias.json'; os.link(path, alias)
    with pytest.raises(m.Held):
        with m.held_file(path): pytest.fail('hardlink admitted')
    target = tmp_path / 'target'; target.mkdir()
    link = tmp_path / 'junction'
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', f"New-Item -ItemType Junction -Path '{link}' -Target '{target}'|Out-Null"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    with pytest.raises(m.Held): m.ordinary(link / 'child.json', missing=True)


def test_real_extra_private_acl_trustee_is_held(tmp_path, private):
    script = f"$p='{tmp_path}';$a=[IO.Directory]::GetAccessControl($p);$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'));[IO.Directory]::SetAccessControl($p,$a)"
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    with pytest.raises(m.Held): private.validate(tmp_path)


def test_actual_intended_operator_ancestry_is_read_only_and_not_replaceable():
    from cochem_pipeline import windows as win
    sid = win._sid_text(win._account_sid(r'AETHERDESK\ansac'))
    value = m.WindowsPrivate(win, sid)
    value.validate_ancestry(m.OPERATOR)
    value.validate(m.OPERATOR)


def test_real_complete_driver_engine_uses_private_journals_and_retained_ids(tmp_path, private):
    root = private.fixture_root / 'evidence'; private.create_directory(root)
    journal = m.Journal(root, private); client = Client()
    with journal.locked():
        journal.write('intent.json', intent())
        assert execute(client, journal)['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
    before = len(client.posts)
    with journal.locked():
        assert execute(client, journal, False)['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
    assert len(client.posts) == before == 2


@pytest.mark.parametrize('mutation', ['execution_kind','subscription','pid','reservation','model'])
def test_exact_reused_planning_validator_refuses_malformed_native_receipts(mutation):
    # These labelled JSON fixtures exercise pure acceptance only; no provider exists.
    source = REPO / 'scripts/verify_pipeline_acceptance.py'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == m.PINS['planning_validator']
    validator = load(source)
    fixture = load(REPO / 'pipeline_tests/test_acceptance_report.py')
    value, providers = fixture.document_contract.__wrapped__()
    report = validator.validate_workflow(value, providers, admission=value['acceptance_admission'])
    assert report['verified'] and report['validated_process_receipts'] == 8
    receipt = value['jobs'][2]['receipt']
    if mutation == 'execution_kind': receipt['execution_kind'] = 'fixture'
    if mutation == 'subscription': receipt['subscription_verified'] = False
    if mutation == 'pid': receipt['pid'] = 0
    if mutation == 'reservation': receipt['route_reservation_sha256'] = '0'*64
    if mutation == 'model': receipt['requested_model'] = 'invented'
    with pytest.raises(ValueError): validator.validate_workflow(value, providers, admission=value['acceptance_admission'])


def test_installed_coding_validator_and_additional_route_validation_are_real():
    path = m.PACKAGES / 'cochem_pipeline/coding_acceptance.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == m.PINS['coding_validator']
    from cochem_pipeline.coding_acceptance import validate_coding_workflow
    from cochem_supervisor.probes import verify_routing_assignment
    with pytest.raises(ValueError): validate_coding_workflow({'status': 'COMPLETED', 'coding': {'status': 'COMPLETED'}})
    fixture = load(REPO / 'pipeline_tests/test_acceptance_report.py')
    value, _ = fixture.document_contract.__wrapped__()
    # Routing validation is kind-specific inside the existing pure verifier.
    value['jobs'] = value['jobs'][1:]  # Planning root is not a coding controller kind.
    assert len(m.validate_coding_routes(value, None, verify_routing_assignment)) == 8
    value['jobs'][1]['route']['candidate_index'] = 200
    with pytest.raises(ValueError): m.validate_coding_routes(value, None, verify_routing_assignment)


def test_only_fixed_submission_endpoints_and_no_cancel_resume_reset_in_source():
    text = SOURCE.read_text()
    assert "client.call('/submit' if phase == 'planning' else '/coding/submit', request)" in text
    assert "'/cancel'" not in text and "'/coding/resume'" not in text and "'/routing/resume'" not in text
    assert 'max_attempts' not in m.PLANNING and 'max_dispatches' not in m.PLANNING
    assert m.requests() == m.requests()


def test_actual_installed_manifest_asset_shape_and_digest_without_runtime_or_token_reads():
    raw = (m.INSTALL / 'source-manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == m.PINS['manifest']
    expected = {}
    for row in json.loads(raw)['files']:
        relative = Path(row['relative'])
        if relative.parts[0] == 'src' and relative.parts[1] in ('cochem_pipeline','cochem_mcp','cochem_supervisor') and relative.suffix in ('.py','.md','.json','.xml'):
            expected[Path(*relative.parts[1:]).as_posix()] = row['sha256']
    assert len(expected) == 109 and m.digest(expected) == m.PINS['revision']
