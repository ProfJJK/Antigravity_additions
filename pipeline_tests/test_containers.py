"""Snapshot/JUnit/ownership contracts and opt-in real Docker acceptance.

Set COCHEM_TEST_DOCKER_IMAGE to the image ID emitted by the reviewed builder to
run real isolated processes. Absence is an explicit skip, never a fake engine.
"""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import threading

import pytest

from cochem_pipeline.container_policy import DockerPolicy
from cochem_pipeline.containers import (ContainerCapacityError, ContainerCleanupError, ContainerError, DockerRunner,
                                       junit_cases, parse_junit, source_snapshot)

IMAGE = 'sha256:' + 'a' * 64


def policy(image=IMAGE, **changes):
    return DockerPolicy.from_dict({'enabled': True, 'image': image, 'allowed_images': [image],
        'commands': [{'name': 'unit', 'argv': ['python','-m','pytest','-q']}], **changes})


def test_snapshot_content_digest_is_independent_of_timestamps_and_archive_bytes(tmp_path):
    (tmp_path/'module.py').write_text('answer=42\n')
    archive, first = source_snapshot(tmp_path, policy())
    os.utime(tmp_path/'module.py', (1, 1))
    _, second = source_snapshot(tmp_path, policy())
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(archive)) as reader:
        assert reader.getnames() == ['module.py']
        assert reader.extractfile('module.py').read() == b'answer=42\n'
    (tmp_path/'module.py').write_text('answer=43\n')
    assert source_snapshot(tmp_path, policy())[1]['sha256'] != first['sha256']


@pytest.mark.parametrize('name', ['.env', 'private.key', 'auth.json'])
def test_snapshot_refuses_credentials(tmp_path, name):
    (tmp_path/name).write_text('DO NOT TRANSFER')
    with pytest.raises(ContainerError, match='Credential'):
        source_snapshot(tmp_path, policy())


def test_snapshot_refuses_symlinks_and_hardlinks(tmp_path):
    source=tmp_path/'source';source.mkdir()
    target=tmp_path/'outside';target.write_text('outside')
    (source/'link').symlink_to(target)
    with pytest.raises(ContainerError, match='ordinary'):
        source_snapshot(source, policy())
    (source/'link').unlink();os.link(target,source/'link')
    with pytest.raises(ContainerError, match='ordinary'):
        source_snapshot(source, policy())


@pytest.mark.parametrize('xml', [b'<testsuite tests="0"/>',
    b'<testsuite tests="1"><testcase name="x"><skipped/></testcase></testsuite>',
    b'<testsuite tests="9"><testcase name="x"/></testsuite>',
    b'<!DOCTYPE x><testsuite/>', b'agent says all tests passed'])
def test_empty_skipped_inconsistent_or_model_text_cannot_establish_tests(xml):
    with pytest.raises(ContainerError):
        parse_junit(xml)


def test_junit_counts_actual_cases_not_green_prose():
    result=parse_junit(b'<testsuites><testsuite tests="2" failures="1"><testcase name="good"/><testcase name="bad"><failure>failed</failure></testcase></testsuite></testsuites>')
    assert result == {'tests':2,'failures':1,'errors':0,'skipped':0,'passed':1}


def test_large_actual_junit_document_retains_late_planned_test_identity_and_diagnostic():
    cases=''.join(f'<testcase name="test_existing_{index}" classname="tests.test_existing"/>'
                  for index in range(1700))
    xml=('<testsuite tests="1701" failures="1">'+cases+
         '<testcase name="test_planned_requirement" classname="tests.test_requirement.TestRequirement">'
         '<failure message="actual assertion">assert actual == expected</failure></testcase></testsuite>').encode()
    assert len(xml)<4*1024*1024
    counts=parse_junit(xml)
    identities=junit_cases(xml)
    assert len(identities)==counts['tests']==1701
    assert identities[-1]=={'name':'test_planned_requirement',
        'class_name':'tests.test_requirement.TestRequirement','status':'failed',
        'message':'actual assertion','diagnostic':'assert actual == expected'}


