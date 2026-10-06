"""Live MCP-to-CLI smoke test. This consumes the selected CLI subscription quota.

Creates a temporary Git repository under the first configured workspace root,
asks the actual CLI to create one nonce file, and verifies it independently.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from cochem_mcp.config import load_settings


def payload(result):
    if result.isError:
        raise RuntimeError("MCP tool returned an error: " + str(result.content))
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(c.text for c in result.content if c.type == "text"))


async def verify(args):
    settings = load_settings(args.config, args.provider)
    nonce = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="cochem-mcp-live-", dir=settings.workspace_roots[0]) as tmp:
        subprocess.run(["git", "init", "--quiet", tmp], check=True)
        parameters = StdioServerParameters(command=sys.executable, env=dict(os.environ), args=[
            "-m", "cochem_mcp", "--config", str(Path(args.config).resolve()), "--provider", args.provider])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                health = payload(await client.call_tool(f"{args.provider}_health"))
                if not health["ready"]:
                    raise RuntimeError(health["reason"])
                job = payload(await client.call_tool(f"{args.provider}_submit", {
                    "prompt": f"Create smoke-output.txt in the current workspace containing exactly {nonce} followed by a newline. Use your file-writing tools. Do not change any other files. Report completion only after reading back that file.",
                    "workspace": tmp, "model": args.model,
                }))
                job_id = job["job_id"]
                deadline = time.monotonic() + settings.timeout_seconds + 45
                while time.monotonic() < deadline:
                    status = payload(await client.call_tool(f"{args.provider}_status", {"job_id": job_id}))
                    if status["status"] in {"completed", "failed", "timed_out", "cancelled", "interrupted"}:
                        break
                    await asyncio.sleep(1)
                else:
                    await client.call_tool(f"{args.provider}_cancel", {"job_id": job_id})
                    raise TimeoutError("MCP job did not finish within its configured timeout")
                result = payload(await client.call_tool(f"{args.provider}_result", {"job_id": job_id}))
                output = Path(tmp) / "smoke-output.txt"
                file_matches = output.is_file() and output.read_text(encoding="utf-8") == nonce + "\n"
                report = {key: result.get(key) for key in (
                    "job_id", "provider", "status", "requested_model", "reported_model", "pid", "exit_code",
                    "session_id", "receipt_path", "error")}
                report["file_verified"] = file_matches
                print(json.dumps(report, indent=2))
                if result["status"] != "completed" or not file_matches:
                    raise RuntimeError("Live CLI handoff or independently verified file creation failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    try:
        asyncio.run(verify(args))
    except Exception as exc:
        print(f"Live MCP verification failed ({type(exc).__name__}); inspect the job report and receipt logs.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
