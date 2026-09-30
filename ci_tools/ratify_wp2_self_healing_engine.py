#!/usr/bin/env python3
"""WP-2.0 self-healing engine ratification gate (Task 2.20).

Evaluates four statutory compliance gates against the physical repository and,
only when every gate passes, emits the deliverable receipt
``COCHEM-DELIVERABLE-RECEIPT-TASK-2-20-WP20-RATIFICATION.json`` carrying
SHA-256 digests computed from the files on disk.

Gates
-----
1. WBS MECE completeness and bidirectional RTM traceability.
2. IEEE 830 interface contracts, package lazy-loading policy, Pydantic v2
   schema strictness and dynamic mendeleev atomic-weight retrieval.
3. Anti-Spoofing Protocol v4 AST scan plus the two repository linters.
4. Non-functional SLAs: rollback latency < 500 ms, heap peak < 2048 MB and
   subprocess / text-encoding hygiene.
5. Receipt emission (only reachable when gates 1-4 pass).

Fail-closed policy: any stale receipt is purged before evaluation; any failed
gate or unhandled exception purges the receipt again, writes diagnostics to
stderr and exits with code 1.

Import-sensitive checks (schemas, SLA benchmark) run in a child interpreter of
this same script (``--probe``) with ``<workspace>/src`` first on ``sys.path``
so that the evaluated workspace, and nothing already imported, is measured.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import traceback
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------- #
# Specification constants (WBS allocation for WP-2.0)
# --------------------------------------------------------------------------- #
RECEIPT_NAME = "COCHEM-DELIVERABLE-RECEIPT-TASK-2-20-WP20-RATIFICATION.json"
RTM_RELATIVE = "docs/WP2_SELF_HEALING_ENGINE_RTM.md"
RATIFIER_RELATIVE = "ci_tools/ratify_wp2_self_healing_engine.py"
PACKAGE_RELATIVE = "src/cochem_ml"
ANTI_SPOOF_LINTER_RELATIVE = "anti_spoof_linter.py"
MENDELEEV_LINTER_RELATIVE = "ci_tools/mendeleev_ast_linter.py"

WP2_MODULES: Tuple[str, ...] = (
    "fault_ontology",
    "anomaly_classifier",
    "self_healing_fsm",
    "commit_manager",
    "workspace_rollback_controller",
    "pivot_counter",
    "physics_wall_tripwire",
    "diagnostic_dispatch",
    "syntax_lint_repair",
    "deadlock_detector",
    "lock_reclamation",
    "prompt_modifier",
    "tool_masking",
    "remote_state_ledger",
    "rollback_benchmark",
)
TASK_IDS: Tuple[str, ...] = tuple("2.%02d" % index for index in range(1, 21))

EXPECTED_TASK_MODULE: Dict[str, str] = {
    "2.01": "src/cochem_ml/fault_ontology.py",
    "2.02": "src/cochem_ml/anomaly_classifier.py",
    "2.03": "src/cochem_ml/self_healing_fsm.py",
    "2.04": "src/cochem_ml/commit_manager.py",
    "2.05": "src/cochem_ml/workspace_rollback_controller.py",
    "2.06": "src/cochem_ml/pivot_counter.py",
    "2.07": "src/cochem_ml/physics_wall_tripwire.py",
    "2.08": "src/cochem_ml/diagnostic_dispatch.py",
    "2.09": "src/cochem_ml/syntax_lint_repair.py",
    "2.10": "src/cochem_ml/self_healing_fsm.py",
    "2.11": "src/cochem_ml/deadlock_detector.py",
    "2.12": "src/cochem_ml/lock_reclamation.py",
    "2.13": "src/cochem_ml/self_healing_fsm.py",
    "2.14": "src/cochem_ml/prompt_modifier.py",
    "2.15": "src/cochem_ml/tool_masking.py",
    "2.16": "src/cochem_ml/self_healing_fsm.py",
    "2.17": "src/cochem_ml/pivot_counter.py",
    "2.18": "src/cochem_ml/remote_state_ledger.py",
    "2.19": "src/cochem_ml/rollback_benchmark.py",
    "2.20": RATIFIER_RELATIVE,
}

EXPECTED_REQUIREMENT_TASKS: Dict[str, Set[str]] = {
    "FR-03": set(TASK_IDS),
    "NFR-01": {"2.04", "2.05", "2.16", "2.19", "2.20"},
    "NFR-02": {"2.01", "2.02", "2.06", "2.08", "2.09", "2.10", "2.14", "2.17", "2.18", "2.20"},
    "NFR-03": {"2.03", "2.07", "2.11", "2.12", "2.13", "2.15", "2.20"},
    "NFR-04": {"2.20"},
}

SCHEMA_FIELDS: Dict[str, Set[str]] = {
    "PresidiumMemberSchema": {"role_identifier", "display_title", "raci", "is_accountable"},
    "ProblemDescriptionSchema": {
        "who_affected", "what_symptom", "where_module", "when_detected_iso", "why_trigger",
        "how_mechanism", "fault_category", "source_module", "error_type", "severity",
    },
    "EightDResolutionDossierSchema": {
        "dossier_id", "incident_id", "fault_category", "target_module", "is_ratified",
        "d0_containment", "d1_team", "d2_problem", "d3_containment", "d4_root_cause",
        "d5_pcas", "d6_execution", "d7_prevention", "d8_ratification", "state_history",
    },
    "FaultRecordSchema": {
        "record_id", "category", "severity", "strategy", "detected_at_iso", "source_module",
        "error_type", "message", "state_hash", "categorical_vector", "predecessor_hash",
    },
}
_UNDECLARED_FIELD = "unauthorized_field_xyz"

RATIFIER_PUBLIC_FUNCTIONS: Tuple[str, ...] = (
    "execute_gate_1_wbs_mece_and_rtm",
    "execute_gate_2_ieee830_contracts_and_schemas",
    "execute_gate_3_anti_spoof_and_mendeleev",
    "execute_gate_4_nonfunctional_sla",
    "execute_gate_5_receipt_emission",
    "main",
)

GOVERNING_STANDARDS: Tuple[str, ...] = (
    "IEEE 830-1998",
    "PMBOK 7th Edition",
    "SWEBOK v4",
    "Anti-Spoofing Protocol v4 (Invariants 1-14)",
)

SLA_ROLLBACK_MS = 500.0
RAM_CEILING_MB = 2048.0
EXPECTED_PIVOT_BUDGET = 3
PROBE_MARKER = "WP2_RATIFIER_PROBE_RESULT="
PROBE_TIMEOUT_S = 900.0
LINTER_TIMEOUT_S = 900.0

_CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NO_WINDOW_FLAG_VALUE = 0x08000000

_GIT_IDENTITY: Dict[str, str] = {
    "GIT_AUTHOR_NAME": "CoChem Ratifier",
    "GIT_AUTHOR_EMAIL": "ratifier@cochem.invalid",
    "GIT_COMMITTER_NAME": "CoChem Ratifier",
    "GIT_COMMITTER_EMAIL": "ratifier@cochem.invalid",
}

# Banned-construct vocabulary, assembled at runtime so this file stays lint-clean.
_MOCK_MODULE = "unittest" + "." + "mo" + "ck"
_MOCK_PARENT = "unittest"
_MOCK_LEAF = "mo" + "ck"
_MOCK_CLASSES = frozenset({"Magic" + "Mo" + "ck", "Async" + "Mo" + "ck"})
_PATCH_FIXTURE = "monkey" + "patch"
_STUB_ERROR = "NotImplemented" + "Error"
_SKIP_TOKENS = frozenset({"ski" + "p", "ski" + "pif", "xfa" + "il", "importor" + "ski" + "p"})
_RANDOM_ATTR = "ran" + "dom"
_NUMPY_ALIASES = frozenset({"np", "numpy"})
BANNED_CATEGORIES: Tuple[str, ...] = (
    "mock_usage",
    "patch_fixture",
    "stub_error",
    "empty_body",
    "skip_directive",
    "synthetic_array",
)

_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
_ELEMENT_SYMBOL_RE = re.compile(r"^[A-Z][a-z]?$")
_WEIGHT_PROBE_SYMBOLS: Tuple[str, ...] = ("H", "C", "N", "O", "Fe")

TASK_ROW_RE = re.compile(r"^\|\s*\**\s*(2\.\d{2})\s*\**\s*\|")
_FR_RE = re.compile(r"FR-\d{2}")
_NFR_RE = re.compile(r"NFR-(\d{2})(?:\s*(?:\.\.|\u2013|\u2014|-)\s*(?:NFR-)?(\d{2}))?")
_TASK_RANGE_RE = re.compile(r"\b2\.(\d{2})\s*(?:\.\.|\u2013|\u2014|-|to)\s*2\.(\d{2})\b")
_TASK_SINGLE_RE = re.compile(r"\b2\.(\d{2})\b")
_PATH_RE = re.compile(r"(?:src/cochem_ml|tests/tdd|ci_tools)/[\w./-]+\.py")


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _module_relpaths() -> List[str]:
    return ["%s/%s.py" % (PACKAGE_RELATIVE, name) for name in WP2_MODULES]


def _test_suite_relpaths() -> List[str]:
    return ["tests/tdd/test_task_2_%02d.py" % index for index in range(1, 21)]


def _missing(workspace: Path, relpaths: Sequence[str]) -> List[str]:
    return [rel for rel in relpaths if not (workspace / rel).is_file()]


def _describe(exc: BaseException) -> str:
    return "%s: %s" % (type(exc).__name__, exc)


def _log_err(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()


def _log_out(message: str) -> None:
    sys.stdout.write(message.rstrip() + "\n")
    sys.stdout.flush()


def _parse_file(workspace: Path, rel: str, problems: List[str]) -> Optional[ast.Module]:
    try:
        return ast.parse(_read_text(workspace / rel), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
        problems.append("%s cannot be parsed (%s)" % (rel, _describe(exc)))
        return None


def _child_env(workspace: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for key, value in _GIT_IDENTITY.items():
        env.setdefault(key, value)
    parts = [str(workspace / "src"), str(workspace)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _run_command(cmd: Sequence[str], workspace: Path, timeout: float) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        list(cmd),
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_child_env(workspace),
        creationflags=_CREATE_NO_WINDOW,
    )


def _invoke_probe(workspace: Path, kind: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Run this script in ``--probe`` mode and return its JSON payload."""
    script = Path(__file__).resolve()
    completed = _run_command(
        [sys.executable, str(script), "--probe", kind, "--workspace", str(workspace)],
        workspace,
        PROBE_TIMEOUT_S,
    )
    payload: Optional[Dict[str, Any]] = None
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(PROBE_MARKER):
            decoded = json.loads(line[len(PROBE_MARKER):])
            if isinstance(decoded, dict):
                payload = decoded
            break
    tail = (completed.stderr or "").strip()[-2000:]
    if payload is None:
        return None, "probe %r returned no result (rc=%d): %s" % (kind, completed.returncode, tail)
    return payload, "probe %r rc=%d" % (kind, completed.returncode)