def test_atomic_capacity_is_shared_across_controller_instances(tmp_path):
    first=DockerRunner(policy(),tmp_path)
    second=DockerRunner(policy(),tmp_path)
    for index in range(4):
        first._reserve('job',str(index))
    with pytest.raises(ContainerCapacityError):
        second._reserve('job','fifth')
    assert second.census()['owned'] == 4


def test_create_contract_never_mounts_host_or_injects_credentials(tmp_path):
    runner=DockerRunner(policy(),tmp_path)
    record=runner._reserve('job','attempt')
    argv=runner.create_arguments(record)
    for flag,value in [('--network','none'),('--memory','4096m'),('--memory-swap','4096m'),
                       ('--cpus','2.0'),('--pids-limit','512'),('--cap-drop','ALL'),
                       ('--security-opt','no-new-privileges:true')]:
        assert argv[argv.index(flag)+1]==value
    assert '--read-only' in argv and '--pull' in argv
    assert all(flag not in argv for flag in ['--volume','--mount','--env-file','--privileged'])
    assert 'HTTP_PROXY=' in argv
    assert IMAGE in argv


@pytest.fixture
def docker_image():
    image=os.environ.get('COCHEM_TEST_DOCKER_IMAGE')
    if not image:
        pytest.skip('Set COCHEM_TEST_DOCKER_IMAGE to run actual local Docker acceptance')
    return image


def real_run(tmp_path, image, source, **changes):
    root=tmp_path/'source';root.mkdir()
    for name,text in source.items():
        (root/name).write_text(text)
    runner=DockerRunner(policy(image,**changes),tmp_path/'private')
    receipt=runner.run(root,job_id='test-job',attempt_id='real-attempt')
    assert receipt['cleanup_verified'], receipt
    assert runner.census()['owned']==0
    return receipt


def test_real_offline_container_enforces_kernel_limits_and_ram_filesystem(tmp_path,docker_image):
    source={'test_kernel.py': '''import errno,os,pathlib,socket

def test_real_boundary():
 assert os.getuid()==1000
 assert not pathlib.Path('/var/run/docker.sock').exists()
 assert not any(os.environ.get(k) for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'))
 mounts=pathlib.Path('/proc/mounts').read_text()
 assert any(line.split()[1]=='/work' and line.split()[2]=='tmpfs' for line in mounts.splitlines())
 assert any(line.split()[1]=='/tmp' and line.split()[2]=='tmpfs' for line in mounts.splitlines())
 assert 'NoNewPrivs:\\t1' in pathlib.Path('/proc/self/status').read_text()
 assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
 try:
  socket.create_connection(('192.0.2.1',443),timeout=1)
 except OSError as error:
  assert error.errno==errno.ENETUNREACH
 else: raise AssertionError('network unexpectedly reachable')
 try: pathlib.Path('/etc/forbidden').write_text('no')
 except OSError: pass
 else: raise AssertionError('root filesystem writable')
'''}
    receipt=real_run(tmp_path,docker_image,source)
    assert receipt['passed'],receipt
    assert receipt['commands'][0]['junit']['passed']==1
    assert receipt['limits']['Memory']==4*1024**3
    assert all(receipt['output']['files'][name]==entry for name,entry in receipt['source']['files'].items())


@pytest.mark.parametrize('source,category', [
    ({'test_fail.py':'def test_fail():\n assert False\n'},'tests_failed'),
    ({'empty.py':'x=1\n'},'container_contract'),
    ({'test_skip.py':'import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n'},'container_contract'),
    ({'pytest.py':'print("all tests passed")\n','test_fail.py':'def test_real(): assert False\n'},'tests_failed'),
    ({'test_mutate.py':'from pathlib import Path\ndef test_mutate(): Path("module.py").write_text("changed")\n','module.py':'original\n'},'container_contract'),
])
def test_real_failures_empty_suites_shims_and_source_mutation_do_not_pass(tmp_path,docker_image,source,category):
    receipt=real_run(tmp_path,docker_image,source)
    assert not receipt['passed']
    assert receipt['failure_category']==category,receipt


