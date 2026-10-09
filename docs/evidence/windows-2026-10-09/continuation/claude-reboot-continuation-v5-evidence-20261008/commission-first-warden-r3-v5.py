"""One-shot SYSTEM first start. No login, model submission, retry or repair.

The reviewed administrator wrapper creates the exact disabled daemon task and
this fresh root. All pre-existing state is checked before one durable intent
permits Enable/Run. A failed/ambiguous start is preserved for inspection.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack, contextmanager
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import shutil
import sqlite3
import sys
import time
import types

INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
ROOT = Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1')
PRIVATE = Path(r'C:\ProgramData\CoChemPipeline427\private')
QUEUE = PRIVATE / 'queue-commissioning-20261007-r3-v1'
PYTHON = INSTALL / '.venv/Scripts/python.exe'
BASE_PYTHON = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
TASK = 'CoChem-4.2.7-Warden'
SUPPORT_HASH = 'c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'
AUTH_HELPER_HASH = '7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1'
INSTALL_HASH = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
CONFIG_HASH = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
LAYOUT_HASH = '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
MANIFEST_HASH = '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
REVISION_HASH = '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
AUTH_PREFIX = 'NativeAuthBoth4.2.7-windows-20261008-r3-v3-'
PRIORS = {
 'knowledge': (ROOT.parent/'KnowledgePublishedVerification4.2.7-windows-20261007-r2/published-verification.json', 'f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'),
 'foundation': (ROOT.parent/'ExecutionFoundation4.2.7-windows-20261007-r3-v2/execution-foundation.json','9aea1e8f382ea8600cbd75d679f76acadc05da32774d9d8742cad08967ce1b41'),
 'docker': (ROOT.parent/'DockerExecutionAcceptance4.2.7-windows-20261007-r3-v2/docker-physical-acceptance.json','2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74'),
 'project': (ROOT.parent/'ProjectImport4.2.7-windows-20261007-r3/project-import.json','9e31fba6cbacb975970653a9230d9093afa0adc6993fbe6b8c98805653a26283'),
}
FIXTURE_FILES = {
 'red/tests/business.py':('3096bff79b2361839831bf6fb0545a5fee4a37301a9c2f52751c45a04f7d130f',24),
 'red/tests/test_contract.py':('10e85eb3add92785024d8b5c7454eb7b4e951d03525c6aa29c673878693b554b',1672),
 'green/tests/business.py':('10a1a999ec16401c757b5e1bf2828e1735ef775c5baa9355c70f2848ea375ef0',24),
 'green/tests/test_contract.py':('10e85eb3add92785024d8b5c7454eb7b4e951d03525c6aa29c673878693b554b',1672),
}
FIXTURE_NAME='execution-acceptance-r3-v2'
PRESERVATION_ROOT=PRIVATE/'first-start-preservation-20261007-r3-v1'

def require(condition, code):
    if not condition: raise ValueError(code)

def exact(value, fields, code):
    require(isinstance(value,dict) and all(type(value.get(k)) is type(v) and value[k]==v for k,v in fields.items()),code)

def load_support(path):
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==SUPPORT_HASH,'SUPPORT_PIN')
    module=types.ModuleType('commission_reviewed_support');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module

def read_control(win, support, path, pin=None, maximum=1048576):
    from cochem_pipeline.ramdisk import ordinary_tree
    win.validate_code_path(path);ordinary_tree(path)
    before=path.stat();require(before.st_nlink==1 and before.st_size<=maximum,'CONTROL_BOUNDS')
    with path.open('rb') as stream: raw=stream.read(maximum+1)
    after=path.stat()
    require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) and len(raw)<=maximum,'CONTROL_DRIFT')
    digest=hashlib.sha256(raw).hexdigest();require(pin is None or digest==pin,'CONTROL_PIN')
    value=support.strict_json(raw.decode('utf-8-sig'));require(isinstance(value,dict),'CONTROL_SHAPE')
    return value,digest

def validate_auth(read, attempt):
    require(type(attempt) is str and re.fullmatch('[a-f0-9]{32}',attempt) and attempt!='0'*32,'AUTH_ATTEMPT')
    auth_root=ROOT.parent/(AUTH_PREFIX+attempt)
    outer,outer_hash=read(auth_root/'series-complete.json',None)
    exact(outer,{'schema':'cochem-both-providers-status-first-result/1','status':'CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED',
          'attempt':attempt,'model_jobs_executed':0,'configuration_changed':False,'activation_ready':False,'agy_integration_hold_preserved':True,'series_preserved':True},'AUTH_OUTER')
    require(re.fullmatch('[a-f0-9]{32}',str(outer.get('nonce'))) and len(outer.get('provider_results',[]))==2,'AUTH_OUTER_SHAPE')
    seen=set();proofs=[]
    for row in outer['provider_results']:
        provider=row.get('provider');require(provider in ('codex','claude') and provider not in seen,'AUTH_PROVIDER_DUPLICATE');seen.add(provider)
        path=ROOT.parent/f'NativeAuthSix4.2.7-windows-20261008-r3-v3-{attempt}-{provider}'/'series-complete.json'
        require(row.get('receipt_path')==str(path) and re.fullmatch('[a-f0-9]{64}',str(row.get('receipt_sha256'))),'AUTH_PROVIDER_PATH')
        value,digest=read(path,row['receipt_sha256'])
        exact(value,{'schema':'cochem-six-worker-status-first-result/1','status':'SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED','provider':provider,
                     'attempt':attempt,'selected_workers_verified':6,'configuration_applied':False,'model_jobs_executed':0,'activation_ready':False,'series_preserved':True},'AUTH_PROVIDER')
        runtime=value.get('runtime',{});exact(runtime,{'install_receipt_sha256':INSTALL_HASH,'configuration_sha256':CONFIG_HASH},'AUTH_RUNTIME')
        require(runtime.get('revision',{}).get('source_sha256')==REVISION_HASH,'AUTH_REVISION')
        counts=[value.get(k) for k in ('login_commands_executed','existing_sessions_reused')]
        require(all(type(n)is int and 0<=n<=6 for n in counts) and sum(counts)==6,'AUTH_COUNTS')
        require(len(value.get('status_receipts',[]))==6,'AUTH_SLOTS');slots=set()
        for result in value['status_receipts']:
            slot=result.get('slot');require(slot in [f'slot{i}' for i in range(1,7)] and slot not in slots,'AUTH_SLOT');slots.add(slot)
            stage={'REUSED_VERIFIED_SESSION':'before','AUTHENTICATED_AFTER_LOGIN':'after'}.get(result.get('status'))
            proof=result.get('proof',{});require(stage is not None and proof.get('stage')==stage and proof.get('decision')=='REUSE_VERIFIED_SESSION','AUTH_STAGE')
            leaf_path=ROOT.parent/f'NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-{attempt}-{slot}-{provider}-{stage}'/'worker-native-status.json'
            require(proof.get('receipt_path')==str(leaf_path) and re.fullmatch('[a-f0-9]{64}',str(proof.get('receipt_sha256'))),'AUTH_LEAF_PATH')
            leaf,leaf_hash=read(leaf_path,proof['receipt_sha256'])
            exact(leaf,{'schema':'cochem-worker-native-auth-status/1','status':'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED','decision':'REUSE_VERIFIED_SESSION',
               'attempt':attempt,'provider':provider,'slot':slot,'stage':stage,'system_sid':'S-1-5-18','helper_sha256':AUTH_HELPER_HASH,'runtime_root':str(INSTALL),
               'install_receipt_sha256':INSTALL_HASH,'source_manifest_sha256':MANIFEST_HASH,'config_sha256':CONFIG_HASH,'layout_sha256':LAYOUT_HASH,
               'cleanup_verified':True,'runtime_custody_verified':True,'daemon_states_verified_before_and_after':True,'native_status_commands_executed':1,
               'native_model_jobs_executed':0,'login_commands_executed':0,'native_exit_code':0},'AUTH_LEAF')
            require(leaf.get('revision',{}).get('source_sha256')==REVISION_HASH and leaf.get('authentication',{}).get('native_subscription_authentication_verified') is True,'AUTH_ATTESTATION')
            process=leaf.get('process',{});require(all(process.get(k) is True for k in ('token_matches_selected_worker','image_matches_reviewed_executable','owned_job_membership_verified')),'AUTH_PROCESS')
            proofs.append({'provider':provider,'slot':slot,'receipt_sha256':leaf_hash})
    return {'receipt_path':str(auth_root/'series-complete.json'),'receipt_sha256':outer_hash,'profiles':proofs}

def absent(path):
    try:path.lstat()
    except FileNotFoundError:return
    raise ValueError('EXISTING_FIRST_START_STATE')

def empty_directory(path, ordinary):
    ordinary(path);require(path.is_dir(),'SCRATCH_NOT_DIRECTORY')
    with os.scandir(path) as entries:require(next(entries,None) is None,'NONEMPTY_SCRATCH_PRESERVED')
    stat=path.stat();return {'path':str(path),'device':stat.st_dev,'file_id':stat.st_ino,'creation_ns':stat.st_ctime_ns}

def fixture_shape(root, ordinary):
    expected={'':{'red','green'},'red':{'tests'},'green':{'tests'},'red/tests':{'business.py','test_contract.py'},'green/tests':{'business.py','test_contract.py'}}
    for relative,names in expected.items():
        path=root/relative;ordinary(path);require(path.is_dir(),'FIXTURE_DIRECTORY')
        with os.scandir(path) as entries:require({e.name for e in entries}==names,'FIXTURE_UNEXPECTED_ENTRY')
    for relative,(pin,length) in FIXTURE_FILES.items():
        path=root/relative;ordinary(path);st=path.stat()
        require(path.is_file() and st.st_nlink==1 and st.st_size==length,'FIXTURE_FILE_METADATA')

def fixture_manifest_match(receipt):
    for phase in ('red','green'):
        entries={key[len(phase)+1:]:{'sha256':pin,'size':length,'executable':False} for key,(pin,length) in FIXTURE_FILES.items() if key.startswith(phase+'/')}
        digest=hashlib.sha256(json.dumps(entries,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        rows=[row for row in receipt.get('executions',[]) if row.get('phase')==phase]
        require(len(rows)==1 and rows[0].get('source_sha256')==digest and rows[0].get('input_and_output_source_verified') is True,'FIXTURE_RECEIPT_SOURCE')

def preserve_fixture(root,destination,receipt,win,ordinary):
    """Copy four exact prior test sources into private retention; never delete.

    This is the sole permitted nonempty scratch subtree. The daemon may perform
    its normal cleanup only after private CreateNew copies and manifest verify.
    Unexpected files or partial preservation stop first commissioning.
    """
    import msvcrt
    fixture_manifest_match(receipt);fixture_shape(root,ordinary);absent(destination)
    sid=win._sid_text(win._account_sid('CoChem422Worker1'))
    for relative in ('','red','green','red/tests','green/tests',*FIXTURE_FILES):win._validate_worker_directory(root/relative,sid,protected=not relative)
    with ExitStack() as stack:
        captured={}
        for relative,(pin,length) in FIXTURE_FILES.items():
            path=root/relative;before=path.stat()
            handle=win._api()['kernel32'].CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
            require(handle not in (0,None,C.c_void_p(-1).value),'FIXTURE_READ_HANDLE')
            try:fd=msvcrt.open_osfhandle(int(handle),os.O_RDONLY|os.O_BINARY)
            except BaseException:win._close(handle);raise
            stream=stack.enter_context(os.fdopen(fd,'rb'));raw=stream.read(length+1);after=path.stat()
            require(len(raw)==length and hashlib.sha256(raw).hexdigest()==pin and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'FIXTURE_SOURCE_DRIFT')
            captured[relative]=(raw,{'file_id':before.st_ino,'device':before.st_dev,'mtime_ns':before.st_mtime_ns,'sha256':pin,'bytes':length})
        win.validate_private_directory(destination.parent)
        destination.mkdir();win.validate_private_directory(destination)
        for relative in ('red','green','red/tests','green/tests'):
            (destination/relative).mkdir();win.validate_private_directory(destination/relative)
        for relative,(raw,metadata) in captured.items():
            path=destination/relative
            with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            win.validate_private_path(path);ordinary(path)
            require(path.stat().st_nlink==1 and path.read_bytes()==raw,'FIXTURE_PRESERVATION_READBACK')
        fixture_shape(root,ordinary)
        manifest={'schema':'cochem-first-start-scratch-preservation/1','source_root':str(root),'destination_root':str(destination),
          'physical_receipt_sha256':PRIORS['docker'][1],'files':{k:v[1] for k,v in captured.items()},'file_count':4,'bytes':3392,'source_deleted_by_helper':False}
        digest=write_new(destination/'preservation.json',manifest)
        win.validate_private_path(destination/'preservation.json')
    return {'source_root':str(root),'destination_root':str(destination),'manifest_sha256':digest,'files':4,'bytes':3392,'source_deleted_by_helper':False,'normal_runtime_scratch_cleanup_permitted':True}

def inspect_registry_buffer(raw, owner_hash):
    require(len(raw)<=16777216 and raw[:16]==b'SQLite format 3\x00','REGISTRY_BUFFER')
    # Deserialize only the captured database bytes; no source database is opened
    # by SQLite. WAL header normalization is confined to this memory copy.
    captured=bytearray(raw);captured[18:20]=b'\x01\x01'
    with sqlite3.connect(':memory:') as db:
        db.deserialize(bytes(captured));db.execute('PRAGMA query_only=ON');db.execute('PRAGMA temp_store=MEMORY')
        require(db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'REGISTRY_INTEGRITY')
        metadata=dict(db.execute('SELECT key,value FROM metadata'))
        require(metadata.get('capacity')=='4' and metadata.get('unknown_owned')=='0' and hashlib.sha256(metadata.get('owner','').encode()).hexdigest()==owner_hash,'REGISTRY_AUTHORITY')
        rows=db.execute('SELECT status,creation_uncertain FROM containers').fetchall()
        require(len(rows)==2 and all(row==('REMOVED',0) for row in rows),'REGISTRY_NOT_CLOSED')
        require(db.execute('SELECT COUNT(*) FROM preparation_requests').fetchone()==(0,),'REGISTRY_PREPARATIONS')
    return {'sha256':hashlib.sha256(raw).hexdigest(),'retained_removed_rows':2,'capacity':4,'unknown_owned':0,'inspection_only':True}

def registry_preflight(config,win,foundation):
    import msvcrt
    from cochem_pipeline.ramdisk import ordinary_tree
    path=config.private_root/'containers/containers.db'
    for suffix in ('-wal','-shm','-journal'):absent(Path(str(path)+suffix))
    ordinary_tree(path);win.validate_private_path(path);before=path.stat()
    require(before.st_nlink==1 and 0<before.st_size<=16777216,'REGISTRY_BOUNDS')
    handle=win._api()['kernel32'].CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    require(handle not in (0,None,C.c_void_p(-1).value),'REGISTRY_READ_HANDLE')
    try:
        fd=msvcrt.open_osfhandle(int(handle),os.O_RDONLY|os.O_BINARY);handle=None
        with os.fdopen(fd,'rb') as stream:
            raw=stream.read(16777217);result=inspect_registry_buffer(raw,foundation['registry']['owner_sha256'])
            after=path.stat();require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'REGISTRY_DRIFT')
            for suffix in ('-wal','-shm','-journal'):absent(Path(str(path)+suffix))
            return result
    finally:win._close(handle)

@contextmanager
def reserve_workers(win,workers):
    """Hold production named-object exclusion; never start a worker here."""
    handles=[]
    try:
        require(set(workers)=={f'slot{i}' for i in range(1,7)},'WORKER_EXCLUSION_SLOTS')
        for slot in (f'slot{i}' for i in range(1,7)):
            sid=win._sid_text(win._account_sid(workers[slot]['name']))
            C.set_last_error(0)
            handle=win._check(win._api()['kernel32'].CreateMutexW(None,False,'Global\\CoChemPipeline422-'+sid),'Reserve first-start worker exclusion')
            existed=C.get_last_error()==183;handles.append(handle)
            require(not existed,'WORKER_ALREADY_ACTIVE')
        yield
    finally:
        for handle in reversed(handles):win._close(handle)

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

RAM_TASK_SHA='1da7687a165ddfb425cb01147f4f5cbd3f7ba035947063ef396e268d7babc928'
RAM_WORKSPACE_ROOT=Path(r'R:\CoChem427-windows-20261007')
RAM_MOUNT_ROOT=str(Path('R:/'))
RAM_RECOVERY_BACKUP='ramdisk-state-before-recovery.json'
RAM_RECOVERY_MANIFEST='ram-recovery-preservation.json'
RAM_RECOVERY_INTENT='ram-recovery-intent.json'
RAM_RECOVERY_ERROR_CODES=frozenset({
 'RAM_LEDGER_BOUNDS','RAM_LEDGER_READ_HANDLE','RAM_LEDGER_DRIFT','RAM_LEDGER_SHAPE',
 'RAM_RECOVERY_POLICY','RAM_RECOVERY_FOUNDATION_LEDGER','RAM_RECOVERY_BOOT',
 'RAM_RECOVERY_VOLUME','RAM_RECOVERY_TASK','RAM_RECOVERY_PRIOR_VOLUME',
 'RAM_RECOVERY_MUTEX_BUSY','RAM_RECOVERY_COPY','RAM_RECOVERY_LEDGER_CHANGED',
 'RAM_RECOVERY_POST_LEDGER','RAM_RECOVERY_POST_VOLUME','RAM_RECOVERY_ROOT_CHANGED',
 'RAM_RECOVERY_EXCLUSIONS','RAM_RECOVERY_ROOT_CONTENTS','RAM_RECOVERY_BACKUP_CHANGED',
 'RAM_RECOVERY_OLD_LEDGER_REQUIRED','RAM_RECOVERY_OUTPUT_EXISTS','RAM_RECOVERY_CAPACITY'})

@contextmanager
def production_ram_lock(win,mount):
    """Same production mutex; ensure recursively acquires only this owner."""
    kernel=win._api()['kernel32'];handle=win._check(kernel.CreateMutexW(None,False,
        'Global\\CoChemRAM-'+hashlib.sha256(str(mount).casefold().encode()).hexdigest()[:24]),'Open exact RAM mutex')
    acquired=False
    try:
        require(kernel.WaitForSingleObject(handle,0) in (0,0x80),'RAM_RECOVERY_MUTEX_BUSY')
        acquired=True;yield
    finally:
        if acquired:
            release=kernel.ReleaseMutex;release.argtypes=[win.HANDLE];release.restype=win.BOOL
            win._check(release(handle),'Release RAM provisioning mutex')
        win._close(handle)

def read_ram_ledger(win,path):
    """Protected bounded read; deny writes/deletion until captured bytes close."""
    import msvcrt
    from cochem_pipeline.ramdisk import ordinary_tree
    ordinary_tree(path);win.validate_private_path(path);before=path.stat()
    require(path.is_file() and before.st_nlink==1 and 0<before.st_size<=1048576,'RAM_LEDGER_BOUNDS')
    handle=win._api()['kernel32'].CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    require(handle not in (0,None,C.c_void_p(-1).value),'RAM_LEDGER_READ_HANDLE')
    try:fd=msvcrt.open_osfhandle(int(handle),os.O_RDONLY|os.O_BINARY);handle=None
    except BaseException:win._close(handle);raise
    with os.fdopen(fd,'rb') as stream:raw=stream.read(1048577)
    after=path.stat()
    require(len(raw)==before.st_size and len(raw)<=1048576 and
        (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
        (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'RAM_LEDGER_DRIFT')
    value=strict_json(raw.decode('utf-8'))
    require(isinstance(value,dict),'RAM_LEDGER_SHAPE')
    return value,raw

def ram_recovery_plan(config,prior,raw,foundation,current_boot,before):
    """A missing subtree alone never authorizes recovery or fixture loss."""
    from cochem_pipeline.ramdisk import mount_recovery_action
    policy=config.ramdisk
    require(policy.enabled is True and policy.adopted_drive is True and policy.size_mb==8192 and
        bool(policy.workspace_subdirectory) and policy.workspace_root==RAM_WORKSPACE_ROOT and
        policy.mount_root==RAM_MOUNT_ROOT,'RAM_RECOVERY_POLICY')
    require(isinstance(prior,dict) and type(prior.get('schema')) is int and prior['schema']==1 and prior.get('state')=='READY' and
        prior.get('config')==policy.as_dict() and prior.get('mount_root')==str(Path(policy.mount_root)) and
        prior.get('workspace_root')==str(policy.workspace_root) and prior.get('slots')==[f'slot{i}' for i in range(1,7)] and
        prior.get('adopted_existing_drive') is True and prior.get('volume_root_metadata_preserved') is True,
        'RAM_RECOVERY_OLD_LEDGER_REQUIRED')
    require(type(foundation.get('ram_ledger_sha256')) is str and
        hashlib.sha256(raw).hexdigest()==foundation['ram_ledger_sha256'] and prior==foundation.get('ram_ledger'),
        'RAM_RECOVERY_FOUNDATION_LEDGER')
    old_boot=prior.get('boot_id')
    require(type(current_boot) is int and type(old_boot) is int and 0<old_boot<current_boot,'RAM_RECOVERY_BOOT')
    volume=before.get('volume') if isinstance(before,dict) else None
    old_volume=prior.get('observed')
    require(isinstance(volume,dict) and isinstance(old_volume,dict) and
        type(volume.get('volume_serial')) is int and type(old_volume.get('volume_serial')) is int and
        volume['volume_serial']!=old_volume['volume_serial'] and type(volume.get('device_number')) is int and
        volume.get('size_bytes')==8589934592 and volume.get('filesystem')=='NTFS' and
        re.fullmatch(r'\\Device\\ImDisk[0-9]{1,8}',str(volume.get('target',''))) and
        mount_recovery_action(prior,policy,current_boot,volume['target'])=='ADOPT','RAM_RECOVERY_VOLUME')
    old_before=foundation.get('ram_before');old_after=foundation.get('ram_after')
    require(isinstance(old_before,dict) and old_before==old_after and old_before.get('volume')==old_volume,'RAM_RECOVERY_PRIOR_VOLUME')
    require(before.get('task_xml_sha256')==old_before.get('task_xml_sha256')==RAM_TASK_SHA,'RAM_RECOVERY_TASK')
    return {'schema':'cochem-first-start-verified-reboot-ram-recovery/1',
        'prior_boot_id':old_boot,'current_boot_id':current_boot,'prior_ledger_sha256':hashlib.sha256(raw).hexdigest(),
        'prior_volume':old_volume,'current_volume':volume,'current_root_task_baseline':before,
        'foundation_receipt_sha256':PRIORS['foundation'][1],'physical_receipt_sha256':PRIORS['docker'][1],
        'workspace_root':str(policy.workspace_root),'verified_reboot':True,
        'adopt_existing_drive_only':True,'docker_retested':False,'current_boot_physical_test_verified':False,
        'volatile_fixture_unavailable_after_verified_reboot':True,'automatic_retry_allowed':False}

def assert_loopback_available(port):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        listener.bind(('127.0.0.1',port))

def assert_ram_capacity(config):
    require(shutil.disk_usage(config.ramdisk.mount_root).free>=config.ramdisk.min_free_mb*1024*1024,
        'RAM_RECOVERY_CAPACITY')

def ram_recovery_outputs_absent():
    for name in (RAM_RECOVERY_INTENT,RAM_RECOVERY_BACKUP,RAM_RECOVERY_MANIFEST):
        try:(ROOT/name).lstat()
        except FileNotFoundError:continue
        raise ValueError('RAM_RECOVERY_OUTPUT_EXISTS')

def recover_ram_once(config,win,ram,prior,raw,plan,identities):
    """Fence once, retain original bytes, then use production ADOPT once."""
    from cochem_pipeline.ramdisk import ordinary_tree
    ram_recovery_outputs_absent()
    # The intent is the first write. It fences a failure during backup creation
    # just as it fences an ambiguous native provisioning result.
    intent_hash=write_new(ROOT/RAM_RECOVERY_INTENT,{**plan,'backup_filename':RAM_RECOVERY_BACKUP,'manifest_filename':RAM_RECOVERY_MANIFEST})
    win.validate_code_path(ROOT/RAM_RECOVERY_INTENT)
    backup=ROOT/RAM_RECOVERY_BACKUP
    with backup.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    ordinary_tree(backup);win.validate_code_path(backup)
    require(backup.stat().st_nlink==1 and backup.read_bytes()==raw,'RAM_RECOVERY_COPY')
    manifest_hash=write_new(ROOT/RAM_RECOVERY_MANIFEST,{'schema':'cochem-first-start-ram-ledger-preservation/1',
        'source_path':str(config.private_root/'ramdisk-state.json'),'backup_path':str(backup),
        'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ram_recovery_intent_sha256':intent_hash,
        'source_deleted_by_helper':False})
    win.validate_code_path(ROOT/RAM_RECOVERY_MANIFEST)
    current,current_raw=read_ram_ledger(win,config.private_root/'ramdisk-state.json')
    require(current==prior and current_raw==raw,'RAM_RECOVERY_LEDGER_CHANGED')
    # No fixture or RAM directory has been created by this helper. Production
    # ensure is the sole mutator and must select ADOPT on the reviewed volume.
    absent(config.ramdisk.workspace_root)
    require(win.current_boot_identity()==plan['current_boot_id'] and
        ram_observation(win,config)==plan['current_root_task_baseline'],'RAM_RECOVERY_POST_VOLUME')
    assert_ram_capacity(config)
    before_exclusions=win.defender_exclusions()
    expected={str(config.ramdisk.workspace_root/slot).casefold() for slot in identities}
    ensured=ram.ensure()
    require(ensured.get('state')=='READY' and ensured.get('lifecycle_action')=='ADOPT' and
        ensured.get('boot_id')==plan['current_boot_id'] and ensured.get('observed')==plan['current_volume'] and
        ensured.get('config')==config.ramdisk.as_dict() and ensured.get('slots')==sorted(identities) and
        ensured.get('workspace_root')==str(config.ramdisk.workspace_root) and ensured.get('backup') is None and
        ensured.get('adopted_existing_drive') is True and ensured.get('volume_root_metadata_preserved') is True,
        'RAM_RECOVERY_POST_LEDGER')
    inspected=ram.inspect(require_capacity=True)
    require(inspected.get('boot_id')==plan['current_boot_id'] and inspected.get('observed')==plan['current_volume'],
        'RAM_RECOVERY_POST_VOLUME')
    ordinary_tree(config.ramdisk.workspace_root)
    with os.scandir(config.ramdisk.workspace_root) as entries:
        require({entry.name for entry in entries}==set(identities),'RAM_RECOVERY_ROOT_CONTENTS')
    roots=[empty_directory(ram.workspace(slot).root,ordinary_tree) for slot in identities]
    after_exclusions=win.defender_exclusions()
    require(after_exclusions==before_exclusions|expected,'RAM_RECOVERY_EXCLUSIONS')
    require(win.current_boot_identity()==plan['current_boot_id'] and
        ram_observation(win,config)==plan['current_root_task_baseline'],'RAM_RECOVERY_ROOT_CHANGED')
    new_ledger,new_raw=read_ram_ledger(win,config.private_root/'ramdisk-state.json')
    require(new_ledger==ensured,'RAM_RECOVERY_POST_LEDGER')
    require(backup.stat().st_nlink==1 and backup.read_bytes()==raw,'RAM_RECOVERY_BACKUP_CHANGED')
    return {**plan,'ram_recovery_intent_sha256':intent_hash,'ledger_preservation_manifest_sha256':manifest_hash,
        'ledger_backup_path':str(backup),'ledger_backup_unchanged':True,
        'new_ledger_sha256':hashlib.sha256(new_raw).hexdigest(),'ram_ensure_calls':1,
        'ram_lifecycle_action':'ADOPT','current_root_and_startup_task_preserved':True,
        'six_empty_ram_roots_verified':True,'drive_created_formatted_or_resized':False},roots

def preflight_state(config, win, foundation, physical):
    from cochem_pipeline.ramdisk import RamdiskManager,ordinary_tree
    identities={slot:win.WorkerIdentity(**spec) for slot,spec in config.workers.items()}
    layout=win.validate_layout(config.private_root,config.slot_roots,identities,require_defender=True)
    win.validate_controller_token(config.token_file,config.operator_name,list(identities.values()))
    for name in ('job_board.db','job_board.db-wal','job_board.db-shm','job_board.db-journal','oracle','operations','supervisor_status.json','warden.lock','warden.log','warden.log.1','warden.log.2','warden.log.3','crash-envelope.json'):
        absent(config.private_root/name)
    absent(QUEUE)
    scratch=[empty_directory(path,ordinary_tree) for path in config.slot_roots.values()]
    ram=RamdiskManager(config.ramdisk,config.private_root,identities)
    with production_ram_lock(win,config.ramdisk.mount_root):
        prior,raw=read_ram_ledger(win,config.private_root/'ramdisk-state.json')
        current_boot=win.current_boot_identity()
        require(type(current_boot) is int and current_boot>0 and type(prior.get('boot_id')) is int and
            prior['boot_id']>0,'RAM_RECOVERY_BOOT')
        reboot=prior['boot_id']!=current_boot
        plan=None
        if reboot:
            # This exception is authorized by a native new boot, the exact old
            # foundation ledger and an absent dedicated subtree together.
            before=ram_observation(win,config)
            plan=ram_recovery_plan(config,prior,raw,foundation,current_boot,before)
            absent(config.ramdisk.workspace_root)
            fixture_manifest_match(physical)
            ram_recovery_outputs_absent()
            assert_ram_capacity(config)
        else:
            ram.inspect(require_capacity=True)
            for slot in identities:
                root=ram.workspace(slot).root
                if slot=='slot1':
                    ordinary_tree(root)
                    with os.scandir(root) as entries:names={e.name for e in entries}
                    require(names=={FIXTURE_NAME},'SLOT1_EXPECTED_PHYSICAL_FIXTURE')
                    fixture_shape(root/FIXTURE_NAME,ordinary_tree)
                else:scratch.append(empty_directory(root,ordinary_tree))
        assert_loopback_available(config.port)
        registry=registry_preflight(config,win,foundation)
        task_control(win,'stopped')
        # All persistent-state, scratch, port and stopped-task guards complete
        # before the first copy/intent or production ADOPT mutation.
        if reboot:
            recovery,ram_scratch=recover_ram_once(config,win,ram,prior,raw,plan,identities)
            scratch.extend(ram_scratch)
            preserved={'status':'VOLATILE_FIXTURE_UNAVAILABLE_AFTER_VERIFIED_REBOOT',
                'physical_receipt_sha256':PRIORS['docker'][1],'files':0,'bytes':0,
                'fixture_recreated':False,'source_deleted_by_helper':False,
                'docker_retested':False,'current_boot_physical_test_verified':False}
        else:
            recovery=None
            preserved=preserve_fixture(ram.workspace('slot1').root/FIXTURE_NAME,PRESERVATION_ROOT,physical,win,ordinary_tree)
        require(registry_preflight(config,win,foundation)==registry,'REGISTRY_DRIFT')
        assert_loopback_available(config.port)
        task_control(win,'stopped')
    result={'fresh_primary_database':True,'empty_scratch_roots':scratch,'ram_inspection_only':not reboot,
        'worker_count':len(layout['slots']),'registry':registry,'preserved_known_scratch_fixture':preserved}
    if recovery is not None:result['verified_reboot_ram_recovery']=recovery
    return result

TASK_SCRIPT = r'''
$s=New-Object -ComObject 'Schedule.Service';$s.Connect();$f=$s.GetFolder('\');$t=$f.GetTask('CoChem-4.2.7-Warden');$d=$t.Definition
foreach($name in @('CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
 $x=$null;try{$x=$f.GetTask($name)}catch{$e=$_.Exception;while($null -ne $e -and $e.HResult -ne -2147024894){$e=$e.InnerException};if($null -eq $e){throw 'TASK_STATE_UNKNOWN'}}
 if($null -ne $x -and ($x.Enabled -or $x.State -notin @(1,3) -or $x.GetInstances(0).Count -ne 0)){throw 'OTHER_DAEMON_ACTIVE'}
}
if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or $d.Settings.RestartCount -ne 0 -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.ExecutionTimeLimit -cne 'PT0S' -or -not $d.Settings.AllowDemandStart){throw 'TASK_DEFINITION'}
$a=$d.Actions.Item(1);if($a.Type -ne 0 -or $a.Path -cne $data.python -or $a.Arguments -cne $data.arguments -or $a.WorkingDirectory -cne $data.install){throw 'TASK_ACTION'}
$sd=[Security.AccessControl.RawSecurityDescriptor]::new($t.GetSecurityDescriptor(7));$seen=@{}
if($sd.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or -not($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $sd.DiscretionaryAcl.Count -ne 2){throw 'TASK_ACL'}
foreach($ace in $sd.DiscretionaryAcl){if($ace.AceType -ne 0 -or $ace.AceFlags -ne 0 -or $ace.AccessMask -ne 2032127 -or $ace.SecurityIdentifier.Value -notin @('S-1-5-18','S-1-5-32-544') -or $seen.ContainsKey($ace.SecurityIdentifier.Value)){throw 'TASK_ACL'};$seen[$ace.SecurityIdentifier.Value]=$true}
if($data.mode -eq 'start'){
 if($t.Enabled -or $t.State -ne 1 -or $t.GetInstances(0).Count -ne 0){throw 'TASK_NOT_FRESH_DISABLED'}
 $t.Enabled=$true;$instance=$t.Run($null)
 [ordered]@{started=$true;instance_guid=$instance.InstanceGuid}|ConvertTo-Json -Compress
}elseif($data.mode -eq 'stopped'){
 if($t.Enabled -or $t.State -ne 1 -or $t.GetInstances(0).Count -ne 0){throw 'TASK_NOT_FRESH_DISABLED'}
 [ordered]@{disabled=$true;instances=0}|ConvertTo-Json -Compress
}else{
 if(-not $t.Enabled -or $t.State -ne 4 -or $t.GetInstances(0).Count -ne 1){throw 'TASK_NOT_ONE_RUNNING_INSTANCE'}
 $instances=$t.GetInstances(0);if($instances.Item(1).InstanceGuid -cne $data.instance){throw 'TASK_INSTANCE_CHANGED'}
 [ordered]@{running=$true;instances=1}|ConvertTo-Json -Compress
}
'''

def task_control(win,mode,instance=None):
    data={'python':str(PYTHON),'arguments':f'-I -B -m cochem_pipeline daemon --config "{ROOT / "pipeline.json"}" --queue-launch-output "{QUEUE}"',
          'install':str(INSTALL),'mode':mode,'instance':instance}
    return json.loads(win._powershell(TASK_SCRIPT,data))

def write_new(path,value):
    raw=(json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False,indent=2)+'\n').encode()
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()

def start_once(intent_path,intent,start):
    write_new(intent_path,intent)
    return start()  # Deliberately no retry, deletion, Stop or Restart on failure.

def health_sample(heartbeat,health,started,now):
    require(isinstance(heartbeat,dict) and isinstance(health,dict),'HEALTH_SHAPE')
    for field in ('timestamp','process_started_at'):
        require(type(heartbeat.get(field)) in (int,float) and math.isfinite(heartbeat[field]),'HEALTH_TIME')
    require(-5<=now-heartbeat['timestamp']<=30 and heartbeat['timestamp']>=heartbeat['process_started_at']>=started-1,'HEALTH_STALE')
    require(type(heartbeat.get('pid')) is int and heartbeat['pid']>0 and type(heartbeat.get('sequence')) is int and heartbeat['sequence']>0 and re.fullmatch('[a-f0-9]{32}',str(heartbeat.get('instance_id'))),'HEALTH_IDENTITY')
    expected_source=str(INSTALL/'.venv/Lib')
    require(heartbeat.get('source_root')==expected_source and health.get('source_root')==expected_source,'HEALTH_SOURCE')
    require(all(health.get(k)==heartbeat.get(k) for k in ('pid','instance_id','process_started_at','version')),'HEALTH_MISMATCH')
    exact(health,{'service_identity':'SYSTEM','quarantined_slots':{},'active':[]},'HEALTH_UNSAFE')
    require(health.get('trip_errors') in ({},[]) and health.get('knowledge',{}).get('ready') is True,'HEALTH_GATES')
    require(type(health.get('admission_capacity')) is int and 0<=health['admission_capacity']<=4,'HEALTH_CAPACITY')
    return {'pid':heartbeat['pid'],'instance_id':heartbeat['instance_id'],'sequence':heartbeat['sequence'],'process_started_at':heartbeat['process_started_at']}

def one_process_proof(win,pid,started,expected_image,expected_sid='S-1-5-18'):
    api=win._api();kernel=api['kernel32'];kernel.OpenProcess.argtypes=[win.DWORD,win.BOOL,win.DWORD];kernel.OpenProcess.restype=win.HANDLE
    process=kernel.OpenProcess(0x1000,False,pid);win._check(process,'Observe controller');token=win.HANDLE()
    try:
        win._check(api['advapi32'].OpenProcessToken(process,8,C.byref(token)),'Observe controller token')
        user=win._SID_AND_ATTRIBUTES.from_buffer(win._token_info(token,1));sid=win._sid_text(user.Sid);require(sid==expected_sid,'CONTROLLER_TOKEN')
        query=kernel.QueryFullProcessImageNameW;query.argtypes=[win.HANDLE,win.DWORD,win.LPWSTR,C.POINTER(win.DWORD)];query.restype=win.BOOL
        count=win.DWORD(32768);image=C.create_unicode_buffer(count.value);win._check(query(process,0,image,C.byref(count)),'Observe controller image')
        require(Path(image.value).resolve()==expected_image.resolve(),'CONTROLLER_IMAGE')
        created=win.process_creation_filetime(process);require(created>=int((started-1+11644473600)*10000000),'CONTROLLER_OLD_PROCESS')
        return {'pid':pid,'creation_filetime':created,'token_sid':sid,'image_path_sha256':hashlib.sha256(str(expected_image).casefold().encode()).hexdigest()}
    finally:win._close(token);win._close(process)

def process_proof(win,pid,started):
    import psutil
    process=psutil.Process(pid);parent=process.parent();require(parent is not None,'CONTROLLER_PARENT')
    expected=[str(PYTHON),'-I','-B','-m','cochem_pipeline','daemon','--config',str(ROOT/'pipeline.json'),'--queue-launch-output',str(QUEUE)]
    require(parent.cmdline()==expected,'CONTROLLER_PARENT_ARGUMENTS')
    child_proof=one_process_proof(win,pid,started,BASE_PYTHON)
    parent_proof=one_process_proof(win,parent.pid,started,PYTHON)
    require(process.ppid()==parent.pid and parent.cmdline()==expected,'CONTROLLER_PARENT_DRIFT')
    require(parent_proof['creation_filetime']<=child_proof['creation_filetime'],'CONTROLLER_PARENT_TIME')
    return {**child_proof,'launcher':parent_proof,'launcher_arguments_verified':True}

def wait_control_plane(win,config,started,instance,timeout=240):
    from cochem_supervisor.probes import ControllerClient
    from cochem_supervisor.shared_io import open_shared_text
    client=ControllerClient(config.port,config.token_file,timeout=5)
    deadline=time.monotonic()+timeout;previous=None;last_code='NO_OBSERVATION'
    while time.monotonic()<deadline:
        try:
            task_control(win,'running',instance)
            with open_shared_text(PRIVATE/'supervisor_status.json') as stream:beat=json.loads(stream.read(65537))
            sample=health_sample(beat,client.call('/health'),started,time.time())
            proof=process_proof(win,sample['pid'],started)
            if previous and (previous['pid'],previous['instance_id'],previous['creation_filetime'])==(sample['pid'],sample['instance_id'],proof['creation_filetime']) and sample['sequence']>previous['sequence']:
                with open_shared_text(QUEUE/'queue-launch.json') as stream:q=json.loads(stream.read(1048577))
                exact(q,{'schema':1,'kind':'actual-warden-queue-launch-observation','platform':'nt','controller_pid':sample['pid'],'configured_slots':6,
                         'configured_admission_ceiling':4,'topology_matches_four_worker_acceptance':True},'QUEUE_BINDING')
                return {**proof,'instance_id':sample['instance_id'],'first_sequence':previous['sequence'],'final_sequence':sample['sequence']}
            previous={**sample,**proof}
        except (OSError,ValueError,RuntimeError) as error:
            previous=None
            text=str(error)
            last_code=text if re.fullmatch('[A-Z][A-Z0-9_]{0,79}',text) else type(error).__name__
        time.sleep(1)
    failure=TimeoutError('CONTROL_PLANE_DEADLINE_PRESERVE_RUNNING_STATE');failure.observation_code=last_code;raise failure

def run(nonce,packet_hash):
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    win.require_system();require(Path(__file__).resolve()==ROOT/'commission-first-warden-r3-v1.py' and Path(sys.executable).resolve()==PYTHON,'PROTECTED_ENTRYPOINT')
    win.validate_code_path(ROOT);win.validate_code_path(Path(__file__))
    support=load_support(ROOT/'worker-native-status-r3.py')
    report={'schema':'cochem-warden-commissioning/1','status':'WARDEN_COMMISSIONING_HELD','nonce':nonce,'system_sid':'S-1-5-18',
       'helper_sha256':support.digest_file(Path(__file__)),'runtime_root':str(INSTALL),'install_receipt_sha256':INSTALL_HASH,'config_sha256':CONFIG_HASH,
       'source_manifest_sha256':MANIFEST_HASH,'revision_sha256':REVISION_HASH,'task_name':TASK,'input_sha256':packet_hash,
       'queue_launch_output':str(QUEUE),'exactly_one_start_requested':False,'monitoring_started':False,'full_srs_acceptance':False,
       'monitoring_scope':'heartbeat_and_queue_only','automatic_repair_enabled':False,'model_jobs_submitted':0,'automatic_retry_allowed':False,'started_at_unix_ms':int(time.time()*1000)}
    phase='runtime_custody';reservations=ExitStack()
    try:
        support.verify_r3_runtime(win,INSTALL_HASH)
        packet,_=read_control(win,support,ROOT/'inputs.json',packet_hash)
        exact(packet,{'schema':'cochem-warden-commissioning-inputs/1','nonce':nonce,'config_sha256':CONFIG_HASH,'source_manifest_sha256':MANIFEST_HASH},'INPUT_BINDING')
        _,_=read_control(win,support,ROOT/'pipeline.json',CONFIG_HASH)
        config=load_config(str(ROOT/'pipeline.json'))
        phase='prior_evidence'
        priors={name:read_control(win,support,path,pin)[0] for name,(path,pin) in PRIORS.items()}
        phase='authentication';auth=validate_auth(lambda path,pin:read_control(win,support,path,pin),packet.get('auth_attempt'))
        require(auth['receipt_sha256']==packet.get('auth_receipt_sha256'),'AUTH_PACKET_PIN')
        report['authentication_attempt']=packet['auth_attempt'];report['authentication_receipt_sha256']=auth['receipt_sha256'];report['authenticated_profiles_verified']=len(auth['profiles'])
        phase='state_preflight';task_control(win,'stopped')
        reservations.enter_context(reserve_workers(win,config.workers));report['all_six_worker_identities_excluded']=True
        report['preservation_preflight']=preflight_state(config,win,priors['foundation'],priors['docker'])
        phase='start_intent';started=time.time()
        intent={'schema':'cochem-warden-first-start-intent/1','nonce':nonce,'task_name':TASK,'config_sha256':CONFIG_HASH,'started_after':started,'automatic_retry_allowed':False}
        def launch():
            nonlocal phase
            phase='enable_run';report['exactly_one_start_requested']=True
            return task_control(win,'start')
        started_task=start_once(ROOT/'start-intent.json',intent,launch)
        report['start_intent_sha256']=support.digest_file(ROOT/'start-intent.json')
        require(started_task.get('started') is True and re.fullmatch(r'\{?[a-fA-F0-9-]{36}\}?',str(started_task.get('instance_guid'))),'TASK_START_RECEIPT')
        report['task_instance_guid']=started_task['instance_guid']
        phase='control_plane';report['controller']=wait_control_plane(win,config,started,started_task['instance_guid'])
        task_control(win,'running',started_task['instance_guid'])
        report.update(status='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED',monitoring_started=True)
    except BaseException as error:
        report['failure']={'phase':phase,'error_type':type(error).__name__,'winerror':getattr(error,'winerror',None) if type(getattr(error,'winerror',None)) is int else None}
        if type(error) is ValueError and str(error) in RAM_RECOVERY_ERROR_CODES:report['failure']['code']=str(error)
        code=getattr(error,'observation_code',None)
        if isinstance(code,str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}',code):report['failure']['last_observation_code']=code
        report['potentially_running_state_preserved']=True
    finally:reservations.close()
    report['finished_at_unix_ms']=int(time.time()*1000)
    write_new(ROOT/'commissioning.json',report)
    return 0 if report['status']=='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED' else 2

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--nonce',required=True);parser.add_argument('--input-sha256',required=True);args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.nonce) or not re.fullmatch('[a-f0-9]{64}',args.input_sha256):raise SystemExit(2)
    try:raise SystemExit(run(args.nonce,args.input_sha256))
    except Exception:raise SystemExit(2)  # Never publish raw native/credential-bearing exception text.
