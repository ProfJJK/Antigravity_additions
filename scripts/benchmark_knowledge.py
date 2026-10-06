"""Measure H6 using physical Markdown, actual FTS5 and an authenticated MCP session.

This is a generated, explicitly labelled benchmark corpus, not production host
acceptance. Run in a fresh Python process. No mocked SQLite, HTTP or MCP calls
are used; the peak RSS includes the controller, indexer and FastMCP server.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import tempfile
import threading
import time
from types import SimpleNamespace

import psutil

from cochem_pipeline.knowledge import KnowledgeConfig,KnowledgeService
from cochem_pipeline.service import ControlClient,ControlServer
from cochem_pipeline.server import create_knowledge_server
from mcp.shared.memory import create_connected_server_and_client_session


def run(directory:Path,iterations:int=100):
    if not 20<=iterations<=1000:
        raise ValueError('Benchmark needs 20..1000 measured queries')
    root=directory/'corpus';private=directory/'private'
    root.mkdir();private.mkdir(mode=0o700)
    (root/'.sources').mkdir();(root/'wiki').mkdir()
    documents=[]
    categories=['fencing','telemetry','lease','recovery','manifest','workspace','watermark','deadline','index','admission']
    for document in range(100):
        paragraphs=[]
        for section in range(100):
            category=categories[section%len(categories)]
            paragraphs.append(f'## {category.title()} contract {document}-{section}\n'
                f'The {category} operation records physical evidence for transaction {document:03d}{section:03d}. '
                'A reader verifies source hashes before accepting immutable artifacts. Concurrent workers must retain '
                'distinct execution identities, report measured resource consumption, and preserve progress after recovery. '
                'Scientific UTF-8 notation Ψ(r), ΔG°, and kJ·mol⁻¹ remains unchanged in archived reference material.\n')
        key=f'wiki/ch{document:03d}.md'
        raw=''.join(paragraphs).encode('utf-8');(root/key).write_bytes(raw)
        documents.append({'path':key,'sha256':hashlib.sha256(raw).hexdigest()})
    source=b'# Benchmark provenance\nGenerated benchmark fixture: 10,000 real Markdown sections, not production research evidence.\n'
    (root/'.sources/provenance.md').write_bytes(source)
    documents.append({'path':'.sources/provenance.md','sha256':hashlib.sha256(source).hexdigest()})
    manifest=root/'v4.1.2_manifest.json'
    manifest.write_text(json.dumps({'manifest_version':'4.2.6','documents':documents},ensure_ascii=False),encoding='utf-8')
    service=KnowledgeService(KnowledgeConfig(enabled=True,source_root=str(root/'.sources'),wiki_root=str(root/'wiki'),
        state_root=str(private/'knowledge'),manifest_path=str(manifest)))
    process=psutil.Process();samples=[];stopped=threading.Event()
    def sample():
        while not stopped.wait(.01):
            samples.append(process.memory_info().rss)
    sampler=threading.Thread(target=sample,daemon=True);sampler.start()
    server=None;server_thread=None
    try:
        started=time.perf_counter();indexed=service.refresh();build_seconds=time.perf_counter()-started
        query_times=[];counts=[]
        started=time.perf_counter();first=service.search('fencing')
        first_search_ms=(time.perf_counter()-started)*1000
        if not first:
            raise RuntimeError('First uncached physical query failed')
        queries=['fencing','telemetry','lease','recovery','manifest','workspace','watermark','deadline','index','admission',
                 'Scientific UTF','immutable artifacts','source hashes','transaction 050050']
        for number in range(iterations):
            started=time.perf_counter();result=service.search(queries[number%len(queries)])
            query_times.append((time.perf_counter()-started)*1000);counts.append(len(result))
        if not all(counts) or indexed['section_count']<10000:
            raise RuntimeError('Benchmark failed to exercise the physical 10k-section index')
        token=secrets.token_urlsafe(48);token_file=private/'controller.token'
        token_file.write_text(token,encoding='utf-8');token_file.chmod(0o600)
        server=ControlServer(SimpleNamespace(config=SimpleNamespace(port=0),knowledge=service),token)
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        client=ControlClient(server.server_address[1],token_file)
        async def measure_mcp():
            times=[]
            async with create_connected_server_and_client_session(create_knowledge_server(client)) as session:
                started=time.perf_counter()
                first=await session.call_tool('knowledge_search',{'query':'fencing','limit':5})
                first_ms=(time.perf_counter()-started)*1000
                if first.isError or not first.structuredContent['results']:
                    raise RuntimeError('First named knowledge MCP request failed')
                for number in range(iterations):
                    started=time.perf_counter()
                    result=await session.call_tool('knowledge_search',{'query':queries[number%len(queries)],'limit':5})
                    times.append((time.perf_counter()-started)*1000)
                    if result.isError or not result.structuredContent['results']:
                        raise RuntimeError('Actual authenticated knowledge MCP search failed')
                result=await session.call_tool('knowledge_read',{'doc_path':'wiki/ch050.md'})
                if result.isError or 'Ψ(r), ΔG°, and kJ·mol⁻¹' not in result.structuredContent['text']:
                    raise RuntimeError('MCP failed literal scientific UTF-8 preservation')
            return times,first_ms
        mcp_times,first_mcp_ms=asyncio.run(measure_mcp())
        samples.append(process.memory_info().rss)
        query_average=sum(query_times)/len(query_times);mcp_average=sum(mcp_times)/len(mcp_times)
        return {'scope':'generated physical 10001-section corpus; real FTS5, HTTP and in-process MCP transport',
            'platform':platform.platform(),'python':platform.python_version(),'pid':os.getpid(),
            'documents':indexed['document_count'],'indexed_sections':indexed['section_count'],
            'manifest_sha256':indexed['manifest_sha256'],'build_seconds':build_seconds,
            'query_iterations':iterations,'search_average_ms':query_average,'search_max_ms':max(query_times),
            'first_search_ms':first_search_ms,'query_result_cache':False,
            'mcp_average_ms':mcp_average,'mcp_max_ms':max(mcp_times),
            'first_mcp_call_ms':first_mcp_ms,'mcp_server':'cochem-knowledge-mcp',
            'corpus_bytes':indexed['corpus_bytes'],'index_bytes':indexed['index_bytes'],'index_ratio':indexed['index_ratio'],
            'peak_rss_bytes':max(samples),'rss_samples':len(samples),
            'search_sla_met':query_average<5,'mcp_search_sla_met':mcp_average<5,
            'index_size_sla_met':indexed['index_size_sla_met'],'memory_sla_met':max(samples)<256*1048576,
            'limitations':['Generated workload does not establish representative production relevance or Windows performance.',
                          'First-query timing does not evict filesystem caches populated during index construction.',
                          'MCP latency includes real protocol and authenticated HTTP but uses in-process MCP session transport.']}
    finally:
        if server is not None:
            server.shutdown();server.server_close();server_thread.join(timeout=5)
        stopped.set();sampler.join(timeout=5);service.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--iterations',type=int,default=100)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cochem-knowledge-benchmark-') as temporary:
        result=run(Path(temporary),args.iterations)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(text+'\n',encoding='utf-8')
    print(text)
    raise SystemExit(0 if all(result[key] for key in ('search_sla_met','mcp_search_sla_met','index_size_sla_met','memory_sla_met')) else 1)


if __name__=='__main__':
    main()
