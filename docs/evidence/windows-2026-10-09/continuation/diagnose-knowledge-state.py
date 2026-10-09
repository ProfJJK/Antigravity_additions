"""Bounded SYSTEM metadata/ACL diagnostic; never open index file contents."""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT=Path(r'C:\Program Files\CoChem\KnowledgeDiagnostic4.2.7-windows-20261007-a')
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
BASE=Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312')
ORIGINAL=Path(r'C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006')
STATE=Path(r'C:\ProgramData\CoChemPipeline427\private\knowledge-windows-20261006')
ORIGINAL_NONCE='d4606fa578ae401b81ace37cd8e2909b'
ORIGINAL_HELPER='832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca'
INVENTORY='edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892'
CONFIG='2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
PINS={'windows.py':'ca07b3bba2b22d0eb095c1f05b9a6c0969207bf7d8b25741adbaee184f4618ba',
      'knowledge.py':'f7b14fc86a7e0739f7788b5710bda09f330d0b1032d403942708fdb415071b76',
      'knowledge_authority.py':'e7dd99cb0f9a6710d3a03cff2d983a58879fef0b68bd463c0f38607603ba1f27'}
KNOWN={'current.json','sources.json','writer.lock','knowledge_index.db','knowledge_index.db-wal','knowledge_index.db-shm','knowledge_index.db-journal'}


def strict_json(raw):
    def pairs(items):
        out={}
        for key,value in items:
            if key in out:raise ValueError('Duplicate evidence field')
            out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite evidence')))


def check_original(value):
    expected={'schema':'cochem-private-knowledge-system-acceptance/1','nonce':ORIGINAL_NONCE,
              'system_sid':'S-1-5-18','status':'KNOWLEDGE_ACCEPTANCE_FAILED','helper_sha256':ORIGINAL_HELPER,
              'payload_inventory_sha256':INVENTORY,'pipeline_config_sha256':CONFIG,
              'index_created_new':True,'started_at_unix_ms':1791378634141,'finished_at_unix_ms':1791378635667}
    if not isinstance(value,dict) or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in expected.items()):
        raise ValueError('Original failure receipt binding differs')
    failure=value.get('failure')
    if not isinstance(failure,dict) or failure.get('phase')!='new_index' or failure.get('error_type')!='WindowsIsolationError':
        raise ValueError('Original failure classification differs')
    return {**expected,'failure':{'phase':'new_index','error_type':'WindowsIsolationError'}}


def safe_name(name):
    if name in KNOWN or re.fullmatch('g-[0-9a-f]{32}',name):return name
    return 'name-sha256:'+hashlib.sha256(name.encode('utf-8','surrogatepass')).hexdigest()


def safe_error(error):
    return {'error_type':re.sub('[^A-Za-z0-9_]','',type(error).__name__)[:80],
            'winerror':getattr(error,'winerror',None) if type(getattr(error,'winerror',None)) is int else None}


