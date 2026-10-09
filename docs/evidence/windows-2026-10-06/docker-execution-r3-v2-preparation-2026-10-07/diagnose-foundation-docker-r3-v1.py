"""One fresh read-only diagnosis of the preserved pre-provisioning failure.

The frozen collector and native security decisions are unchanged. Transparent
wrappers expose bounded operation codes, never exception strings or Docker raw
output. There is no retry, provisioning, provider call, or existing-state write.
Only this new helper root's receipt and empty Docker client directory are used.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1')
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PREFLIGHT_ROOT=Path(r'C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3')
FOUNDATION_ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3')
PREFLIGHT_SHA='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
WORKER_SHA='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
FOUNDATION_SHA='fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1'
PREFLIGHT_RECEIPT_SHA='3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'
FOUNDATION_RECEIPT_SHA='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'
FOUNDATION_PACKET_SHA='a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6'
PREFLIGHT_PACKET_SHA='b5bbab59e9574e34a9a6b5109e22120cb925d20ee37eed8f598339872aa7da94'
INSTALL_RECEIPT_SHA='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
SLOTS=tuple('slot'+str(i) for i in range(1,7))
COMMAND_STAGES=('docker_info','docker_image','docker_owner_census','docker_name_census')
BOUNDARY_STAGES=('docker_pipe_denials_before','docker_pipe_denials_after')


def sha(raw):return hashlib.sha256(raw).hexdigest()


# Exact messages from the installed deployment/windows sources. Paths, account
# names, exception messages and raw Docker responses never enter the receipt.
CHECK_OPERATIONS={
    'Inspect actual Docker pipe server PID':'pipe_server_pid',
    'Open actual Docker pipe server process':'server_process_open',
    'Open Docker pipe server token':'server_token_open',
    'Read actual Docker pipe server executable':'server_image_query',
    'Read Docker server process creation time':'server_creation_time',
    'Recheck Docker pipe server PID':'pipe_server_pid_recheck',
    'Open current token':'current_token_open',
    'Token inspection':'token_inspection',
    'SID conversion':'sid_conversion',
    'Resolve worker account':'account_sid_lookup',
    'Read security control':'code_acl_control',
    'Read directory ACL':'code_acl_information',
    'Read ACL entry':'code_acl_entry',
    'Impersonate isolated worker for Docker access test':'worker_impersonation',
    'Read SYSTEM worker credential; provision this identity first':'worker_credential_read',
    'Worker batch logon; verify stored credential and SeBatchLogonRight':'worker_batch_logon',
    'Remove worker token privileges':'worker_privilege_removal',
    'Open exact RAM mutex':'ram_mutex_open',
    'Release RAM provisioning mutex':'ram_mutex_release',
}
GUARD_OPERATIONS={
    'Docker pipe server executable paths require an explicit reviewed allowlist':'server_allowlist_missing',
    'Docker pipe server returned an invalid process ID':'server_pid_invalid',
    'Docker pipe server token is not SYSTEM or the reviewed operator':'server_token_rejected',
    'Docker pipe server executable is outside the reviewed Docker backend allowlist':'server_image_rejected',
    'Docker pipe server exited or changed during attestation':'server_changed_or_exited',
    'Cannot inspect native Windows token':'token_inspection_size',
    'The Warden must run as NT AUTHORITY\\SYSTEM, not an administrator or an agent account':'system_identity_required',
    'Privileged code and native worker executables must be deployed under protected Program Files/Windows paths':'protected_code_root_required',
    'Credential target belongs to a different worker account':'worker_credential_identity_rejected',
    'Worker token SID does not match the configured local identity':'worker_token_identity_rejected',
    'Workers must not belong to administrative/operator groups':'worker_privileged_group_rejected',
    'Worker token retained privileges after restriction':'worker_privilege_retained',
}
ERROR_TYPES={'WindowsIsolationError','OSError','PermissionError','FileNotFoundError','ValueError','TimeoutError','ContainerError','RuntimeError','KeyError','TypeError'}


def error_node(error):
    kind=type(error).__name__
    native=getattr(error,'winerror',None)
    native=native if type(native) is int and 0<=native<=0xffffffff else None
    result={'error_type':kind if kind in ERROR_TYPES else 'OtherError',
            'operation':'unclassified','winerror':native,'winerror_source':'attribute' if native is not None else None}
    message=error.args[0] if len(error.args)==1 and type(error.args[0]) is str else None
    if kind!='WindowsIsolationError' or message is None or len(message)>2048:return result
    code=None
    match=re.fullmatch(r'Cannot inspect Docker pipe server \(Windows error ([0-9]{1,10})\)',message)
    if match:result['operation']='pipe_open';code=int(match[1])
    else:
        match=re.fullmatch(r'(.{1,100}) failed \(Windows error ([0-9]{1,10})\)',message)
        if match and match[1] in CHECK_OPERATIONS:
            result['operation']=CHECK_OPERATIONS[match[1]];code=int(match[2])
        elif message in GUARD_OPERATIONS:result['operation']=GUARD_OPERATIONS[message]
        elif re.fullmatch(r'Cannot inspect directory ACL \(Windows error ([0-9]{1,10})\): [^\r\n]{1,1024}',message):
            result['operation']='code_acl_query';code=int(re.match(r'Cannot inspect directory ACL \(Windows error ([0-9]{1,10})\)',message)[1])
        elif re.fullmatch(r'Untrusted code writes or path replacement are possible: [^\r\n]{1,1024}',message):result['operation']='code_acl_rejected'
        elif re.fullmatch(r'Dedicated local worker account is missing: [^\r\n]{1,512}',message):result['operation']='account_sid_missing'
        else:
            match=re.fullmatch(r'Docker (write|read|read_write) access denial for slot[1-6] was not established \(Win32 ([0-9]{1,10})\)',message)
            if match:result['operation']='worker_denial_not_established';code=int(match[2])
            elif re.fullmatch(r'Worker slot[1-6] can access the Docker daemon \((write|read|read_write)\)',message):result['operation']='worker_pipe_accessible'
    if code is not None and 0<=code<=0xffffffff:
        result['winerror']=code;result['winerror_source']='allowlisted_message'
    return result


def error_chain(error):
    """Bounded graph retains explicit causes AND contexts, including cycles."""
    queue=[(error,None,'root')];nodes=[];seen={};truncated=False
    while queue:
        current,parent,relation=queue.pop(0)
        if id(current) in seen:
            nodes.append({'parent':parent,'relation':relation,'reference':seen[id(current)]})
            if len(nodes)>=8:truncated=bool(queue);break
            continue
        index=len(nodes);seen[id(current)]=index
        nodes.append({'parent':parent,'relation':relation,**error_node(current)})
        for label,child in (('cause',current.__cause__),('context',current.__context__)):
            if child is not None:queue.append((child,index,label))
        if len(nodes)>=8:truncated=bool(queue);break
    return {'nodes':nodes,'truncated':truncated,'raw_exception_text_published':False}


class DiagnosticTrace:
    def __init__(self):
        self.stage='before_observation';self.events=[];self.counts={};self.commands_started=0;self.commands_completed=0

    def progress(self,stage):
        if stage not in COMMAND_STAGES+BOUNDARY_STAGES:raise ValueError('Unknown Docker collector stage')
        self.stage=stage

    def start(self,kind,position,endpoint=None):
        if len(self.events)>=96:raise ValueError('Diagnostic event bound exceeded')
        event={'stage':self.stage,'kind':kind,'position':position,'completed':False}
        if endpoint is not None:
            aliases={'npipe:////./pipe/dockerdesktoplinuxengine':'selected_linux','npipe:////./pipe/dockerdesktopwindowsengine':'windows_alias','npipe:////./pipe/docker_engine':'default_alias'}
            if not isinstance(endpoint,str) or endpoint.casefold() not in aliases:raise ValueError('Unexpected observed pipe alias')
            event['alias']=aliases[endpoint.casefold()]
        self.events.append(event)
        return event

    @contextmanager
    def instrument(self):
        """Delegate once to the original functions and restore on all exits."""
        from cochem_pipeline import deployment,containers
        original_attest=deployment.attest_docker_pipe_server
        original_transport=containers._bounded_process
        def attest(endpoint,*args,**kwargs):
            count=self.counts.get(self.stage,0)+1;self.counts[self.stage]=count
            if self.stage in COMMAND_STAGES:
                if count>2:raise ValueError('Unexpected command attestation count')
                position='before_cli' if count==1 else 'after_cli'
            elif self.stage in BOUNDARY_STAGES:position='boundary_attestation_'+str(count)
            else:raise ValueError('Attestation occurred outside reviewed stages')
            event=self.start('server_attestation',position,endpoint)
            try:
                result=original_attest(endpoint,*args,**kwargs);event['completed']=True;return result
            except BaseException as error:event['failure']=error_chain(error);raise
        def transport(argv,**kwargs):
            if self.stage not in COMMAND_STAGES or self.commands_started!=COMMAND_STAGES.index(self.stage):raise ValueError('Unexpected read-only transport order')
            self.commands_started+=1;event=self.start('bounded_cli','single_attempt')
            try:
                result=original_transport(argv,**kwargs);event['completed']=True
                self.commands_completed+=1;return result
            except BaseException as error:event['failure']=error_chain(error);raise
        deployment.attest_docker_pipe_server=attest;containers._bounded_process=transport
        try:yield
        finally:
            deployment.attest_docker_pipe_server=original_attest;containers._bounded_process=original_transport

    def result(self):
        return {'last_stage':self.stage,'events':self.events,'commands_started':self.commands_started,
                'commands_returned':self.commands_completed,'retry_attempts':0,'original_guard_decisions_unchanged':True}


@contextmanager
def production_ram_lock(win,mount):
    kernel=win._api()['kernel32'];handle=win._check(kernel.CreateMutexW(None,False,
        'Global\\CoChemRAM-'+sha(str(mount).casefold().encode())[:24]),'Open exact RAM mutex')
    acquired=False
    try:
        if kernel.WaitForSingleObject(handle,0) not in (0,0x80):raise ValueError('RAM inspection mutex is busy')
        acquired=True;yield
    finally:
        if acquired:
            release=kernel.ReleaseMutex;release.argtypes=[win.HANDLE];release.restype=win.BOOL
            win._check(release(handle),'Release RAM provisioning mutex')
        win._close(handle)


def prior_bindings(P,packet,prior,failed,old_packet,old_preflight_packet,revision):
    P.same_fields(packet,{'schema':'cochem-foundation-diagnostic-inputs/1','install_receipt_sha256':INSTALL_RECEIPT_SHA,
        'knowledge_receipt_sha256':P.KNOWLEDGE_SHA,'preflight_receipt_sha256':PREFLIGHT_RECEIPT_SHA,
        'failed_foundation_receipt_sha256':FOUNDATION_RECEIPT_SHA,'failed_foundation_packet_sha256':FOUNDATION_PACKET_SHA})
    if set(packet.get('workers',{}))!=set(SLOTS):raise ValueError('All worker commitments required')
    P.same_fields(prior,{'schema':'cochem-execution-prerequisites/1','status':'READ_ONLY_PREREQUISITES_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':PREFLIGHT_SHA,'runtime_root':str(INSTALL),'install_receipt_sha256':INSTALL_RECEIPT_SHA,
        'nonce':'fc64e3d3990049ed809b7189906dd0e9','packet_sha256':PREFLIGHT_PACKET_SHA,
        'activation_ready':False,'native_model_jobs_executed':0,'knowledge_service_constructed':False,
        'docker_runner_constructed':False,'ram_ensure_called':False,'existing_databases_modified':False,
        'existing_files_or_acls_modified':False,'existing_tasks_modified_or_run':False,'native_provider_authentication_performed':False})
    P.same_fields(failed,{'schema':'cochem-execution-foundation/1','status':'EXECUTION_FOUNDATION_HELD',
        'system_sid':'S-1-5-18','helper_sha256':FOUNDATION_SHA,'runtime_root':str(INSTALL),'install_receipt_sha256':INSTALL_RECEIPT_SHA,
        'nonce':'a92fe71e71224573a3738b5131ecfb36','packet_sha256':FOUNDATION_PACKET_SHA,
        'preflight_receipt_sha256':PREFLIGHT_RECEIPT_SHA,'activation_ready':False,'automatic_retry_allowed':False,
        'ram_provision_started':False,'registry_provision_started':False,'containers_created':0,'warm_pool_created':0,
        'native_model_jobs_executed':0,'credentials_or_native_profiles_modified':False,'legacy_databases_or_budgets_modified':False,
        'ram_volume_or_startup_task_modified':False,'existing_tasks_modified_or_run':False,
        'knowledge_service_constructed_or_refreshed':False,'general_readiness_called':False,'partial_outputs_preserved':True})
    if failed.get('failure')!={'phase':'docker_owner_census','error_type':'WindowsIsolationError','winerror':None}:raise ValueError('Prior failure differs')
    if (prior.get('revision')!=revision or failed.get('revision')!=revision or prior.get('holds')!=[]
        or prior.get('worker_receipt_sha256')!=packet['workers'] or prior.get('ram_before')!=prior.get('ram_after')
        or prior.get('fresh_state_before')!=prior.get('fresh_state_after')):raise ValueError('Prior preservation commitments differ')
    P.same_fields(prior['docker']['ownership_census'],{'empty':True,'owner_label_count':0,'name_prefix_count':0,'union_count':0})
    P.same_fields(prior['knowledge'],{'receipt_sha256':P.KNOWLEDGE_SHA,'current_bytes_unchanged':True,'r2_to_r3_only_resource_limits_delta_verified':True})
    if failed.get('knowledge_index_sha256')!=prior['knowledge']['index_sha256']:raise ValueError('Prior index differs')
    P.same_fields(old_packet,{'schema':'cochem-execution-foundation-inputs/1','install_receipt_sha256':INSTALL_RECEIPT_SHA,'knowledge_receipt_sha256':P.KNOWLEDGE_SHA,'preflight_receipt_sha256':PREFLIGHT_RECEIPT_SHA})
    P.same_fields(old_preflight_packet,{'schema':'cochem-execution-prerequisites-inputs/1','install_receipt_sha256':INSTALL_RECEIPT_SHA,'knowledge_receipt_sha256':P.KNOWLEDGE_SHA})
    if old_packet.get('workers')!=packet['workers'] or old_preflight_packet.get('workers')!=packet['workers']:raise ValueError('Original packet workers differ')


def run(nonce,packet_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    if Path(__file__).resolve()!=ROOT/'diagnose-foundation-docker-r3-v1.py' or Path(sys.executable).resolve()!=INSTALL/'.venv/Scripts/python.exe':raise ValueError('Protected diagnostic entrypoint differs')
    for path in (ROOT,Path(__file__),Path(sys.executable)):
        ordinary_tree(path);win.validate_code_path(path)
    report={'schema':'cochem-foundation-diagnostic/1','nonce':nonce,'system_sid':win.SYSTEM_SID,'status':'UNVERIFIED',
        'helper_sha256':sha(Path(__file__).read_bytes()),'packet_sha256':packet_sha,'runtime_root':str(INSTALL),
        'started_at_unix_ms':int(time.time()*1000),'activation_ready':False,'diagnostic_only':True,
        'ram_provision_started':False,'registry_provision_started':False,'existing_files_or_acls_modified':False,
        'existing_databases_modified':False,'existing_tasks_modified_or_run':False,'native_model_jobs_executed':0,
        'knowledge_service_constructed':False,'docker_runner_constructed':False,'ram_ensure_called':False,
        'own_fresh_receipt_created':True,'own_fresh_docker_config_directory_used':False,
        'worker_handle_probe_phase_entered':False,'native_provider_authentication_performed':False,
        'historical_exact_failure_unrecoverable_from_original_receipt':True,'automatic_retry_performed':False}
    phase='trusted_support';trace=DiagnosticTrace()
    def progress(value):
        nonlocal phase
        phase=value
    def docker_progress(value):
        progress(value);trace.progress(value)
        if value in BOUNDARY_STAGES:report['worker_handle_probe_phase_entered']=True
    with (ROOT/'foundation-diagnostic.json').open('x',encoding='utf-8') as output:
      try:
        with ExitStack() as stack:
            # Only definitions are loaded from the exact frozen support bytes.
            def support(name,pin):
                path=ROOT/name;ordinary_tree(path);win.validate_code_path(path)
                if path.stat().st_nlink!=1 or path.stat().st_size>1048576:raise ValueError('Support bound differs')
                raw=path.read_bytes()
                if sha(raw)!=pin:raise ValueError('Support hash differs')
                namespace={'__name__':'diagnostic_support','__file__':str(path)}
                exec(compile(raw,str(path),'exec'),namespace)
                return namespace
            p=support('inspect-execution-prerequisites-r3.py',PREFLIGHT_SHA);p['ROOT']=ROOT;P=SimpleNamespace(**p)
            S=SimpleNamespace(**support('worker-denial-acceptance-r3.py',WORKER_SHA))
            get=lambda path,m=1048576,h=None,private=False:stack.enter_context(P.held_read(path,m,win,h,private))
            get(ROOT/'inspect-execution-prerequisites-r3.py',1048576,PREFLIGHT_SHA);get(ROOT/'worker-denial-acceptance-r3.py',1048576,WORKER_SHA)
            get(S.BASE_PYTHON,1048576,S.BASE_SHA256)
            packet=P.strict_json(get(ROOT/'inputs.json',32768,packet_sha))
            phase='runtime';revision=S.verify_r3_runtime(win,INSTALL_RECEIPT_SHA)
            report.update(install_receipt_sha256=INSTALL_RECEIPT_SHA,revision=revision,
                preflight_receipt_sha256=PREFLIGHT_RECEIPT_SHA,failed_foundation_receipt_sha256=FOUNDATION_RECEIPT_SHA,
                failed_foundation_packet_sha256=FOUNDATION_PACKET_SHA)
            config=load_config(str(INSTALL/'pipeline.json'));raw_config,config_hash=S.protected_json(win,INSTALL/'pipeline.json');layout,_=S.protected_json(win,INSTALL/'windows-layout.json')
            if config_hash!=S.CONFIG_SHA256 or config.private_root!=P.PRIVATE or not config.ramdisk.adopted_drive:raise ValueError('Reviewed private adopted scope differs')
            S.validate_layout(layout,raw_config)
            identities={slot:win.WorkerIdentity(row['name'],row['credential_target']) for slot,row in raw_config['workers'].items()}
            phase='prior_receipts'
            prior=P.strict_json(get(PREFLIGHT_ROOT/'execution-prerequisites.json',131072,PREFLIGHT_RECEIPT_SHA))
            failed=P.strict_json(get(FOUNDATION_ROOT/'execution-foundation.json',131072,FOUNDATION_RECEIPT_SHA))
            old_packet=P.strict_json(get(FOUNDATION_ROOT/'inputs.json',32768,FOUNDATION_PACKET_SHA))
            old_preflight_packet=P.strict_json(get(PREFLIGHT_ROOT/'inputs.json',32768,PREFLIGHT_PACKET_SHA))
            prior_bindings(P,packet,prior,failed,old_packet,old_preflight_packet,revision)
            for slot in SLOTS:
                sid=win._sid_text(win._account_sid(identities[slot].name))
                if sid!=layout['slots'][slot]['sid']:raise ValueError('Worker SID differs')
                path=Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json'
                P.worker_receipt(P.strict_json(get(path,32768,packet['workers'][slot])),slot,INSTALL_RECEIPT_SHA,revision,sid)
            report['worker_receipt_sha256']=packet['workers']
            phase='knowledge_binding'
            P.only_resource_delta(P.strict_json(get(P.PRIOR/'source-manifest.json',1048576,P.PRIOR_MANIFEST_SHA)),P.strict_json(get(INSTALL/'source-manifest.json',1048576,S.MANIFEST_SHA256)))
            knowledge=P.strict_json(get(P.KNOWLEDGE,131072,P.KNOWLEDGE_SHA));P.knowledge_receipt(knowledge)
            get(P.STATE/'writer.lock',1,P.sha(b'0'),True)
            pointer=P.strict_json(get(P.STATE/'current.json',16384,knowledge['current_pointer_sha256'],True))
            if pointer.get('generation')!=knowledge['generation'] or knowledge['index_sha256']!=prior['knowledge']['index_sha256']:raise ValueError('Knowledge authority bytes differ')
            get(P.STATE/'sources.json',1048576,knowledge['source_pins_sha256'],True)
            get(P.STATE/knowledge['generation']/'knowledge_index.db',134217728,knowledge['index_sha256'],True)
            phase='docker_custody'
            stack.enter_context(P.held_read(P.DOCKER,67108864,win,P.DOCKER_SHA,retain=False));stack.enter_context(P.held_read(P.BACKEND,268435456,win,P.BACKEND_SHA,retain=False))
            report['docker_import_custody']=P.docker_import_custody(win)
            ordinary_tree(ROOT/'docker-client-config');win.validate_code_path(ROOT/'docker-client-config')
            if any((ROOT/'docker-client-config').iterdir()):raise ValueError('Diagnostic Docker config must be empty')
            S.require_stopped(win);win.validate_private_directory(P.PRIVATE)
            with production_ram_lock(win,config.ramdisk.mount_root):
                report['fresh_state_before']=P.fresh_state(progress)
                before=P.ram_observation(win,config,progress);report['ram_before']=before
                if before!=prior['ram_after']:raise ValueError('Preserved RAM/root/task baseline changed')
                # Postchecks run even when a native guard refuses the observation.
                observation_error=None
                try:
                    with trace.instrument():report['docker']=P.docker_observation(win,identities,config,docker_progress)
                except BaseException as error:
                    observation_error=error;raise
                finally:
                    failure_phase=phase
                    try:
                        after=P.ram_observation(win,config,progress);report['ram_after']=after
                        if before!=after:raise ValueError('RAM/root/task changed during diagnostic')
                        report['fresh_state_after']=P.fresh_state(progress);phase='daemon_postcheck';S.require_stopped(win)
                        report['preservation_after_observation_verified']=True
                    except BaseException as post_error:
                        report['preservation_after_observation_verified']=False
                        report['preservation_failure']={'phase':phase,'chain':error_chain(post_error)}
                        if observation_error is None:raise
                    finally:
                        if observation_error is not None:phase=failure_phase
                phase='docker_baseline'
                docker=report['docker']
                if not docker['ownership_census']['empty'] or any(docker[key]!=prior['docker'][key] for key in ('server','engine','api_aliases')):raise ValueError('Docker baseline changed; separate review required')
        report['status']='FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED'
      except BaseException as error:
        report.update(status='FOUNDATION_DIAGNOSTIC_HELD',failure={'phase':phase,'chain':error_chain(error)},operator_review_required=True)
      finally:
        report['own_fresh_docker_config_directory_used']=trace.commands_started>0
        report['docker_trace']=trace.result();report['finished_at_unix_ms']=int(time.time()*1000)
        json.dump(report,output,sort_keys=True,indent=2);output.flush();os.fsync(output.fileno())
    return 0 if report['status']=='FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED' else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--nonce',required=True);parser.add_argument('--packet-sha256',required=True);args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.nonce) or not re.fullmatch('[a-f0-9]{64}',args.packet_sha256):parser.error('Exact bounded arguments required')
    try:return run(args.nonce,args.packet_sha256)
    except BaseException as error:
        print(json.dumps({'status':'PRE_RECEIPT_FAILURE','failure':{'phase':'trusted_entry','chain':error_chain(error)}}),file=sys.stderr);return 3


if __name__=='__main__':raise SystemExit(main())
