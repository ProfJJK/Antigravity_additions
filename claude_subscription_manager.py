"""Subscription-only Claude Code execution using the CLI's native authentication.

No credential files are read or tokens copied. ``claude auth status --json`` is
an authentication check, never an inference request. The CLI owns OAuth refresh.
Every generation requires a structured terminal success result; plain prose,
truncated streams, denied tools and errors cannot be reported as successful jobs.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)
REFRESH_THRESHOLD_SEC = 600.0
REFRESH_TIMEOUT_SEC = 30.0
DEFAULT_PERSONA = "You are an expert AI assistant. Use the available native tools to complete the task."
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_STDERR_KEEP_CHARS = 20000
_BILLING_OVERRIDE_KEYS = frozenset({
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
})


class ClaudeSubscriptionError(RuntimeError):
    """Native Claude subscription authentication could not be established."""


@dataclass
class ClaudeSubscriptionStatus:
    is_valid: bool
    subscription_type: str = "unknown"
    auth_method: str = ""
    api_provider: str = ""
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    # Compatibility metadata. Native auth status does not disclose token TTLs.
    rate_limit_tier: str = "unknown"
    time_to_live_sec: float = 0.0
    expires_at: Optional[datetime] = None
    expires_at_ms: int = 0
    refresh_token_expires_at: Optional[datetime] = None
    scopes: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass
class ClaudeCLIResult:
    content: str
    returncode: int
    elapsed_sec: float
    input_tokens: int = 0
    output_tokens: int = 0
    stderr: str = ""
    session_id: str = ""
    model: str = ""


def _billing_override(key: str) -> bool:
    upper = key.upper()
    return upper.startswith("ANTHROPIC_") or upper in _BILLING_OVERRIDE_KEYS


def subscription_environment(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Preserve native login/config paths while removing external billing overrides."""
    source = os.environ if env is None else env
    return {key: value for key, value in source.items() if not _billing_override(key)}


def assert_subscription_only_env(env: Mapping[str, str]) -> None:
    leaked = sorted(key for key in env if _billing_override(key))
    if leaked:
        raise ClaudeSubscriptionError(f"Refusing external billing/authentication overrides: {leaked}")


def _require_native_path(path: str) -> None:
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", path):
        raise ClaudeSubscriptionError(
            "A Windows Claude path cannot run in Linux/WSL. Install and log in to the Linux Claude CLI "
            "inside WSL, or launch this adapter with Windows Python and claude.exe."
        )


def _require_native_binary(executable: Path) -> None:
    """A Linux npm binary can be named claude.exe; PE magic identifies Windows."""
    if os.name == "nt":
        return
    try:
        with executable.open("rb") as stream:
            is_windows = stream.read(2) == b"MZ"
    except OSError:
        return  # Missing files receive the normal installation diagnostic.
    if is_windows:
        raise ClaudeSubscriptionError(
            "Windows Claude executables cannot run through this Linux/WSL adapter. "
            "Use a native Linux Claude installation/login, or launch the adapter with Windows Python."
        )


def default_claude_exe() -> Path:
    """Resolve an explicit CLI override, then PATH, then the native install path."""
    configured = os.environ.get("CLAUDE_CLI_EXE", "").strip()
    if configured:
        _require_native_path(configured)
        found = shutil.which(configured)
        executable = Path(found or configured).expanduser()
        _require_native_binary(executable)
        return executable
    names = ("claude.exe", "claude") if os.name == "nt" else ("claude", "claude.exe")
    for name in names:
        found = shutil.which(name)
        if found:
            executable = Path(found)
            _require_native_binary(executable)
            return executable
    executable = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    _require_native_binary(executable)
    return executable


def _require_exe(claude_exe: Optional[Path]) -> Path:
    if claude_exe is None:
        exe = default_claude_exe()
    else:
        configured = str(claude_exe)
        _require_native_path(configured)
        exe = Path(shutil.which(configured) or configured).expanduser()
    if not exe.is_file():
        raise FileNotFoundError(f"Claude CLI not found at {exe}; install Claude Code and run 'claude auth login' in that operating system")
    _require_native_binary(exe)
    return exe.resolve()