def test_real_custom_command_timeout_removes_process_tree(tmp_path,docker_image):
    receipt=real_run(tmp_path,docker_image,{'module.py':'x=1\n'},commands=[{
        'name':'hang','kind':'command','argv':['python','-c','import time;time.sleep(60)'],'timeout_seconds':1}])
    assert not receipt['passed'] and receipt['quarantine_required']
    assert receipt['failure_category']=='timeout'
    assert receipt['input_source_verified'] and receipt['failure_scope']=='code'


def test_real_custom_build_command_and_actual_exit_evidence(tmp_path,docker_image):
    receipt=real_run(tmp_path,docker_image,{'module.py':'x=1\n'},commands=[{
        'name':'compile','kind':'command','argv':['python','-m','compileall','-q','.']}])
    assert receipt['passed'],receipt
    assert receipt['commands'][0]['exit_code']==0
    assert receipt['output']['sha256']


def test_real_oom_is_contained_and_requires_quarantine(tmp_path,docker_image):
    receipt=real_run(tmp_path,docker_image,{'module.py':'x=1\n'},memory_mb=64,tmpfs_mb=16,commands=[{
        'name':'oom','kind':'command','argv':['python','-c','x=bytearray(256*1024*1024)'],'timeout_seconds':20}])
    assert not receipt['passed'] and receipt['quarantine_required'], receipt
    assert receipt['failure_category']=='oom'
    assert receipt['input_source_verified'] and receipt['failure_scope']=='code'
    assert receipt['commands'][0]['exit_code']==137


def test_real_output_flood_is_bounded_and_container_removed(tmp_path,docker_image):
    receipt=real_run(tmp_path,docker_image,{'module.py':'x=1\n'},output_limit_bytes=4096,commands=[{
        'name':'flood','kind':'command','argv':['python','-c','print("x"*1000000)']}])
    assert not receipt['passed'] and receipt['quarantine_required'],receipt
    assert receipt['failure_category']=='output_limit'
    assert len(receipt['commands'][0]['stdout'])<=4096


def test_real_reaper_recovers_crash_between_creation_and_recording_id_and_spares_other_owner(tmp_path,docker_image):
    runner=DockerRunner(policy(docker_image),tmp_path/'first')
    other=DockerRunner(policy(docker_image),tmp_path/'other')
    first_record=runner._reserve('job','crashed-attempt')
    other_record=other._reserve('other-job','other-attempt')
    try:
        for owner,record in ((runner,first_record),(other,other_record)):
            result=owner._call(owner.create_arguments(record))
            assert result.returncode==0,result.stderr
            # Deliberately do not persist returned ID: simulate abrupt death
            # immediately after Docker created the uniquely labelled container.
        result=runner.reap_orphans(set())
        assert result['removed']==[first_record['lease']]
        assert runner._inspect_owned(first_record) is None
        assert other._inspect_owned(other_record) is not None
    finally:
        runner._remove(first_record)
        other._remove(other_record)


def test_real_preflight_is_read_only_and_reports_enforced_engine_support(tmp_path,docker_image):
    runner=DockerRunner(policy(docker_image),tmp_path)
    result=runner.preflight()
    assert result['ready'] and result['image_id']==docker_image
    assert runner.census()['owned']==0


def test_preparing_reservations_obey_hardware_target_across_instances(tmp_path):
    first=DockerRunner(policy(),tmp_path)
    second=DockerRunner(policy(),tmp_path)
    first._reserve('_warm_','warm-first',preparing=True,capacity_limit=1)
    with pytest.raises(ContainerCapacityError):
        second._reserve('_warm_','warm-second',preparing=True,capacity_limit=1)
    assert second.census()['owned']==1
    assert second.census()['reserved_memory_mb']==4096


def test_removed_lease_cannot_be_resurrected_by_late_prepare(tmp_path):
    runner=DockerRunner(policy(),tmp_path)
    record=runner._reserve('_warm_','late',preparing=True)
    runner._set(record['lease'],status='REMOVED')
    with pytest.raises(ContainerError,match='resurrected'):
        runner._set(record['lease'],status='WARM')