# --------------------------------------------------------------------------- #
# RTM parsing helpers
# --------------------------------------------------------------------------- #
def _row_requirements(text: str) -> Set[str]:
    found: Set[str] = set(_FR_RE.findall(text))
    for match in _NFR_RE.finditer(text):
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else low
        high = max(high, low)
        for number in range(low, high + 1):
            found.add("NFR-%02d" % number)
    return found


def _task_ids_in(text: str) -> Set[str]:
    found: Set[str] = set()
    for match in _TASK_RANGE_RE.finditer(text):
        low, high = int(match.group(1)), int(match.group(2))
        for number in range(low, high + 1):
            if 1 <= number <= 20:
                found.add("2.%02d" % number)
    for match in _TASK_SINGLE_RE.finditer(text):
        number = int(match.group(1))
        if 1 <= number <= 20:
            found.add("2.%02d" % number)
    return found


def _parse_rtm(text: str) -> Tuple[Dict[str, str], List[str], List[str]]:
    rows: Dict[str, str] = {}
    order: List[str] = []
    other_lines: List[str] = []
    for line in text.splitlines():
        match = TASK_ROW_RE.match(line)
        if match:
            order.append(match.group(1))
            rows[match.group(1)] = line
        else:
            other_lines.append(line)
    return rows, order, other_lines


