"""Unprivileged Antigravity interface; the SYSTEM Warden owns execution."""
from __future__ import annotations
import asyncio
from typing import Any
from mcp.server.fastmcp import FastMCP
from .service import ControlClient


def create_server(client:ControlClient):
    server=FastMCP('CoChem Pipeline 4.2.2',instructions=(
        'Submit one objective with pipeline_submit. The Warden automatically generates a manifest, '
        'runs isolated chapter workers within hardware limits, and starts Gemini synthesis only after '
        'all chapter outputs are accepted. A submission is not completion. Poll pipeline_status. '
        'Report failures and blocked prerequisites; never invent another agent output.'))

    @server.tool()
    async def pipeline_submit(objective:str,requirements:list[str]|None=None,chapter_count:int=6)->dict[str,Any]:
        """Create one complete planning workflow; no manual repeated delegation is needed."""
        return await asyncio.to_thread(client.call,'/submit',{
            'objective':objective,'requirements':requirements or ['REQ-001'],'chapter_count':chapter_count})

    @server.tool()
    async def pipeline_status(workflow_id:str)->dict[str,Any]:
        """Read accepted outputs, hashes, and actual DAG state. Does not execute or complete jobs."""
        return await asyncio.to_thread(client.call,'/workflow/'+workflow_id)

    @server.tool()
    async def pipeline_health()->dict[str,Any]:
        """Read privileged service, resource admission, active processes and quarantine status."""
        return await asyncio.to_thread(client.call,'/health')

    @server.tool()
    async def pipeline_cancel(workflow_id:str)->dict[str,Any]:
        """Terminate this workflow's processes and fence unfinished jobs as failed."""
        return await asyncio.to_thread(client.call,'/cancel',{'workflow_id':workflow_id})

    return server
