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


def _structured_node_prompt(kind: str, payload: dict[str, Any], workflow_id: str) -> str:
    """Validate manual node data without accepting controller-owned authority."""
    if kind not in {"MANIFEST_GENERATOR", "CHAPTER_DRAFT"}:
        raise ValueError("kind must be MANIFEST_GENERATOR or CHAPTER_DRAFT; protected synthesis belongs to Gemini")
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
    # Enforce JSON-compatible finite data before the shared renderer accepts it.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    from cochem_pipeline.worker import node_prompt

    return (
        "Standalone development request only. This CLI job is not a protected pipeline attempt. "
        "This tool provides no controller identity isolation or DAG commit; any such assurances in "
        "the shared node template describe the controller-managed workflow, not this development job. "
        "Return the requested structured draft; do not claim that its workflow was committed or completed.\n\n"
        + node_prompt({"kind": kind, "payload": payload, "workflow_id": workflow_id})
    )


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
            f"Delegate to the actual {provider} subscription CLI using {provider}_submit. "
            "A returned job_id means accepted only. Poll status, then retrieve result. "
            "Never report completion before status=completed; failed/timed_out/cancelled/interrupted are failures. "
            "Do not impersonate the worker or invent a result. Retain job_id, provider, exit_code and session_id "
            "when citing a handoff. requested_model is configuration; reported_model may be null when the CLI "
            "does not disclose it. A completed CLI turn does not prove the requested code works: inspect changes "
            "and run relevant tests separately. There is no provider fallback. "
            f"{provider}_submit_node accepts structured standalone development drafts only; "
            "it never claims, changes or completes the protected pipeline DAG."
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

    @server.tool(name=f"{provider}_submit_node", annotations=write)
    def submit_node(kind: str, payload: dict[str, Any], workflow_id: str,
                    workspace: str = "", model: str = "") -> dict[str, Any]:
        """Accept a standalone MANIFEST_GENERATOR or CHAPTER_DRAFT CLI job; poll status/result.

        Returns acceptance only. Does not mutate the protected workflow DAG.
        Only the controller owns leases and accepted completion. Payload must
        contain objective/requirements plus chapter_count (manifest), or
        chapter_id/title (chapter). Never supply fencing or credential fields.
        """
        prompt = _structured_node_prompt(kind, payload, workflow_id)
        record = manager.submit(prompt, workspace, model)
        return {**record, "submission_scope": "standalone_node_draft",
                "workflow_id": workflow_id, "node_kind": kind, "protected_dag_mutated": False,
                "notice": "Accepted CLI job only; this is not a protected workflow claim or completion."}

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
