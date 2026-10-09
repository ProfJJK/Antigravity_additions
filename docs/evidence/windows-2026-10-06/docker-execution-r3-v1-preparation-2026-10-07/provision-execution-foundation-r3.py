"""Fresh scoped RAM roots and empty Docker registry only. Reviewed task required.

This candidate never calls general execution_readiness, KnowledgeService,
workspace.environment, native-cache bindings or Docker container methods.
Existing root/task/DB/ledger evidence is never reset, reused or overwritten.
"""
from __future__ import annotations
from contextlib import ExitStack,contextmanager
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

ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3')
PREFLIGHT_ROOT=Path(r'C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3')
PREFLIGHT_SHA='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
WORKER_SHA='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PRIVATE=Path(r'C:\ProgramData\CoChemPipeline427\private')
BOUNDARY=Path(r'R:\CoChem427-windows-20261007')
SLOTS=tuple('slot'+str(i) for i in range(1,7))


def digest(raw):return hashlib.sha256(raw).hexdigest()


def require_absent(path):
    from cochem_pipeline.ramdisk import ordinary_tree
    ordinary_tree(path,allow_missing=True)
    try:path.lstat()
    except FileNotFoundError:return
    raise ValueError('Fresh-only target already exists')


def preflight_binding(value,packet,revision):
    expected={'schema':'cochem-execution-prerequisites/1','status':'READ_ONLY_PREREQUISITES_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':PREFLIGHT_SHA,'runtime_root':str(INSTALL),
        'install_receipt_sha256':packet['install_receipt_sha256'],'activation_ready':False,'native_model_jobs_executed':0,
        'knowledge_service_constructed':False,'docker_runner_constructed':False,'ram_ensure_called':False,
        'existing_databases_modified':False,'existing_files_or_acls_modified':False,'existing_tasks_modified_or_run':False,
        'native_provider_authentication_performed':False}
    if not isinstance(value,dict) or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in expected.items()):raise ValueError('Actual successful preflight is required')
    if (value.get('holds')!=[] or value.get('worker_receipt_sha256')!=packet['workers'] or value.get('revision')!=revision
        or value.get('ram_before')!=value.get('ram_after') or value.get('fresh_state_before')!=value.get('fresh_state_after')
        or not re.fullmatch('[a-f0-9]{32}',str(value.get('nonce','')))):raise ValueError('Preflight preservation bindings differ')
    census=value['docker']['ownership_census']
    if census.get('empty') is not True or any(census.get(k)!=0 for k in ('owner_label_count','name_prefix_count','union_count')):raise ValueError('Existing Docker objects require separate ownership review')
    if value['knowledge'].get('current_bytes_unchanged') is not True or value['knowledge'].get('r2_to_r3_only_resource_limits_delta_verified') is not True:raise ValueError('Preflight knowledge binding incomplete')


def fresh_ram_type(base,expected_volume):
    class FreshRamManager(base):
        def _save_state(self,evidence):
            # The installed default publishes via replace. Fresh-only setup
            # instead refuses even a concurrently appearing ledger, leaving any
            # partial new file for review on write/fsync failure.
            if (evidence.get('state')!='READY' or evidence.get('adopted_existing_drive') is not True
                or evidence.get('workspace_root')!=str(BOUNDARY) or evidence.get('slots')!=list(SLOTS)
                or evidence.get('config')!=self.config.as_dict() or evidence.get('observed')!=expected_volume
                or evidence.get('mount_root')!=str(Path('R:/')) or evidence.get('lifecycle_action')!='ADOPT'
                or evidence.get('backup') is not None or evidence.get('volume_root_metadata_preserved') is not True
                or self.private_root!=PRIVATE):raise ValueError('Unexpected RAM ledger publication')
            from cochem_pipeline import windows as win
            win.validate_private_directory(self.private_root)
            path=self.private_root/'ramdisk-state.json'
            require_absent(path)
            with path.open('x',encoding='utf-8') as stream:
                json.dump(evidence,stream,sort_keys=True,indent=2);stream.flush();os.fsync(stream.fileno())
            win.validate_private_path(path)
    return FreshRamManager


@contextmanager
def production_ram_lock(win,mount):
    # Same Windows mutex as installed ensure(). Recursive ownership is supported
    # for this same thread; inner ensure releases only its own acquisition.
    kernel=win._api()['kernel32'];handle=win._check(kernel.CreateMutexW(None,False,
        'Global\\CoChemRAM-'+hashlib.sha256(str(mount).casefold().encode()).hexdigest()[:24]),'Open exact RAM mutex')
    acquired=False
    try:
        if kernel.WaitForSingleObject(handle,0) not in (0,0x80):raise ValueError('RAM provisioning mutex is busy')
        acquired=True;yield
    finally:
        if acquired:
            release=kernel.ReleaseMutex;release.argtypes=[win.HANDLE];release.restype=win.BOOL
            win._check(release(handle),'Release RAM provisioning mutex')
        win._close(handle)


