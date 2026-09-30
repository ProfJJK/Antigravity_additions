"""SRS-412-05 Research-Driven TDD Pivot: Research Stage.

Implements the 3-strike code-generation freeze gate (SRS-412-05-FR-003),
multi-database scientific research dossier assembly (SRS-412-05-FR-004),
Claude Fable root cause triage classifier (SRS-412-05-FR-005),
contract ambiguity human review escalation (SRS-412-05 Section 8),
MAX_META_PIVOT hard abort trigger (SRS-412-05-FR-006), and
Physics_Autopsy_Report.md report generation (SRS-412-05 Section 8).

This is a general software engineering pipeline module:
NO mocked variables, NO synthetic stubs, NO PySCF, NO ASE, NO Mendeleev.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Sequence

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.tdd_pivot.research")

# Resilient imports for standalone staging and package usage
try:
    from cochem.tdd_pivot.state_machine import (
        TddCycleState,
        MAX_META_PIVOTS,
        bound_diagnostic_prompt,
    )
except ImportError:
    try:
        from .state_machine import (
            TddCycleState,
            MAX_META_PIVOTS,
            bound_diagnostic_prompt,
        )
    except ImportError:
        from state_machine import (  # type: ignore
            TddCycleState,
            MAX_META_PIVOTS,
            bound_diagnostic_prompt,
        )

try:
    from cochem.tdd_pivot.literature import (
        LiteratureBackend,
        LiteratureBackendError,
        LiteratureHit,
    )
except ImportError:
    try:
        from .literature import (
            LiteratureBackend,
            LiteratureBackendError,
            LiteratureHit,
        )
    except ImportError:
        from literature import (  # type: ignore
            LiteratureBackend,
            LiteratureBackendError,
            LiteratureHit,
        )


# -----------------------------------------------------------------------------
# [MC-TDD-10] SRS-412-05-FR-003: 3-Strike Code-Generation Freeze
# -----------------------------------------------------------------------------

STRIKE_LIMIT: int = 3
TRIAGE_MODEL: str = "claude-fable-5-1"
TRIAGE_TIMEOUT_SEC: float = 300.0
ROOT_CAUSE_CATEGORIES: tuple[str, ...] = (
    "SCOPE_UNDERESTIMATION",
    "CONTRACT_AMBIGUITY",
    "LIBRARY_DEFICIENCY",
)


class CodeGenerationFrozen(RuntimeError):
    """Raised when code generation is attempted while the Mandatory Literature Research Stage is active."""


def should_freeze_code_generation(state: TddCycleState) -> bool:
    """True once the audit has failed STRIKE_LIMIT (3) or more cycles in a row (SRS-412-05-FR-003)."""
    return state.consecutive_failures >= STRIKE_LIMIT


def three_strike_freeze_gate(state: TddCycleState) -> bool:
    """freeze_gate for run_tdd_state_machine: on the third strike trigger the research stage."""
    if not should_freeze_code_generation(state):
        return False
    state.research_stage_triggered = True
    return True


def assert_code_generation_allowed(state: TddCycleState) -> None:
    """Raise CodeGenerationFrozen while the research stage is triggered; repair steps call this first."""
    if state.research_stage_triggered:
        raise CodeGenerationFrozen(
            f"code generation frozen after {state.consecutive_failures} consecutive "
            f"failures (cycle {state.cycle_number}): literature research stage required"
        )


# -----------------------------------------------------------------------------
# [MC-TDD-11] SRS-412-05-FR-004: Multi-Database Research Dossier Assembly
# -----------------------------------------------------------------------------

DOSSIER_LIMIT: int = 5
LITERATURE_COUNT_RE: re.Pattern[str] = re.compile(r"^Literature hits: (\d+)$", re.M)


class DossierError(RuntimeError):
    """No Research Dossier: the research stage is not triggered, the backends are unusable or all failed."""


@dataclass
class BackendFailure:
    backend: str
    error: str


@dataclass
class ResearchDossier:
    task_id: str
    query: str
    path: str
    backends: list[str]
    hits: list[LiteratureHit]
    failures: list[BackendFailure]

    @property
    def literature_count(self) -> int:
        return len(self.hits)


def render_dossier(d: ResearchDossier, failure_history: Sequence[str]) -> str:
    """Render Markdown dossier; failed backends get their own visible section, never hidden."""
    lines = [
        f"# Research Dossier: {d.task_id}",
        "",
        f"Query: {d.query}",
        f"Backends queried: {', '.join(d.backends)}",
        f"Literature hits: {d.literature_count}",
        "",
        "## Literature",
        "",
    ]
    if d.hits:
        lines += [f"- [{h.source}] {h.identifier}: {h.title} <{h.url}>" for h in d.hits]
    else:
        lines.append("No literature found.")
    lines += ["", "## Failed backends", ""]
    if d.failures:
        lines += [f"- {f.backend}: {f.error}" for f in d.failures]
    else:
        lines.append("None.")
    last = bound_diagnostic_prompt(failure_history[-1]) if failure_history else "No failure recorded."
    lines += ["", "## Latest failure", "", "```text", last.rstrip("\n"), "```"]
    return "\n".join(lines) + "\n"


def assemble_research_dossier(
    state: TddCycleState,
    task_id: str,
    query: str,
    failure_history: Sequence[str],
    backends: Sequence[LiteratureBackend],
    dossier_path: str | Path,
    limit: int = DOSSIER_LIMIT,
) -> ResearchDossier:
    """Query every backend, write the Markdown dossier atomically to dossier_path, set state.dossier_path."""
    if not state.research_stage_triggered:
        raise DossierError(f"{task_id}: the literature research stage has not been triggered (FR-003)")
    names = [b.name for b in backends]
    if not names or len(set(names)) != len(names):
        raise DossierError(f"{task_id}: backends must be non-empty with unique names, got {names}")
    hits: list[LiteratureHit] = []
    failures: list[BackendFailure] = []
    for backend in backends:
        try:
            hits += backend.search(query, limit)[:limit]
        except LiteratureBackendError as exc:
            failures.append(BackendFailure(backend.name, str(exc).removeprefix(f"{backend.name}: ")))
    if len(failures) == len(names):
        raise DossierError(f"{task_id}: every backend failed: " + "; ".join(f"{f.backend}: {f.error}" for f in failures))
    path = Path(dossier_path)
    dossier = ResearchDossier(task_id, query, str(path), names, hits, failures)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(render_dossier(dossier, failure_history), encoding="utf-8")
    tmp_path.replace(path)
    state.dossier_path = str(path)
    return dossier


# -----------------------------------------------------------------------------
# [MC-TDD-12] SRS-412-05-FR-005: Root Cause Triage Classifier (Claude Fable 5.1)
# -----------------------------------------------------------------------------


class TriageError(RuntimeError):
    """Root Cause Triage produced no valid classification; a category is never guessed."""


@dataclass
class RootCauseTriage:
    category: str
    rationale: str
    model: str


def failure_sections(failure_history: Sequence[str]) -> str:
    """Numbered failure sections, each bounded by bound_diagnostic_prompt so no prompt grows without limit."""
    return "\n\n".join(
        f"--- Failure {i} ---\n{bound_diagnostic_prompt(t)}"
        for i, t in enumerate(failure_history, 1)
    )


def build_triage_prompt(failure_history: Sequence[str], dossier_text: str) -> str:
    """FR-005 prompt: the three categories, the JSON answer shape, bounded failures and bounded dossier."""
    return (
        f"Root Cause Triage. Classify the persistent failure as exactly one of: {', '.join(ROOT_CAUSE_CATEGORIES)}.\n"
        'Answer with one JSON object {"category": "...", "rationale": "..."}.\n\n'
        f"Failure history:\n{failure_sections(failure_history)}\n\n"
        f"Research dossier:\n{bound_diagnostic_prompt(dossier_text)}\n"
    )


def triage_root_cause(
    failure_history: Sequence[str],
    dossier_text: str,
    triage_model: Callable[[str], str],
    model: str = TRIAGE_MODEL,
) -> RootCauseTriage:
    """Ask the triage model and accept only the first JSON object naming a known category."""
    try:
        answer = triage_model(build_triage_prompt(failure_history, dossier_text))
    except Exception as exc:
        raise TriageError(f"triage model raised {type(exc).__name__}: {exc}") from exc
    try:
        data = json.JSONDecoder().raw_decode(answer, answer.index("{"))[0]
    except (AttributeError, TypeError, ValueError) as exc:
        raise TriageError(f"triage answer holds no JSON object: {str(answer)[:200]!r}") from exc
    category, rationale = data.get("category"), data.get("rationale")
    if category not in ROOT_CAUSE_CATEGORIES or not isinstance(rationale, str) or not rationale.strip():
        raise TriageError(f"triage rejected: category={category!r} not allowed or rationale={rationale!r} empty")
    return RootCauseTriage(category=category, rationale=rationale.strip(), model=model)


def fable_cli_triage_model(
    claude_exe: str | Path,
    model: str = TRIAGE_MODEL,
    timeout: float = TRIAGE_TIMEOUT_SEC,
) -> Callable[[str], str]:
    """Callable running `claude -p --model <model>` with the prompt on stdin (no shell, no command-line limit)."""
    def run(prompt: str) -> str:
        try:
            proc = subprocess.run(
                [str(claude_exe), "-p", "--model", model],
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise TriageError(f"triage CLI failed: {type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            raise TriageError(f"triage CLI exited {proc.returncode}: {proc.stderr[-500:]}")
        return proc.stdout
    return run


# -----------------------------------------------------------------------------
# [MC-TDD-13] FAILURE: Exhausted Research Stage (SRS-412-05 Section 8)
# -----------------------------------------------------------------------------

HUMAN_REVIEW_STATUS: str = "AWAITING_HUMAN_REVIEW"


@dataclass
class HumanReviewFlag:
    task_id: str
    category: str
    status: str
    question: str
    failure_count: int
    dossier_path: str | None
    model: str
    flag_path: str


class ReviewPathError(ValueError):
    """The task id cannot name a review file inside review_dir (traversal, separators)."""


def research_stage_exhausted(state: TddCycleState, literature_count: int | None = None) -> bool:
    """Determine whether the literature research stage found no alternative literature."""
    text = (
        Path(p).read_text("utf-8", "replace")
        if (p := state.dossier_path) and Path(p).is_file()
        else ""
    )
    if literature_count is None and text.strip() and not (m := LITERATURE_COUNT_RE.search(text)):
        raise DossierError(f"dossier {p} has no 'Literature hits: N' line to derive literature_count from")
    count = int(m.group(1)) if literature_count is None and text.strip() else literature_count
    if count is not None and count < 0:
        raise ValueError(f"literature_count must be >= 0, got {count}")
    return state.research_stage_triggered and (not count or not text.strip())


def build_ambiguity_prompt(task_id: str, failure_history: Sequence[str]) -> str:
    """Build prompt asking Fable 5.1 to formulate the ambiguity question for human review."""
    return (
        f"Contract ambiguity, task {task_id}: the research stage found no alternative literature.\n"
        "State the ambiguous contract clause and what the human must decide.\n"
        f"Failure history:\n{failure_sections(failure_history)}\n"
    )


def review_flag_path(task_id: str, review_dir: str | Path) -> Path:
    """Path to the HUMAN_REVIEW JSON file directly inside review_dir."""
    base = Path(review_dir).resolve()
    name = f"HUMAN_REVIEW_{task_id}.json"
    if (
        not (isinstance(task_id, str) and re.fullmatch(r"[A-Za-z0-9._-]+", task_id))
        or ".." in task_id
        or (base / name).resolve().parent != base
    ):
        raise ReviewPathError(f"unsafe task_id {task_id!r}: the HUMAN_REVIEW file must stay inside {base}")
    return Path(review_dir) / name


def flag_contract_ambiguity(
    task_id: str,
    state: TddCycleState,
    failure_history: Sequence[str],
    literature_count: int | None,
    review_dir: str | Path,
    triage_model: Callable[[str], str],
    model: str = TRIAGE_MODEL,
) -> HumanReviewFlag:
    """Flag contract ambiguity for human user review (SRS-412-05 Section 8)."""
    target = review_flag_path(task_id, review_dir)
    if not failure_history or not research_stage_exhausted(state, literature_count):
        raise ValueError(f"{task_id}: a human review flag needs an exhausted research stage and failures")
    try:
        raw_answer = triage_model(build_ambiguity_prompt(task_id, failure_history))
        if not isinstance(raw_answer, str):
            raise TypeError(f"expected str from ambiguity model, got {type(raw_answer).__name__}")
        question = raw_answer.strip()
    except Exception as exc:
        raise TriageError(f"ambiguity model gave no question: {type(exc).__name__}: {exc}") from exc
    if not question:
        raise TriageError("ambiguity model returned an empty question")
    flag = HumanReviewFlag(
        task_id=task_id,
        category=ROOT_CAUSE_CATEGORIES[1],  # "CONTRACT_AMBIGUITY"
        status=HUMAN_REVIEW_STATUS,          # "AWAITING_HUMAN_REVIEW"
        question=question,
        failure_count=len(failure_history),
        dossier_path=state.dossier_path,
        model=model,
        flag_path=str(target),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + ".tmp")
    flag_dict = {
        "human_review_flag": {
            "task_id": flag.task_id,
            "category": flag.category,
            "status": flag.status,
            "question": flag.question,
            "failure_count": flag.failure_count,
            "dossier_path": flag.dossier_path,
            "model": flag.model,
            "flag_path": flag.flag_path,
        }
    }
    tmp_path.write_text(json.dumps(flag_dict, indent=2), encoding="utf-8")
    tmp_path.replace(target)
    return flag


# -----------------------------------------------------------------------------
# [MC-TDD-14] SRS-412-05-FR-006: MAX_META_PIVOT Counter and Hard Abort Trigger
# -----------------------------------------------------------------------------

MAX_META_PIVOT: int = 3
HARD_ABORT_MARKER: str = "[HARD_ABORT: PHYSICS WALL]"


@dataclass
class PivotOutcome:
    pivot_number: int
    method: str
    resolved: bool
    detail: str


class HardAbort(RuntimeError):
    """FR-006: max_meta_pivots methodological pivots failed; system halts with HARD_ABORT_MARKER."""

    def __init__(self, state: TddCycleState, failed_pivots: Sequence[PivotOutcome]) -> None:
        super().__init__(
            f"{HARD_ABORT_MARKER} meta_pivot_count={state.meta_pivot_count} reached "
            f"max_meta_pivots={state.max_meta_pivots}: {len(failed_pivots)} failed pivot(s)"
        )
        self.state = state
        self.failed_pivots = list(failed_pivots)
        self.report_path: Path | None = None


def should_hard_abort(state: TddCycleState) -> bool:
    """True once meta_pivot_count has reached run-time bound state.max_meta_pivots (SRS-412-05-FR-006)."""
    return state.meta_pivot_count >= state.max_meta_pivots


def attempt_meta_pivot(
    state: TddCycleState,
    method: str,
    pivot: Callable[[TddCycleState], tuple[bool, str]],
    failed_pivots: list[PivotOutcome],
) -> PivotOutcome:
    """Run one pivot; only a real failed verdict advances counter, and reaching the bound aborts."""
    if state.max_meta_pivots < 1 or state.meta_pivot_count < 0:
        raise ValueError(f"invalid pivot bounds: count={state.meta_pivot_count}, max={state.max_meta_pivots}")
    if should_hard_abort(state):
        raise HardAbort(state, failed_pivots)
    pivot_number = state.meta_pivot_count + 1
    verdict = pivot(state)
    if not (isinstance(verdict, tuple) and len(verdict) == 2 and isinstance(verdict[0], bool)):
        raise TypeError(f"pivot must return (bool, str), got {verdict!r}")
    outcome = PivotOutcome(pivot_number, method, verdict[0], str(verdict[1]))
    if not outcome.resolved:
        state.meta_pivot_count += 1
        failed_pivots.append(outcome)
        if should_hard_abort(state):
            raise HardAbort(state, failed_pivots)
    return outcome


# -----------------------------------------------------------------------------
# [MC-TDD-15] FAILURE: Physics Wall Hard Abort (SRS-412-05 Section 8)
# -----------------------------------------------------------------------------

AUTOPSY_REPORT_NAME: str = "Physics_Autopsy_Report.md"


def _autopsy_cell(text: object) -> str:
    """One Markdown line: line breaks collapse to ' / ' and '|' is escaped, keeping tables/lists intact."""
    return re.sub(r"\s*[\r\n]+\s*", " / ", str(text).strip()).replace("|", "\\|")


def render_autopsy_report(
    task_id: str,
    state: TddCycleState,
    history: Sequence[dict],
    failed_pivots: Sequence[PivotOutcome],
) -> str:
    """Markdown autopsy: abort marker, tdd_cycle_state JSON, cycle history, failed pivots and dossier path."""
    lines = [
        f"# Physics Autopsy Report: {_autopsy_cell(task_id)}",
        "",
        f"Status: {HARD_ABORT_MARKER}",
        "",
        "## TDD cycle state",
        "",
        "```json",
        state.to_json(),
        "```",
        "",
        "## Cycle history",
        "",
    ]
    if history:
        lines += ["| Cycle | Phase | Result |", "| --- | --- | --- |"]
        for entry in history:
            result = "-" if entry.get("passed") is None else ("PASS" if entry.get("passed") else "FAIL")
            lines.append(f"| {_autopsy_cell(entry.get('cycle', ''))} | {_autopsy_cell(entry.get('phase', ''))} | {result} |")
    else:
        lines.append("No cycle history recorded.")
    lines += ["", "## Failed pivots", ""]
    if failed_pivots:
        lines += [f"{p.pivot_number}. {_autopsy_cell(p.method)}: {_autopsy_cell(p.detail)}" for p in failed_pivots]
    else:
        lines.append("No failed pivots recorded.")
    dossier = _autopsy_cell(state.dossier_path) if state.dossier_path else "No dossier was written."
    lines += ["", "## Research dossier", "", dossier]
    return "\n".join(lines) + "\n"


def write_autopsy_report(
    report_dir: str | Path,
    task_id: str,
    state: TddCycleState,
    history: Sequence[dict],
    failed_pivots: Sequence[PivotOutcome],
) -> Path:
    """Write report_dir/Physics_Autopsy_Report.md as UTF-8, only for a state at the hard-abort bound."""
    if not should_hard_abort(state):
        raise ValueError(
            f"no autopsy before the physics wall: meta_pivot_count={state.meta_pivot_count} "
            f"< max_meta_pivots={state.max_meta_pivots}"
        )
    path = Path(report_dir) / AUTOPSY_REPORT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_autopsy_report(task_id, state, history, failed_pivots), encoding="utf-8", newline="\n")
    return path


def halt_with_autopsy(
    abort: HardAbort,
    task_id: str,
    history: Sequence[dict],
    report_dir: str | Path,
) -> None:
    """Write autopsy of a real HardAbort, store its path on the abort, then halt by raising that abort."""
    abort.report_path = write_autopsy_report(report_dir, task_id, abort.state, history, abort.failed_pivots)
    raise abort
