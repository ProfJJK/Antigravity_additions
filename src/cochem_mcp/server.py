"""Small stdio MCP surface: submission is distinct from completion."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .jobs import JobManager


def create_server(manager: JobManager) -> FastMCP:
    provider = manager.settings.provider

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await asyncio.to_thread(manager.close)

    server = FastMCP(
        f"cochem-{provider}-4.2.1",
        instructions=(
            f"Delegate to the actual {provider} subscription CLI using {provider}_submit. "
            "A returned job_id means accepted only. Poll status, then retrieve result. "
            "Never report completion before status=completed; failed/timed_out/cancelled/interrupted are failures. "
            "Do not impersonate the worker or invent a result. Retain job_id, provider, exit_code and session_id "
            "when citing a handoff. requested_model is configuration; reported_model may be null when the CLI "
            "does not disclose it. A completed CLI turn does not prove the requested code works: inspect changes "
            "and run relevant tests separately. There is no provider fallback."
        ),
        lifespan=lifespan,
    )
    read = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

    @server.tool(name=f"{provider}_health", annotations=read)
    async def health() -> dict[str, Any]:
        """Check binary discovery and native subscription login without model inference. Models remain unverified."""
        return await asyncio.to_thread(manager.health)

    @server.tool(name=f"{provider}_submit", annotations=write)
    def submit(prompt: str, workspace: str = "", model: str = "") -> dict[str, Any]:
        """Start a CLI coding job. Returns an acceptance receipt, not a completed answer. Use configured model alias."""
        return manager.submit(prompt, workspace, model)

    @server.tool(name=f"{provider}_status", annotations=read)
    def status(job_id: str) -> dict[str, Any]:
        """Read recorded process state. Polling never advances a job. Only completed means CLI terminal success."""
        return manager.status(job_id)

    @server.tool(name=f"{provider}_result", annotations=read)
    def result(job_id: str, offset: int = 0, limit: int = 16000) -> dict[str, Any]:
        """Read completed CLI text and receipt, in pages. Noncompleted jobs return content=null."""
        return manager.result(job_id, offset, limit)

    @server.tool(name=f"{provider}_cancel", annotations=write)
    async def cancel(job_id: str) -> dict[str, Any]:
        """Cancel a queued job or terminate the running CLI process tree. Already completed jobs are preserved."""
        return await asyncio.to_thread(manager.cancel, job_id)

    return server
