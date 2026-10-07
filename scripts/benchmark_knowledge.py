"""Measure H6 using physical Markdown, actual FTS5 and an authenticated MCP session.

This is a generated, explicitly labelled benchmark corpus, not production host
acceptance. Run in a fresh Python process. No mocked SQLite, HTTP or MCP calls
are used; the peak RSS includes the controller, indexer and FastMCP server.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, contextmanager
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import secrets
import statistics
import tempfile
import threading
import time
from types import SimpleNamespace

import psutil
import jsonschema

from cochem_pipeline.knowledge import KnowledgeConfig,KnowledgeService
from cochem_pipeline.service import ControlClient,ControlServer
from cochem_pipeline.server import create_knowledge_server
from mcp.shared.memory import create_connected_server_and_client_session


def distribution(values):
    ordered=sorted(values)
    return {'count':len(values),'average_ms':statistics.mean(values),'median_ms':statistics.median(values),
            'p95_ms':ordered[min(len(ordered)-1,(95*len(ordered)+99)//100-1)],'max_ms':max(values)}


class Timings(dict):
    def __init__(self,keys):
        super().__init__((key,[]) for key in keys)
        self.intervals={key:[] for key in keys}
        self.pending=0
        self.finished=threading.Condition()


@contextmanager
def measured_execution(service,client):
    """Time real functions without replacing their results, validation or I/O.

    One benchmark owns these wrappers and executes requests sequentially. They
    are never installed in the production controller. Timings include the small
    instrumentation overhead; nested measurements are not independent costs.
    """
    from cochem_pipeline import service as controller
    timings=Timings(('backend','generation_verify','sqlite_execute','sqlite_fetch',
                    'client','token_read','authentication','reply','schema_validation'))
    def wrap(key,function):
        def measured(*args,**kwargs):
            with timings.finished:
                timings.pending+=1
            started=time.perf_counter_ns()
            try:
                return function(*args,**kwargs)
            finally:
                finished=time.perf_counter_ns()
                with timings.finished:
                    timings[key].append((finished-started)/1e6)
                    timings.intervals[key].append((started,finished))
                    timings.pending-=1
                    timings.finished.notify_all()
        return measured
    original_search=service.search;original_current=service._current;original_reader=service._reader
    original_call=client.call;original_token=client.token_file
    original_auth=controller.hmac;original_reply=controller.Handler.reply;original_validate=jsonschema.validate
    service.search=wrap('backend',original_search)
    service._current=wrap('generation_verify',original_current)
    class TimedReader:
        def __init__(self,connection):
            self.connection=connection
        def execute(self,*args,**kwargs):
            cursor=wrap('sqlite_execute',self.connection.execute)(*args,**kwargs)
            return SimpleNamespace(fetchall=wrap('sqlite_fetch',cursor.fetchall),
                                   fetchone=wrap('sqlite_fetch',cursor.fetchone))
    @contextmanager
    def reader():
        with original_reader() as (generation,connection):
            yield generation,TimedReader(connection)
    service._reader=reader
    client.call=wrap('client',original_call)
    client.token_file=SimpleNamespace(read_text=wrap('token_read',original_token.read_text))
    controller.hmac=SimpleNamespace(compare_digest=wrap('authentication',original_auth.compare_digest))
    controller.Handler.reply=wrap('reply',original_reply)
    jsonschema.validate=wrap('schema_validation',original_validate)
    try:
        yield timings
    finally:
        service.search=original_search;service._current=original_current;service._reader=original_reader
        client.call=original_call;client.token_file=original_token
        controller.hmac=original_auth;controller.Handler.reply=original_reply;jsonschema.validate=original_validate


def start_capture(timings):
    return {key:len(values) for key,values in timings.items()}


def finish_capture(timings,offsets,elapsed_ms):
    # sendall can finish on the controller thread after the client has received
    # its bytes. Attribute that reply to this request before starting the next;
    # waiting here is outside the already captured end-to-end measurement.
    with timings.finished:
        if not timings.finished.wait_for(lambda:timings.pending==0,timeout=5):
            raise RuntimeError('Timed controller operation did not finish')
    row={key:sum(values[offsets[key]:]) for key,values in timings.items()}
    clients=timings.intervals['client'][offsets['client']:]
    row['reply_inside_client']=sum(max(0,min(reply_end,client_end)-max(reply_start,client_start))/1e6
        for reply_start,reply_end in timings.intervals['reply'][offsets['reply']:]
        for client_start,client_end in clients)
    row['reply_after_client']=row['reply']-row['reply_inside_client']
    row['end_to_end']=elapsed_ms
    row['backend_other']=row['backend']-sum(row[key] for key in ('generation_verify','sqlite_execute','sqlite_fetch'))
    # Both server and client schema validation happen outside ControlClient.call.
    row['http_other']=row['client']-sum(row[key] for key in ('backend','token_read','authentication','reply_inside_client'))
    row['mcp_dispatch_other']=elapsed_ms-row['client']-row['schema_validation']
    return row


def run(directory:Path,iterations:int=100,comparison_server_factory=None):
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
    server=None;server_thread=None;client=None
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
        async def measure_mcp(timings):
            times=[]
            profiles=[]
            comparison_times=[];comparison_profiles=[]
            async with AsyncExitStack() as sessions:
                session=await sessions.enter_async_context(create_connected_server_and_client_session(create_knowledge_server(client)))
                comparison_session=None
                if comparison_server_factory is not None:
                    comparison_session=await sessions.enter_async_context(create_connected_server_and_client_session(comparison_server_factory(client)))
                    checked=await comparison_session.call_tool('knowledge_search',{'query':'fencing','limit':5})
                    if checked.isError or not checked.structuredContent['results']:
                        raise RuntimeError('Comparison MCP server failed its first physical query')
                started=time.perf_counter()
                first=await session.call_tool('knowledge_search',{'query':'fencing','limit':5})
                first_ms=(time.perf_counter()-started)*1000
                if first.isError or not first.structuredContent['results']:
                    raise RuntimeError('First named knowledge MCP request failed')
                for number in range(iterations):
                    calls=[(session,times,profiles)]
                    if comparison_session is not None:
                        calls.append((comparison_session,comparison_times,comparison_profiles))
                        if number%2:
                            calls.reverse()
                    values=[]
                    for current,current_times,current_profiles in calls:
                        offsets=start_capture(timings);started=time.perf_counter()
                        result=await current.call_tool('knowledge_search',{'query':queries[number%len(queries)],'limit':5})
                        elapsed=(time.perf_counter()-started)*1000;current_times.append(elapsed)
                        current_profiles.append(finish_capture(timings,offsets,elapsed))
                        if result.isError or not result.structuredContent['results']:
                            raise RuntimeError('Actual authenticated knowledge MCP search failed')
                        values.append(result.structuredContent)
                    if len(values)==2 and values[0]!=values[1]:
                        raise RuntimeError('Comparison changed the public structured result')
                result=await session.call_tool('knowledge_read',{'doc_path':'wiki/ch050.md'})
                if result.isError or 'Ψ(r), ΔG°, and kJ·mol⁻¹' not in result.structuredContent['text']:
                    raise RuntimeError('MCP failed literal scientific UTF-8 preservation')
            return times,first_ms,profiles,comparison_times,comparison_profiles
        with measured_execution(service,client) as timings:
            http_times=[];http_profiles=[]
            for number in range(iterations):
                offsets=start_capture(timings);started=time.perf_counter()
                result=client.call('/knowledge/search',{'query':queries[number%len(queries)],'limit':5})
                elapsed=(time.perf_counter()-started)*1000;http_times.append(elapsed)
                http_profiles.append(finish_capture(timings,offsets,elapsed))
                if not result['results']:
                    raise RuntimeError('Authenticated HTTP search failed')
            mcp_times,first_mcp_ms,mcp_profiles,comparison_times,comparison_profiles=asyncio.run(measure_mcp(timings))
        samples.append(process.memory_info().rss)
        query_average=sum(query_times)/len(query_times);mcp_average=sum(mcp_times)/len(mcp_times)
        return {'scope':'generated physical 10001-section corpus; real FTS5, HTTP and in-process MCP transport',
            'platform':platform.platform(),'python':platform.python_version(),'pid':os.getpid(),
            'documents':indexed['document_count'],'indexed_sections':indexed['section_count'],
            'manifest_sha256':indexed['manifest_sha256'],'build_seconds':build_seconds,
            'query_iterations':iterations,'search_average_ms':query_average,'search_max_ms':max(query_times),
            'first_search_ms':first_search_ms,'query_result_cache':False,
            'mcp_average_ms':mcp_average,'mcp_max_ms':max(mcp_times),
            'http_average_ms':statistics.mean(http_times),
            'latency_distributions':{'backend_direct':distribution(query_times),'http':distribution(http_times),
                                     'mcp':distribution(mcp_times)},
            'paired_stage_averages_ms':{label:{key:statistics.mean(row[key] for row in rows)
                for key in rows[0]} for label,rows in (('http',http_profiles),('mcp',mcp_profiles))},
            'paired_stage_samples_ms':{'http':http_profiles,'mcp':mcp_profiles},
            'profile_boundaries':{'backend':'KnowledgeService.search including protected generation and real FTS query',
                'generation_verify':'Protected current-generation pointer, file metadata and SQLite header verification',
                'sqlite_execute':'Real immutable SQLite execute call including FTS matching and ranking until first row',
                'sqlite_fetch':'Real cursor materialization including remaining SQLite work and row creation',
                'backend_other':'Backend minus generation verification, SQLite execute and fetch; includes pool, query validation and result shaping',
                'client':'ControlClient.call including lock, token reread, authenticated HTTP and JSON decoding',
                'authentication':'Actual server constant-time bearer comparison only',
                'reply':'Actual controller JSON encode, HTTP headers and socket write',
                'reply_inside_client':'Reply wall time intersecting the actual client call interval; excludes completion after bytes arrive',
                'reply_after_client':'Reply wall time after the client call completes; outside end-to-end cost',
                'schema_validation':'Actual SDK jsonschema.validate calls; server and client combined',
                'http_other':'Client minus backend, token read, authentication and reply_inside_client; includes parsing, sockets and scheduling',
                'mcp_dispatch_other':'End-to-end minus client and JSON-schema validation; includes async scheduling, SDK and transport'},
            'package_versions':{name:importlib.metadata.version(name) for name in ('mcp','pydantic','jsonschema')},
            'active_mcp_server_source_sha256':hashlib.sha256(Path(inspect.getsourcefile(create_knowledge_server)).read_bytes()).hexdigest(),
            'interleaved_comparison':None if comparison_server_factory is None else {
                'scope':'Same index, query sequence, authenticated HTTP client and transport; alternate A/B order per query',
                'all_structured_results_equal':True,
                'comparison_source_sha256':hashlib.sha256(Path(inspect.getsourcefile(comparison_server_factory)).read_bytes()).hexdigest(),
                'comparison_latency':distribution(comparison_times),
                'paired_stage_averages_ms':{key:statistics.mean(row[key] for row in comparison_profiles) for key in comparison_profiles[0]},
                'paired_stage_samples_ms':comparison_profiles,
                'current_minus_comparison_mean_ms':statistics.mean(after-before for after,before in zip(mcp_times,comparison_times))},
            'source_sha256':{str(path.relative_to(Path(__file__).resolve().parents[1])):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (Path(__file__).resolve(),*(Path(__file__).resolve().parents[1]/'src/cochem_pipeline'/name
                    for name in ('knowledge.py','knowledge_mcp.py','service.py','server.py')))},
            'first_mcp_call_ms':first_mcp_ms,'mcp_server':'cochem-knowledge-mcp',
            'corpus_bytes':indexed['corpus_bytes'],'index_bytes':indexed['index_bytes'],'index_ratio':indexed['index_ratio'],
            'peak_rss_bytes':max(samples),'rss_samples':len(samples),
            'search_sla_met':query_average<5,'mcp_search_sla_met':mcp_average<5,
            'index_size_sla_met':indexed['index_size_sla_met'],'memory_sla_met':max(samples)<256*1048576,
            'limitations':['Generated workload does not establish representative production relevance or Windows performance.',
                          'First-query timing does not evict filesystem caches populated during index construction.',
                          'MCP latency includes real protocol and authenticated HTTP but uses in-process MCP session transport.',
                          'Paired function wrappers add small overhead; nested stage measurements must not be summed twice.',
                          'The 5 ms latency value is an optimization target; the owner accepted the historical 7.731 ms MCP mean.']}
    finally:
        if client is not None:
            client.close()
        if server is not None:
            server.shutdown();server.server_close();server_thread.join(timeout=5)
        stopped.set();sampler.join(timeout=5);service.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--iterations',type=int,default=100)
    parser.add_argument('--comparison-server-file',type=Path,
                        help='Execute a reviewed previous cochem_pipeline/server.py for interleaved comparison')
    parser.add_argument('--require-latency-targets',action='store_true',
                        help='Exit nonzero if either optional 5 ms optimization target is missed')
    args=parser.parse_args()
    comparison=None
    if args.comparison_server_file is not None:
        spec=importlib.util.spec_from_file_location('cochem_pipeline._benchmark_comparison',
                                                    args.comparison_server_file.resolve(strict=True))
        if spec is None or spec.loader is None:
            raise ValueError('Comparison must be a reviewed Python server source file')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        comparison=module.create_knowledge_server
    with tempfile.TemporaryDirectory(prefix='cochem-knowledge-benchmark-') as temporary:
        result=run(Path(temporary),args.iterations,comparison_server_factory=comparison)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(text+'\n',encoding='utf-8')
    displayed={key:value for key,value in result.items() if key!='paired_stage_samples_ms'}
    if displayed['interleaved_comparison'] is not None:
        displayed['interleaved_comparison']={key:value for key,value in displayed['interleaved_comparison'].items()
                                           if key!='paired_stage_samples_ms'}
    print(json.dumps(displayed,ensure_ascii=False,indent=2))
    checks=['index_size_sla_met','memory_sla_met']
    if args.require_latency_targets:
        checks+=['search_sla_met','mcp_search_sla_met']
    raise SystemExit(0 if all(result[key] for key in checks) else 1)


if __name__=='__main__':
    main()