def inspect_claude_auth(
    claude_exe: Optional[Path] = None, *, env: Optional[Mapping[str, str]] = None,
    timeout: float = REFRESH_TIMEOUT_SEC,
) -> ClaudeSubscriptionStatus:
    """Return only native auth metadata, without reading or reporting credentials."""
    child_env = subscription_environment(env)
    try:
        exe = _require_exe(claude_exe)
        proc = subprocess.run(
            [str(exe), "--setting-sources", "", "auth", "status", "--json"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=child_env, creationflags=_NO_WINDOW,
        )
    except ClaudeSubscriptionError as exc:
        return ClaudeSubscriptionStatus(False, error_type="unsupported_platform", error_message=str(exc))
    except (OSError, subprocess.SubprocessError):
        return ClaudeSubscriptionStatus(False, error_type="cli_unavailable", error_message="Claude CLI auth check failed; verify CLAUDE_CLI_EXE and run 'claude auth login'")
    if proc.returncode:
        return ClaudeSubscriptionStatus(False, error_type="auth_failed", error_message="Native Claude authentication check failed; run 'claude auth login' using the configured CLI")
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        return ClaudeSubscriptionStatus(False, error_type="invalid_auth_status", error_message="Claude auth status did not return JSON; update Claude Code")
    method = data.get("authMethod")
    provider = data.get("apiProvider")
    plan = data.get("subscriptionType")
    recognized_plan = isinstance(plan, str) and plan in {"pro", "max", "team", "enterprise"}
    valid_plan = "subscriptionType" not in data or recognized_plan
    valid = data.get("loggedIn") is True and method == "claude.ai" and provider == "firstParty" and valid_plan
    # Whitelist metadata values rather than forwarding arbitrary CLI fields.
    return ClaudeSubscriptionStatus(
        valid,
        subscription_type=plan if recognized_plan else "unknown",
        auth_method="claude.ai" if method == "claude.ai" else "unverified",
        api_provider="firstParty" if provider == "firstParty" else "unverified",
        error_type=None if valid else "subscription_unverified",
        error_message=None if valid else "Native CLI has not confirmed a Claude subscription login; run 'claude auth login' (API billing is disabled)",
    )


def inspect_claude_credentials(creds_path: Optional[Path] = None) -> ClaudeSubscriptionStatus:
    """Compatibility entry point; inspect native auth, never a credential file."""
    if creds_path is not None:
        return ClaudeSubscriptionStatus(False, error_type="unsupported_credentials_path", error_message="Credential-file overrides are unsupported; use native CLI login and CLAUDE_CONFIG_DIR")
    return inspect_claude_auth()


def refresh_claude_token_if_needed(
    threshold_sec: float = REFRESH_THRESHOLD_SEC, creds_path: Optional[Path] = None,
    claude_exe: Optional[Path] = None, allow_probe: bool = False,
) -> Tuple[bool, ClaudeSubscriptionStatus]:
    """Compatibility check. Refresh belongs to the CLI; no inference probe is made."""
    if creds_path is not None:
        return False, inspect_claude_credentials(creds_path)
    return False, inspect_claude_auth(claude_exe)


def get_isolated_subscription_env(
    creds_path: Optional[Path] = None, *, claude_exe: Optional[Path] = None,
    threshold_sec: float = REFRESH_THRESHOLD_SEC,
) -> Dict[str, str]:
    if creds_path is not None:
        raise ClaudeSubscriptionError("Credential-file overrides are unsupported; use native CLI login and CLAUDE_CONFIG_DIR")
    env = subscription_environment()
    status = inspect_claude_auth(claude_exe, env=env)
    if not status.is_valid:
        raise ClaudeSubscriptionError(status.error_message or "Native subscription login unverified")
    return env


def get_claude_subscription_health(
    creds_path: Optional[Path] = None, *, expiring_threshold_sec: float = REFRESH_THRESHOLD_SEC,
) -> Dict[str, Any]:
    status = inspect_claude_credentials(creds_path)
    return {
        "status": "HEALTHY" if status.is_valid else "ERROR",
        "auth_method": status.auth_method, "api_provider": status.api_provider,
        "plan": status.subscription_type, "tier": "unknown",
        "error": status.error_message, "error_type": status.error_type,
        "recommended_action": "none" if status.is_valid else "Run 'claude auth login' using the configured CLI",
        "checked_at_iso": datetime.now(timezone.utc).isoformat(),
        "refresh_managed_by": "native_cli", "ttl_available": False,
        # Empty compatibility fields for older status renderers: no token data.
        "ttl_seconds": 0.0, "expires_at_iso": "", "token_prefix": "",
        "needs_refresh": False, "refresh_token_ttl_seconds": None,
        "refresh_token_expires_at_iso": "", "credentials_path": "",
    }


def resolve_persona_system_prompt(agent_name: str = "", system: str = "") -> str:
    try:
        from claude_agent_profiles import AGENT_PROFILES
    except ImportError:
        AGENT_PROFILES = {}
    base = AGENT_PROFILES.get(agent_name, DEFAULT_PERSONA)
    # Legacy personas refer to a Gemini-specific tool name. Claude uses its own tools.
    base = base.replace("write_to_file", "the native Write and Edit tools")
    return f"{base.strip()}\n\n{system.strip()}" if system.strip() else base.strip()


def _write_temp_text(content: str, prefix: str) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".txt", prefix=prefix, delete=False) as stream:
        stream.write(content)
        return Path(stream.name)


