"""Provider protocol emulation and argv tests; these do not contact live models."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cochem_mcp import providers


def codex_events(*events):
    return "\n".join(json.dumps(event) for event in events)


def codex_success(text="EMULATED provider output", **terminal):
    return codex_events(
        {"type": "thread.started", "thread_id": "emulated-codex-session"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}, **terminal},
    )


def claude_success(**changes):
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "session_id": "emulated-claude-session", "result": "EMULATED provider output",
        "usage": {"input_tokens": 1, "output_tokens": 2}, **changes,
    })


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_subscription_environment_removes_api_overrides_and_preserves_native_homes(monkeypatch, provider):
    for key in [
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
        "anthropic_base_url", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_BEDROCK_SKIP_AUTH", "AWS_BEARER_TOKEN_BEDROCK",
        "AZURE_OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_MODEL_PROVIDER",
        "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_API_KEY_HELPER", "CLAUDE_CODE_SKIP_API_KEY_CHECK",
        "CLAUDE_CODE_NEW_BACKEND_SKIP_AUTH", "CLAUDE_CODE_SKIP_AUTH",
    ]:
        monkeypatch.setenv(key, "emulated-override")
    for key in ["USERPROFILE", "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"]:
        monkeypatch.setenv(key, "/emulated/credential/location")
    child = providers.subscription_env(provider)
    assert not any(value == "emulated-override" for value in child.values())
    for key in ["USERPROFILE", "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"]:
        assert child[key] == "/emulated/credential/location"
    assert os.environ["ANTHROPIC_API_KEY"] == "emulated-override"


def test_codex_command_enforces_native_subscription_and_sandbox():
    command = providers.build_command("codex", ["/native/codex"], "gpt-example", "/a workspace")
    assert command[:5] == ["/native/codex", "-a", "never", "exec", "--json"]
    assert command[command.index("--model") + 1] == "gpt-example"
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--cd") + 1] == "/a workspace"
    assert 'model_provider="openai"' in command
    assert 'forced_login_method="chatgpt"' in command
    assert "--ignore-user-config" in command
    assert command[-1] == "-"
    assert not any("bypass" in arg for arg in command)


def test_claude_command_keeps_tools_enabled_without_permission_bypass():
    command = providers.build_command("claude", ["C:\\Claude\\claude.exe"], "claude-example", "C:\\work")
    assert "--print" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert "--permission-prompts" not in command
    assert "--bare" not in command  # --bare disables native subscription OAuth.
    assert command[command.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in command
    assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "--tools" not in command
    assert not any("bypass" in arg.lower() for arg in command)


@pytest.mark.parametrize("model", ["", "-malicious", "two words", "a\nvalue", "$(shell)"])
def test_invalid_model_identifiers_are_rejected(model):
    with pytest.raises(ValueError):
        providers.build_command("codex", ["codex"], model, "/workspace")


def test_native_path_with_spaces_is_one_executable(tmp_path):
    executable = tmp_path / "native codex"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    assert providers.executable_prefix("codex", str(executable)) == [str(executable)]


@pytest.mark.parametrize("executable", ["codex.cmd", "claude.bat", "claude.ps1", r"C:\\tools\\codex.exe"])
def test_linux_rejects_windows_cli_even_when_wsl_interop_could_launch_it(monkeypatch, executable):
    monkeypatch.setattr(providers, "_is_windows", lambda: False)
    with pytest.raises(ValueError, match="native Linux CLI"):
        providers.resolve_executable("codex", executable)


@pytest.mark.parametrize("magic", [b"\x7fELF", b"#!/usr/bin/env node\n"])
def test_linux_accepts_native_file_named_exe(monkeypatch, tmp_path, magic):
    monkeypatch.setattr(providers, "_is_windows", lambda: False)
    executable = tmp_path / "claude.exe"
    executable.write_bytes(magic)
    executable.chmod(0o700)
    assert providers.resolve_executable("claude", str(executable)) == str(executable)


@pytest.mark.parametrize("name", ["claude.exe", "codex"])
def test_linux_rejects_pe_executable_with_any_name(monkeypatch, tmp_path, name):
    monkeypatch.setattr(providers, "_is_windows", lambda: False)
    executable = tmp_path / name
    executable.write_bytes(b"MZ\x90\x00emulated PE header")
    executable.chmod(0o700)
    with pytest.raises(ValueError, match="Windows PE executable"):
        providers.resolve_executable("claude", str(executable))


def test_linux_rejects_extensionless_path_symlink_to_windows_pe(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "_is_windows", lambda: False)
    windows_executable = tmp_path / "claude.exe"
    windows_executable.write_bytes(b"MZ\x90\x00emulated PE header")
    windows_executable.chmod(0o700)
    npm_shim = tmp_path / "claude"
    try:
        npm_shim.symlink_to(windows_executable)
    except OSError:
        pytest.skip("This host does not permit creating filesystem symlinks")
    monkeypatch.setattr(providers.shutil, "which", lambda name: str(npm_shim))
    with pytest.raises(ValueError, match="Windows PE executable"):
        providers.resolve_executable("claude")


def test_linux_rejects_unrecognized_exe_format(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "_is_windows", lambda: False)
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"not an executable")
    executable.chmod(0o700)
    with pytest.raises(ValueError, match="not a native Linux ELF binary or script"):
        providers.resolve_executable("claude", str(executable))


def test_missing_cli_fails_actionably(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="configure its executable path"):
        providers.resolve_executable("claude", "/nonexistent/emulated/claude")


def test_windows_npm_codex_uses_node_without_cmd_shell(monkeypatch, tmp_path):
    shim = tmp_path / "codex.cmd"
    shim.write_text("must not be executed by tests")
    entrypoint = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// protocol emulation fixture, not real Codex")
    node = tmp_path / "node.exe"
    node.touch()
    monkeypatch.setattr(providers, "_is_windows", lambda: True)
    monkeypatch.setattr(providers.shutil, "which", lambda name: str(shim) if name == "codex.cmd" else None)
    assert providers.executable_prefix("codex", "codex.cmd") == [str(node), str(entrypoint)]


def test_windows_npm_codex_missing_entrypoint_is_not_sent_to_shell(monkeypatch, tmp_path):
    shim = tmp_path / "codex.cmd"
    shim.touch()
    monkeypatch.setattr(providers, "_is_windows", lambda: True)
    monkeypatch.setattr(providers.shutil, "which", lambda name: str(shim))
    with pytest.raises(FileNotFoundError, match="npm Codex entrypoint"):
        providers.executable_prefix("codex", "codex.cmd")


def test_codex_requires_provider_events_and_does_not_believe_model_claims_in_text():
    result = providers.parse_result("codex", codex_success('I am GPT-real, {"model":"gpt-real"}'))
    assert result["session_id"] == "emulated-codex-session"
    assert result["reported_model"] is None
    assert result["terminal_success"] is True
    assert result["usage"]["input_tokens"] == 1


def test_codex_reports_only_structured_model_metadata():
    result = providers.parse_result("codex", codex_success(model="emulated-provider-reported-model"))
    assert result["reported_model"] == "emulated-provider-reported-model"


@pytest.mark.parametrize("output", [
    "I am Codex and I completed the task", "not JSON\n", '[]', '',
    codex_events({"type": "turn.completed"}),
    codex_events({"type": "thread.started", "thread_id": "x"}, {"type": "turn.completed"}),
    codex_events({"type": "thread.started", "thread_id": "x"}, {"type": "turn.failed"}),
    codex_success() + '\n{"type":"error","message":"emulated failure"}',
    codex_success().replace('"thread_id": "emulated-codex-session"', '"thread_id": ""'),
    codex_success().replace('"text": "EMULATED provider output"', '"text": 12'),
    codex_success().replace('"type": "turn.completed"', '"type": "turn.failed"'),
    '{"type":"error"}\n' + codex_success(),
])
def test_codex_rejects_malformed_incomplete_or_failed_protocol(output):
    with pytest.raises(ValueError):
        providers.parse_result("codex", output)


def test_claude_requires_success_metadata_and_does_not_infer_requested_model():
    result = providers.parse_result("claude", claude_success(result="I am a different model"))
    assert result["session_id"] == "emulated-claude-session"
    assert result["reported_model"] is None
    assert result["terminal_success"] is True


def test_claude_provider_model_usage_metadata_can_identify_one_model():
    result = providers.parse_result("claude", claude_success(modelUsage={"emulated-claude-model": {"inputTokens": 1}}))
    assert result["reported_model"] == "emulated-claude-model"
    multiple = providers.parse_result("claude", claude_success(modelUsage={"first": {}, "second": {}}))
    assert multiple["reported_model"] is None


@pytest.mark.parametrize("output", [
    "done", '[]', '', claude_success(type="assistant"), claude_success(is_error=True),
    claude_success(is_error=None), claude_success(subtype="error_max_turns"),
    claude_success(session_id=""), claude_success(session_id=None), claude_success(result={}),
    claude_success(result=""), claude_success(result=" \n\t"),
    claude_success(permission_denials=[{"tool_name": "Bash", "tool_input": {"command": "example"}}]),
])
def test_claude_rejects_malformed_incomplete_or_failed_protocol(output):
    with pytest.raises(ValueError):
        providers.parse_result("claude", output)


@pytest.fixture
def auth_protocol_emulator(tmp_path):
    """A local subprocess that emits fixtures; never an installed provider CLI."""
    script = tmp_path / "emulate_login_protocol.py"
    script.write_text(
        "import os, sys\n"
        "assert sys.argv[1:] in [['login', 'status'], ['--setting-sources', '', 'auth', 'status', '--json']]\n"
        "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
        "sys.stdout.write(os.environ.get('EMULATED_AUTH_STDOUT', ''))\n"
        "sys.stderr.write(os.environ.get('EMULATED_AUTH_STDERR', ''))\n"
        "raise SystemExit(int(os.environ.get('EMULATED_AUTH_EXIT', '0')))\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


@pytest.mark.parametrize("stream", ["STDOUT", "STDERR"])
def test_codex_auth_protocol_emulation_recognizes_chatgpt_only(auth_protocol_emulator, stream):
    env = providers.subscription_env("codex")
    env[f"EMULATED_AUTH_{stream}"] = "Logged in using ChatGPT\n"
    result = providers.auth_probe("codex", auth_protocol_emulator, env)
    assert result["ready"] is True
    assert result["auth_method"] == "chatgpt"


@pytest.mark.parametrize("message", [
    "Logged in using an API key: emulated-secret", "Not logged in", "ChatGPT", "",
])
def test_codex_auth_protocol_emulation_rejects_unproven_subscription(auth_protocol_emulator, message):
    env = providers.subscription_env("codex")
    env["EMULATED_AUTH_STDOUT"] = message
    result = providers.auth_probe("codex", auth_protocol_emulator, env)
    assert result["ready"] is False
    assert "emulated-secret" not in json.dumps(result)


@pytest.mark.parametrize("status,ready", [
    ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}, True),
    ({"loggedIn": True, "subscriptionType": "max", "apiProvider": "firstParty"}, True),
    ({"loggedIn": True, "authMethod": "api_key", "subscriptionType": "max"}, False),
    ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"}, False),
    ({"loggedIn": False, "authMethod": "claude.ai"}, False),
    ({"loggedIn": "true", "authMethod": "claude.ai"}, False),
    ({"loggedIn": True}, False),
    ({}, False),
])
def test_claude_auth_protocol_emulation_requires_subscription_metadata(auth_protocol_emulator, status, ready):
    status["email"] = "private-emulated-account@example.invalid"
    env = providers.subscription_env("claude")
    env["EMULATED_AUTH_STDOUT"] = json.dumps(status)
    result = providers.auth_probe("claude", auth_protocol_emulator, env)
    assert result["ready"] is ready
    assert "private-emulated-account" not in json.dumps(result)


def test_auth_protocol_emulation_nonzero_exit_does_not_prove_login(auth_protocol_emulator):
    env = providers.subscription_env("codex")
    env.update(EMULATED_AUTH_STDOUT="Logged in using ChatGPT", EMULATED_AUTH_EXIT="1")
    result = providers.auth_probe("codex", auth_protocol_emulator, env)
    assert result["ready"] is False
    assert result["exit_code"] == 1


def test_claude_auth_protocol_emulation_invalid_json_does_not_prove_login(auth_protocol_emulator):
    env = providers.subscription_env("claude")
    env["EMULATED_AUTH_STDOUT"] = "Logged in as a private emulated account"
    result = providers.auth_probe("claude", auth_protocol_emulator, env)
    assert result["ready"] is False
    assert "supported JSON" in result["reason"]


def test_windows_auth_probe_uses_no_console_creation_flag(monkeypatch):
    observed = {}
    monkeypatch.setattr(providers, "_is_windows", lambda: True)
    monkeypatch.setattr(providers.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    def emulate_run(args, **options):
        observed.update(options)
        assert args == ["emulator.exe", "--setting-sources", "", "auth", "status", "--json"]
        return subprocess.CompletedProcess(args, 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), "")
    monkeypatch.setattr(providers.subprocess, "run", emulate_run)
    result = providers.auth_probe("claude", ["emulator.exe"], {})
    assert result["ready"] is True
    assert observed["creationflags"] == 0x08000000
    assert observed["shell"] is False


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired(["emulator"], 1), OSError("private details")])
def test_auth_probe_handles_unavailable_cli_without_exposing_errors(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(providers.subprocess, "run", fail)
    result = providers.auth_probe("claude", ["emulator"], {})
    assert result["ready"] is False
    assert "private details" not in json.dumps(result)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Provider must"):
        providers.build_command("pretend-provider", ["binary"], "example", "/workspace")