def metadata(path, win):
    """FILE_READ_ATTRIBUTES + READ_CONTROL only, no FILE_READ_DATA access."""
    api=win._api();kernel=api['kernel32'];advapi=api['advapi32']
    handle=kernel.CreateFileW(str(path),0x20080,7,None,3,0x02200000,None)
    if handle==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
    try:
        class Info(C.Structure):
            _fields_=[('attributes',W.DWORD),('created',W.FILETIME),('accessed',W.FILETIME),('written',W.FILETIME),
                      ('volume',W.DWORD),('high',W.DWORD),('low',W.DWORD),('links',W.DWORD),('index_high',W.DWORD),('index_low',W.DWORD)]
        query=kernel.GetFileInformationByHandle;query.argtypes=[W.HANDLE,C.POINTER(Info)];query.restype=W.BOOL
        item=Info()
        if not query(handle,C.byref(item)):raise C.WinError(C.get_last_error())
        final=kernel.GetFinalPathNameByHandleW;final.argtypes=[W.HANDLE,W.LPWSTR,W.DWORD,W.DWORD];final.restype=W.DWORD
        buffer=C.create_unicode_buffer(32768);size=final(handle,buffer,len(buffer),0)
        if not 0<size<len(buffer) or buffer.value.casefold()!=('\\\\?\\'+str(path.absolute())).casefold():raise ValueError('Metadata handle path differs')
        owner,dacl,descriptor=win.HANDLE(),win.HANDLE(),win.HANDLE()
        security=advapi.GetSecurityInfo
        security.argtypes=[W.HANDLE,C.c_int,W.DWORD,C.POINTER(win.HANDLE),C.c_void_p,C.POINTER(win.HANDLE),C.c_void_p,C.POINTER(win.HANDLE)]
        security.restype=W.DWORD
        result=security(handle,1,5,C.byref(owner),None,C.byref(dacl),None,C.byref(descriptor))
        if result:raise C.WinError(result)
        try:
            control,revision=win.WORD(),win.DWORD()
            win._check(advapi.GetSecurityDescriptorControl(descriptor,C.byref(control),C.byref(revision)),'Read diagnostic ACL control')
            rules=[]
            if dacl:
                count=win._ACL_SIZE_INFORMATION()
                win._check(advapi.GetAclInformation(dacl,C.byref(count),C.sizeof(count),2),'Read diagnostic ACL count')
                if count.AceCount>128:raise ValueError('ACL count exceeds diagnostic bound')
                for n in range(count.AceCount):
                    ace=win.HANDLE();win._check(advapi.GetAce(dacl,n,C.byref(ace)),'Read diagnostic ACE')
                    header=win._ACE_HEADER.from_address(ace.value)
                    row={'type':header.AceType,'flags':header.AceFlags,'size':header.AceSize}
                    if header.AceType in (0,1) and header.AceSize>=12:
                        row.update(sid=win._sid_text(ace.value+8),mask=win.DWORD.from_address(ace.value+4).value)
                    rules.append(row)
            record={'directory':bool(item.attributes&0x10),'reparse':bool(item.attributes&0x400),
                    'bytes':item.high<<32|item.low,'links':item.links,'attributes':item.attributes,
                    'file_identity':f'{item.volume:x}:{item.index_high:x}:{item.index_low:x}',
                    'created_filetime':item.created.dwHighDateTime<<32|item.created.dwLowDateTime,
                    'written_filetime':item.written.dwHighDateTime<<32|item.written.dwLowDateTime,
                    'owner_sid':win._sid_text(owner),'dacl_protected':bool(control.value&0x1000),
                    'null_dacl':not bool(dacl),'aces':rules,'owner_rights_ace':any(row.get('sid')=='S-1-3-4' for row in rules),
                    'desired_access':0x20080,'content_read':False,'content_hashed':False}
            record['metadata_sha256']=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            return record
        finally:kernel.LocalFree(descriptor)
    finally:win._close(handle)


def inspect_state(root, inspect, *, maximum=128, seconds=30):
    deadline=time.monotonic()+seconds;rows=[];queue=[(root,0,'state')]
    while queue:
        if len(rows)>=maximum or time.monotonic()>deadline:raise ValueError('State diagnostic exceeded bounded scope')
        path,depth,label=queue.pop(0)
        try:record=inspect(path)
        except FileNotFoundError:rows.append({'relative':label,'state':'NOT_FOUND'});continue
        except BaseException as error:rows.append({'relative':label,'state':'INACCESSIBLE_OR_ERROR',**safe_error(error)});continue
        rows.append({'relative':label,'state':'OBSERVED',**record})
        if record['reparse'] or not record['directory']:continue
        if depth==0 or depth==1 and re.fullmatch('g-[0-9a-f]{32}',path.name):
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(rows)+len(queue)>=maximum:raise ValueError('State diagnostic entry bound exceeded')
                    queue.append((Path(entry.path),depth+1,label+'/'+safe_name(entry.name)))
    return rows


