"""Isolated ordinary Windows memory experiment over generated fixture bytes.

No production service, credentials, databases, task state or hardware devices
are opened. Permission checks are explicitly substituted only for this fixture.
This is causal fixture evidence, never deployment acceptance.
"""
from contextlib import ExitStack
from pathlib import Path
import hashlib
import json
import os
import sys
import threading
import time
import tracemalloc
import uuid

HERE=Path(__file__).absolute().parent
INSTALL=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
assert Path(sys.executable)==INSTALL/'.venv/Scripts/python.exe'
assert sys.version_info[:3]==(3,12,13) and sys.flags.isolated and sys.dont_write_bytecode
from cochem_pipeline import knowledge as k
from cochem_pipeline.knowledge_authority import KnowledgeAuthority
import psutil

root=HERE/('knowledge-memory-fixture-20261009-'+uuid.uuid4().hex)
root.mkdir()
corpus=root/'corpus';corpus.mkdir()
sources=corpus/'.sources';sources.mkdir()
wiki=corpus/'wiki';wiki.mkdir()
private=root/'private';private.mkdir()

def fixture_protected(path,*,directory=False,private=False):
    path=Path(path)
    assert path==root or root in path.parents
    return k._ordinary(path,directory=directory)

def fixture_private(path,*,exist_ok=False):
    path=Path(path)
    assert root in path.parents
    path.mkdir(mode=0o700,exist_ok=exist_ok)

def fixture_ancestors(path):
    assert Path(path)==root or root in Path(path).parents
    k._ordinary(Path(path),directory=True)

k._protected=fixture_protected;k._private_directory=fixture_private;k._ancestors=fixture_ancestors
documents=[]
for index in range(137):
    path=f'.sources/fixture-{index:03d}.md'
    text=f'# Fixture {index}\n'+''.join(f'## Section {n}\n'+('synthetic immutable knowledge test data '*32)+'\n' for n in range(12))
    raw=text.encode('utf-8')
    (corpus/path).write_bytes(raw)
    row={'path':path,'sha256':hashlib.sha256(raw).hexdigest()}
    if index==0:row.update(authority='current_normative',authority_revision='fixture-v1')
    elif index==1:row.update(authority='owner_decision',authority_revision='fixture-owner-v1')
    documents.append(row)
manifest=corpus/'v4.1.2_manifest.json'
manifest.write_text(json.dumps({'documents':documents}),encoding='utf-8')
config=k.KnowledgeConfig(enabled=True,source_root=str(sources),wiki_root=str(wiki),
                         state_root=str(private/'knowledge'),manifest_path=str(manifest))
service=k.KnowledgeService(config)
authority=KnowledgeAuthority(service,{'specification_sha256':documents[0]['sha256'],
                                     'specification_revision':'fixture-v1',
                                     'owner_amendments':[{'source':'fixture-owner.md','sha256':documents[1]['sha256'],
                                                         'revision':'fixture-owner-v1'}]})
process=psutil.Process()
tracemalloc.start()
phases=[]

def capture(phase,iterations=0):
    info=process.memory_info()
    current,peak=tracemalloc.get_traced_memory()
    phases.append({'phase':phase,'iterations':iterations,'rss_mb':info.rss/1048576,
                   'private_mb':getattr(info,'private',0)/1048576,'threads':process.num_threads(),
                   'handles':process.num_handles(),'python_current_mb':current/1048576,
                   'python_peak_mb':peak/1048576,'pooled_readers':service._readers.qsize()})

capture('fixture_before_refresh')
accepted=service.refresh()
assert accepted['document_count']==137 and accepted['index_size_sla_met']
capture('initial_generation_built')
assert authority.status(force=True)['ready']
capture('initial_authority_checked')
for n in range(300):assert service.status()['ready']
capture('status_300',300)
for n in range(100):assert authority.status(force=True)['ready']
capture('forced_authority_100',100)
for n in range(50):assert service.refresh()['changed_documents']==0
capture('serial_unchanged_refresh_50',50)
for n in range(50):
    assert service.request_refresh()
    service._background.join(timeout=10)
    assert not service._background.is_alive() and service.status()['ready']
capture('background_unchanged_refresh_50',50)
service.close()
capture('service_closed')
snapshot=tracemalloc.take_snapshot()
allocations=[]
for row in snapshot.statistics('lineno'):
    frame=row.traceback[0]
    if 'cochem_pipeline' in frame.filename:
        allocations.append({'file':Path(frame.filename).name,'line':frame.lineno,
                            'bytes':row.size,'allocations':row.count})
    if len(allocations)>=10:break
record={'schema':'cochem-knowledge-memory-generated-fixture/1','fixture_root':str(root),
        'runtime_root':str(INSTALL),'python':sys.version.split()[0],
        'corpus_bytes':accepted['corpus_bytes'],'index_bytes':accepted['index_bytes'],
        'document_count':accepted['document_count'],'section_count':accepted['section_count'],
        'phases':phases,'retained_python_allocations':allocations,
        'permissions_explicitly_mocked_for_fixture':True,'production_data_read':False,
        'provider_calls':0,'tasks_changed':0,'controller_modified':False,
        'full_srs_acceptance':False,'recorded_at':time.time()}
out=HERE/'knowledge-memory-generated-fixture-r3-20261009.json'
with out.open('x',encoding='utf-8') as stream:json.dump(record,stream,sort_keys=True,indent=2)
print(json.dumps(record,sort_keys=True))
