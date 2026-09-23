"""
LLM Router — Multi-Model Abstraction Layer
==========================================
Provides a unified interface over Gemini (via agy CLI) and Claude (via Anthropic SDK).

SQLite WAL Rule: db write transactions MUST be committed/released BEFORE calling
generate() on any provider. This module is network-only; no DB operations live here.

XML Wrapping: Both providers receive structured XML context for consistent instruction
following. Gemini benefits from XML for complex agentic tasks (no degradation found);
Claude is designed for it.

Process safety: every CLI spawn goes through proc_supervisor (Job Object ownership,
per-call tree kill on timeout, machine-wide llm_slot cap). Quota state is shared
across processes via QuotaBreaker (.state/quota_breaker.json).
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import logging
import time
import atexit
import contextlib
import threading
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import psutil

try:
    import msvcrt
except ImportError:  # non-Windows
    msvcrt = None
    import fcntl

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent

try:
    from proc_supervisor import Supervisor, Deadline, DeadlineExceeded, llm_slot
except ImportError:
    sys.path.insert(0, str(_HERE / ".scripts"))
    from proc_supervisor import Supervisor, Deadline, DeadlineExceeded, llm_slot

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

# Per-attempt CLI timeout. Callers may still pass an explicit timeout=.
_LLM_CALL_TIMEOUT_S = float(os.environ.get("LLM_CALL_TIMEOUT_S", "900"))

# ---------------------------------------------------------------------------
# Shared quota circuit breaker (cross-process, file-backed)
# ---------------------------------------------------------------------------

_BREAKER_PATH = Path(os.environ.get(
    "COCHEM_QUOTA_BREAKER_PATH", str(_HERE / ".state" / "quota_breaker.json")))


class QuotaExhausted(RuntimeError):
    """
    No cloud entry in the fallback chain is usable. Subclasses RuntimeError and keeps the
    legacy "All cloud providers exhausted ... Pipeline paused" wording so existing callers
    (task_work_loop.safe_chat_cli) still recognise it.
    """


class QuotaBreaker:
    """
    Quota / failure circuit breaker shared by every process on the machine.

    State lives in one small JSON file:
        entries[<provider>/<model>] = {strikes, hard_failures, open_until, probe, last_error}
        paused_for_quota            = replaces the old per-process PIPELINE_PAUSED_FOR_QUOTA global
        spawn_bucket                = {tokens, ts} machine-wide CLI spawn token bucket

    - Polling reads (paused, open_remaining) are cached for CACHE_TTL_S. allow() reads fresh
      because it gates a spawn.
    - Writes happen only when state changes: read-modify-write under a lock file, then an
      atomic temp-file + os.replace.
    - Cooldown per key is exponential: 90 s -> 180 s -> 360 s ... capped at 30 min, reset on
      success. Failures reported while the key is already open do not escalate, so N
      parallel processes that fail together count as one strike.
    - 3 consecutive hard (non-quota) failures also open the circuit.
    - When a cooldown expires, exactly one caller machine-wide gets a PROBE_LEASE_S probe
      lease (half-open). Everyone else keeps skipping, so expiry does not release a herd.
    """

    BASE_COOLDOWN_S = 90.0
    MAX_COOLDOWN_S = 1800.0
    HARD_FAILURE_THRESHOLD = 3
    PROBE_LEASE_S = 60.0
    CACHE_TTL_S = 5.0
    LOCK_TIMEOUT_S = 10.0
    SPAWN_RATE_PER_S = float(os.environ.get("COCHEM_SPAWN_RATE_PER_S", "4"))

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock_path = path.with_suffix(".lock")
        self._tlock = threading.RLock()
        self._cache: Optional[dict] = None
        self._cache_at = 0.0

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    # ── storage ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        for _ in range(5):
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (PermissionError, json.JSONDecodeError):
                time.sleep(0.05)  # Windows: target briefly locked by a concurrent os.replace
        logger.warning(f"[QUOTA-BREAKER] Could not read {self.path}; treating state as empty.")
        return {}

    def _write(self, state: dict) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        for _ in range(20):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.05)  # a reader has the target open
        tmp.unlink(missing_ok=True)
        logger.error(f"[QUOTA-BREAKER] Failed to persist {self.path}; state change lost.")

    @contextlib.contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a+b") as fh:
            give_up = time.monotonic() + self.LOCK_TIMEOUT_S
            locked = False
            while not locked:
                try:
                    fh.seek(0)
                    if msvcrt:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError:
                    if time.monotonic() > give_up:
                        logger.warning("[QUOTA-BREAKER] Lock timeout; proceeding unlocked.")
                        break
                    time.sleep(0.02)
            try:
                yield
            finally:
                if locked:
                    fh.seek(0)
                    if msvcrt:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _state(self, fresh: bool = False) -> dict:
        """Cached read (<= CACHE_TTL_S stale) unless fresh=True. Never mutate the returned dict."""
        with self._tlock:
            now = time.monotonic()
            if fresh or self._cache is None or now - self._cache_at > self.CACHE_TTL_S:
                self._cache = self._load()
                self._cache_at = now
            return self._cache

    @contextlib.contextmanager
    def _mutate(self):
        """Locked read-modify-write on fresh state; persists only if something changed."""
        with self._tlock, self._file_lock():
            state = self._load()
            before = json.dumps(state, sort_keys=True)
            yield state
            if json.dumps(state, sort_keys=True) != before:
                self._write(state)
            self._cache = state
            self._cache_at = time.monotonic()

    # ── circuit state ─────────────────────────────────────────────────────

    def open_remaining(self, provider: str, model: str) -> float:
        e = self._state().get("entries", {}).get(self._key(provider, model))
        if not e or not e.get("strikes"):
            return 0.0
        return max(0.0, e.get("open_until", 0.0) - time.time())

    def allow(self, provider: str, model: str) -> bool:
        """True if the caller may attempt (provider, model) now; claims the probe lease if half-open."""
        key = self._key(provider, model)
        # Uncached: this gates a multi-second spawn, and a 5 s stale view is exactly the herd window.
        e = self._state(fresh=True).get("entries", {}).get(key)
        if not e or not e.get("strikes"):
            return True
        if e.get("open_until", 0.0) > time.time():
            return False
        with self._mutate() as state:
            e = state.get("entries", {}).get(key)
            if not e or not e.get("strikes"):
                return True
            now = time.time()
            if e.get("open_until", 0.0) > now:
                return False  # another process claimed the probe first
            e["open_until"] = now + self.PROBE_LEASE_S
            e["probe"] = True
            return True

    def _trip(self, e: dict, reason: str) -> float:
        now = time.time()
        if e.get("open_until", 0.0) > now and not e.get("probe"):
            return e["open_until"] - now  # already open: a concurrent failure, not a new strike
        e["strikes"] = e.get("strikes", 0) + 1
        cooldown = min(self.BASE_COOLDOWN_S * 2 ** (e["strikes"] - 1), self.MAX_COOLDOWN_S)
        e.update(open_until=now + cooldown, probe=False, hard_failures=0, last_error=reason[:500])
        return cooldown

    def record_quota(self, provider: str, model: str, err: object) -> float:
        """Open the circuit for (provider, model). Returns the cooldown in seconds."""
        with self._mutate() as state:
            e = state.setdefault("entries", {}).setdefault(self._key(provider, model), {})
            return self._trip(e, f"quota: {err}")

    def record_hard_failure(self, provider: str, model: str, err: object) -> float:
        """Count a non-quota failure. Returns the cooldown if this opened the circuit, else 0."""
        with self._mutate() as state:
            e = state.setdefault("entries", {}).setdefault(self._key(provider, model), {})
            e["hard_failures"] = e.get("hard_failures", 0) + 1
            if e.get("probe") or e["hard_failures"] >= self.HARD_FAILURE_THRESHOLD:
                return self._trip(e, f"hard failure x{e['hard_failures']}: {err}")
            e["last_error"] = f"hard failure: {err}"[:500]
            return 0.0

    def record_success(self, provider: str, model: str) -> None:
        key = self._key(provider, model)
        cached = self._state()
        if key not in cached.get("entries", {}) and not cached.get("paused_for_quota"):
            return  # nothing to reset, skip the write
        with self._mutate() as state:
            state.get("entries", {}).pop(key, None)
            state["paused_for_quota"] = False

    # ── pipeline pause flag (replaces the per-process module global) ─────

    @property
    def paused(self) -> bool:
        return bool(self._state().get("paused_for_quota", False))

    def set_paused(self, value: bool) -> None:
        if self.paused == value:
            return
        with self._mutate() as state:
            state["paused_for_quota"] = bool(value)
            if value:
                state["paused_at"] = time.time()

    # ── spawn rate limiter ────────────────────────────────────────────────

    def acquire_spawn_token(self) -> None:
        """Machine-wide token bucket: at most SPAWN_RATE_PER_S CLI spawns per second (burst = rate)."""
        rate = self.SPAWN_RATE_PER_S
        if rate <= 0:
            return
        while True:
            with self._mutate() as state:
                now = time.time()
                b = state.get("spawn_bucket") or {"tokens": rate, "ts": now}
                tokens = min(rate, b["tokens"] + max(0.0, now - b["ts"]) * rate)
                if tokens >= 1.0:
                    state["spawn_bucket"] = {"tokens": tokens - 1.0, "ts": now}
                    return
                wait = (1.0 - tokens) / rate
            time.sleep(wait)


_BREAKER = QuotaBreaker(_BREAKER_PATH)

# ---------------------------------------------------------------------------
# Supervised spawning — the ONLY place this module starts processes
# ---------------------------------------------------------------------------
# One Job Object per router process (pid in the name: named jobs are shared machine-wide,
# and a shared job would let terminate_all() kill another process's CLIs).

try:
    _SUPERVISOR: Optional[Supervisor] = Supervisor(f"CoChem_llm_router_{os.getpid()}")
except Exception as _sup_exc:
    _SUPERVISOR = None
    logger.error(
        f"[PROC-SUPERVISOR] !!! Job Object creation failed ({_sup_exc}). LLM CLI spawns are "
        f"NOT job-owned; falling back to psutil tree-kill on timeout. Orphans are possible."
    )


def _shutdown_supervisor() -> None:
    if _SUPERVISOR is not None:
        _SUPERVISOR.terminate_all()
        _SUPERVISOR.close()

atexit.register(_shutdown_supervisor)


def _kill_tree(pid: int) -> None:
    """Kill pid and every descendant still reachable through the parent chain."""
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        return
    for p in procs:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    psutil.wait_procs(procs, timeout=5)


def _supervised_run(
    cmd: list,
    *,
    timeout: float,
    input: Optional[str] = None,
    env: Optional[dict] = None,
    use_slot: bool = True,
) -> subprocess.CompletedProcess:
    """
    subprocess.run replacement: holds an llm_slot, respects the spawn rate limit, and on
    timeout kills the child's whole tree. Raises subprocess.TimeoutExpired on timeout.
    """
    kw = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
              text=True, encoding="utf-8", env=env)
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    # llm_slot(timeout=) is handed straight to WaitForSingleObject (milliseconds), so no
    # timeout is passed here — wait for a slot indefinitely.
    with (llm_slot() if use_slot else contextlib.nullcontext()):
        _BREAKER.acquire_spawn_token()
        if _SUPERVISOR is not None:
            try:
                return _SUPERVISOR.run(cmd, timeout=timeout, input=input, **kw)
            except DeadlineExceeded:
                raise subprocess.TimeoutExpired(cmd, timeout) from None

        proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW, **kw)
        try:
            stdout, stderr = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _supervised_stream(cmd: list, *, timeout: float, env: Optional[dict] = None) -> Iterator[str]:
    """
    Popen replacement for line streaming. Holds an llm_slot for the stream's lifetime,
    kills the tree on timeout, and kills it if the consumer abandons the generator
    (GeneratorExit) or anything raises. Raises RuntimeError on a non-zero exit.
    """
    import tempfile
    with llm_slot(), tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errf:
        _BREAKER.acquire_spawn_token()
        # stderr goes to a temp file: an undrained PIPE can deadlock a chatty CLI
        kw = dict(stdout=subprocess.PIPE, stderr=errf, text=True, encoding="utf-8", env=env)
        if _SUPERVISOR is not None:
            proc = _SUPERVISOR.popen(cmd, **kw)
        else:
            proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW, **kw)

        timed_out = threading.Event()
        def _on_timeout() -> None:
            timed_out.set()
            _kill_tree(proc.pid)
        watchdog = threading.Timer(timeout, _on_timeout)
        watchdog.daemon = True
        watchdog.start()
        try:
            for line in proc.stdout:
                yield line
            proc.wait()
        finally:
            watchdog.cancel()
            if proc.poll() is None:
                _kill_tree(proc.pid)
            proc.stdout.close()
            proc.wait()

        if timed_out.is_set():
            raise subprocess.TimeoutExpired(cmd, timeout)
        if proc.returncode != 0:
            errf.seek(0)
            raise RuntimeError(f"{Path(cmd[0]).name} failed (rc={proc.returncode}):\n"
                               f"STDERR: {errf.read()[:2000]}")

# ---------------------------------------------------------------------------
# Shared return type
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    content: str
    provider: str          # "gemini" | "claude"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_sec: float = 0.0

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
    """Routes tasks to Gemini via the agy.exe CLI subprocess."""

    DEFAULT_MODEL = os.environ.get("DEFAULT_GEMINI_MODEL", "gemini-3.8-flash")

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        timeout: float = _LLM_CALL_TIMEOUT_S,
        **kwargs,
    ) -> LLMResponse:
        model = model or self.DEFAULT_MODEL
        wrapped = self.wrap_xml(prompt, system=system, agent_name=agent_name)

        agent = agent_name or "cochem-coder"
        cmd = [
            str(_AGY_EXE), "--agent", agent,
            "--print-timeout", "60m",
            "--dangerously-skip-permissions",
        ]

        t0 = time.monotonic()
        result = _supervised_run(cmd, input=wrapped, timeout=timeout)
        elapsed = time.monotonic() - t0

        if result.returncode != 0:
            raise RuntimeError(
                f"agy failed (rc={result.returncode}):\n"
                f"STDERR: {result.stderr[:2000]}\nSTDOUT: {result.stdout[:2000]}"
            )

        return LLMResponse(
            content=result.stdout,
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
        timeout: float = _LLM_CALL_TIMEOUT_S,
        **kwargs,
    ) -> Iterator[str]:
        """Stream agy output line-by-line (agy doesn't expose SSE so we tail stdout)."""
        model = model or self.DEFAULT_MODEL
        wrapped = self.wrap_xml(prompt, system=system, agent_name=agent_name)
        agent = agent_name or "cochem-coder"

        cmd = [
            str(_AGY_EXE), "--agent", agent,
            "-p", wrapped,
            "--print-timeout", "60m",
            "--dangerously-skip-permissions",
        ]

        yield from _supervised_stream(cmd, timeout=timeout)

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
        # XML wrap only the user-side prompt; system is separate per Anthropic spec
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
# The claude CLI bills against your Claude Max subscription's Agent SDK allowance.
# Requires: claude.exe installed + logged in via `claude auth login`
# IMPORTANT: Do NOT set ANTHROPIC_API_KEY in env — it overrides subscription billing.

_CLAUDE_CLI_EXE = Path(os.environ.get(
    "CLAUDE_CLI_EXE",
    r"C:\Users\ansac\.local\bin\claude.exe"
))

class ClaudeSubscriptionProvider(BaseLLMProvider):
    """
    Routes via the official Claude Code CLI (claude.exe) using your Max subscription.
    Same subprocess pattern as GeminiProvider → agy.exe.
    Billing: Max subscription Agent SDK allowance (not pay-per-token API).
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
        """Load ANTHROPIC_AUTH_TOKEN from .credentials.json for headless --bare invocation.

        Key fix: before reading the token, check if it is within 10 minutes of expiry.
        If so, run `claude --version` (fast, no D: drive scan, no MCP servers) to let
        claude.exe's native OAuth flow refresh and update .credentials.json itself.
        We then read the freshly-written token. This means claude.exe manages its own
        auth lifecycle — we never hold a stale copy.
        """
        import datetime
        creds_path = Path.home() / ".claude" / ".credentials.json"
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}

        def _read_token() -> tuple[str, int]:
            """Returns (access_token, expires_at_ms)."""
            try:
                creds = json.loads(creds_path.read_text(encoding="utf-8"))
                oauth = creds.get("claudeAiOauth", {})
                return oauth.get("accessToken", ""), oauth.get("expiresAt", 0)
            except Exception:
                return "", 0

        token, expires_ms = _read_token()

        # Check if token expires within 10 minutes — if so, trigger native refresh
        if expires_ms:
            expires_dt = datetime.datetime.fromtimestamp(expires_ms / 1000)
            time_left = (expires_dt - datetime.datetime.now()).total_seconds()
            if time_left < 600:  # under 10 minutes
                logger.info(
                    f"ClaudeSubscriptionProvider: token expires in {time_left:.0f}s — "
                    "triggering native claude.exe refresh..."
                )
                try:
                    # Run `claude --version` — fast, no MCP scan, triggers OAuth refresh
                    r = _supervised_run(
                        [str(_CLAUDE_CLI_EXE), "--version"],
                        timeout=30,
                        env={k: v for k, v in os.environ.items()
                             if k != "ANTHROPIC_API_KEY"},
                        use_slot=False,  # short, no LLM work; don't take a slot we may already hold
                    )
                    if r.returncode == 0:
                        # Re-read the now-refreshed token
                        token, expires_ms = _read_token()
                        new_expires = datetime.datetime.fromtimestamp(expires_ms / 1000)
                        logger.info(f"ClaudeSubscriptionProvider: token refreshed, new expiry {new_expires}")
                    else:
                        logger.warning(f"ClaudeSubscriptionProvider: refresh attempt rc={r.returncode}: {r.stderr[:100]}")
                except Exception as e:
                    logger.warning(f"ClaudeSubscriptionProvider: refresh attempt failed: {e}")

        if token:
            env["ANTHROPIC_AUTH_TOKEN"] = token
            logger.debug("ClaudeSubscriptionProvider: ANTHROPIC_AUTH_TOKEN loaded")
        else:
            logger.warning("ClaudeSubscriptionProvider: no access token found in .credentials.json")

        return env

    def _build_cmd(self, sys_prompt_file: Path, prompt: str, model: Optional[str]) -> list:
        # PUSH THE ENTIRE PROMPT TO THE TEMP FILE TO AVOID WinError 206
        with open(sys_prompt_file, 'a', encoding='utf-8') as f:
            f.write("\n\n--- USER PROMPT ---\n\n" + prompt)
        
        cmd = [
            str(_CLAUDE_CLI_EXE),
            "--bare",
            "--print",
            "--permission-mode", "bypassPermissions",
            "--dangerously-skip-permissions",
            "--tools", "",
            "--system-prompt-file", str(sys_prompt_file),
            "-p", "Please proceed.",
        ]
        if model and model != self.DEFAULT_MODEL:
            cmd += ["--model", model]
        return cmd

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        stream: bool = False,
        agent_name: str = "",
        timeout: float = _LLM_CALL_TIMEOUT_S,
        **kwargs,
    ) -> LLMResponse:
        import tempfile
        self._check_cli()
        model = model or self.DEFAULT_MODEL

        from claude_agent_profiles import AGENT_PROFILES  # type: ignore
        sys_prompt = AGENT_PROFILES.get(agent_name, "You are an expert AI assistant.")
        if system:
            sys_prompt = f"{sys_prompt}\n\n{system}"
        wrapped_prompt = self.wrap_xml(prompt, agent_name=agent_name)

        # Write system prompt to temp file — avoids CLI length limit and inline hang
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         encoding="utf-8", delete=False) as f:
            f.write(sys_prompt)
            sys_prompt_file = Path(f.name)

        try:
            cmd = self._build_cmd(sys_prompt_file, wrapped_prompt, model)
            env = self._get_auth_env()
            t0 = time.monotonic()
            result = _supervised_run(cmd, timeout=timeout, env=env)
            elapsed = time.monotonic() - t0
        finally:
            sys_prompt_file.unlink(missing_ok=True)

        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (rc={result.returncode}):\n"
                f"STDERR: {result.stderr[:2000]}\nSTDOUT: {result.stdout[:2000]}"
            )
        return LLMResponse(
            content=result.stdout,
            provider="claude-subscription",
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
        timeout: float = _LLM_CALL_TIMEOUT_S,
        **kwargs,
    ) -> Iterator[str]:
        import tempfile
        self._check_cli()
        model = model or self.DEFAULT_MODEL

        from claude_agent_profiles import AGENT_PROFILES  # type: ignore
        sys_prompt = AGENT_PROFILES.get(agent_name, "You are an expert AI assistant.")
        if system:
            sys_prompt = f"{sys_prompt}\n\n{system}"
        wrapped_prompt = self.wrap_xml(prompt, agent_name=agent_name)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         encoding="utf-8", delete=False) as f:
            f.write(sys_prompt)
            sys_prompt_file = Path(f.name)

        try:
            cmd = self._build_cmd(sys_prompt_file, wrapped_prompt, model)
            env = self._get_auth_env()
            yield from _supervised_stream(cmd, timeout=timeout, env=env)
        finally:
            sys_prompt_file.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Ollama Provider — local models, ultimate fallback (halts pipeline on use)
# ---------------------------------------------------------------------------
# Ollama REST API at http://localhost:11434
# When this tier is reached the QuotaFallbackRouter sets a PIPELINE_PAUSED flag
# so the Kanban daemon knows to pause and retry later when cloud quota recovers.
# Preferred local models (by quality): qwen3.5:35b > qwen3.5:9b > lfm2.5:8b

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen3.5:9b")

# PIPELINE_PAUSED_FOR_QUOTA — set when the halt/Ollama tier is reached, polled by
# task_work_loop. It is a module property backed by QuotaBreaker (see end of file), so
# reads and writes are shared across processes instead of living in one child's globals.

class OllamaProvider(BaseLLMProvider):
    """
    Routes to local Ollama instance. Zero cloud cost, always available.
    WARNING: setting this as active tier signals the pipeline to pause
    (via PIPELINE_PAUSED_FOR_QUOTA flag) and wait for cloud quota recovery.
    """

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
        import urllib.request, urllib.error
        model = model or _OLLAMA_DEFAULT_MODEL
        wrapped = self.wrap_xml(prompt, system=system, agent_name=agent_name)
        body = json.dumps({"model": model, "prompt": wrapped, "stream": False}).encode()
        req = urllib.request.Request(
            f"{_OLLAMA_BASE_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Ollama request failed: {e}")
        elapsed = time.monotonic() - t0
        return LLMResponse(
            content=data.get("response", ""),
            provider="ollama",
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
        timeout: int = 300,
        **kwargs,
    ) -> Iterator[str]:
        import urllib.request
        model = model or _OLLAMA_DEFAULT_MODEL
        wrapped = self.wrap_xml(prompt, system=system, agent_name=agent_name)
        body = json.dumps({"model": model, "prompt": wrapped, "stream": True}).encode()
        req = urllib.request.Request(
            f"{_OLLAMA_BASE_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                try:
                    chunk = json.loads(raw_line)
                    if chunk.get("response"):
                        yield chunk["response"]
                except Exception:
                    continue

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
# Each entry: primary model, then ordered list of fallbacks tried on quota error.
# Fallback tuple: (provider, model)
# Strategy: when primary provider quota hits 429/ResourceExhausted, step through
# fallbacks in order until one succeeds or all are exhausted.

MODEL_REGISTRY = {
    # ══════════════════════════════════════════════════════════════════════
    # v2 MODEL REGISTRY — Fable TDD Architecture Design (2026-09-22)
    # ──────────────────────────────────────────────────────────────────────
    # FALLBACK RULES:
    #   Fable-primary:  Fable → Gemini Pro → Opus → Flash → HALT
    #   Opus-primary:   Opus → Gemini Pro → Sonnet → Flash → HALT
    #   Sonnet-primary: Sonnet → Gemini Pro → Flash → HALT
    #   GPro-primary:   GPro → Sonnet → Flash → HALT
    #   Flash-primary:  Flash → Sonnet → HALT (floor exception: no cheaper Claude)
    #
    # HALT sentinel = graceful pipeline pause for hardware daemon restart.
    # Fable has its OWN rate limit. Opus/Sonnet share one. Gemini is provider-wide.
    # ══════════════════════════════════════════════════════════════════════

    # ── TDD Stage A: FRAME (one-shot per task) ────────────────────────────
    "cochem-planner":       {"provider": "gemini", "model": "gemini-3.1-pro-preview",
                             "fallbacks": [("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-researcher":    {"provider": "gemini", "model": "gemini-3.8-flash",
                             "fallbacks": [("claude-subscription", "claude-haiku-4-5-20251001"), ("claude-subscription", "claude-sonnet-5"), ("halt", "graceful")]},
    "cochem-test-author":   {"provider": "claude-subscription", "model": "claude-sonnet-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},

    # ── TDD Stage B: BUILD ────────────────────────────────────────────────
    "cochem-coder":         {"provider": "claude-subscription", "model": "claude-opus-5-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-coder-deep":    {"provider": "claude-subscription", "model": "claude-opus-5-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-tester":        {"provider": "gemini", "model": "gemini-3.8-flash",
                             "fallbacks": [("claude-subscription", "claude-haiku-4-5-20251001"), ("claude-subscription", "claude-sonnet-5"), ("halt", "graceful")]},
    "cochem-audit":         {"provider": "gemini", "model": "gemini-3.1-pro-preview",
                             "fallbacks": [("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},

    # ── TDD Stage C: CONVERGE (≤3 iterations) ─────────────────────────────
    "cochem-improve-code":  {"provider": "claude-subscription", "model": "claude-sonnet-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-summarizer":    {"provider": "gemini", "model": "gemini-3.8-flash",
                             "fallbacks": [("claude-subscription", "claude-haiku-4-5-20251001"), ("claude-subscription", "claude-sonnet-5"), ("halt", "graceful")]},
    "cochem-coder-refine":  {"provider": "claude-subscription", "model": "claude-sonnet-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-audit-escalation": {"provider": "claude-subscription", "model": "claude-opus-5-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-debug":         {"provider": "claude-subscription", "model": "claude-sonnet-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},

    # ── Pivot Council ─────────────────────────────────────────────────────
    "pivot-summarizer":     {"provider": "gemini", "model": "gemini-3.8-flash",
                             "fallbacks": [("claude-subscription", "claude-haiku-4-5-20251001"), ("claude-subscription", "claude-sonnet-5"), ("halt", "graceful")]},
    "pivot-researcher":     {"provider": "gemini", "model": "gemini-3.8-flash",
                             "fallbacks": [("claude-subscription", "claude-haiku-4-5-20251001"), ("claude-subscription", "claude-sonnet-5"), ("halt", "graceful")]},
    "pivot-strategist":     {"provider": "claude-subscription", "model": "claude-fable-5-1",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-opus-5-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "pivot-reviewer":       {"provider": "gemini", "model": "gemini-3.1-pro-preview",
                             "fallbacks": [("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "pivot-executor":       {"provider": "claude-subscription", "model": "claude-sonnet-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    # Legacy aliases
    "pivot-architect":      {"provider": "claude-subscription", "model": "claude-fable-5-1",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-opus-5-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "pivot-planner":        {"provider": "gemini", "model": "gemini-3.1-pro-preview",
                             "fallbacks": [("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},

    # ── Governance ────────────────────────────────────────────────────────
    "council-adjudicator":  {"provider": "claude-subscription", "model": "claude-fable-5-1",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-opus-5-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "0rchestrator":         {"provider": "gemini", "model": "gemini-3.1-pro-preview",
                             "fallbacks": [("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},

    # ── Review / Architecture ─────────────────────────────────────────────
    "cochem-improve":       {"provider": "claude-subscription", "model": "claude-opus-5-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-peer-reviewer": {"provider": "claude-subscription", "model": "claude-opus-5-5",
                             "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("claude-subscription", "claude-sonnet-5"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},

    # ── Writing / Research ────────────────────────────────────────────────
    "cochem-scribe":          {"provider": "claude-subscription", "model": "claude-sonnet-5",
                               "fallbacks": [("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-author":          {"provider": "claude-subscription", "model": "claude-sonnet-5",
                               "fallbacks": [("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-academic-editor": {"provider": "claude-subscription", "model": "claude-sonnet-5",
                               "fallbacks": [("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-helper":          {"provider": "claude-subscription", "model": "claude-sonnet-5",
                               "fallbacks": [("gemini", "gemini-3.8-flash"), ("halt", "graceful")]},
    "cochem-literature-miner": {"provider": "gemini", "model": "gemini-3.8-flash",
                                "fallbacks": [("claude-subscription", "claude-haiku-4-5-20251001"), ("claude-subscription", "claude-sonnet-5"), ("halt", "graceful")]},
}


# Error signatures that indicate quota/rate exhaustion (not hard failures)
_QUOTA_ERRORS = (
    "429", "quota", "rate_limit", "resource_exhausted",
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
    Logs each fallback so the user can see what degraded and why.

    Quota state is the machine-wide QuotaBreaker, not per-instance: a provider opened by
    one process is skipped by every other process, with zero spawns, until its
    cooldown expires.

    Usage:
        router = QuotaFallbackRouter()
        response = router.generate("cochem-coder", prompt, deadline=Deadline(1800))
    """

    def __init__(self) -> None:
        # Legacy attributes: task_work_loop.py still calls .clear() on these. Clearing them
        # is a deliberate no-op. A local reset must not reopen the shared breaker for
        # every process at once, because that recreates the herd.
        self._disabled: set = set()
        self._disabled_at: dict = {}

    def _entry(self, agent_name: str) -> dict:
        return MODEL_REGISTRY.get(
            agent_name,
            {"provider": "gemini", "model": "gemini-3.1-pro-preview",
             "fallbacks": [("claude-subscription", "claude-sonnet-5")]},
        )

    @staticmethod
    def _record_failure(provider_name: str, model: str, exc: Exception) -> None:
        if _is_quota_error(exc):
            cooldown = _BREAKER.record_quota(provider_name, model, exc)
            logger.warning(
                f"  [QUOTA-ROUTER] {provider_name}/{model} quota exhausted: {exc}. "
                f"Breaker open {cooldown:.0f}s (shared); trying next fallback."
            )
        else:
            cooldown = _BREAKER.record_hard_failure(provider_name, model, exc)
            logger.warning(
                f"  [QUOTA-ROUTER] {provider_name}/{model} failed: {exc!r:.300}"
                + (f" — breaker open {cooldown:.0f}s after repeated failures." if cooldown else "")
            )

    @staticmethod
    def _exhausted(agent_name: str, chain: list, last_exc: Optional[Exception],
                   attempted: int, halted: bool) -> Exception:
        """Build the terminal exception. The halt sentinel or an all-open chain => QuotaExhausted."""
        if halted or attempted == 0:
            _BREAKER.set_paused(True)
            why = "every cloud entry is breaker-open (nothing spawned)" if attempted == 0 \
                else f"last error: {last_exc}"
            logger.warning(
                f"  [QUOTA-ROUTER] ⚠️  ALL cloud providers exhausted for '{agent_name}' ({why}). "
                f"PIPELINE_PAUSED_FOR_QUOTA=True — halting gracefully."
            )
            return QuotaExhausted(
                f"All cloud providers exhausted for '{agent_name}' ({why}). "
                f"Pipeline paused — hardware daemon will restart when quota clears."
            )
        return RuntimeError(
            f"All models exhausted for agent '{agent_name}'. "
            f"Fallback chain: {chain}. Last error: {last_exc}"
        )

    def _usable(self, provider_name: str, model: str) -> bool:
        if _BREAKER.allow(provider_name, model):
            return True
        logger.warning(
            f"  [QUOTA-ROUTER] Skipping {provider_name}/{model} "
            f"(shared breaker open, {_BREAKER.open_remaining(provider_name, model):.0f}s left)"
        )
        return False

    def generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        system: str = "",
        stream: bool = False,
        deadline: Optional[Deadline] = None,
        **kwargs,
    ) -> "LLMResponse":
        """
        Walk the fallback chain. Each attempt gets min(timeout, deadline.remaining()).
        Raises DeadlineExceeded when the deadline runs out, and QuotaExhausted when the halt
        sentinel is reached or every cloud entry is breaker-open.
        """
        entry = self._entry(agent_name)
        chain = [(entry["provider"], entry["model"])] + entry.get("fallbacks", [])
        per_call_timeout = float(kwargs.pop("timeout", _LLM_CALL_TIMEOUT_S))

        last_exc: Optional[Exception] = None
        attempted = 0
        for provider_name, model in chain:
            # Raises DeadlineExceeded (stopping the chain) once the budget is spent. Checked
            # before the halt sentinel so a timeout never masquerades as quota exhaustion.
            remaining = deadline.remaining() if deadline is not None else None

            # ── HALT sentinel: all cloud providers exhausted ──────────
            if provider_name in ("halt", "ollama"):
                raise self._exhausted(agent_name, chain, last_exc, attempted, halted=True)

            if not self._usable(provider_name, model):
                continue

            call_timeout = per_call_timeout if remaining is None else min(per_call_timeout, remaining)

            attempted += 1
            try:
                provider = get_provider(provider_name)
                logger.info(f"  [QUOTA-ROUTER] {agent_name} → {provider_name}/{model}")
                response = provider.generate(prompt, system=system, model=model,
                                             agent_name=agent_name, timeout=call_timeout, **kwargs)
            except Exception as exc:
                self._record_failure(provider_name, model, exc)
                last_exc = exc
                continue  # HARD FAILURES ALSO WALK CHAIN (as designed in v2); breaker bounds the loop
            _BREAKER.record_success(provider_name, model)
            return response

        raise self._exhausted(agent_name, chain, last_exc, attempted, halted=False)

    def stream_generate(
        self,
        agent_name: str,
        prompt: str,
        *,
        system: str = "",
        **kwargs,
    ) -> "Iterator[str]":
        entry = self._entry(agent_name)
        chain = [(entry["provider"], entry["model"])] + entry.get("fallbacks", [])
        kwargs.setdefault("timeout", _LLM_CALL_TIMEOUT_S)

        last_exc: Optional[Exception] = None
        attempted = 0
        for provider_name, model in chain:
            if provider_name in ("halt", "ollama"):
                raise self._exhausted(agent_name, chain, last_exc, attempted, halted=True)
            if not self._usable(provider_name, model):
                continue
            attempted += 1
            yielded = False
            try:
                provider = get_provider(provider_name)
                logger.info(f"  [QUOTA-ROUTER] stream {agent_name} → {provider_name}/{model}")
                for chunk in provider.stream_generate(prompt, system=system, model=model,
                                                      agent_name=agent_name, **kwargs):
                    yielded = True
                    yield chunk
            except Exception as exc:
                self._record_failure(provider_name, model, exc)
                # Falling back mid-stream would duplicate output; only quota errors
                # before the first chunk walk the chain.
                if yielded or not _is_quota_error(exc):
                    raise
                last_exc = exc
                continue
            _BREAKER.record_success(provider_name, model)
            return

        raise self._exhausted(agent_name, chain, last_exc, attempted, halted=False)


# Module-level singleton router
_router = QuotaFallbackRouter()


def get_provider_for_agent(agent_name: str) -> "BaseLLMProvider":
    """Legacy compat shim — returns primary provider, ignores fallbacks. Use router for new code."""
    entry = MODEL_REGISTRY.get(agent_name, {"provider": "gemini", "model": "gemini-3.1-pro-preview"})
    return get_provider(entry["provider"])


def router_generate(agent_name: str, prompt: str, **kwargs) -> "LLMResponse":
    """Convenience: generate via the module-level QuotaFallbackRouter (accepts deadline=)."""
    return _router.generate(agent_name, prompt, **kwargs)


class _RouterModule(types.ModuleType):
    """Routes `llm_router.PIPELINE_PAUSED_FOR_QUOTA` reads/writes to the shared QuotaBreaker."""

    @property
    def PIPELINE_PAUSED_FOR_QUOTA(self) -> bool:
        return _BREAKER.paused

    @PIPELINE_PAUSED_FOR_QUOTA.setter
    def PIPELINE_PAUSED_FOR_QUOTA(self, value: bool) -> None:
        _BREAKER.set_paused(bool(value))


sys.modules[__name__].__class__ = _RouterModule



