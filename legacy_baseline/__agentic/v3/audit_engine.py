r"""CoChem v3 codebase audit engine.

Static (AST based) auditing utilities that enforce the CoChem engineering
mandates across the sibling repositories (CoChem-BASE, CoChem-TOPOS,
CoChem-TORQ, CoChem-SpycFit):

* **Zero-mock / zero-stub** -- :meth:`CodebaseAuditor.audit_anti_spoofing`
  flags mock-library imports, patch decorators, mock instantiation, pytest's
  monkeypatch fixture, ``raise NotImplementedError`` and pass-only function
  bodies.  Detection is purely syntactic (``ast``), so banned tokens that only
  appear inside string constants are never reported.
* **Mendeleev mass mandate** -- :meth:`CodebaseAuditor.audit_mendeleev_mass_mandate`
  flags hard-coded atomic / isotopic mass tables and scalars; masses must be
  looked up dynamically through ``mendeleev.element(...)``.
* **Physical fixture provenance** --
  :meth:`CodebaseAuditor.verify_physical_fixture_provenance` validates xyz
  geometries, HDF5 fixtures (coordinates + provenance metadata) and flags
  Python modules that synthesise fake geometries / Hessians.
* **Repository integrity** -- :meth:`CodebaseAuditor.audit_repo_integrity`
  checks for a ``pyproject.toml`` manifest and import-intercept files
  (``sitecustomize.py``, ``usercustomize.py``, ``mocks.py``, ``mock_*.py``).

Only the standard library is imported at module import time; ``h5py``,
``numpy`` and ``mendeleev`` are imported lazily where they are needed.
"""
from __future__ import annotations

import ast
import fnmatch
import functools
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

PathLike = Union[str, "os.PathLike[str]"]

DEFAULT_EXTERNAL_REPO_ROOT = Path(r"D:\__CoChem\GitHub-Repo")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"

PRUNE_DIRS = frozenset({
    ".git", ".venv", "venv", "env", "site-packages", "node_modules", ".trash",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
})
INTERCEPT_PATTERNS: Tuple[str, ...] = (
    "sitecustomize.py", "usercustomize.py", "mocks.py", "mock_*.py",
)

# Banned tokens are kept exclusively inside string constants so that this
# module self-audits clean.
MOCK_MODULES = frozenset({"unittest.mock", "mock", "pytest_mock"})
MOCK_CLASS_NAMES = frozenset({
    "MagicMock", "Mock", "PropertyMock", "AsyncMock", "NonCallableMock",
    "NonCallableMagicMock",
})
MOCK_IMPORT_NAMES = MOCK_CLASS_NAMES | frozenset({"patch", "mock", "create_autospec"})
PATCH_DECORATOR_NAME = "patch"
PATCH_SUB_ATTRS = frozenset({"object", "dict", "multiple"})
FIXTURE_INTERCEPT_NAME = "monkey" + "patch"
NOT_IMPL_NAME = "NotImplementedError"
ABSTRACT_BASES = frozenset({"Protocol", "ABC", "ABCMeta"})
ABSTRACT_DECORATORS = frozenset({
    "abstractmethod", "abstractproperty", "abstractclassmethod",
    "abstractstaticmethod", "overload",
})

MASS_TOKENS = frozenset({"MASS", "MASSES", "WEIGHT", "WEIGHTS", "AMU", "DALTON", "DALTONS"})
NON_MASS_TOKENS = frozenset({
    "RADII", "RADIUS", "VALENCE", "VALENCES", "CHARGE", "CHARGES",
    "ELECTRONEGATIVITY", "ELECTRONEGATIVITIES",
})

COORD_NAME_RE = re.compile(r"coord|position|geometry|xyz|hessian|gradient|force", re.IGNORECASE)
H5_COORD_NAME_RE = re.compile(r"coord|position|geometry|xyz", re.IGNORECASE)
SYNTHETIC_NUMPY_FUNCS = frozenset({
    "zeros", "ones", "zeros_like", "ones_like", "full", "full_like",
    "empty", "empty_like", "eye", "identity",
})
TRIG_FUNCS = frozenset({"sin", "cos"})
TRIG_MODULES = frozenset({"math", "np", "numpy", ""})
REQUIRED_H5_ATTRS: Tuple[str, ...] = ("units", "method", "basis", "source")
DEGENERATE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class AuditViolation:
    """A single audit finding located in a source or fixture file."""

    file_path: str
    line: int
    col: int
    severity: str
    category: str
    symbol: str
    message: str


