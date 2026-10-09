"""Inert durable Windows private-journal fixtures; zero real HTTP/model work."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


c = load(HERE / 'continue-live-prepost-r3-v1.py')
v2 = load(HERE / 'test_live_commissioning_r3_v2.py')
m, base = v2.m, v2.base
native = v2.native


class Probe:
    def __init__(self): self.calls = []
    def check(self, phase, workflow_id):
        assert workflow_id == m.requests()[phase]['workflow_id']
        self.calls.append(phase)
        return 'EXACT_UNKNOWN_JOB_ID'


@pytest.fixture
def setup(native, monkeypatch):
    parent, sid, win, scoped, private = native
    v2.set_fixture_acl(parent, sid, 0x1200A9)
    root = parent / 'original-v2-journal'
    private.create_directory(root)
    journal = m.Journal(root, private)
    expected = c.expected_intent(m, {'pid': 501, 'instance_id': 'a'*32}, 'b'*64)
    observation = {'schema': 'cochem-live-commissioning-observation/1', 'intent_sha256': m.digest(expected),
                   'recorded_at': 123.0, 'full_srs_acceptance': False,
                   'result': {'status': 'HELD_OBSERVATION_OR_SUBMISSION_UNCERTAIN', 'error_type': 'Held',
                              'code': 'controller_not_ready', 'http_status': None,
                              'automatic_resubmission_allowed': False, 'server_work_cancelled': False}}
    with journal.locked():
        journal.write('intent.json', expected)
        journal.write('observation-0002.json', observation)
    monkeypatch.setattr(c, 'ORIGINAL_INTENT_SHA', m.digest(expected))
    monkeypatch.setattr(c, 'ORIGINAL_OBSERVATION_SHA', m.digest(observation))
    monkeypatch.setattr(base, 'm', m)
    clock = base.Clock()
    client = base.Client()
    original_call = client.call
    health_count = []

    def call(path, data=None):
        if path == '/health':
            health_count.append(path)
            client.health['hardware']['measured_at'] = 1000+clock.now
            client.health['components']['warden_controller']['checked_at'] = 1000+clock.now
        if data is not None:
            phase = 'coding' if path.startswith('/coding/') else 'planning'
            assert journal.exists(c.FENCE)
            assert journal.exists(phase+'-post-attempt.json')
            assert journal.read(phase+'-post-attempt.json')['workflow_id'] == data['workflow_id']
        return original_call(path, data)

    client.call = call
    return journal, expected, observation, clock, client, Probe(), health_count, win, sid


def run(setup, **kwargs):
    journal, expected, _, clock, client, probe, _, _, _ = setup
    with ExitStack() as stack, journal.locked():
        return c.continue_locked(m, journal, stack, expected, client, probe, 'c'*64,
                                 base.fixture_validation, clock=clock, sleep=clock.sleep,
                                 wall=lambda:1000+clock.now, **kwargs)


def test_exact_known_pins_and_frozen_only_post_sites():
    assert hashlib.sha256(c.DRIVER.read_bytes()).hexdigest() == c.DRIVER_SHA
    assert c.DRIVER_SHA == 'cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363'
    assert c.ORIGINAL_INTENT_SHA == '294bfc7cfc8bccd38c883600876751862c35d562409352c36c2f576b9e5159d3'
    assert c.ORIGINAL_OBSERVATION_SHA == '2a6796e91c20fadf26602355b50213ce9883b2b65ab008d02120bf4dc2774eb2'
    source = Path(c.__file__).read_text()
    assert "connection.request('GET'" in source and "connection.request('POST'" not in source
    assert 'm.run_workflows(admitted, journal, expected, True' in source
    assert "'/submit'" not in source and "'/coding/submit'" not in source


def test_real_private_journal_success_preserves_original_bytes_and_never_replays(setup):
    journal, expected, _, clock, client, probe, health_gets, win, sid = setup
    before = {name:(journal.root/name).read_bytes() for name in c.ORIGINAL_NAMES}
    result = run(setup)
    assert result['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
    assert result['consecutive_ready_samples'] == 3 and clock.now == 10
    assert client.posts == ['/submit', '/coding/submit'] and probe.calls == ['planning', 'coding']
    assert {name:(journal.root/name).read_bytes() for name in c.ORIGINAL_NAMES} == before
    assert journal.read(c.FENCE)['original_requests'] == expected['requests']
    owner, protected, rules = win._acl(journal.root)
    assert owner == sid and protected
    assert {s for s,_,_ in rules} == {sid,win.SYSTEM_SID,win.ADMIN_SID}
    assert {s for s,_,_ in win._acl(journal.root/c.FENCE)[2]} == {sid,win.SYSTEM_SID,win.ADMIN_SID}
    previous_calls = len(client.calls)
    with pytest.raises(m.Held, match='not_exact_original_prepost_inventory'):
        run(setup)
    assert len(client.calls) == previous_calls and len(client.posts) == 2


@pytest.mark.parametrize('extra', ['planning-post-attempt.json', 'coding-post-attempt.json',
                                    'snapshot-planning-unknown.json', c.FENCE, c.RESULT, 'unknown.json'])
def test_any_marker_snapshot_prior_fence_or_unknown_file_refuses_before_network(setup, extra):
    journal, _, _, _, client, probe, _, _, _ = setup
    journal.write(extra, {'fixture': True})
    with pytest.raises(m.Held, match='not_exact_original_prepost_inventory'):
        run(setup)
    assert not client.calls and not probe.calls and not client.posts


def test_original_byte_change_refuses_before_network(setup):
    journal, _, _, _, client, probe, _, _, _ = setup
    path = journal.root/'observation-0002.json'
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(m.Held, match='file_digest_changed'):
        run(setup)
    assert not client.calls and not probe.calls


def test_unknown_prior_failure_refuses_even_with_fixture_hash_rebound(setup, monkeypatch):
    journal, _, observation, _, client, probe, _, _, _ = setup
    changed = deepcopy(observation)
    changed['result']['code'] = 'bounded_operation_failed'
    (journal.root/'observation-0002.json').write_bytes(m.encoded(changed))
    monkeypatch.setattr(c, 'ORIGINAL_OBSERVATION_SHA', m.digest(changed))
    with pytest.raises(m.Held, match='not_exact_known_prepost_readiness_failure'):
        run(setup)
    assert not client.calls and not probe.calls


def test_existing_workflow_or_unrecognized_missing_refuses_without_fence(setup):
    journal, _, _, _, client, probe, _, _, _ = setup
    probe.check = lambda *args:'EXISTING_OR_UNCERTAIN'
    with pytest.raises(m.Held, match='workflow_probe_consistency_failed'):
        run(setup)
    assert not client.calls and not journal.exists(c.FENCE)


def test_original_inputs_and_exclusive_lock_remain_held_during_probe(setup):
    journal, _, _, _, _, probe, _, _, _ = setup
    original = probe.check

    def check(*args):
        with pytest.raises(OSError):
            (journal.root/'intent.json').write_bytes(b'replacement refused')
        with pytest.raises(OSError):
            with journal.locked(): pytest.fail('second lock acquired')
        return original(*args)

    probe.check = check
    assert run(setup)['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'


def test_wait_timeout_durably_fences_without_marker_or_post_and_cannot_repeat(setup):
    journal, _, _, clock, client, _, _, _, _ = setup
    client.health['admission_capacity'] = 0
    result = run(setup, wait_seconds=15, observe_seconds=20, poll_seconds=5)
    assert result['code'] == 'stable_readiness_timeout_before_first_post' and clock.now == 15
    assert client.posts == [] and not journal.exists('planning-post-attempt.json')
    assert journal.exists(c.FENCE) and journal.exists(c.RESULT)
    count = len(client.calls)
    with pytest.raises(m.Held): run(setup)
    assert len(client.calls) == count


def test_readiness_requires_three_consecutive_fresh_advanced_samples(setup):
    _, _, _, clock, client, _, _, _, _ = setup
    original = client.call
    capacities = iter([4,0,4,4,4])

    def call(path, data=None):
        if path == '/health':
            client.health['admission_capacity'] = next(capacities,4)
        return original(path,data)

    client.call = call
    result = run(setup, wait_seconds=30, observe_seconds=40, poll_seconds=5)
    assert result['status'] == 'TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED'
    assert result['readiness_health_gets'] == 5 and clock.now == 20


@pytest.mark.parametrize('variant',['stale','duplicate','identity'])
def test_stale_repeated_or_wrong_controller_samples_never_post(setup,variant):
    journal, _, _, _, client, _, _, _, _ = setup
    original = client.call

    def call(path,data=None):
        value = original(path,data)
        if path == '/health':
            if variant == 'identity': value['instance_id']='d'*32
            else:
                stamp = 0 if variant == 'stale' else 1000
                value['hardware']['measured_at']=stamp
                value['components']['warden_controller']['checked_at']=stamp
        return value

    client.call=call
    result=run(setup,wait_seconds=15,observe_seconds=20,poll_seconds=5)
    assert result['status']=='HELD_PREPOST_CONTINUATION_UNCERTAIN'
    assert not client.posts and not journal.exists('planning-post-attempt.json')


def test_response_lost_after_first_post_keeps_marker_and_never_replays(setup):
    journal, _, _, _, client, _, _, _, _ = setup
    client.post_failure='after'
    result=run(setup)
    assert result['status']=='HELD_PREPOST_CONTINUATION_UNCERTAIN'
    assert client.posts==['/submit'] and journal.exists('planning-post-attempt.json')
    client.post_failure=None
    with pytest.raises(m.Held): run(setup)
    assert client.posts==['/submit']


def test_ambiguous_fence_write_stops_before_health_and_cannot_repeat(setup,monkeypatch):
    journal, _, _, _, client, _, _, _, _ = setup
    original=journal.write

    def write(name,value):
        original(name,value)
        if name==c.FENCE: raise OSError('inert lost acknowledgement')

    monkeypatch.setattr(journal,'write',write)
    with pytest.raises(OSError): run(setup)
    assert journal.exists(c.FENCE) and not client.calls
    with pytest.raises(m.Held): run(setup)
    assert not client.calls


@pytest.mark.parametrize('status,body,passes',[(400,{'error':'Unknown job_id'},True),
                                            (400,{'error':'other'},False),
                                            (404,{'error':'Unknown job_id'},False),
                                            (200,{'workflow_id':'existing'},False)])
def test_missing_probe_accepts_only_exact_memory_only_response(monkeypatch,status,body,passes):
    calls=[]
    class Response:
        def __init__(self): self.status=status;self.raw=m.encoded(body)
        def read1(self,size):
            raw,self.raw=self.raw[:size],self.raw[size:]
            return raw
    class Connection:
        sock=None
        def __init__(self,*args,**kwargs): calls.append((args,kwargs))
        def request(self,method,path,headers):
            assert method=='GET' and path=='/workflow/'+m.requests()['planning']['workflow_id']
            assert headers['Authorization']=='Bearer fixture-secret-not-real'
        def getresponse(self): return Response()
        def close(self): calls.append('closed')
    monkeypatch.setattr(c.http.client,'HTTPConnection',Connection)
    client=type('Client',(),{'port':47824,'token':'fixture-secret-not-real'})()
    probe=c.MissingWorkflowProbe(m,client)
    if passes: assert probe.check('planning',m.requests()['planning']['workflow_id'])=='EXACT_UNKNOWN_JOB_ID'
    else:
        with pytest.raises(m.Held,match='workflow_probe_not_exact_missing'):
            probe.check('planning',m.requests()['planning']['workflow_id'])
    assert calls[-1]=='closed'


def test_default_preview_has_no_file_token_or_http_access(monkeypatch,capsys):
    monkeypatch.setattr(c,'execute',lambda **kwargs:pytest.fail('live execution'))
    monkeypatch.setattr(Path,'read_bytes',lambda *args:pytest.fail('file read'))
    monkeypatch.setattr(c.http.client,'HTTPConnection',lambda *args,**kwargs:pytest.fail('HTTP'))
    assert c.main([])==0
    value=json.loads(capsys.readouterr().out)
    assert value['mode']=='PREVIEW_NO_IO' and value['http_calls']==value['tokens_read']==value['files_created']==0