def task_evidence(win):
    # Fixed task only, no registration/start/stop calls, no raw action text.
    value=win._powershell(r'''
      $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$task=$scheduler.GetFolder('\').GetTask('CoChem-4.2.7-PrivateKnowledge-Acceptance');
      $d=$task.Definition;$action=$d.Actions.Item(1);
      [ordered]@{state=[int]$task.State;instances=[int]$task.GetInstances(0).Count;last_task_result=[int64]$task.LastTaskResult;enabled=[bool]$task.Enabled;
       system_principal=($d.Principal.UserId -in @('SYSTEM','S-1-5-18'));logon_type=[int]$d.Principal.LogonType;run_level=[int]$d.Principal.RunLevel;
       triggers=[int]$d.Triggers.Count;actions=[int]$d.Actions.Count;action_type=[int]$action.Type;
       executable_matches=($action.Path -ceq $data.python);arguments_match=($action.Arguments -ceq $data.arguments);working_directory_matches=($action.WorkingDirectory -ceq $data.root)}|ConvertTo-Json -Compress
    ''',{'python':str(INSTALL/'.venv/Scripts/python.exe'),'arguments':f'-I -B "{ORIGINAL / "accept-private-knowledge.py"}" {ORIGINAL_NONCE} {INVENTORY}','root':str(ORIGINAL)})
    value=strict_json(value)
    if (value['state'] not in (1,3) or value['instances']!=0 or value['last_task_result']!=2 or not value['system_principal']
        or value['logon_type']!=5 or value['run_level']!=1 or value['triggers']!=0 or value['actions']!=1 or value['action_type']!=0
        or not all(value[k] for k in ('executable_matches','arguments_match','working_directory_matches'))):
        raise ValueError('Original failed task is not the expected terminal bound task')
    return value


def read_control(path, expected, win, maximum=1048576):
    win.validate_code_path(path)
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or getattr(before,'st_file_attributes',0)&0x400 or before.st_size>maximum:
        raise ValueError('Control artifact is not bounded ordinary single-link data')
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        raw=stream.read(maximum+1)
        finished=os.fstat(stream.fileno())
    after=path.lstat()
    identity=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    if not identity(before)==identity(opened)==identity(finished)==identity(after):raise ValueError('Control artifact identity changed')
    if len(raw)>maximum or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Control artifact pin differs')
    return raw


def stopped_daemons(win):
    raw=win._powershell(r'''
      $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');$rows=@();
      foreach($name in $data){
        $task=$null;try{$task=$folder.GetTask($name)}catch{
          $missing=$false;$e=$_.Exception;while($null -ne $e){if($e.HResult -eq -2147024894){$missing=$true;break};$e=$e.InnerException}
          if(-not $missing){throw 'Daemon state unknown.'}
        }
        if($null -eq $task){$rows+=@([ordered]@{name=$name;present=$false});continue}
        $instances=[int]$task.GetInstances(0).Count
        if($task.Enabled -or $task.State -notin @(1,3) -or $instances -ne 0){throw 'Protected daemon is not stopped and disabled.'}
        $rows+=@([ordered]@{name=$name;present=$true;state=[int]$task.State;enabled=[bool]$task.Enabled;instances=$instances})
      };ConvertTo-Json -InputObject $rows -Compress
    ''',['CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor'])
    return strict_json(raw)


def inspect_synthetic_directories(root, win, inspect):
    records=[]
    for name,mode in (('fixture-inherited',None),('fixture-mode700',0o700)):
        path=root/name
        if mode is None:path.mkdir(exist_ok=False)
        else:path.mkdir(mode=mode,exist_ok=False)
        row={'relative':name,'mode_argument':mode,'created_empty_in_diagnostic_root':True,'metadata':inspect(path)}
        try:win.validate_private_directory(path);row['installed_validator']='PASSED'
        except BaseException as error:row['installed_validator']='REJECTED';row['failure']=safe_error(error)
        records.append(row)
    return records