def test_real_warm_pool_is_single_use_reserved_and_fast(tmp_path,docker_image):
    source=tmp_path/'source';source.mkdir()
    (source/'test_warm.py').write_text('def test_warm(): assert True\n')
    runner=DockerRunner(policy(docker_image),tmp_path/'private')
    prepared=runner.prepare_pool(target=1)
    assert len(prepared['prepared'])==1
    container_id=prepared['prepared'][0]
    assert prepared['census']['reserved_memory_mb']==4096
    assert prepared['census']['warm']==1
    # Ordinary orphan maintenance must not destroy the deliberately idle pool.
    assert runner.reap_orphans(set())['removed']==[]
    try:
        reservation=runner.reserve_attempt('warm-job','warm-attempt')
        assert runner.census()['warm']==0 and runner.census()['active']==1
        receipt=runner.run(source,job_id='warm-job',attempt_id='warm-attempt',reservation=reservation)
        assert receipt['passed'] and receipt['source_verified'],receipt
        assert receipt['warm_pool_used'] and receipt['container_id']==container_id
        assert receipt['cleanup_verified'] and runner.census()['owned']==0
        # Preserve real timing for the host SLA report; correctness does not turn
        # a slow host into a false 1.5-second acceptance pass.
        (tmp_path/'warm-timing.json').write_text(json.dumps({key:receipt[key] for key in
            ('startup_seconds','startup_sla_met','warm_pool_used','container_id')}))
        assert receipt['startup_sla_met']==(receipt['startup_seconds']<=1.5)
        second=runner.prepare_pool(target=1)
        assert second['prepared'][0]!=container_id
        drained=runner.prepare_pool(target=0)
        assert drained['drained']==second['prepared']
        assert drained['census']['owned']==0
    finally:
        runner.reap_orphans(set(),include_warm=True)
    assert runner.census()['owned']==0


def test_real_failed_pytest_retains_red_assertion_evidence(tmp_path,docker_image):
    receipt=real_run(tmp_path,docker_image,{'test_red.py':'def test_red(): assert 1==2\n'})
    assert not receipt['passed'] and receipt['source_verified']
    item=receipt['commands'][0]
    assert item['exit_code']==1
    assert item['junit']['failures']==1 and item['junit']['errors']==0
    assert item['junit_cases'][0]['status']=='failed'
    assert '1 == 2' in item['junit_cases'][0]['diagnostic']


def test_real_pytest_collection_error_is_not_a_red_assertion(tmp_path,docker_image):
    receipt=real_run(tmp_path,docker_image,{'test_broken.py':'raise ImportError("missing prerequisite")\n'})
    assert not receipt['passed'] and receipt['source_verified']
    item=receipt['commands'][0]
    assert item['exit_code']==2
    assert item['junit']['failures']==0 and item['junit']['errors']==1
    assert item['junit_cases'][0]['status']=='error'


def test_ram_descriptor_cannot_be_replaced_by_a_duck_typed_mock(tmp_path):
    from types import SimpleNamespace
    (tmp_path/'source.py').write_text('x=1\n')
    with pytest.raises(ContainerError,match='genuine controller'):
        source_snapshot(tmp_path,policy(),ramdisk_workspace=SimpleNamespace(validate=lambda **kwargs:None))


def test_real_warm_security_profile_accepts_other_trusted_commands(tmp_path,docker_image):
    source=tmp_path/'source';source.mkdir();(source/'code.py').write_text('x=1\n')
    first=DockerRunner(policy(docker_image,max_containers=1),tmp_path/'private')
    second=DockerRunner(policy(docker_image,max_containers=1,commands=[{
        'name':'build','kind':'command','argv':['python','-m','compileall','-q','.']}]),tmp_path/'private')
    prepared=first.prepare_pool(target=1)
    try:
        receipt=second.run(source,job_id='profile-job',attempt_id='profile-attempt')
        assert receipt['passed'] and receipt['warm_pool_used'],receipt
        assert receipt['container_id']==prepared['prepared'][0]
        assert receipt['policy_sha256']==second.policy.digest
    finally:
        first.reap_orphans(set(),include_warm=True)