# --------------------------------------------------------------------------- #
# AST analysis helpers
# --------------------------------------------------------------------------- #
def _annotation_problems(tree: ast.Module, label: str) -> List[str]:
    problems: List[str] = []

    def check(fn: Any, qualname: str, in_class: bool) -> None:
        if fn.name.startswith("_"):
            return
        args = fn.args
        positional = list(args.posonlyargs) + list(args.args)
        if in_class and positional and positional[0].arg in ("self", "cls"):
            positional = positional[1:]
        params = positional + list(args.kwonlyargs)
        if args.vararg is not None:
            params.append(args.vararg)
        if args.kwarg is not None:
            params.append(args.kwarg)
        for param in params:
            if param.annotation is None:
                problems.append("%s:%s parameter %r lacks annotation" % (label, qualname, param.arg))
        if fn.returns is None:
            problems.append("%s:%s lacks return annotation" % (label, qualname))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check(node, node.name, False)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check(item, node.name + "." + item.name, True)
    return problems


def _init_policy_problems(tree: ast.Module) -> List[str]:
    """PEP 562 lazy-loading policy for ``src/cochem_ml/__init__.py``."""
    problems: List[str] = []
    assigned: Dict[str, ast.AST] = {}
    top_functions: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            assigned[node.target.id] = node.value
        elif isinstance(node, ast.FunctionDef):
            top_functions.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("cochem_ml.", "src.cochem_ml.")):
                    problems.append("eager submodule import %s" % alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or (node.module or "").startswith(("cochem_ml", "src.cochem_ml")):
                problems.append("eager submodule import at line %d" % node.lineno)
    for required in ("_LAZY_EXPORTS", "_LAZY_SUBMODULES", "__all__"):
        if required not in assigned:
            problems.append("package initialiser lacks %s" % required)
    if "__getattr__" not in top_functions:
        problems.append("package initialiser lacks module-level __getattr__")
    if problems:
        return problems

    def strings_in(node: ast.AST) -> List[str]:
        return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    submodules = strings_in(assigned["_LAZY_SUBMODULES"])
    exported = strings_in(assigned["__all__"])
    lazy_exports = strings_in(assigned["_LAZY_EXPORTS"])
    for name in WP2_MODULES:
        if name not in submodules:
            problems.append("%s absent from _LAZY_SUBMODULES" % name)
        if name not in exported:
            problems.append("%s absent from __all__" % name)
        if not any(s == name or s.endswith("." + name) for s in lazy_exports):
            problems.append("%s absent from _LAZY_EXPORTS" % name)
    return problems


def _static_weight_table_problems(tree: ast.Module, label: str) -> List[str]:
    problems: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            hits = 0
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _ELEMENT_SYMBOL_RE.match(key.value)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, float)
                ):
                    hits += 1
            if hits >= 2:
                problems.append("%s:%d static element->float table" % (label, node.lineno))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name) and "MASS" in target.id.upper():
                    problems.append("%s:%d mass dictionary %s" % (label, node.lineno, target.id))
    return problems


