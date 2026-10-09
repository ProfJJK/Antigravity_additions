"""Small stdio MCP surface: submission is distinct from completion."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import __version__
from .jobs import JobManager


def _validate_structured_request(kind: str, payload: dict[str, Any], workflow_id: str) -> None:
    """Validate a new planning request, which is not an executable DAG node."""
    if kind not in {"MANIFEST_GENERATOR", "CHAPTER_DRAFT"}:
        raise ValueError("kind must be MANIFEST_GENERATOR or CHAPTER_DRAFT; all DAG nodes belong to the controller")
    if not isinstance(workflow_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", workflow_id):
        raise ValueError("workflow_id must be a nonempty identifier of at most 128 characters")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a structured object")
    forbidden = {"attempt_id", "fencing_token", "lease_owner", "lease_expires_at",
                 "controller_token", "api_token", "private_root", "job_db"}
    pending: list[Any] = [payload]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(not isinstance(key, str) or key.casefold() in forbidden for key in item):
                raise ValueError("Structured payload must not contain controller credentials, leases or fencing fields")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    if not isinstance(payload.get("objective"), str) or not payload["objective"].strip():
        raise ValueError("payload.objective must be a nonempty string")
    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or not requirements or any(
        not isinstance(value, str) or not value.strip() for value in requirements
    ) or len(set(requirements)) != len(requirements):
        raise ValueError("payload.requirements must be a nonempty list of distinct requirement strings")
    if kind == "MANIFEST_GENERATOR":
        count = payload.get("chapter_count")
        if type(count) is not int or not 1 <= count <= 64:
            raise ValueError("payload.chapter_count must be an integer from 1 to 64")
    else:
        chapter = payload.get("chapter_id")
        if not isinstance(chapter, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", chapter):
            raise ValueError("payload.chapter_id must be a nonempty identifier")
        if not isinstance(payload.get("title"), str) or not payload["title"].strip():
            raise ValueError("payload.title must be a nonempty string")
    # Native chapter prompts require accepted manifest commitments. A new user
    # request precedes that manifest, so it must never enter the worker renderer.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def create_server(manager: JobManager) -> FastMCP:
    provider = manager.settings.provider

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await asyncio.to_thread(manager.close)

    server = FastMCP(
        f"cochem-{provider}-{__version__}",
        instructions=(
            "Compatibility front end for the authenticated pipeline job board. Every model task uses "
            "Chapter 06 complexity routing and spillover across Claude, Codex and Agy. Provider tool "
            "names do not pin a model; explicit model requests are rejected. A job_id is acceptance "
            "only. Poll status and retrieve real controller evidence. Never impersonate an agent or "
            "invent completion. Coding requires an exact registered project mapping. Structured "
            "node submission starts a complete planning DAG; it cannot modify an existing node."
        ),
        lifespan=lifespan,
    )
    read = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

    @server.tool(name=f"{provider}_health", annotations=read)
    async def health() -> dict[str, Any]:
        """Check authenticated job-board health without inference. Models remain unverified."""
        return await asyncio.to_thread(manager.health)

    @server.tool(name=f"{provider}_submit", annotations=write)
    def submit(prompt: str, workspace: str = "", model: str = "") -> dict[str, Any]:
        """Submit a routed coding workflow. Returns acceptance only; model must be empty."""
        return manager.submit(prompt, workspace, model)

    @server.tool(name=f"{provider}_submit_node", annotations=write)
    def submit_node(kind: str, payload: dict[str, Any], workflow_id: str,
                    workspace: str = "", model: str = "") -> dict[str, Any]:
        """Submit a complete planning DAG from a validated manifest/chapter request.

        Every generated task is routed by the controller. The workflow_id names
        a new workflow, never an existing node. Model must be empty.
        """
        return manager.submit_node(kind, payload, workflow_id, workspace, model)

    @server.tool(name=f"{provider}_status", annotations=read)
    def status(job_id: str) -> dict[str, Any]:
        """Read controller workflow state; polling never advances work or asserts model execution."""
        return manager.status(job_id)

    @server.tool(name=f"{provider}_result", annotations=read)
    def result(job_id: str, offset: int = 0, limit: int = 16000) -> dict[str, Any]:
        """Read accepted workflow evidence in pages. Noncompleted workflows return content=null."""
        return manager.result(job_id, offset, limit)

    @server.tool(name=f"{provider}_cancel", annotations=write)
    async def cancel(job_id: str) -> dict[str, Any]:
        """Request controller-owned workflow cancellation. Completed evidence is preserved."""
        return await asyncio.to_thread(manager.cancel, job_id)

    return server
