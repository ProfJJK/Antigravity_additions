"""CreateNew first-start successor; never execute deployment functions."""
import ast
import hashlib
from pathlib import Path

HERE = Path(__file__).parent
OLD_SHA = '9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755'
raw = (HERE / 'commission-first-warden-r3-v3.py').read_bytes()
assert hashlib.sha256(raw).hexdigest() == OLD_SHA
text = raw.decode('utf-8')
old = ast.parse(text)
prerequisite = (HERE / 'inspect-execution-prerequisites-r3.py').read_bytes()
assert hashlib.sha256(prerequisite).hexdigest() == '17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
prerequisite_text = prerequisite.decode('utf-8')
prerequisite_ast = ast.parse(prerequisite_text)
copied = '\n\n'.join(ast.get_source_segment(prerequisite_text, node)
                     for node in prerequisite_ast.body
                     if isinstance(node, ast.FunctionDef) and node.name in {'strict_json', 'sha', 'ram_observation'})
assert len([node for node in ast.parse(copied).body if isinstance(node, ast.FunctionDef)]) == 3

addition = r'''
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
'''

preflight = r'''def preflight_state(config, win, foundation, physical):
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
'''

node = next(node for node in old.body if isinstance(node, ast.FunctionDef) and node.name == 'preflight_state')
start = text.index(ast.get_source_segment(text, node))
end = start + len(ast.get_source_segment(text, node))
text = text[:start] + copied + '\n\n' + addition.strip() + '\n\n' + preflight.rstrip() + text[end:]
text = text.replace('import socket\n', 'import socket\nimport shutil\n', 1)
needle = "        code=getattr(error,'observation_code',None)"
assert text.count(needle) == 1
text = text.replace(needle, "        if type(error) is ValueError and str(error) in RAM_RECOVERY_ERROR_CODES:report['failure']['code']=str(error)\n" + needle)
ast.parse(text)
destination = HERE / 'commission-first-warden-r3-v5.py'
with destination.open('x', encoding='utf-8', newline='\n') as stream:
    stream.write(text)
print(hashlib.sha256(destination.read_bytes()).hexdigest())