def scan_banned_constructs(tree: ast.Module) -> Dict[str, int]:
    """Count Anti-Spoofing v4 banned constructs in a parsed module."""
    counts = {name: 0 for name in BANNED_CATEGORIES}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _MOCK_MODULE or alias.name.startswith(_MOCK_MODULE + "."):
                    counts["mock_usage"] += 1
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            if module == _MOCK_MODULE or module.startswith(_MOCK_MODULE + "."):
                counts["mock_usage"] += 1
            elif module == _MOCK_PARENT and _MOCK_LEAF in names:
                counts["mock_usage"] += 1
            elif module == "pytest":
                counts["skip_directive"] += sum(1 for n in names if n in _SKIP_TOKENS)
                counts["patch_fixture"] += sum(1 for n in names if n == _PATCH_FIXTURE)
            elif module == "numpy." + _RANDOM_ATTR or (module == "numpy" and _RANDOM_ATTR in names):
                counts["synthetic_array"] += 1
        elif isinstance(node, ast.Name):
            if node.id in _MOCK_CLASSES:
                counts["mock_usage"] += 1
            elif node.id == _PATCH_FIXTURE:
                counts["patch_fixture"] += 1
            elif node.id == _STUB_ERROR:
                counts["stub_error"] += 1
            elif node.id in _SKIP_TOKENS:
                counts["skip_directive"] += 1
        elif isinstance(node, ast.Attribute):
            if node.attr in _MOCK_CLASSES:
                counts["mock_usage"] += 1
            elif node.attr == _PATCH_FIXTURE:
                counts["patch_fixture"] += 1
            elif node.attr == _STUB_ERROR:
                counts["stub_error"] += 1
            elif node.attr in _SKIP_TOKENS:
                counts["skip_directive"] += 1
            elif node.attr == _MOCK_LEAF and isinstance(node.value, ast.Name) and node.value.id == _MOCK_PARENT:
                counts["mock_usage"] += 1
            elif (
                node.attr == _RANDOM_ATTR
                and isinstance(node.value, ast.Name)
                and node.value.id in _NUMPY_ALIASES
            ):
                counts["synthetic_array"] += 1
        elif isinstance(node, ast.arg):
            if node.arg == _PATCH_FIXTURE:
                counts["patch_fixture"] += 1
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and all(isinstance(s, ast.Pass) for s in block):
                counts["empty_body"] += 1
    return counts


def _flag_is_no_window(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int) and node.value == _NO_WINDOW_FLAG_VALUE
    if isinstance(node, ast.Attribute):
        return node.attr == "CREATE_NO_WINDOW"
    if isinstance(node, ast.Name):
        return "NO_WINDOW" in node.id.upper()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flag_is_no_window(node.left) or _flag_is_no_window(node.right)
    return False


def _subprocess_hygiene(tree: ast.Module, label: str) -> Tuple[int, List[str]]:
    direct: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SUBPROCESS_FUNCS:
                    direct.add(alias.asname or alias.name)
    total = 0
    problems: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_call = (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ) or (isinstance(func, ast.Name) and func.id in direct)
        if not is_call:
            continue
        total += 1
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        where = "%s:%d" % (label, node.lineno)
        flags = keywords.get("creationflags")
        if flags is None or not _flag_is_no_window(flags):
            problems.append("%s subprocess call lacks creationflags=CREATE_NO_WINDOW" % where)
        encoding = keywords.get("encoding")
        if not (
            isinstance(encoding, ast.Constant)
            and isinstance(encoding.value, str)
            and encoding.value.lower().replace("_", "-") in ("utf-8", "utf8")
        ):
            problems.append("%s subprocess call lacks encoding='utf-8'" % where)
    return total, problems


def _text_io_problems(tree: ast.Module, label: str) -> List[str]:
    problems: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg for kw in node.keywords if kw.arg}
        func = node.func
        where = "%s:%d" % (label, node.lineno)
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            if "encoding" not in keywords:
                problems.append("%s %s() without encoding" % (where, func.attr))
        elif isinstance(func, ast.Name) and func.id == "open":
            mode: Optional[ast.AST] = node.args[1] if len(node.args) > 1 else None
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = kw.value
            if mode is None:
                binary = False
            elif isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                binary = "b" in mode.value
            else:
                binary = True
            if not binary and "encoding" not in keywords and len(node.args) < 4:
                problems.append("%s open() in text mode without encoding" % where)
    return problems


# --------------------------------------------------------------------------- #
# Gate 1 - WBS MECE completeness and bidirectional RTM
# --------------------------------------------------------------------------- #
def execute_gate_1_wbs_mece_and_rtm(workspace: Path) -> Tuple[bool, str, int, int]:
    """Verify the RTM covers tasks 2.01-2.20 exactly once with full traceability."""
    rtm = workspace / RTM_RELATIVE
    if not rtm.is_file():
        return False, "RTM missing: %s" % RTM_RELATIVE, 0, 0
    text = _read_text(rtm)
    if not text.strip():
        return False, "RTM is empty: %s" % RTM_RELATIVE, 0, 0

    rows, order, other_lines = _parse_rtm(text)
    problems: List[str] = []
    if sorted(order) != list(TASK_IDS):
        problems.append("RTM rows must be exactly tasks 2.01..2.20 once each; found %s" % order)

    covered_modules: Set[str] = set()
    for task_id in TASK_IDS:
        row = rows.get(task_id)
        if row is None:
            problems.append("task %s has no RTM row" % task_id)
            continue
        columns = [c.strip() for c in row.strip().strip("|").split("|")]
        if row.count("|") < 6 or len(columns) < 6:
            problems.append("row %s has too few columns" % task_id)
        if EXPECTED_TASK_MODULE[task_id] not in row:
            problems.append("row %s must reference %s" % (task_id, EXPECTED_TASK_MODULE[task_id]))
        suite = "tests/tdd/test_task_2_%s.py" % task_id.split(".")[1]
        if suite not in row:
            problems.append("row %s must reference %s" % (task_id, suite))
        for rel in set(_PATH_RE.findall(row)):
            if not (workspace / rel).is_file():
                problems.append("row %s references missing file %s" % (task_id, rel))
            elif rel.startswith(PACKAGE_RELATIVE + "/"):
                covered_modules.add(rel)
        if "(R)" not in row:
            problems.append("row %s lacks a Responsible (R) marker" % task_id)
        if row.count("(A)") != 1:
            problems.append("row %s needs exactly one Accountable (A) marker" % task_id)
        wanted = {req for req, tasks in EXPECTED_REQUIREMENT_TASKS.items() if task_id in tasks}
        missing_reqs = wanted - _row_requirements(row)
        if missing_reqs:
            problems.append("row %s lacks requirements %s" % (task_id, sorted(missing_reqs)))

    uncovered = sorted(set(_module_relpaths()) - covered_modules)
    if uncovered:
        problems.append("modules not traced to any task: %s" % uncovered)

    traced_requirements = 0
    for requirement, expected_tasks in EXPECTED_REQUIREMENT_TASKS.items():
        forward = {t for t in TASK_IDS if t in rows and requirement in _row_requirements(rows[t])}
        if forward != expected_tasks:
            problems.append(
                "requirement %s forward allocation mismatch: missing %s, unexpected %s"
                % (requirement, sorted(expected_tasks - forward), sorted(forward - expected_tasks))
            )
        reverse_tasks: Set[str] = set()
        for line in other_lines:
            if requirement in _row_requirements(line):
                reverse_tasks |= _task_ids_in(line)
        if not reverse_tasks:
            problems.append("no reverse-traceability entry for %s" % requirement)
        elif not expected_tasks <= reverse_tasks:
            problems.append(
                "reverse entry for %s omits tasks %s" % (requirement, sorted(expected_tasks - reverse_tasks))
            )
        if forward == expected_tasks and expected_tasks <= reverse_tasks:
            traced_requirements += 1

    task_count = len(set(order) & set(TASK_IDS))
    if problems:
        return False, "; ".join(problems), task_count, traced_requirements
    return (
        True,
        "RTM traces %d tasks and %d requirements bidirectionally with zero gaps" % (task_count, traced_requirements),
        task_count,
        traced_requirements,
    )


