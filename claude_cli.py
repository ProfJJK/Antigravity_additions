"""Claude Code subscription dispatcher. Prompts go over stdin; auth stays native.

Examples:
    python claude_cli.py --agent cochem-coder -p "Fix this bug" --model sonnet
    python claude_cli.py --health
    python claude_cli.py --stdin --no-stream < task.txt

The legacy ``--provider claude`` spelling now selects the same subscription CLI.
Paid API routing is intentionally unavailable.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys

import claude_subscription_manager as csm

logger = logging.getLogger("claude_cli")
SUBSCRIPTION_PROVIDER = "claude-subscription"
PROVIDER_CHOICES = [SUBSCRIPTION_PROVIDER, "claude"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Headless Claude Code using your native subscription login")
    parser.add_argument("--agent", "-a", default="cochem-coder", help="Agent persona")
    parser.add_argument("--prompt", "-p", default="", help="Prompt text (otherwise read stdin)")
    parser.add_argument("--stdin", action="store_true", help="Read prompt from stdin")
    parser.add_argument("--stream", action="store_true", default=True, help="Stream text (default)")
    parser.add_argument("--no-stream", dest="stream", action="store_false", help="Print only a verified successful result")
    parser.add_argument("--model", "-m", default="", help="Exact model ID or a native Claude CLI alias")
    parser.add_argument("--provider", default=SUBSCRIPTION_PROVIDER, choices=PROVIDER_CHOICES,
                        help="Both names use Claude Code subscription authentication")
    parser.add_argument("--system", default="", help="Additional system instructions")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Job timeout in seconds")
    parser.add_argument("--health", action="store_true", help="Check native authentication; no inference")
    return parser


def _read_prompt(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    if args.prompt and not args.stdin:
        return args.prompt
    if sys.stdin is None or (sys.stdin.isatty() and not args.stdin):
        parser.print_help()
        return ""
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read().decode("utf-8", errors="replace") if buffer is not None else sys.stdin.read()
    return raw.strip()


def _emit_stream(chunks) -> str:
    collected = []
    for chunk in chunks:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        collected.append(chunk)
    text = "".join(collected)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")
        sys.stdout.flush()
    return text


def _run_subscription(args: argparse.Namespace, prompt: str) -> int:
    try:
        exe = csm.default_claude_exe()
        env = csm.get_isolated_subscription_env(claude_exe=exe)
        model = args.model or os.environ.get("DEFAULT_CLAUDE_MODEL") or None
        system_prompt = csm.resolve_persona_system_prompt(args.agent, args.system)
        # Never remove an explicitly selected model or silently substitute a provider.
        kwargs = dict(claude_exe=exe, system_prompt=system_prompt, prompt=prompt,
                      env=env, model=model, timeout=args.timeout)
        if args.stream:
            _emit_stream(csm.stream_claude_subscription(**kwargs))
        else:
            result = csm.run_claude_subscription_batch(**kwargs)
            sys.stdout.write(result.content + ("" if result.content.endswith("\n") else "\n"))
            sys.stdout.flush()
    except Exception as exc:
        logger.error("Claude subscription dispatch failed: %s", exc)
        return 1
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    if args.health:
        health = csm.get_claude_subscription_health()
        sys.stdout.write(json.dumps(health) + "\n")
        return 0 if health["status"] == "HEALTHY" else 1
    prompt = _read_prompt(args, parser)
    if not prompt:
        logger.error("No prompt provided via -p or stdin")
        return 1
    return _run_subscription(args, prompt)


if __name__ == "__main__":
    sys.exit(main())
