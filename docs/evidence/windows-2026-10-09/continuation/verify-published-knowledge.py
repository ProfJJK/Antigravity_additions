"""Read-only validation of one already-published, hash-bound knowledge index.

Creates only its own new verification receipt. No KnowledgeService constructor,
refresh, index repair, corpus provisioning, ACL change, or existing task change.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
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

ROOT=Path(r'C:\Program Files\CoChem\KnowledgePublishedVerification4.2.7-windows-20261007-r2')
FAILED_SHA='8cdaebaaf9338046ef928368160f9ea7eff792c22d0f46a3a23820a78c94e66b'
SUPPORT={'resume-private-knowledge.py':'780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b',
         'diagnose-knowledge-state.py':'e799d3d932a42af251c5ea752530eb9bcee022196d0c22db60c75e481e67da87',
         'accept-private-knowledge.py':'832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca'}


def read_code(path,pin,win,maximum=1048576):
    win.validate_code_path(path)
    before=path.lstat()
    if before.st_size>maximum or before.st_nlink!=1 or not path.is_file() or getattr(before,'st_file_attributes',0)&0x400:
        raise ValueError('Control is not bounded ordinary single-link data')
    identity=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns)
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno());raw=stream.read(maximum+1);finished=os.fstat(stream.fileno())
    if not identity(before)==identity(opened)==identity(finished)==identity(path.lstat()):raise ValueError('Control identity changed')
    if len(raw)>maximum or hashlib.sha256(raw).hexdigest()!=pin:raise ValueError('Control pin differs')
    return raw


def support(name,win):
    raw=read_code(ROOT/name,SUPPORT[name],win)
    namespace={'__name__':'readonly_support_'+name.replace('-','_'),'__file__':str(ROOT/name)}
    exec(compile(raw,str(ROOT/name),'exec'),namespace)
    return SimpleNamespace(**namespace)


def failed_binding(value,R,D,A):
    expected={'schema':'cochem-private-knowledge-resume/1','status':'KNOWLEDGE_RESUME_HELD','system_sid':'S-1-5-18',
      'helper_sha256':SUPPORT['resume-private-knowledge.py'],'runtime_root':str(R.INSTALL),
      'install_receipt_sha256':R.INSTALL_RECEIPT_SHA,'original_receipt_sha256':R.ORIGINAL_SHA,
      'diagnostic_receipt_sha256':R.DIAGNOSTIC_SHA,'source_manifest_sha256':R.MANIFEST_SHA,
      'source_files_verified':166,'pipeline_config_sha256':R.CONFIG_SHA,'payload_inventory_sha256':D.INVENTORY,
      'corpus_manifest_sha256':A.MANIFEST_SHA,'source_pins_sha256':A.SOURCE_PINS_SHA,
      'documents':137,'sections':1708,'corpus_files':138,'source_bytes_preserved':True,
      'canonical_authority_matches_capture':True,'index_size_sla_met':True,'index_integrity_check':'ok',
      'index_resume_started':True,'native_model_jobs_executed':0,'existing_acl_modified':False,
      'corpus_reprovisioned':False,'old_tasks_modified_or_run':False,'configuration_modified':False,
      'budgets_modified':False,'daemon_started':False,'activation_ready':False}
    if not isinstance(value,dict) or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in expected.items()):
        raise ValueError('Failed resume lacks complete successful publication commitments')
    if (value.get('failure')!={'phase':'final_verification','error_type':'ValueError','winerror':None}
       or value.get('module_pins')!=R.PINS or not re.fullmatch('[a-f0-9]{32}',str(value.get('nonce','')))
       or not re.fullmatch('g-[a-f0-9]{32}',str(value.get('generation','')))
       or not re.fullmatch('[a-f0-9]{64}',str(value.get('index_sha256','')))
       or type(value.get('corpus_bytes')) is not int or not 0<value['corpus_bytes']<=134217728
       or type(value.get('index_bytes')) is not int or not 0<value['index_bytes']<=4*value['corpus_bytes']
       or type(value.get('index_ratio')) not in (int,float) or value['index_ratio']!=value['index_bytes']/value['corpus_bytes']):
        raise ValueError('Published generation or final failure binding differs')
    return value


def preserved_root(baseline,current):
    # NTFS directory stream/allocation size changes as entries are published.
    # Keep every identity/security/creation/attribute/link field exact.
    dynamic={'relative','state','metadata_sha256','written_filetime','bytes'}
    difference={key:{'before':old,'after':current.get(key)} for key,old in baseline.items()
                if key not in {'relative','state'} and current.get(key)!=old}
    if any(current.get(key)!=old for key,old in baseline.items() if key not in dynamic):
        raise ValueError('Original state directory identity or security changed')
    if type(current.get('bytes')) is not int or current['bytes']<0:raise ValueError('Invalid directory size metadata')
    return difference


def exact_shape(state,generation):
    if not re.fullmatch('g-[a-f0-9]{32}',generation):raise ValueError('Invalid generation')
    def names(path,maximum):
        found=set()
        with os.scandir(path) as entries:
            for item in entries:
                found.add(item.name)
                if len(found)>maximum:raise ValueError('Unexpected published state entry')
        return found
    if names(state,4)!={'writer.lock','current.json','sources.json',generation}:
        raise ValueError('Published state is not the exact single-generation shape')
    if names(state/generation,1)!={'knowledge_index.db'}:raise ValueError('Unexpected journal, sidecar or generation entry')


@contextmanager
def held_private_bytes(path,maximum,win,D,expected=None):
    """Read-only Win32 handle denies concurrent write/delete; never creates."""
    import msvcrt
    win.validate_private_path(path)
    before=D.metadata(path,win)
    if before['directory'] or before['reparse'] or before['links']!=1 or before['bytes']>maximum:
        raise ValueError('Published file is not bounded ordinary single-link data')
    kernel=win._api()['kernel32']
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        win._close(handle);raise
    with os.fdopen(descriptor,'rb') as stream:
        opened=os.fstat(stream.fileno());current=path.lstat()
        if (opened.st_dev,opened.st_ino,opened.st_size)!=(current.st_dev,current.st_ino,current.st_size):
            raise ValueError('Published path and held file identity differ')
        if D.metadata(path,win)!=before:raise ValueError('Published file metadata changed before read')
        raw=stream.read(maximum+1)
        digest=hashlib.sha256(raw).hexdigest()
        if len(raw)>maximum or expected is not None and digest!=expected:raise ValueError('Published file pin differs')
        yield raw,digest,before
        if D.metadata(path,win)!=before:raise ValueError('Published file metadata changed while held')


def pointer_binding(value,failed,manifest_sha):
    expected={'generation':failed['generation'],'manifest_sha256':manifest_sha,'document_count':137,
      'section_count':1708,'corpus_bytes':failed['corpus_bytes'],'index_bytes':failed['index_bytes'],
      'index_ratio':failed['index_ratio'],'index_size_sla_met':True}
    allowed=set(expected)|{'relative_links_checked','updated_at'}
    if not isinstance(value,dict) or set(value)!=allowed or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in expected.items()):
        raise ValueError('Current pointer differs from committed published index')
    if type(value['relative_links_checked']) is not int or value['relative_links_checked']<0 or type(value['updated_at']) not in (int,float) or value['updated_at']<=0:
        raise ValueError('Current pointer metadata invalid')


def verify_database(path,catalog,captured,failed):
    """Fixed immutable read-only URI. No model, corpus text, or SQLite writes."""
    database=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        database.execute('PRAGMA query_only=ON');database.execute('PRAGMA temp_store=MEMORY')
        deadline=time.monotonic()+60
        database.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        if database.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('Published index integrity failed')
        if database.execute('SELECT count(*) FROM documents').fetchone()[0]!=137 or database.execute('SELECT count(*) FROM fts_index').fetchone()[0]!=1708:
            raise ValueError('Published index counts differ')
        rows=database.execute('SELECT doc_path,sha256,authority,authority_revision FROM documents LIMIT 138').fetchall()
        actual={path:(sha,authority,revision) for path,sha,authority,revision in rows}
        expected={item['path']:(item['sha256'],item.get('authority','unclassified'),item.get('authority_revision','')) for item in catalog['documents']}
        if len(rows)!=137 or len(actual)!=137 or actual!=expected:raise ValueError('Published document authority rows differ from exact corpus catalog')
        bindings=[(captured['specification_sha256'],'current_normative',captured['specification_revision'])]
        bindings.extend((item['sha256'],'owner_decision',item['revision']) for item in captured['owner_amendments'])
        resolved=[]
        for pin,authority,revision in bindings:
            selected=database.execute('SELECT doc_path FROM documents WHERE substr(doc_path,1,9)=? AND sha256=? AND authority=? AND authority_revision=? LIMIT 2',('.sources/',pin,authority,revision)).fetchall()
            if len(selected)!=1 or not selected[0][0].startswith('.sources/'):
                raise ValueError('Canonical source authority is not unique and captured')
            resolved.append(selected[0][0])
        if resolved[0]!=failed['canonical_source'] or resolved[1:]!=failed['owner_amendment_sources']:
            raise ValueError('Canonical source resolution differs from failed receipt commitments')
        return {'documents':137,'sections':1708,'integrity_check':'ok','canonical_source':resolved[0],
                'owner_amendment_sources':resolved[1:],'canonical_authority_matches_capture':True,
                'sqlite_mode':'ro','sqlite_immutable':True,'sqlite_query_only':True,'sqlite_temp_store':'memory'}
    finally:database.close()


def check_resume_task(win,R,failed):
    raw=win._powershell(r'''
      $s=New-Object -ComObject 'Schedule.Service';$s.Connect();$t=$s.GetFolder('\').GetTask('CoChem-4.2.7-KnowledgeResume-20261007-r2');
      $d=$t.Definition;$a=$d.Actions.Item(1);
      if($t.State -notin @(1,3) -or $t.GetInstances(0).Count -ne 0 -or $t.LastTaskResult -ne 2 -or
         $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
         $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or $a.Type -ne 0 -or $a.Path -cne $data.python -or
         $a.Arguments -cne $data.arguments -or $a.WorkingDirectory -cne $data.root){throw 'Failed resume task binding differs.'}
      'VERIFIED'
    ''',{'python':str(R.INSTALL/'.venv/Scripts/python.exe'),'root':str(R.ROOT),
         'arguments':f'-I -B "{R.ROOT / "resume-private-knowledge.py"}" {failed["nonce"]}'})
    if raw.strip()!='VERIFIED':raise ValueError('Failed resume task not verified')


def run(nonce):
    from cochem_pipeline import windows as win,knowledge
    from cochem_pipeline.planning_governance import canonical_authority
    from cochem_pipeline.deployment_revision import verify_installed_revision
    win.require_system();win.validate_private_directory(ROOT);win.validate_code_path(__file__)
    if Path(__file__).resolve()!=ROOT/'verify-published-knowledge.py':raise ValueError('Use protected verification helper')
    R=support('resume-private-knowledge.py',win);D=support('diagnose-knowledge-state.py',win);A=support('accept-private-knowledge.py',win)
    if (Path(sys.executable).resolve()!=R.INSTALL/'.venv/Scripts/python.exe' or Path(sys.base_prefix).resolve()!=R.BASE
        or Path(sys._base_executable).resolve()!=R.BASE/'python.exe' or sys.version_info[:3]!=(3,12,13)
        or not sys.flags.isolated or not sys.dont_write_bytecode):raise ValueError('Protected r2 runtime binding differs')
    report={'schema':'cochem-published-knowledge-verification/1','nonce':nonce,'system_sid':win.SYSTEM_SID,
       'status':'UNVERIFIED','helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
       'failed_resume_receipt_sha256':FAILED_SHA,'started_at_unix_ms':int(time.time()*1000),
       'knowledge_service_constructed':False,'index_refreshed_or_repaired':False,'existing_files_or_acls_modified':False,
       'existing_tasks_modified_or_run':False,'native_model_jobs_executed':0,'activation_ready':False,'substep':'trusted_preflight'}
    with (ROOT/'published-verification.json').open('x',encoding='utf-8') as output:
      try:
        report['substep']='runtime.control_pins'
        for path,pin,bound in ((R.INSTALL/'.venv/pyvenv.cfg',R.VENV_SHA,8192),(Path(sys.executable),R.PYTHON_SHA,1048576),(R.BASE/'python.exe',R.BASE_SHA,1048576)):
            read_code(path,pin,win,bound)
        for name,pin in R.PINS.items():read_code(Path(win.__file__).parent/name,pin,win)
        installed=R.strict_json(read_code(R.INSTALL/'install-after.json',R.INSTALL_RECEIPT_SHA,win));R.check_install_receipt(installed)
        manifest=R.strict_json(read_code(R.INSTALL/'source-manifest.json',R.MANIFEST_SHA,win))
        R.verify_source_manifest(manifest,lambda p,h,n:read_code(p,h,win,n))
        revision=verify_installed_revision(R.INSTALL/'source',R.INSTALL/'.venv/Lib/site-packages',R.INSTALL/'source');R.assert_revision_matches(revision,installed)
        report.update(runtime_root=str(R.INSTALL),install_receipt_sha256=R.INSTALL_RECEIPT_SHA,module_pins=R.PINS,
                      source_manifest_sha256=R.MANIFEST_SHA,source_files_verified=166,revision=revision)
        report['substep']='evidence.failed_resume'
        failed=failed_binding(R.strict_json(read_code(R.ROOT/'resume-acceptance.json',FAILED_SHA,win,131072)),R,D,A)
        if failed['revision']!=revision:raise ValueError('Failed receipt revision commitment differs')
        report.update(failed_resume_nonce=failed['nonce'],generation=failed['generation'],index_sha256=failed['index_sha256'])
        report['substep']='evidence.original_and_diagnostic'
        D.check_original(R.strict_json(read_code(R.ORIGINAL/'knowledge-acceptance.json',R.ORIGINAL_SHA,win,32768)))
        diagnostic=R.strict_json(read_code(R.DIAGNOSTIC/'diagnostic.json',R.DIAGNOSTIC_SHA,win,131072))
        baseline=R.assert_blank(diagnostic['state_entries'])
        D.task_evidence(win);R.diagnostic_task(win);check_resume_task(win,R,failed)
        report['substep']='configuration.exact_r2_delta'
        old=read_code(R.OLD_INSTALL/'pipeline.json',A.CONFIG_SHA,win);new=read_code(R.INSTALL/'pipeline.json',R.CONFIG_SHA,win)
        R.assert_config_delta(old,new);configuration=R.strict_json(new)
        if configuration['knowledge']['state_root']!=str(R.STATE):raise ValueError('Knowledge state differs')
        report['pipeline_config_sha256']=R.CONFIG_SHA
        report['substep']='corpus.all_138_files_before'
        inventory=R.strict_json(read_code(R.ORIGINAL/'private-install-inventory.json',D.INVENTORY,win))
        win.validate_private_directory(R.CORPUS)
        private_ordinary=lambda path,directory=False:knowledge._protected(path,directory=directory,private=True)
        corpus_before=A.verify_inventory(inventory,R.CORPUS,private_ordinary)
        catalog=R.strict_json(read_code(R.CORPUS/'v4.1.2_manifest.json',A.MANIFEST_SHA,win))
        report['daemons_before']=D.stopped_daemons(win)
        report['substep']='state.exact_shape_before'
        win.validate_private_directory(R.STATE);win.validate_private_directory(R.STATE/failed['generation'])
        exact_shape(R.STATE,failed['generation'])
        report['substep']='state.root_stable_metadata_before'
        root_before=D.metadata(R.STATE,win);report['root_metadata_before']=root_before
        report['root_difference_from_blank_diagnostic']=preserved_root(baseline['state'],root_before)
        report['substep']='state.writer_lock_metadata_before'
        lock_before=D.metadata(R.STATE/'writer.lock',win);report['writer_lock_metadata_before']=lock_before
        if (lock_before['file_identity'],lock_before['metadata_sha256'])!=R.BASELINE['state/writer.lock']:raise ValueError('Original writer lock metadata differs')
        with ExitStack() as stack:
            report['substep']='state.held_writer_lock_byte'
            stack.enter_context(held_private_bytes(R.STATE/'writer.lock',1,win,D,hashlib.sha256(b'0').hexdigest()))
            report['substep']='state.held_current_pointer'
            pointer_raw,pointer_sha,_=stack.enter_context(held_private_bytes(R.STATE/'current.json',16384,win,D))
            pointer_binding(R.strict_json(pointer_raw),failed,A.MANIFEST_SHA)
            report['substep']='state.held_source_pins'
            pins_raw,pins_sha,_=stack.enter_context(held_private_bytes(R.STATE/'sources.json',1048576,win,D,A.SOURCE_PINS_SHA))
            pins=R.strict_json(pins_raw)
            if pins!={item['path']:item['sha256'] for item in catalog['documents'] if item['path'].startswith('.sources/')}:
                raise ValueError('Source ledger differs from physical corpus catalog')
            report['substep']='state.held_index_hash'
            index=R.STATE/failed['generation']/'knowledge_index.db'
            index_raw,index_sha,_=stack.enter_context(held_private_bytes(index,134217728,win,D,failed['index_sha256']))
            if len(index_raw)!=failed['index_bytes']:raise ValueError('Index byte length differs')
            del index_raw
            report['substep']='index.immutable_sqlite_and_canonical_authority'
            report.update(verify_database(index,catalog,canonical_authority(),failed))
            report.update(current_pointer_sha256=pointer_sha,source_pins_sha256=pins_sha,index_sha256=index_sha,
                          index_bytes=failed['index_bytes'],corpus_bytes=failed['corpus_bytes'],index_ratio=failed['index_ratio'])
            report['substep']='corpus.all_138_files_after'
            if A.verify_inventory(inventory,R.CORPUS,private_ordinary)!=corpus_before:raise ValueError('Corpus changed during verification')
            report['substep']='state.exact_shape_and_metadata_after'
            exact_shape(R.STATE,failed['generation'])
            root_after=D.metadata(R.STATE,win);lock_after=D.metadata(R.STATE/'writer.lock',win)
            if root_before!=root_after or lock_before!=lock_after:raise ValueError('State metadata changed during read-only verification')
            report.update(root_metadata_after=root_after,writer_lock_metadata_after=lock_after)
            report['substep']='configuration.final_pin'
            read_code(R.INSTALL/'pipeline.json',R.CONFIG_SHA,win)
            report['substep']='tasks.original_diagnostic_resume_final'
            D.task_evidence(win);R.diagnostic_task(win);check_resume_task(win,R,failed)
            report['substep']='tasks.daemons_final'
            report['daemons_after']=D.stopped_daemons(win)
            report['substep']='state.held_file_final_metadata'
        report.update(status='PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',substep='complete',corpus_files=138,
                      corpus_bytes_preserved=True,original_root_identity_and_security_preserved=True,
                      original_writer_lock_preserved=True,index_size_sla_met=True)
      except BaseException as error:
        report.update(status='PUBLISHED_KNOWLEDGE_VERIFICATION_HELD',failure={'substep':report['substep'],**D.safe_error(error)},operator_review_required=True)
      finally:
        report['finished_at_unix_ms']=int(time.time()*1000)
        json.dump(report,output,sort_keys=True,indent=2);output.flush();os.fsync(output.fileno())
    return 0 if report['status']=='PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED' else 2


if __name__=='__main__':
    if len(sys.argv)!=2 or not re.fullmatch('[a-f0-9]{32}',sys.argv[1]):raise SystemExit(3)
    try:raise SystemExit(run(sys.argv[1]))
    except Exception:raise SystemExit(3)
