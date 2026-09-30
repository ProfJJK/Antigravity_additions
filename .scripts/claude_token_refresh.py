"""
claude_token_refresh.py — Windows Task Scheduler token keeper.

Uses claude_subscription_manager to inspect ~/.claude/.credentials.json. If the token
expires within --threshold seconds (default 3600), it triggers claude.exe's native OAuth
refresh (`claude --version`, with ANTHROPIC_API_KEY scrubbed). No browser is needed while
the refresh token is valid.

Keep the scheduler interval below (token lifetime - threshold). Tokens that are already
expired are still refreshed through the refresh token.

Telemetry goes to stdout and to .scripts/claude_token_refresh.log (UTF-8). The last line
is a JSON document for machine consumers.

Exit codes: 0 = token fresh, 1 = credentials invalid (claude auth login needed),
            2 = token valid but still below threshold after a refresh attempt.

Note: whether `claude --version` runs the CLI's auth lifecycle depends on the installed
Claude Code version. This script never assumes the refresh worked; it re-reads the
credentials file afterwards and exits 2 when the expiry did not move.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_AGENTIC_ROOT = _SCRIPTS_DIR.parent
if str(_AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTIC_ROOT))

from claude_subscription_manager import (  # noqa: E402
    get_claude_subscription_health,
    inspect_claude_credentials,
    refresh_claude_token_if_needed,
)

LOG = _SCRIPTS_DIR / "claude_token_refresh.log"
DEFAULT_THRESHOLD_SEC = float(os.environ.get("CLAUDE_REFRESH_THRESHOLD_SEC", "3600"))


def _configure_logging(log_path: Path) -> logging.Logger:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("claude_token_refresh")
    log.setLevel(logging.INFO)
    log.propagate = False
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)
    log.addHandler(stdout_handler)
    try:
        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    except OSError as exc:
        log.warning("File logging disabled (%s): %s", log_path, exc)
    return log


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the Claude Pro/Max OAuth token if near expiry")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_SEC,
                        help="Refresh when TTL is below this many seconds (default 3600)")
    parser.add_argument("--claude-exe", default="", help="Path to claude.exe (default: auto-detect)")
    parser.add_argument("--creds", default="", help="Credentials path (default: ~/.claude/.credentials.json)")
    parser.add_argument("--log", default=str(LOG), help="Log file path")
    args = parser.parse_args(argv)

    log = _configure_logging(Path(args.log))
    creds = Path(args.creds) if args.creds else None
    exe = Path(args.claude_exe) if args.claude_exe else None

    before = inspect_claude_credentials(creds)
    if before.error_type and before.error_type != "expired":
        log.error("Token check failed (%s): %s", before.error_type, before.error_message)
    else:
        log.info("Token check: plan=%s tier=%s TTL=%.0fs (%.1fh)",
                 before.subscription_type, before.rate_limit_tier,
                 before.time_to_live_sec, before.time_to_live_sec / 3600)

    refreshed, after = refresh_claude_token_if_needed(threshold_sec=args.threshold, creds_path=creds,
                                                      claude_exe=exe)
    health = get_claude_subscription_health(creds)

    if after.is_valid and (refreshed or after.time_to_live_sec >= args.threshold):
        log.info("SUCCESS: token is fresh (refreshed=%s, TTL %.1fh).", refreshed, after.time_to_live_sec / 3600)
        rc = 0
    elif after.is_valid:
        log.warning(
            "WARNING: token valid but TTL %.0fs is below threshold %.0fs; the native refresh did not "
            "extend it. 'claude --version' may not run the CLI's auth lifecycle on this Claude Code "
            "version: check expiresAt in the credentials file and run 'claude auth login' if it "
            "does not advance.",
            after.time_to_live_sec, args.threshold)
        rc = 2
    else:
        log.error("FAILURE: credentials invalid (%s) — manual 'claude auth login' may be needed.",
                  after.error_type)
        rc = 1

    telemetry = {
        "event": "claude_token_refresh",
        "refreshed": refreshed,
        "threshold_sec": args.threshold,
        "ttl_before_sec": round(before.time_to_live_sec, 3),
        "exit_code": rc,
        "health": health,
    }
    log.info("TELEMETRY %s", json.dumps(telemetry, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())
