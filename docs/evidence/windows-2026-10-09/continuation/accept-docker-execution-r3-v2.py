"""One fresh two-container physical boundary trial; not a routed coding workflow.

Requires prior accepted scoped foundation. No native inference, owner profile,
provider authentication, pipeline bootstrap, knowledge refresh or budget work.
The existing empty registry gains two retained leases/demand rows/receipts. Both
owned containers are single-use; cleanup preserves quarantine when unverified.
"""
from __future__ import annotations
from contextlib import ExitStack,contextmanager,closing
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from types import SimpleNamespace

ROOT=Path(r'C:\Program Files\CoChem\DockerExecutionAcceptance4.2.7-windows-20261007-r3-v2')
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
FOUNDATION_ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2')
FOUNDATION_SHA='620c43db688bac8fca2c7870197c92a7cb4b54f0a1c6b81bc3453ab6b2a3ff0c'
DIAGNOSTIC_SHA='fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5'
DIAGNOSTIC_ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1')
FAILED_FOUNDATION_ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3')
CONTINUITY_PINS={
    'diagnostic_receipt_sha256':'cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9',
    'diagnostic_packet_sha256':'8744b6a0b569b820404734a491dd7b1671ab2a439ccc27f7251dccbf75a7550d',
    'failed_foundation_receipt_sha256':'bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336',
    'failed_foundation_packet_sha256':'a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6',
}
PREFLIGHT_SHA='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
WORKER_SHA='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
PRIVATE=Path(r'C:\ProgramData\CoChemPipeline427\private')
WORKSPACE=Path(r'R:\CoChem427-windows-20261007\slot1\execution-acceptance-r3-v2')
SLOTS=tuple('slot'+str(n) for n in range(1,7))
TEST_SOURCE=b'''import errno, os, pathlib, socket
from business import answer

def test_physical_sandbox():
    assert os.getuid()==1000 and os.getgid()==1000
    status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
    assert status['NoNewPrivs'].strip()=='1' and status['Seccomp'].strip()=='2'
    assert int(status['CapEff'].strip(),16)==0
    mounts={line.split()[1]:line.split()[2] for line in pathlib.Path('/proc/mounts').read_text().splitlines()}
    assert mounts['/work']=='tmpfs' and mounts['/tmp']=='tmpfs'
    root=pathlib.Path('/sys/fs/cgroup')
    if (root/'cgroup.controllers').exists():
        assert int((root/'memory.max').read_text())==4294967296
        assert int((root/'pids.max').read_text())==512
        quota,period=map(int,(root/'cpu.max').read_text().split())
    else:
        assert int((root/'memory/memory.limit_in_bytes').read_text())==4294967296
        assert int((root/'pids/pids.max').read_text())==512
        cpu=root/('cpu' if (root/'cpu').is_dir() else 'cpu,cpuacct')
        quota=int((cpu/'cpu.cfs_quota_us').read_text());period=int((cpu/'cpu.cfs_period_us').read_text())
    assert quota==2*period
    assert {name for _,name in socket.if_nameindex()}=={'lo'}
    try:
        pathlib.Path('/cochem-readonly-root-probe').write_bytes(b'forbidden')
    except OSError as error:
        assert error.errno in (errno.EROFS,errno.EACCES)
    else:
        raise AssertionError('Root unexpectedly writable')
    probe=pathlib.Path('/tmp/cochem-tmpfs-probe');probe.write_bytes(b'bounded');assert probe.read_bytes()==b'bounded';probe.unlink()

def test_answer():
    assert answer()==42
'''
FIXTURES={phase:{'tests/business.py':f'def answer(): return {number}\n'.encode(),'tests/test_contract.py':TEST_SOURCE}
          for phase,number in (('red',41),('green',42))}


def digest(raw):return hashlib.sha256(raw).hexdigest()


