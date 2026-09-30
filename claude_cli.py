"""
Claude CLI — Standalone Claude Dispatcher
==========================================
Mirrors `agy --agent X -p PROMPT` semantics for the Claude providers.

Usage examples:
    python claude_cli.py --agent cochem-coder -p "Fix this bug"
    type error.log | python claude_cli.py --agent cochem-debug --stdin
    python claude_cli.py --health
    python claude_cli.py --provider claude --allow-paid-api -p "Explicit paid API call"

Default provider: claude-subscription (Claude Pro/Max OAuth billing through claude.exe).

Subscription guardrails:
* ANTHROPIC_API_KEY is removed from the child environment.
* Dispatch is refused (exit code 1) if the subscription credentials cannot be verified.
* There is never a fallback to paid API billing.
* The paid Anthropic SDK provider ('claude') is refused (exit code 1) unless the caller
  passes the explicit --allow-paid-api safety flag.

Prompts and personas (claude_agent_profiles.AGENT_PROFILES) are passed through temp files,
so large payloads never hit the Windows command-line limit (WinError 206).

Exit code: 0 on success, 1 on subscription, guardrail or subprocess failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import llm_router  # noqa: E402
from llm_router import BaseLLMProvider, get_provider  # noqa: E402
import claude_subscription_manager as csm  # noqa: E402
from claude_subscription_manager import ClaudeSubscriptionError  # noqa: E402

SUBSCRIPTION_PROVIDER = "claude-subscription"
PAID_API_PROVIDER = "claude"
PROVIDER_CHOICES = [SUBSCRIPTION_PROVIDER, PAID_API_PROVIDER, "gemini", "ollama"]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_handlers: list = [logging.StreamHandler(sys.stderr)]
try:
    LOG_DIR = _HERE / ".logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _handlers.append(logging.FileHandler(LOG_DIR / "claude_cli.log", encoding="utf-8"))
except OSError as _exc:
    sys.stderr.write(f"[claude_cli] file logging disabled: {_exc}\n")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=_handlers)
logger = logging.getLogger("claude_cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Claude CLI dispatcher mirroring agy semantics (default: Claude Pro/Max subscription)"
    )
    parser.add_argument("--agent", "-a", default="cochem-coder",
                        help="Agent persona from claude_agent_profiles.AGENT_PROFILES")
    parser.add_argument("--prompt", "-p", default="",
                        help="Prompt text (omit to read from stdin)")
    parser.add_argument("--stdin", action="store_true",
                        help="Read prompt from stdin (pipe mode)")
    parser.add_argument("--stream", action="store_true", default=True,
                        help="Stream output in real-time (default: on)")
    parser.add_argument("--no-stream", dest="stream", action="store_false",
                        help="Disable streaming, print the full response at the end")
    parser.add_argument("--model", "-m", default="",
                        help="Override model string")
    parser.add_argument("--provider", default=SUBSCRIPTION_PROVIDER, choices=PROVIDER_CHOICES,
                        help="LLM provider (default: claude-subscription; 'claude' bills paid API "
                             "credits and additionally requires --allow-paid-api)")
    parser.add_argument("--allow-paid-api", dest="allow_paid_api", action="store_true", default=False,
                        help="Safety flag: explicitly permit the paid Anthropic API provider "
                             "('--provider claude'). Without it, paid dispatch is refused.")
    parser.add_argument("--system", default="",
                        help="Extra system prompt appended to the agent persona")
    parser.add_argument("--timeout", type=float, default=3600.0,
                        help="Subprocess timeout in seconds (claude-subscription)")
    parser.add_argument("--health", action="store_true",
                        help="Print Claude subscription health JSON and exit")
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
    exe = Path(getattr(llm_router, "_CLAUDE_CLI_EXE"))
    try:
        env = csm.get_isolated_subscription_env(claude_exe=exe)
    except ClaudeSubscriptionError as exc:
        logger.error("Subscription billing not verified; refusing to dispatch (no paid API fallback): %s", exc)
        return 1
    if any(k.upper() == "ANTHROPIC_API_KEY" for k in env):
        logger.error("ANTHROPIC_API_KEY still present in dispatch environment; refusing to dispatch")
        return 1

    default_model = getattr(llm_router.ClaudeSubscriptionProvider, "DEFAULT_MODEL", "")
    cli_model = args.model if args.model and args.model != default_model else None
    system_prompt = csm.resolve_persona_system_prompt(args.agent, args.system)
    wrapped = BaseLLMProvider.wrap_xml(prompt, agent_name=args.agent)

    logger.info("Dispatching provider=%s agent=%s model=%s", SUBSCRIPTION_PROVIDER, args.agent,
                cli_model or "default")
    t0 = time.monotonic()
    try:
        if args.stream:
            text = _emit_stream(csm.stream_claude_subscription(
                claude_exe=exe, system_prompt=system_prompt, prompt=wrapped,
                env=env, model=cli_model, timeout=args.timeout,
            ))
            logger.info("Stream complete. elapsed=%.1fs chars=%d", time.monotonic() - t0, len(text))
        else:
            result = csm.run_claude_subscription_batch(
                claude_exe=exe, system_prompt=system_prompt, prompt=wrapped,
                env=env, model=cli_model, timeout=args.timeout,
            )
            sys.stdout.write(result.content)
            if not result.content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
            logger.info("Complete. elapsed=%.1fs input_tokens=%d output_tokens=%d",
                        result.elapsed_sec, result.input_tokens, result.output_tokens)
    except ClaudeSubscriptionError as exc:
        logger.error("Subscription guardrail tripped: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - any dispatch failure maps to exit code 1
        logger.error("Claude subscription dispatch failed: %s", exc)
        return 1
    return 0


def _run_generic(args: argparse.Namespace, prompt: str) -> int:
    if args.provider == PAID_API_PROVIDER:
        if not args.allow_paid_api:
            logger.error(
                "Provider 'claude' uses the Anthropic SDK and bills PAID API credits. "
                "Refusing to dispatch without the explicit --allow-paid-api safety flag "
                "(use '--provider claude-subscription' for Pro/Max subscription billing)."
            )
            return 1
        logger.warning("Provider 'claude' uses the Anthropic SDK and bills PAID API credits "
                       "(--allow-paid-api confirmed).")
    try:
        provider = get_provider(args.provider)
    except Exception as exc:  # noqa: BLE001
        logger.error("Provider initialisation failed: %s", exc)
        return 1
    model = args.model or None
    logger.info("Dispatching provider=%s agent=%s model=%s", args.provider, args.agent, model or "default")
    t0 = time.monotonic()
    try:
        if args.stream:
            text = _emit_stream(provider.stream_generate(
                prompt, system=args.system, model=model, agent_name=args.agent))
            logger.info("Stream complete. elapsed=%.1fs chars=%d", time.monotonic() - t0, len(text))
        else:
            response = provider.generate(prompt, system=args.system, model=model, agent_name=args.agent)
            sys.stdout.write(response.content)
            if not response.content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
            logger.info("Complete. elapsed=%.1fs input_tokens=%s output_tokens=%s",
                        time.monotonic() - t0, response.input_tokens, response.output_tokens)
    except Exception as exc:  # noqa: BLE001
        logger.error("Generation failed: %s", exc)
        return 1
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.health:
        health = csm.get_claude_subscription_health()
        sys.stdout.write(json.dumps(health) + "\n")
        return 0 if health["status"] in ("HEALTHY", "EXPIRING") else 1

    prompt = _read_prompt(args, parser)
    if not prompt:
        logger.error("No prompt provided via -p or stdin.")
        return 1

    if args.provider == SUBSCRIPTION_PROVIDER:
        return _run_subscription(args, prompt)
    return _run_generic(args, prompt)


if __name__ == "__main__":
    sys.exit(main())
