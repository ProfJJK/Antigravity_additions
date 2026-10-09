"""Retired 4.2.1 live checker; this entry point never starts MCP or model work.

Use the current authenticated controller preflight and registered-project coding
acceptance described in docs/EXECUTION_4.2.7.md. Every model probe uses the durable
job board and Chapter 06 routing. A retired checker exit is not a passed check.
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_known_args()
    print(
        "RETIRED: verify_cli_mcp.py does not run an acceptance check. "
        "Its unregistered workspace, model pinning and direct-edit assumptions "
        "are incompatible with the protected pipeline. Follow "
        "docs/EXECUTION_4.2.7.md and docs/OPERATIONS_4.2.7-r2.md for bounded "
        "routed preflight and registered-project acceptance. No model job was submitted.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