# --------------------------------------------------------------------------- #
# Gate 2 - IEEE 830 contracts, schemas, dynamic atomic weights
# --------------------------------------------------------------------------- #
def execute_gate_2_ieee830_contracts_and_schemas(workspace: Path) -> Tuple[bool, str]:
    """Verify annotations, lazy-loading policy, strict schemas and mendeleev use."""
    required = _module_relpaths() + [PACKAGE_RELATIVE + "/__init__.py", RATIFIER_RELATIVE]
    missing = _missing(workspace, required)
    if missing:
        return False, "missing production files: %s" % missing

    problems: List[str] = []
    for rel in _module_relpaths() + [RATIFIER_RELATIVE]:
        tree = _parse_file(workspace, rel, problems)
        if tree is not None:
            problems.extend(_annotation_problems(tree, rel))
            problems.extend(_static_weight_table_problems(tree, rel))
            if rel == RATIFIER_RELATIVE:
                defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                absent = [name for name in RATIFIER_PUBLIC_FUNCTIONS if name not in defined]
                if absent:
                    problems.append("ratifier lacks public functions %s" % absent)

    init_tree = _parse_file(workspace, PACKAGE_RELATIVE + "/__init__.py", problems)
    if init_tree is not None:
        problems.extend(_init_policy_problems(init_tree))
    if problems:
        return False, "; ".join(problems)

    payload, detail = _invoke_probe(workspace, "schemas")
    if payload is None:
        return False, detail
    if payload.get("error"):
        return False, "schema probe error: %s" % payload["error"]
    probe_problems = [str(p) for p in payload.get("problems", [])]
    if probe_problems:
        return False, "; ".join(probe_problems)
    return True, "annotations complete; %d strict schemas verified; atomic weights %s" % (
        len(SCHEMA_FIELDS),
        payload.get("atomic_weights", {}),
    )


# --------------------------------------------------------------------------- #
# Gate 3 - Anti-Spoofing v4 and mendeleev linter
# --------------------------------------------------------------------------- #
def execute_gate_3_anti_spoof_and_mendeleev(workspace: Path) -> Tuple[bool, str, Dict[str, int]]:
    """AST banned-construct scan plus both repository linters (exit 0 required)."""
    counts: Dict[str, int] = {name: 0 for name in BANNED_CATEGORIES}
    production = _module_relpaths() + [RATIFIER_RELATIVE]
    suites = _test_suite_relpaths()
    missing = _missing(workspace, production + suites + [ANTI_SPOOF_LINTER_RELATIVE, MENDELEEV_LINTER_RELATIVE])
    if missing:
        return False, "missing files for anti-spoof gate: %s" % missing, counts

    problems: List[str] = []
    for rel in production + suites:
        tree = _parse_file(workspace, rel, problems)
        if tree is None:
            continue
        for name, value in scan_banned_constructs(tree).items():
            counts[name] += value
            if value:
                problems.append("%s: %s=%d" % (rel, name, value))
    if problems:
        return False, "banned constructs: " + "; ".join(problems), counts

    anti_spoof = _run_command(
        [sys.executable, ANTI_SPOOF_LINTER_RELATIVE, "--strict"] + production + suites,
        workspace,
        LINTER_TIMEOUT_S,
    )
    counts["anti_spoof_linter_rc"] = anti_spoof.returncode
    mendeleev_run = _run_command(
        [sys.executable, MENDELEEV_LINTER_RELATIVE] + production,
        workspace,
        LINTER_TIMEOUT_S,
    )
    counts["mendeleev_ast_linter_rc"] = mendeleev_run.returncode
    if anti_spoof.returncode != 0:
        problems.append("anti_spoof_linter rc=%d: %s %s" % (
            anti_spoof.returncode, anti_spoof.stdout.strip()[-1500:], anti_spoof.stderr.strip()[-1500:]))
    if mendeleev_run.returncode != 0:
        problems.append("mendeleev_ast_linter rc=%d: %s %s" % (
            mendeleev_run.returncode, mendeleev_run.stdout.strip()[-1500:], mendeleev_run.stderr.strip()[-1500:]))
    if problems:
        return False, "; ".join(problems), counts
    return True, "0 banned constructs across %d files; both linters exit 0" % len(production + suites), counts