def failure_chain(error,diagnostic=None):
    if diagnostic is not None:return diagnostic.error_chain(error)
    code=getattr(error,'winerror',None)
    code=code if type(code) is int and 0<=code<=0xffffffff else None
    kind=type(error).__name__
    if kind not in {'WindowsIsolationError','OSError','PermissionError','FileNotFoundError','ValueError','TimeoutError','ContainerError','RuntimeError','KeyError','TypeError'}:kind='OtherError'
    return {'nodes':[{'parent':None,'relation':'root','error_type':kind,'operation':'unclassified',
        'winerror':code,'winerror_source':'attribute' if code is not None else None}],
        'truncated':error.__cause__ is not None or error.__context__ is not None,'raw_exception_text_published':False}


def foundation_binding(value,packet,revision):
    if any(packet.get(k)!=v for k,v in CONTINUITY_PINS.items()):raise ValueError('Preserved diagnostic authority differs')
    fields={'schema':'cochem-execution-foundation/2','status':'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED',
        **CONTINUITY_PINS,'original_failure_cause_established':False,
        'helper_sha256':FOUNDATION_SHA,'system_sid':'S-1-5-18','runtime_root':str(INSTALL),
        'install_receipt_sha256':packet['install_receipt_sha256'],'preflight_receipt_sha256':packet['preflight_receipt_sha256'],
        'activation_ready':False,'automatic_retry_allowed':False,'ram_ledger_created_new':True,'ram_scoped_roots_verified':6,
        'containers_created':0,'warm_pool_created':0,'native_model_jobs_executed':0,
        'legacy_databases_or_budgets_modified':False,'credentials_or_native_profiles_modified':False,
        'ram_volume_or_startup_task_modified':False,'existing_tasks_modified_or_run':False,
        'knowledge_service_constructed_or_refreshed':False,'general_readiness_called':False}
    if not isinstance(value,dict) or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in fields.items()):raise ValueError('Foundation success binding differs')
    if (value.get('revision')!=revision or value.get('ram_before')!=value.get('ram_after')
        or value.get('worker_receipt_sha256')!=packet.get('workers') or 'failure' in value):raise ValueError('Foundation preservation binding differs')
    registry=value['registry']
    for key,expected in {'registry_created_new':True,'capacity':4,'unknown_owned':0,'work_rows':0,'integrity_check':'ok','journal_mode':'wal'}.items():
        if type(registry.get(key)) is not type(expected) or registry.get(key)!=expected:raise ValueError('Foundation empty registry binding differs')
    for candidate in (value.get('nonce'),):
        if not re.fullmatch('[a-f0-9]{32}',str(candidate)):raise ValueError('Foundation nonce differs')
    for candidate in (registry.get('database_sha256'),registry.get('owner_sha256'),value.get('ram_ledger_sha256')):
        if not re.fullmatch('[a-f0-9]{64}',str(candidate)):raise ValueError('Foundation state pin differs')


@contextmanager
def reserve_worker_identities(win,identities):
    """Exact production exclusion: named object existence, not Wait ownership."""
    handles=[]
    try:
        for slot in SLOTS:
            sid=win._sid_text(win._account_sid(identities[slot].name))
            ctypes.set_last_error(0)
            handle=win._check(win._api()['kernel32'].CreateMutexW(None,False,'Global\\CoChemPipeline422-'+sid),'Reserve existing worker exclusion name')
            existed=ctypes.get_last_error()==183
            handles.append(handle)
            if existed:raise ValueError('A selected worker identity is already active')
        yield
    finally:
        for handle in reversed(handles):win._close(handle)


