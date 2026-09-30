"""
LLM Router — Multi-Model Abstraction Layer
==========================================
Provides a unified interface over Gemini (via agy CLI), Claude and local Ollama.
SQLite WAL Rule: db write transactions MUST be committed/released BEFORE calling
generate() on any provider. This module is network-only; no DB operations live here.

Structured output
-----------------
``structured_call(schema, provider, prompt, max_repair_retries=2)`` asks a provider
for JSON matching a Pydantic model. It pulls the JSON out of fences or preambles,
validates it, and sends up to ``max_repair_retries`` repair prompts. Every attempt
is recorded in an in-memory ``AttemptRecord`` ledger. There is no persistence here.

Asymmetric verification
-----------------------
``route_verifier(producer_provider, available)`` returns a provider from a
different model family, or raises ``AsymmetricVerificationError``.

Hardened subprocess execution
-----------------------------
Every child process is launched by ``run_cli``, using ``build_cli_kwargs``:

* ``encoding='utf-8'``, ``errors='replace'`` and ``shell=False``.
* ``CREATE_NO_WINDOW`` on win32.
* Prompts of 40,000 characters or more go through a temp-file path in argv.
  This avoids WinError 206.
* On timeout the whole process tree is killed with ``taskkill /F /T /PID``.

XML Wrapping: providers receive structured XML context for consistent instruction
following.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from pydantic import BaseModel, ValidationError

import claude_subscription_manager as csm
from claude_subscription_manager import ClaudeSubscriptionError  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env from the agentic directory
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).parent / ".env"


def _load_env(env_path: Path) -> None:
    """Minimal .env loader — sets os.environ for missing keys."""
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_env(_ENV_PATH)

# ---------------------------------------------------------------------------
# Shared return type
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    content: str
    provider: str          # "gemini" | "claude" | "claude-subscription" | "ollama"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_sec: float = 0.0


# ---------------------------------------------------------------------------
# Hardened subprocess execution
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"

# Prompts of this many characters or more are handed to the child via a temp file
# whose path is appended to argv (never on the command line itself -> no WinError 206).
LARGE_PROMPT_THRESHOLD = 40_000

_TASKKILL_TIMEOUT_S = 15.0   # upper bound for taskkill itself
_REAP_TIMEOUT_S = 5.0        # time allowed to drain pipes after the tree is killed


class CLIProcessError(subprocess.CalledProcessError):
    """Non-zero exit from a CLI child; str() carries stderr/stdout heads for diagnosis
    (quota detection in QuotaFallbackRouter relies on the stderr text)."""

    def __str__(self) -> str:
        base = super().__str__()
        stderr = self.stderr or ""
        stdout = self.output or ""
        return f"{base}\nSTDERR: {stderr[:2000]}\nSTDOUT: {stdout[:2000]}"


def build_cli_kwargs(timeout: float) -> dict[str, Any]:
    """Return the hardened keyword set every child process in this module uses."""
    kwargs: dict[str, Any] = {
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
        "timeout": timeout,
    }
    if _IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _popen_kwargs(cli_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Adapt build_cli_kwargs() output for Popen (which takes no 'timeout')."""
    adapted = dict(cli_kwargs)
    adapted.pop("timeout", None)
    if not _IS_WINDOWS:
        # Own process group so the whole tree can be signalled on timeout.
        adapted["start_new_session"] = True
    return adapted


