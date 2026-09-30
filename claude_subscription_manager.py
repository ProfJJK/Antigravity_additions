"""
Claude Pro/Max Subscription Manager
===================================
Single source of truth for the Claude subscription credential lifecycle.

Public API
----------
* inspect_claude_credentials()      Read-only, never-raising parser for ~/.claude/.credentials.json.
* refresh_claude_token_if_needed()  Headless native OAuth refresh (staged, verified by re-reading expiresAt).
* get_isolated_subscription_env()   Fail-closed environment with the paid API key removed.
* get_claude_subscription_health()  Machine-readable telemetry for MCP tools and daemons.
* resolve_persona_system_prompt()   Persona resolution through claude_agent_profiles.AGENT_PROFILES.
* run_claude_subscription_batch()   Batch CLI execution. Prompts are passed through temp files.
* stream_claude_subscription()      Streaming CLI execution that cannot deadlock on pipes.

Billing invariant
-----------------
Nothing here ever forwards ANTHROPIC_API_KEY (or Bedrock/Vertex routing switches) to the
claude CLI. Every dispatch path re-checks the environment and raises ClaudeSubscriptionError
if the subscription cannot be verified. Callers must never catch that error in order to fall
back to a pay-per-token provider.

Refresh strategy
----------------
The Claude Code CLI does not document a dedicated "refresh token" command, and not every
build runs its auth lifecycle for every sub-command. Refresh is therefore staged and
verified: after each native invocation the credentials file is re-read, and the refresh is
only reported as successful when ``expiresAt`` actually advanced.

  1. ``claude --version``      cheapest; enough on builds that initialise auth at start-up.
  2. ``claude auth status``    reads the credentials and refreshes an expiring token.
  3. minimal headless probe    a one-word ``--print`` request. It is the one path that is
                               guaranteed to exercise the CLI's OAuth refresh because it
                               performs an authenticated API call. It bills the subscription
                               (never the paid API: the paid key is scrubbed) and is skipped
                               when the refresh token is already known to be expired.

SQLite WAL rule
---------------
This module does file, subprocess and network work only. Do not call it while holding a
SQLite write transaction.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

REFRESH_THRESHOLD_SEC = 600.0
REFRESH_TIMEOUT_SEC = 90.0
REFRESH_PROBE_TIMEOUT_SEC = 120.0
REFRESH_PROBE_PROMPT = "Reply with the single word: ok"
DEFAULT_PERSONA = "You are an expert AI assistant."
_STDERR_KEEP_CHARS = 20000

ERR_MISSING_FILE = "missing_file"
ERR_UNREADABLE = "unreadable_file"
ERR_CORRUPT_JSON = "corrupt_json"
ERR_MISSING_OAUTH = "missing_oauth"
ERR_INVALID_SCHEMA = "invalid_schema"
ERR_EXPIRED = "expired"
ERR_INSPECTION = "inspection_failure"

# Errors that no CLI refresh can repair (a human must run `claude auth login` or fix the file).
_NON_REFRESHABLE_ERRORS = frozenset(
    {ERR_MISSING_FILE, ERR_UNREADABLE, ERR_CORRUPT_JSON, ERR_MISSING_OAUTH, ERR_INVALID_SCHEMA, ERR_INSPECTION}
)

# Variables that would move billing away from the subscription.
_BILLING_OVERRIDE_KEYS = frozenset({"ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"})
# Removed from every dispatch environment. Not configurable: there is no bypass.
_DISPATCH_SCRUBBED_KEYS = _BILLING_OVERRIDE_KEYS | frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})
# The refresh run must authenticate from the credentials file only.
_REFRESH_SCRUBBED_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }
)
_PAID_KEY_PREFIX = "sk-ant-api"

_REFRESH_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ClaudeSubscriptionStatus:
    is_valid: bool
    access_token: str = ""
    refresh_token: str = ""
    expires_at: Optional[datetime] = None  # UTC, timezone-aware
    expires_at_ms: int = 0
    refresh_token_expires_at: Optional[datetime] = None  # UTC, timezone-aware
    time_to_live_sec: float = 0.0
    subscription_type: str = "unknown"
    rate_limit_tier: str = "unknown"
    scopes: List[str] = field(default_factory=list)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    source_path: str = ""


class ClaudeSubscriptionError(RuntimeError):
    """Raised when subscription validation fails or credentials cannot be refreshed."""


@dataclass
class ClaudeCLIResult:
    content: str
    returncode: int
    elapsed_sec: float
    input_tokens: int = 0
    output_tokens: int = 0
    stderr: str = ""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def default_credentials_path() -> Path:
    """~/.claude/.credentials.json, resolved at call time so HOME/USERPROFILE changes apply."""
    return Path.home() / ".claude" / ".credentials.json"


def default_claude_exe() -> Path:
    """CLAUDE_CLI_EXE env > ~/.local/bin/claude(.exe) > first `claude` on PATH."""
    configured = os.environ.get("CLAUDE_CLI_EXE", "").strip()
    if configured:
        return Path(configured)
    candidate = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    if candidate.is_file():
        return candidate
    found = shutil.which("claude")
    return Path(found) if found else candidate


# ---------------------------------------------------------------------------
# Credential inspection (AC1)
# ---------------------------------------------------------------------------

def _error_status(error_type: str, message: str, source_path: str = "") -> ClaudeSubscriptionStatus:
    return ClaudeSubscriptionStatus(
        is_valid=False, error_type=error_type, error_message=message, source_path=source_path
    )


def _parse_epoch_ms(value: Any) -> Optional[Tuple[int, datetime]]:
    """Validate an epoch-millisecond timestamp and convert it to an aware UTC datetime."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return int(value), dt


