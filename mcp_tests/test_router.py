"""Regression checks for provider selection, never live inference."""
import pytest
import llm_router


def test_provider_names_do_not_fall_back_to_gemini():
    with pytest.raises(ValueError, match="Unknown CLI provider"):
        llm_router.get_provider("codec")
    assert isinstance(llm_router.get_provider("codex"), llm_router.CodexSubscriptionProvider)
    assert isinstance(llm_router.get_provider("claude"), llm_router.ClaudeSubscriptionProvider)


def test_unconfigured_agent_cannot_choose_another_provider():
    with pytest.raises(ValueError, match="No explicit model registry"):
        llm_router.QuotaFallbackRouter()._entry("unconfigured-test-agent")


def test_hard_failures_do_not_invoke_fallback(monkeypatch):
    calls = []
    class Unauthenticated:
        def generate(self, *args, **kwargs):
            calls.append("codex")
            raise RuntimeError("Authentication unavailable")
        def stream_generate(self, *args, **kwargs):
            calls.append("codex-stream")
            raise RuntimeError("Authentication unavailable")
            yield
    monkeypatch.setattr(llm_router, "MODEL_REGISTRY", {
        "test": {"provider": "codex", "model": "test", "fallbacks": [("gemini", "test")]}
    })
    monkeypatch.setattr(llm_router, "get_provider", lambda provider: Unauthenticated())
    with pytest.raises(RuntimeError, match="Authentication"):
        llm_router.QuotaFallbackRouter().generate("test", "test")
    with pytest.raises(RuntimeError, match="Authentication"):
        list(llm_router.QuotaFallbackRouter().stream_generate("test", "test"))
    assert calls == ["codex", "codex-stream"]


def test_codex_family_requires_another_provider_for_verification():
    assert llm_router.provider_family("codex") == "openai"
    assert llm_router.route_verifier("codex", ["codex", "claude-subscription"]) == "claude-subscription"


def test_native_stdin_large_prompt_uses_regular_file_and_preserves_unicode():
    import json
    import sys
    prompt = 'π' * 70000
    # The transport emits UTF-8 bytes; the protocol emulator must decode that
    # contract explicitly, independent of Windows' locale-default Python stdin.
    script = ("import json,os,stat,sys; sys.stdin.reconfigure(encoding='utf-8'); "
              "print(json.dumps([stat.S_ISREG(os.fstat(0).st_mode), len(sys.stdin.read())]))")
    output = llm_router.run_cli([sys.executable, '-c', script], stdin_text=prompt,
                               large_prompt_to_file=False)
    assert json.loads(output) == [True, len(prompt)]