def run(nonce,receipt_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    if (Path(__file__).resolve()!=ROOT/'diagnose-knowledge-state.py' or Path(sys.executable).resolve()!=INSTALL/'.venv/Scripts/python.exe'
        or Path(sys.base_prefix).resolve()!=BASE or Path(sys._base_executable).resolve()!=BASE/'python.exe'
        or sys.version_info[:3]!=(3,12,13) or not sys.flags.isolated or not sys.dont_write_bytecode):raise ValueError('Protected diagnostic runtime differs')
    ordinary_tree(ROOT);win.validate_private_directory(ROOT);win.validate_code_path(__file__)
    report={'schema':'cochem-knowledge-state-diagnostic/1','nonce':nonce,'system_sid':win.SYSTEM_SID,
            'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'status':'UNVERIFIED',
            'started_at_unix_ms':int(time.time()*1000),'index_contents_read_or_hashed':False,
            'knowledge_service_constructed':False,'index_refreshed':False,'existing_acl_or_files_modified':False,
            'original_task_modified_or_run':False,'native_model_jobs_executed':0,'activation_ready':False}
    phase='runtime_binding'
    with (ROOT/'diagnostic.json').open('x',encoding='utf-8') as output:
        try:
            read_control(INSTALL/'.venv/pyvenv.cfg','d6ebb0d905488486e5a438a8a30a589f256f3499515baf1dd9d4ab71693b5f95',win)
            report['module_pins']={}
            for name,pin in PINS.items():
                read_control(Path(win.__file__).parent/name,pin,win)
                report['module_pins'][name]=pin
            phase='original_binding'
            read_control(INSTALL/'pipeline.json',CONFIG,win)
            read_control(ORIGINAL/'accept-private-knowledge.py',ORIGINAL_HELPER,win)
            read_control(ORIGINAL/'private-install-inventory.json',INVENTORY,win)
            original=check_original(strict_json(read_control(ORIGINAL/'knowledge-acceptance.json',receipt_sha,win,32768)))
            report['original_receipt']=original;report['original_receipt_sha256']=receipt_sha
            report['original_task']=task_evidence(win)
            phase='daemon_precheck'
            report['daemons_before']=stopped_daemons(win)
            phase='state_metadata'
            ordinary_tree(STATE.parent);win.validate_private_directory(STATE.parent)
            report['state_entries']=inspect_state(STATE,lambda path:metadata(path,win))
            report['state_entry_count']=len(report['state_entries'])
            phase='new_root_synthetic_fixtures'
            report['synthetic_directories']=inspect_synthetic_directories(ROOT,win,lambda path:metadata(path,win))
            report['synthetic_directories_created']=2
            phase='daemon_postcheck'
            report['daemons_after']=stopped_daemons(win)
            report['daemon_states_verified_before_and_after']=True
            report['status']='METADATA_DIAGNOSTIC_COMPLETE'
        except BaseException as error:
            report['status']='DIAGNOSTIC_HELD';report['failure']={'phase':phase,**safe_error(error)}
        finally:
            report['finished_at_unix_ms']=int(time.time()*1000)
            json.dump(report,output,sort_keys=True,indent=2);output.flush();os.fsync(output.fileno())
    return 0 if report['status']=='METADATA_DIAGNOSTIC_COMPLETE' else 2


if __name__=='__main__':
    if len(sys.argv)!=3 or not re.fullmatch('[a-f0-9]{32}',sys.argv[1]) or not re.fullmatch('[a-f0-9]{64}',sys.argv[2]):raise SystemExit(3)
    try:raise SystemExit(run(sys.argv[1],sys.argv[2]))
    except Exception:raise SystemExit(3)
