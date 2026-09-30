import resource_governor as _gov
"""
Task Work Loop — Perpetual Kanban Daemon
========================================
v2 multi-phase TDD cycle (design: v2/TDD_CYCLE_ARCHITECTURE_V2.md)

    process_task = run_setup(ctx) -> run_rounds(ctx) -> finalize(ctx)

    Stage A  FRAME     P1 PLAN (cochem-planner) · P2 RESEARCH (cochem-researcher)
                       P3 TEST-FIRST (cochem-test-author) + deterministic RED gate
    Stage B  BUILD     P4 CODE (task agent, default cochem-coder) · P5 pytest · P6 AUDIT (cochem-audit)
    Stage C  CONVERGE  <= V2_MAX_AUDIT_CYCLES iterations of
                       P7 IMPROVE (cochem-improve-code) · P8 PLAN-NEXT (pure-Python gate + next_work)
                       [cochem-debug interrupt] · P9 REFINE (cochem-coder-refine) · P10 pytest + AUDIT

Key properties
- Test execution is a pytest subprocess (--junitxml); no LLM ever reports test results.
- LLM file output uses the artifact protocol (<<<FILE: path>>> ... <<<END FILE>>>) and is
  written atomically by the orchestrator; modified_files / diff_lines are computed from disk.
- Structured phases (plan, audit, adjudication) are pydantic-validated; malformed output
  fails closed (audit -> INDETERMINATE, which can never ACCEPT).
- The progress gate is pure Python (zero tokens): ACCEPT / CONTINUE / PIVOT / ESCALATE /
  QUARANTINE_CANDIDATE from pass_rate, audit_score, weighted findings and diff_lines.
- Per-task run artefacts live in .scripts/tdd_runs/<task_id>/ (context.json, plan.json,
  dossier.md, junit xml, raw responses). Stage A is resumed after a quota pause.
- All subprocess calls: creationflags=CREATE_NO_WINDOW, encoding='utf-8'.
- SQLite WAL boundary rule unchanged (no DB locks are held across LLM calls here).
"""
import os
import re
import glob
import json
import asyncio
import difflib
import hashlib
import logging
import logging.handlers
import tempfile
import contextlib
import dataclasses
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional
import pydantic
import sys
import time
import subprocess
import multiprocessing
from pathlib import Path

try:
    import psutil as _psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None
    _PSUTIL_AVAILABLE = False

# Only build handlers when basicConfig will actually install them. If the root logger is
# already configured (e.g. when imported by pytest or another tool), basicConfig is a no-op
# and the orphaned TextIOWrapper would be garbage-collected — closing the host's stdout.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(
                open(os.devnull, "w", encoding="utf-8") if not sys.stdout
                else __import__("io").TextIOWrapper(
                    sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout,
                    encoding="utf-8", errors="replace", line_buffering=True
                )
            ),
            logging.handlers.RotatingFileHandler(
                r"d:\__CoChem\__agentic\.scripts\task_work_loop.log",
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
                encoding="utf-8",
            ),
        ],
    )
logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
USER_HOME = os.path.expanduser("~")

# Ensure parent agentic dir is on path so llm_router imports regardless of cwd
_AGENTIC_DIR = str(_HERE.parent)
if _AGENTIC_DIR not in sys.path:
    sys.path.insert(0, _AGENTIC_DIR)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

PROMPTS_DIR = str(_HERE / "prompts")
STATE_FILE   = str(_HERE / "work_loop_state.json")


def load_state() -> dict[str, Any]:
    """Load persistent loop state from STATE_FILE."""
    p = Path(STATE_FILE)
    if not p.is_file():
        return {}
    try:
        content = p.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Could not load state from {p}: {e}")
        return {}


