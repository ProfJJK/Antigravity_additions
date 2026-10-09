"""One fresh SYSTEM Docker/RAM inspection; no execution-state provisioning.

Only the new helper root's receipt and isolated Docker client directory belong
to this operation. Existing private state is opened read-only with write/delete
sharing denied. No DockerRunner or KnowledgeService object is constructed.
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
import shutil
import struct
import sys
import time
from types import SimpleNamespace

ROOT=Path(r'C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3')
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PRIOR=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2')
SUPPORT_SHA='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
PRIOR_MANIFEST_SHA='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'
KNOWLEDGE=Path(r'C:\Program Files\CoChem\KnowledgePublishedVerification4.2.7-windows-20261007-r2\published-verification.json')
KNOWLEDGE_SHA='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
KNOWLEDGE_HELPER_SHA='9f3b6c00255d6e5c9bc0a6e6bec546ae5a60a8287c2b24a65a2f675f6d3e0496'
PRIVATE=Path(r'C:\ProgramData\CoChemPipeline427\private')
STATE=PRIVATE/'knowledge-windows-20261006'
DOCKER=Path(r'C:\Program Files\Docker\Docker\resources\bin\docker.exe')
DOCKER_SHA='1aaf3dd59c24ff4c83d930bc51ecac8bf6f6f4e7a310573ee69d14ca3a7ef85c'
BACKEND=Path(r'C:\Program Files\Docker\Docker\resources\com.docker.backend.exe')
BACKEND_SHA='c1214651f9de3a00c37b90139e5ac7ed57f0c686a6401e311ecace59854c070a'
IMAGE='sha256:d9d3e8644b6fc407c7d5ee7151fcb22470d74b8c14a4e6ef8cf8e9acb62877a2'
ENDPOINT='npipe:////./pipe/dockerDesktopLinuxEngine'
RAM_TASK_SHA='1da7687a165ddfb425cb01147f4f5cbd3f7ba035947063ef396e268d7babc928'
SLOTS=tuple('slot'+str(i) for i in range(1,7))


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON field')
            result[key]=value
        return result
    def constant(_):raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def pe_imports(data):
    """Bounded static imports; derived from reviewed Git custody collector."""
    if len(data)<64 or data[:2]!=b'MZ':raise ValueError('Not PE')
    pe=struct.unpack_from('<I',data,60)[0]
    if pe+24>len(data) or data[pe:pe+4]!=b'PE\0\0':raise ValueError('PE header invalid')
    machine,count=struct.unpack_from('<HH',data,pe+4);optional_size=struct.unpack_from('<H',data,pe+20)[0];optional=pe+24
    if optional+optional_size+count*40>len(data) or not 1<=count<=96:raise ValueError('PE section bound')
    magic=struct.unpack_from('<H',data,optional)[0]
    if magic not in (0x10b,0x20b):raise ValueError('PE optional header invalid')
    directory=optional+(96 if magic==0x10b else 112)
    if directory+14*8>optional+optional_size:raise ValueError('PE directory bound')
    sections=[]
    for i in range(count):
        vs,rva,size,offset=struct.unpack_from('<IIII',data,optional+optional_size+i*40+8)
        if offset+size>len(data):raise ValueError('PE raw section bound')
        sections.append((rva,size,offset))
    def offset(rva):
        matches=[raw+rva-start for start,size,raw in sections if start<=rva<start+size]
        if len(matches)==1:return matches[0]
        if not matches and 0<=rva<optional+optional_size:return rva
        raise ValueError('Unknown or ambiguous PE RVA')
    def name(rva):
        start=offset(rva);end=data.find(b'\0',start,start+256)
        if end<0:raise ValueError('PE name exceeds bound')
        value=data[start:end].decode('ascii').lower()
        if not re.fullmatch('[a-z0-9_.-]+\\.dll',value):raise ValueError('PE import is not a DLL basename')
        return value
    results=[]
    for number,size,fmt,name_index in ((1,20,'<IIIII',3),(13,32,'<IIIIIIII',1)):
        rva,length=struct.unpack_from('<II',data,directory+number*8);found=[]
        if rva:
            start=offset(rva)
            for i in range(min(length//size+1,256)):
                if start+(i+1)*size>len(data):raise ValueError('PE import descriptor bound')
                entry=struct.unpack_from(fmt,data,start+i*size)
                if not any(entry):break
                if number==13 and entry[0]!=1:raise ValueError('Unsupported delay import mode')
                found.append(name(entry[name_index]))
            else:raise ValueError('PE imports exceed bound')
        results.append(sorted(set(found)))
    return {'machine':hex(machine),'imports':results[0],'delay_imports':results[1]}


def same_fields(value,expected):
    if not isinstance(value,dict) or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in expected.items()):
        raise ValueError('Required evidence fields differ')


@contextmanager
def held_read(path,maximum,win,expected=None,private=False,retain=True):
    """OPEN_EXISTING, read-only, single-link; deny simultaneous writes/deletes."""
    import msvcrt
    from cochem_pipeline.ramdisk import ordinary_tree
    ordinary_tree(path)
    (win.validate_private_path if private else win.validate_code_path)(path)
    before=path.lstat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink,getattr(s,'st_file_attributes',0))
    if not path.is_file() or before.st_nlink!=1 or not 0<=before.st_size<=maximum:raise ValueError('File bound differs')
    handle=win._api()['kernel32'].CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:win._close(handle);raise
    with os.fdopen(descriptor,'rb') as stream:
        if identity(os.fstat(stream.fileno()))!=identity(before):raise ValueError('Held identity differs')
        digest=hashlib.sha256();count=0;chunks=[]
        while chunk:=stream.read(min(1048576,maximum-count+1)):
            count+=len(chunk)
            if count>maximum:raise ValueError('Held bytes exceed bound')
            digest.update(chunk)
            if retain:chunks.append(chunk)
        raw=b''.join(chunks) if retain else None
        if expected is not None and digest.hexdigest()!=expected:raise ValueError('Held bytes differ')
        if identity(path.lstat())!=identity(before):raise ValueError('Path changed during read')
        yield raw
        if identity(os.fstat(stream.fileno()))!=identity(before) or identity(path.lstat())!=identity(before):
            raise ValueError('Held file changed')


def only_resource_delta(old,new):
    def rows(value):
        values=value.get('files')
        if not isinstance(values,list) or len(values)!=166:raise ValueError('Manifest size differs')
        result={}
        for row in values:
            key=row['relative']
            if key.casefold() in result:raise ValueError('Duplicate manifest path')
            result[key.casefold()]=(row['sha256'],row['length'])
        return result
    a,b=rows(old),rows(new)
    if set(a)!=set(b) or [key for key in a if a[key]!=b[key]]!=['src/cochem_pipeline/resource_limits.py']:
        raise ValueError('r2 knowledge authority is not unchanged in r3')


def worker_receipt(value,slot,install_sha,revision,sid):
    same_fields(value,{'schema':'cochem-worker-denial-acceptance/1','status':'HANDLE_DENIALS_VERIFIED',
        'slot':slot,'system_sid':'S-1-5-18','helper_sha256':SUPPORT_SHA,'runtime_root':str(INSTALL),
        'install_receipt_sha256':install_sha,'cleanup_verified':True,'worker_released':True,
        'daemon_states_verified_before_and_after':True,'ioctls_sent':0,'tested_target_contents_read':0,
        'pipeline_started':False,'docker_denial_tested':False,'repair_identity_tested':False})
    if value.get('revision')!=revision or not re.fullmatch('[a-f0-9]{32}',str(value.get('nonce',''))):raise ValueError('Worker revision or nonce differs')
    process=value['process']
    same_fields(process,{'token_sid':sid,'token_matches_selected_worker':True,'image_matches_reviewed_executable':True,'owned_job_membership_verified':True})
    same_fields(process['token_details'],{'sid':sid,'elevated':False,'administrators_enabled':False})


def knowledge_receipt(value):
    same_fields(value,{'schema':'cochem-published-knowledge-verification/1','status':'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':KNOWLEDGE_HELPER_SHA,'runtime_root':str(PRIOR),
        'corpus_files':138,'corpus_bytes_preserved':True,'original_root_identity_and_security_preserved':True,
        'original_writer_lock_preserved':True,'index_size_sla_met':True,'knowledge_service_constructed':False,
        'index_refreshed_or_repaired':False,'existing_files_or_acls_modified':False,'activation_ready':False})
    if not re.fullmatch('g-[a-f0-9]{32}',str(value.get('generation',''))):raise ValueError('Knowledge generation differs')
    for key in ('index_sha256','current_pointer_sha256','source_pins_sha256'):
        if not re.fullmatch('[a-f0-9]{64}',str(value.get(key,''))):raise ValueError('Knowledge commitment missing')


def require_absent(path):
    from cochem_pipeline.ramdisk import ordinary_tree
    ordinary_tree(path,allow_missing=True)
    try:path.lstat()
    except FileNotFoundError:return
    raise ValueError('Proposed new execution state already exists')


def fresh_state(progress=lambda value:None):
    paths=(Path('R:/CoChem427-windows-20261007'),PRIVATE/'ramdisk-state.json',PRIVATE/'containers',PRIVATE/'execution-readiness.json')
    for code,path in zip(('ram_workspace_absence','ram_ledger_absence','container_registry_absence','readiness_receipt_absence'),paths):
        progress(code);require_absent(path)
    return {'ram_workspace_absent':True,'ram_ledger_absent':True,'container_registry_root_absent':True,'readiness_receipt_absent':True}


def ram_observation(win,config,progress=lambda value:None):
    from cochem_pipeline.ramdisk import _native_volume,_validate_adopted_parent
    if not config.ramdisk.adopted_drive or str(config.ramdisk.workspace_root)!=r'R:\CoChem427-windows-20261007':raise ValueError('Scoped adopted RAM policy differs')
    progress('ram_root_acl');_validate_adopted_parent(Path('R:/'))
    progress('ram_startup_task')
    task=strict_json(win._powershell(r"""
      $task=Get-ScheduledTask -TaskName 'Mount_CoChem_RAMDisk' -TaskPath '\' -ErrorAction Stop
      $actions=@($task.Actions);$triggers=@($task.Triggers)
      if($task.Principal.UserId -notin @('SYSTEM','S-1-5-18','NT AUTHORITY\SYSTEM') -or -not $task.Settings.Enabled -or $actions.Count -ne 1 -or $triggers.Count -ne 1 -or $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskBootTrigger' -or -not $triggers[0].Enabled){throw 'RAM task changed.'}
      if($actions[0].Execute -cne 'imdisk.exe' -or $actions[0].Arguments -cne '-a -s 8G -m R: -p "/fs:ntfs /q /y"' -or $actions[0].WorkingDirectory){throw 'RAM action changed.'}
      $xml=Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
      [byte[]]$bytes=[Text.Encoding]::Unicode.GetPreamble()+[Text.Encoding]::Unicode.GetBytes($xml)
      $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
      @{task_xml_sha256=$hash;root_sddl=(Get-Acl -LiteralPath 'R:\').Sddl}|ConvertTo-Json -Compress
    """))
    if task['task_xml_sha256']!=RAM_TASK_SHA:raise ValueError('Existing owner startup task hash changed')
    progress('ram_device_query');volume=_native_volume(config.ramdisk)
    if volume['size_bytes']!=8589934592:raise ValueError('Existing RAM device size differs')
    progress('ram_filesystem');st=Path('R:/').stat();usage=shutil.disk_usage('R:/')
    if usage.total!=8589930496:raise ValueError('Existing NTFS filesystem size differs')
    return {'task_xml_sha256':task['task_xml_sha256'],'root_security_sha256':sha(task['root_sddl'].encode('utf-8')),
        'root_file_id':st.st_ino,'root_device':st.st_dev,'root_attributes':st.st_file_attributes,
        'filesystem_bytes':usage.total,'volume':volume,'imdisk_query_ioctl_used':True,'ram_modified':False}


def daemon_info(value):
    if not isinstance(value,dict) or value.get('OSType')!='linux' or not isinstance(value.get('ID'),str) or not 1<=len(value['ID'])<=256:raise ValueError('Linux daemon identity unavailable')
    required=('MemoryLimit','SwapLimit','CpuCfsQuota','PidsLimit')
    if any(value.get(key) is not True for key in required):raise ValueError('Docker enforcement capability incomplete')
    options=value.get('SecurityOptions')
    if not isinstance(options,list) or not any(isinstance(v,str) and 'seccomp' in v for v in options):raise ValueError('Seccomp unavailable')
    return {'daemon_id_sha256':sha(value['ID'].encode()),'linux':True,'resource_limits_supported':{key:True for key in required},'seccomp_available':True}


def image_info(values):
    if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict):raise ValueError('Image inspection shape differs')
    value=values[0]
    if value.get('Id')!=IMAGE or value.get('Os')!='linux':raise ValueError('Exact local Linux image missing')
    config=value.get('Config')
    if not isinstance(config,dict) or config.get('Volumes'):raise ValueError('Image volumes forbidden')
    env=config.get('Env',[])
    if not isinstance(env,list) or len(env)>1024 or any(not isinstance(v,str) or any(marker in v.split('=',1)[0].upper() for marker in ('API_KEY','TOKEN','PASSWORD','CREDENTIAL','SECRET')) for v in env):raise ValueError('Image environment policy differs')
    return {'image_id':IMAGE,'linux':True,'image_declared_volumes':False,'credential_named_environment_absent':True}


def census_ids(raw):
    if not isinstance(raw,bytes) or len(raw)>65536:raise ValueError('Ownership census exceeds bound')
    values=raw.decode('ascii').splitlines()
    if len(values)>256 or len(set(values))!=len(values) or any(not re.fullmatch('[a-f0-9]{64}',v) for v in values):raise ValueError('Ownership census identifiers invalid')
    return set(values)


def server_identity(value):
    return {key:value[key] for key in ('pid','process_created_filetime','token_sid','executable')}


def docker_import_custody(win):
    """Exact Go CLI has only a KnownDLL static import; backend is not launched."""
    import winreg
    with DOCKER.open('rb') as stream:raw=stream.read(67108865)
    if len(raw)>67108864 or sha(raw)!=DOCKER_SHA:raise ValueError('CLI changed before dependency inspection')
    imports=pe_imports(raw)
    if imports!={'machine':'0x8664','imports':['kernel32.dll'],'delay_imports':[]}:raise ValueError('Exact CLI import closure differs')
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs',0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
        # This host's actual registry value name includes the literal asterisk.
        name,kind=winreg.QueryValueEx(key,'*kernel32')
    if kind!=winreg.REG_SZ or name.casefold()!='kernel32.dll':raise ValueError('Kernel32 KnownDLL registration differs')
    kernel=Path(os.environ['SystemRoot'])/'System32/kernel32.dll'
    from cochem_pipeline.ramdisk import ordinary_tree
    ordinary_tree(kernel);win.validate_code_path(kernel)
    return {'cli_declared_imports':imports,'kernel32_known_dll_registration_verified':True,
        'system32_kernel32_protected_path_verified':True,'backend_launched':False,
        'unrelated_plugin_binaries_in_execution_scope':False}


def docker_observation(win,identities,config,progress=lambda value:None):
    from cochem_pipeline.deployment import attest_docker_pipe_server,verify_docker_access_boundary
    from cochem_pipeline.containers import _bounded_process
    policy=config.docker
    if (policy.executable!=str(DOCKER) or policy.endpoint!=ENDPOINT or policy.image!=IMAGE
        or tuple(policy.pipe_server_executables)!=(str(BACKEND),) or policy.max_containers!=4 or policy.warm_pool_size!=2):raise ValueError('Reviewed Docker policy differs')
    kwargs={'trusted_operator':config.operator_name,'trusted_server_executables':policy.pipe_server_executables}
    progress('docker_pipe_denials_before');before=verify_docker_access_boundary(ENDPOINT,identities,required=True,**kwargs)
    for rows in before.values():
        if len(rows)!=6 or {r['slot'] for r in rows}!=set(SLOTS) or any(r.get('access_denied') is not True or r.get('denied_modes')!=['write','read','read_write'] for r in rows):raise ValueError('Six worker denial modes missing')
    baseline=server_identity(before[ENDPOINT][0]['server'])
    allowed=[['info','--format','{{json .}}'],['image','inspect',IMAGE],
        ['ps','--all','--no-trunc','--filter','label=org.cochem.owner','--format','{{.ID}}'],
        ['ps','--all','--no-trunc','--filter','name=^/cochem-','--format','{{.ID}}']]
    def call(arguments):
        if arguments not in allowed:raise ValueError('Read-only Docker command allowlist rejected arguments')
        if server_identity(attest_docker_pipe_server(ENDPOINT,identities,**kwargs))!=baseline:raise ValueError('Selected Docker server changed')
        env={key:os.environ[key] for key in ('SystemRoot','WINDIR') if key in os.environ}
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        result=_bounded_process([str(DOCKER),'--config',str(ROOT/'docker-client-config'),'--host',ENDPOINT,*arguments],env=env,timeout=20,output_limit=1048576)
        if result.returncode or result.timed_out or result.cancelled or result.output_exceeded or result.stderr:raise ValueError('Bounded Docker inspection failed')
        if server_identity(attest_docker_pipe_server(ENDPOINT,identities,**kwargs))!=baseline:raise ValueError('Selected Docker server changed')
        return result.stdout
    progress('docker_info');info=daemon_info(strict_json(call(allowed[0])))
    progress('docker_image');image=image_info(strict_json(call(allowed[1])))
    progress('docker_owner_census');labels=census_ids(call(allowed[2]))
    progress('docker_name_census');names=census_ids(call(allowed[3]))
    progress('docker_pipe_denials_after');after=verify_docker_access_boundary(ENDPOINT,identities,required=True,**kwargs)
    normalized=lambda values:{key:[{**r,'server':server_identity(r['server'])} for r in rows] for key,rows in values.items()}
    if normalized(before)!=normalized(after):raise ValueError('Docker aliases, token denials or servers changed')
    result={'engine':info,'image':image,'api_aliases':sorted(before),'worker_access_denied_before_and_after':True,
        'workers_per_alias':6,'denied_modes':['write','read','read_write'],'server':baseline,
        'backend_sha256':BACKEND_SHA,'cli_sha256':DOCKER_SHA,'commands_executed':4,
        'ownership_census':{'owner_label_count':len(labels),'name_prefix_count':len(names),'union_count':len(labels|names),
            'identifiers_sha256':sha('\n'.join(sorted(labels|names)).encode()),'empty':not(labels|names)},
        'raw_info_environment_labels_published':False,'containers_created_or_modified':False}
    return result


def safe_failure(error,phase):
    code=getattr(error,'winerror',None)
    return {'phase':phase,'error_type':re.sub('[^A-Za-z0-9_]','',type(error).__name__)[:80],
        'winerror':code if type(code) is int and 0<=code<=0xffffffff else None}


def run(nonce,packet_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    if Path(__file__).resolve()!=ROOT/'inspect-execution-prerequisites-r3.py' or Path(sys.executable).resolve()!=INSTALL/'.venv/Scripts/python.exe':raise ValueError('Protected preflight entrypoint differs')
    for path in (ROOT,Path(__file__),Path(sys.executable)):
        ordinary_tree(path);win.validate_code_path(path)
    report={'schema':'cochem-execution-prerequisites/1','nonce':nonce,'system_sid':win.SYSTEM_SID,'status':'UNVERIFIED',
        'helper_sha256':sha(Path(__file__).read_bytes()),'packet_sha256':packet_sha,'runtime_root':str(INSTALL),
        'started_at_unix_ms':int(time.time()*1000),'activation_ready':False,'native_model_jobs_executed':0,
        'knowledge_service_constructed':False,'docker_runner_constructed':False,'ram_ensure_called':False,
        'existing_databases_modified':False,'existing_files_or_acls_modified':False,'existing_tasks_modified_or_run':False,
        'own_fresh_receipt_created':True,'own_fresh_docker_config_directory_used':True,
        'windows_worker_tokens_used_for_handle_probes':True,'native_provider_authentication_performed':False}
    with (ROOT/'execution-prerequisites.json').open('x',encoding='utf-8') as output:
      phase='runtime'
      def progress(value):
        nonlocal phase
        phase=value
      try:
        with ExitStack() as stack:
            get=lambda p,m=1048576,h=None,private=False:stack.enter_context(held_read(p,m,win,h,private))
            support_raw=get(ROOT/'worker-denial-acceptance-r3.py',1048576,SUPPORT_SHA)
            namespace={'__name__':'preflight_support','__file__':str(ROOT/'worker-denial-acceptance-r3.py')}
            exec(compile(support_raw,str(ROOT/'worker-denial-acceptance-r3.py'),'exec'),namespace);S=SimpleNamespace(**namespace)
            get(S.BASE_PYTHON,1048576,S.BASE_SHA256)
            packet=strict_json(get(ROOT/'inputs.json',32768,packet_sha))
            same_fields(packet,{'schema':'cochem-execution-prerequisites-inputs/1','knowledge_receipt_sha256':KNOWLEDGE_SHA})
            revision=S.verify_r3_runtime(win,packet['install_receipt_sha256']);report.update(install_receipt_sha256=packet['install_receipt_sha256'],revision=revision)
            config=load_config(str(INSTALL/'pipeline.json'));layout,_=S.protected_json(win,INSTALL/'windows-layout.json')
            raw_config,config_hash=S.protected_json(win,INSTALL/'pipeline.json')
            if config_hash!=S.CONFIG_SHA256:raise ValueError('Configuration changed')
            S.validate_layout(layout,raw_config)
            identities={key:win.WorkerIdentity(row['name'],row['credential_target']) for key,row in raw_config['workers'].items()}
            if set(packet['workers'])!=set(SLOTS):raise ValueError('All six worker receipts required')
            phase='worker_evidence'
            for slot in SLOTS:
                if win._sid_text(win._account_sid(identities[slot].name))!=layout['slots'][slot]['sid']:raise ValueError('Worker SID changed')
                path=Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json'
                value=strict_json(get(path,32768,packet['workers'][slot]))
                worker_receipt(value,slot,packet['install_receipt_sha256'],revision,layout['slots'][slot]['sid'])
            report['worker_receipt_sha256']=packet['workers']
            phase='knowledge_binding'
            only_resource_delta(strict_json(get(PRIOR/'source-manifest.json',1048576,PRIOR_MANIFEST_SHA)),strict_json(get(INSTALL/'source-manifest.json',1048576,S.MANIFEST_SHA256)))
            knowledge=strict_json(get(KNOWLEDGE,131072,KNOWLEDGE_SHA));knowledge_receipt(knowledge)
            get(STATE/'writer.lock',1,sha(b'0'),True)
            pointer=strict_json(get(STATE/'current.json',16384,knowledge['current_pointer_sha256'],True))
            if pointer.get('generation')!=knowledge['generation']:raise ValueError('Knowledge pointer changed')
            get(STATE/'sources.json',1048576,knowledge['source_pins_sha256'],True)
            index=get(STATE/knowledge['generation']/'knowledge_index.db',134217728,knowledge['index_sha256'],True)
            if len(index)!=knowledge['index_bytes']:raise ValueError('Knowledge index size changed')
            report['knowledge']={'receipt_sha256':KNOWLEDGE_SHA,'index_sha256':knowledge['index_sha256'],
                'generation':knowledge['generation'],'current_bytes_unchanged':True,'r2_to_r3_only_resource_limits_delta_verified':True}
            phase='preconditions'
            S.require_stopped(win);win.validate_private_directory(PRIVATE)
            report['fresh_state_before']=fresh_state(progress)
            before=ram_observation(win,config,progress);report['ram_before']=before
            phase='docker_custody'
            stack.enter_context(held_read(DOCKER,67108864,win,DOCKER_SHA,retain=False))
            stack.enter_context(held_read(BACKEND,268435456,win,BACKEND_SHA,retain=False))
            phase='docker_import_custody';report['docker_import_custody']=docker_import_custody(win)
            ordinary_tree(ROOT/'docker-client-config');win.validate_code_path(ROOT/'docker-client-config')
            if any((ROOT/'docker-client-config').iterdir()):raise ValueError('Fresh isolated Docker config is not empty')
            phase='docker_inspection'
            report['docker']=docker_observation(win,identities,config,progress)
            phase='preservation'
            after=ram_observation(win,config,progress)
            if before!=after:raise ValueError('Existing RAM root/device/task changed')
            report['ram_after']=after;report['fresh_state_after']=fresh_state(progress);phase='daemon_postcheck';S.require_stopped(win)
            report['holds']=[] if report['docker']['ownership_census']['empty'] else ['existing_cochem_containers_require_separate_ledger_review']
        report['status']='READ_ONLY_PREREQUISITES_VERIFIED' if not report['holds'] else 'PREREQUISITES_HELD'
      except BaseException as error:
        report.update(status='PREREQUISITES_HELD',failure=safe_failure(error,phase),operator_review_required=True)
      finally:
        report['finished_at_unix_ms']=int(time.time()*1000)
        json.dump(report,output,sort_keys=True,indent=2);output.flush();os.fsync(output.fileno())
    return 0 if report['status']=='READ_ONLY_PREREQUISITES_VERIFIED' else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--nonce',required=True);parser.add_argument('--packet-sha256',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.nonce) or not re.fullmatch('[a-f0-9]{64}',args.packet_sha256):parser.error('Bounded reviewed arguments required')
    try:return run(args.nonce,args.packet_sha256)
    except BaseException as error:
        print(json.dumps({'status':'PRE_RECEIPT_FAILURE','failure':safe_failure(error,'trusted_entry')}),file=sys.stderr);return 3


if __name__=='__main__':raise SystemExit(main())
