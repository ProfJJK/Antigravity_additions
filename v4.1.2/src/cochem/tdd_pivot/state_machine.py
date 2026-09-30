# [MC-TDD-05] DATA_MODEL:tdd_cycle_state
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Define the
# SPEC: tdd_cycle_state model from section 7 with to_json/from_json that round-trip the section 7
# SPEC: layout. Fields: cycle_number (e.g. 3); max_cycles (e.g. 10); consecutive_failures (e.g. 3);
# SPEC: research_stage_triggered (e.g. true); dossier_path (e.g. ".docs/research/DOSSIER_TASK_04.md");
# SPEC: meta_pivot_count (e.g. 1); max_meta_pivots (e.g. 3) The section 7 values are an example
# SPEC: snapshot, not defaults: default every counter to 0.
"""SRS-412-05 TDD pivot state machine; TddCycleState.to_json/from_json round-trip the section 7 layout."""
from __future__ import annotations
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
MAX_CYCLES: int = 10
MAX_META_PIVOTS: int = 3
CYCLE_TIMEOUT_SEC: float = 30.0
DIAGNOSTIC_TOKEN_LIMIT: int = 2000
CHARS_PER_TOKEN: float = 3.0
DIAGNOSTIC_MODEL: str = "gemini-3.8-flash-high"
PHASES: tuple[str, ...] = ("EXECUTE", "AUDIT", "REPAIR")
@dataclass
class TddCycleState:
    cycle_number: int = 0
    max_cycles: int = MAX_CYCLES
    consecutive_failures: int = 0
    research_stage_triggered: bool = False
    dossier_path: str | None = None
    meta_pivot_count: int = 0
    max_meta_pivots: int = MAX_META_PIVOTS
    def to_json(self) -> str:
        return json.dumps({"tdd_cycle_state": asdict(self)}, indent=2)
    @classmethod
    def from_json(cls, text: str) -> "TddCycleState":
        data = obj.get("tdd_cycle_state") if isinstance(obj := json.loads(text), dict) and len(obj) == 1 else None
        spec = {k: str(f.type).split(" | ") for k, f in cls.__dataclass_fields__.items()}
        if not isinstance(data, dict) or set(data) != set(spec) or any(
                ("None" if data[k] is None else type(data[k]).__name__) not in spec[k] for k in spec):
            raise ValueError(f"tdd_cycle_state needs exactly the fields and types {spec}, got: {text!r}")
        return cls(**data)
# [MC-TDD-06] SRS-412-05-FR-001
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-05-FR-001: Coding
# SPEC: tasks shall execute within a bounded 10-cycle state machine following the Execute -> Audit ->
# SPEC: Repair loop. Traceability test: test_scenario_2_poison_pill_quarantine_and_10_cycle_pivot.
@dataclass
class TddRunResult:
    """Outcome of one bounded Execute -> Audit -> Repair run (SRS-412-05-FR-001)."""
    status: str
    state: TddCycleState
    history: list[dict[str, Any]]
    diagnostics: list["DiagnosticAnalysis"] = field(default_factory=list)
def run_tdd_state_machine(execute: Callable[[TddCycleState], Any],
                          audit: Callable[[TddCycleState, Any], tuple[bool, str]],
                          repair: Callable[[TddCycleState, str], Any],
                          state: TddCycleState | None = None,
                          freeze_gate: Callable[[TddCycleState], bool] | None = None,
                          summarizer: Callable[[str], str] | None = None) -> TddRunResult:
    """Run the bounded loop until PASSED, EXHAUSTED or FROZEN_FOR_RESEARCH.

    The state changes only from the real ``audit`` verdict. A resumed state continues from its
    ``cycle_number``, and no more than ``state.max_cycles`` cycles are ever run in total. When
    ``freeze_gate`` returns True after a failure, code generation is frozen: REPAIR is not called.
    REPAIR gets build_repair_prompt output (below DIAGNOSTIC_TOKEN_LIMIT, NFR-TDD-02); with a ``summarizer``
    every failed cycle first gets a request_diagnostic_analysis (FR-002), kept in ``diagnostics``."""
    state = TddCycleState() if state is None else state
    if state.max_cycles < 1 or state.cycle_number < 0:
        raise ValueError(f"invalid cycle bounds: cycle_number={state.cycle_number}, max_cycles={state.max_cycles}")
    history: list[dict[str, Any]] = []
    diagnostics: list[DiagnosticAnalysis] = []
    while state.cycle_number < state.max_cycles:
        state.cycle_number += 1
        artifact = execute(state)
        history.append({"cycle": state.cycle_number, "phase": PHASES[0], "passed": None})
        verdict = audit(state, artifact)
        if not (isinstance(verdict, tuple) and len(verdict) == 2 and isinstance(verdict[0], bool)):
            raise TypeError(f"audit must return (bool, str), got {verdict!r}")
        passed, failure_output = verdict
        history.append({"cycle": state.cycle_number, "phase": PHASES[1], "passed": passed})
        if passed:
            state.consecutive_failures = 0
            return TddRunResult("PASSED", state, history, diagnostics)
        state.consecutive_failures += 1
        if summarizer is not None:  # a DiagnosticError propagates: no analysis is ever invented
            diagnostics.append(request_diagnostic_analysis(str(failure_output), summarizer, state.cycle_number))
        if freeze_gate is not None and freeze_gate(state):
            return TddRunResult("FROZEN_FOR_RESEARCH", state, history, diagnostics)
        if state.cycle_number < state.max_cycles:
            repair(state, build_repair_prompt(str(failure_output), diagnostics[-1].analysis if summarizer else None))
            history.append({"cycle": state.cycle_number, "phase": PHASES[2], "passed": None})
    return TddRunResult("EXHAUSTED", state, history, diagnostics)
