"""Unprivileged Antigravity interface; the SYSTEM Warden owns execution."""
from __future__ import annotations
import asyncio
from typing import Any
from mcp.server.fastmcp import FastMCP
from .service import ControlClient
from . import __version__


def create_server(client:ControlClient):
    server=FastMCP(f'CoChem Pipeline {__version__}',instructions=(
        'For code changes, list configured projects with pipeline_projects and submit once with pipeline_code. '
        'The controller owns staged edits, independent tests, review and configured Git integration. '
        'Poll pipeline_code_status; only its verified terminal result establishes completion. '
        'For planning documents, submit one objective with pipeline_submit. The Warden automatically generates a manifest, '
        'runs isolated chapter workers within hardware limits, and starts Gemini synthesis only after '
        'all chapter outputs are accepted. Complexity determines the configured model priority; '
        'busy or quota-limited targets can cause a recorded fallback or timed wait. '
        'A submission is not completion. Poll pipeline_status for routing decisions and wait times. '
        'Report failures and blocked prerequisites; never invent another agent output.'))

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