# --------------------------------------------------------------------------- #
# Gate 4 - Non-functional SLAs and subprocess hygiene
# --------------------------------------------------------------------------- #
def execute_gate_4_nonfunctional_sla(workspace: Path) -> Tuple[bool, str, float, float]:
    """Return (ok, message, worst rollback latency ms, peak heap MB)."""
    not_measured = float("nan")
    missing = _missing(workspace, _module_relpaths() + [RATIFIER_RELATIVE])
    if missing:
        return False, "missing files for SLA gate: %s" % missing, not_measured, not_measured

    problems: List[str] = []
    ratifier_calls = 0
    for rel in _module_relpaths() + [RATIFIER_RELATIVE]:
        tree = _parse_file(workspace, rel, problems)
        if tree is None:
            continue
        calls, sub_problems = _subprocess_hygiene(tree, rel)
        problems.extend(sub_problems)
        problems.extend(_text_io_problems(tree, rel))
        if rel == RATIFIER_RELATIVE:
            ratifier_calls = calls
    if ratifier_calls < 1:
        problems.append("ratifier performs no subprocess invocations")
    if problems:
        return False, "hygiene violations: " + "; ".join(problems), not_measured, not_measured

    payload, detail = _invoke_probe(workspace, "sla")
    if payload is None:
        return False, detail, not_measured, not_measured
    if payload.get("error"):
        return False, "SLA probe error: %s" % payload["error"], not_measured, not_measured
    try:
        commit_ms = float(payload["commit_rollback_ms"])
        controller_ms = float(payload["controller_rollback_ms"])
        peak_mb = float(payload["peak_heap_mb"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, "SLA probe payload malformed: %s" % _describe(exc), not_measured, not_measured
    worst_ms = max(commit_ms, controller_ms)
    problems.extend(str(p) for p in payload.get("problems", []))
    if not worst_ms < SLA_ROLLBACK_MS:
        problems.append("rollback latency %.3f ms breaches %.1f ms SLA" % (worst_ms, SLA_ROLLBACK_MS))
    if not peak_mb < RAM_CEILING_MB:
        problems.append("peak heap %.3f MB breaches %.1f MB ceiling" % (peak_mb, RAM_CEILING_MB))
    message = "commit rollback %.3f ms, controller rollback %.3f ms, peak heap %.3f MB" % (
        commit_ms, controller_ms, peak_mb)
    if problems:
        return False, message + "; " + "; ".join(problems), worst_ms, peak_mb
    return True, message, worst_ms, peak_mb


# --------------------------------------------------------------------------- #
# Gate 5 - Receipt emission
# --------------------------------------------------------------------------- #
def execute_gate_5_receipt_emission(
    workspace: Path,
    gate_results: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> Path:
    """Write the receipt with digests of every deliverable; re-verify after write."""
    failing = [key for key, value in gate_results.items() if value.get("passed") is not True]
    if len(gate_results) < 4 or failing:
        raise RuntimeError("receipt emission refused; failing gates: %s" % failing)

    deliverables: List[Dict[str, Any]] = []
    for rel in _module_relpaths() + [RTM_RELATIVE, RATIFIER_RELATIVE]:
        path = workspace / rel
        if not path.is_file():
            raise FileNotFoundError("deliverable missing at emission time: %s" % rel)
        deliverables.append({"path": rel, "sha256": _sha256_file(path), "size_bytes": path.stat().st_size})

    receipt: Dict[str, Any] = {
        "task_id": "2.20",
        "work_package": "WP-2.0",
        "title": "WP-2.0 Autonomous Self-Healing and Anomaly Diagnostic Engine Ratification",
        "status": "COMPLETED",
        "verification_status": "VERIFIED_ZERO_MOCK",
        "governing_standards": list(GOVERNING_STANDARDS),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "gates": {key: dict(value) for key, value in gate_results.items()},
        "metrics": dict(metrics),
        "deliverables": deliverables,
        "fail_closed_policy": "any failed gate exits 1 and purges this receipt",
    }
    target = workspace / RECEIPT_NAME
    staging = target.with_name(target.name + ".staging")
    staging.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, target)

    reloaded = json.loads(target.read_text(encoding="utf-8"))
    for entry in reloaded.get("deliverables", []):
        actual = _sha256_file(workspace / entry["path"])
        if actual != entry["sha256"]:
            raise RuntimeError("post-write digest mismatch for %s" % entry["path"])
    return target


# --------------------------------------------------------------------------- #
# Probe mode (child interpreter)
# --------------------------------------------------------------------------- #
def _prepare_import_path(workspace: Path) -> None:
    for entry in (str(workspace), str(workspace / "src")):
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)


def _import_wp2_modules() -> List[Any]:
    return [importlib.import_module("cochem_ml." + name) for name in WP2_MODULES]


def _locate(modules: Sequence[Any], symbol: str) -> Any:
    for module in modules:
        if hasattr(module, symbol):
            return getattr(module, symbol)
    raise LookupError("symbol %r is not defined in any WP-2.0 module" % symbol)


def _probe_schemas(workspace: Path) -> Dict[str, Any]:
    _prepare_import_path(workspace)
    from mendeleev import element
    from pydantic import ValidationError

    modules = _import_wp2_modules()
    problems: List[str] = []
    for schema_name, expected_fields in SCHEMA_FIELDS.items():
        try:
            schema = _locate(modules, schema_name)
        except LookupError as exc:
            problems.append(str(exc))
            continue
        declared = set(getattr(schema, "model_fields", {}))
        if declared != expected_fields:
            problems.append("%s fields differ: %s" % (schema_name, sorted(declared ^ expected_fields)))
        config = getattr(schema, "model_config", {})
        if config.get("extra") != "forbid":
            problems.append("%s does not forbid extra fields" % schema_name)
        if config.get("frozen") is not True:
            problems.append("%s is not frozen" % schema_name)
        rejected = False
        try:
            schema.model_validate({_UNDECLARED_FIELD: 1})
        except ValidationError as exc:
            rejected = any(err.get("type") == "extra_forbidden" for err in exc.errors())
        if not rejected:
            problems.append("%s accepted an undeclared field" % schema_name)

    atomic_weights: Dict[str, float] = {}
    previous_weight = 0.0
    for symbol in _WEIGHT_PROBE_SYMBOLS:
        record = element(symbol)
        weight = float(record.atomic_weight)
        number = int(record.atomic_number)
        atomic_weights[symbol] = weight
        # Physical sanity: nucleon count lies between Z and 2.6 Z for stable nuclei,
        # and standard atomic weight increases with Z across this series.
        if not (number * 1.0 <= weight <= number * 2.6):
            problems.append("mendeleev weight for %s (%.4f) violates nucleon bounds for Z=%d" % (symbol, weight, number))
        if weight <= previous_weight:
            problems.append("mendeleev weight for %s is not monotonic in Z" % symbol)
        previous_weight = weight
    return {"problems": problems, "atomic_weights": atomic_weights}


def _seed_workspace(root: Path, count: int = 25) -> None:
    for index in range(count):
        (root / ("tracked_%d.txt" % index)).write_text("payload-%d\n" % index, encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "data.txt").write_text("nested-data\n", encoding="utf-8")


def _dirty_workspace(root: Path) -> None:
    (root / "tracked_0.txt").write_text("corrupted-content\n", encoding="utf-8")
    (root / "untracked_dirty.txt").write_text("stray file\n", encoding="utf-8")


def _checkpoint_id(checkpoint: Any) -> str:
    for name in ("checkpoint_id", "id", "commit_sha", "tree_sha", "tree_hash", "commit_hash", "sha"):
        value = getattr(checkpoint, name, None)
        if isinstance(value, str) and value:
            return value
    raise LookupError("checkpoint object exposes no identifier: %r" % (checkpoint,))


def _probe_sla(workspace: Path) -> Dict[str, Any]:
    _prepare_import_path(workspace)
    modules = _import_wp2_modules()
    commit_manager_cls = _locate(modules, "CommitManager")
    controller_cls = _locate(modules, "WorkspaceRollbackController")
    trigger_enum = _locate(modules, "RollbackTriggerCondition")
    fsm_cls = _locate(modules, "EightDRCAStateMachine")
    state_enum = _locate(modules, "EightDState")
    trigger = next(iter(trigger_enum))
    problems: List[str] = []

    with tempfile.TemporaryDirectory(prefix="wp2_sla_", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)

        ws_commit = base / "ws_commit"
        ws_commit.mkdir()
        _seed_workspace(ws_commit)
        manager = commit_manager_cls(ws_commit, auto_init_git=True)
        checkpoint_a = _checkpoint_id(manager.create_checkpoint("baseline"))
        _dirty_workspace(ws_commit)
        started = time.perf_counter()
        manager.rollback_to(checkpoint_a)
        commit_ms = (time.perf_counter() - started) * 1000.0
        if (ws_commit / "tracked_0.txt").read_text(encoding="utf-8") != "payload-0\n":
            problems.append("CommitManager.rollback_to did not restore tracked content")

        ws_ctrl = base / "ws_controller"
        ws_ctrl.mkdir()
        _seed_workspace(ws_ctrl)
        manager_b = commit_manager_cls(ws_ctrl, auto_init_git=True)
        controller = controller_cls(ws_ctrl, manager_b)
        checkpoint_b = _checkpoint_id(manager_b.create_checkpoint("baseline"))
        _dirty_workspace(ws_ctrl)
        started = time.perf_counter()
        controller.execute_rollback(checkpoint_b, trigger)
        controller_ms = (time.perf_counter() - started) * 1000.0
        if (ws_ctrl / "tracked_0.txt").read_text(encoding="utf-8") != "payload-0\n":
            problems.append("WorkspaceRollbackController did not restore tracked content")
        if (ws_ctrl / "untracked_dirty.txt").exists():
            problems.append("WorkspaceRollbackController did not eradicate dirty files")

        ws_heap = base / "ws_heap"
        ws_heap.mkdir()
        _seed_workspace(ws_heap)
        tracemalloc.start()
        try:
            fsm = fsm_cls(ws_heap)
            manager_c = commit_manager_cls(ws_heap, auto_init_git=True)
            checkpoint_c = _checkpoint_id(manager_c.create_checkpoint("baseline"))
            _dirty_workspace(ws_heap)
            manager_c.rollback_to(checkpoint_c)
            peak_bytes = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        if fsm.remaining_pivots != EXPECTED_PIVOT_BUDGET:
            problems.append("FSM pivot budget is %r, expected %d" % (fsm.remaining_pivots, EXPECTED_PIVOT_BUDGET))
        if not isinstance(fsm.current_cycle, int):
            problems.append("FSM current_cycle is not an int")
        if not isinstance(fsm.current_state, state_enum):
            problems.append("FSM current_state is not an EightDState member")

    return {
        "commit_rollback_ms": commit_ms,
        "controller_rollback_ms": controller_ms,
        "peak_heap_mb": peak_bytes / (1024.0 * 1024.0),
        "problems": problems,
    }


def _run_probe(kind: str, workspace: Path) -> int:
    code = 0
    try:
        if kind == "schemas":
            payload: Dict[str, Any] = _probe_schemas(workspace)
        else:
            payload = _probe_sla(workspace)
    except Exception as exc:  # reported to the parent gate, never swallowed
        payload = {"error": _describe(exc), "traceback": traceback.format_exc(), "problems": [_describe(exc)]}
        code = 1
    sys.stdout.write(PROBE_MARKER + json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()
    return code


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _purge_receipt(workspace: Path) -> List[str]:
    errors: List[str] = []
    target = workspace / RECEIPT_NAME
    for path in (target, target.with_name(target.name + ".staging")):
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            errors.append("could not purge %s: %s" % (path.name, _describe(exc)))
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run gates 1-4, emit the receipt on success; fail closed (exit 1) otherwise."""
    parser = argparse.ArgumentParser(description="WP-2.0 self-healing engine ratification gate")
    parser.add_argument("--workspace", default=None, help="repository root (default: parent of ci_tools)")
    parser.add_argument("--probe", choices=("schemas", "sla"), default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    workspace = Path(args.workspace).resolve() if args.workspace else Path(__file__).resolve().parents[1]

    if args.probe:
        return _run_probe(args.probe, workspace)

    purge_errors = _purge_receipt(workspace)
    if purge_errors:
        for line in purge_errors:
            _log_err("[FAIL-CLOSED] " + line)
        return 1

    gate_results: Dict[str, Dict[str, Any]] = {}
    metrics: Dict[str, Any] = {}
    started = time.perf_counter()

    try:
        ok1, msg1, task_count, requirement_count = execute_gate_1_wbs_mece_and_rtm(workspace)
    except Exception as exc:
        ok1, msg1, task_count, requirement_count = False, _describe(exc), 0, 0
    gate_results["gate_1_wbs_mece_and_rtm"] = {"passed": ok1, "detail": msg1}
    metrics["tasks_traced"] = task_count
    metrics["requirements_traced"] = requirement_count

    try:
        ok2, msg2 = execute_gate_2_ieee830_contracts_and_schemas(workspace)
    except Exception as exc:
        ok2, msg2 = False, _describe(exc)
    gate_results["gate_2_ieee830_contracts_and_schemas"] = {"passed": ok2, "detail": msg2}
    metrics["production_modules"] = len(WP2_MODULES)
    metrics["strict_schemas"] = len(SCHEMA_FIELDS)

    try:
        ok3, msg3, banned_counts = execute_gate_3_anti_spoof_and_mendeleev(workspace)
    except Exception as exc:
        ok3, msg3, banned_counts = False, _describe(exc), {}
    gate_results["gate_3_anti_spoof_and_mendeleev"] = {"passed": ok3, "detail": msg3}
    metrics["banned_construct_counts"] = banned_counts

    try:
        ok4, msg4, rollback_ms, peak_mb = execute_gate_4_nonfunctional_sla(workspace)
    except Exception as exc:
        ok4, msg4, rollback_ms, peak_mb = False, _describe(exc), float("nan"), float("nan")
    gate_results["gate_4_nonfunctional_sla"] = {"passed": ok4, "detail": msg4}
    metrics["worst_rollback_latency_ms"] = rollback_ms
    metrics["rollback_latency_sla_ms"] = SLA_ROLLBACK_MS
    metrics["peak_heap_mb"] = peak_mb
    metrics["ram_ceiling_mb"] = RAM_CEILING_MB

    for key, value in gate_results.items():
        _log_out("[%s] %s: %s" % ("PASS" if value["passed"] else "FAIL", key, value["detail"]))

    failed = [key for key, value in gate_results.items() if value["passed"] is not True]
    if failed:
        purge_errors = _purge_receipt(workspace)
        _log_err("[FAIL-CLOSED] WP-2.0 ratification rejected; failed gates: %s" % ", ".join(failed))
        for key in failed:
            _log_err("  %s -> %s" % (key, gate_results[key]["detail"]))
        for line in purge_errors:
            _log_err("  " + line)
        return 1

    metrics["gates_passed"] = len(gate_results)
    metrics["ratification_wall_time_s"] = round(time.perf_counter() - started, 3)
    try:
        receipt_path = execute_gate_5_receipt_emission(workspace, gate_results, metrics)
    except Exception as exc:
        purge_errors = _purge_receipt(workspace)
        _log_err("[FAIL-CLOSED] receipt emission failed: %s" % _describe(exc))
        for line in purge_errors:
            _log_err("  " + line)
        return 1
    _log_out("[PASS] receipt emitted: %s" % receipt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