# [MC-TDD-07] NFR-TDD-01
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-TDD-01: TDD cycle
# SPEC: execution (test run and result collection) shall complete within 30 seconds per cycle.
@dataclass
class PytestCycleResult:
    """Outcome of one real pytest run (NFR-TDD-01); ``returncode`` is None when the run timed out."""
    passed: bool
    returncode: int | None
    output: str
    duration_sec: float
    timed_out: bool
class CycleKillError(RuntimeError): """The process tree of a timed-out cycle could not be killed."""
def kill_process_tree(proc: subprocess.Popen, flags: int = 0) -> None:
    """Kill proc and its children (taskkill /T on Windows); a tree that is already gone is not an error."""
    if os.name != "nt": return os.killpg(proc.pid, 9)  # POSIX: start_new_session made proc a group leader
    kill = subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, errors="replace", creationflags=flags, timeout=15)
    if kill.returncode != 0 and proc.poll() is None:  # 128 = no such process: the tree already exited
        raise CycleKillError(f"taskkill exited {kill.returncode} for pid {proc.pid}: {(kill.stderr or kill.stdout).strip()}")
def run_pytest_cycle(test_target: str | Path, cwd: str | Path | None = None,
                     timeout: float = CYCLE_TIMEOUT_SEC, extra_args: Sequence[str] = ()) -> PytestCycleResult:
    """Run pytest as a real subprocess; on timeout kill its whole process tree and keep the captured output."""
    argv = [sys.executable, "-m", "pytest", str(test_target), "-q", "-p", "no:cacheprovider", *extra_args]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    start = time.perf_counter()
    proc = subprocess.Popen(argv, cwd=None if cwd is None else str(cwd), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=flags, start_new_session=os.name != "nt")
    try:
        output, _ = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(proc, flags)
        proc.kill()
        output, _ = proc.communicate(timeout=15)
    duration = time.perf_counter() - start
    returncode = None if timed_out else proc.returncode
    return PytestCycleResult(passed=returncode == 0, returncode=returncode, output=output or "",
                             duration_sec=duration, timed_out=timed_out)
# [MC-TDD-08] NFR-TDD-02
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-TDD-02: Diagnostic
# SPEC: error prompts injected into repair agents shall remain bounded below 2,000 tokens.
def estimate_tokens(text: str) -> int:
    """Conservative estimate (no stdlib tokenizer): max of ceil(len / CHARS_PER_TOKEN), word/punctuation runs, and
    ceil(ASCII chars / CHARS_PER_TOKEN) + UTF-8 bytes of non-ASCII chars (unspaced CJK/emoji: up to a token per byte)."""
    ascii_len = len(text.encode("ascii", "ignore"))
    by_bytes = math.ceil(ascii_len / CHARS_PER_TOKEN) + len(text.encode("utf-8", "surrogatepass")) - ascii_len
    return max(math.ceil(len(text) / CHARS_PER_TOKEN), len(re.findall(r"\w+|[^\w\s]", text)), by_bytes)
