"""Offline Claude adapter regressions; fixture subprocesses are not live models."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

import claude_cli
import claude_subscription_manager as csm


AUTH = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "result": "fixture task completed",
           "session_id": "fixture-session", "permission_denials": [], "usage": {"input_tokens": 8, "output_tokens": 3}}


@pytest.fixture
def fixture_cli(tmp_path, monkeypatch):
    """A real child process implementing documented CLI envelopes, without inference."""
    if os.name == "nt":
        pytest.skip("POSIX executable fixture; parser and environment tests also run on Windows")
    script = tmp_path / "claude"
    script.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys, time
from pathlib import Path
argv = sys.argv[1:]
auth = json.loads(os.environ.get("TEST_CLAUDE_AUTH", '{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"max"}'))
if "auth" in argv:
    print(json.dumps(auth))
    raise SystemExit(0)
assert "--print" in argv
assert "--dangerously-skip-permissions" not in argv
assert "--tools" not in argv
assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
assert argv[argv.index("--permission-prompts") + 1] == "none"
assert argv[argv.index("--setting-sources") + 1] == ""
assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
assert "ANTHROPIC_AUTH_TOKEN" not in os.environ
assert "ANTHROPIC_API_KEY" not in os.environ
assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
assert Path(argv[argv.index("--append-system-prompt-file") + 1]).read_text() == "system fixture"
prompt = sys.stdin.read()
mode = os.environ.get("TEST_CLAUDE_MODE", "success")
if mode == "timeout": time.sleep(30)
if mode == "noisy": sys.stderr.write("fixture diagnostic " * 10_000)
if mode == "invalid":
    print("I pretend to be another model")
    raise SystemExit(0)
if mode == "truncated":
    print(json.dumps({"type":"assistant","message":{"content":[{"type":"text","text":"partial"}]}}))
    raise SystemExit(0)
result = {"type":"result", "subtype":"success", "is_error":False,
          "result":json.dumps({"prompt_len":len(prompt), "model":argv[argv.index("--model")+1] if "--model" in argv else None}),
          "session_id":"fixture-session", "usage":{"input_tokens":8,"output_tokens":3}}
if mode == "error":
    result.update(subtype="error_max_turns", is_error=True)
if mode == "denied": result["permission_denials"] = [{"tool_name":"Bash"}]
print(json.dumps(result))
if mode == "nonzero": raise SystemExit(3)
''', encoding="utf-8")
    script.chmod(0o700)
    monkeypatch.setenv("CLAUDE_CLI_EXE", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "not-a-real-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "not-a-real-token")
    return script


def test_environment_preserves_native_config_and_scrubs_overrides():
    env = {"ANTHROPIC_API_KEY": "fixture", "anthropic_auth_token": "fixture", "ANTHROPIC_BASE_URL": "fixture",
           "CLAUDE_CODE_OAUTH_TOKEN": "fixture", "CLAUDE_CODE_USE_BEDROCK": "1",
           "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1",
           "CLAUDE_CONFIG_DIR": "/native/config", "PATH": "/bin"}
    clean = csm.subscription_environment(env)
    assert clean == {"CLAUDE_CONFIG_DIR": "/native/config", "PATH": "/bin"}
    csm.assert_subscription_only_env(clean)
    with pytest.raises(csm.ClaudeSubscriptionError):
        csm.assert_subscription_only_env(env)


@pytest.mark.parametrize("patch", [{"loggedIn": False}, {"authMethod": "api_key"}, {"apiProvider": "bedrock"}, {"subscriptionType": None}, {"subscriptionType": "api"}, {"subscriptionType": []}])
def test_auth_refuses_non_subscription(fixture_cli, monkeypatch, patch):
    monkeypatch.setenv("TEST_CLAUDE_AUTH", json.dumps({**AUTH, **patch}))
    with pytest.raises(csm.ClaudeSubscriptionError):
        csm.get_isolated_subscription_env(claude_exe=fixture_cli)


def test_auth_accepts_native_subscription_without_tier_field(fixture_cli, monkeypatch):
    data = {k: v for k, v in AUTH.items() if k != "subscriptionType"}
    monkeypatch.setenv("TEST_CLAUDE_AUTH", json.dumps(data))
    assert csm.inspect_claude_auth(fixture_cli).is_valid


def test_health_exposes_no_tokens_or_personal_auth_fields(fixture_cli, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_AUTH", json.dumps({**AUTH, "accessToken": "never-report", "email": "private@example.test"}))
    health = csm.get_claude_subscription_health()
    assert health["status"] == "HEALTHY"
    assert health["token_prefix"] == ""
    assert "never-report" not in json.dumps(health)
    assert "private@example.test" not in json.dumps(health)
    assert not hasattr(csm.inspect_claude_credentials(), "access_token")


def test_credential_override_is_rejected_without_reading_file(tmp_path):
    nonexistent = tmp_path / "do-not-open.json"
    assert csm.inspect_claude_credentials(nonexistent).error_type == "unsupported_credentials_path"
    with pytest.raises(csm.ClaudeSubscriptionError):
        csm.get_isolated_subscription_env(nonexistent)


def test_refresh_never_performs_inference(fixture_cli, monkeypatch):
    # Any print invocation would hit this invalid-output fixture; status checks succeed.
    monkeypatch.setenv("TEST_CLAUDE_MODE", "invalid")
    refreshed, status = csm.refresh_claude_token_if_needed(allow_probe=True)
    assert refreshed is False
    assert status.is_valid


def test_model_large_prompt_and_native_tools_reach_real_subprocess(fixture_cli):
    env = csm.get_isolated_subscription_env(claude_exe=fixture_cli)
    result = csm.run_claude_subscription_batch(claude_exe=fixture_cli, system_prompt="system fixture",
        prompt="x" * 100_000, env=env, model="explicit-model", timeout=5)
    assert json.loads(result.content) == {"prompt_len": 100_000, "model": "explicit-model"}
    assert result.session_id == "fixture-session"
    assert result.input_tokens == 8


@pytest.mark.parametrize("mode", ["invalid", "truncated", "error", "denied", "nonzero"])
@pytest.mark.parametrize("stream", [False, True])
def test_missing_or_unsuccessful_terminal_results_fail(fixture_cli, monkeypatch, mode, stream):
    monkeypatch.setenv("TEST_CLAUDE_MODE", mode)
    call = csm.stream_claude_subscription if stream else csm.run_claude_subscription_batch
    with pytest.raises(RuntimeError):
        result = call(claude_exe=fixture_cli, system_prompt="system fixture", prompt="fixture",
                      env=csm.subscription_environment(), timeout=5)
        if stream:
            list(result)


def test_successful_stream_drains_large_stderr(fixture_cli, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "noisy")
    output = "".join(csm.stream_claude_subscription(
        claude_exe=fixture_cli, system_prompt="system fixture", prompt="hello",
        env=csm.subscription_environment(), model="sonnet", timeout=5,
    ))
    assert json.loads(output) == {"prompt_len": 5, "model": "sonnet"}


def test_windows_executable_is_rejected_on_linux(tmp_path):
    if os.name == "nt":
        pytest.skip("Linux/WSL-only operating system restriction")
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"MZfixture")
    with pytest.raises(csm.ClaudeSubscriptionError, match="native Linux"):
        csm.build_claude_command(executable, Path("/tmp/system.txt"))
    with pytest.raises(csm.ClaudeSubscriptionError, match="native Linux"):
        csm._require_exe(executable)


def test_windows_drive_path_is_rejected_on_linux(monkeypatch):
    if os.name == "nt":
        pytest.skip("Linux/WSL-only operating system restriction")
    monkeypatch.setenv("CLAUDE_CLI_EXE", "C:\\Tools\\claude.exe")
    with pytest.raises(csm.ClaudeSubscriptionError, match="Windows Python"):
        csm.default_claude_exe()
    with pytest.raises(csm.ClaudeSubscriptionError, match="Windows Python"):
        csm._require_exe(Path("C:\\Tools\\claude.exe"))


def test_linux_npm_claude_exe_does_not_get_windows_path_conversion(monkeypatch, tmp_path):
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"\x7fELFfixture")
    command = csm.build_claude_command(executable, Path("/tmp/system.txt"))
    assert command[command.index("--append-system-prompt-file") + 1] == str(Path("/tmp/system.txt"))


@pytest.mark.parametrize("stream", [False, True])
def test_timeout_is_a_failure(fixture_cli, monkeypatch, stream):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "timeout")
    call = csm.stream_claude_subscription if stream else csm.run_claude_subscription_batch
    with pytest.raises(RuntimeError, match="timed out"):
        result = call(claude_exe=fixture_cli, system_prompt="system fixture", prompt="fixture",
                      env=csm.subscription_environment(), timeout=0.1)
        if stream:
            list(result)


@pytest.mark.parametrize("stream", [False, True])
def test_large_prompt_timeout_when_real_child_never_reads_stdin(monkeypatch, stream):
    """Exercise real OS pipes/process cleanup, including Windows communicate()."""
    native_popen = csm._popen
    prompt = "\u00e9" * (2 * 1024 * 1024)
    launched = []

    def start_stalled_child(command, env, **kwargs):
        input_file = kwargs["stdin"]
        assert stat.S_ISREG(os.fstat(input_file.fileno()).st_mode)
        assert os.fstat(input_file.fileno()).st_size == len(prompt.encode("utf-8"))
        process = native_popen(
            [sys.executable, "-c", "import time; time.sleep(30)"], env, **kwargs,
        )
        launched.append(process)
        return process

    monkeypatch.setattr(csm, "_popen", start_stalled_child)
    monkeypatch.setattr(csm, "inspect_claude_auth", lambda *args, **kwargs: csm.ClaudeSubscriptionStatus(True))
    call = csm.stream_claude_subscription if stream else csm.run_claude_subscription_batch
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        result = call(claude_exe=Path(sys.executable), system_prompt="fixture", prompt=prompt,
                      env=csm.subscription_environment(), timeout=0.2)
        if stream:
            list(result)
    assert time.monotonic() - started < 10
    assert len(launched) == 1
    assert launched[0].poll() is not None


@pytest.mark.parametrize("payload", ["", "looks successful", "{}", "[]", json.dumps({**SUCCESS, "is_error": "false"}),
                                    json.dumps({**SUCCESS, "result": ""}), json.dumps([SUCCESS, {"type": "assistant"}])])
def test_batch_parser_fails_closed(payload):
    with pytest.raises(RuntimeError):
        csm.parse_claude_result(payload)


def test_cli_rejects_paid_api_and_unknown_provider():
    parser = claude_cli._build_parser()
    for argv in [["--allow-paid-api"], ["--provider", "gemini"], ["--provider", "unknown"]]:
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
    assert parser.parse_args(["--provider", "claude"]).provider == "claude"


def test_dynamic_executable_override(fixture_cli, monkeypatch, tmp_path):
    assert csm.default_claude_exe() == fixture_cli
    second = tmp_path / "another-claude"
    monkeypatch.setenv("CLAUDE_CLI_EXE", str(second))
    assert csm.default_claude_exe() == second


def test_explicit_model_is_not_suppressed_by_standalone_cli(fixture_cli, monkeypatch, capsys):
    monkeypatch.setenv("DEFAULT_CLAUDE_MODEL", "same-explicit-model")
    monkeypatch.setattr(csm, "resolve_persona_system_prompt", lambda *args: "system fixture")
    assert claude_cli.main(["--provider", "claude", "--model", "same-explicit-model", "--no-stream", "-p", "hello"]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "same-explicit-model"
