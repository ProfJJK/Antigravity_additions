"""Entrypoint for separate Codex and Claude stdio MCP servers."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__
from .config import load_settings
from .jobs import JobManager


def main() -> None:
    parser = argparse.ArgumentParser(description="CoChem subscription CLI MCP bridge")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", required=True, help="Bridge JSON configuration in this runtime")
    parser.add_argument("--provider", required=True, choices=("codex", "claude"))
    parser.add_argument("--health", action="store_true", help="Print no-inference health JSON and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    manager = JobManager(load_settings(args.config, args.provider))
    try:
        if args.health:
            result = manager.health()
            print(json.dumps(result, indent=2))
            if not result["ready"]:
                raise SystemExit(1)
        else:
            from .server import create_server
            create_server(manager).run(transport="stdio")
    finally:
        manager.close()


if __name__ == "__main__":
    main()