def bound_diagnostic_prompt(failure_output: str, limit: int = DIAGNOSTIC_TOKEN_LIMIT) -> str:
    """Return failure_output unchanged when it estimates below limit tokens (NFR-TDD-02); otherwise keep its head and
    a larger tail (pytest prints the failure summary last) around a marker line '... [N lines elided] ...', shrinking
    until estimate_tokens(result) < limit. Raises ValueError when limit is too small to hold even the marker line."""
    if estimate_tokens(failure_output) < limit:
        return failure_output
    text = failure_output
    budget = min(int(limit * CHARS_PER_TOKEN), len(text) - 1)
    while budget >= 0:
        head_len = budget // 3
        head = text[:head_len]
        tail = text[len(text) - (budget - head_len):] if budget > head_len else ""
        cut = head.rfind("\n")
        if cut > 0:
            head = head[:cut + 1]
        cut = tail.find("\n")
        if 0 <= cut < len(tail) - 1:
            tail = tail[cut + 1:]
        middle = text[len(head):len(text) - len(tail)]
        sep = "" if not head or head.endswith("\n") else "\n"
        result = f"{head}{sep}... [{len(middle.splitlines())} lines elided] ...\n{tail}"
        if estimate_tokens(result) < limit:
            return result
        budget = budget * 3 // 4 if budget > 3 else budget - 1
    raise ValueError(f"limit {limit} tokens is too small to hold the elision marker line")
def build_repair_prompt(failure_output: str, analysis: str | None = None, limit: int = DIAGNOSTIC_TOKEN_LIMIT) -> str:
    """What REPAIR receives: failure_output bounded below limit; an FR-002 analysis is prepended in a third of it."""
    if analysis is None:
        return bound_diagnostic_prompt(failure_output, limit)
    head = f"Diagnostic analysis:\n{bound_diagnostic_prompt(analysis, limit // 3)}\n\nPytest output:\n"
    return head + bound_diagnostic_prompt(failure_output, limit - estimate_tokens(head) - 1)
# [MC-TDD-09] SRS-412-05-FR-002
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-05-FR-002: Failed
# SPEC: test cycles shall receive automated, real-time diagnostic error analyses synthesized by Gemini
# SPEC: Flash 3.8. Take the Gemini Flash client as an injected callable so tests can pass a real local
# SPEC: summariser instead of a mock (NFR-TDD-03).
class DiagnosticError(RuntimeError):
    """No real diagnostic analysis could be obtained for a failed cycle; nothing is substituted."""
@dataclass
class DiagnosticAnalysis:
    cycle_number: int
    model: str
    prompt: str
    analysis: str
    prompt_tokens: int

def build_diagnostic_prompt(failure_output: str, cycle_number: int) -> str:
    """Instructions plus bounded failure output; the whole prompt stays below DIAGNOSTIC_TOKEN_LIMIT (NFR-TDD-02)."""
    head = f"TDD cycle {cycle_number} failed. Name each failing test, its root cause and the repair.\nPytest output:\n"
    return head + bound_diagnostic_prompt(failure_output, DIAGNOSTIC_TOKEN_LIMIT - estimate_tokens(head) - 1)

def request_diagnostic_analysis(failure_output: str, summarizer: Callable[[str], str],
                                cycle_number: int, model: str = DIAGNOSTIC_MODEL) -> DiagnosticAnalysis:
    """Send the bounded prompt to the injected summariser; raise DiagnosticError rather than invent text."""
    prompt = build_diagnostic_prompt(failure_output, cycle_number)
    try:
        answer = summarizer(prompt)
    except Exception as exc:
        raise DiagnosticError(f"cycle {cycle_number}: {model} raised {type(exc).__name__}: {exc}") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise DiagnosticError(f"cycle {cycle_number}: {model} returned no analysis ({type(answer).__name__})")
    return DiagnosticAnalysis(cycle_number, model, prompt, bound_diagnostic_prompt(answer), estimate_tokens(prompt))
def gemini_flash_cli_summarizer(gemini_exe: str | Path, model: str = DIAGNOSTIC_MODEL,
                                timeout: float = CYCLE_TIMEOUT_SEC) -> Callable[[str], str]:
    """Return a callable running the real CLI as [exe, -m, model, -p, prompt] without a shell."""
    def summarize(prompt: str) -> str:
        try:
            exe_str = str(gemini_exe)
            args = [exe_str]
            if "agy" in exe_str.lower():
                args.extend(["--dangerously-skip-permissions", "--model", model])
            else:
                args.extend(["-m", model])
            args.extend(["-p", prompt])
            proc = subprocess.run(
                args, timeout=timeout, capture_output=True,
                encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise DiagnosticError(f"{model} CLI {gemini_exe} failed: {type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            raise DiagnosticError(f"{model} CLI exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
        if not proc.stdout.strip():
            raise DiagnosticError(f"Empty stdout from CLI. Stderr: {proc.stderr.strip()}")
        return proc.stdout
    return summarize