def save_state(state: dict[str, Any]) -> None:
    """Save persistent loop state to STATE_FILE atomically."""
    p = Path(STATE_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_p = p.with_name(f"{p.name}.tmp.{os.getpid()}")
    try:
        tmp_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp_p.replace(p)
    except Exception as e:
        logger.warning(f"Atomic replace failed ({e}), writing directly to {p}")
        with contextlib.suppress(Exception):
            if tmp_p.exists():
                tmp_p.unlink()
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")

GUARD_SCRIPT = r"C:\Users\ansac\.gemini\config\scripts\concurrency_guard.py"
KANBAN_SCRIPT = str(_HERE.parent / "cochem_kanban.py")
CLAUDE_CLI    = str(_HERE.parent / "claude_cli.py")
MAX_PIVOT_CYCLES = int(os.environ.get("V2_MAX_PIVOT_CYCLES", os.environ.get("MAX_PIVOT_CYCLES", "3")))

# Track per-task pivot attempts
_pivot_counts: dict = {}

# ── .env bootstrap ────────────────────────────────────────────────────────────
_env_path = _HERE.parent / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

DEFAULT_PROVIDER = os.environ.get("DEFAULT_LLM_PROVIDER", "gemini")

# ── v2 TDD configuration (design §7.6) ───────────────────────────────────────
V2_MAX_AUDIT_CYCLES        = int(os.environ.get("V2_MAX_AUDIT_CYCLES", "3"))
V2_STALL_LIMIT             = int(os.environ.get("V2_STALL_LIMIT", "2"))
V2_AUDIT_PASS_THRESHOLD    = int(os.environ.get("V2_AUDIT_PASS_THRESHOLD", "85"))
V2_ESCALATION_BAND_LOW     = int(os.environ.get("V2_ESCALATION_BAND_LOW", "70"))
V2_PYTEST_TIMEOUT_S        = int(os.environ.get("V2_PYTEST_TIMEOUT_S", "900"))
V2_RESEARCH_CONTEXT_TOKENS = int(os.environ.get("V2_RESEARCH_CONTEXT_TOKENS", "30000"))
TDD_RUNS_DIR       = Path(os.environ.get("COCHEM_TDD_RUNS_DIR", str(_HERE / "tdd_runs")))
TDD_WORKSPACE_ROOT = Path(os.environ.get("COCHEM_TDD_WORKSPACE_ROOT", _AGENTIC_DIR))

# ── LLM Router bootstrap ─────────────────────────────────────────────────────
# Import the module-level QuotaFallbackRouter — this gives per-agent dynamic model
# routing from MODEL_REGISTRY (claude-subscription → gemini → ollama fallback chain)
# preserving the original dynamic design intent.
try:
    import llm_router as _llm_router
    _router = _llm_router.QuotaFallbackRouter()
    _LLM_ROUTER_AVAILABLE = True
    logger.info("LLM QuotaFallbackRouter loaded. Per-agent dynamic routing active.")
except ImportError:
    _router = None
    _LLM_ROUTER_AVAILABLE = False
    logger.warning("llm_router not available — falling back to agy-only dispatch.")

try:
    from v2.MODEL_REGISTRY_V2 import weighted_cost
except ImportError:
    try:
        from MODEL_REGISTRY_V2 import weighted_cost
    except ImportError:
        def weighted_cost(model: str, input_tokens: int, output_tokens: int) -> float:
            return 0.0

# ── Errors ────────────────────────────────────────────────────────────────────

class StructuredOutputError(ValueError):
    """LLM output could not be parsed/validated into the requested pydantic model."""


class QuotaExhaustedError(RuntimeError):
    """Cloud quota did not recover within safe_chat_cli's pause/retry window."""


class PhaseFailure(RuntimeError):
    """A pipeline phase could not produce its required artefact (routes to PIVOT)."""


# ── Pydantic contracts for LLM-produced structured data ───────────────────────

def _to_str(v: Any) -> Any:
    return "" if v is None else (str(v) if isinstance(v, (int, float)) else v)


StrId = Annotated[str, pydantic.BeforeValidator(_to_str)]

SEVERITY_WEIGHTS: dict[str, int] = {"CRITICAL": 8, "HIGH": 4, "MEDIUM": 2, "LOW": 1}
_SEVERITY_SYNONYMS = {
    "BLOCKER": "CRITICAL", "FATAL": "CRITICAL", "SEVERE": "CRITICAL",
    "MAJOR": "HIGH", "ERROR": "HIGH",
    "MODERATE": "MEDIUM", "WARNING": "MEDIUM", "WARN": "MEDIUM",
    "MINOR": "LOW", "INFO": "LOW", "TRIVIAL": "LOW", "NIT": "LOW",
}
AUDIT_VERDICTS = ("PASS", "FAIL", "SPOOFING_DETECTED", "INDETERMINATE")


class _Lenient(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")


class AcceptanceCriterion(_Lenient):
    id: StrId
    statement: str
    verifiable_by: str = ""


class PlannedTestCase(_Lenient):
    id: StrId
    name: str
    asserts: str = ""
    criteria_ids: list[StrId] = pydantic.Field(default_factory=list)


class Plan(_Lenient):
    """P1 output (plan.json)."""
    goal: str
    acceptance_criteria: list[AcceptanceCriterion] = pydantic.Field(min_length=1)
    test_cases: list[PlannedTestCase] = pydantic.Field(min_length=1)
    file_targets: list[str] = pydantic.Field(default_factory=list)
    out_of_scope: list[str] = pydantic.Field(default_factory=list)
    risks: list[str] = pydantic.Field(default_factory=list)
    mutating: bool = True


class Finding(_Lenient):
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = "HIGH"
    file: StrId = ""
    line: Optional[int] = None
    issue: str
    criteria_id: StrId = ""

    @pydantic.field_validator("severity", mode="before")
    @classmethod
    def _norm_severity(cls, v: Any) -> str:
        s = str(v or "").strip().upper()
        s = _SEVERITY_SYNONYMS.get(s, s)
        # Fail closed: an unrecognised severity blocks ACCEPT (HIGH) rather than hiding.
        return s if s in SEVERITY_WEIGHTS else "HIGH"

    @pydantic.field_validator("line", mode="before")
    @classmethod
    def _norm_line(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        m = re.search(r"\d+", str(v))
        return int(m.group()) if m else None


class AuditResult(_Lenient):
    """P6/P10 audit contract. Accepts legacy 'status' as an alias of 'verdict'."""
    verdict: Literal["PASS", "FAIL", "SPOOFING_DETECTED", "INDETERMINATE"] = pydantic.Field(
        validation_alias=pydantic.AliasChoices("verdict", "status"))
    score: int = pydantic.Field(ge=0, le=100)
    critique: str = ""
    findings: list[Finding] = pydantic.Field(default_factory=list)

    @pydantic.field_validator("verdict", mode="before")
    @classmethod
    def _norm_verdict(cls, v: Any) -> str:
        s = re.sub(r"[\s\-]+", "_", str(v or "").strip().upper())
        return {"SPOOFING": "SPOOFING_DETECTED", "SPOOFED": "SPOOFING_DETECTED",
                "PASSED": "PASS", "FAILED": "FAIL"}.get(s, s)

    @pydantic.field_validator("score", mode="before")
    @classmethod
    def _norm_score(cls, v: Any) -> Any:
        if isinstance(v, str):
            m = re.search(r"-?\d+(?:\.\d+)?", v)
            v = float(m.group()) if m else v
        if isinstance(v, float):
            return int(round(v))
        return v

    @classmethod
    def indeterminate(cls, reason: str) -> "AuditResult":
        return cls(verdict="INDETERMINATE", score=0, critique=reason,
                   findings=[Finding(severity="HIGH", issue=reason)])


class AdjudicationResult(_Lenient):
    """council-adjudicator contract for QUARANTINE-candidate verdicts."""
    confirmed: bool
    rationale: str = ""


# ── Structured output parsing (P0.1) ─────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n(.*?)```", re.S)


def extract_json_objects(text: str) -> list[dict]:
    """Return every top-level JSON object found in text: fenced blocks first, then bare.

    Wrappers such as {"structured_output": {...}} (agy --output-format json) are
    unwrapped and the inner object is offered as an additional candidate.
    """
    if not text:
        return []
    found: list[dict] = []
    for m in _JSON_FENCE_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(obj, dict):
            found.append(obj)
    decoder = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            found.append(obj)
        i = text.find("{", end)
    expanded: list[dict] = []
    for obj in found:
        expanded.append(obj)
        for key in ("structured_output", "result", "output", "data"):
            inner = obj.get(key)
            if isinstance(inner, str):
                with contextlib.suppress(ValueError):
                    inner = json.loads(inner)
            if isinstance(inner, dict):
                expanded.append(inner)
    return expanded


def parse_structured(text: str, model_cls: type[pydantic.BaseModel]) -> pydantic.BaseModel:
    """Validate the first JSON object in text that satisfies model_cls. Fails closed."""
    candidates = extract_json_objects(text)
    if not candidates:
        raise StructuredOutputError(
            f"No JSON object found in response ({len(text or '')} chars) for {model_cls.__name__}.")
    errors: list[str] = []
    for obj in candidates:
        try:
            return model_cls.model_validate(obj)
        except pydantic.ValidationError as exc:
            errors.append(f"{exc.error_count()} error(s): {str(exc)[:300]}")
    raise StructuredOutputError(
        f"{len(candidates)} JSON object(s) found but none validated as {model_cls.__name__}: "
        + " | ".join(errors[:3]))


def _json_nudge(model_cls: Optional[type[pydantic.BaseModel]], err: Exception) -> str:
    schema = json.dumps(model_cls.model_json_schema(), indent=None)[:4000] if model_cls else "{}"
    return (
        "\n\n--- FORMAT CORRECTION ---\n"
        f"Your previous answer could not be used: {str(err)[:500]}\n"
        "Return ONLY one JSON object (no prose, no markdown fences) that validates against "
        f"this JSON schema:\n{schema}\n"
    )


@dataclass
class LLMCallMeta:
    """Which concrete (provider, model) served a call — needed for asymmetry checks."""
    agent: str
    provider: str
    model: str
    elapsed_sec: float = 0.0
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    cost_units: float = 0.0

    def __post_init__(self) -> None:
        if self.cost_units == 0.0:
            self.cost_units = weighted_cost(self.model, self.input_tokens, self.output_tokens)

    @property
    def triple(self) -> tuple[str, str]:
        return (self.provider, self.model)

# ── Hardware-adaptive concurrency ──────────────────────────────────────────────
# Matches the old Gemini pipeline behaviour: scale to 60 concurrent agents when
# hardware headroom allows; back off gracefully under load.
#
# Env overrides:
#   COCHEM_MAX_CONCURRENT_PROCESSES  — hard ceiling (default 60)
#   COCHEM_MIN_CONCURRENT_PROCESSES  — floor when system is heavily loaded (default 1)
#   COCHEM_HW_POLL_INTERVAL          — seconds between hardware polls (default 30)

_HW_CEIL = int(os.environ.get("COCHEM_MAX_CONCURRENT_PROCESSES", "16"))
_HW_FLOOR = int(os.environ.get("COCHEM_MIN_CONCURRENT_PROCESSES", "1"))
_HW_POLL_INTERVAL = int(os.environ.get("COCHEM_HW_POLL_INTERVAL", "30"))
_RAMP_STEP = max(1, int(os.environ.get("COCHEM_RAMP_STEP", "5")))

# Cross-process guard path (concurrency_guard.py semaphore dir)
_GUARD_SEM_DIR = Path(GUARD_SCRIPT).parent / ".swarm_semaphore" if Path(GUARD_SCRIPT).exists() else None

try:
    import concurrency_guard as _cg
    check_disk_io_headroom = _cg.check_disk_io_headroom
    get_disk_io_metrics = _cg.get_disk_io_metrics
except ImportError:
    _cg = None
    check_disk_io_headroom = None
    get_disk_io_metrics = None

_ORIGINAL_CG_CHECK_FN = check_disk_io_headroom


def _compute_max_workers() -> int:
    """Poll live CPU, RAM, GPU and Disk I/O utilisation and return safe concurrent worker count.

    Scale table (each claude.exe ≈ 190 MB RAM + negligible CPU while waiting):
      CPU < 30%  and RAM free > 16 GB  → full ceiling (up to COCHEM_MAX_CONCURRENT_PROCESSES)
      CPU < 50%  and RAM free > 8 GB   → 75% of ceiling
      CPU < 70%  and RAM free > 4 GB   → 50% of ceiling
      CPU < 85%  and RAM free > 2 GB   → 25% of ceiling  (min 4)
      otherwise                        → COCHEM_MIN_CONCURRENT_PROCESSES (floor, default 1)
    GPU VRAM check: if GPU mem > 80% used, cap at 25% ceiling regardless.
    Disk I/O checks:
      - Saturated disk throughput or IOPS: throttle capacity to <= 25% ceiling (at least floor).
      - Critically low free disk space (< 5.0 GB): collapse concurrency to floor.
      - Guarded scale-up above 10 slots: strictly requires verified zero disk I/O saturation.
    """
    cpu = 50.0       # safe default if psutil unavailable
    ram_free_gb = 8.0
    gpu_pct = 0.0

    if _PSUTIL_AVAILABLE:
        # 2.0s window captures the full startup burst of freshly spawned claude.exe/Node procs
        cpu = _psutil.cpu_percent(interval=2.0)
        vm = _psutil.virtual_memory()

        # Count RAM already consumed by running claude.exe processes so headroom is not
        # double-counted between polls (psutil.available lags behind spawn bursts).
        try:
            claude_procs = [p for p in _psutil.process_iter(['name', 'memory_info'])
                            if p.info['name'] and 'claude' in p.info['name'].lower()]
            claude_ram_gb = sum(p.info['memory_info'].rss for p in claude_procs) / (1024 ** 3)
        except Exception:
            claude_ram_gb = 0.0
        # Subtract already-consumed Claude RAM from available headroom
        ram_free_gb = max(0, vm.available / (1024 ** 3) - claude_ram_gb)

        # Optional GPU check via nvidia-smi subprocess (non-fatal)
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    parts = line.split(",")
                    if len(parts) == 2:
                        used, total = float(parts[0].strip()), float(parts[1].strip())
                        gpu_pct = max(gpu_pct, (used / total * 100) if total else 0)
        except Exception:
            pass

    ceil = _HW_CEIL
    floor = _HW_FLOOR

    # GPU cap: heavy VRAM usage (model training etc.) → reduce workers
    if gpu_pct > 80:
        ceil = max(floor, ceil // 4)

    if cpu < 30 and ram_free_gb > 16:
        workers = ceil
    elif cpu < 50 and ram_free_gb > 8:
        workers = max(floor, ceil * 3 // 4)
    elif cpu < 70 and ram_free_gb > 4:
        workers = max(floor, ceil // 2)
    elif cpu < 85 and ram_free_gb > 2:
        workers = max(floor, max(4, ceil // 4))
    else:
        workers = floor

    # Poll disk I/O metrics and storage headroom
    is_io_healthy = True
    io_metrics: dict = {}
    check_fn = None
    twl_fn = globals().get("check_disk_io_headroom")
    cg_fn = getattr(_cg, "check_disk_io_headroom", None) if _cg is not None else None

    if twl_fn is not None and twl_fn is not _ORIGINAL_CG_CHECK_FN:
        check_fn = twl_fn
    elif cg_fn is not None:
        check_fn = cg_fn
    elif twl_fn is not None:
        check_fn = twl_fn

    if check_fn is not None:
        try:
            is_io_healthy, io_metrics = check_fn()
        except Exception as e:
            logger.warning(f"[HW] Disk I/O headroom check failed: {e}")
            is_io_healthy = True
            io_metrics = {}

    # Guarded scale-up above 10: strictly requires verified zero disk I/O saturation [AC3]
    # (total_mb_s < 20.0, total_iops < 500, free_gb > 15.0)
    if workers > 10:
        zero_saturation = (
            bool(io_metrics)
            and io_metrics.get("total_mb_s", 999.0) < 20.0
            and io_metrics.get("total_iops", 999.0) < 500.0
            and io_metrics.get("free_gb", 0.0) > 15.0
        )
        if not zero_saturation:
            workers = 10

    # Aggressive disk saturation throttling [AC2]:
    free_gb = io_metrics.get("free_gb", 100.0) if io_metrics else 100.0
    if free_gb < 5.0:
        workers = floor
    elif not is_io_healthy:
        workers = max(floor, min(workers, ceil // 4))

    logger.debug(
        f"[HW] cpu={cpu:.1f}% ram_free={ram_free_gb:.1f}GB gpu={gpu_pct:.1f}% "
        f"io_healthy={is_io_healthy} free_gb={free_gb:.1f}GB → workers={workers}"
    )
    return workers


# Global asyncio.Semaphore — dynamically replaced as hardware changes
_worker_semaphore: asyncio.Semaphore | None = None
_current_worker_limit: int = 0
# Number of workers currently holding a semaphore slot (ramp-up bookkeeping)
_active_worker_count: int = 0


def _refresh_worker_semaphore(loop: asyncio.AbstractEventLoop | None = None) -> int:
    """Recompute hardware headroom and update the global asyncio.Semaphore.
    Returns the new worker limit. Thread-safe: only call from within the event loop."""
    global _worker_semaphore, _current_worker_limit
    new_limit = _compute_max_workers()
    if new_limit != _current_worker_limit or _worker_semaphore is None:
        _worker_semaphore = asyncio.Semaphore(new_limit)
        _current_worker_limit = new_limit
        logger.info(f"[HW] Worker pool resized → {new_limit} concurrent slots "
                    f"(ceiling={_HW_CEIL}, floor={_HW_FLOOR})")
    return new_limit

# ── Provider-aware dispatcher ─────────────────────────────────────────────────

async def safe_chat_cli(agent_name, prompt, timeout=3600, extract_type="text", pydantic_model=None,
                        *, return_meta=False, exclude=None):
    """
    Routes task to the correct LLM provider for this specific agent using
    QuotaFallbackRouter (per-agent dynamic model selection from MODEL_REGISTRY).

    Fallback chain per agent ends in the ('halt', 'qwen3.5:9b') sentinel: reaching it makes
    the router set llm_router.PIPELINE_PAUSED_FOR_QUOTA and raise; this function then
    sleeps and retries (quota recovery loop below).
    If llm_router unavailable, falls back to agy subprocess (Gemini only).

    Structured output (P0.1): when ``pydantic_model`` is given (or extract_type ==
    "structured"), the first JSON object in the response is validated. On failure the
    same agent is re-prompted ONCE with a JSON-only nudge; a second failure raises
    StructuredOutputError (fail closed — callers never get a silent default).

    Returns the text / validated model, or ``(result, LLMCallMeta)`` when return_meta=True.
    Raises QuotaExhaustedError if quota does not recover within the retry window.

    SQLite WAL rule: caller MUST release all DB write locks before calling this.
    """
    structured = pydantic_model is not None or extract_type == "structured"

    def _parse(text):
        if pydantic_model is not None:
            return parse_structured(text, pydantic_model)
        objs = extract_json_objects(text)
        if not objs:
            raise StructuredOutputError(f"No JSON object found in response for {agent_name}.")
        return objs[0]

    if _LLM_ROUTER_AVAILABLE and _router is not None:
        # Per-agent dynamic routing: MODEL_REGISTRY selects best model, falls back on quota errors
        # Retry loop: if all cloud providers exhausted, sleep and retry instead of crashing
        _max_pause_retries = 6  # 6 × 5 min = 30 min max wait
        for _pause_attempt in range(_max_pause_retries):
            # Check pause flag before entering router
            if _llm_router.PIPELINE_PAUSED_FOR_QUOTA:
                logger.warning(
                    "[PIPELINE PAUSED] All cloud quota exhausted — pipeline halted. "
                    f"Will retry in 5 minutes (attempt {_pause_attempt + 1}/{_max_pause_retries})."
                )
                await asyncio.sleep(300)  # 5 min pause
                _llm_router.PIPELINE_PAUSED_FOR_QUOTA = False
                _router._disabled.clear()
                _router._disabled_at.clear()
                logger.info("[PIPELINE RESUME] Quota pause cleared — retrying cloud providers.")
                continue

            def run_router():
                response = _router.generate(agent_name, prompt, timeout=timeout, exclude=exclude)
                in_tok = int(getattr(response, "input_tokens", 0) or (len(prompt) // 4))
                out_tok = int(getattr(response, "output_tokens", 0) or (len(response.content or "") // 4))
                cost = float(weighted_cost(response.model, in_tok, out_tok))
                meta = LLMCallMeta(agent_name, response.provider, response.model,
                                   float(getattr(response, "elapsed_sec", 0.0) or 0.0),
                                   input_tokens=in_tok, output_tokens=out_tok, cost_units=cost)
                content = response.content or ""
                if not structured:
                    return content, meta
                try:
                    return _parse(content), meta
                except StructuredOutputError as first_err:
                    logger.warning(f"[STRUCTURED] {agent_name} via {meta.provider}/{meta.model}: "
                                   f"{first_err}. Re-prompting once with JSON-only nudge.")
                    retry = _router.generate(agent_name, prompt + _json_nudge(pydantic_model, first_err),
                                             timeout=timeout, exclude=exclude)
                    retry_in = int(getattr(retry, "input_tokens", 0) or (len(prompt) // 4))
                    retry_out = int(getattr(retry, "output_tokens", 0) or (len(retry.content or "") // 4))
                    retry_cost = float(weighted_cost(retry.model, retry_in, retry_out))
                    meta = LLMCallMeta(agent_name, retry.provider, retry.model,
                                       meta.elapsed_sec + float(getattr(retry, "elapsed_sec", 0.0) or 0.0),
                                       attempts=2,
                                       input_tokens=meta.input_tokens + retry_in,
                                       output_tokens=meta.output_tokens + retry_out,
                                       cost_units=meta.cost_units + retry_cost)
                    return _parse(retry.content or ""), meta  # second failure propagates

            try:
                result, meta = await asyncio.to_thread(run_router)
                return (result, meta) if return_meta else result
            except RuntimeError as exc:
                if "Pipeline paused" in str(exc) or "All cloud providers exhausted" in str(exc):
                    # Router hit Ollama tier and raised — sleep and retry
                    logger.warning(
                        f"[QUOTA-PAUSE] {agent_name}: {exc}. "
                        f"Sleeping 5 min before retry ({_pause_attempt + 1}/{_max_pause_retries})."
                    )
                    await asyncio.sleep(300)
                    _llm_router.PIPELINE_PAUSED_FOR_QUOTA = False
                    _router._disabled.clear()
                    _router._disabled_at.clear()
                    logger.info("[PIPELINE RESUME] Quota pause cleared — retrying cloud providers.")
                    continue
                raise  # non-quota RuntimeErrors propagate

        # Exhausted all retries — raise so the caller can persist state and re-queue.
        # (Returning "" here used to look like a real empty answer, e.g. a zero-diff coder.)
        logger.error(
            f"[QUOTA-EXHAUST] {agent_name}: Cloud quota not recovered after "
            f"{_max_pause_retries * 5} min."
        )
        raise QuotaExhaustedError(
            f"Cloud quota not recovered for '{agent_name}' after {_max_pause_retries * 5} min.")

    # Fallback: agy subprocess (legacy path, Gemini only, no per-agent routing)
    logger.warning(f"[LEGACY PATH] Using agy fallback for {agent_name} — no dynamic routing.")
    cmd = [
        "agy", "--agent", agent_name,
        "-p", prompt,
        "--print-timeout", "60m",
        "--dangerously-skip-permissions",
    ]
    if structured and pydantic_model is not None:
        cmd.extend(["--output-format", "json", "--json-schema",
                    json.dumps(pydantic_model.model_json_schema())])

    def run_cmd():
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(f"agy failed (rc={result.returncode}): STDERR: {result.stderr[:2000]}\nSTDOUT: {result.stdout[:2000]}")
        out = result.stdout or ""
        return _parse(out) if structured else out

    result = await asyncio.to_thread(run_cmd)
    in_tok = len(prompt) // 4
    out_tok = len(str(result)) // 4
    cost = float(weighted_cost("gemini-3.8-flash", in_tok, out_tok))
    meta = LLMCallMeta(agent_name, "gemini", "agy-legacy",
                       input_tokens=in_tok, output_tokens=out_tok, cost_units=cost)
    return (result, meta) if return_meta else result


# ── Pivot Council trigger ─────────────────────────────────────────────────────

async def trigger_pivot_council(task_file: str, failure_trace: str, task_id: str) -> bool:
    """Invoke the Pivot Council for a failed task. Returns True if a new task was queued."""
    current_cycle = _pivot_counts.get(task_id, 0) + 1
    _pivot_counts[task_id] = current_cycle

    logger.warning(
        f"[PIVOT] Triggering Pivot Council for task '{task_id}' "
        f"(methodological cycle {current_cycle}/{MAX_PIVOT_CYCLES})."
    )

    if current_cycle > MAX_PIVOT_CYCLES:
        logger.error(
            f"[HARD_ABORT: PHYSICS WALL] Task '{task_id}' exhausted {MAX_PIVOT_CYCLES} "
            "methodological pivots without converging. Pipeline halted for this task."
        )
        return False

    pivot_council_script = str(_HERE / "pivot_council.py")
    if not Path(pivot_council_script).exists():
        logger.warning(f"pivot_council.py not found at {pivot_council_script} — skipping council.")
        return False

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    cmd = [
        _python_for_subprocess(),
        pivot_council_script,
        "--task-file", task_file,
        "--failure-trace", failure_trace[:4000],
        "--cycle", str(current_cycle),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        rc = proc.returncode

        if rc == 0:
            logger.info(f"[PIVOT] Council succeeded for '{task_id}':\n{stdout.decode(errors='replace')[:500]}")
            return True
        else:
            logger.error(
                f"[PIVOT] Council failed (rc={rc}) for '{task_id}':\n"
                f"STDOUT: {stdout.decode(errors='replace')[:500]}\n"
                f"STDERR: {stderr.decode(errors='replace')[:500]}"
            )
            return False

    except asyncio.TimeoutError:
        logger.error(f"[PIVOT] Pivot council timed out after 600s for task '{task_id}'.")
        return False
    except Exception as e:
        logger.error(f"[PIVOT] Failed to invoke pivot council: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# v2 TDD PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

# ── Artifact protocol (P0.2) ─────────────────────────────────────────────────
# LLM file output format (Claude runs with --tools "" and cannot write files itself):
#     <<<FILE: relative/path.py>>>
#     ...complete file content...
#     <<<END FILE>>>
# Fallback accepted: a fenced block whose info string names the path, e.g. ```python path=a/b.py

_FILE_OPEN_RE  = re.compile(r"^\s*<<<\s*FILE\s*:\s*(?P<path>.+?)\s*>>>\s*$")
_FILE_CLOSE_RE = re.compile(r"^\s*<<<\s*END\s*FILE\s*>>>\s*$")
_FENCE_PATH_RE = re.compile(
    r"^\s*```[\w+.-]*[ \t]+(?:path|file|filename)\s*=\s*[\"']?(?P<path>[^\s\"']+)[\"']?\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACTS_PER_RESPONSE = 50
_FORBIDDEN_PARTS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
_FORBIDDEN_NAMES = {".env", ".credentials.json"}
# Providers whose CLI can write to disk on its own (agy --dangerously-skip-permissions).
# ClaudeSubscriptionProvider runs with --tools "" and can only produce artifact blocks.
_TOOL_CAPABLE_PROVIDERS = {"gemini"}
_SNAPSHOT_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
                      ".bat", ".ps1", ".sh", ".css", ".html", ".js", ".ts", ".sql", ".csv"}
_VOLATILE_SUFFIXES = (".log", ".pid", ".db", ".sqlite", "_state.json", ".tmp")


@dataclass
class FileArtifact:
    path: str
    content: str


@dataclass
class ArtifactParse:
    files: list[FileArtifact]
    malformed: list[str]
    prose: str


def _unwrap_inner_fence(content: str) -> str:
    """Models sometimes wrap a protocol block's body in ```lang ... ``` — strip that."""
    lines = content.splitlines(keepends=True)
    if len(lines) >= 2 and lines[0].lstrip().startswith("```") and _FENCE_CLOSE_RE.match(lines[-1]):
        return "".join(lines[1:-1])
    return content


def parse_file_blocks(text: str) -> ArtifactParse:
    """Line-based state machine. Unterminated blocks are reported as malformed and NOT
    returned — writing a truncated response would clobber a good file with half of it."""
    files: dict[str, FileArtifact] = {}
    malformed: list[str] = []
    prose: list[str] = []
    mode: Optional[str] = None   # None | "protocol" | "fence"
    cur_path = ""
    buf: list[str] = []
    for line in (text or "").splitlines(keepends=True):
        if mode is None:
            m = _FILE_OPEN_RE.match(line)
            if m:
                mode, cur_path, buf = "protocol", m.group("path").strip().strip("`'\""), []
                continue
            m = _FENCE_PATH_RE.match(line)
            if m:
                mode, cur_path, buf = "fence", m.group("path").strip(), []
                continue
            prose.append(line)
            continue
        closed = _FILE_CLOSE_RE.match(line) if mode == "protocol" else _FENCE_CLOSE_RE.match(line)
        if closed:
            content = "".join(buf)
            if mode == "protocol":
                content = _unwrap_inner_fence(content)
            if cur_path in files:
                logger.warning(f"[ARTIFACT] duplicate block for {cur_path}; last one wins")
            files[cur_path] = FileArtifact(cur_path, content)
            mode, cur_path, buf = None, "", []
            continue
        reopen = _FILE_OPEN_RE.match(line) if mode == "protocol" else None
        if reopen:
            malformed.append(f"{cur_path}: block not closed before next <<<FILE:>>>")
            cur_path, buf = reopen.group("path").strip().strip("`'\""), []
            continue
        buf.append(line)
    if mode is not None:
        malformed.append(f"{cur_path}: unterminated {mode} block (response truncated?)")
    return ArtifactParse(list(files.values()), malformed, "".join(prose).strip())


def resolve_workspace_path(workspace: Path, raw: str) -> Path:
    """Resolve an LLM-supplied path inside workspace. Raises ValueError on escape/forbidden."""
    cleaned = (raw or "").strip().strip("`'\"").replace("\\", "/")
    if not cleaned:
        raise ValueError("empty path")
    ws = Path(workspace).resolve()
    p = Path(cleaned)
    target = (p if p.is_absolute() else ws / p).resolve()
    if target == ws:
        raise ValueError(f"path is the workspace root: {raw}")
    if ws not in target.parents:
        raise ValueError(f"path escapes workspace: {raw}")
    if any(part in _FORBIDDEN_PARTS for part in target.relative_to(ws).parts):
        raise ValueError(f"forbidden directory in path: {raw}")
    if target.name in _FORBIDDEN_NAMES:
        raise ValueError(f"forbidden file: {raw}")
    return target


def atomic_write_text(path: Path, content: str) -> None:
    """Write via same-directory temp file + fsync + os.replace (retried for Windows locks)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(5):
            try:
                if path.exists() and not os.access(path, os.W_OK):
                    import stat
                    os.chmod(path, stat.S_IWRITE)
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_text_safe(path: Path, limit: Optional[int] = None) -> Optional[str]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if limit is not None:
        data = data[:limit]
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@dataclass
class FileState:
    sha256: str
    size: int
    text: Optional[str]      # None when too large to diff


def file_state(path: Path) -> Optional[FileState]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    text: Optional[str] = None
    if len(data) <= MAX_ARTIFACT_BYTES:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return FileState(hashlib.sha256(data).hexdigest(), len(data), text)


def snapshot(paths) -> dict[str, Optional[FileState]]:
    return {str(Path(p)): file_state(Path(p)) for p in paths}


def diff_line_count(before: Optional[str], after: Optional[str]) -> int:
    """Number of added + removed lines between two texts (unified diff, headers excluded)."""
    n = 0
    for line in difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                     lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            n += 1
    return n


def compute_changes(pre: dict, post: dict) -> tuple[list[str], int, dict[str, int]]:
    """Compare two snapshots -> (modified_files, total diff_lines, per-file diff_lines)."""
    per_file: dict[str, int] = {}
    for p in sorted(set(pre) | set(post)):
        a, b = pre.get(p), post.get(p)
        if a is None and b is None:
            continue
        if a is not None and b is not None and a.sha256 == b.sha256:
            continue
        if (a is None or a.text is not None) and (b is None or b.text is not None):
            n = diff_line_count(a.text if a else "", b.text if b else "")
        else:  # binary/oversize: estimate from size delta
            n = abs((b.size if b else 0) - (a.size if a else 0)) // 80
        per_file[p] = max(n, 1)
    return sorted(per_file), sum(per_file.values()), per_file


@dataclass
class ApplyResult:
    written: list[str]
    rejected: list[tuple[str, str]]
    targets: list[str]


def apply_artifacts(workspace: Path, parsed: ArtifactParse, *,
                    protected: frozenset = frozenset(),
                    allowed: Optional[frozenset] = None) -> ApplyResult:
    """Atomically write parsed artifacts inside workspace, enforcing path policy."""
    written: list[str] = []
    rejected: list[tuple[str, str]] = [(m, "malformed") for m in parsed.malformed]
    targets: list[str] = []
    protected_s = {str(Path(p).resolve()) for p in protected}
    allowed_s = {str(Path(p).resolve()) for p in allowed} if allowed is not None else None
    for i, art in enumerate(parsed.files):
        if i >= MAX_ARTIFACTS_PER_RESPONSE:
            rejected.append((art.path, f"more than {MAX_ARTIFACTS_PER_RESPONSE} files in one response"))
            continue
        try:
            target = resolve_workspace_path(workspace, art.path)
        except ValueError as exc:
            rejected.append((art.path, str(exc)))
            continue
        key = str(target)
        if key in protected_s:
            rejected.append((art.path, "protected file (read-only for this phase)"))
            continue
        if allowed_s is not None and key not in allowed_s:
            rejected.append((art.path, "not an allowed output for this phase"))
            continue
        if len(art.content.encode("utf-8", errors="replace")) > MAX_ARTIFACT_BYTES:
            rejected.append((art.path, f"exceeds {MAX_ARTIFACT_BYTES} bytes"))
            continue
        targets.append(key)
        current = file_state(target)
        content = art.content
        if (current is not None and current.text and "\r\n" in current.text
                and "\r\n" not in content):
            # Preserve the file's CRLF convention: LLMs emit LF, and an LF re-emission of an
            # unchanged CRLF file must not count as a change (it would defeat zero-diff PIVOT).
            content = content.replace("\n", "\r\n")
        if current is not None and current.text == content:
            continue  # identical rewrite is not a change
        atomic_write_text(target, content)
        written.append(key)
    return ApplyResult(written, rejected, targets)


# ── Deterministic test runner (P0.4) ─────────────────────────────────────────

@dataclass
class TestReport:
    __test__ = False  # tell pytest this is not a test class

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    failing_ids: list[str] = field(default_factory=list)
    top_frames: dict[str, str] = field(default_factory=dict)
    failure_details: dict[str, str] = field(default_factory=dict)
    has_tracebacks: bool = False
    signature_hash: str = ""
    returncode: int = 0
    timed_out: bool = False
    collection_error: bool = False
    output_tail: str = ""
    junit_path: str = ""

    @property
    def pass_rate(self) -> float:
        # skipped tests count in the denominator: skipping cannot buy an ACCEPT
        return (self.passed / self.total) if self.total else 0.0

    @property
    def is_red(self) -> bool:
        return self.failed + self.errored > 0 or self.total == 0

    def summary(self) -> str:
        return (f"total={self.total} passed={self.passed} failed={self.failed} "
                f"errored={self.errored} skipped={self.skipped} pass_rate={self.pass_rate:.2f}"
                + (" TIMED_OUT" if self.timed_out else "")
                + (" COLLECTION_ERROR" if self.collection_error else ""))


_PYTEST_FRAME_RE = re.compile(r"^(?P<file>[^\s:][^\n:]*?\.py):(?P<line>\d+):", re.M)
_PY_FRAME_RE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')
_EXC_TYPE_RE = re.compile(
    r"^\s*(?:E\s+)?(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Failed))\b")
_ASSERTION_TYPES = {"AssertionError", "Failed"}


def _exception_type(message: str, body: str) -> str:
    for src in (message or "", body or ""):
        for line in src.splitlines():
            m = _EXC_TYPE_RE.match(line)
            if m:
                return m.group("exc").rsplit(".", 1)[-1]
    if (message or "").lstrip().startswith("assert"):
        return "AssertionError"
    return "UnknownError"


def _top_frame(body: str) -> tuple[str, str]:
    """Innermost frame as ('file.py:LINE', 'file.py') from a pytest failure body."""
    frames = [(m.group("file"), m.group("line")) for m in _PYTEST_FRAME_RE.finditer(body or "")]
    if not frames:
        frames = [(m.group("file"), m.group("line")) for m in _PY_FRAME_RE.finditer(body or "")]
    if not frames:
        return "", ""
    f, line = frames[-1]
    name = Path(f.strip()).name
    return f"{name}:{line}", name


def parse_junit_xml(junit_path: Path) -> TestReport:
    report = TestReport(junit_path=str(junit_path))
    root = ET.parse(str(junit_path)).getroot()
    sig_parts: list[str] = []
    for tc in root.iter("testcase"):
        cls, name = tc.get("classname", ""), tc.get("name", "")
        tid = f"{cls}::{name}" if cls else name
        report.total += 1
        failure, error, skipped = tc.find("failure"), tc.find("error"), tc.find("skipped")
        el = failure if failure is not None else error
        if el is None:
            if skipped is not None:
                report.skipped += 1
            else:
                report.passed += 1
            continue
        if failure is not None:
            report.failed += 1
        else:
            report.errored += 1
            if not cls or "collect" in (el.get("message") or "").lower():
                report.collection_error = True
        message = el.get("message") or ""
        body = el.text or ""
        exc = _exception_type(message, body)
        frame, frame_file = _top_frame(body)
        if error is not None or exc not in _ASSERTION_TYPES:
            report.has_tracebacks = True
        report.failing_ids.append(tid)
        report.top_frames[tid] = f"{frame} {exc}".strip()
        report.failure_details[tid] = f"{message}\n{body}"[-3000:]
        # line numbers deliberately excluded: "same failure" must survive unrelated edits
        sig_parts.append(f"{tid}|{frame_file}|{exc}")
    report.signature_hash = (hashlib.sha256("\n".join(sorted(sig_parts)).encode("utf-8")).hexdigest()
                             if sig_parts else "")
    return report


def _python_for_subprocess() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def run_pytest(test_paths, cwd: Path, junit_path: Path, timeout: int = V2_PYTEST_TIMEOUT_S) -> TestReport:
    """Run pytest in a subprocess and build a TestReport from --junitxml. Never an LLM call."""
    junit_path = Path(junit_path)
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        junit_path.unlink()
    cmd = [_python_for_subprocess(), "-m", "pytest", "-q", "-p", "no:cacheprovider",
           f"--junitxml={junit_path}", "-o", "junit_family=xunit2",
           "--rootdir", str(cwd), *[str(p) for p in test_paths]]
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return TestReport(total=0, errored=1, timed_out=True, returncode=-1,
                          signature_hash=hashlib.sha256(b"timeout").hexdigest(),
                          failing_ids=["<pytest-timeout>"],
                          top_frames={"<pytest-timeout>": f"timed out after {timeout}s"},
                          output_tail=(out or "")[-4000:], junit_path=str(junit_path))
    tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
    if junit_path.exists() and junit_path.stat().st_size > 0:
        try:
            report = parse_junit_xml(junit_path)
            report.returncode = proc.returncode
            report.output_tail = tail
            return report
        except Exception as exc:
            logger.warning(f"Error parsing {junit_path}: {exc}")
    # Fallback if no valid junit XML was written (e.g. collection crashed hard)
    report = TestReport(total=0, errored=1, collection_error=True, returncode=proc.returncode,
                        output_tail=tail, junit_path=str(junit_path))
    report.signature_hash = hashlib.sha256(tail.encode("utf-8", errors="replace")).hexdigest()
    report.failing_ids = ["<collection-failure>"]
    report.top_frames = {"<collection-failure>": f"pytest failed with rc={proc.returncode}"}
    report.failure_details = {"<collection-failure>": tail}
    return report


# ── TaskContext (P0.3 run state) ─────────────────────────────────────────────

DECISION_ACCEPT = "ACCEPT"
DECISION_CONTINUE = "CONTINUE"
DECISION_PIVOT = "PIVOT"
DECISION_ESCALATE = "ESCALATE"
DECISION_QUARANTINE_CANDIDATE = "QUARANTINE_CANDIDATE"
DECISION_QUARANTINE = "QUARANTINE"

OUTCOME_ACCEPTED = "ACCEPTED"
OUTCOME_PIVOTED = "PIVOTED"
OUTCOME_QUARANTINED = "QUARANTINED"
OUTCOME_HARD_ABORT = "HARD_ABORT"
TERMINAL_OUTCOMES = frozenset({OUTCOME_ACCEPTED, OUTCOME_PIVOTED, OUTCOME_QUARANTINED, OUTCOME_HARD_ABORT})


@dataclass
class Dossier:
    text: str
    sources: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    phase: str
    agent: str
    provider: str
    model: str
    summary: str
    modified_files: list[str]
    diff_lines: int
    rejected: list[list[str]] = field(default_factory=list)


@dataclass
class ProgressRecord:
    iteration: int
    phase: str
    pass_rate: float
    audit_score: int
    verdict: str
    weighted_findings: int
    blocking_findings: int          # CRITICAL + HIGH count
    diff_lines: int
    signature_hash: str
    evidence_files: int
    asymmetry_ok: bool
    has_tracebacks: bool
    stalled: bool = False
    decision: str = ""
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class GateDecision:
    decision: str
    reason: str
    debug: bool = False


def _safe_id(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(task_id)) or "task"


@dataclass
class TaskContext:
    task_id: str
    task_file: str
    agent_name: str
    prompt: str
    raw_task: dict
    prompt_sha: str
    workspace: Path
    run_dir: Path
    test_file: Path
    plan: Optional[Plan] = None
    dossier: Optional[Dossier] = None
    test_report: Optional[TestReport] = None
    audit: Optional[AuditResult] = None
    progress: list[ProgressRecord] = field(default_factory=list)
    # Stage A state
    red_verified: bool = False
    test_sha: str = ""
    tautology_strikes: int = 0
    # evidence / asymmetry
    evidence_files: set = field(default_factory=set)
    baseline_hashes: dict = field(default_factory=dict)
    producer_models: set = field(default_factory=set)
    audit_model: Optional[tuple] = None
    asymmetry_ok: bool = False
    exec_results: list[ExecutionResult] = field(default_factory=list)
    pending_findings: list[Finding] = field(default_factory=list)
    # convergence state
    iteration: int = 0
    round_diff_lines: int = 0
    escalation_used: bool = False
    adjudication_used: bool = False
    debug_notes: list[str] = field(default_factory=list)
    next_work: str = ""
    decision: Optional[GateDecision] = None
    outcome: Optional[str] = None
    outcome_reason: str = ""
    response_seq: int = 0
    # telemetry
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_units: float = 0.0
    call_records: list[dict] = field(default_factory=list)

    def record_call(self, meta: LLMCallMeta) -> None:
        self.total_input_tokens += meta.input_tokens
        self.total_output_tokens += meta.output_tokens
        self.total_cost_units += meta.cost_units
        self.call_records.append({
            "agent": meta.agent,
            "model": meta.model,
            "input_tokens": meta.input_tokens,
            "output_tokens": meta.output_tokens,
            "cost_units": meta.cost_units,
        })

    @property
    def context_path(self) -> Path:
        return self.run_dir / "context.json"

    @property
    def test_rel(self) -> str:
        return self.test_file.resolve().relative_to(self.workspace.resolve()).as_posix()

    @property
    def mutating(self) -> bool:
        return self.plan.mutating if self.plan else True

    @classmethod
    def create_or_resume(cls, task_file: str, raw: dict) -> "TaskContext":
        """Resume from run_dir/context.json when the task prompt is unchanged; otherwise
        archive the stale run directory and start fresh."""
        task_id = str(raw.get("task_id") or os.path.splitext(os.path.basename(task_file))[0])
        prompt = raw.get("prompt", "") or ""
        agent = raw.get("agent_name") or raw.get("agent") or "cochem-coder"
        ws_raw = raw.get("workspace")
        workspace = (Path(ws_raw) if ws_raw and Path(ws_raw).is_dir() else TDD_WORKSPACE_ROOT).resolve()
        sid = _safe_id(task_id)
        runs_dir = Path(os.environ.get("COCHEM_TDD_RUNS_DIR", str(TDD_RUNS_DIR))).resolve()
        run_dir = (runs_dir / sid).resolve()
        prompt_sha = hashlib.sha256(
            (prompt + "\0" + str(raw.get("kanban_source", ""))).encode("utf-8")).hexdigest()
        test_file = workspace / "tests" / "tdd" / f"test_task_{sid}.py"
        fresh = cls(task_id=task_id, task_file=str(task_file), agent_name=agent, prompt=prompt,
                    raw_task=raw, prompt_sha=prompt_sha, workspace=workspace, run_dir=run_dir,
                    test_file=test_file)
        if fresh.context_path.exists():
            data = None
            try:
                data = json.loads(fresh.context_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning(f"[TDD {task_id}] unreadable context.json ({exc}); starting fresh")
            if data and data.get("prompt_sha") == prompt_sha:
                try:
                    return cls.from_dict(data, raw)
                except (KeyError, TypeError, ValueError, pydantic.ValidationError) as exc:
                    logger.warning(f"[TDD {task_id}] incompatible context.json ({exc}); starting fresh")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived = run_dir.with_name(f"{sid}__superseded_{stamp}")
            try:
                run_dir.rename(archived)
                logger.info(f"[TDD {task_id}] archived previous run to {archived}")
            except OSError as exc:
                logger.warning(f"[TDD {task_id}] could not archive previous run: {exc}")
        run_dir.mkdir(parents=True, exist_ok=True)
        return fresh

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "task_file": self.task_file, "agent_name": self.agent_name,
            "prompt_sha": self.prompt_sha, "workspace": str(self.workspace),
            "run_dir": str(self.run_dir), "test_file": str(self.test_file),
            "plan": self.plan.model_dump() if self.plan else None,
            "dossier": dataclasses.asdict(self.dossier) if self.dossier else None,
            "test_report": dataclasses.asdict(self.test_report) if self.test_report else None,
            "audit": self.audit.model_dump() if self.audit else None,
            "progress": [dataclasses.asdict(r) for r in self.progress],
            "red_verified": self.red_verified, "test_sha": self.test_sha,
            "tautology_strikes": self.tautology_strikes,
            "evidence_files": sorted(self.evidence_files),
            "baseline_hashes": self.baseline_hashes,
            "producer_models": sorted(list(t) for t in self.producer_models),
            "audit_model": list(self.audit_model) if self.audit_model else None,
            "asymmetry_ok": self.asymmetry_ok,
            "exec_results": [dataclasses.asdict(e) for e in self.exec_results],
            "pending_findings": [f.model_dump() for f in self.pending_findings],
            "iteration": self.iteration, "round_diff_lines": self.round_diff_lines,
            "escalation_used": self.escalation_used, "adjudication_used": self.adjudication_used,
            "debug_notes": self.debug_notes, "next_work": self.next_work,
            "decision": dataclasses.asdict(self.decision) if self.decision else None,
            "outcome": self.outcome, "outcome_reason": self.outcome_reason,
            "response_seq": self.response_seq,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_units": self.total_cost_units,
            "call_records": self.call_records,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict, raw: dict) -> "TaskContext":
        ctx = cls(task_id=d["task_id"], task_file=d["task_file"], agent_name=d["agent_name"],
                  prompt=raw.get("prompt", "") or "", raw_task=raw, prompt_sha=d["prompt_sha"],
                  workspace=Path(d["workspace"]), run_dir=Path(d["run_dir"]),
                  test_file=Path(d["test_file"]))
        ctx.plan = Plan.model_validate(d["plan"]) if d.get("plan") else None
        ctx.dossier = Dossier(**d["dossier"]) if d.get("dossier") else None
        ctx.test_report = TestReport(**d["test_report"]) if d.get("test_report") else None
        ctx.audit = AuditResult.model_validate(d["audit"]) if d.get("audit") else None
        ctx.progress = [ProgressRecord(**r) for r in d.get("progress", [])]
        ctx.red_verified = bool(d.get("red_verified"))
        ctx.test_sha = d.get("test_sha", "")
        ctx.tautology_strikes = int(d.get("tautology_strikes", 0))
        ctx.evidence_files = set(d.get("evidence_files", []))
        ctx.baseline_hashes = dict(d.get("baseline_hashes", {}))
        ctx.producer_models = {tuple(t) for t in d.get("producer_models", [])}
        ctx.audit_model = tuple(d["audit_model"]) if d.get("audit_model") else None
        ctx.asymmetry_ok = bool(d.get("asymmetry_ok"))
        ctx.exec_results = [ExecutionResult(**e) for e in d.get("exec_results", [])]
        ctx.pending_findings = [Finding.model_validate(f) for f in d.get("pending_findings", [])]
        ctx.iteration = int(d.get("iteration", 0))
        ctx.round_diff_lines = int(d.get("round_diff_lines", 0))
        ctx.escalation_used = bool(d.get("escalation_used"))
        ctx.adjudication_used = bool(d.get("adjudication_used"))
        ctx.debug_notes = list(d.get("debug_notes", []))
        ctx.next_work = d.get("next_work", "")
        ctx.decision = GateDecision(**d["decision"]) if d.get("decision") else None
        ctx.outcome = d.get("outcome")
        ctx.outcome_reason = d.get("outcome_reason", "")
        ctx.response_seq = int(d.get("response_seq", 0))
        ctx.total_input_tokens = int(d.get("total_input_tokens", 0))
        ctx.total_output_tokens = int(d.get("total_output_tokens", 0))
        ctx.total_cost_units = float(d.get("total_cost_units", 0.0))
        ctx.call_records = list(d.get("call_records", []))
        return ctx

    def save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.context_path, json.dumps(self.to_dict(), indent=2, default=str))

    def save_artifact(self, name: str, content: str) -> Path:
        path = self.run_dir / name
        atomic_write_text(path, content)
        return path

    def save_response(self, phase: str, text: str) -> None:
        self.response_seq += 1
        self.save_artifact(f"responses/{self.response_seq:03d}_{phase}.txt", text or "")

    def reset_rounds(self) -> None:
        """Stage B/C always restart from P4 (Stage A artefacts are reused)."""
        self.progress = []
        self.iteration = 0
        self.round_diff_lines = 0
        self.audit = None
        self.decision = None
        self.debug_notes = []
        self.next_work = ""

    def log(self, msg: str, level: int = logging.INFO) -> None:
        logger.log(level, f"[TDD {self.task_id}] {msg}")


# ── Progress gate (pure Python, zero LLM tokens) ─────────────────────────────

def weighted_findings(findings) -> int:
    return sum(SEVERITY_WEIGHTS.get(f.severity, 4) for f in findings)


def blocking_count(findings) -> int:
    return sum(1 for f in findings if f.severity in ("CRITICAL", "HIGH"))


def is_stalled(cur: ProgressRecord, prev: ProgressRecord,
               prev2: Optional[ProgressRecord] = None) -> bool:
    """Negation of the design §3.2 improvement predicate."""
    eps = 1e-9
    improved = (cur.pass_rate >= prev.pass_rate + 0.05 - eps
                or cur.audit_score >= prev.audit_score + 5
                or cur.weighted_findings <= prev.weighted_findings - 2)
    if cur.diff_lines == 0:
        improved = False
    if (cur.signature_hash and cur.signature_hash == prev.signature_hash
            and cur.pass_rate <= prev.pass_rate + eps):
        improved = False
    if (prev2 is not None and cur.pass_rate < prev.pass_rate - eps
            and prev.pass_rate > prev2.pass_rate + eps):
        improved = False   # oscillation
    return not improved


def progress_gate(records: list[ProgressRecord], *,
                  pass_threshold: int = V2_AUDIT_PASS_THRESHOLD,
                  escalation_low: int = V2_ESCALATION_BAND_LOW,
                  stall_limit: int = V2_STALL_LIMIT,
                  max_iterations: int = V2_MAX_AUDIT_CYCLES,
                  mutating: bool = True,
                  escalation_used: bool = False,
                  adjudication_used: bool = False) -> GateDecision:
    """Pure-Python decision function. Zero LLM calls."""
    if not records:
        raise ValueError("progress_gate requires at least one ProgressRecord")
    cur = records[-1]
    i = cur.iteration
    if i >= 1 and len(records) >= 2:
        cur.stalled = is_stalled(cur, records[-2], records[-3] if len(records) >= 3 else None)

    # 1. spoofing / zero evidence -> adjudication (at most once per task)
    if not adjudication_used:
        if cur.verdict == "SPOOFING_DETECTED":
            return GateDecision(DECISION_QUARANTINE_CANDIDATE, "auditor verdict SPOOFING_DETECTED")
        if mutating and cur.evidence_files == 0:
            return GateDecision(DECISION_QUARANTINE_CANDIDATE, "no evidence files on disk for a mutating task")
    # 2. accept
    if (cur.pass_rate >= 1.0 and cur.audit_score >= pass_threshold and cur.verdict == "PASS"
            and cur.blocking_findings == 0 and cur.asymmetry_ok
            and (cur.evidence_files > 0 or not mutating)):
        return GateDecision(DECISION_ACCEPT,
                            f"pass_rate=1.00, score {cur.audit_score} >= {pass_threshold}, asymmetric PASS")
    # 3. zero diff in a convergence iteration is conclusive
    if i >= 1 and cur.diff_lines == 0:
        return GateDecision(DECISION_PIVOT, f"iteration {i} produced zero diff lines")
    # 4. consecutive stalls
    if i >= 1:
        tail = [r for r in records if r.iteration >= 1][-stall_limit:]
        if len(tail) >= stall_limit and all(r.stalled for r in tail):
            return GateDecision(DECISION_PIVOT, f"{stall_limit} consecutive stalled iterations")
    # 5. hard cap
    if i >= max_iterations:
        return GateDecision(DECISION_PIVOT, f"hard cap of {max_iterations} convergence iterations reached")
    # 6. ambiguous band -> one escalation audit per task
    if (cur.pass_rate >= 1.0 and escalation_low <= cur.audit_score < pass_threshold
            and cur.asymmetry_ok and not escalation_used):
        return GateDecision(DECISION_ESCALATE, f"score {cur.audit_score} in escalation band")
    # 7. tracebacks -> continue with a debug interrupt
    if cur.has_tracebacks:
        return GateDecision(DECISION_CONTINUE, "tests raised tracebacks", debug=True)
    # 8. continue
    return GateDecision(DECISION_CONTINUE, f"pass_rate={cur.pass_rate:.2f} score={cur.audit_score} "
                                           f"weighted_findings={cur.weighted_findings}")


# ── Prompt helpers ───────────────────────────────────────────────────────────

_CAP_FILE_CHARS = 24_000
_CAP_BUNDLE_CHARS = 90_000
_CAP_FAILURE_CHARS = 32_000
_CAP_NEXT_WORK_CHARS = 12_000

_COCHEM_CONSTRAINTS = """HARD CONSTRAINTS (CoChem):
- Real, working code only. No mocks, stubs, placeholders, empty `pass` bodies, TODOs or NotImplementedError.
- Every subprocess call must pass creationflags=subprocess.CREATE_NO_WINDOW and encoding='utf-8'.
- Open text files with encoding='utf-8'. No LLM/network calls inside SQLite write transactions (WAL rule).
- Never hard-code expected test outputs or special-case the test harness."""


def _artifact_protocol(ctx: "TaskContext", protected: list[str]) -> str:
    prot = "\n".join(f"  - {p}" for p in protected) or "  (none)"
    open_tag = "<<<" + "FILE: relative/path/from/workspace.py>>>"
    close_tag = "<<<" + "END FILE>>>"
    return f"""OUTPUT PROTOCOL — you have NO file-system tools; the orchestrator writes files for you.
Emit every file you create or change as a COMPLETE file (never a diff or an excerpt), exactly:
{open_tag}
<entire file content>
{close_tag}
- Paths are relative to the workspace root: {ctx.workspace}
- Only emit files you actually change. Blocks that are not closed are discarded.
- Read-only (writes are rejected and reported to the auditor):
{prot}
After the file blocks, write a short plain-text summary of what you changed and why."""


def _rel(ctx: "TaskContext", path: str) -> str:
    try:
        return Path(path).resolve().relative_to(ctx.workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def render_files(ctx: "TaskContext", paths, cap_total: int = _CAP_BUNDLE_CHARS,
                 cap_each: int = _CAP_FILE_CHARS, with_hash: bool = False) -> str:
    parts: list[str] = []
    used = 0
    for p in sorted({str(x) for x in paths}):
        st = file_state(Path(p))
        if st is None:
            parts.append(f"--- FILE: {_rel(ctx, p)} (MISSING ON DISK) ---\n")
            continue
        text = st.text if st.text is not None else f"<binary or oversize file, {st.size} bytes>"
        if len(text) > cap_each:
            text = text[:cap_each] + f"\n... [truncated, {len(st.text or '')} chars total]\n"
        header = f"--- FILE: {_rel(ctx, p)}"
        if with_hash:
            header += (f" sha256_before_task={ctx.baseline_hashes.get(p, 'unknown')}"
                       f" sha256_now={st.sha256}")
        block = f"{header} ---\n{text}\n"
        if used + len(block) > cap_total:
            parts.append(f"--- FILE: {_rel(ctx, p)} (omitted: bundle size cap reached) ---\n")
            continue
        used += len(block)
        parts.append(block)
    return "\n".join(parts) if parts else "(no files)"


def _plan_brief(plan: Optional[Plan]) -> str:
    if plan is None:
        return "(no plan)"
    crit = "\n".join(f"  [{c.id}] {c.statement}" for c in plan.acceptance_criteria)
    tests = "\n".join(f"  [{t.id}] {t.name} -> {t.asserts} (criteria {', '.join(t.criteria_ids)})"
                      for t in plan.test_cases)
    return (f"GOAL: {plan.goal}\nACCEPTANCE CRITERIA:\n{crit}\nTEST CASES:\n{tests}\n"
            f"FILE TARGETS: {', '.join(plan.file_targets) or '(none)'}\n"
            f"OUT OF SCOPE: {'; '.join(plan.out_of_scope) or '(none)'}")


def _render_findings(findings) -> str:
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows = sorted(findings, key=lambda f: order.get(f.severity, 1))
    return "\n".join(
        f"- [{f.severity}] {f.file or '?'}{':' + str(f.line) if f.line else ''} "
        f"{('(' + f.criteria_id + ') ') if f.criteria_id else ''}{f.issue}" for f in rows) or "(none)"


def _render_failures(report: Optional[TestReport], cap: int = _CAP_FAILURE_CHARS) -> str:
    if report is None:
        return "(tests not run)"
    lines = [f"MEASURED BY ORCHESTRATOR: {report.summary()}"]
    for tid in report.failing_ids:
        lines.append(f"\n### {tid} — {report.top_frames.get(tid, '')}\n{report.failure_details.get(tid, '')}")
    if report.collection_error or report.timed_out or report.total == 0:
        lines.append(f"\npytest output tail:\n{report.output_tail}")
    text = "\n".join(lines)
    if len(text) > cap:
        trunc_tag = f"\n... [truncated, {len(text)} chars total]"
        text = text[:max(0, cap - len(trunc_tag))] + trunc_tag
    return text


def _workspace_listing(ws: Path, limit: int = 400) -> str:
    """Depth-limited listing of source-like files for the planner."""
    out: list[str] = []
    skip = _FORBIDDEN_PARTS | {"dropzones", "tdd_runs", ".pytest_cache", "failed_prompts", "prompts"}
    for root, dirs, files in os.walk(ws):
        rel_root = Path(root).relative_to(ws)
        dirs[:] = [] if len(rel_root.parts) >= 2 else sorted(
            d for d in dirs if d not in skip and (not d.startswith(".") or d == ".scripts"))
        for f in sorted(files):
            if Path(f).suffix in _SNAPSHOT_SUFFIXES and not f.endswith(_VOLATILE_SUFFIXES):
                out.append((rel_root / f).as_posix())
                if len(out) >= limit:
                    return "\n".join(out) + "\n... (listing truncated)"
    return "\n".join(out)


def tracked_paths(ctx: "TaskContext") -> set[str]:
    """Files whose pre/post state is compared around a code phase (design §2.2 evidence):
    evidence so far, the test file, file_targets and their sibling source files."""
    paths: set[str] = set(ctx.evidence_files)
    paths.add(str(ctx.test_file.resolve()))
    for target in (ctx.plan.file_targets if ctx.plan else []):
        try:
            p = resolve_workspace_path(ctx.workspace, target)
        except ValueError:
            continue
        scan_dir = p if p.is_dir() else p.parent
        if not p.is_dir():
            paths.add(str(p))
        if scan_dir.is_dir():
            with contextlib.suppress(OSError):
                for child in sorted(scan_dir.iterdir())[:300]:
                    if (child.is_file() and child.suffix in _SNAPSHOT_SUFFIXES
                            and not child.name.endswith(_VOLATILE_SUFFIXES)):
                        paths.add(str(child.resolve()))
    return paths


def _test_file_block(ctx: "TaskContext") -> str:
    return f"TEST FILE ({ctx.test_rel}, read-only):\n{read_text_safe(ctx.test_file) or '(missing)'}"


# ── Code-producing phase (shared by P3, P4, P7, P9) ──────────────────────────

async def _run_code_phase(ctx: TaskContext, phase: str, agent: str, prompt: str, *,
                          allowed: Optional[frozenset] = None,
                          protect_tests: bool = True) -> ExecutionResult:
    """LLM call -> parse artifact blocks -> atomic writes -> disk diff -> ExecutionResult."""
    pre = snapshot(tracked_paths(ctx))
    text, meta = await safe_chat_cli(agent, prompt, return_meta=True)
    ctx.record_call(meta)
    ctx.save_response(phase, text)
    parsed = parse_file_blocks(text)

    for art in parsed.files:   # pre-state of targets that were not tracked yet
        with contextlib.suppress(ValueError):
            key = str(resolve_workspace_path(ctx.workspace, art.path))
            if key not in pre:
                pre[key] = file_state(Path(key))
    for key, st in pre.items():
        ctx.baseline_hashes.setdefault(key, st.sha256 if st else "new-file")

    test_key = str(ctx.test_file.resolve())
    protected = frozenset({test_key}) if protect_tests else frozenset()
    applied = apply_artifacts(ctx.workspace, parsed, protected=protected, allowed=allowed)
    post = snapshot(set(pre) | set(applied.targets))
    modified, _, per_file = compute_changes(pre, post)

    if meta.provider not in _TOOL_CAPABLE_PROVIDERS:
        # A tool-less provider cannot have caused out-of-band changes; ignore concurrent
        # writes by other workers to tracked neighbour files.
        own = set(applied.written)
        modified = [m for m in modified if m in own]

    # Tamper guard: the test file must stay byte-identical to the RED-verified copy.
    if protect_tests and ctx.red_verified and test_key in modified:
        canonical = read_text_safe(ctx.run_dir / "test_canonical.py")
        if canonical is not None:
            atomic_write_text(ctx.test_file, canonical)
        modified = [m for m in modified if m != test_key]
        ctx.pending_findings.append(Finding(
            severity="CRITICAL", file=ctx.test_rel,
            issue=f"{phase} ({meta.provider}/{meta.model}) modified the protected test file; restored."))
    for path, reason in applied.rejected:
        ctx.pending_findings.append(Finding(
            severity="CRITICAL" if "protected" in reason else "MEDIUM", file=path,
            issue=f"{phase} artifact rejected: {reason}"))

    diff_lines = sum(per_file[m] for m in modified)
    result = ExecutionResult(phase=phase, agent=agent, provider=meta.provider, model=meta.model,
                             summary=parsed.prose[-3000:], modified_files=modified,
                             diff_lines=diff_lines, rejected=[list(r) for r in applied.rejected])
    ctx.exec_results.append(result)
    if modified and phase != "P3":
        ctx.producer_models.add(meta.triple)
        ctx.evidence_files.update(modified)
    ctx.log(f"{phase} {agent} via {meta.provider}/{meta.model}: {len(modified)} file(s), "
            f"{diff_lines} diff lines, {len(applied.rejected)} rejected")
    ctx.save()
    return result


# ── Stage A: FRAME ───────────────────────────────────────────────────────────

async def phase_plan(ctx: TaskContext) -> None:
    """P1 PLAN — cochem-planner, structured Plan."""
    source = ""
    src_path = ctx.raw_task.get("kanban_source")
    if src_path and Path(src_path).is_file():
        source = read_text_safe(Path(src_path), limit=70_000) or ""
    prompt = f"""You are the P1 PLANNER of a test-driven pipeline. Produce the plan for this task.

TASK (task_id {ctx.task_id}):
{ctx.prompt}

SOURCE DOCUMENT ({src_path or 'none'}):
{source or '(none)'}

WORKSPACE ROOT: {ctx.workspace}
WORKSPACE LISTING (partial):
{_workspace_listing(ctx.workspace)}

Tests will live in {ctx.test_rel} and run with `python -m pytest` from the workspace root.
Every acceptance criterion must be verifiable by an automated pytest test against real code.
file_targets are workspace-relative paths the implementation will create or modify
(never the test file). Set "mutating": false only if the task changes no files.

Return ONLY one JSON object:
{{"goal": "...",
  "acceptance_criteria": [{{"id": "AC1", "statement": "...", "verifiable_by": "T1"}}],
  "test_cases": [{{"id": "T1", "name": "test_...", "asserts": "...", "criteria_ids": ["AC1"]}}],
  "file_targets": ["relative/path.py"], "out_of_scope": ["..."], "risks": ["..."],
  "mutating": true}}"""
    try:
        plan, meta = await safe_chat_cli("cochem-planner", prompt, pydantic_model=Plan, return_meta=True)
        ctx.record_call(meta)
    except StructuredOutputError as exc:
        raise PhaseFailure(f"P1 PLAN produced no valid plan: {exc}") from exc
    ctx.plan = plan
    ctx.save_artifact("plan.json", plan.model_dump_json(indent=2))
    ctx.log(f"P1 plan via {meta.provider}/{meta.model}: {len(plan.acceptance_criteria)} criteria, "
            f"{len(plan.test_cases)} tests, targets={plan.file_targets}")
    ctx.save()


_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)


def _research_sources(ctx: TaskContext) -> list[Path]:
    """Existing file_targets plus the local modules they import (one level)."""
    selected: list[Path] = []
    for target in (ctx.plan.file_targets if ctx.plan else []):
        with contextlib.suppress(ValueError):
            p = resolve_workspace_path(ctx.workspace, target)
            if p.is_file():
                selected.append(p)
    for p in list(selected):
        for m in _IMPORT_RE.finditer(read_text_safe(p, limit=200_000) or ""):
            mod = (m.group(1) or m.group(2) or "").split(".")[0]
            for base in (p.parent, ctx.workspace):
                cand = base / f"{mod}.py"
                if cand.is_file() and cand not in selected:
                    selected.append(cand)
    return selected


async def phase_research(ctx: TaskContext) -> None:
    """P2 RESEARCH — cochem-researcher, markdown dossier."""
    sources = _research_sources(ctx)
    source_text = render_files(ctx, [str(p) for p in sources], cap_total=V2_RESEARCH_CONTEXT_TOKENS * 4)
    prompt = f"""You are the P2 RESEARCHER. Write a concise context dossier (markdown, <= 1500 words)
for the engineer who will implement the plan below. Cover: existing code and patterns to reuse,
exact APIs and signatures involved, constraints, and pitfalls. Do not write the implementation.

{_plan_brief(ctx.plan)}

TASK:
{ctx.prompt}

{_COCHEM_CONSTRAINTS}

RELEVANT SOURCE FILES:
{source_text}"""
    text, meta = await safe_chat_cli("cochem-researcher", prompt, return_meta=True)
    ctx.record_call(meta)
    ctx.save_response("P2", text)
    ctx.dossier = Dossier(text=(text or "").strip() or "(researcher returned no content)",
                          sources=[_rel(ctx, str(p)) for p in sources])
    ctx.save_artifact("dossier.md", ctx.dossier.text)
    ctx.log(f"P2 dossier: {len(ctx.dossier.text)} chars from {len(sources)} source file(s)")
    ctx.save()


async def phase_test_first(ctx: TaskContext) -> None:
    """P3 TEST-FIRST — cochem-test-author, then the deterministic RED gate (max 2 attempts)."""
    feedback = ""
    test_key = frozenset({str(ctx.test_file.resolve())})
    for attempt in (1, 2):
        open_test_tag = "<<<" + f"FILE: {ctx.test_rel}>>>"
        close_test_tag = "<<<" + "END FILE>>>"
        prompt = f"""You are the P3 TEST AUTHOR. Write the pytest file for the plan below BEFORE any
implementation exists. Cover every test case id. Tests must exercise real behaviour of the real
module(s) in file_targets; they must FAIL now (ImportError or assertion) and PASS once the feature
is correctly implemented. No mocks of the code under test, no tautologies, no skips.

The file runs as `python -m pytest {ctx.test_rel}` with cwd = {ctx.workspace}; the workspace root
is importable. If a module lives in a sub-directory without __init__.py, insert that directory
into sys.path at the top of the test file using pathlib relative to __file__.

{_plan_brief(ctx.plan)}

CONTEXT DOSSIER:
{ctx.dossier.text if ctx.dossier else '(none)'}

{_COCHEM_CONSTRAINTS}
{feedback}
Emit exactly ONE file block:
{open_test_tag}
...complete test module...
{close_test_tag}"""
        await _run_code_phase(ctx, "P3", "cochem-test-author", prompt, allowed=test_key,
                              protect_tests=False)
        source = read_text_safe(ctx.test_file)
        if not source:
            feedback = f"\nPREVIOUS ATTEMPT FAILED: no file block for {ctx.test_rel} was produced.\n"
            ctx.log(f"P3 attempt {attempt}: no test file produced", logging.WARNING)
            continue
        try:
            compile(source, str(ctx.test_file), "exec")
        except SyntaxError as exc:
            feedback = f"\nPREVIOUS ATTEMPT FAILED: SyntaxError in the test file: {exc}\n"
            ctx.log(f"P3 attempt {attempt}: {exc}", logging.WARNING)
            continue
        async with _gov.pytest_gate():
            report = await asyncio.to_thread(run_pytest, [ctx.test_file], ctx.workspace,
                                             ctx.run_dir / f"junit_P3_red_{attempt}.xml")
        ctx.log(f"P3 RED gate attempt {attempt}: {report.summary()}")
        if report.total == 0 and not report.collection_error:
            feedback = "\nPREVIOUS ATTEMPT FAILED: pytest collected zero tests.\n"
            continue
        if report.is_red:
            ctx.red_verified = True
            ctx.test_sha = file_state(ctx.test_file).sha256
            ctx.save_artifact("test_canonical.py", source)
            ctx.test_report = report
            ctx.save()
            return
        ctx.tautology_strikes += 1
        feedback = ("\nPREVIOUS ATTEMPT FAILED: every test PASSED before any implementation was "
                    "written — the tests are tautological or do not exercise the new behaviour.\n")
        ctx.save()
    if ctx.tautology_strikes >= 2:
        ctx.pending_findings.append(Finding(
            severity="CRITICAL", file=ctx.test_rel,
            issue="P3 tests were GREEN before implementation on both attempts (tautology)."))
        ctx.decision = GateDecision(DECISION_QUARANTINE_CANDIDATE, "P3 tautology detected twice")
        raise PhaseFailure("P3 tautology detected twice")
    raise PhaseFailure("P3 could not produce a valid RED test file in 2 attempts")


async def run_setup(ctx: TaskContext) -> None:
    """Stage A — runs once per task; resumes from persisted artefacts after a quota pause."""
    if ctx.plan is None:
        await phase_plan(ctx)
    else:
        ctx.log("P1 plan reused from previous run")
    if ctx.dossier is None:
        await phase_research(ctx)
    else:
        ctx.log("P2 dossier reused from previous run")
    st = file_state(ctx.test_file)
    if ctx.red_verified and st is not None and st.sha256 == ctx.test_sha:
        ctx.log("P3 RED-verified tests reused from previous run")
        return
    ctx.red_verified = False
    await phase_test_first(ctx)


# ── Stage B / C phases ───────────────────────────────────────────────────────

def _target_paths(ctx: TaskContext) -> set[str]:
    out: set[str] = set()
    for t in (ctx.plan.file_targets if ctx.plan else []):
        with contextlib.suppress(ValueError):
            p = resolve_workspace_path(ctx.workspace, t)
            if not p.is_dir():
                out.add(str(p))
    return out


async def phase_code(ctx: TaskContext) -> None:
    """P4 CODE — task agent (default cochem-coder)."""
    prompt = f"""You are the P4 CODER. Implement the task so that every test passes and every
acceptance criterion is met by real, working code.

TASK (task_id {ctx.task_id}):
{ctx.prompt}

{_plan_brief(ctx.plan)}

CONTEXT DOSSIER:
{ctx.dossier.text if ctx.dossier else '(none)'}

{_test_file_block(ctx)}

CURRENT CONTENT OF FILE TARGETS:
{render_files(ctx, _target_paths(ctx))}

{_COCHEM_CONSTRAINTS}

{_artifact_protocol(ctx, [ctx.test_rel])}"""
    result = await _run_code_phase(ctx, "P4", ctx.agent_name, prompt)
    ctx.round_diff_lines += result.diff_lines


async def phase_test(ctx: TaskContext, phase: str) -> TestReport:
    """P5 / P10 test execution — pytest subprocess, zero LLM quota."""
    async with _gov.pytest_gate():
        report = await asyncio.to_thread(run_pytest, [ctx.test_file], ctx.workspace,
                                         ctx.run_dir / f"junit_{phase}_i{ctx.iteration}.xml")
    if report.total == 0:
        ctx.pending_findings.append(Finding(severity="HIGH", file=ctx.test_rel,
                                            issue="no tests executed (pass_rate treated as 0)"))
    ctx.test_report = report
    ctx.log(f"{phase} pytest: {report.summary()}")
    ctx.save()
    return report


def _audit_prompt(ctx: TaskContext, report: TestReport,
                  second_opinion: Optional[AuditResult] = None) -> str:
    last = next((e for e in reversed(ctx.exec_results) if e.phase != "P3"), None)
    extra = ""
    if second_opinion is not None:
        extra = (f"\nA primary auditor scored this {second_opinion.score}/100 ({second_opinion.verdict}). "
                 f"Its findings:\n{_render_findings(second_opinion.findings)}\n"
                 "You are an independent second opinion: verify against the evidence yourself.\n")
    return f"""You are the CoChem AUDITOR. Judge ONLY the physical evidence below (read from disk by the
orchestrator, not reported by the coder) against the acceptance criteria.

TASK (task_id {ctx.task_id}):
{ctx.prompt}

{_plan_brief(ctx.plan)}

TEST RESULTS (executed by the orchestrator; authoritative):
{_render_failures(report)[:_CAP_FAILURE_CHARS]}

{_test_file_block(ctx)}

EVIDENCE BUNDLE (files changed by this task; sha256 before the task vs now):
{render_files(ctx, ctx.evidence_files, with_hash=True)}

CODER'S OWN SUMMARY (a claim, not evidence):
{(last.summary if last else '(none)')[:3000]}
{extra}
RUBRIC
- verdict PASS only if every criterion is met by the files on disk, all tests pass, and there are
  no mocks, stubs, placeholders, hard-coded test answers or constraint violations.
- verdict SPOOFING_DETECTED if the evidence contradicts the claims (claimed work absent, tests
  gamed, outputs hard-coded to satisfy tests).
- otherwise FAIL. score 0-100 = degree of criteria satisfaction and code quality.
- findings: one entry per concrete problem; severity CRITICAL|HIGH|MEDIUM|LOW.

Return ONLY one JSON object:
{{"verdict": "PASS|FAIL|SPOOFING_DETECTED", "score": 0, "critique": "...",
  "findings": [{{"severity": "HIGH", "file": "path", "line": 1, "issue": "...", "criteria_id": "AC1"}}]}}"""


async def _call_auditor(ctx: TaskContext, agent: str,
                        prompt: str) -> tuple[AuditResult, Optional[tuple], bool]:
    """Returns (audit, (provider, model), asymmetry_ok). Fails closed to INDETERMINATE.

    Asymmetry: the resolved audit model must not be any model that wrote code for this
    task (SRS never-self-verify). On collision, re-run once (the router's primary may have
    recovered from quota); a second collision voids the audit.
    """
    for attempt in (1, 2):
        temp_disabled = set()
        if _LLM_ROUTER_AVAILABLE and _router is not None and ctx.producer_models:
            entry = _router._entry(agent)
            chain_models = {(entry["provider"], entry["model"])} | {
                (p, m) for p, m in entry.get("fallbacks", []) if p not in ("halt", "ollama")
            }
            if chain_models - ctx.producer_models:
                for p_triple in ctx.producer_models:
                    if p_triple not in _router._disabled:
                        _router._disabled.add(p_triple)
                        _router._disabled_at[p_triple] = time.monotonic()
                        temp_disabled.add(p_triple)
        try:
            audit, meta = await safe_chat_cli(agent, prompt, pydantic_model=AuditResult, return_meta=True,
                                              exclude=ctx.producer_models)
            ctx.record_call(meta)
        except StructuredOutputError as exc:
            return AuditResult.indeterminate(f"{agent} output malformed: {exc}"), None, False
        finally:
            if _LLM_ROUTER_AVAILABLE and _router is not None and temp_disabled:
                for p_triple in temp_disabled:
                    _router._disabled.discard(p_triple)
                    _router._disabled_at.pop(p_triple, None)
        if meta.triple not in ctx.producer_models:
            return audit, meta.triple, True
        ctx.log(f"{agent} resolved to producer model {meta.triple} (attempt {attempt})", logging.WARNING)
    return (AuditResult.indeterminate(f"audit asymmetry violated: {meta.triple} also produced code"),
            meta.triple, False)


async def phase_audit(ctx: TaskContext, phase: str) -> AuditResult:
    """P6 / P10 audit — cochem-audit on the disk evidence bundle; evidence gate first."""
    report = ctx.test_report or TestReport()
    if ctx.mutating and not ctx.evidence_files and not ctx.producer_models:
        audit = AuditResult(verdict="FAIL", score=0,
                            critique="Evidence gate: no files changed on disk; auditor not called.",
                            findings=[Finding(severity="CRITICAL", issue="no evidence files on disk")])
        ctx.audit_model, ctx.asymmetry_ok = None, False
    else:
        audit, ctx.audit_model, ctx.asymmetry_ok = await _call_auditor(
            ctx, "cochem-audit", _audit_prompt(ctx, report))
    audit.findings.extend(ctx.pending_findings)
    ctx.pending_findings = []
    if audit.verdict == "PASS" and blocking_count(audit.findings):
        audit.verdict = "FAIL"   # orchestrator-detected blockers override a PASS
    ctx.audit = audit
    ctx.save_artifact(f"audit_{phase}_i{ctx.iteration}.json", audit.model_dump_json(indent=2))
    ctx.log(f"{phase} audit: {audit.verdict} score={audit.score} findings={len(audit.findings)} "
            f"model={ctx.audit_model} asymmetry_ok={ctx.asymmetry_ok}")
    if not ctx.asymmetry_ok and (ctx.audit_model in ctx.producer_models or "asymmetry" in (audit.critique or "").lower()):
        ctx.decision = GateDecision(
            DECISION_QUARANTINE_CANDIDATE,
            f"audit asymmetry collision: {ctx.audit_model} matches producer model",
        )
    ctx.save()
    return audit


def record_progress(ctx: TaskContext, phase: str) -> ProgressRecord:
    report, audit = ctx.test_report or TestReport(), ctx.audit
    findings = audit.findings if audit else []
    rec = ProgressRecord(
        iteration=ctx.iteration, phase=phase, pass_rate=report.pass_rate,
        audit_score=audit.score if audit else 0,
        verdict=audit.verdict if audit else "INDETERMINATE",
        weighted_findings=weighted_findings(findings), blocking_findings=blocking_count(findings),
        diff_lines=ctx.round_diff_lines, signature_hash=report.signature_hash,
        evidence_files=len(ctx.evidence_files), asymmetry_ok=ctx.asymmetry_ok,
        has_tracebacks=report.has_tracebacks)
    ctx.progress.append(rec)
    ctx.save()
    return rec


def _refresh_record(ctx: TaskContext) -> None:
    """Re-sync the latest ProgressRecord after an escalation/adjudication changed the audit."""
    rec = ctx.progress[-1]
    findings = ctx.audit.findings if ctx.audit else []
    rec.audit_score = ctx.audit.score if ctx.audit else 0
    rec.verdict = ctx.audit.verdict if ctx.audit else "INDETERMINATE"
    rec.weighted_findings = weighted_findings(findings)
    rec.blocking_findings = blocking_count(findings)
    rec.asymmetry_ok = ctx.asymmetry_ok


async def phase_escalation_audit(ctx: TaskContext) -> None:
    """Rule 6 — cochem-audit-escalation second opinion, once per task."""
    primary = ctx.audit
    prompt = _audit_prompt(ctx, ctx.test_report or TestReport(), second_opinion=primary)
    second, triple, asym = await _call_auditor(ctx, "cochem-audit-escalation", prompt)
    ctx.escalation_used = True
    ctx.save_artifact(f"audit_escalation_i{ctx.iteration}.json", second.model_dump_json(indent=2))
    accepted = (asym and second.verdict == "PASS" and second.score >= V2_AUDIT_PASS_THRESHOLD
                and blocking_count(second.findings) == 0)
    if accepted:
        ctx.audit, ctx.audit_model, ctx.asymmetry_ok = second, triple, True
    else:
        seen = {(f.file, f.issue) for f in primary.findings}
        primary.findings.extend(f for f in second.findings if (f.file, f.issue) not in seen)
    ctx.log(f"escalation audit via {triple}: {second.verdict} score={second.score} "
            f"asymmetry_ok={asym} -> {'ACCEPT' if accepted else 'CONTINUE with union of findings'}")
    _refresh_record(ctx)
    ctx.save()


async def phase_adjudicate(ctx: TaskContext, reason: str) -> bool:
    """council-adjudicator confirms/denies QUARANTINE. Malformed output -> NOT confirmed
    (quarantine is irreversible) but a CRITICAL finding still blocks ACCEPT."""
    ctx.adjudication_used = True
    records = json.dumps([dataclasses.asdict(e) for e in ctx.exec_results[-4:]])[:6000]
    prompt = f"""You are the COUNCIL ADJUDICATOR. Quarantine is terminal and removes this task from the
pipeline pending human review. Confirm ONLY if the evidence shows deliberate spoofing (claimed work
absent, gamed tests, fabricated results). A coder that merely failed, or ignored the output
protocol, is NOT spoofing.

TRIGGER: {reason}
TASK: {ctx.prompt}
AUDIT: {ctx.audit.model_dump_json() if ctx.audit else '(none)'}
EXECUTION RECORDS (computed from disk by the orchestrator): {records}
EVIDENCE:
{render_files(ctx, ctx.evidence_files, cap_total=30_000, with_hash=True)}

Return ONLY one JSON object: {{"confirmed": true, "rationale": "..."}}"""
    try:
        verdict, meta = await safe_chat_cli("council-adjudicator", prompt, pydantic_model=AdjudicationResult, return_meta=True)
        ctx.record_call(meta)
    except StructuredOutputError as exc:
        verdict = AdjudicationResult(confirmed=False, rationale=f"adjudicator output malformed: {exc}")
    ctx.save_artifact("adjudication.json", verdict.model_dump_json(indent=2))
    ctx.log(f"adjudication: confirmed={verdict.confirmed} — {verdict.rationale[:200]}")
    if not verdict.confirmed and ctx.audit is not None:
        ctx.audit.findings.append(Finding(
            severity="CRITICAL", issue=f"Quarantine not confirmed ({reason}): {verdict.rationale[:500]}"))
        if ctx.audit.verdict == "SPOOFING_DETECTED":
            ctx.audit.verdict = "FAIL"
        if ctx.progress:
            _refresh_record(ctx)
    ctx.save()
    return verdict.confirmed


async def phase_improve(ctx: TaskContext) -> None:
    """P7 IMPROVE — cochem-improve-code on MEDIUM/LOW findings; skipped when nothing qualifies."""
    report, audit = ctx.test_report or TestReport(), ctx.audit
    if audit is None:
        return
    if report.pass_rate >= 1.0 and audit.score >= V2_AUDIT_PASS_THRESHOLD:
        ctx.log("P7 skipped (pass_rate=1.0 and score >= threshold)")
        return
    minor = [f for f in audit.findings if f.severity in ("MEDIUM", "LOW")]
    if not minor:
        ctx.log("P7 skipped (no MEDIUM/LOW findings)")
        return
    prompt = f"""You are P7 IMPROVE. Make targeted quality fixes for ONLY these audit findings;
do not change behaviour the tests rely on.

FINDINGS TO ADDRESS:
{_render_findings(minor)}

{_plan_brief(ctx.plan)}

{_test_file_block(ctx)}

CURRENT FILES:
{render_files(ctx, ctx.evidence_files | _target_paths(ctx))}

{_COCHEM_CONSTRAINTS}

{_artifact_protocol(ctx, [ctx.test_rel])}"""
    result = await _run_code_phase(ctx, "P7", "cochem-improve-code", prompt)
    ctx.round_diff_lines += result.diff_lines


async def phase_plan_next(ctx: TaskContext, previous: GateDecision) -> None:
    """P8 PLAN-NEXT — deterministic next_work.md; cochem-summarizer only above the size cap."""
    report, audit = ctx.test_report or TestReport(), ctx.audit

    def _is_error(tid: str) -> bool:
        frame = report.top_frames.get(tid, "")
        return not frame.endswith(("AssertionError", "Failed"))

    ordered = sorted(report.failing_ids, key=lambda t: 0 if _is_error(t) else 1)
    lines = [f"# Next work — task {ctx.task_id}, iteration {ctx.iteration}",
             f"Previous gate decision: {previous.decision} ({previous.reason})",
             f"Tests: {report.summary()}", "", "## Failing tests (errors first)"]
    lines += [f"{n}. {tid} — {report.top_frames.get(tid, '')}" for n, tid in enumerate(ordered, 1)] or ["(none)"]
    lines += ["", "## Open audit findings", _render_findings(audit.findings if audit else [])]
    text = "\n".join(lines)
    if len(text) > _CAP_NEXT_WORK_CHARS:
        summary, meta = await safe_chat_cli(
            "cochem-summarizer",
            "Compress this work list into an ordered, de-duplicated list of at most 25 concrete fixes. "
            "Keep test ids, file paths and severities verbatim.\n\n" + text[:120_000],
            return_meta=True)
        ctx.record_call(meta)
        text = (summary or "").strip() or text[:_CAP_NEXT_WORK_CHARS]
    ctx.next_work = text
    ctx.save_artifact(f"next_work_i{ctx.iteration}.md", text)
    ctx.save()


async def phase_debug(ctx: TaskContext) -> None:
    """cochem-debug interrupt (rule 7) — traceback triage appended to the P9 prompt."""
    prompt = f"""You are the DEBUGGER. Tests raised real tracebacks (not only assertion failures).
Localise each fault to file and line and propose the minimal fix. Do not rewrite whole files;
answer in plain text, one section per failing test.

{_render_failures(ctx.test_report)[:_CAP_FAILURE_CHARS]}

{_test_file_block(ctx)}

CURRENT FILES:
{render_files(ctx, ctx.evidence_files | _target_paths(ctx), cap_total=60_000)}"""
    text, meta = await safe_chat_cli("cochem-debug", prompt, return_meta=True)
    ctx.record_call(meta)
    ctx.save_response("DEBUG", text)
    ctx.debug_notes.append(f"[iteration {ctx.iteration}]\n{(text or '').strip()[:8000]}")
    ctx.log(f"debug interrupt: {len(text or '')} chars of triage")
    ctx.save()


async def _failures_for_prompt(ctx: TaskContext) -> str:
    """Failing output for prompts; cochem-tester compresses it only when over the cap."""
    text = _render_failures(ctx.test_report)
    if len(text) <= _CAP_FAILURE_CHARS:
        return text
    compressed, meta = await safe_chat_cli(
        "cochem-tester",
        "Compress this pytest failure output. For each failing test keep: test id, exception type, "
        "innermost frame (file:line), and the assertion/error message. Drop everything else.\n\n"
        + text[:200_000],
        return_meta=True)
    ctx.record_call(meta)
    return (compressed or "").strip() or text[:_CAP_FAILURE_CHARS]


async def phase_refine(ctx: TaskContext, previous: GateDecision) -> None:
    """P9 REFINE — cochem-coder-refine with FULL context (fixes design defect D3)."""
    report, audit = ctx.test_report or TestReport(), ctx.audit
    if report.total > 0 and report.pass_rate >= 1.0 and not (audit and audit.findings):
        ctx.log("P9 skipped (all tests pass, no open findings)")
        return
    tag = f"[iteration {ctx.iteration}]"
    debug = ctx.debug_notes[-1] if ctx.debug_notes and ctx.debug_notes[-1].startswith(tag) else "(none)"
    prompt = f"""You are P9 REFINE. Make the failing tests pass and resolve the open findings with
targeted changes. Keep everything that already works.

ORIGINAL TASK (task_id {ctx.task_id}):
{ctx.prompt}

{_plan_brief(ctx.plan)}

NEXT WORK INSTRUCTIONS:
{ctx.next_work or '(none)'}

DEBUGGER TRIAGE:
{debug}

FAILS FROM PREVIOUS RUN:
{await _failures_for_prompt(ctx)}

{_test_file_block(ctx)}

CURRENT EVIDENCE BUNDLE:
{render_files(ctx, ctx.evidence_files | _target_paths(ctx))}

{_COCHEM_CONSTRAINTS}

{_artifact_protocol(ctx, [ctx.test_rel])}"""
    result = await _run_code_phase(ctx, "P9", "cochem-coder-refine", prompt)
    ctx.round_diff_lines += result.diff_lines


# ── Round driver (Stage B + Stage C convergence loop) ────────────────────────

async def run_rounds(ctx: TaskContext) -> str:
    """Execute Stage B (iteration 0) then Stage C (iterations 1..N) until terminal gate."""
    # ── Stage B: BUILD (iteration 0)
    ctx.iteration = 0
    ctx.round_diff_lines = 0
    await phase_code(ctx)
    report = await phase_test(ctx, "P5")
    await phase_audit(ctx, "P6")
    rec = record_progress(ctx, "P6")
    ctx.log(f"Stage B complete: pass_rate={rec.pass_rate:.2f} score={rec.audit_score} "
            f"verdict={rec.verdict} diff_lines={rec.diff_lines}")

    # ── Stage C: CONVERGE (iterations 1..N)
    while True:
        ctx.decision = progress_gate(
            ctx.progress,
            mutating=ctx.mutating,
            escalation_used=ctx.escalation_used,
            adjudication_used=ctx.adjudication_used,
        )
        ctx.log(f"progress gate -> {ctx.decision.decision}: {ctx.decision.reason}")
        ctx.save()

        d = ctx.decision.decision
        if d == DECISION_ACCEPT:
            return "ACCEPTED"
        if d == DECISION_PIVOT:
            return "PIVOT"
        if d == DECISION_ESCALATE:
            await phase_escalation_audit(ctx)
            continue
        if d == DECISION_QUARANTINE_CANDIDATE:
            confirmed = await phase_adjudicate(ctx, ctx.decision.reason)
            return "QUARANTINED" if confirmed else "PIVOT"

        # d == DECISION_CONTINUE
        ctx.iteration += 1
        ctx.round_diff_lines = 0
        ctx.log(f"starting convergence iteration {ctx.iteration}/{V2_MAX_AUDIT_CYCLES}")

        await phase_improve(ctx)
        await phase_plan_next(ctx, ctx.decision)
        if ctx.decision.debug:
            await phase_debug(ctx)
        await phase_refine(ctx, ctx.decision)
        await phase_test(ctx, "P10")
        await phase_audit(ctx, "P10")
        rec = record_progress(ctx, "P10")
        ctx.log(f"iteration {ctx.iteration} complete: pass_rate={rec.pass_rate:.2f} "
                f"score={rec.audit_score} verdict={rec.verdict} diff_lines={rec.diff_lines}")


# ── Finalize + Kanban update ──────────────────────────────────────────────────

def _render_dossier(ctx: TaskContext) -> str:
    """Format the final run dossier (design §4)."""
    rows = []
    for r in ctx.progress:
        rows.append(f"| {r.iteration} | {r.phase} | {r.pass_rate:.2f} | {r.audit_score} | "
                    f"{r.verdict} | {r.weighted_findings} ({r.blocking_findings}) | "
                    f"{r.diff_lines} | {r.evidence_files} | {r.asymmetry_ok} | {r.stalled} | "
                    f"{r.decision or '-'} |")
    table = ("| iter | phase | pass_rate | score | verdict | weighted (block) | diff | "
             "evidence | asym_ok | stall | decision |\n"
             "|---|---|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows))
    return f"""# TDD Run Dossier — {ctx.task_id}
Outcome: **{ctx.outcome}** ({ctx.outcome_reason})
Agent: `{ctx.agent_name}` · Workspace: `{ctx.workspace}` · Mutating: `{ctx.mutating}`
Timestamp: {datetime.now(timezone.utc).isoformat()}

## Progress
{table}

## Plan
```json
{json.dumps(ctx.plan.model_dump() if ctx.plan else {}, indent=2)}
```

## Final Audit
```json
{json.dumps(ctx.audit.model_dump() if ctx.audit else {}, indent=2)}
```
"""


async def finalize(ctx: TaskContext, outcome: str) -> str:
    ctx.outcome = outcome
    ctx.outcome_reason = ctx.decision.reason if ctx.decision else "run completed"
    ctx.save_artifact("dossier.md", _render_dossier(ctx))
    ctx.save()
    ctx.log(f"final outcome: {outcome} ({ctx.outcome_reason})")

    # Kanban state sync via existing CLI helpers
    task_file = Path(ctx.task_file)
    if outcome == "ACCEPTED":
        # Leave artifact in place; user/orchestrator moves task to completed
        return outcome
    if outcome == "QUARANTINED":
        # Mark quarantined in Kanban if cochem_kanban is available
        with contextlib.suppress(Exception):
            subprocess.run([_python_for_subprocess(), KANBAN_SCRIPT, "quarantine",
                            "--task-id", ctx.task_id, "--reason", ctx.outcome_reason],
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        return outcome
    if outcome == "PIVOT":
        # Trigger pivot council (P11)
        tail = ctx.test_report.output_tail if ctx.test_report else ctx.outcome_reason
        await trigger_pivot_council(str(task_file), tail, ctx.task_id)
        return outcome
    return outcome


# ── Entry point ───────────────────────────────────────────────────────────────

async def process_task(
    task_file: str,
    raw_task: Optional[dict] = None,
    state: Optional[dict] = None,
    **kwargs: Any,
) -> Any:
    """Top-level TDD pipeline entry point for a single task."""
    p = Path(task_file)
    if not p.is_file():
        raise FileNotFoundError(f"task file does not exist: {task_file}")
    raw = raw_task if raw_task is not None and len(raw_task) > 0 else json.loads(p.read_text(encoding="utf-8"))
    ctx = TaskContext.create_or_resume(str(p), raw)
    if ctx.outcome in (OUTCOME_ACCEPTED, OUTCOME_PIVOTED, OUTCOME_QUARANTINED, OUTCOME_HARD_ABORT):
        return True
    ctx.log(f"loaded task '{ctx.task_id}', prompt_sha={ctx.prompt_sha[:10]}")
    try:
        await run_setup(ctx)
        outcome = await run_rounds(ctx)
        return await finalize(ctx, outcome)
    except PhaseFailure as exc:
        ctx.log(f"PhaseFailure: {exc} -> routing to PIVOT", logging.ERROR)
        ctx.decision = GateDecision(DECISION_PIVOT, str(exc))
        return await finalize(ctx, "PIVOT")


# ── Daemon batch execution with hardware concurrency ─────────────────────────

async def _hardware_monitor_loop():
    """Background task that periodically polls system resources and adjusts workers."""
    while True:
        await asyncio.sleep(_HW_POLL_INTERVAL)
        try:
            _refresh_worker_semaphore()
        except Exception as e:
            logger.debug(f"[HW] monitor loop poll error: {e}")


async def _dropzone_poller(
    dropzone: Path | str,
    queue: asyncio.Queue[Optional[str]],
    poll_interval: float = 10,
    known_tasks: Optional[set[str]] = None,
) -> None:
    """Discovers task files in dropzone continuously and enqueues them."""
    if known_tasks is None:
        known_tasks = set()
    dz = Path(dropzone)
    while True:
        try:
            if dz.is_dir():
                for item in sorted(dz.iterdir()):
                    if not item.is_file():
                        continue
                    name = item.name.lower()
                    if not name.endswith(".json"):
                        continue
                    if name == "work_loop_state.json" or name.endswith("_state.json") or name == "state.json":
                        continue
                    item_str = str(item)
                    if item_str not in known_tasks:
                        logger.info(f"[POLLER] Discovered new task: {item.name}")
                        known_tasks.add(item_str)
                        await queue.put(item_str)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[POLLER] Scan error: {e}")
        await asyncio.sleep(poll_interval)


async def _worker_loop(
    worker_id: int,
    queue: asyncio.Queue[Optional[str]],
    results: Optional[list] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    state: Optional[dict] = None,
) -> None:
    """Worker coroutine that processes tasks from queue, gated by hardware semaphore."""
    global _active_worker_count
    while True:
        task_file = await queue.get()
        if task_file is None:
            queue.task_done()
            break

        # Acquire a hardware concurrency slot
        sem = semaphore if semaphore is not None else (_worker_semaphore or asyncio.Semaphore(_HW_FLOOR))
        try:
            async with sem:
                _active_worker_count += 1
                t0 = time.monotonic()
                logger.info(f"[Worker {worker_id}] Starting task: {Path(task_file).name} "
                            f"(active={_active_worker_count}/{_current_worker_limit})")
                try:
                    outcome = await process_task(task_file, state=state)
                    elapsed = time.monotonic() - t0
                    logger.info(f"[Worker {worker_id}] Finished {Path(task_file).name} -> {outcome} ({elapsed:.1f}s)")
                    if results is not None:
                        results.append((task_file, outcome, elapsed))
                except Exception as e:
                    elapsed = time.monotonic() - t0
                    logger.error(f"[Worker {worker_id}] Task {Path(task_file).name} crashed ({elapsed:.1f}s): {e}",
                                 exc_info=True)
                    if results is not None:
                        results.append((task_file, "ERROR", elapsed))
                finally:
                    _active_worker_count = max(0, _active_worker_count - 1)
        finally:
            queue.task_done()


async def daemon_loop(
    watch_dir: Path,
    poll_interval: int = 10,
    max_workers: Optional[int] = None,
) -> None:
    _gov.install_job_memory_cap()
    num_workers = max_workers if max_workers is not None else _compute_max_workers()
    logger.info(f"[DAEMON] Watching {watch_dir} (poll={poll_interval}s, workers={num_workers})")

    _refresh_worker_semaphore()
    sem = asyncio.Semaphore(max_workers) if max_workers is not None else None
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    results: list = []
    known_tasks: set[str] = set()

    hw_task = asyncio.create_task(_hardware_monitor_loop())
    workers = [
        asyncio.create_task(_worker_loop(i + 1, queue, results=results, semaphore=sem))
        for i in range(num_workers)
    ]
    poller_task = asyncio.create_task(
        _dropzone_poller(watch_dir, queue, poll_interval=poll_interval, known_tasks=known_tasks)
    )

    try:
        await asyncio.gather(poller_task, hw_task, *workers)
    except asyncio.CancelledError:
        logger.info("[DAEMON] Received cancellation; shutting down.")
    finally:
        poller_task.cancel()
        hw_task.cancel()
        for w in workers:
            w.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(poller_task, hw_task, *workers, return_exceptions=True)


def main(argv: Optional[list[str]] = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="CoChem v2 Perpetual Kanban Daemon / Task Runner")
    parser.add_argument("task_file", nargs="?", default=None, help="Path to a single task_*.json file to run")
    parser.add_argument("--dropzone", default=r"D:\__CoChem\__agentic\.scripts\prompts",
                        help="Dropzone directory to watch in daemon mode")
    parser.add_argument("--poll", type=int, default=10, help="Seconds between dropzone polls")
    parser.add_argument("--max-workers", type=int, default=None, help="Bound worker pool size")
    args = parser.parse_args(argv)

    if args.task_file:
        asyncio.run(process_task(args.task_file))
    else:
        asyncio.run(daemon_loop(Path(args.dropzone), poll_interval=args.poll, max_workers=args.max_workers))


if __name__ == "__main__":
    main()
