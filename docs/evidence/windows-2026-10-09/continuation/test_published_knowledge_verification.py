"""Disposable ordinary-user Windows tests; no production KB access or SYSTEM."""
import copy
from contextlib import closing,ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).parent
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
V=load('verify-published-knowledge');R=load('resume-private-knowledge')
D=load('diagnose-knowledge-state');A=load('accept-private-knowledge')


def failed():
    return {'schema':'cochem-private-knowledge-resume/1','status':'KNOWLEDGE_RESUME_HELD','system_sid':'S-1-5-18',
      'helper_sha256':V.SUPPORT['resume-private-knowledge.py'],'runtime_root':str(R.INSTALL),
      'install_receipt_sha256':R.INSTALL_RECEIPT_SHA,'original_receipt_sha256':R.ORIGINAL_SHA,
      'diagnostic_receipt_sha256':R.DIAGNOSTIC_SHA,'source_manifest_sha256':R.MANIFEST_SHA,
      'source_files_verified':166,'pipeline_config_sha256':R.CONFIG_SHA,'payload_inventory_sha256':D.INVENTORY,
      'corpus_manifest_sha256':A.MANIFEST_SHA,'source_pins_sha256':A.SOURCE_PINS_SHA,
      'documents':137,'sections':1708,'corpus_files':138,'source_bytes_preserved':True,
      'canonical_authority_matches_capture':True,'index_size_sla_met':True,'index_integrity_check':'ok',
      'index_resume_started':True,'native_model_jobs_executed':0,'existing_acl_modified':False,
      'corpus_reprovisioned':False,'old_tasks_modified_or_run':False,'configuration_modified':False,
      'budgets_modified':False,'daemon_started':False,'activation_ready':False,
      'failure':{'phase':'final_verification','error_type':'ValueError','winerror':None},
      'module_pins':R.PINS,'nonce':'a'*32,'generation':'g-'+'b'*32,'index_sha256':'c'*64,
      'corpus_bytes':1000,'index_bytes':2000,'index_ratio':2.0,
      'canonical_source':'.sources/canonical.md','owner_amendment_sources':['.sources/owner.md']}


def test_exact_failed_publication_can_be_bound_without_reindexing():
    value=failed();assert V.failed_binding(value,R,D,A) is value


@pytest.mark.parametrize('field,value',[
    ('nonce','bad'),('generation','../other'),('index_sha256','bad'),('sections',1707),('documents',136),
    ('module_pins',{}),('failure',{'phase':'index_resume','error_type':'ValueError','winerror':None}),
    ('source_bytes_preserved',1),('index_ratio',1.0),('activation_ready',True),('index_bytes',5000),
    ('install_receipt_sha256','0'*64),('pipeline_config_sha256','0'*64),('source_manifest_sha256','0'*64)])
def test_incomplete_or_changed_failed_commitment_refused(field,value):
    record=failed();record[field]=value
    with pytest.raises(ValueError):V.failed_binding(record,R,D,A)