def build_claude_command(
    claude_exe: Path, system_prompt_file: Path, *, model: Optional[str] = None,
    output_format: str = "json",
) -> list[str]:
    """Headless coding with native tools, edit approval, native auth, and stdin input."""
    _require_native_path(str(claude_exe))
    _require_native_binary(claude_exe)
    system_path = str(system_prompt_file)
    cmd = [
        str(claude_exe), "--print", "--permission-mode", "acceptEdits",
        "--permission-prompts", "none",
        "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--append-system-prompt-file", system_path, "--output-format", output_format,
        "--no-session-persistence",
    ]
    if output_format == "stream-json":
        cmd += ["--verbose", "--include-partial-messages"]
    if model:
        cmd += ["--model", model]
    return cmd


def parse_claude_result(stdout: str) -> Dict[str, Any]:
    """Require a terminal success envelope rather than trusting arbitrary text."""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Claude returned no valid terminal JSON result") from exc
    if isinstance(data, list):
        data = data[-1] if data else None
    if not isinstance(data, dict) or data.get("type") != "result":
        raise RuntimeError("Claude returned no terminal result event")
    if data.get("subtype") != "success" or data.get("is_error") is not False:
        errors = data.get("errors")
        detail = data.get("result")
        if not isinstance(detail, str) and isinstance(errors, list):
            detail = "; ".join(item for item in errors if isinstance(item, str))
        detail = detail[:1000] if isinstance(detail, str) else ""
        raise RuntimeError(f"Claude terminal result was unsuccessful ({data.get('subtype', 'unknown')}): {detail}")
    if data.get("permission_denials"):
        raise RuntimeError("Claude reported denied tools; task completion is unverified")
    if not isinstance(data.get("result"), str) or not data["result"].strip():
        raise RuntimeError("Claude returned an empty terminal result")
    return data


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0


def _parse_batch_output(stdout: str) -> Tuple[str, int, int, bool]:
    result = parse_claude_result(stdout)
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return result["result"], _as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens")), False


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL, capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _popen(cmd: list[str], env: Mapping[str, str], **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", env=dict(env), shell=False,
        creationflags=_NO_WINDOW, start_new_session=os.name != "nt", **kwargs,
    )


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")