def test_real_incompatible_idle_profile_cannot_starve_pending_job(tmp_path,docker_image):
    source=tmp_path/'source';source.mkdir();(source/'test_profile.py').write_text('def test_profile(): assert True\n')
    first=DockerRunner(policy(docker_image,max_containers=1),tmp_path/'private')
    second=DockerRunner(policy(docker_image,max_containers=1,memory_mb=256,tmpfs_mb=128),tmp_path/'private')
    prepared=first.prepare_pool(target=1)
    try:
        receipt=second.run(source,job_id='other-profile',attempt_id='other-attempt')
        assert receipt['passed'] and not receipt['warm_pool_used'],receipt
        assert receipt['container_id']!=prepared['prepared'][0]
        assert second.census()['owned']==0
    finally:
        first.reap_orphans(set(),include_warm=True)


@pytest.mark.parametrize('name',['.ENV','.Claude','SECRET.PEM','.NETRC'])
def test_case_insensitive_credential_aliases_cannot_enter_snapshot(tmp_path,name):
    (tmp_path/name).write_text('must not transfer')
    with pytest.raises(ContainerError,match='Credential'):
        source_snapshot(tmp_path,policy())


def test_windows_mode_manifest_controls_archive_executable_bit(tmp_path):
    (tmp_path/'entry.py').write_text('x=1\n')
    (tmp_path/'entry.py').chmod(0o644)
    archive,manifest=source_snapshot(tmp_path,policy(),source_modes={'entry.py':'100755'})
    assert manifest['files']['entry.py']['executable']
    with tarfile.open(fileobj=io.BytesIO(archive)) as reader:
        assert reader.getmember('entry.py').mode==0o755


def test_durable_capacity_closes_cross_instance_cold_and_warm_reservation_races(tmp_path):
    first=DockerRunner(policy(),tmp_path);second=DockerRunner(policy(),tmp_path)
    first.set_capacity(1)
    record=first.reserve_attempt('job','attempt')
    with pytest.raises(ContainerCapacityError):second.reserve_attempt('next','next')
    with pytest.raises(ContainerCapacityError):second._reserve('_warm_','warm',preparing=True)
    first._set(record['lease'],status='REMOVED')
    second.set_capacity(0)
    with pytest.raises(ContainerCapacityError):first.reserve_attempt('blocked','blocked')
    assert first.census()['limit']==0


def test_reserved_attempt_is_bound_to_identity_and_consumable_only_once(tmp_path):
    runner=DockerRunner(policy(),tmp_path)
    reservation=runner.reserve_attempt('job','attempt')
    with pytest.raises(ContainerError):runner._consume_reservation(reservation,'job','other')
    result=runner._consume_reservation(reservation,'job','attempt')
    assert result['status']=='EXECUTING'
    with pytest.raises(ContainerError):runner._consume_reservation(reservation,'job','attempt')


def test_never_started_container_needs_proof_that_previous_controller_stopped(tmp_path):
    runner=DockerRunner(policy(),tmp_path)
    assert not runner.confirm_attempt_cleanup('unknown')['cleanup_verified']
    assert runner.confirm_attempt_cleanup('unknown',controller_stopped=True)['cleanup_verified']


def test_real_capacity_zero_during_preparation_removes_late_container(tmp_path,docker_image,monkeypatch):
    runner=DockerRunner(policy(docker_image),tmp_path)
    original=runner._call
    def publish_stop(arguments,**kwargs):
        result=original(arguments,**kwargs)
        if arguments[0]=='run' and result.returncode==0:
            runner.set_capacity(0)
        return result
    monkeypatch.setattr(runner,'_call',publish_stop)
    result=runner.prepare_pool(target=1)
    assert result['prepared']==[] and len(result['drained'])==1
    assert result['census']['owned']==0 and result['census']['limit']==0


def test_capacity_revoked_between_reservation_and_execution_keeps_bound_cleanup_hold(tmp_path):
    runner=DockerRunner(policy(),tmp_path)
    reservation=runner.reserve_attempt('job','attempt')
    runner.set_capacity(0)
    with pytest.raises(ContainerCapacityError,match='revoked'):
        runner._consume_reservation(reservation,'job','attempt')
    assert runner.census()['containers'][0]['status']=='BOUND'
    assert runner.census()['owned']==1