# ── Generic helpers ──────────────────────────────────────────────────────────
def _dotted_name(node: ast.AST) -> str:
    """Return the dotted name of a Name/Attribute chain ("" if not a chain)."""
    parts: List[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def _name_tokens(name: str) -> Set[str]:
    """Split an identifier into upper-case tokens (snake_case and camelCase)."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return {tok.upper() for tok in re.split(r"[^A-Za-z0-9]+", spaced) if tok}


def _is_number_node(node: ast.AST) -> bool:
    """True for int/float literals, optionally with a unary sign."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_number_node(node.operand)
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


def _number_value(node: ast.AST) -> float:
    """Evaluate a numeric literal node accepted by :func:`_is_number_node`."""
    if isinstance(node, ast.UnaryOp):
        inner = _number_value(node.operand)
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.Constant):
        return float(node.value)
    raise TypeError(f"not a numeric literal: {ast.dump(node)}")


def _read_source(path: Path) -> str:
    """Read a Python source file as UTF-8 text (BOM stripped)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return text[1:] if text.startswith("\ufeff") else text


def _iter_python_files(root: Path) -> Iterator[Path]:
    """Yield *.py files below ``root`` (sorted), pruning tool/venv folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def _sort_unique(violations: Iterable[AuditViolation]) -> List[AuditViolation]:
    """Deduplicate violations and sort them by (file, line, col, category)."""
    unique = set(violations)
    return sorted(unique, key=lambda v: (v.file_path, v.line, v.col, v.category, v.symbol))


@functools.lru_cache(maxsize=512)
def _canonical_element(token: str) -> Optional[str]:
    """Resolve an element symbol or atomic number via mendeleev.

    Returns the canonical symbol (e.g. ``"He"``) or None when the token is not
    a known element.  Raises ImportError when mendeleev is unavailable.
    """
    import mendeleev  # lazy: heavy import, only needed for xyz validation

    token = token.strip()
    if not token:
        return None
    try:
        if token.isdigit():
            elem = mendeleev.element(int(token))
        else:
            match = re.match(r"^([A-Za-z]{1,3})", token)
            if match is None:
                return None
            elem = mendeleev.element(match.group(1).capitalize())
    except Exception:  # mendeleev raises several error types for unknown symbols
        return None
    symbol = getattr(elem, "symbol", None)
    return str(symbol) if symbol else None