def run_claude_subscription_batch(
    *, claude_exe: Optional[Path], system_prompt: str, prompt: str,
    env: Mapping[str, str], model: Optional[str] = None, timeout: float = 3600.0,
) -> ClaudeCLIResult:
    _validate_timeout(timeout)
    assert_subscription_only_env(env)
    exe = _require_exe(claude_exe)
    status = inspect_claude_auth(exe, env=env)
    if not status.is_valid:
        raise ClaudeSubscriptionError(status.error_message)
    sys_file = _write_temp_text(system_prompt, "cochem_claude_sys_")
    proc = None
    t0 = time.monotonic()
    try:
        cmd = build_claude_command(exe, sys_file, model=model)
        # On Windows communicate(input=...) can block while writing a full stdin
        # pipe before its timeout applies. An inherited, prefilled file avoids
        # that write and needs no reopening of a named Windows temporary file.
        with tempfile.TemporaryFile(mode="w+b") as stdin_fh:
            stdin_fh.write(prompt.encode("utf-8"))
            stdin_fh.seek(0)
            proc = _popen(cmd, env, stdin=stdin_fh)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _kill_process_tree(proc)
                proc.communicate(timeout=10)
                raise RuntimeError(f"Claude CLI timed out after {timeout:.0f}s") from exc
        if proc.returncode:
            raise RuntimeError(f"Claude CLI failed (rc={proc.returncode}): {stderr[-2000:]}")
        result = parse_claude_result(stdout)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        models = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) else {}
        return ClaudeCLIResult(
            content=result["result"], returncode=proc.returncode, elapsed_sec=time.monotonic() - t0,
            input_tokens=_as_int(usage.get("input_tokens")), output_tokens=_as_int(usage.get("output_tokens")),
            stderr=stderr, session_id=result.get("session_id", ""),
            model=next(iter(models), "") if len(models) == 1 else "",
        )
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc)
            proc.wait(timeout=10)
        sys_file.unlink(missing_ok=True)


def stream_claude_subscription(
    *, claude_exe: Optional[Path], system_prompt: str, prompt: str,
    env: Mapping[str, str], model: Optional[str] = None, timeout: float = 3600.0,
) -> Iterator[str]:
    _validate_timeout(timeout)
    assert_subscription_only_env(env)
    exe = _require_exe(claude_exe)
    status = inspect_claude_auth(exe, env=env)
    if not status.is_valid:
        raise ClaudeSubscriptionError(status.error_message)
    sys_file = _write_temp_text(system_prompt, "cochem_claude_sys_")
    stdin_fh = None
    proc = timer = drain = None
    timed_out = threading.Event()
    stderr_parts: list[str] = []
    try:
        cmd = build_claude_command(exe, sys_file, model=model, output_format="stream-json")
        stdin_fh = tempfile.TemporaryFile(mode="w+b")
        stdin_fh.write(prompt.encode("utf-8"))
        stdin_fh.seek(0)
        proc = _popen(cmd, env, stdin=stdin_fh)

        def drain_stderr() -> None:
            assert proc.stderr is not None
            try:
                for chunk in iter(lambda: proc.stderr.read(8192), ""):
                    stderr_parts.append(chunk)
                    while sum(map(len, stderr_parts)) > _STDERR_KEEP_CHARS and len(stderr_parts) > 1:
                        stderr_parts.pop(0)
            except (ValueError, OSError):
                pass

        drain = threading.Thread(target=drain_stderr, daemon=True)
        drain.start()

        def terminate() -> None:
            timed_out.set()
            _kill_process_tree(proc)

        timer = threading.Timer(timeout, terminate)
        timer.daemon = True
        timer.start()
        result = None
        yielded = False
        assert proc.stdout is not None
        for raw in proc.stdout:
            if not raw.strip():
                continue
            if result is not None:
                raise RuntimeError("Claude emitted events after its terminal result")
            try:
                event = json.loads(raw)
            except ValueError as exc:
                raise RuntimeError("Claude stream contains invalid JSON") from exc
            if not isinstance(event, dict):
                raise RuntimeError("Claude stream contains an invalid event")
            if event.get("type") == "result":
                result = parse_claude_result(raw)
            elif event.get("type") == "stream_event":
                inner = event.get("event")
                delta = inner.get("delta") if isinstance(inner, dict) else None
                if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    yielded = True
                    yield delta["text"]
        rc = proc.wait(timeout=10)
        if timed_out.is_set():
            raise RuntimeError(f"Claude CLI timed out after {timeout:.0f}s")
        if rc:
            raise RuntimeError(f"Claude CLI failed (rc={rc}): {''.join(stderr_parts)[-2000:]}")
        if result is None:
            raise RuntimeError("Claude stream ended without a terminal result")
        if not yielded:
            yield result["result"]
    finally:
        if timer is not None:
            timer.cancel()
        if proc is not None:
            if proc.poll() is None:
                _kill_process_tree(proc)
            proc.wait(timeout=10)
            if drain is not None:
                drain.join(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        sys_file.unlink(missing_ok=True)
        if stdin_fh is not None:
            stdin_fh.close()
