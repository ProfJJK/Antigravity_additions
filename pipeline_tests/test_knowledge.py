"""Real corpus, FTS5, authenticated HTTP and MCP knowledge acceptance."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from cochem_pipeline.knowledge import KnowledgeConfig,KnowledgeError,KnowledgeService


def ratify(root,documents):
    entries=[]
    for key,text in documents.items():
        target=root/key;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(text.encode('utf-8'))
        entries.append({'path':key,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()})
    manifest=root/'v4.1.2_manifest.json'
    manifest.write_text(json.dumps({'manifest_version':'4.2.6','documents':entries,
                                   'srs_documents':[key for key in documents if key.startswith('wiki/')]},ensure_ascii=False),encoding='utf-8')


@pytest.fixture
def corpus(tmp_path):
    root=tmp_path/'corpus';root.mkdir()
    private=tmp_path/'private';private.mkdir(mode=0o700)
    docs={'.sources/reference.md':'# Reference\nPermanent Ψ(r), ΔG°, kJ·mol⁻¹ observations.\n',
          'wiki/00_skeleton.md':'# Catalog\n[Warden](warden.md#safety)\n[Reference](../.sources/reference.md)\n',
          'wiki/warden.md':'# Warden\n## Safety\nSQLite fencing protects native workers.\n'}
    ratify(root,docs)
    config=KnowledgeConfig(enabled=True,source_root=str(root/'.sources'),wiki_root=str(root/'wiki'),
                           manifest_path=str(root/'v4.1.2_manifest.json'),state_root=str(private/'knowledge'))
    service=KnowledgeService(config)
    try:
        yield SimpleNamespace(root=root,private=private,docs=docs,config=config,service=service)
    finally:
        service.close()


def test_real_index_unicode_catalog_links_and_read_only_schema(corpus):
    result=corpus.service.refresh()
    assert result['document_count']==3 and result['section_count']==4
    assert result['relative_links_checked']==2 and result['changed_documents']==3
    assert corpus.service.read('.sources/reference.md')['text']==corpus.docs['.sources/reference.md']
    rows=corpus.service.search('SQLite fencing')
    assert rows[0]['doc_path']=='wiki/warden.md' and rows[0]['score']<0
    assert corpus.service.search('ΔG')[0]['doc_path']=='.sources/reference.md'
    with corpus.service._reader() as (_,reader):
        assert [row[1] for row in reader.execute('PRAGMA table_info(documents)')]==[
            'id','doc_path','title','line_count','sha256','updated_at']
        assert 'porter unicode61' in reader.execute("SELECT sql FROM sqlite_master WHERE name='fts_index'").fetchone()[0]
        with pytest.raises(sqlite3.OperationalError):
            reader.execute('DELETE FROM documents')


def test_incremental_only_reindexes_changed_documents_and_deletion(corpus):
    initial=corpus.service.refresh()
    assert corpus.service.refresh()['changed_documents']==0
    corpus.docs['wiki/warden.md']+='Reliable recovery.\n'
    ratify(corpus.root,corpus.docs)
    updated=corpus.service.refresh()
    assert updated['generation']!=initial['generation'] and updated['changed_documents']==1
    assert corpus.service.search('Reliable recovery')
    corpus.docs['wiki/00_skeleton.md']='# Catalog\n[Reference](../.sources/reference.md)\n'
    corpus.docs.pop('wiki/warden.md');(corpus.root/'wiki/warden.md').unlink()
    ratify(corpus.root,corpus.docs)
    removed=corpus.service.refresh()
    assert removed['removed_documents']==1 and not corpus.service.search('SQLite')


def test_full_reindex_is_atomic_for_real_concurrent_readers(corpus):
    corpus.service.refresh()
    def search():
        for _ in range(25):
            assert corpus.service.search('SQLite')[0]['doc_path']=='wiki/warden.md'
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(search) for _ in range(3)]
        rebuilt=corpus.service.refresh(full=True)
        for future in futures:
            future.result()
    assert rebuilt['changed_documents']==3


def test_failed_ratification_preserves_accepted_generation_and_archive(corpus):
    before=corpus.service.refresh()
    corpus.docs['.sources/reference.md']='Changed historic source\n'
    ratify(corpus.root,corpus.docs)
    with pytest.raises(KnowledgeError,match='immutable'):
        corpus.service.refresh()
    assert corpus.service.status()['generation']==before['generation']
    with pytest.raises(KnowledgeError,match='changed since'):
        corpus.service.read('.sources/reference.md')


def test_corrupt_binary_header_rebuilds_from_real_corpus(corpus):
    before=corpus.service.refresh()
    _,database=corpus.service._current()
    database.write_bytes(b'broken SQLite file')
    result=corpus.service.refresh()
    assert result['rebuilt_corruption'] and result['generation']!=before['generation']
    assert corpus.service.search('fencing')


def test_corruption_cannot_reset_the_permanent_source_archive(corpus):
    corpus.service.refresh()
    _,database=corpus.service._current();database.write_bytes(b'corrupt')
    corpus.docs['.sources/reference.md']='# Replaced history\n'
    ratify(corpus.root,corpus.docs)
    with pytest.raises(KnowledgeError,match='immutable'):
        corpus.service.refresh()


def test_srs_catalog_requires_a_skeleton_link_to_each_chapter(corpus):
    corpus.docs['wiki/00_skeleton.md']='# Catalog\n[Reference](../.sources/reference.md)\n'
    ratify(corpus.root,corpus.docs)
    with pytest.raises(KnowledgeError,match='skeleton must link'):
        corpus.service.refresh()


def test_search_requests_real_background_rebuild_after_corruption(corpus):
    corpus.service.refresh()
    _,database=corpus.service._current();database.write_bytes(b'corrupt')
    with pytest.raises(KnowledgeError,match='background rebuild'):
        corpus.service.search('fencing')
    corpus.service._background.join(timeout=5)
    assert not corpus.service._background.is_alive()
    assert corpus.service.search('fencing')


@pytest.mark.parametrize('path',['../secret.md','wiki/../../secret.md','/etc/passwd','C:/secret.md',
                               'wiki\\file.md','wiki/CON.md','wiki/file.md.','wiki/a//b.md','wiki/a.py'])
def test_read_rejects_unregistered_or_ambiguous_paths(corpus,path):
    corpus.service.refresh()
    with pytest.raises(KnowledgeError):
        corpus.service.read(path)


@pytest.mark.parametrize('text',['# Catalog\n[Absent](absent.md)\n',
    '# Catalog\n[Bad](warden.md#missing)\n','# Catalog\n[Escape](../../secret.md)\n',
    '# Catalog\n[Undefined][missing]\n','# Catalog\n'+'line\n'*400])
def test_invalid_links_and_oversized_chapters_cannot_publish(corpus,text):
    initial=corpus.service.refresh()
    corpus.docs['wiki/00_skeleton.md']=text;ratify(corpus.root,corpus.docs)
    with pytest.raises((KnowledgeError,FileNotFoundError)):
        corpus.service.refresh()
    assert corpus.service.status()['generation']==initial['generation']


def test_code_fences_do_not_invent_links_or_sections(corpus):
    corpus.docs['wiki/warden.md']+="```python\n# not a chapter\n'[missing](missing.md)'\n```\n"
    ratify(corpus.root,corpus.docs)
    assert corpus.service.refresh()['section_count']==4


def test_symlink_hardlink_and_manifest_desync_rejected(corpus,tmp_path):
    outside=tmp_path/'outside.md';outside.write_text('secret',encoding='utf-8')
    target=corpus.root/'wiki/link.md'
    target.symlink_to(outside)
    with pytest.raises(KnowledgeError,match='without links'):
        corpus.service.refresh()
    target.unlink();os.link(outside,target)
    with pytest.raises(KnowledgeError,match='without links'):
        corpus.service.refresh()
    target.unlink();target.write_text('# Unratified\n',encoding='utf-8')
    with pytest.raises(KnowledgeError,match='not synchronized'):
        corpus.service.refresh()


def test_manifest_hash_and_bounded_search_do_not_trust_caller_text(corpus):
    corpus.service.refresh()
    assert corpus.service.search('" OR nonexistentword')==[]
    for query,limit in [('',5),('word '*33,5),('valid',True),('valid',21)]:
        with pytest.raises(KnowledgeError):
            corpus.service.search(query,limit)
    (corpus.root/'wiki/warden.md').write_text('# Warden\n## Safety\nUnratified edit',encoding='utf-8')
    with pytest.raises(KnowledgeError,match='Manifest hash'):
        corpus.service.refresh()


def test_config_placement_rejects_worker_or_private_corpus(corpus):
    corpus.config.validate_placement(corpus.private,[])
    with pytest.raises(KnowledgeError,match='disjoint'):
        corpus.config.validate_placement(corpus.private,[corpus.root])
    with pytest.raises(KnowledgeError,match='dedicated child'):
        replace(corpus.config,state_root=str(corpus.root.parent/'other')).validate_placement(corpus.private)
    with pytest.raises(KnowledgeError,match='enabled'):
        KnowledgeService(KnowledgeConfig())


def test_pipeline_loader_registers_real_corpus_and_rejects_worker_overlap(corpus,tmp_path):
    from cochem_pipeline.config import load_config
    from pipeline_tests.test_config import config_document,write_config
    raw=config_document(tmp_path)
    raw['knowledge']=corpus.config.as_dict()
    raw['private_root']=str(corpus.private)
    loaded=load_config(write_config(tmp_path,raw))
    assert loaded.knowledge==corpus.config
    raw['slot_roots']['slot-0']=str(corpus.root/'slot')
    with pytest.raises(KnowledgeError,match='disjoint'):
        load_config(write_config(tmp_path,raw))


def test_real_authenticated_http_mcp_and_cli_retrieval(corpus,tmp_path):
    from cochem_pipeline.service import ControlClient,ControlServer
    from cochem_pipeline.server import create_server,create_knowledge_server
    from mcp.shared.memory import create_connected_server_and_client_session
    corpus.service.refresh()
    runtime=SimpleNamespace(config=SimpleNamespace(port=0),knowledge=corpus.service)
    token=secrets.token_urlsafe(48);token_file=tmp_path/'token';token_file.write_text(token,encoding='utf-8')
    server=ControlServer(runtime,token)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=ControlClient(server.server_address[1],token_file)
    try:
        connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
        connection.request('GET','/knowledge/status');response=connection.getresponse()
        assert response.status==401;response.read();connection.close()
        status=client.call('/knowledge/status')
        assert status['indexed'] and not status['ready'], 'Tiny fixtures do not meet the 4x SQLite page-size floor'
        token_file.write_text('invalid-token',encoding='utf-8')
        with pytest.raises(RuntimeError,match='Unauthorized'):
            client.call('/knowledge/status')
        token_file.write_text(token,encoding='utf-8')
        async def scenario():
            async with create_connected_server_and_client_session(create_server(client)) as session:
                result=await session.call_tool('knowledge_search',{'query':'fencing'})
                assert not result.isError and result.structuredContent['results'][0]['doc_path']=='wiki/warden.md'
                text=await session.call_tool('knowledge_read',{'doc_path':'.sources/reference.md'})
                assert not text.isError and text.structuredContent['text']==corpus.docs['.sources/reference.md']
                denied=await session.call_tool('knowledge_read',{'doc_path':'../../secret.md'})
                assert denied.isError
            async with create_connected_server_and_client_session(create_knowledge_server(client)) as session:
                tools=await session.list_tools()
                assert {tool.name for tool in tools.tools}=={'knowledge_search','knowledge_read','knowledge_status'}
                # The optimized result envelope must keep the public schema and
                # both MCP representations, including literal scientific UTF-8.
                for tool in tools.tools:
                    assert tool.outputSchema=={'additionalProperties':True,
                        'title':tool.name+'DictOutput','type':'object'}
                result=await session.call_tool('knowledge_search',{'query':'fencing'})
                assert not result.isError and result.structuredContent['results']
                assert json.loads(result.content[0].text)==result.structuredContent
                read=await session.call_tool('knowledge_read',{'doc_path':'.sources/reference.md'})
                assert json.loads(read.content[0].text)==read.structuredContent
                assert 'Ψ(r), ΔG°, kJ·mol⁻¹' in read.content[0].text
                token_file.write_text('invalid-token',encoding='utf-8')
                denied=await session.call_tool('knowledge_search',{'query':'fencing'})
                assert denied.isError and 'Unauthorized' in denied.content[0].text
                token_file.write_text(token,encoding='utf-8')
                restored=await session.call_tool('knowledge_search',{'query':'fencing'})
                assert not restored.isError and restored.structuredContent['results']
                bounded=await session.call_tool('knowledge_search',{'query':'fencing','limit':21})
                assert bounded.isError
        asyncio.run(scenario())
        config=tmp_path/'client.json';config.write_text(json.dumps({'port':server.server_address[1],'token_file':str(token_file)}),encoding='utf-8')
        environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
        result=subprocess.run([sys.executable,'-m','cochem_pipeline','knowledge-search','--client-config',str(config),'fencing'],
            capture_output=True,text=True,encoding='utf-8',timeout=10,env=environment,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['results'][0]['doc_path']=='wiki/warden.md'
    finally:
        client.close()
        server.shutdown();server.server_close();thread.join(timeout=3)