def _label(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "unknown"


def _parse_oauth(oauth: Mapping[str, Any], source: str) -> ClaudeSubscriptionStatus:
    access = oauth.get("accessToken")
    if not isinstance(access, str) or not access.strip():
        return _error_status(
            ERR_INVALID_SCHEMA,
            "claudeAiOauth.accessToken is missing or is not a non-empty string",
            source,
        )
    parsed_exp = _parse_epoch_ms(oauth.get("expiresAt"))
    if parsed_exp is None:
        return _error_status(
            ERR_INVALID_SCHEMA,
            f"claudeAiOauth.expiresAt is missing or not a valid epoch-millisecond number "
            f"(got {type(oauth.get('expiresAt')).__name__})",
            source,
        )
    expires_ms, expires_at = parsed_exp

    refresh = oauth.get("refreshToken")
    refresh_token = refresh if isinstance(refresh, str) else ""
    parsed_rexp = _parse_epoch_ms(oauth.get("refreshTokenExpiresAt"))
    scopes_raw = oauth.get("scopes")
    scopes = [s for s in scopes_raw if isinstance(s, str)] if isinstance(scopes_raw, list) else []

    ttl = (expires_at - datetime.now(timezone.utc)).total_seconds()
    status = ClaudeSubscriptionStatus(
        is_valid=True,
        access_token=access,
        refresh_token=refresh_token,
        expires_at=expires_at,
        expires_at_ms=expires_ms,
        refresh_token_expires_at=parsed_rexp[1] if parsed_rexp else None,
        time_to_live_sec=ttl,
        subscription_type=_label(oauth.get("subscriptionType")),
        rate_limit_tier=_label(oauth.get("rateLimitTier")),
        scopes=scopes,
        source_path=source,
    )
    if ttl <= 0:
        status.is_valid = False
        status.error_type = ERR_EXPIRED
        status.error_message = (
            f"OAuth access token expired {abs(ttl):.0f}s ago (at {expires_at.isoformat()})"
        )
    return status


def inspect_claude_credentials(creds_path: Optional[Path] = None) -> ClaudeSubscriptionStatus:
    """Parse the Claude CLI OAuth credentials. Never raises; reports failures as structured status."""
    try:
        path = Path(creds_path) if creds_path is not None else default_credentials_path()
        source = str(path)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return _error_status(
                ERR_MISSING_FILE,
                f"Claude credentials file not found at {path}; run 'claude auth login'",
                source,
            )
        except OSError as exc:
            return _error_status(ERR_UNREADABLE, f"Cannot read credentials file {path}: {exc}", source)

        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return _error_status(ERR_CORRUPT_JSON, f"Credentials file is not valid UTF-8: {exc}", source)
        if not text.strip():
            return _error_status(
                ERR_CORRUPT_JSON, f"Credentials file {path} is empty ({len(raw)} bytes)", source
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return _error_status(ERR_CORRUPT_JSON, f"Credentials file is not valid JSON: {exc}", source)
        if not isinstance(data, dict):
            return _error_status(
                ERR_CORRUPT_JSON,
                f"Credentials root must be a JSON object, got {type(data).__name__}",
                source,
            )
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            return _error_status(
                ERR_MISSING_OAUTH,
                "Credentials file has no 'claudeAiOauth' object; run 'claude auth login'",
                source,
            )
        return _parse_oauth(oauth, source)
    except Exception as exc:  # noqa: BLE001 - inspection must never crash daemons
        logger.exception("Unexpected failure while inspecting Claude credentials")
        return _error_status(ERR_INSPECTION, f"Unexpected inspection failure: {exc!r}")


# ---------------------------------------------------------------------------
# Environment isolation helpers (AC3)
# ---------------------------------------------------------------------------

def _scrub(env: Mapping[str, str], keys: frozenset) -> Dict[str, str]:
    """Copy env without the given keys. Case-insensitive, because Windows env names are."""
    return {k: v for k, v in env.items() if k.upper() not in keys}


def assert_subscription_only_env(env: Mapping[str, str]) -> None:
    """Final guard before any dispatch: no paid-billing overrides and an OAuth token present."""
    leaked = sorted({k.upper() for k in env} & _BILLING_OVERRIDE_KEYS)
    if leaked:
        raise ClaudeSubscriptionError(
            f"Refusing to dispatch: paid-billing variables present in environment: {leaked}"
        )
    token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    if not token:
        raise ClaudeSubscriptionError("Refusing to dispatch: no subscription ANTHROPIC_AUTH_TOKEN in environment")
    if token.startswith(_PAID_KEY_PREFIX):
        raise ClaudeSubscriptionError("Refusing to dispatch: ANTHROPIC_AUTH_TOKEN holds a paid API key")


# ---------------------------------------------------------------------------
# Headless native OAuth refresh (AC2)
# ---------------------------------------------------------------------------

def _run_native_refresh_stage(
    exe: Path, args: List[str], stdin_text: Optional[str], timeout: float
) -> Optional["subprocess.CompletedProcess[str]"]:
    """Run one native CLI invocation with the paid key scrubbed. Returns None if it could not run."""
    kwargs: Dict[str, Any] = {}
    if stdin_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin_text
    try:
        return subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_scrub(os.environ, _REFRESH_SCRUBBED_KEYS),
            creationflags=_NO_WINDOW,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("Claude native refresh stage '%s' could not run: %s", " ".join(args), exc)
        return None


def refresh_claude_token_if_needed(
    threshold_sec: float = REFRESH_THRESHOLD_SEC,
    creds_path: Optional[Path] = None,
    claude_exe: Optional[Path] = None,
    allow_probe: bool = True,
) -> Tuple[bool, ClaudeSubscriptionStatus]:
    """Refresh the OAuth token through the native CLI if it expires within threshold_sec.

    Stages (stop at the first that advances ``expiresAt``): ``--version``, ``auth status``,
    then a minimal headless probe request (skipped when allow_probe is False or the refresh
    token is known to be expired). Every stage runs with the paid API key scrubbed.

    Returns (refreshed, current_status). refreshed is True only when the credentials file
    now holds a valid token with a later expiry than before. Never raises for missing
    binaries, CLI failures, timeouts or broken credential files.
    """
    status = inspect_claude_credentials(creds_path)
    if status.error_type in _NON_REFRESHABLE_ERRORS:
        logger.warning("Claude token refresh skipped (%s): %s", status.error_type, status.error_message)
        return False, status
    if status.time_to_live_sec >= threshold_sec:
        return False, status

    with _REFRESH_LOCK:
        # Another thread may have refreshed while we waited for the lock.
        status = inspect_claude_credentials(creds_path)
        if status.error_type in _NON_REFRESHABLE_ERRORS or status.time_to_live_sec >= threshold_sec:
            return False, status

        exe = Path(claude_exe) if claude_exe else default_claude_exe()
        if not exe.is_file():
            logger.error("Claude token refresh impossible: CLI not found at %s", exe)
            return False, status

        rt_exp = status.refresh_token_expires_at
        refresh_token_dead = rt_exp is not None and rt_exp <= datetime.now(timezone.utc)
        if refresh_token_dead:
            logger.warning(
                "Claude refresh token expired at %s; native refresh will likely fail, "
                "'claude auth login' required", rt_exp.isoformat()
            )

        before_ms = status.expires_at_ms
        logger.info(
            "Claude token TTL %.0fs < threshold %.0fs; running native refresh via %s",
            status.time_to_live_sec, threshold_sec, exe,
        )

        stages: List[Tuple[str, List[str], Optional[str], float]] = [
            ("version", ["--version"], None, REFRESH_TIMEOUT_SEC),
            ("auth-status", ["auth", "status"], None, REFRESH_TIMEOUT_SEC),
        ]
        if allow_probe and not refresh_token_dead:
            stages.append(
                (
                    "probe",
                    ["--print", "--output-format", "json", "--tools", ""],
                    REFRESH_PROBE_PROMPT,
                    REFRESH_PROBE_TIMEOUT_SEC,
                )
            )

        after = status
        for name, args, stdin_text, stage_timeout in stages:
            proc = _run_native_refresh_stage(exe, args, stdin_text, stage_timeout)
            if proc is None:
                after = inspect_claude_credentials(creds_path)
                break
            after = inspect_claude_credentials(creds_path)
            if after.is_valid and after.expires_at_ms > before_ms:
                logger.info(
                    "Claude token refreshed by stage '%s' (rc=%s); new expiry %s (TTL %.0fs)",
                    name, proc.returncode,
                    after.expires_at.isoformat() if after.expires_at else "?",
                    after.time_to_live_sec,
                )
                return True, after
            logger.warning(
                "Claude refresh stage '%s' did not extend the token (rc=%s): %s",
                name, proc.returncode, (proc.stderr or proc.stdout or "").strip()[:300],
            )
        return False, after


def get_isolated_subscription_env(
    creds_path: Optional[Path] = None,
    *,
    claude_exe: Optional[Path] = None,
    threshold_sec: float = REFRESH_THRESHOLD_SEC,
) -> Dict[str, str]:
    """Build a subscription-only environment for the claude CLI. Fails closed.

    The paid API key and every billing-routing override are always removed; there is
    deliberately no parameter that can disable the scrub.
    """
    _, status = refresh_claude_token_if_needed(threshold_sec, creds_path, claude_exe)
    if not status.is_valid:
        raise ClaudeSubscriptionError(
            f"Claude subscription billing cannot be verified ({status.error_type}): "
            f"{status.error_message}. Refusing to dispatch; paid API fallback is disabled."
        )
    if status.access_token.startswith(_PAID_KEY_PREFIX):
        raise ClaudeSubscriptionError(
            "Credentials file holds a paid API key instead of an OAuth subscription token; refusing to dispatch."
        )

    env = _scrub(os.environ, _DISPATCH_SCRUBBED_KEYS)
    env["ANTHROPIC_AUTH_TOKEN"] = status.access_token
    return env


# ---------------------------------------------------------------------------
# Health telemetry (AC5)
# ---------------------------------------------------------------------------

def _token_prefix(token: str) -> str:
    return token[: min(16, len(token) // 2)] if token else ""


def get_claude_subscription_health(
    creds_path: Optional[Path] = None,
    *,
    expiring_threshold_sec: float = REFRESH_THRESHOLD_SEC,
) -> Dict[str, Any]:
    """Machine-readable health: status HEALTHY|EXPIRING|EXPIRED|ERROR plus TTL and plan data.

    Never raises and never includes full access or refresh tokens.
    """
    now = datetime.now(timezone.utc)
    path = Path(creds_path) if creds_path is not None else default_credentials_path()
    st = inspect_claude_credentials(path)

    if st.error_type and st.error_type != ERR_EXPIRED:
        status = "ERROR"
    elif st.expires_at is None:
        status = "ERROR"
    elif st.time_to_live_sec <= 0:
        status = "EXPIRED"
    elif st.time_to_live_sec < expiring_threshold_sec:
        status = "EXPIRING"
    else:
        status = "HEALTHY"

    rt_exp = st.refresh_token_expires_at
    rt_ttl = (rt_exp - now).total_seconds() if rt_exp else None
    if status == "HEALTHY":
        action = "none"
    elif status in ("EXPIRING", "EXPIRED"):
        if rt_ttl is not None and rt_ttl <= 0:
            action = "reauthenticate: refresh token expired, run 'claude auth login'"
        else:
            action = "refresh: run .scripts/claude_token_refresh.py (staged native OAuth refresh)"
    elif st.error_type == ERR_UNREADABLE:
        action = "check permissions of the credentials file"
    else:
        action = "reauthenticate: run 'claude auth login'"

    error: Optional[str] = None
    if status in ("EXPIRED", "ERROR"):
        error = st.error_message or f"credentials status {status}"

    return {
        "status": status,
        "ttl_seconds": float(round(st.time_to_live_sec, 3)) if st.expires_at else 0.0,
        "expires_at_iso": st.expires_at.isoformat() if st.expires_at else "",
        "plan": st.subscription_type,
        "tier": st.rate_limit_tier,
        "token_prefix": _token_prefix(st.access_token),
        "error": error,
        "error_type": st.error_type,
        "refresh_token_expires_at_iso": rt_exp.isoformat() if rt_exp else "",
        "refresh_token_ttl_seconds": float(round(rt_ttl, 3)) if rt_ttl is not None else None,
        "has_refresh_token": bool(st.refresh_token),
        "scopes": list(st.scopes),
        "needs_refresh": status in ("EXPIRING", "EXPIRED"),
        "recommended_action": action,
        "checked_at_iso": now.isoformat(),
        "credentials_path": str(path),
    }


# ---------------------------------------------------------------------------
# Personas and tempfile-backed CLI execution (AC4)
# ---------------------------------------------------------------------------

def resolve_persona_system_prompt(agent_name: str = "", system: str = "") -> str:
    """AGENT_PROFILES[agent_name] (or the default persona), followed by the caller's system text."""
    profiles: Mapping[str, Any] = {}
    try:
        from claude_agent_profiles import AGENT_PROFILES  # type: ignore
        profiles = AGENT_PROFILES
    except ImportError as exc:
        logger.warning("claude_agent_profiles unavailable (%s); using default persona", exc)
    base = profiles.get(agent_name) if agent_name else None
    if not isinstance(base, str) or not base.strip():
        base = DEFAULT_PERSONA
    base = base.strip()
    if system and system.strip():
        return f"{base}\n\n{system}"
    return base


def _write_temp_text(content: str, prefix: str) -> Path:
    """Write UTF-8 text to a temp file and close it before the CLI opens it (avoids WinError 32)."""
    fh = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", suffix=".txt", prefix=prefix, delete=False
    )
    path = Path(fh.name)
    try:
        fh.write(content)
    except BaseException:
        fh.close()
        path.unlink(missing_ok=True)
        raise
    fh.close()
    return path


def _cleanup_paths(*paths: Optional[Path]) -> None:
    for p in paths:
        if p is None:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete temp file %s: %s", p, exc)


def _require_exe(claude_exe: Optional[Path]) -> Path:
    exe = Path(claude_exe) if claude_exe else default_claude_exe()
    if not exe.is_file():
        raise FileNotFoundError(
            f"claude CLI not found at {exe}. Install via: irm https://claude.ai/install.ps1 | iex  "
            "then: claude auth login"
        )
    return exe


def build_claude_command(
    claude_exe: Path,
    system_prompt_file: Path,
    *,
    model: Optional[str] = None,
    output_format: str = "json",
) -> List[str]:
    """argv for a headless subscription run. The user prompt goes on stdin, not the command line.

    ``--bare`` is intentionally NOT passed: bare mode restricts authentication to an API key
    or apiKeyHelper and does not read OAuth/keychain credentials, which is incompatible with
    subscription billing via ANTHROPIC_AUTH_TOKEN / the credentials file.
    """
    cmd = [
        str(claude_exe),
        "--print",
        "--permission-mode", "bypassPermissions",
        "--dangerously-skip-permissions",
        "--tools", "",
        "--system-prompt-file", str(system_prompt_file),
        "--output-format", output_format,
    ]
    if output_format == "stream-json":
        cmd += ["--verbose", "--include-partial-messages"]
    if model:
        cmd += ["--model", model]
    return cmd


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _parse_result_dict(data: Mapping[str, Any]) -> Tuple[str, int, int, bool]:
    result = data.get("result")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return (
        result if isinstance(result, str) else "",
        _as_int(usage.get("input_tokens")),
        _as_int(usage.get("output_tokens")),
        bool(data.get("is_error")),
    )


def _parse_batch_output(stdout: str) -> Tuple[str, int, int, bool]:
    """Parse `--output-format json` output. Falls back to raw text if the output is not JSON."""
    text = stdout.strip()
    if not text:
        return "", 0, 0, False
    candidates = [text] + [ln for ln in reversed(text.splitlines()) if ln.strip()]
    for cand in candidates:
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, dict) and ("result" in data or data.get("type") == "result"):
            return _parse_result_dict(data)
        if isinstance(data, list):
            for item in reversed(data):
                if isinstance(item, dict) and item.get("type") == "result":
                    return _parse_result_dict(item)
    return stdout, 0, 0, False


def run_claude_subscription_batch(
    *,
    claude_exe: Optional[Path],
    system_prompt: str,
    prompt: str,
    env: Mapping[str, str],
    model: Optional[str] = None,
    timeout: float = 3600.0,
) -> ClaudeCLIResult:
    """Run one batch generation. Raises RuntimeError on non-zero exit, timeout or error result."""
    assert_subscription_only_env(env)
    exe = _require_exe(claude_exe)
    sys_file: Optional[Path] = None
    prompt_file: Optional[Path] = None
    try:
        sys_file = _write_temp_text(system_prompt, "cochem_claude_sys_")
        prompt_file = _write_temp_text(prompt, "cochem_claude_prompt_")
        cmd = build_claude_command(exe, sys_file, model=model, output_format="json")
        t0 = time.monotonic()
        with open(prompt_file, "rb") as stdin_fh:
            try:
                proc = subprocess.run(
                    cmd,
                    stdin=stdin_fh,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=dict(env),
                    creationflags=_NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"claude CLI timed out after {timeout:.0f}s") from exc
        elapsed = time.monotonic() - t0

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (rc={proc.returncode}):\n"
                f"STDERR: {(proc.stderr or '')[:2000]}\nSTDOUT: {(proc.stdout or '')[:2000]}"
            )

        content, in_tok, out_tok, is_error = _parse_batch_output(proc.stdout or "")
        if is_error:
            raise RuntimeError(f"claude CLI reported an error result: {content[:2000]}")
        return ClaudeCLIResult(
            content=content,
            returncode=proc.returncode,
            elapsed_sec=elapsed,
            input_tokens=in_tok,
            output_tokens=out_tok,
            stderr=proc.stderr or "",
        )
    finally:
        _cleanup_paths(sys_file, prompt_file)


def _text_from_blocks(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""
    return "".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )


def stream_claude_subscription(
    *,
    claude_exe: Optional[Path],
    system_prompt: str,
    prompt: str,
    env: Mapping[str, str],
    model: Optional[str] = None,
    timeout: float = 3600.0,
) -> Iterator[str]:
    """Stream text chunks from the claude CLI (``--output-format stream-json``).

    stderr is drained on a background thread so a chatty CLI can never fill the pipe and
    deadlock. Raises RuntimeError (with the stderr tail) on non-zero exit, timeout, or an
    error result. Temp files are always removed and the child is always reaped, including
    when the consumer abandons the generator early.
    """
    assert_subscription_only_env(env)
    exe = _require_exe(claude_exe)
    sys_file: Optional[Path] = None
    prompt_file: Optional[Path] = None
    stdin_fh = None
    proc: Optional[subprocess.Popen] = None
    timer: Optional[threading.Timer] = None
    drain: Optional[threading.Thread] = None
    stderr_parts: List[str] = []
    timed_out = threading.Event()
    try:
        sys_file = _write_temp_text(system_prompt, "cochem_claude_sys_")
        prompt_file = _write_temp_text(prompt, "cochem_claude_prompt_")
        cmd = build_claude_command(exe, sys_file, model=model, output_format="stream-json")
        stdin_fh = open(prompt_file, "rb")
        proc = subprocess.Popen(
            cmd,
            stdin=stdin_fh,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env),
            creationflags=_NO_WINDOW,
        )

        def _drain_stderr(stream: Any) -> None:
            kept = 0
            try:
                for chunk in iter(lambda: stream.read(8192), ""):
                    stderr_parts.append(chunk)
                    kept += len(chunk)
                    while kept > _STDERR_KEEP_CHARS and len(stderr_parts) > 1:
                        kept -= len(stderr_parts.pop(0))
            except (OSError, ValueError):
                pass

        drain = threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True)
        drain.start()

        def _on_timeout() -> None:
            timed_out.set()
            try:
                proc.kill()  # type: ignore[union-attr]
            except OSError:
                pass

        timer = threading.Timer(timeout, _on_timeout)
        timer.daemon = True
        timer.start()

        saw_text = False
        assistant_text = ""
        result_text = ""
        result_error = False
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                saw_text = True
                yield raw if raw.endswith("\n") else raw + "\n"
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            if etype == "stream_event":
                inner = event.get("event")
                delta = inner.get("delta") if isinstance(inner, dict) else None
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        saw_text = True
                        yield text
            elif etype == "assistant":
                message = event.get("message")
                if isinstance(message, dict):
                    assistant_text = _text_from_blocks(message.get("content")) or assistant_text
            elif etype == "result":
                res = event.get("result")
                result_text = res if isinstance(res, str) else ""
                result_error = bool(event.get("is_error"))

        returncode = proc.wait()
        drain.join(timeout=5.0)
        stderr_text = "".join(stderr_parts)
        if timed_out.is_set():
            raise RuntimeError(f"claude CLI timed out after {timeout:.0f}s")
        if returncode != 0:
            raise RuntimeError(f"claude CLI failed (rc={returncode}):\nSTDERR: {stderr_text[-2000:]}")
        if result_error:
            raise RuntimeError(f"claude CLI reported an error result: {result_text[:2000]}")
        if not saw_text:
            fallback = assistant_text or result_text
            if fallback:
                yield fallback
    finally:
        if timer is not None:
            timer.cancel()
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.SubprocessError:
                pass
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except (OSError, ValueError):
                    pass
        if drain is not None:
            drain.join(timeout=2.0)
        if stdin_fh is not None:
            try:
                stdin_fh.close()
            except OSError:
                pass
        _cleanup_paths(sys_file, prompt_file)
