"""cochem-dsp-mcp FastMCP Server Definition (MC-DSP-31, MC-DSP-32).

Runs as a background service or daemon, serving streamable HTTP (or legacy SSE)
on the loopback interface only:

    python -m cochem.dsp.toolkit.mcp_server [--transport http|sse] [--port 47822]
"""
from __future__ import annotations
import argparse
import logging
import os
import time
from typing import Any
import uuid

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

BIND_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 47822
PORT_ENV_VAR: str = "COCHEM_DSP_MCP_PORT"
TRANSPORTS: tuple[str, ...] = ("http", "sse")

logger = logging.getLogger("cochem.dsp.toolkit.mcp_server")

# Active DSP Job State Registry
DSP_JOB_REGISTRY: dict[str, dict[str, Any]] = {}
REGISTERED_PIPELINES: list[str] = ["code_forge", "academic_press", "pedagogy_engine"]
DISPATCH_UNAVAILABLE = (
    "This legacy DSP MCP server has no connected execution backend. "
    "No task was queued or dispatched. Use the direct CLI MCP server to run "
    "a Codex or Claude job and inspect its execution receipt."
)

mcp = FastMCP(
    name="cochem-dsp-mcp",
    instructions=(
        "Legacy DSP workflow registration and status interface. Workflow execution "
        "is unavailable: registering or polling a request never executes an agent. "
        "Use the direct CLI MCP server for Codex and Claude execution."
    ),
)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False, openWorldHint=False))
def trigger_dsp_pipeline(domain: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Records an unavailable request; this legacy server cannot dispatch agents."""
    if domain not in REGISTERED_PIPELINES:
        raise ValueError(f"Unknown domain pipeline '{domain}'. Available: {REGISTERED_PIPELINES}")
    if not isinstance(payload, dict):
        raise TypeError("Payload must be a dictionary")

    job_id = f"dsp-{domain}-{uuid.uuid4().hex[:8]}"
    record = {
        "job_id": job_id,
        "domain": domain,
        "payload": payload,
        "status": "UNAVAILABLE",
        "execution_started": False,
        "error": DISPATCH_UNAVAILABLE,
        "created_at": int(time.time()),
        "completed_at": None,
    }
    DSP_JOB_REGISTRY[job_id] = record
    return {
        "job_id": job_id,
        "status": record["status"],
        "domain": domain,
        "execution_started": False,
        "error": DISPATCH_UNAVAILABLE,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_dsp_status(job_id: str) -> dict[str, Any]:
    """Reads recorded status without advancing or claiming execution."""
    if not job_id or not isinstance(job_id, str):
        raise ValueError("Job ID must be a non-empty string")

    if job_id not in DSP_JOB_REGISTRY:
        return {
            "job_id": job_id,
            "status": "NOT_FOUND",
            "error": f"No job recorded with ID {job_id}",
        }

    job = DSP_JOB_REGISTRY[job_id]
    return {
        "job_id": job_id,
        "domain": job["domain"],
        "status": job["status"],
        "created_at": job["created_at"],
        "completed_at": job.get("completed_at"),
        "execution_started": job.get("execution_started", False),
        "error": job.get("error"),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def list_dsp_pipelines() -> list[str]:
    """Lists recognized domain names; their execution backend is unavailable."""
    return list(REGISTERED_PIPELINES)


def main(argv: list[str] | None = None) -> None:
    """Serves the DSP tools over loopback HTTP/SSE until the process is stopped."""
    parser = argparse.ArgumentParser(prog="python -m cochem.dsp.toolkit.mcp_server")
    parser.add_argument("--transport", choices=TRANSPORTS, default="http")
    parser.add_argument("--port", type=int, default=int(os.environ.get(PORT_ENV_VAR, DEFAULT_PORT)))
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error(f"--port must be in 1024..65535; got {args.port}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("Starting DSP Toolkit MCP (%s) on %s:%d", args.transport, BIND_HOST, args.port)
    mcp.run(
        transport=args.transport,
        host=BIND_HOST,
        port=args.port,
        show_banner=False,
        host_origin_protection=True,
    )


if __name__ == "__main__":
    main()