def source_manifest(files):
    entries={name:{'sha256':digest(raw),'size':len(raw),'executable':False} for name,raw in files.items()}
    raw=json.dumps(entries,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return {'sha256':digest(raw),'files':entries,'file_count':len(files),'bytes':sum(map(len,files.values()))}


def create_sources(win,descriptor,require_absent):
    from cochem_pipeline.ramdisk import _no_content_index,ordinary_tree
    descriptor.validate(identity=descriptor.identity,cwd=descriptor.root)
    require_absent(WORKSPACE);WORKSPACE.mkdir()
    sid=win._sid_text(win._account_sid(descriptor.identity))
    win._set_acl(WORKSPACE,sid);win._validate_worker_directory(WORKSPACE,sid,protected=True)
    _no_content_index(WORKSPACE,apply=True)
    for phase,files in FIXTURES.items():
        source=WORKSPACE/phase;source.mkdir();(source/'tests').mkdir()
        for name,raw in files.items():
            path=source/name
            with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            ordinary_tree(path);_no_content_index(path,apply=True)
        for path in (source,source/'tests'):_no_content_index(path,apply=True)
        descriptor.validate(identity=descriptor.identity,cwd=source)


def receipt_summary(receipt,phase,source,policy):
    if (receipt.get('kind')!='docker-test-execution' or receipt.get('policy_sha256')!=policy.digest
        or receipt.get('image_id')!=policy.image or receipt.get('source')!=source
        or any(receipt.get(key) is not True for key in ('cleanup_verified','source_verified','input_source_verified','warm_pool_used','test_cycle_deadline_met'))
        or receipt.get('quarantine_required') is not False or receipt.get('container_creation_path')!='background_pool_replenishment'):
        raise ValueError('Physical execution or cleanup evidence incomplete')
    commands=receipt.get('commands',[])
    if len(commands)!=1 or commands[0].get('kind')!='pytest':raise ValueError('Expected one fixed pytest invocation')
    command=commands[0];cases=command.get('junit_cases',[]);junit=command.get('junit',{})
    expected_failed=1 if phase=='red' else 0
    if (junit.get('tests')!=2 or junit.get('failures')!=expected_failed or junit.get('errors')!=0 or junit.get('skipped')!=0
        or len(cases)!=2 or command.get('exit_code')!=expected_failed or receipt.get('passed') is not (phase=='green')
        or receipt.get('failure_category')!=('tests_failed' if phase=='red' else None)):
        raise ValueError('Actual RED/GREEN results differ from the fixed fixture')
    failures=[case for case in cases if case.get('status')=='failed']
    if phase=='red' and (len(failures)!=1 or failures[0].get('name')!='test_answer'
        or failures[0].get('assertion_failure',{}).get('exception_type')!='AssertionError'
        or failures[0].get('assertion_failure',{}).get('phase')!='call'):raise ValueError('RED must be the intended executed assertion')
    if any(case.get('status') not in ('passed','failed') for case in cases):raise ValueError('Unexpected test status')
    for key in ('junit_sha256','assertions_sha256','assertion_observer_sha256'):
        if not re.fullmatch('[a-f0-9]{64}',str(command.get(key,''))):raise ValueError('Missing physical test evidence hash')
    return {'phase':phase,'expected_test_failure':phase=='red','tests':2,'failures':expected_failed,'source_sha256':source['sha256'],
        'receipt_sha256':receipt['receipt_sha256'],'container_id_sha256':digest(receipt['container_id'].encode()),
        'cleanup_verified':True,'input_and_output_source_verified':True,'test_cycle_seconds':receipt['test_cycle_seconds'],
        'startup_seconds':receipt['startup_seconds'],'startup_sla_met':receipt['startup_sla_met'],
        'junit_sha256':command['junit_sha256'],'assertions_sha256':command['assertions_sha256']}


def execute_cycle(runner,descriptor,progress,report):
    before=runner.census()
    if any(before[k]!=0 for k in ('owned','warm','active','preparing','quarantined','unknown_owned','pending_profiles')) or before['published_capacity']!=4:raise ValueError('Registry is not empty at unchanged capacity')
    active_phase='prepare_two';report['container_creation_started']=True;progress(active_phase)
    try:
        prepared=runner.prepare_pool(target=2)
        census=prepared['census'];ids=prepared['prepared']
        if len(ids)!=2 or len(set(ids))!=2 or prepared.get('drained') or census['warm']!=2 or census['owned']!=2 or census['reserved_memory_mb']!=8192:raise ValueError('Two fresh bounded warm instances were not established')
        report['prepared_count']=2;report['maximum_owned_observed']=2
        summaries=[]
        for phase in ('red','green'):
            active_phase=phase+'_execution';progress(active_phase)
            job='windows-docker-physical-'+phase;attempt=job+'-'+report['nonce']
            record=runner.reserve_attempt(job,attempt)
            if record['container_id'] not in ids:raise ValueError('Reservation escaped this two-instance preparation')
            receipt=runner.run(WORKSPACE/phase,job_id=job,attempt_id=attempt,reservation=record,
                ramdisk_workspace=descriptor,source_modes={name:'100644' for name in FIXTURES[phase]})
            summary=receipt_summary(receipt,phase,source_manifest(FIXTURES[phase]),runner.policy)
            summary['private_receipt_name']=record['lease']+'.receipt.json';summaries.append(summary)
            report['executions']=summaries
        if len({item['container_id_sha256'] for item in summaries})!=2:raise ValueError('Single-use container identities were reused')
    finally:
        # One bounded ownership-verified drain of remaining WARM members only.
        # Production run already removes its consumed lease. No reap/retry,
        # unknown object adoption or quarantine clearing is attempted here.
        progress('drain_remaining_warm')
        try:runner.prepare_pool(target=0)
        finally:
            final=runner.census()
            report['final_registry_counts']={k:final[k] for k in ('owned','warm','active','preparing','quarantined','unknown_owned','published_capacity')}
            report['cleanup_verified']=all(final[k]==0 for k in ('owned','warm','active','preparing','quarantined','unknown_owned'))
        progress(active_phase)  # Keep the original failure phase if cleanup succeeded.
    if not report['cleanup_verified']:raise ValueError('Owned execution state did not finish empty')
    report['physical_boundary_verified']=True
    report['startup_sla_met']=all(item['startup_sla_met'] is True for item in report['executions'])


def bounded_runner_type(base,P,S,win,config,identities,baseline):
    class BoundedRunner(base):
        def prepare_pool(self,target=None,*,admission_lock=None):
            if type(target) is not int or target not in (0,2):raise ValueError('Only two-instance preparation or zero-instance drain is allowed')
            if target==2:
                if getattr(self,'_preparation_requested',False):raise ValueError('No second preparation or retry is allowed')
                self._preparation_requested=True
            return super().prepare_pool(target=target,admission_lock=admission_lock)

        def _call(self,arguments,*,timeout=30,output_limit=None,data=None,cancel_event=None):
            from cochem_pipeline.containers import _bounded_process
            from cochem_pipeline.deployment import attest_docker_pipe_server
            if not arguments or arguments[0] not in {'info','image','ps','inspect','run','exec','top','rm'}:raise ValueError('Out-of-scope Docker operation')
            if arguments[0]=='run':
                attempts=getattr(self,'_create_attempts',0)
                if attempts>=2 or not getattr(self,'_preparation_requested',False):raise ValueError('Creation attempt budget exhausted')
                self._create_attempts=attempts+1
            S.require_stopped(win)
            kw={'trusted_operator':config.operator_name,'trusted_server_executables':config.docker.pipe_server_executables}
            if P.server_identity(attest_docker_pipe_server(P.ENDPOINT,identities,**kw))!=baseline:raise ValueError('Docker server changed before command')
            env={key:os.environ[key] for key in ('SystemRoot','WINDIR') if key in os.environ};env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
            result=_bounded_process([str(P.DOCKER),'--config',str(ROOT/'docker-client-config'),'--host',P.ENDPOINT,*arguments],
                env=env,timeout=min(timeout,60),output_limit=min(output_limit or self.policy.output_limit_bytes,4194304),data=data,cancel_event=cancel_event)
            if P.server_identity(attest_docker_pipe_server(P.ENDPOINT,identities,**kw))!=baseline:raise ValueError('Docker server changed after command')
            S.require_stopped(win)
            return result
    return BoundedRunner


def run(nonce,packet_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    from cochem_pipeline.ramdisk import RamdiskManager,ordinary_tree
    from cochem_pipeline.containers import DockerRunner
    win.require_system()
    if Path(__file__).resolve()!=ROOT/'accept-docker-execution-r3-v2.py' or Path(sys.executable).resolve()!=INSTALL/'.venv/Scripts/python.exe':raise ValueError('Protected entrypoint differs')
    for path in (ROOT,Path(__file__),Path(sys.executable)):ordinary_tree(path);win.validate_code_path(path)
    report={'schema':'cochem-docker-physical-acceptance/2','nonce':nonce,'system_sid':win.SYSTEM_SID,'status':'UNVERIFIED',
        'helper_sha256':digest(Path(__file__).read_bytes()),'packet_sha256':packet_sha,'runtime_root':str(INSTALL),'started_at_unix_ms':int(time.time()*1000),
        'activation_ready':False,'chapter06_coding_workflow_tested':False,'native_model_jobs_executed':0,'credentials_or_native_profiles_modified':False,
        'legacy_databases_or_budgets_modified':False,'knowledge_service_constructed_or_refreshed':False,'ram_ensure_called':False,
        'ram_volume_or_startup_task_modified':False,'existing_tasks_modified_or_run':False,'automatic_retry_allowed':False,
        'ram_fixture_creation_started':False,'foundation_registry_mutation_started':False,
        'container_creation_started':False,'prepared_count':0,'cleanup_verified':False,'physical_boundary_verified':False,'executions':[]}
    phase='trusted_support';D=None
    def progress(value):
        nonlocal phase
        phase=value
    with (ROOT/'docker-physical-acceptance.json').open('x',encoding='utf-8') as output:
      try:
        with ExitStack() as stack:
            def support(name,pin):
                path=ROOT/name;ordinary_tree(path);win.validate_code_path(path)
                if path.stat().st_nlink!=1 or path.stat().st_size>1048576:raise ValueError('Support bound differs')
                raw=path.read_bytes()
                if digest(raw)!=pin:raise ValueError('Support pin differs')
                namespace={'__name__':'acceptance_support','__file__':str(path)};exec(compile(raw,str(path),'exec'),namespace);return namespace
            p=support('inspect-execution-prerequisites-r3.py',PREFLIGHT_SHA);p['ROOT']=ROOT;P=SimpleNamespace(**p)
            F=SimpleNamespace(**support('provision-execution-foundation-r3-v2.py',FOUNDATION_SHA));S=SimpleNamespace(**support('worker-denial-acceptance-r3.py',WORKER_SHA))
            D=SimpleNamespace(**support('diagnose-foundation-docker-r3-v1.py',DIAGNOSTIC_SHA))
            get=lambda path,m=1048576,h=None,private=False:stack.enter_context(P.held_read(path,m,win,h,private))
            for name,pin in [('inspect-execution-prerequisites-r3.py',PREFLIGHT_SHA),('provision-execution-foundation-r3-v2.py',FOUNDATION_SHA),('worker-denial-acceptance-r3.py',WORKER_SHA),('diagnose-foundation-docker-r3-v1.py',DIAGNOSTIC_SHA)]:get(ROOT/name,1048576,pin)
            get(S.BASE_PYTHON,1048576,S.BASE_SHA256)
            packet=P.strict_json(get(ROOT/'inputs.json',32768,packet_sha))
            if packet.get('schema')!='cochem-docker-physical-inputs/2' or set(packet.get('workers',{}))!=set(SLOTS):raise ValueError('Input packet differs')
            phase='runtime';revision=S.verify_r3_runtime(win,packet['install_receipt_sha256'])
            report.update(install_receipt_sha256=packet['install_receipt_sha256'],foundation_receipt_sha256=packet['foundation_receipt_sha256'],revision=revision)
            config=load_config(str(INSTALL/'pipeline.json'));raw,config_hash=S.protected_json(win,INSTALL/'pipeline.json');layout,_=S.protected_json(win,INSTALL/'windows-layout.json')
            if config_hash!=S.CONFIG_SHA256 or config.private_root!=PRIVATE or config.docker.max_containers!=4 or config.docker.warm_pool_size!=2:raise ValueError('Runtime policy differs')
            S.validate_layout(layout,raw);identities={slot:win.WorkerIdentity(row['name'],row['credential_target']) for slot,row in raw['workers'].items()}
            phase='prior_receipts'
            for slot in SLOTS:
                sid=win._sid_text(win._account_sid(identities[slot].name))
                if sid!=layout['slots'][slot]['sid']:raise ValueError('Worker identity changed')
                path=Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json'
                P.worker_receipt(P.strict_json(get(path,32768,packet['workers'][slot])),slot,packet['install_receipt_sha256'],revision,sid)
            prior=P.strict_json(get(F.PREFLIGHT_ROOT/'execution-prerequisites.json',131072,packet['preflight_receipt_sha256']));F.preflight_binding(prior,packet,revision)
            foundation=P.strict_json(get(FOUNDATION_ROOT/'execution-foundation.json',131072,packet['foundation_receipt_sha256']));foundation_binding(foundation,packet,revision)
            # Hold the exact preserved historical evidence throughout this new
            # acceptance. Neither old task nor old helper is run or modified.
            for path,key,maximum in (
                (DIAGNOSTIC_ROOT/'foundation-diagnostic.json','diagnostic_receipt_sha256',131072),
                (DIAGNOSTIC_ROOT/'inputs.json','diagnostic_packet_sha256',32768),
                (FAILED_FOUNDATION_ROOT/'execution-foundation.json','failed_foundation_receipt_sha256',131072),
                (FAILED_FOUNDATION_ROOT/'inputs.json','failed_foundation_packet_sha256',32768)):
                get(path,maximum,CONTINUITY_PINS[key])
            report.update(CONTINUITY_PINS)
            phase='knowledge_binding';P.only_resource_delta(P.strict_json(get(P.PRIOR/'source-manifest.json',1048576,P.PRIOR_MANIFEST_SHA)),P.strict_json(get(INSTALL/'source-manifest.json',1048576,S.MANIFEST_SHA256)))
            kb=P.strict_json(get(P.KNOWLEDGE,131072,P.KNOWLEDGE_SHA));P.knowledge_receipt(kb)
            get(P.STATE/'writer.lock',1,P.sha(b'0'),True);get(P.STATE/'current.json',16384,kb['current_pointer_sha256'],True);get(P.STATE/'sources.json',1048576,kb['source_pins_sha256'],True)
            get(P.STATE/kb['generation']/'knowledge_index.db',134217728,kb['index_sha256'],True)
            phase='docker_custody';stack.enter_context(P.held_read(P.DOCKER,67108864,win,P.DOCKER_SHA,retain=False));stack.enter_context(P.held_read(P.BACKEND,268435456,win,P.BACKEND_SHA,retain=False));P.docker_import_custody(win)
            ordinary_tree(ROOT/'docker-client-config');win.validate_code_path(ROOT/'docker-client-config')
            if any((ROOT/'docker-client-config').iterdir()):raise ValueError('CLI config must remain empty')
            S.require_stopped(win)
            phase='worker_exclusion';stack.enter_context(reserve_worker_identities(win,identities));report['all_six_worker_identities_excluded']=True
            stack.enter_context(F.production_ram_lock(win,config.ramdisk.mount_root))
            phase='foundation_state';get(PRIVATE/'ramdisk-state.json',1048576,foundation['ram_ledger_sha256'],True)
            manager=RamdiskManager(config.ramdisk,PRIVATE,identities);manager.inspect(require_capacity=True);descriptor=manager.workspace('slot1')
            F.require_absent(WORKSPACE);F.require_absent(PRIVATE/'execution-readiness.json')
            if P.ram_observation(win,config,progress)!=foundation['ram_after']:raise ValueError('R/root/task foundation changed')
            docker=P.docker_observation(win,identities,config,progress)
            if not docker['ownership_census']['empty'] or any(docker[key]!=foundation['docker_before'][key] for key in ('server','engine','api_aliases')):raise ValueError('Docker foundation changed or is not empty')
            root=PRIVATE/'containers';database=root/'containers.db';ordinary_tree(root);win.validate_private_directory(root)
            if {item.name for item in root.iterdir()}!={'containers.db'}:raise ValueError('Registry already contains acceptance/history outputs')
            # Release this read hold before the explicitly declared registry writes.
            with P.held_read(database,1048576,win,foundation['registry']['database_sha256'],True) as raw_db:
                conn=sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True)
                try:owner=conn.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()[0]
                finally:conn.close()
                if digest(owner.encode())!=foundation['registry']['owner_sha256']:raise ValueError('Registry owner changed')
                F.registry_inspection(database,owner)
            phase='fixture_sources';report['ram_fixture_creation_started']=True;create_sources(win,descriptor,F.require_absent)
            S.require_stopped(win)
            if digest(database.read_bytes())!=foundation['registry']['database_sha256']:raise ValueError('Registry drift before declared mutation')
            phase='registry_open';report['foundation_registry_mutation_started']=True
            runner=bounded_runner_type(DockerRunner,P,S,win,config,identities,docker['server'])(config.docker,root,trusted_operator=config.operator_name)
            if runner.owner!=owner:raise ValueError('Existing owner was not preserved')
            execute_cycle(runner,descriptor,progress,report)
            phase='final_preservation';S.require_stopped(win);manager.inspect(require_capacity=True)
            if P.ram_observation(win,config,progress)!=foundation['ram_after']:raise ValueError('R/root/task drift after execution')
            final=P.docker_observation(win,identities,config,progress)
            if not final['ownership_census']['empty'] or any(final[key]!=docker[key] for key in ('server','engine','api_aliases')):raise ValueError('Final Docker census or identity differs')
            F.require_absent(PRIVATE/'execution-readiness.json')
            report.update(final_physical_census_empty=True,ram_ledger_preserved=True,registry_owner_preserved=True,shared_capacity=4,native_occupancy=0)
        report['status']='DOCKER_PHYSICAL_ACCEPTANCE_VERIFIED' if report['startup_sla_met'] else 'DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_SLA_HELD'
      except BaseException as error:
        failure=P.safe_failure(error,phase) if 'P' in locals() else {'phase':phase,'error_type':type(error).__name__,'winerror':None}
        failure['chain']=failure_chain(error,D)
        report.update(status='DOCKER_PHYSICAL_ACCEPTANCE_HELD',failure=failure,partial_outputs_preserved=True,operator_review_required=True)
      finally:
        report['finished_at_unix_ms']=int(time.time()*1000);json.dump(report,output,sort_keys=True,indent=2);output.flush();os.fsync(output.fileno())
    return 0 if report['status']=='DOCKER_PHYSICAL_ACCEPTANCE_VERIFIED' else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--nonce',required=True);parser.add_argument('--packet-sha256',required=True);args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.nonce) or not re.fullmatch('[a-f0-9]{64}',args.packet_sha256):parser.error('Bounded reviewed arguments required')
    try:return run(args.nonce,args.packet_sha256)
    except BaseException as error:print(json.dumps({'status':'PRE_RECEIPT_FAILURE','error_type':type(error).__name__,'chain':failure_chain(error)}),file=sys.stderr);return 3


if __name__=='__main__':raise SystemExit(main())