def registry_inspection(path,expected_owner):
    """Immutable read-only postcondition of this exclusively created new DB."""
    for suffix in ('-wal','-shm','-journal'):require_absent(path.with_name(path.name+suffix))
    if not 0<path.stat().st_size<=1048576 or path.stat().st_nlink!=1:raise ValueError('New empty registry size/link bound differs')
    with path.open('rb') as stream:header=stream.read(100)
    # Immutable readers report connection-local journal_mode=delete even for
    # a persistently WAL database. File-header read/write versions attest WAL.
    if len(header)!=100 or header[:16]!=b'SQLite format 3\x00' or header[18:20]!=b'\x02\x02':raise ValueError('Expected persistent WAL registry header')
    database=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        database.execute('PRAGMA query_only=ON')
        if database.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('New registry integrity failed')
        tables={row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables!={'metadata','containers','pool_demand','preparation_requests','sqlite_sequence'}:raise ValueError('New registry schema differs')
        metadata=dict(database.execute('SELECT key,value FROM metadata'))
        if metadata!={'owner':expected_owner,'capacity':'4','unknown_owned':'0'} or not re.fullmatch('[a-f0-9]{32}',expected_owner):raise ValueError('New registry authority or capacity differs')
        if any(database.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]!=0 for table in ('containers','pool_demand','preparation_requests','sqlite_sequence')):raise ValueError('New registry unexpectedly contains work')
        columns={row[1] for row in database.execute('PRAGMA table_info(containers)')}
        expected={'lease','name','container_id','image','job_id','attempt_id','created_at','deadline','status','policy_digest','label_job_hash','memory_mb','pool_digest','endpoint','creation_uncertain','creation_boot_id','creation_daemon_id','request_started_at','reservation_started_at','prepared_at','preparation_seconds','pool_policy_json'}
        if columns!=expected:raise ValueError('New registry columns differ')
        return {'integrity_check':'ok','table_count':5,'work_rows':0,'capacity':4,'unknown_owned':0,'owner_sha256':digest(expected_owner.encode()),'journal_mode':'wal'}
    finally:database.close()


def create_fresh_registry(policy,private,create_directory,validate,runner_type):
    if policy.max_containers!=4 or policy.warm_pool_size!=2:raise ValueError('Initial registry capacity policy differs')
    root=private/'containers';require_absent(root)
    create_directory(root)  # Exact parent-validated inheritance, exist_ok=False.
    validate(root)
    path=root/'containers.db';require_absent(path)
    with path.open('xb'):pass  # Exclusive reservation; constructor sees our empty file.
    validate(path)
    identity=(path.stat().st_dev,path.stat().st_ino)
    runner=runner_type(policy,root,trusted_operator=r'AETHERDESK\ansac')
    if (path.stat().st_dev,path.stat().st_ino)!=identity:raise ValueError('New registry object was replaced')
    # No API/preflight/pool/census/reap/run method is called on this object.
    if any(item.name!='containers.db' for item in root.iterdir()):raise ValueError('Unexpected new registry sidecar; preserve partial state')
    validate(path)
    summary=registry_inspection(path,runner.owner)
    summary.update(database_sha256=digest(path.read_bytes()),database_bytes=path.stat().st_size,registry_created_new=True)
    return summary


