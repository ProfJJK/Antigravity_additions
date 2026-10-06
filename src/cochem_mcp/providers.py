"""Native subscription CLI adapters; provider replies are parsed, never simulated.

This module does not read credential files or make model API calls. Authentication
and inference always belong to the installed Codex or Claude CLI process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
from typing import Any


_PROVIDERS = {"codex", "claude"}
_REMOVED_PREFIXES = (
    "OPENAI_", "ANTHROPIC_", "AZURE_OPENAI_", "CLAUDE_CODE_USE_",
    "CLAUDE_CODE_BEDROCK_", "CLAUDE_CODE_VERTEX_", "CLAUDE_CODE_FOUNDRY_",
    "BEDROCK_", "VERTEX_",
)
_REMOVED_KEYS = {
    "CODEX_API_KEY", "CODEX_BASE_URL", "CODEX_MODEL_PROVIDER", "CODEX_PROVIDER",
    "CLAUDE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "API_TIMEOUT_MS",
    "AWS_BEARER_TOKEN_BEDROCK", "GOOGLE_APPLICATION_CREDENTIALS",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_API_KEY_HELPER",
    "CLAUDE_CODE_SKIP_API_KEY_CHECK",
}
_CLAUDE_SETTINGS_ARGS = ["--setting-sources", ""]


def _provider(provider: str) -> None:
    if provider not in _PROVIDERS:
        raise ValueError("Provider must be codex or claude")


def _is_windows() -> bool:
    return os.name == "nt"


def resolve_executable(provider: str, configured: str | None = None) -> str:
    """Resolve a CLI in this Python process's OS, without crossing WSL boundaries.

    A configured value is one executable name or path, never a command string.
    Windows npm ``codex.cmd`` is resolved here and safely expanded to Node by
    :func:`executable_prefix` before launching it.
    """
    _provider(provider)
    windows = _is_windows()
    if configured is not None and (not configured.strip() or "\x00" in configured):
        raise ValueError("Configured executable must be a nonempty path or command name")
    if configured is not None:
        candidates = [configured]
    elif windows:
        candidates = ["codex.exe", "codex.cmd", "codex"] if provider == "codex" else ["claude.exe", "claude"]
    else:
        candidates = [provider]

    for candidate in candidates:
        if not windows and (PureWindowsPath(candidate).drive or candidate.lower().endswith((".cmd", ".bat", ".ps1"))):
            raise ValueError("Use a native Linux CLI with WSL Python; run Windows CLIs with Windows Python")
        expanded = os.path.expanduser(candidate)
        found = shutil.which(expanded)
        if found is None and Path(expanded).is_file():
            if windows or os.access(expanded, os.X_OK):
                found = str(Path(expanded).absolute())
        if found is None:
            continue
        suffix = Path(found).suffix.lower()
        if not windows:
            if suffix in {".cmd", ".bat", ".ps1"}:
                raise ValueError("A Windows shell wrapper was found in the Linux PATH; install the native Linux CLI")
            # Claude's official npm Linux package may call an ELF file
            # ``claude.exe``. Inspect the target instead of treating its suffix
            # as proof of Windows interop. Opening follows extensionless npm
            # symlinks too, so a PE target cannot bypass this check via a shim.
            with open(found, "rb") as executable_file:
                magic = executable_file.read(4)
            if magic.startswith(b"MZ"):
                raise ValueError("A Windows PE executable was found in the Linux PATH; install the native Linux CLI")
            if suffix == ".exe" and not (magic == b"\x7fELF" or magic.startswith(b"#!")):
                raise ValueError("The .exe CLI is not a native Linux ELF binary or script")
        if suffix in {".bat", ".ps1"} or (suffix == ".cmd" and provider != "codex"):
            raise ValueError("Shell wrappers are unsupported; configure the native CLI executable")
        return os.path.abspath(found)
    raise FileNotFoundError(f"Native {provider} CLI was not found; configure its executable path")


def executable_prefix(provider: str, configured: str | None = None) -> list[str]:
    """Return argv prefix; npm's Windows shim is executed through Node, not cmd.exe."""
    executable = resolve_executable(provider, configured)
    if Path(executable).suffix.lower() != ".cmd":
        return [executable]
    # The official npm global shim is beside node_modules. Do not execute or
    # interpolate the shim's batch contents: prompts never pass through a shell.
    shim_dir = Path(executable).parent
    entrypoint = shim_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if not entrypoint.is_file():
        raise FileNotFoundError("The npm Codex entrypoint beside codex.cmd is missing; reinstall @openai/codex")
    sibling_node = shim_dir / "node.exe"
    node = str(sibling_node) if sibling_node.is_file() else shutil.which("node.exe")
    if not node:
        raise FileNotFoundError("Node.js is required to launch npm Codex; install Node.js or use native codex.exe")
    return [os.path.abspath(node), str(entrypoint)]


def subscription_env(provider: str) -> dict[str, str]:
    """Copy the environment while removing API billing and backend overrides.

    HOME, USERPROFILE, CODEX_HOME, and CLAUDE_CONFIG_DIR remain intact, allowing
    the native CLIs to use and refresh their own existing subscription login.
    No credential files are read and no secret values are logged.
    """
    _provider(provider)
    return {
        key: value for key, value in os.environ.items()
        if key.upper() not in _REMOVED_KEYS
        and not key.upper().startswith(_REMOVED_PREFIXES)
        and not (key.upper().startswith("CLAUDE_CODE_") and "SKIP_AUTH" in key.upper())
    }