def actual_windows():
    sys.path.insert(0,r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\src')
    from cochem_pipeline import windows
    return windows


def test_real_ntfs_directory_growth_bug_and_narrow_readonly_comparator(tmp_path,monkeypatch):
    win=actual_windows();state=tmp_path/'state';state.mkdir();lock=state/'writer.lock';lock.write_bytes(b'0')
    root_before=D.metadata(state,win);lock_before=D.metadata(lock,win)
    baseline={'relative':'state','state':'OBSERVED',**root_before}
    generation='g-'+'d'*32;(state/generation).mkdir()
    (state/'current.json').write_text('{}');(state/'sources.json').write_text('{}')
    root_after=D.metadata(state,win);lock_after=D.metadata(lock,win)
    assert root_before['bytes']==0 and root_after['bytes']==4096
    assert lock_before==lock_after
    monkeypatch.setitem(R.BASELINE,'state/writer.lock',(lock_before['file_identity'],lock_before['metadata_sha256']))
    with pytest.raises(ValueError):R.assert_preserved({'state':baseline},root_after,lock_after)
    changes=V.preserved_root(baseline,root_after)
    assert set(changes)<= {'bytes','written_filetime','metadata_sha256'} and 'bytes' in changes
    for field in ('owner_sid','file_identity','created_filetime','attributes','links','aces','reparse'):
        bad=dict(root_after);bad[field]='changed'
        with pytest.raises(ValueError):V.preserved_root(baseline,bad)


@pytest.mark.parametrize('extra',['knowledge_index.db-wal','knowledge_index.db-shm','knowledge_index.db-journal','extra'])
def test_no_extra_journal_or_sidecar_is_accepted(tmp_path,extra):
    generation='g-'+'a'*32;folder=tmp_path/generation;folder.mkdir()
    for name in ('writer.lock','current.json','sources.json'):(tmp_path/name).write_bytes(b'0')
    (folder/'knowledge_index.db').write_bytes(b'fixture');V.exact_shape(tmp_path,generation)
    (folder/extra).write_bytes(b'preserve')
    with pytest.raises(ValueError):V.exact_shape(tmp_path,generation)
    assert (folder/extra).read_bytes()==b'preserve'


def database_fixture(path):
    catalog={'documents':[
      {'path':'.sources/canonical.md','sha256':'1'*64,'authority':'current_normative','authority_revision':'r1'},
      {'path':'.sources/owner.md','sha256':'2'*64,'authority':'owner_decision','authority_revision':'r2'}]}
    catalog['documents'] += [{'path':f'wiki/{n}.md','sha256':'3'*64,'authority':'unclassified','authority_revision':''} for n in range(135)]
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE documents(doc_path TEXT,sha256 TEXT,authority TEXT,authority_revision TEXT)')
        db.executemany('INSERT INTO documents VALUES(?,?,?,?)',[(d['path'],d['sha256'],d['authority'],d['authority_revision']) for d in catalog['documents']])
        db.execute('CREATE VIRTUAL TABLE fts_index USING fts5(content)')
        db.executemany('INSERT INTO fts_index(content) VALUES(?)',[('disposable fixture',)]*1708)
        db.commit()
    captured={'specification_sha256':'1'*64,'specification_revision':'r1','owner_amendments':[{'sha256':'2'*64,'revision':'r2'}]}
    return catalog,captured


def test_real_windows_held_read_only_index_allows_immutable_sqlite_and_blocks_writes(tmp_path,monkeypatch):
    win=actual_windows();path=tmp_path/'knowledge_index.db';catalog,captured=database_fixture(path)
    # Explicit ordinary-user permission fixture; actual file access masks,
    # metadata identity, sharing exclusion and SQLite read-only URI remain real.
    monkeypatch.setattr(win,'validate_private_path',lambda _:None)
    before=path.read_bytes();sha=hashlib.sha256(before).hexdigest();metadata=D.metadata(path,win)
    for name,raw in [('writer.lock',b'0'),('current.json',b'{}'),('sources.json',b'{}')]:
        (tmp_path/name).write_bytes(raw)
    control_before={name:D.metadata(tmp_path/name,win) for name in ('writer.lock','current.json','sources.json')}
    with ExitStack() as stack:
        for name in control_before:stack.enter_context(V.held_private_bytes(tmp_path/name,16384,win,D))
        raw,digest,observed=stack.enter_context(V.held_private_bytes(path,134217728,win,D,sha))
        assert raw==before and digest==sha and observed==metadata
        with pytest.raises(PermissionError):
            with path.open('r+b'):pass
        with pytest.raises(PermissionError):path.unlink()
        value=V.verify_database(path,catalog,captured,failed())
        assert value['canonical_authority_matches_capture'] is True
        assert value['sqlite_immutable'] and value['sqlite_query_only']
    assert path.read_bytes()==before and D.metadata(path,win)==metadata
    assert {name:D.metadata(tmp_path/name,win) for name in control_before}==control_before
    assert {p.name for p in tmp_path.iterdir()}=={'knowledge_index.db','writer.lock','current.json','sources.json'}


@pytest.mark.parametrize('kind',['wrong_authority','different_capture','different_receipt','document_hash'])
def test_readonly_canonical_mismatch_does_not_repair_index(tmp_path,kind):
    path=tmp_path/'knowledge_index.db';catalog,captured=database_fixture(path);record=failed()
    if kind=='wrong_authority':catalog['documents'][0]['authority']='historical'
    elif kind=='different_capture':captured['specification_sha256']='f'*64
    elif kind=='different_receipt':record['canonical_source']='.sources/other.md'
    else:catalog['documents'][0]['sha256']='e'*64
    before=path.read_bytes()
    with pytest.raises(ValueError):V.verify_database(path,catalog,captured,record)
    assert path.read_bytes()==before
    assert {p.name for p in tmp_path.iterdir()}=={'knowledge_index.db'}


def test_pointer_commits_to_the_failed_generation_and_exact_sizes():
    record=failed();pointer={'generation':record['generation'],'manifest_sha256':A.MANIFEST_SHA,
      'document_count':137,'section_count':1708,'corpus_bytes':1000,'index_bytes':2000,'index_ratio':2.0,
      'index_size_sla_met':True,'relative_links_checked':10,'updated_at':123.0}
    V.pointer_binding(pointer,record,A.MANIFEST_SHA)
    for field,value in [('generation','g-'+'c'*32),('index_bytes',2001),('section_count',1707),('index_size_sla_met',1)]:
        bad=dict(pointer);bad[field]=value
        with pytest.raises(ValueError):V.pointer_binding(bad,record,A.MANIFEST_SHA)


def test_source_does_not_construct_knowledge_service_or_refresh_or_mutate_old_state():
    import ast
    tree=ast.parse((ROOT/'verify-published-knowledge.py').read_text())
    forbidden={'KnowledgeService','refresh','provision_knowledge','mkdir','unlink','rmdir','chmod','SetNamedSecurityInfoW'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
            assert name not in forbidden
            if name=='replace':assert not isinstance(node.func.value,ast.Name) or node.func.value.id!='os'
    for name,pin in V.SUPPORT.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==pin


def test_existing_disposable_137_document_candidate_pointer_and_sqlite_are_read_only(monkeypatch):
    stage=Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\knowledge-continuation-candidate-20261007T053341Z')
    state=stage/'validation/knowledge-index';corpus=stage/'corpus'
    win=actual_windows();monkeypatch.setattr(win,'validate_private_path',lambda _:None)
    from cochem_pipeline.planning_governance import canonical_authority
    evidence=json.loads((stage/'validation-evidence.json').read_bytes())
    pointer=json.loads((state/'current.json').read_bytes())
    record=failed()
    record.update(generation=pointer['generation'],index_sha256=evidence['index_sha256'],
                  index_bytes=pointer['index_bytes'],corpus_bytes=pointer['corpus_bytes'],index_ratio=pointer['index_ratio'],
                  canonical_source=evidence['canonical_source'],owner_amendment_sources=evidence['owner_amendment_sources'])
    assert record['index_sha256']=='86ef64a73763002ad32f00e393609a147f86a6f30f38381c13c3285676dca578'
    catalog=json.loads((corpus/'v4.1.2_manifest.json').read_bytes())
    paths=[state/'writer.lock',state/'current.json',state/'sources.json',state/record['generation']/'knowledge_index.db']
    before={str(p):D.metadata(p,win) for p in [state,*paths]}
    with ExitStack() as stack:
        stack.enter_context(V.held_private_bytes(paths[0],1,win,D,hashlib.sha256(b'0').hexdigest()))
        raw,_,_=stack.enter_context(V.held_private_bytes(paths[1],16384,win,D))
        V.pointer_binding(json.loads(raw),record,A.MANIFEST_SHA)
        stack.enter_context(V.held_private_bytes(paths[2],1048576,win,D,A.SOURCE_PINS_SHA))
        stack.enter_context(V.held_private_bytes(paths[3],134217728,win,D,record['index_sha256']))
        result=V.verify_database(paths[3],catalog,canonical_authority(),record)
        assert result['documents']==137 and result['sections']==1708 and result['integrity_check']=='ok'
    assert {str(p):D.metadata(p,win) for p in [state,*paths]}==before
