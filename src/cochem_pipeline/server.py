"""Unprivileged Antigravity interface; the SYSTEM Warden owns execution."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
from typing import Annotated, Any
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from .service import ControlClient
from . import __version__


KnowledgeResult = Annotated[CallToolResult, dict[str,Any]]


def _knowledge_result(value:dict[str,Any])->CallToolResult:
    # The SDK validates structuredContent with the declared Pydantic output
    # model and publishes the same JSON output schema to clients. Supplying the
    # supported result envelope avoids its second server-side schema check and
    # pretty-print conversion on every read. Authentication remains in HTTP.
    return CallToolResult(content=[TextContent(type='text',text=json.dumps(value,
        ensure_ascii=False,separators=(',',':'),allow_nan=False))],structuredContent=value)


def client_lifespan(client):
    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await asyncio.to_thread(client.close)
    return lifespan


def register_knowledge_tools(server,client):
    read=ToolAnnotations(readOnlyHint=True,idempotentHint=True,openWorldHint=False)
    @server.tool(annotations=read)
    async def knowledge_search(query:str,limit:int=5)->KnowledgeResult:
        """Search the ratified .sources/wiki catalog using literal terms and BM25; returns indexed evidence."""
        return _knowledge_result(await asyncio.to_thread(client.call,'/knowledge/search',{'query':query,'limit':limit}))

    @server.tool(annotations=read)
    async def knowledge_read(doc_path:str)->KnowledgeResult:
        """Read a catalog Markdown document with verified UTF-8 bytes and SHA-256; host paths are forbidden."""
        return _knowledge_result(await asyncio.to_thread(client.call,'/knowledge/read',{'doc_path':doc_path}))

    @server.tool(annotations=read)
    async def knowledge_status()->KnowledgeResult:
        """Inspect actual knowledge generation, index-size evidence and background refresh failures."""
        return _knowledge_result(await asyncio.to_thread(client.call,'/knowledge/status'))


def create_knowledge_server(client:ControlClient):
    server=FastMCP('cochem-knowledge-mcp',log_level='WARNING',lifespan=client_lifespan(client),instructions=(
        'Search the ratified permanent .sources/wiki catalog, then read catalog paths with their physical hashes. '
        'The authenticated Windows controller owns files and indexing; this interface never accepts arbitrary host paths.'))
    register_knowledge_tools(server,client)
    return server


def create_server(client:ControlClient):
    server=FastMCP(f'CoChem Pipeline {__version__}',lifespan=client_lifespan(client),instructions=(
        'For code changes, list configured projects with pipeline_projects and submit once with pipeline_code. '
        'The controller owns staged edits, independent tests, review and configured Git integration. '
        'Poll pipeline_code_status; only its verified terminal result establishes completion. '
        'For planning documents, submit one objective with pipeline_submit. The Warden automatically generates a manifest, '
        'runs isolated chapter workers within hardware limits, and routes synthesis only after '
        'all chapter outputs are accepted. Complexity determines the configured model priority; '
        'busy or quota-limited targets can cause a recorded fallback or timed wait. '
        'A submission is not completion. Poll pipeline_status for routing decisions and wait times. '
        'Report failures and blocked prerequisites; never invent another agent output.'))
    register_knowledge_tools(server,client)

    @server.tool()
    async def pipeline_submit(objective:str,requirements:list[str]|None=None,chapter_count:int=6,
                              max_attempts:int|None=None,max_dispatches:int|None=None)->dict[str,Any]:
        """Create one complete planning workflow; no manual repeated delegation is needed."""
        return await asyncio.to_thread(client.call,'/submit',{
            'objective':objective,'requirements':requirements or ['REQ-001'],'chapter_count':chapter_count,
            'max_attempts':max_attempts,'max_dispatches':max_dispatches})

    @server.tool()
    async def pipeline_status(workflow_id:str)->dict[str,Any]:
        """Read actual DAG state, complexity, selected routes, wait deadlines and accepted outputs."""
        return await asyncio.to_thread(client.call,'/workflow/'+workflow_id)

    @server.tool()
    async def pipeline_health()->dict[str,Any]:
        """Read privileged service, resource admission, active processes and quarantine status."""
        return await asyncio.to_thread(client.call,'/health')

    @server.tool()
    async def pipeline_provider_preflight_submit(workflow_id:str|None=None)->dict[str,Any]:
        """Manually enqueue one bounded genuine subscription job through Chapter 06; poll pipeline_status for the actual selected provider/model receipt."""
        return await asyncio.to_thread(client.call,'/preflight',{'workflow_id':workflow_id})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,idempotentHint=True,openWorldHint=False))
    async def pipeline_operator_view(workflow_id:str|None=None,job_id:str|None=None,after_event_id:int|None=None)->dict[str,Any]:
        """Inspect governing captures, blocking prerequisites, queue, resource plots, deployment drift and SRS acceptance evidence."""
        if workflow_id is not None and job_id is not None:
            raise ValueError('Select a workflow or a job, not both')
        for identifier in (workflow_id,job_id):
            import re
            if identifier is not None and not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',identifier):
                raise ValueError('Invalid workflow or job ID')
        if after_event_id is not None and (type(after_event_id) is not int or after_event_id<0):
            raise ValueError('Event cursor must be a nonnegative integer')
        operation='/operator/job/'+job_id if job_id else '/operator/workflow/'+workflow_id if workflow_id else '/operator'
        if after_event_id is not None:
            operation+='?after_event_id='+str(after_event_id)
        return await asyncio.to_thread(client.call,operation)

    @server.tool()
    async def pipeline_cancel(workflow_id:str)->dict[str,Any]:
        """Terminate this workflow's processes and fence unfinished jobs as failed."""
        return await asyncio.to_thread(client.call,'/cancel',{'workflow_id':workflow_id})

    @server.tool()
    async def pipeline_resume_routing(job_id:str,reason:str)->dict[str,Any]:
        """Explicitly resume a corrected configuration hold with an operator reason; preserves all budgets."""
        return await asyncio.to_thread(client.call,'/routing/resume',{'job_id':job_id,'reason':reason})

    @server.tool()
    async def pipeline_projects()->dict[str,Any]:
        """List operator-configured project IDs that accept code jobs."""
        return await asyncio.to_thread(client.call,'/coding/projects')

    @server.tool()
    async def pipeline_code(project_id:str,objective:str,requirements:list[str]|None=None,
                            workflow_id:str|None=None)->dict[str,Any]:
        """Submit a staged coding workflow with independent tests and review; acceptance is not completion."""
        return await asyncio.to_thread(client.call,'/coding/submit',{
            'project_id':project_id,'objective':objective,'requirements':requirements or ['REQ-001'],
            'workflow_id':workflow_id})

    @server.tool()
    async def pipeline_code_status(workflow_id:str)->dict[str,Any]:
        """Read actual coding stages, immutable test/review receipts and Git integration result."""
        return await asyncio.to_thread(client.call,'/coding/workflow/'+workflow_id)

    @server.tool()
    async def pipeline_code_cancel(workflow_id:str)->dict[str,Any]:
        """Cancel coding and revoke current work without accepting unverified changes."""
        return await asyncio.to_thread(client.call,'/coding/cancel',{'workflow_id':workflow_id})

    @server.tool()
    async def pipeline_code_resume(workflow_id:str,reason:str)->dict[str,Any]:
        """Resume a corrected operator hold with an audit reason; does not reset retry budgets."""
        return await asyncio.to_thread(client.call,'/coding/resume',{'workflow_id':workflow_id,'reason':reason})

    return server