def build_command(provider: str, prefix: list[str], model: str, workspace: str) -> list[str]:
    """Build a bounded-permission headless invocation reading its prompt on stdin."""
    _provider(provider)
    if not prefix or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in prefix):
        raise ValueError("Executable prefix must contain nonempty argv strings")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
        raise ValueError("Use an explicit model identifier accepted by the installed CLI")
    if not isinstance(workspace, str) or not workspace or "\x00" in workspace:
        raise ValueError("Workspace must be a nonempty path")
    if provider == "codex":
        return [
            *prefix, "-a", "never", "exec", "--json", "--model", model,
            "--ignore-user-config",
            "--sandbox", "workspace-write", "--cd", workspace,
            "-c", 'model_provider="openai"', "-c", 'forced_login_method="chatgpt"',
            "-",
        ]
    return [
        *prefix, "--print", "--output-format", "json", "--model", model,
        "--permission-mode", "acceptEdits", "--permission-prompts", "none",
        *_CLAUDE_SETTINGS_ARGS, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reported_model(events: list[dict[str, Any]]) -> str | None:
    # Only structured CLI metadata qualifies. Never inspect generated text for
    # a provider/model claim, and never substitute the requested model here.
    models = {event["model"] for event in events if _nonempty_string(event.get("model"))}
    return next(iter(models)) if len(models) == 1 else None


def parse_result(provider: str, stdout: str) -> dict[str, Any]:
    """Require native success records and a CLI session id, failing closed otherwise."""
    _provider(provider)
    if provider == "claude":
        try:
            result = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Claude did not return valid JSON") from exc
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ValueError("Claude did not return a terminal result record")
        if result.get("subtype") != "success" or result.get("is_error") is not False:
            raise ValueError("Claude returned an unsuccessful terminal result")
        if result.get("permission_denials"):
            raise ValueError("Claude reported denied tool permissions; task completion is unverified")
        if not _nonempty_string(result.get("session_id")) or not _nonempty_string(result.get("result")):
            raise ValueError("Claude result is missing its session id or result text")
        reported_model = _reported_model([result])
        model_usage = result.get("modelUsage")
        if reported_model is None and isinstance(model_usage, dict) and len(model_usage) == 1:
            model = next(iter(model_usage))
            if _nonempty_string(model):
                reported_model = model
        return {
            "content": result["result"], "session_id": result["session_id"],
            "reported_model": reported_model,
            "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
            "terminal_success": True,
        }

    events: list[dict[str, Any]] = []
    try:
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("Codex emitted an invalid event record")
            events.append(event)
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise ValueError("Codex did not return valid JSONL") from exc
    if not events or events[-1].get("type") != "turn.completed":
        raise ValueError("Codex did not return a successful terminal turn.completed event")
    if any(event["type"] in {"error", "turn.failed"} for event in events):
        raise ValueError("Codex returned a failure event")
    started = [event for event in events if event["type"] == "thread.started"]
    if len(started) != 1 or not _nonempty_string(started[0].get("thread_id")):
        raise ValueError("Codex is missing an unambiguous thread.started session id")
    messages = []
    for event in events:
        item = event.get("item")
        if event["type"] == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            if not isinstance(item.get("text"), str):
                raise ValueError("Codex agent_message is missing its text")
            messages.append(item["text"])
    if not messages:
        raise ValueError("Codex completed without an agent_message result")
    terminal = events[-1]
    return {
        "content": "\n\n".join(messages), "session_id": started[0]["thread_id"],
        "reported_model": _reported_model(events),
        "usage": terminal.get("usage") if isinstance(terminal.get("usage"), dict) else {},
        "terminal_success": True,
    }


def auth_probe(provider: str, prefix: list[str], env: dict[str, str], timeout: float = 15) -> dict[str, Any]:
    """Check native subscription login without inference or exposing account data."""
    _provider(provider)
    result: dict[str, Any] = {
        "ready": False, "provider": provider, "auth_method": None,
        "reason": "Subscription login was not confirmed", "exit_code": None,
    }
    args = [*prefix, "login", "status"] if provider == "codex" else [*prefix, *_CLAUDE_SETTINGS_ARGS, "auth", "status", "--json"]
    process_options: dict[str, Any] = {}
    if _is_windows():
        process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=timeout, shell=False,
            **process_options,
        )
    except subprocess.TimeoutExpired:
        result["reason"] = "Native login status timed out"
        return result
    except OSError:
        result["reason"] = "Native login status could not be started"
        return result
    result["exit_code"] = process.returncode
    if process.returncode != 0:
        result["reason"] = "Native CLI login status failed; log in through this OS's CLI"
        return result
    if provider == "codex":
        status_text = process.stdout + "\n" + process.stderr
        if re.search(r"^\s*Logged in using ChatGPT\s*$", status_text, re.IGNORECASE | re.MULTILINE):
            result.update(ready=True, auth_method="chatgpt", reason="Native ChatGPT login confirmed")
        return result
    try:
        status = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError):
        result["reason"] = "Claude auth status did not return supported JSON; update Claude CLI"
        return result
    if not isinstance(status, dict) or status.get("loggedIn") is not True:
        return result
    method = str(status.get("authMethod", "")).lower()
    subscription = str(status.get("subscriptionType", "")).lower()
    first_party = status.get("apiProvider") in (None, "firstParty", "first_party")
    subscription_metadata = (
        method in {"", "oauth"} and first_party
        and subscription in {"pro", "max", "team", "enterprise"}
    )
    if first_party and (method == "claude.ai" or subscription_metadata):
        result.update(ready=True, auth_method="claude.ai", reason="Native Claude subscription login confirmed")
    return result