# ── Anti-spoofing visitor ────────────────────────────────────────────────────
class _AntiSpoofVisitor(ast.NodeVisitor):
    """Collect zero-mock / zero-stub violations for one parsed module."""

    def __init__(self, file_path: str) -> None:
        """Initialise the visitor for ``file_path``."""
        self.file_path = file_path
        self.violations: List[AuditViolation] = []
        self._class_stack: List[bool] = []  # True when class is abstract/protocol

    def _add(self, node: ast.AST, category: str, symbol: str, message: str) -> None:
        """Record a CRITICAL violation located at ``node``."""
        self.violations.append(AuditViolation(
            file_path=self.file_path,
            line=int(getattr(node, "lineno", 1)),
            col=int(getattr(node, "col_offset", 0)),
            severity=SEVERITY_CRITICAL,
            category=category,
            symbol=symbol,
            message=message,
        ))

    @staticmethod
    def _is_mock_module(name: str) -> bool:
        """True if ``name`` is a mock package or one of its submodules."""
        return any(name == mod or name.startswith(mod + ".") for mod in MOCK_MODULES)

    def visit_Import(self, node: ast.Import) -> None:
        """Flag ``import unittest.mock`` style imports."""
        for alias in node.names:
            if self._is_mock_module(alias.name):
                self._add(node, "MOCK_IMPORT", alias.name,
                          f"import of mock library '{alias.name}' is forbidden")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Flag ``from unittest.mock import ...`` and mock-name imports."""
        module = node.module or ""
        if node.level == 0 and self._is_mock_module(module):
            self._add(node, "MOCK_IMPORT", module,
                      f"import from mock library '{module}' is forbidden")
        for alias in node.names:
            if alias.name in MOCK_IMPORT_NAMES and (
                    self._is_mock_module(module) or module == "unittest"
                    or alias.name in MOCK_CLASS_NAMES):
                self._add(node, "MOCK_IMPORT", alias.name,
                          f"import of mock object '{alias.name}' is forbidden")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track whether methods belong to an abstract / Protocol class."""
        abstract = any(_dotted_name(base).split(".")[-1] in ABSTRACT_BASES
                       for base in node.bases)
        abstract = abstract or any(
            kw.arg == "metaclass" and _dotted_name(kw.value).split(".")[-1] in ABSTRACT_BASES
            for kw in node.keywords)
        for deco in node.decorator_list:
            self._check_decorator(deco)
            self.visit(deco)
        for base in node.bases:
            self.visit(base)
        self._class_stack.append(abstract)
        for stmt in node.body:
            self.visit(stmt)
        self._class_stack.pop()

    def _check_decorator(self, deco: ast.AST) -> None:
        """Flag patch-style decorators (``@patch``, ``@x.patch.object``...)."""
        target = deco.func if isinstance(deco, ast.Call) else deco
        dotted = _dotted_name(target)
        if not dotted:
            return
        parts = dotted.split(".")
        is_patch = parts[-1] == PATCH_DECORATOR_NAME or (
            len(parts) >= 2 and parts[-1] in PATCH_SUB_ATTRS
            and parts[-2] == PATCH_DECORATOR_NAME)
        if is_patch:
            self._add(deco, "MOCK_PATCH_DECORATOR", dotted,
                      f"patch decorator '@{dotted}' replaces real behaviour")

    def _visit_function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        """Check a function definition for stubs, patching and fixtures."""
        for deco in node.decorator_list:
            self._check_decorator(deco)
        all_args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        if node.args.vararg is not None:
            all_args.append(node.args.vararg)
        if node.args.kwarg is not None:
            all_args.append(node.args.kwarg)
        for arg in all_args:
            if arg.arg == FIXTURE_INTERCEPT_NAME:
                self._add(node, "MONKEYPATCH", arg.arg,
                          f"function '{node.name}' requests the pytest "
                          f"{FIXTURE_INTERCEPT_NAME} fixture")
        if self._is_stub_body(node.body) and not self._is_exempt(node):
            self._add(node, "EMPTY_PASS_STUB", node.name,
                      f"function '{node.name}' has an empty pass/ellipsis body")
        # Definitions nested inside a function are never abstract class members.
        self._class_stack.append(False)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Delegate to the shared function checker."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Delegate to the shared function checker."""
        self._visit_function(node)

    @staticmethod
    def _is_stub_body(body: Sequence[ast.stmt]) -> bool:
        """True if ``body`` (minus a leading docstring) is only pass / ``...``."""
        stmts = list(body)
        if (stmts and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)):
            stmts = stmts[1:]
        if not stmts:
            return True
        for stmt in stmts:
            if isinstance(stmt, ast.Pass):
                continue
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is Ellipsis):
                continue
            return False
        return True

    def _is_exempt(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> bool:
        """Abstract / Protocol members and @overload signatures may be empty."""
        if self._class_stack and self._class_stack[-1]:
            return True
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            if _dotted_name(target).split(".")[-1] in ABSTRACT_DECORATORS:
                return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        """Flag instantiation of mock objects."""
        dotted = _dotted_name(node.func)
        if dotted and dotted.split(".")[-1] in MOCK_CLASS_NAMES:
            self._add(node, "MOCK_INSTANTIATION", dotted,
                      f"mock object '{dotted}()' instantiated")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Flag every load of the monkeypatch fixture."""
        if node.id == FIXTURE_INTERCEPT_NAME and isinstance(node.ctx, ast.Load):
            self._add(node, "MONKEYPATCH", node.id,
                      f"use of pytest {FIXTURE_INTERCEPT_NAME} fixture")

    def visit_Raise(self, node: ast.Raise) -> None:
        """Flag ``raise NotImplementedError`` (bare or called)."""
        exc = node.exc
        target = exc.func if isinstance(exc, ast.Call) else exc
        if target is not None and _dotted_name(target).split(".")[-1] == NOT_IMPL_NAME:
            self._add(node, "NOT_IMPLEMENTED_ERROR", NOT_IMPL_NAME,
                      "unimplemented code path raises " + NOT_IMPL_NAME)
        self.generic_visit(node)


# ── Mass-mandate helpers ─────────────────────────────────────────────────────
def _target_names(target: ast.AST) -> List[Tuple[str, ast.AST]]:
    """Return (name, node) pairs for simple assignment targets."""
    if isinstance(target, ast.Name):
        return [(target.id, target)]
    if isinstance(target, ast.Attribute):
        return [(target.attr, target)]
    if isinstance(target, (ast.Tuple, ast.List)):
        pairs: List[Tuple[str, ast.AST]] = []
        for elt in target.elts:
            pairs.extend(_target_names(elt))
        return pairs
    return []


def _is_mass_name(name: str) -> bool:
    """True when ``name`` denotes a mass/weight and not a radius/valence table."""
    tokens = _name_tokens(name)
    return bool(tokens & MASS_TOKENS) and not (tokens & NON_MASS_TOKENS)


def _numeric_container(node: ast.AST) -> bool:
    """True for tuples/lists of numeric literals."""
    return (isinstance(node, (ast.Tuple, ast.List)) and bool(node.elts)
            and all(_is_number_node(e) for e in node.elts))


def _hardcoded_mass_value(node: ast.AST) -> Optional[str]:
    """Describe a hard-coded mass value, or None when the value is dynamic.

    Dict literals whose values are all numeric literals, and non-trivial
    numeric scalars, count as hard-coded.  Exact 0 / +-1 scalars are treated
    as accumulator / unit initialisers rather than physical masses.
    """
    if isinstance(node, ast.Dict):
        values = [v for v in node.values if v is not None]
        if values and all(_is_number_node(v) or _numeric_container(v) for v in values):
            return f"dict literal with {len(values)} numeric mass entries"
        return None
    if _is_number_node(node):
        value = _number_value(node)
        if value in (0.0, 1.0, -1.0):
            return None
        return f"numeric literal {value!r}"
    return None


# ── Semantic spoof helpers ───────────────────────────────────────────────────
def _is_synthetic_numpy_call(node: ast.AST) -> Optional[str]:
    """Return the dotted numpy call name if ``node`` synthesises a placeholder array."""
    if not isinstance(node, ast.Call):
        return None
    parts = _dotted_name(node.func).split(".")
    if len(parts) < 2 or parts[0] not in ("np", "numpy"):
        return None
    if len(parts) == 2 and parts[1] in SYNTHETIC_NUMPY_FUNCS:
        return ".".join(parts)
    if parts[1] == "random":
        return ".".join(parts)
    return None


def _contains_trig(node: ast.AST) -> bool:
    """True if ``node`` contains a sin/cos call from math / numpy (or bare)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            parts = _dotted_name(sub.func).split(".")
            module = ".".join(parts[:-1])
            if parts[-1] in TRIG_FUNCS and module in TRIG_MODULES:
                return True
    return False


def _base_name(node: ast.AST) -> str:
    """Return the root identifier of a Name / Attribute / Subscript target."""
    if isinstance(node, ast.Attribute):
        return node.attr
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    if isinstance(current, ast.Attribute):
        return current.attr
    return ""


class _SemanticSpoofVisitor(ast.NodeVisitor):
    """Find synthetic geometry / Hessian generators in a Python fixture."""

    def __init__(self, file_path: str) -> None:
        """Initialise the visitor for ``file_path``."""
        self.file_path = file_path
        self.violations: List[AuditViolation] = []
        self._function_depth = 0

    def _add(self, node: ast.AST, symbol: str, message: str) -> None:
        """Record a CRITICAL SEMANTIC_SPOOF violation."""
        self.violations.append(AuditViolation(
            file_path=self.file_path, line=int(getattr(node, "lineno", 1)),
            col=int(getattr(node, "col_offset", 0)), severity=SEVERITY_CRITICAL,
            category="SEMANTIC_SPOOF", symbol=symbol, message=message))

    def _enter_function(self, node: ast.AST) -> None:
        """Visit a function body while tracking function nesting depth."""
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track function scope."""
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Track function scope."""
        self._enter_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Track function scope."""
        self._enter_function(node)

    def _check_module_assignment(self, targets: Sequence[ast.AST], value: Optional[ast.AST],
                                 node: ast.AST) -> None:
        """Flag module-level coordinate-named placeholder numpy arrays."""
        if self._function_depth or value is None:
            return
        func = _is_synthetic_numpy_call(value)
        if func is None:
            return
        for target in targets:
            for name, _ in _target_names(target):
                if COORD_NAME_RE.search(name):
                    self._add(node, name,
                              f"module-level '{name}' synthesised with {func}() "
                              "instead of physically sourced data")

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check plain assignments."""
        self._check_module_assignment(node.targets, node.value, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check annotated assignments."""
        self._check_module_assignment([node.target], node.value, node)
        self.generic_visit(node)

    def _check_loop(self, loop: Union[ast.For, ast.AsyncFor, ast.While]) -> None:
        """Flag loops that build coordinate arrays from sin/cos expressions."""
        hits: List[Tuple[ast.AST, str]] = []
        for sub in ast.walk(loop):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in ("append", "extend", "insert")):
                target = _base_name(sub.func.value)
                if COORD_NAME_RE.search(target) and any(_contains_trig(a) for a in sub.args):
                    hits.append((sub, target))
            elif isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                if sub.value is None or not _contains_trig(sub.value):
                    continue
                for target in targets:
                    name = _base_name(target)
                    if COORD_NAME_RE.search(name):
                        hits.append((sub, name))
        if hits:
            first_symbol = hits[0][1]
            self._add(loop, first_symbol,
                      f"loop generates synthetic '{first_symbol}' from sin/cos")
            for sub, name in hits:
                self._add(sub, name, f"synthetic trigonometric geometry written to '{name}'")

    def visit_For(self, node: ast.For) -> None:
        """Check for-loops."""
        self._check_loop(node)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Check async for-loops."""
        self._check_loop(node)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """Check while-loops."""
        self._check_loop(node)
        self.generic_visit(node)


# ── Public auditor ───────────────────────────────────────────────────────────
class CodebaseAuditor:
    """Audit CoChem repositories for spoofing, mass-table and provenance issues."""

    def __init__(self, repo_root: Optional[PathLike] = None,
                 amnesty_file: Optional[PathLike] = None) -> None:
        """Create an auditor.

        Args:
            repo_root: optional directory containing the CoChem repositories
                (or a repository itself) used during repo-name resolution and
                as the base for amnesty path matching.
            amnesty_file: optional JSON list or newline separated text file of
                relative paths / glob patterns that are skipped by scans.
        """
        self.repo_root: Optional[Path] = Path(repo_root) if repo_root is not None else None
        self.amnesty_file: Optional[Path] = Path(amnesty_file) if amnesty_file is not None else None
        self.amnesty_patterns: Tuple[str, ...] = self._load_amnesty(self.amnesty_file)

    # ── amnesty ──
    @staticmethod
    def _load_amnesty(path: Optional[Path]) -> Tuple[str, ...]:
        """Load amnesty patterns; a missing file yields an empty tuple."""
        if path is None or not path.is_file():
            return tuple()
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        entries: List[str] = []
        stripped = text.strip()
        if stripped.startswith("["):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, list):
                entries = [str(item) for item in data]
        if not entries:
            entries = [ln.strip() for ln in text.splitlines()]
        cleaned = [e.replace("\\", "/").strip() for e in entries]
        return tuple(e for e in cleaned if e and not e.startswith("#"))

    def _is_amnestied(self, file_path: Path, scan_root: Optional[Path]) -> bool:
        """True when ``file_path`` matches an amnesty pattern."""
        if not self.amnesty_patterns:
            return False
        candidates = {file_path.name, file_path.as_posix()}
        resolved = file_path.resolve()
        for base in (scan_root, self.repo_root):
            if base is None:
                continue
            try:
                candidates.add(resolved.relative_to(Path(base).resolve()).as_posix())
            except ValueError:
                continue
        for pat in self.amnesty_patterns:
            prefix = pat.rstrip("/")
            for cand in candidates:
                if fnmatch.fnmatch(cand, pat) or cand == prefix or cand.startswith(prefix + "/"):
                    return True
        return False

    def _collect_py_targets(self, target: PathLike) -> List[Path]:
        """Expand a file or directory into the list of Python files to scan."""
        path = Path(target)
        if path.is_dir():
            return [f for f in _iter_python_files(path) if not self._is_amnestied(f, path)]
        if path.is_file():
            return [] if self._is_amnestied(path, path.parent) else [path]
        raise FileNotFoundError(f"audit target does not exist: {path}")

    @staticmethod
    def _parse(path: Path) -> Tuple[Optional[ast.AST], Optional[AuditViolation]]:
        """Parse ``path``; on failure return a PARSE_ERROR violation."""
        try:
            return ast.parse(_read_source(path), filename=str(path)), None
        except (SyntaxError, ValueError) as exc:
            line = int(getattr(exc, "lineno", None) or 1)
            col = max(int(getattr(exc, "offset", None) or 1) - 1, 0)
            return None, AuditViolation(
                file_path=str(path), line=line, col=col, severity=SEVERITY_HIGH,
                category="PARSE_ERROR", symbol=path.name,
                message=f"cannot parse source: {exc}")

    # ── repo resolution ──
    def resolve_repo_path(self, repo_name: str) -> Path:
        """Locate a CoChem repository directory by name.

        Search order: the canonical external root, the COCHEM_REPO_DIR /
        COCHEM_ROOT environment variables, the ``repo_root`` constructor
        argument, then parents of the workspace (``name`` or
        ``GitHub-Repo/name``).  Falls back to ``WORKSPACE/name``.
        """
        name = str(repo_name)
        external = DEFAULT_EXTERNAL_REPO_ROOT / name
        if external.is_dir():
            return external
        candidates: List[Path] = []
        repo_dir_env = os.environ.get("COCHEM_REPO_DIR")
        if repo_dir_env:
            candidates.append(Path(repo_dir_env) / name)
        root_env = os.environ.get("COCHEM_ROOT")
        if root_env:
            candidates.extend([Path(root_env) / name, Path(root_env) / "GitHub-Repo" / name])
        if self.repo_root is not None:
            if self.repo_root.name == name:
                candidates.append(self.repo_root)
            candidates.extend([self.repo_root / name, self.repo_root / "GitHub-Repo" / name])
        for cand in candidates:
            if cand.is_dir():
                return cand
        for parent in (WORKSPACE_ROOT, *WORKSPACE_ROOT.parents):
            for cand in (parent / name, parent / "GitHub-Repo" / name):
                if cand.is_dir():
                    return cand
        return WORKSPACE_ROOT / name

    # ── anti spoofing ──
    def audit_anti_spoofing(self, target: PathLike) -> List[AuditViolation]:
        """AST-scan a file or directory for mocks, patching and stub code."""
        violations: List[AuditViolation] = []
        for path in self._collect_py_targets(target):
            tree, error = self._parse(path)
            if error is not None:
                violations.append(error)
                continue
            visitor = _AntiSpoofVisitor(str(path))
            visitor.visit(tree)
            violations.extend(visitor.violations)
        return _sort_unique(violations)

    # ── mendeleev mandate ──
    def audit_mendeleev_mass_mandate(self, target: PathLike) -> List[AuditViolation]:
        """Flag hard-coded atomic / isotopic masses (use mendeleev instead)."""
        violations: List[AuditViolation] = []
        for path in self._collect_py_targets(target):
            tree, error = self._parse(path)
            if error is not None:
                violations.append(error)
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    targets, value = list(node.targets), node.value
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets, value = [node.target], node.value
                else:
                    continue
                description = _hardcoded_mass_value(value)
                if description is None:
                    continue
                for tgt in targets:
                    for name, _ in _target_names(tgt):
                        if _is_mass_name(name):
                            violations.append(AuditViolation(
                                file_path=str(path), line=node.lineno, col=node.col_offset,
                                severity=SEVERITY_CRITICAL, category="MENDELEEV_VIOLATION",
                                symbol=name,
                                message=(f"'{name}' hard-codes masses ({description}); "
                                         "use mendeleev.element(symbol) lookups")))
        return _sort_unique(violations)

    # ── fixture provenance ──
    def verify_physical_fixture_provenance(self, path: PathLike) -> Dict[str, Any]:
        """Validate that a physical fixture (xyz / h5 / py) is authentic."""
        fixture = Path(path)
        suffix = fixture.suffix.lower().lstrip(".")
        fmt = {"xyz": "xyz", "h5": "h5", "hdf5": "h5", "py": "py"}.get(suffix, suffix)
        if not fixture.is_file():
            violation = self._fixture_violation(fixture, 1, "FIXTURE_MISSING", fixture.name,
                                                f"fixture file not found: {fixture}")
            return {"valid": False, "format": fmt, "violations": [violation],
                    "path": str(fixture)}
        if fmt == "xyz":
            result = self._verify_xyz(fixture)
        elif fmt == "h5":
            result = self._verify_h5(fixture)
        elif fmt == "py":
            result = self._verify_py(fixture)
        else:
            result = {"violations": [self._fixture_violation(
                fixture, 1, "UNSUPPORTED_FORMAT", fixture.suffix or fixture.name,
                f"unsupported fixture format '{fixture.suffix}'")]}
        result["violations"] = _sort_unique(result["violations"])
        result["format"] = fmt
        result["valid"] = not result["violations"]
        result["path"] = str(fixture)
        return result

    @staticmethod
    def _fixture_violation(path: Path, line: int, category: str, symbol: str,
                           message: str, severity: str = SEVERITY_CRITICAL) -> AuditViolation:
        """Build a fixture violation record."""
        return AuditViolation(file_path=str(path), line=line, col=0, severity=severity,
                              category=category, symbol=symbol, message=message)

    def _parse_xyz_frame(self, path: Path, lines: List[str], start: int,
                         violations: List[AuditViolation]
                         ) -> Tuple[int, List[str], List[Tuple[float, float, float]]]:
        """Parse one xyz frame beginning at index ``start``.

        Returns (next_index, elements, coordinates); problems are appended to
        ``violations``.
        """
        header = lines[start].strip()
        try:
            count = int(header.split()[0])
        except (ValueError, IndexError):
            violations.append(self._fixture_violation(
                path, start + 1, "FIXTURE_INVALID", header[:40],
                f"xyz atom-count line is not an integer: {header!r}"))
            return len(lines), [], []
        if count < 1:
            violations.append(self._fixture_violation(
                path, start + 1, "FIXTURE_INVALID", header,
                f"xyz atom count must be positive, got {count}"))
        # Atom lines: following non-blank lines, stopping at the next frame header.
        idx = start + 2
        atom_lines: List[Tuple[int, str]] = []
        while idx < len(lines) and len(atom_lines) < max(count, 0):
            if lines[idx].strip():
                if re.fullmatch(r"\s*\d+\s*", lines[idx]):
                    break
                atom_lines.append((idx, lines[idx]))
            idx += 1
        if len(atom_lines) != count:
            violations.append(self._fixture_violation(
                path, start + 1, "FIXTURE_INVALID", str(count),
                f"xyz header declares {count} atoms but {len(atom_lines)} atom lines found"))
        elements: List[str] = []
        coords: List[Tuple[float, float, float]] = []
        for line_idx, raw in atom_lines:
            parts = raw.split()
            if len(parts) < 4:
                violations.append(self._fixture_violation(
                    path, line_idx + 1, "FIXTURE_INVALID", raw.strip()[:40],
                    "xyz atom line needs a symbol and three coordinates"))
                continue
            token = parts[0]
            try:
                symbol = _canonical_element(token)
            except ImportError as exc:
                violations.append(self._fixture_violation(
                    path, line_idx + 1, "MENDELEEV_UNAVAILABLE", token,
                    f"cannot validate element symbols without mendeleev: {exc}",
                    severity=SEVERITY_HIGH))
                symbol = token
            if symbol is None:
                violations.append(self._fixture_violation(
                    path, line_idx + 1, "FIXTURE_INVALID", token,
                    f"unknown element symbol {token!r}"))
                symbol = token
            elements.append(symbol)
            try:
                xyz = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError:
                violations.append(self._fixture_violation(
                    path, line_idx + 1, "FIXTURE_INVALID", token,
                    f"unparseable coordinates: {' '.join(parts[1:4])!r}"))
                continue
            if not all(math.isfinite(c) for c in xyz):
                violations.append(self._fixture_violation(
                    path, line_idx + 1, "FIXTURE_INVALID", token, "non-finite coordinate value"))
                continue
            coords.append(xyz)
        return idx, elements, coords

    def _verify_xyz(self, path: Path) -> Dict[str, Any]:
        """Validate an xyz geometry file (count, elements, non-degeneracy)."""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if text.startswith("\ufeff"):
            text = text[1:]
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        violations: List[AuditViolation] = []
        if not lines:
            violations.append(self._fixture_violation(path, 1, "FIXTURE_INVALID", path.name,
                                                      "empty xyz file"))
            return {"violations": violations, "atom_count": 0, "elements": [],
                    "comment": "", "frames": 0}
        comment = lines[1] if len(lines) > 1 else ""
        next_idx, elements, coords = self._parse_xyz_frame(path, lines, 0, violations)
        frames = 1
        # Additional frames (trajectory files) must be complete and consistent.
        while next_idx < len(lines):
            if not lines[next_idx].strip():
                next_idx += 1
                continue
            frame_start = next_idx
            next_idx, frame_elems, _ = self._parse_xyz_frame(path, lines, next_idx, violations)
            frames += 1
            if frame_elems and frame_elems != elements:
                violations.append(self._fixture_violation(
                    path, frame_start + 1, "FIXTURE_INVALID", "frame",
                    f"xyz frame {frames} has a different composition"))
        if len(coords) > 1:
            x0, y0, z0 = coords[0]
            spread = max(max(abs(x - x0), abs(y - y0), abs(z - z0)) for x, y, z in coords)
            if spread <= DEGENERATE_TOLERANCE:
                violations.append(self._fixture_violation(
                    path, 3, "FIXTURE_INVALID", "geometry",
                    f"degenerate geometry: all {len(coords)} atoms coincide"))
        try:
            declared = int(lines[0].split()[0])
        except (ValueError, IndexError):
            declared = len(elements)
        return {"violations": violations, "atom_count": declared, "elements": elements,
                "comment": comment, "frames": frames}

    def _verify_h5(self, path: Path) -> Dict[str, Any]:
        """Validate an HDF5 fixture: real coordinates plus provenance attrs."""
        import h5py  # lazy import
        import numpy as np  # lazy import

        violations: List[AuditViolation] = []
        dataset_info: Dict[str, Dict[str, Any]] = {}
        metadata: Dict[str, Any] = {}
        try:
            handle = h5py.File(path, "r")
        except OSError as exc:
            violations.append(self._fixture_violation(path, 1, "FIXTURE_INVALID", path.name,
                                                      f"cannot open HDF5 file: {exc}"))
            return {"violations": violations, "datasets": [], "metadata": {}}
        with handle:
            for key, value in handle.attrs.items():
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                elif hasattr(value, "tolist"):
                    value = value.tolist()
                metadata[str(key)] = value
            found: List[Tuple[str, Any]] = []

            def _collect(name: str, obj: Any) -> None:
                """visititems callback recording every dataset."""
                if isinstance(obj, h5py.Dataset):
                    found.append((name, obj))
                    dataset_info[name] = {"shape": tuple(obj.shape), "dtype": str(obj.dtype)}

            handle.visititems(_collect)
            coord_sets = [(n, d) for n, d in found
                          if H5_COORD_NAME_RE.search(n.rsplit("/", 1)[-1])]
            if not coord_sets:
                coord_sets = [(n, d) for n, d in found if d.dtype.kind == "f"]
            if not coord_sets:
                violations.append(self._fixture_violation(
                    path, 1, "FIXTURE_INVALID", "coordinates",
                    "no coordinate dataset found in HDF5 fixture"))
            for name, ds in coord_sets:
                try:
                    data = np.asarray(ds[()], dtype=np.float64)
                except (TypeError, ValueError) as exc:
                    violations.append(self._fixture_violation(
                        path, 1, "FIXTURE_INVALID", name,
                        f"dataset '{name}' is not numeric: {exc}"))
                    continue
                if data.size == 0:
                    violations.append(self._fixture_violation(
                        path, 1, "FIXTURE_INVALID", name, f"dataset '{name}' is empty"))
                elif not bool(np.all(np.isfinite(data))):
                    violations.append(self._fixture_violation(
                        path, 1, "FIXTURE_INVALID", name,
                        f"dataset '{name}' contains non-finite values"))
                elif not bool(np.any(data)):
                    violations.append(self._fixture_violation(
                        path, 1, "SEMANTIC_SPOOF", name,
                        f"dataset '{name}' is all zeros (placeholder geometry)"))
        for attr in REQUIRED_H5_ATTRS:
            value = metadata.get(attr)
            if value is None or (isinstance(value, str) and not value.strip()):
                violations.append(self._fixture_violation(
                    path, 1, "PROVENANCE_MISSING", attr,
                    f"HDF5 root attribute '{attr}' (provenance metadata) is missing"))
        return {"violations": violations, "datasets": sorted(dataset_info),
                "metadata": metadata, "dataset_info": dataset_info}

    def _verify_py(self, path: Path) -> Dict[str, Any]:
        """Flag Python fixtures that synthesise geometries / Hessians."""
        tree, error = self._parse(path)
        if error is not None:
            return {"violations": [error]}
        visitor = _SemanticSpoofVisitor(str(path))
        visitor.visit(tree)
        return {"violations": visitor.violations}

    # ── repo integrity ──
    def audit_repo_integrity(self, repo_name_or_path: PathLike) -> Dict[str, Any]:
        """Check a repository for a manifest and import-intercept / mock files."""
        if isinstance(repo_name_or_path, Path):
            repo = repo_name_or_path
        else:
            candidate = Path(os.fspath(repo_name_or_path))
            repo = candidate if candidate.is_dir() else self.resolve_repo_path(str(candidate))
        intercepts: List[Path] = []
        if repo.is_dir():
            for dirpath, dirnames, filenames in os.walk(repo):
                dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS)
                for fn in sorted(filenames):
                    if any(fnmatch.fnmatch(fn, pat) for pat in INTERCEPT_PATTERNS):
                        hit = Path(dirpath) / fn
                        if not self._is_amnestied(hit, repo):
                            intercepts.append(hit)
        has_pyproject = (repo / "pyproject.toml").is_file()
        has_conftest = (repo / "conftest.py").is_file()
        return {
            "repo_path": repo,
            "exists": repo.is_dir(),
            "has_pyproject": has_pyproject,
            "has_conftest": has_conftest,
            "mock_intercept_files": [str(p) for p in intercepts],
            "compliant": bool(has_pyproject and not intercepts),
        }