def run_cli(
    argv: Sequence[str],
    stdin_text: Optional[str] = None,
    timeout: float = 30.0,
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
    large_prompt_to_file: bool = True,
    check: bool = True,
) -> str:
    """Run a CLI child process with Windows hardening and return its stdout.

    * ``stdin_text`` shorter than LARGE_PROMPT_THRESHOLD is piped to stdin.
      Longer text is written to a UTF-8 temp file under ``tempfile.gettempdir()``.
      That file's path is appended to argv and stdin is closed.
      ``large_prompt_to_file=False`` keeps the stdin path for CLIs that read stdin
      natively. Stdin has no length limit.
    * On timeout the whole process tree is terminated. On win32 this uses
      ``taskkill /F /T /PID``; elsewhere it uses ``killpg``. Then ``TimeoutError``
      is raised.
    * A non-zero exit raises ``CLIProcessError`` when ``check`` is true.
    """
    args = [str(a) for a in argv]
    if not args:
        raise ValueError("run_cli requires a non-empty argv")

    feed = stdin_text
    prompt_path: Optional[str] = None
    if (
        stdin_text is not None
        and large_prompt_to_file
        and len(stdin_text) >= LARGE_PROMPT_THRESHOLD
    ):
        fd, prompt_path = tempfile.mkstemp(
            prefix="llm_prompt_", suffix=".txt", dir=tempfile.gettempdir()
        )
        # Handle is closed before the child starts (Windows file locking).
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(stdin_text)
        args.append(prompt_path)
        feed = None

    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            **_popen_kwargs(build_cli_kwargs(timeout)),
        )
        try:
            stdout, stderr = proc.communicate(input=feed, timeout=timeout)
        except BaseException as exc:
            # Kill the whole tree while the parent is still alive, so /T can walk it.
            if _IS_WINDOWS:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        **build_cli_kwargs(timeout=_TASKKILL_TIMEOUT_S),
                    )
                except (OSError, subprocess.TimeoutExpired) as kill_exc:
                    logger.warning("taskkill failed for PID %s: %s", proc.pid, kill_exc)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError as kill_exc:
                    logger.warning("killpg failed for PID %s: %s", proc.pid, kill_exc)
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.communicate(timeout=_REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                logger.warning("PID %s: output pipes still held after tree kill", proc.pid)
            if isinstance(exc, subprocess.TimeoutExpired):
                raise TimeoutError(
                    f"Command {args[0]!r} exceeded timeout of {timeout}s; "
                    f"process tree rooted at PID {proc.pid} was terminated."
                ) from None
            raise
    finally:
        if prompt_path is not None:
            try:
                os.unlink(prompt_path)
            except OSError as unlink_exc:
                logger.warning("could not remove prompt temp file %s: %s", prompt_path, unlink_exc)

    if check and proc.returncode != 0:
        raise CLIProcessError(proc.returncode, args, output=stdout, stderr=stderr)
    return stdout or ""


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """Abstract contract all LLM providers must satisfy."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        **kwargs,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def stream_generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        agent_name: str = "",
        **kwargs,
    ) -> Iterator[str]:
        ...

    @staticmethod
    def wrap_xml(prompt: str, system: str = "", agent_name: str = "") -> str:
        """
        Wrap prompt in Anthropic-style XML tags.
        Beneficial for both Claude and Gemini on complex agentic tasks.
        """
        parts = []
        if system:
            parts.append(f"<system>\n{system}\n</system>")
        if agent_name:
            parts.append(f"<agent_role>{agent_name}</agent_role>")
        parts.append(f"<instructions>\n{prompt}\n</instructions>")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Gemini Provider — wraps `agy` CLI subprocess
# ---------------------------------------------------------------------------

_AGY_EXE = Path(os.environ.get("AGY_EXE", r"C:\Users\ansac\AppData\Local\agy\bin\agy.exe"))


class GeminiProvider(BaseLLMProvider):
    """Routes tasks to Gemini via the agy.exe CLI subprocess (prompt on stdin)."""

    DEFAULT_MODEL = os.environ.get("DEFAULT_GEMINI_MODEL", "gemini-3.8-flash")

    def _cmd(self, agent_name: str) -> list[str]:
        agent = agent_name or "cochem-coder"
        return [
            str(_AGY_EXE), "--agent", agent,
            "--print-timeout", "60m",
            "--dangerously-skip-permissions",
        ]

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        timeout: int = 3600,
        **kwargs,
    ) -> LLMResponse:
        model = model or self.DEFAULT_MODEL
        wrapped = self.wrap_xml(prompt, system=system, agent_name=agent_name)

        t0 = time.monotonic()
        # agy reads the prompt from stdin, which has no length limit.
        output = run_cli(
            self._cmd(agent_name),
            stdin_text=wrapped,
            timeout=timeout,
            large_prompt_to_file=False,
        )
        elapsed = time.monotonic() - t0

        return LLMResponse(
            content=output,
            provider="gemini",
            model=model,
            elapsed_sec=elapsed,
        )

    def stream_generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        agent_name: str = "",
        timeout: int = 3600,
        **kwargs,
    ) -> Iterator[str]:
        """Yield agy output line by line.

        The prompt goes through stdin rather than ``-p <prompt>`` to avoid WinError 206.
        Output is collected through run_cli and then yielded, so the child is always
        tree-killed on timeout.
        """
        wrapped = self.wrap_xml(prompt, system=system, agent_name=agent_name)
        output = run_cli(
            self._cmd(agent_name),
            stdin_text=wrapped,
            timeout=timeout,
            large_prompt_to_file=False,
        )
        yield from output.splitlines(keepends=True)


# ---------------------------------------------------------------------------
# Claude Provider — direct Anthropic SDK
# ---------------------------------------------------------------------------

class ClaudeProvider(BaseLLMProvider):
    """
    Routes tasks directly to Claude via the Anthropic Python SDK.

    System prompt passed as top-level `system=` param per Anthropic API spec.
    Prompts wrapped in XML tags for optimal instruction following.
    """

    DEFAULT_MODEL = os.environ.get("DEFAULT_CLAUDE_MODEL", "claude-sonnet-5")
    MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "8192"))

    def __init__(self) -> None:
        try:
            import anthropic  # type: ignore
            self._anthropic = anthropic
        except ImportError as e:
            raise ImportError(
                "anthropic SDK not installed. Run: pip install anthropic>=0.40.0"
            ) from e

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "sk-ant-PLACEHOLDER":
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. Add it to d:\\__CoChem\\__agentic\\.env"
            )
        self._client = self._anthropic.Anthropic(api_key=api_key)

    def _build_system(self, system: str, agent_name: str) -> str:
        """Build Claude system prompt from agent profile + caller override."""
        from claude_agent_profiles import AGENT_PROFILES  # type: ignore
        base = AGENT_PROFILES.get(agent_name, "You are an expert AI coding and research assistant.")
        if system:
            return f"{base}\n\n{system}"
        return base

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        **kwargs,
    ) -> LLMResponse:
        model = model or self.DEFAULT_MODEL
        sys_prompt = self._build_system(system, agent_name)
        wrapped_prompt = self.wrap_xml(prompt, agent_name=agent_name)

        t0 = time.monotonic()
        response = self._client.messages.create(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=sys_prompt,
            messages=[{"role": "user", "content": wrapped_prompt}],
        )
        elapsed = time.monotonic() - t0

        content = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        return LLMResponse(
            content=content,
            provider="claude",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            elapsed_sec=elapsed,
        )

    def stream_generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        agent_name: str = "",
        **kwargs,
    ) -> Iterator[str]:
        """Real SSE streaming via Anthropic SDK. Yields text delta chunks."""
        model = model or self.DEFAULT_MODEL
        sys_prompt = self._build_system(system, agent_name)
        wrapped_prompt = self.wrap_xml(prompt, agent_name=agent_name)

        with self._client.messages.stream(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=sys_prompt,
            messages=[{"role": "user", "content": wrapped_prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text


# ---------------------------------------------------------------------------
# Claude Subscription Provider — uses claude CLI (Max plan, zero API cost)
# ---------------------------------------------------------------------------
# Requires: claude.exe installed + logged in via `claude auth login`
# IMPORTANT: Do NOT set ANTHROPIC_API_KEY in env — it overrides subscription billing.

_CLAUDE_CLI_EXE = Path(os.environ.get(
    "CLAUDE_CLI_EXE",
    r"C:\Users\ansac\.local\bin\claude.exe"
))


class ClaudeSubscriptionProvider(BaseLLMProvider):
    """
    Routes via the official Claude Code CLI (claude.exe) using your Max subscription.
    Delegates credential lifecycle, isolated environment scrubbing, and execution
    to claude_subscription_manager.
    """

    DEFAULT_MODEL = os.environ.get("DEFAULT_CLAUDE_MODEL", "claude-sonnet-5")

    def _check_cli(self) -> None:
        if not _CLAUDE_CLI_EXE.exists():
            raise FileNotFoundError(
                f"claude CLI not found at {_CLAUDE_CLI_EXE}. "
                "Install via: irm https://claude.ai/install.ps1 | iex  "
                "then: claude auth login"
            )

    def _get_auth_env(self) -> dict:
        """Load isolated subscription environment via claude_subscription_manager.
        Fails closed by raising ClaudeSubscriptionError if credentials are absent or invalid.
        """
        return csm.get_isolated_subscription_env(claude_exe=_CLAUDE_CLI_EXE)

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        timeout: int = 3600,
        **kwargs,
    ) -> LLMResponse:
        self._check_cli()
        auth_env = self._get_auth_env()
        sys_prompt = csm.resolve_persona_system_prompt(agent_name, system)
        wrapped_prompt = self.wrap_xml(prompt, agent_name=agent_name)
        cli_model = model if model and model != self.DEFAULT_MODEL else None

        result = csm.run_claude_subscription_batch(
            claude_exe=_CLAUDE_CLI_EXE,
            system_prompt=sys_prompt,
            prompt=wrapped_prompt,
            env=auth_env,
            model=cli_model,
            timeout=float(timeout),
        )
        return LLMResponse(
            content=result.content,
            provider="claude-subscription",
            model=model or self.DEFAULT_MODEL,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            elapsed_sec=result.elapsed_sec,
        )

    def stream_generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        agent_name: str = "",
        timeout: int = 3600,
        **kwargs,
    ) -> Iterator[str]:
        self._check_cli()
        auth_env = self._get_auth_env()
        sys_prompt = csm.resolve_persona_system_prompt(agent_name, system)
        wrapped_prompt = self.wrap_xml(prompt, agent_name=agent_name)
        cli_model = model if model and model != self.DEFAULT_MODEL else None

        yield from csm.stream_claude_subscription(
            claude_exe=_CLAUDE_CLI_EXE,
            system_prompt=sys_prompt,
            prompt=wrapped_prompt,
            env=auth_env,
            model=cli_model,
            timeout=float(timeout),
        )


# ---------------------------------------------------------------------------
# Ollama Provider — local models via native /api/chat
# ---------------------------------------------------------------------------
# When this tier is reached the QuotaFallbackRouter sets a PIPELINE_PAUSED flag
# so the Kanban daemon knows to pause and retry later when cloud quota recovers.

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen3.5:9b")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Module-level flag — set True when Ollama tier is reached. Polled by task_work_loop.
PIPELINE_PAUSED_FOR_QUOTA = False


def _normalise_base_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


class OllamaProvider(BaseLLMProvider):
    """
    Routes to a local Ollama instance through the native /api/chat endpoint.
    If the caller supplies ``response_schema`` (as structured_call does), it is sent
    as Ollama's ``format`` field for grammar-constrained JSON decoding.
    """

    def __init__(self, base_url: Optional[str] = None, default_model: Optional[str] = None) -> None:
        resolved = base_url or os.environ.get("OLLAMA_BASE_URL") or _OLLAMA_BASE_URL
        self.base_url = _normalise_base_url(resolved)
        self.default_model = default_model or _OLLAMA_DEFAULT_MODEL

    def _opener(self) -> urllib.request.OpenerDirector:
        host = (urllib.parse.urlsplit(self.base_url).hostname or "").lower()
        if host in _LOOPBACK_HOSTS:
            # Never route loopback traffic through an environment proxy.
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener()

    def _chat_body(
        self, prompt: str, system: str, agent_name: str, model: str, stream: bool, kwargs: dict
    ) -> bytes:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": self.wrap_xml(prompt, agent_name=agent_name)})
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        fmt = kwargs.get("response_schema") or kwargs.get("format")
        if fmt:
            body["format"] = fmt
        return json.dumps(body).encode("utf-8")

    def _request(self, data: bytes) -> urllib.request.Request:
        return urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        timeout: int = 300,
        **kwargs,
    ) -> LLMResponse:
        model = model or self.default_model
        req = self._request(self._chat_body(prompt, system, agent_name, model, False, kwargs))
        t0 = time.monotonic()
        try:
            with self._opener().open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(f"Ollama request failed: {e}") from e
        elapsed = time.monotonic() - t0
        message = data.get("message") or {}
        return LLMResponse(
            content=message.get("content", "") if isinstance(message, dict) else "",
            provider="ollama",
            model=data.get("model") or model,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            elapsed_sec=elapsed,
        )

    def stream_generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        agent_name: str = "",
        timeout: int = 300,
        **kwargs,
    ) -> Iterator[str]:
        model = model or self.default_model
        req = self._request(self._chat_body(prompt, system, agent_name, model, True, kwargs))
        with self._opener().open(req, timeout=timeout) as resp:
            for raw_line in resp:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line)
                except ValueError:
                    logger.debug("Ollama stream: skipping non-JSON line %r", raw_line[:200])
                    continue
                message = chunk.get("message") or {}
                text = message.get("content") if isinstance(message, dict) else None
                if text:
                    yield text


# ---------------------------------------------------------------------------
# Structured output: ledger types, errors, extraction, bounded repair
# ---------------------------------------------------------------------------

@dataclass
class AttemptRecord:
    """One generation attempt inside structured_call (in-memory audit ledger)."""
    attempt_num: int
    raw_output: str
    validation_error: str          # "" when the attempt validated
    timestamp: str                 # ISO 8601, UTC
    model_used: str


@dataclass
class StructuredResult:
    data: Any                      # validated Pydantic model instance
    attempts: list[AttemptRecord] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class StructuredOutputError(Exception):
    """Raised when every attempt (initial + repairs) failed schema validation."""

    def __init__(self, attempts: list[AttemptRecord], final_error: str):
        self.attempts = list(attempts)
        self.final_error = final_error
        self.message = (
            f"Structured output failed validation after {len(self.attempts)} attempt(s): "
            f"{final_error[:1000]}"
        )
        super().__init__(self.message)


class AsymmetricVerificationError(Exception):
    """Raised when no provider from a different model family is available to verify."""


_FENCE_RE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n?(.*?)```", re.DOTALL)
_MAX_SCAN_CANDIDATES = 20


def _extract_json_candidates(text: str) -> list[Any]:
    """Return parsed JSON values found in model output, in order of preference.

    Preference order:
    1. The whole output.
    2. Fenced blocks.
    3. Embedded objects or arrays that follow a preamble.
    """
    stripped = text.strip()
    if stripped:
        try:
            return [json.loads(stripped)]
        except ValueError:
            pass

    fenced: list[Any] = []
    for match in _FENCE_RE.finditer(text):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            fenced.append(json.loads(block))
        except ValueError:
            continue
    if fenced:
        return fenced

    decoder = json.JSONDecoder()
    found: list[Any] = []
    idx = 0
    while idx < len(text) and len(found) < _MAX_SCAN_CANDIDATES:
        if text[idx] in "{[":
            try:
                obj, end = decoder.raw_decode(text, idx)
            except ValueError:
                idx += 1
                continue
            found.append(obj)
            idx = end
        else:
            idx += 1
    return found


def _validate_output(schema: type[BaseModel], raw: str) -> tuple[Optional[BaseModel], str]:
    """Validate raw model output; returns (instance, "") or (None, error_text)."""
    candidates = _extract_json_candidates(raw)
    if not candidates:
        try:
            return schema.model_validate_json(raw.strip()), ""
        except ValidationError as exc:
            return None, f"No JSON document could be extracted from the model output.\n{exc}"
    first_error: Optional[str] = None
    for candidate in candidates:
        try:
            return schema.model_validate(candidate), ""
        except ValidationError as exc:
            if first_error is None:
                first_error = str(exc)
    return None, first_error or "JSON validation failed"


def _structured_prompt(prompt: str, schema_text: str) -> str:
    return (
        f"{prompt}\n\n"
        "<output_contract>\n"
        "Respond with ONLY a single JSON value that validates against the JSON Schema below. "
        "No markdown fences, no preamble, no trailing commentary.\n"
        f"<json_schema>\n{schema_text}\n</json_schema>\n"
        "</output_contract>"
    )


def _repair_prompt(prompt: str, schema_text: str, raw: str, error: str, attempt_num: int) -> str:
    return (
        f"{_structured_prompt(prompt, schema_text)}\n\n"
        f'<repair_request attempt="{attempt_num}">\n'
        "Your previous response was rejected: it did not pass JSON schema validation.\n"
        f"<validation_error>\n{error[:4000]}\n</validation_error>\n"
        f"<previous_response>\n{raw[:4000]}\n</previous_response>\n"
        "Return ONLY one valid JSON object conforming to the JSON Schema above. "
        "Do not include markdown fences, explanations, or any text before or after the JSON.\n"
        "</repair_request>"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def structured_call(
    schema: type[BaseModel],
    provider: BaseLLMProvider,
    prompt: str,
    *,
    max_repair_retries: int = 2,
    system: str = "",
    agent_name: str = "",
    model: Optional[str] = None,
    **generate_kwargs: Any,
) -> StructuredResult:
    """Generate output from ``provider`` and validate it against the ``schema`` Pydantic model.

    Sequence:
    * The first attempt sends ``prompt`` plus the JSON-schema contract.
    * Each failure is followed by a repair prompt, up to ``max_repair_retries`` times.
      The repair prompt quotes the validation error and the previous output.
    * Provider or network exceptions propagate unchanged and are not retried.
    * The JSON schema is passed to ``provider.generate`` as ``response_schema=``.
      Providers with native constrained decoding, such as Ollama's ``format``, use it.

    Raises ``StructuredOutputError`` carrying ``1 + max_repair_retries`` records.
    """
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise TypeError(f"schema must be a pydantic BaseModel subclass, got {schema!r}")
    retries = max(0, int(max_repair_retries))
    json_schema = schema.model_json_schema()
    schema_text = json.dumps(json_schema, indent=2, ensure_ascii=False)

    attempts: list[AttemptRecord] = []
    input_tokens = 0
    output_tokens = 0
    current_prompt = _structured_prompt(prompt, schema_text)
    default_label = getattr(provider, "name", None) or type(provider).__name__

    for attempt_num in range(1, retries + 2):
        response = provider.generate(
            current_prompt,
            system=system,
            model=model,
            agent_name=agent_name,
            response_schema=json_schema,
            **generate_kwargs,
        )
        raw = getattr(response, "content", response)
        raw = raw if isinstance(raw, str) else str(raw)
        model_used = str(
            getattr(response, "model", None)
            or model
            or getattr(provider, "default_model", None)
            or getattr(provider, "DEFAULT_MODEL", "")
            or ""
        )
        input_tokens += int(getattr(response, "input_tokens", 0) or 0)
        output_tokens += int(getattr(response, "output_tokens", 0) or 0)

        instance, error = _validate_output(schema, raw)
        attempts.append(
            AttemptRecord(
                attempt_num=attempt_num,
                raw_output=raw,
                validation_error=error,
                timestamp=_utc_now(),
                model_used=model_used,
            )
        )
        if instance is not None:
            return StructuredResult(
                data=instance,
                attempts=attempts,
                provider=str(getattr(response, "provider", None) or default_label),
                model=model_used,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        logger.warning(
            "structured_call: attempt %d/%d failed validation for %s",
            attempt_num, retries + 1, schema.__name__,
        )
        if attempt_num <= retries:
            current_prompt = _repair_prompt(prompt, schema_text, raw, error, attempt_num)

    raise StructuredOutputError(attempts, attempts[-1].validation_error)


# ---------------------------------------------------------------------------
# Asymmetric verification routing
# ---------------------------------------------------------------------------

_PROVIDER_FAMILIES = {
    "claude": "anthropic",
    "claude-subscription": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google",
    "agy": "google",
    "google": "google",
    "ollama": "local",
}


def provider_family(provider_name: str) -> str:
    """Map a provider name to its model family (unknown names are their own family)."""
    key = provider_name.strip().lower()
    return _PROVIDER_FAMILIES.get(key, key)


def route_verifier(producer_provider: str, available: Sequence[str]) -> str:
    """Return the first available provider whose model family differs from the producer's.

    Raises AsymmetricVerificationError instead of ever reusing the producer's family.
    """
    producer_family = provider_family(producer_provider)
    for candidate in available:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        name = candidate.strip()
        if provider_family(name) != producer_family:
            return name
    raise AsymmetricVerificationError(
        f"No verifier from a model family other than {producer_family!r} "
        f"(producer {producer_provider!r}) among available providers {list(available)!r}."
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def get_provider(provider: Optional[str] = None) -> BaseLLMProvider:
    """
    Return the appropriate provider instance.

    Supported values: 'gemini' | 'claude' (SDK) | 'claude-subscription' (Max CLI) | 'ollama'
    Priority: explicit arg > DEFAULT_LLM_PROVIDER env > gemini
    """
    p = (provider or os.environ.get("DEFAULT_LLM_PROVIDER", "gemini")).lower().strip()
    if p == "claude-subscription":
        return ClaudeSubscriptionProvider()
    if p == "claude":
        return ClaudeProvider()
    if p == "ollama":
        return OllamaProvider()
    return GeminiProvider()


# ---------------------------------------------------------------------------
# Model registry — canonical assignments + fallback chains
# ---------------------------------------------------------------------------
_agentic_dir = str(Path(__file__).resolve().parent)
if _agentic_dir not in sys.path:
    sys.path.insert(0, _agentic_dir)

try:
    from v3.legacy_v2_intercept import intercept_legacy_cli as _intercept_legacy_cli
    _intercept_legacy_cli()          # no-op unless __main__ is cochem_kanban.py / pipeline_v2.py
except ImportError:
    pass

try:
    from v2.MODEL_REGISTRY_V2 import MODEL_REGISTRY
except ImportError:
    from MODEL_REGISTRY_V2 import MODEL_REGISTRY


# Error signatures that indicate quota/rate exhaustion (not hard failures)
_QUOTA_ERRORS = (
    "429", "quota", "rate_limit", "resource_exhausted", "resourceexhausted",
    "overloaded", "too many requests", "capacity",
)


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(sig in msg for sig in _QUOTA_ERRORS)


class QuotaFallbackRouter:
    """
    Wraps the MODEL_REGISTRY with automatic quota-aware fallback.

    On 429 / quota_exceeded / overloaded errors, steps through the
    fallback chain for the given agent and retries with the next model.

    Usage:
        router = QuotaFallbackRouter()
        response = router.generate("cochem-coder", prompt)
    """

    _DISABLED_TTL_S = 90  # seconds before a quota-disabled cloud key is retried

    def __init__(self) -> None:
        self._disabled: set = set()
        self._disabled_at: dict = {}

    def _entry(self, agent_name: str) -> dict:
        return MODEL_REGISTRY.get(
            agent_name,
            {"provider": "gemini", "model": "gemini-3.1-pro-preview",
             "fallbacks": [("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "qwen3.5:9b")]},
        )

    def generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        system: str = "",
        stream: bool = False,
        exclude: Optional[set[tuple[str, str]]] = None,
        **kwargs,
    ) -> "LLMResponse":
        entry = self._entry(agent_name)
        chain = [(entry["provider"], entry["model"])] + entry.get("fallbacks", [])

        last_exc: Optional[Exception] = None
        for provider_name, model in chain:
            key = (provider_name, model)

            if exclude and key in exclude:
                logger.info(f"  [QUOTA-ROUTER] Skipping {key} (in exclude set)")
                continue

            if provider_name == "halt" or provider_name == "ollama":
                global PIPELINE_PAUSED_FOR_QUOTA
                PIPELINE_PAUSED_FOR_QUOTA = True
                if "llm_router" in sys.modules:
                    sys.modules["llm_router"].PIPELINE_PAUSED_FOR_QUOTA = True
                logger.warning(
                    f"  [QUOTA-ROUTER] ALL cloud providers exhausted for '{agent_name}'. "
                    f"PIPELINE_PAUSED_FOR_QUOTA=True — halting gracefully. "
                    f"Hardware daemon will restart when quota recovers."
                )
                raise RuntimeError(
                    f"All cloud providers exhausted for '{agent_name}'. "
                    f"Pipeline paused — hardware daemon will restart when quota clears."
                )

            if key in self._disabled:
                age = time.monotonic() - self._disabled_at.get(key, 0)
                if age > self._DISABLED_TTL_S:
                    logger.info(
                        f'  [QUOTA-ROUTER] {provider_name}/{model} window recovered '
                        f'after {age:.0f}s — retrying.'
                    )
                    self._disabled.discard(key)
                    self._disabled_at.pop(key, None)
                else:
                    logger.warning(
                        f'  [QUOTA-ROUTER] Skipping {provider_name}/{model} '
                        f'(disabled {age:.0f}s ago, TTL={self._DISABLED_TTL_S}s)'
                    )
                    continue

            try:
                provider = get_provider(provider_name)
                logger.info(f"  [QUOTA-ROUTER] {agent_name} → {provider_name}/{model}")
                return provider.generate(prompt, system=system, model=model, agent_name=agent_name, **kwargs)
            except Exception as exc:
                if _is_quota_error(exc):
                    logger.warning(
                        f"  [QUOTA-ROUTER] {provider_name}/{model} quota exhausted: {exc}. "
                        f"Disabling for this session and trying next fallback."
                    )
                    self._disabled.add(key)
                    self._disabled_at[key] = time.monotonic()
                    last_exc = exc
                    continue
                last_exc = exc
                continue  # HARD FAILURES ALSO WALK CHAIN (as designed in v2)

        raise RuntimeError(
            f"All models exhausted for agent '{agent_name}'. "
            f"Fallback chain: {chain}. Last error: {last_exc}"
        )

    def stream_generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        system: str = "",
        exclude: Optional[set[tuple[str, str]]] = None,
        **kwargs,
    ) -> "Iterator[str]":
        entry = self._entry(agent_name)
        chain = [(entry["provider"], entry["model"])] + entry.get("fallbacks", [])

        for provider_name, model in chain:
            key = (provider_name, model)

            if exclude and key in exclude:
                continue

            if provider_name == "halt" or provider_name == "ollama":
                global PIPELINE_PAUSED_FOR_QUOTA
                PIPELINE_PAUSED_FOR_QUOTA = True
                if "llm_router" in sys.modules:
                    sys.modules["llm_router"].PIPELINE_PAUSED_FOR_QUOTA = True
                logger.warning(
                    f"  [QUOTA-ROUTER] ALL cloud providers exhausted for '{agent_name}'. "
                    f"PIPELINE_PAUSED_FOR_QUOTA=True — halting gracefully."
                )
                raise RuntimeError(f"All cloud providers exhausted for '{agent_name}'.")

            if key in self._disabled:
                age = time.monotonic() - self._disabled_at.get(key, 0)
                if age > self._DISABLED_TTL_S:
                    self._disabled.discard(key)
                    self._disabled_at.pop(key, None)
                else:
                    continue

            try:
                provider = get_provider(provider_name)
                yield from provider.stream_generate(prompt, system=system, model=model, agent_name=agent_name, **kwargs)
                return
            except Exception as exc:
                if _is_quota_error(exc):
                    self._disabled.add(key)
                    self._disabled_at[key] = time.monotonic()
                    continue
                continue

        raise RuntimeError(f"All models exhausted for agent '{agent_name}'. Fallback chain: {chain}")


# ---------------------------------------------------------------------------
# High-level dispatch helper
# ---------------------------------------------------------------------------

def route(
    prompt: str,
    *,
    agent_name: str = "cochem-coder",
    system: str = "",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    stream: bool = False,
    timeout: int = 3600,
    **kwargs,
) -> LLMResponse:
    """
    Convenience wrapper: routes by agent name or explicit provider override.
    """
    if provider:
        p = get_provider(provider)
        return p.generate(prompt, system=system, model=model, agent_name=agent_name, timeout=timeout, **kwargs)

    router = QuotaFallbackRouter()
    return router.generate(agent_name, prompt, system=system, stream=stream, timeout=timeout, **kwargs)