def test_windows_attestation_precedes_every_docker_command_and_failure_never_launches_client(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from cochem_pipeline import containers, deployment
    runner=DockerRunner(policy(endpoint='npipe:////./pipe/docker_engine',
        pipe_server_executables=[r'C:\Program Files\Docker\Docker\resources\com.docker.backend.exe']),
        tmp_path,trusted_operator='reviewed-operator')
    calls=[]
    def attest(endpoint,identities,**context):
        calls.append(('attest',endpoint,identities,context))
    monkeypatch.setattr(containers,'os',SimpleNamespace(name='nt',environ=dict(os.environ)))
    monkeypatch.setattr(deployment,'attest_docker_pipe_server',attest)
    monkeypatch.setattr(containers,'_bounded_process',lambda argv,**kwargs:calls.append(('client',argv)))
    runner._call(['info']);runner._call(['inspect','exact-id'])
    assert [call[0] for call in calls]==['attest','client','attest','client']
    assert calls[0][2]=={} and calls[0][3]['trusted_operator']=='reviewed-operator'
    assert calls[0][3]['trusted_server_executables']==runner.policy.pipe_server_executables
    def deny(*args,**kwargs):
        raise RuntimeError('untrusted pipe server')
    monkeypatch.setattr(deployment,'attest_docker_pipe_server',deny)
    with pytest.raises(RuntimeError,match='untrusted'):
        runner._call(['run','untrusted'])
    assert len(calls)==4


def test_uncertain_creation_absence_is_not_cleanup_even_after_controller_stopped(tmp_path,monkeypatch):
    runner=DockerRunner(policy(),tmp_path)
    record=runner._reserve('_warm_','uncertain',preparing=True)
    runner._daemon_identity='real-daemon-id-fixture'
    runner._begin_creation(record)
    monkeypatch.setattr(runner,'_inspect_owned',lambda record:None)
    result=runner.confirm_attempt_cleanup('uncertain',controller_stopped=True)
    assert not result['cleanup_verified']
    row=runner.census()['containers'][0]
    assert row['status']=='QUARANTINED' and row['creation_uncertain']==1


@pytest.mark.parametrize('boot,daemon,cleared',[
    ('100','same-daemon',False),('200','different-daemon',False),('200','same-daemon',True)])
def test_uncertain_creation_requires_full_new_host_boot_and_same_local_daemon(tmp_path,monkeypatch,boot,daemon,cleared):
    runner=DockerRunner(policy(),tmp_path)
    record=runner._reserve('_warm_','uncertain',preparing=True)
    runner._set(record['lease'],creation_uncertain=1,creation_boot_id='100',creation_daemon_id='same-daemon')
    monkeypatch.setattr(runner,'_native_boot_identity',lambda:boot)
    monkeypatch.setattr(runner,'_inspect_owned',lambda record:None)
    monkeypatch.setattr(runner,'_json',lambda argv:{'ID':daemon})
    result=runner.confirm_attempt_cleanup('uncertain',controller_stopped=True)
    assert result['cleanup_verified'] is cleared
    assert runner.census()['owned']==(0 if cleared else 1)


def test_real_late_daemon_object_is_reaped_even_after_historic_removed_tombstone(tmp_path,docker_image):
    runner=DockerRunner(policy(docker_image),tmp_path)
    record=runner._reserve('_warm_','historic-late-create',preparing=True)
    runner._set(record['lease'],status='REMOVED')
    try:
        created=runner._call(runner.create_arguments(record))
        assert created.returncode==0,created.stderr
        assert runner.census()['owned']==0  # Historic false-404 record.
        result=runner.reap_orphans(set())
        assert result['quarantined']==[]
        assert result['removed']==[record['lease']]
        assert runner._inspect_owned(record) is None
        assert runner.census()['owned']==0
    finally:
        runner._remove(record)


def test_real_unacknowledged_creation_remains_held_then_reaps_actual_late_object(tmp_path,docker_image):
    runner=DockerRunner(policy(docker_image),tmp_path)
    runner.preflight()
    record=runner._reserve('_warm_','late-create',preparing=True)
    runner._begin_creation(record)
    with pytest.raises(ContainerCleanupError,match='Unacknowledged'):
        runner._remove(record)
    assert runner.census()['quarantined']==1
    try:
        created=runner._call(runner.create_arguments(record))
        assert created.returncode==0,created.stderr
        result=runner.reap_orphans(set())
        assert result['quarantined']==[] and result['removed']==[record['lease']]
        assert runner.census()['owned']==0
    finally:
        runner._remove(record)


def test_real_unknown_owner_label_blocks_admission_without_deleting_unproved_identity(tmp_path,docker_image):
    runner=DockerRunner(policy(docker_image),tmp_path)
    record=runner._reserve('_warm_','unknown-fixture',preparing=True)
    # A second privileged producer forged the owner label but has no protected
    # lease. The controller must fail closed, not assume labels authorize rm.
    with runner._connect() as db:
        db.execute('DELETE FROM containers WHERE lease=?',(record['lease'],))
    created=runner._call(runner.create_arguments(record))
    assert created.returncode==0,created.stderr
    identifier=created.stdout.decode().strip()
    try:
        result=runner.reap_orphans(set())
        assert len(result['quarantined'])==1 and result['removed']==[]
        assert runner.census()['unknown_owned']==1
        assert runner._inspect_owned(record)['Id']==identifier
        with pytest.raises(ContainerCapacityError,match='Unrecognized'):
            runner.reserve_attempt('blocked','blocked')
    finally:
        # Only this test knows it created this unregistered object.
        assert runner._call(['rm','--force',identifier]).returncode==0
    assert runner.reap_orphans(set())['quarantined']==[]
    assert runner.census()['unknown_owned']==0


def test_real_joint_admission_drains_one_idle_container_before_native_claim(tmp_path,docker_image):
    from cochem_pipeline.admission import JointAdmission
    from cochem_pipeline.store import JobStore
    store=JobStore(tmp_path/'jobs.db')
    runner=DockerRunner(policy(docker_image),tmp_path/'containers')
    admission=JointAdmission(store,runner,4)
    try:
        prepared=runner.prepare_pool(target=4)
        assert len(prepared['prepared'])==4
        store.submit('Observe joint physical capacity',['REQ-1'],1)
        assert admission.claim('test-owner',worker_slot='slot1') is None
        assert store.active_jobs()==[] and runner.census()['owned']==4
        result=admission.maintenance()
        assert len(result['drained'])==1
        assert runner.census()['owned']==3
        node,reservation=admission.claim('test-owner',worker_slot='slot1')
        assert reservation is None and node['kind']=='MANIFEST_GENERATOR'
        assert len(store.active_jobs())+runner.census()['owned']==4
        for record in runner.census()['containers']:
            assert runner._inspect_owned(record)['State']['Running']
    finally:
        runner.reap_orphans(set(),include_warm=True)


def test_real_pytest_controller_rootdir_seals_nested_junit_module_and_class(tmp_path,docker_image):
    source=tmp_path/'source'
    nested=source/'tests'/'nested'
    nested.mkdir(parents=True)
    (nested/'test_canonical.py').write_text('class TestEvidence:\n def test_identity(self): assert True\n')
    runner=DockerRunner(policy(docker_image,commands=[{
        'name':'nested','argv':['python','-m','pytest','-q','tests/nested',
                              '--rootdir=/work/source/tests/nested']}]),tmp_path/'containers')
    receipt=runner.run(source,job_id='canonical-root',attempt_id='actual-pytest')
    assert receipt['passed'] and receipt['source_verified'] and receipt['cleanup_verified'],receipt
    command=receipt['commands'][0]
    assert command['argv'][-4]=='--rootdir=/work/source'
    assert command['junit_cases']==[{
        'name':'test_identity','class_name':'tests.nested.test_canonical.TestEvidence',
        'status':'passed','message':'','diagnostic':''}]