def run(nonce,packet_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    from cochem_pipeline.ramdisk import RamdiskManager,ordinary_tree
    from cochem_pipeline.containers import DockerRunner
    from cochem_pipeline.knowledge import _private_directory
    win.require_system()
    if Path(__file__).resolve()!=ROOT/'provision-execution-foundation-r3.py' or Path(sys.executable).resolve()!=INSTALL/'.venv/Scripts/python.exe':raise ValueError('Protected provisioning entrypoint differs')
    for path in (ROOT,Path(__file__),Path(sys.executable)):
        ordinary_tree(path);win.validate_code_path(path)
    report={'schema':'cochem-execution-foundation/1','nonce':nonce,'system_sid':win.SYSTEM_SID,'status':'UNVERIFIED',
        'helper_sha256':digest(Path(__file__).read_bytes()),'packet_sha256':packet_sha,'runtime_root':str(INSTALL),
        'started_at_unix_ms':int(time.time()*1000),'activation_ready':False,'ram_provision_started':False,
        'registry_provision_started':False,'containers_created':0,'warm_pool_created':0,'native_model_jobs_executed':0,
        'legacy_databases_or_budgets_modified':False,'credentials_or_native_profiles_modified':False,
        'ram_volume_or_startup_task_modified':False,'existing_tasks_modified_or_run':False,
        'knowledge_service_constructed_or_refreshed':False,'general_readiness_called':False,'automatic_retry_allowed':False}
    phase='trusted_support'
    def progress(value):
        nonlocal phase
        phase=value
    with (ROOT/'execution-foundation.json').open('x',encoding='utf-8') as output:
      try:
        with ExitStack() as stack:
            # Bootstrap support code only after path/hash/size/one-link checks.
            def support(name,pin):
                path=ROOT/name;ordinary_tree(path);win.validate_code_path(path)
                if path.stat().st_nlink!=1 or path.stat().st_size>1048576:raise ValueError('Support bound differs')
                raw=path.read_bytes()
                if digest(raw)!=pin:raise ValueError('Support hash differs')
                namespace={'__name__':'foundation_support','__file__':str(path)}
                exec(compile(raw,str(path),'exec'),namespace)
                return namespace
            p=support('inspect-execution-prerequisites-r3.py',PREFLIGHT_SHA)
            p['ROOT']=ROOT  # Fixed read-only Docker commands use this new empty CLI config.
            P=SimpleNamespace(**p);S=SimpleNamespace(**support('worker-denial-acceptance-r3.py',WORKER_SHA))
            get=lambda path,m=1048576,h=None,private=False:stack.enter_context(P.held_read(path,m,win,h,private))
            get(ROOT/'inspect-execution-prerequisites-r3.py',1048576,PREFLIGHT_SHA);get(ROOT/'worker-denial-acceptance-r3.py',1048576,WORKER_SHA)
            get(S.BASE_PYTHON,1048576,S.BASE_SHA256)
            packet=P.strict_json(get(ROOT/'inputs.json',32768,packet_sha))
            if packet.get('schema')!='cochem-execution-foundation-inputs/1' or set(packet.get('workers',{}))!=set(SLOTS):raise ValueError('Bound provisioning packet differs')
            phase='runtime';revision=S.verify_r3_runtime(win,packet['install_receipt_sha256'])
            report.update(install_receipt_sha256=packet['install_receipt_sha256'],revision=revision,preflight_receipt_sha256=packet['preflight_receipt_sha256'])
            config=load_config(str(INSTALL/'pipeline.json'));raw_config,config_hash=S.protected_json(win,INSTALL/'pipeline.json');layout,_=S.protected_json(win,INSTALL/'windows-layout.json')
            if config_hash!=S.CONFIG_SHA256 or config.private_root!=PRIVATE or config.ramdisk.workspace_root!=BOUNDARY or not config.ramdisk.adopted_drive:raise ValueError('Exact adopted scope differs')
            S.validate_layout(layout,raw_config)
            identities={slot:win.WorkerIdentity(row['name'],row['credential_target']) for slot,row in raw_config['workers'].items()}
            phase='prerequisite_receipts'
            for slot in SLOTS:
                sid=win._sid_text(win._account_sid(identities[slot].name))
                if sid!=layout['slots'][slot]['sid']:raise ValueError('Worker identity changed')
                value=P.strict_json(get(Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json',32768,packet['workers'][slot]))
                P.worker_receipt(value,slot,packet['install_receipt_sha256'],revision,sid)
            prior=P.strict_json(get(PREFLIGHT_ROOT/'execution-prerequisites.json',131072,packet['preflight_receipt_sha256']));preflight_binding(prior,packet,revision)
            phase='knowledge_binding'
            P.only_resource_delta(P.strict_json(get(P.PRIOR/'source-manifest.json',1048576,P.PRIOR_MANIFEST_SHA)),P.strict_json(get(INSTALL/'source-manifest.json',1048576,S.MANIFEST_SHA256)))
            knowledge=P.strict_json(get(P.KNOWLEDGE,131072,P.KNOWLEDGE_SHA));P.knowledge_receipt(knowledge)
            get(P.STATE/'writer.lock',1,P.sha(b'0'),True);get(P.STATE/'current.json',16384,knowledge['current_pointer_sha256'],True)
            get(P.STATE/'sources.json',1048576,knowledge['source_pins_sha256'],True);get(P.STATE/knowledge['generation']/'knowledge_index.db',134217728,knowledge['index_sha256'],True)
            report['knowledge_index_sha256']=knowledge['index_sha256']
            phase='docker_custody'
            stack.enter_context(P.held_read(P.DOCKER,67108864,win,P.DOCKER_SHA,retain=False));stack.enter_context(P.held_read(P.BACKEND,268435456,win,P.BACKEND_SHA,retain=False));P.docker_import_custody(win)
            ordinary_tree(ROOT/'docker-client-config');win.validate_code_path(ROOT/'docker-client-config')
            if any((ROOT/'docker-client-config').iterdir()):raise ValueError('New isolated Docker config must be empty')
            S.require_stopped(win)
            with production_ram_lock(win,config.ramdisk.mount_root):
                phase='fresh_state';P.fresh_state(progress)
                before=P.ram_observation(win,config,progress)
                if before!=prior['ram_after']:raise ValueError('Preflight RAM/root/task baseline changed')
                docker=P.docker_observation(win,identities,config,progress)
                if not docker['ownership_census']['empty'] or docker['server']!=prior['docker']['server'] or docker['engine']!=prior['docker']['engine'] or docker['api_aliases']!=prior['docker']['api_aliases']:raise ValueError('Preflight Docker baseline changed')
                report['ram_before']=before;report['docker_before']=docker
                phase='defender_before';exclusions_before=win.defender_exclusions()
                expected={str(BOUNDARY/slot).casefold() for slot in SLOTS}
                S.require_stopped(win);P.fresh_state(progress)
                phase='ram_provision';report['ram_provision_started']=True
                manager=fresh_ram_type(RamdiskManager,before['volume'])(config.ramdisk,PRIVATE,identities)
                ram=manager.ensure()
                phase='ram_postconditions';manager.inspect(require_capacity=True)
                if {p.name for p in BOUNDARY.iterdir()}!=set(SLOTS) or any(any((BOUNDARY/s).iterdir()) for s in SLOTS):raise ValueError('New RAM boundary contains unexpected contents')
                exclusions_after=win.defender_exclusions()
                if exclusions_after!=exclusions_before|expected:raise ValueError('Defender exclusions changed outside exact six paths')
                if P.ram_observation(win,config,progress)!=before:raise ValueError('R root/device/task changed during scoped setup')
                report.update(ram_scoped_roots_verified=6,ram_ledger_created_new=True,ram_ledger_sha256=digest((PRIVATE/'ramdisk-state.json').read_bytes()),
                    defender_added_exact_roots_count=len(expected-exclusions_before),existing_defender_exclusions_preserved=True)
                phase='before_registry';S.require_stopped(win);require_absent(PRIVATE/'containers');require_absent(PRIVATE/'execution-readiness.json')
                current=P.docker_observation(win,identities,config,progress)
                if not current['ownership_census']['empty'] or current['server']!=docker['server'] or current['engine']!=docker['engine']:raise ValueError('Docker authority changed before registry creation')
                phase='registry_provision';report['registry_provision_started']=True
                report['registry']=create_fresh_registry(config.docker,PRIVATE,lambda path:_private_directory(path,exist_ok=False),win.validate_private_path,DockerRunner)
                phase='final_preservation';S.require_stopped(win)
                if P.ram_observation(win,config,progress)!=before:raise ValueError('R root/device/task changed')
                manager.inspect(require_capacity=True);require_absent(PRIVATE/'execution-readiness.json')
                report['ram_after']=before;report['ram_ledger']=ram
        report['status']='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'
      except BaseException as error:
        report.update(status='EXECUTION_FOUNDATION_HELD',failure={'phase':phase,'error_type':re.sub('[^A-Za-z0-9_]','',type(error).__name__)[:80],
            'winerror':getattr(error,'winerror',None) if type(getattr(error,'winerror',None)) is int else None},operator_review_required=True,partial_outputs_preserved=True)
      finally:
        report['finished_at_unix_ms']=int(time.time()*1000);json.dump(report,output,sort_keys=True,indent=2);output.flush();os.fsync(output.fileno())
    return 0 if report['status']=='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED' else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--nonce',required=True);parser.add_argument('--packet-sha256',required=True);args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.nonce) or not re.fullmatch('[a-f0-9]{64}',args.packet_sha256):parser.error('Bounded reviewed arguments required')
    try:return run(args.nonce,args.packet_sha256)
    except BaseException as error:
        print(json.dumps({'status':'PRE_RECEIPT_FAILURE','error_type':type(error).__name__}),file=sys.stderr);return 3


if __name__=='__main__':raise SystemExit(main())
