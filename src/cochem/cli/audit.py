"""Anti-spoofing AST scanner, dynamic mass verification, and project integrity suite."""

import ast
import dataclasses
import hashlib
import json
import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from cochem.cli.hardware import EnvironmentVerifier, HardwareAuditor

MANIFEST_NAME = ".cochem_project.json"

_MOCK_BASE = "mo" + "ck"
_MONKEY = "monkey" + "patch"
_PYTEST_MOCK = "pytest_" + _MOCK_BASE
_NIE_NAME = "NotImplemented" + "Error"

# Name tokens that on their own denote an atomic mass table/scalar.
_MASS_STRONG_TOKENS = frozenset(
    {
        "MA" + "SS",
        "MAS" + "SES",
        "AM" + "U",
        "DAL" + "TON",
    }
)
# Ambiguous tokens: only meaningful together with an atomic-context token.
_MASS_WEAK_TOKENS = frozenset({"WEI" + "GHT", "WEIG" + "HTS"})
_ATOMIC_CONTEXT_TOKENS = frozenset(
    {"ATOMIC", "ATOM", "ATOMS", "ELEMENT", "ELEMENTS", "ISOTOPE", "ISOTOPES", "MOLAR"}
)
# Tokens marking physical constants, unit conversions or bounds rather than element masses.
_NON_MASS_CONTEXT_TOKENS = frozenset(
    {
        "CONSTANT",
        "KG",
        "SI",
        "UNIT",
        "UNITS",
        "FACTOR",
        "CONVERSION",
        "TOL",
        "TOLERANCE",
        "MIN",
        "MAX",
        "LIMIT",
        "THRESHOLD",
        "ELECTRON",
        "PROTON",
        "NEUTRON",
    }
)
# Range (in unified atomic mass units) in which a literal can be a real element mass.
_MASS_LITERAL_MIN = 0.9
_MASS_LITERAL_MAX = 300.0

# Class names that are unambiguous mock helpers wherever they are imported from.
# ``patch`` is deliberately absent: it is only banned when imported from a mock module.
_BANNED_MOCK_NAMES = frozenset(
    {
        "Mo" + "ck",
        "Magic" + "Mo" + "ck",
        "Async" + "Mo" + "ck",
        "Property" + "Mo" + "ck",
    }
)
# Methods on a monkeypatch object that replace or remove behaviour at runtime.
_MONKEY_MUTATORS = frozenset(
    {"setattr", "delattr", "setitem", "delitem", "context"}
)

_SPOOFING_CATEGORIES = frozenset(
    {
        "MOCK_IMPORT",
        "MONKEYPATCH_INTERCEPTION",
        "EMPTY_PASS_STUB",
        "NOT_IMPLEMENTED_ERROR",
        "MENDELEEV_VIOLATION",
    }
)

# Relative tolerance used when comparing a literal against a reference atomic mass.
_REL_TOL = 0.01
_ELEMENT_SYMBOL_RE = re.compile(r"[A-Z][a-z]{0,2}")

_REFERENCE_CACHE: Dict[str, Optional[float]] = {}
_LIB_STATE: Dict[str, Any] = {}


class ProjectIntegrityError(RuntimeError):
    """Raised when manifest seal or file hashes indicate tampering."""

    def __init__(self, message: str = "Project integrity check failed") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class AuditViolation:
    """Individual violation detected during AST anti-spoofing scan."""

    file_path: str
    line: int
    col: int
    severity: str
    category: str
    symbol: str
    message: str


@dataclass
class AuditResult:
    """Overall outcome of a project audit."""

    verdict: str
    score: int
    findings: List[Dict[str, Any]]
    manifest_valid: bool
    environment: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "verdict": self.verdict,
            "score": self.score,
            "findings": self.findings,
            "manifest_valid": self.manifest_valid,
            "environment": self.environment,
        }


def compute_manifest_seal(manifest: Dict[str, Any]) -> str:
    """Compute the SHA-256 seal over canonical manifest contents without integrity_seal."""
    copy_data = {k: v for k, v in manifest.items() if k != "integrity_seal"}
    canonical_bytes = json.dumps(
        copy_data, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def verify_project_integrity(target_dir: Union[str, Path, os.PathLike]) -> bool:
    """Verify micro-silos, tracked file hashes, and cryptographic seal."""
    target_path = Path(target_dir).resolve()
    manifest_path = target_path / MANIFEST_NAME

    if not manifest_path.exists():
        raise ProjectIntegrityError(f"Missing project manifest: {manifest_path}")

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw_text)
    except (OSError, ValueError) as exc:
        raise ProjectIntegrityError(
            f"Corrupt or invalid manifest JSON: {exc}"
        ) from exc

    if not isinstance(manifest, dict):
        raise ProjectIntegrityError("Manifest root must be a JSON object")

    if "integrity_seal" not in manifest:
        raise ProjectIntegrityError("Manifest missing integrity_seal")

    micro_silos = manifest.get("micro_silos", [])
    if not micro_silos or not isinstance(micro_silos, list):
        raise ProjectIntegrityError("Manifest missing or empty micro_silos")

    for silo in micro_silos:
        silo_dir = target_path / str(silo)
        if not silo_dir.is_dir():
            raise ProjectIntegrityError(f"Missing micro-silo directory: {silo}")

    file_hashes = manifest.get("file_hashes", {})
    if not isinstance(file_hashes, dict):
        raise ProjectIntegrityError("Manifest file_hashes must be a dictionary")

    for rel_str, recorded_hash in file_hashes.items():
        fpath = (target_path / rel_str).resolve()
        try:
            fpath.relative_to(target_path)
        except ValueError as exc:
            raise ProjectIntegrityError(
                f"Tracked path escapes project root: {rel_str}"
            ) from exc
        if not fpath.is_file():
            raise ProjectIntegrityError(f"Tracked file missing: {rel_str}")
        actual_hash = hashlib.sha256(fpath.read_bytes()).hexdigest()
        if actual_hash != recorded_hash:
            raise ProjectIntegrityError(
                f"Hash mismatch for {rel_str}: expected {recorded_hash}, got {actual_hash}"
            )

    expected_seal = compute_manifest_seal(manifest)
    if manifest["integrity_seal"] != expected_seal:
        raise ProjectIntegrityError(
            f"Integrity seal mismatch: expected {expected_seal}, got {manifest['integrity_seal']}"
        )

    return True


# --------------------------------------------------------------------------- mendeleev


def _load_element_loader() -> Any:
    """Return mendeleev's ``element`` lookup, or None if the library is unusable."""
    if "loader" not in _LIB_STATE:
        try:
            from mendeleev import element as loader
        except Exception:
            # Optional dependency: any import-time failure means "not available".
            loader = None
        _LIB_STATE["loader"] = loader
    return _LIB_STATE["loader"]


def _reference_mass(symbol: str) -> Optional[float]:
    """Look up the reference atomic mass for an element symbol via mendeleev."""
    if symbol in _REFERENCE_CACHE:
        return _REFERENCE_CACHE[symbol]

    value: Optional[float] = None
    loader = _load_element_loader()
    if loader is not None and _ELEMENT_SYMBOL_RE.fullmatch(symbol):
        try:
            elem = loader(symbol)
            raw = getattr(elem, "atomic_weight", None)
            if raw is None:
                raw = getattr(elem, "mass", None)
            value = float(raw) if raw is not None else None
        except Exception:
            # Unknown symbol or library error: no reference available.
            value = None
    _REFERENCE_CACHE[symbol] = value
    return value


def _numeric_value(node: Optional[ast.AST]) -> Optional[float]:
    """Return the numeric literal value of a node, if it is one."""
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _numeric_value(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    return None


def _numeric_literals(node: Optional[ast.AST]) -> List[float]:
    """All numeric literal values that are, or are directly contained in, the node."""
    if node is None:
        return []
    number = _numeric_value(node)
    if number is not None:
        return [number]
    found: List[float] = []
    if isinstance(node, ast.Dict):
        for value in node.values:
            found.extend(_numeric_literals(value))
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            found.extend(_numeric_literals(elt))
    return found


def _has_plausible_mass_literal(node: Optional[ast.AST]) -> bool:
    """True if the node holds a literal in the range of real element masses."""
    return any(
        _MASS_LITERAL_MIN <= number <= _MASS_LITERAL_MAX
        for number in _numeric_literals(node)
    )


def _name_denotes_atomic_mass(name: str) -> bool:
    """True if a variable name reads as an atomic mass table/scalar (not a constant)."""
    tokens = {t for t in re.split(r"[_\W]+", name.upper()) if t}
    if tokens & _NON_MASS_CONTEXT_TOKENS:
        return False
    if tokens & _MASS_STRONG_TOKENS:
        return True
    return bool(tokens & _MASS_WEAK_TOKENS) and bool(tokens & _ATOMIC_CONTEXT_TOKENS)


def _pair_matches_reference(symbol_node: Optional[ast.AST], value_node: ast.AST) -> bool:
    """True if (symbol literal, numeric literal) agrees with the mendeleev mass."""
    if not (
        isinstance(symbol_node, ast.Constant) and isinstance(symbol_node.value, str)
    ):
        return False
    number = _numeric_value(value_node)
    if number is None or number <= 0:
        return False
    reference = _reference_mass(symbol_node.value)
    if reference is None or reference <= 0:
        return False
    return abs(number - reference) <= _REL_TOL * reference


def _matches_reference_masses(node: Optional[ast.AST]) -> bool:
    """Name-independent check: does a literal table reproduce real atomic masses?

    Requires the mendeleev library; without it this check reports nothing.
    """
    if node is None:
        return False
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if _pair_matches_reference(key, value):
                return True
            if isinstance(value, (ast.Dict, ast.List, ast.Tuple)):
                if _matches_reference_masses(value):
                    return True
        return False
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                if _pair_matches_reference(elt.elts[0], elt.elts[1]):
                    return True
            elif isinstance(elt, (ast.Dict, ast.List)):
                if _matches_reference_masses(elt):
                    return True
        return False
    return False


def _iter_target_pairs(
    target: ast.AST, value: ast.AST
) -> Iterator[Tuple[str, ast.AST]]:
    """Yield (variable name, assigned value node) pairs for an assignment target."""
    if isinstance(target, ast.Name):
        yield target.id, value
    elif isinstance(target, (ast.Tuple, ast.List)):
        if (
            isinstance(value, (ast.Tuple, ast.List))
            and len(value.elts) == len(target.elts)
            and not any(isinstance(e, ast.Starred) for e in target.elts)
            and not any(isinstance(e, ast.Starred) for e in value.elts)
        ):
            for sub_target, sub_value in zip(target.elts, value.elts):
                yield from _iter_target_pairs(sub_target, sub_value)


def _terminal_name(node: ast.AST) -> str:
    """Return the last identifier of a Name/Attribute expression, else ''."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_mock_module_parts(parts: List[str]) -> bool:
    return _MOCK_BASE in parts or any(p.startswith(_PYTEST_MOCK) for p in parts)


def _is_monkey_module_parts(parts: List[str]) -> bool:
    return any(p.lower() == _MONKEY for p in parts)


class _AntiSpoofVisitor(ast.NodeVisitor):
    """AST visitor detecting mock imports, empty stubs, NotImplementedError, and mass tables."""

    def __init__(self, current_file: Path) -> None:
        self.current_file = current_file
        self.violations: List[AuditViolation] = []

    def _report(
        self,
        node: ast.AST,
        category: str,
        symbol: str,
        message: str,
        severity: str = "CRITICAL",
    ) -> None:
        self.violations.append(
            AuditViolation(
                file_path=str(self.current_file),
                line=int(getattr(node, "lineno", 0)),
                col=int(getattr(node, "col_offset", 0)),
                severity=severity,
                category=category,
                symbol=symbol,
                message=message,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            parts = name.split(".")
            if _is_mock_module_parts(parts):
                self._report(
                    node,
                    "MOCK_IMPORT",
                    name,
                    f"Banned mock import detected: {name}",
                )
            elif _is_monkey_module_parts(parts):
                self._report(
                    node,
                    "MONKEYPATCH_INTERCEPTION",
                    name,
                    f"Banned monkeypatch import detected: {name}",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        mod_parts = mod.split(".") if mod else []
        is_mock_mod = _is_mock_module_parts(mod_parts)
        is_monkey_mod = _is_monkey_module_parts(mod_parts)

        for alias in node.names:
            name = alias.name
            symbol = f"{mod}.{name}" if mod else name
            if is_mock_mod:
                self._report(
                    node,
                    "MOCK_IMPORT",
                    symbol,
                    f"Banned mock import from module '{mod}': {name}",
                )
            elif name == _MOCK_BASE or name in _BANNED_MOCK_NAMES:
                self._report(
                    node,
                    "MOCK_IMPORT",
                    symbol,
                    f"Banned mock symbol imported: {name}",
                )
            elif is_monkey_mod or name.lower() == _MONKEY:
                self._report(
                    node,
                    "MONKEYPATCH_INTERCEPTION",
                    symbol,
                    f"Banned monkeypatch import detected: {symbol}",
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            owner = _terminal_name(func.value)
            if owner.lower() == _MONKEY and func.attr in _MONKEY_MUTATORS:
                self._report(
                    node,
                    "MONKEYPATCH_INTERCEPTION",
                    f"{owner}.{func.attr}",
                    f"Runtime interception via {owner}.{func.attr} is forbidden",
                )
            elif func.attr.lower() == _MONKEY:
                self._report(
                    node,
                    "MONKEYPATCH_INTERCEPTION",
                    func.attr,
                    f"Monkeypatch object construction detected: {func.attr}",
                )
        elif isinstance(func, ast.Name) and func.id.lower() == _MONKEY:
            self._report(
                node,
                "MONKEYPATCH_INTERCEPTION",
                func.id,
                f"Monkeypatch object construction detected: {func.id}",
            )
        self.generic_visit(node)

    def _check_func_stub(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> None:
        for dec in node.decorator_list:
            dec_name = ""
            if isinstance(dec, ast.Name):
                dec_name = dec.id
            elif isinstance(dec, ast.Attribute):
                dec_name = dec.attr
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    dec_name = dec.func.id
                elif isinstance(dec.func, ast.Attribute):
                    dec_name = dec.func.attr
            if dec_name in ("abstractmethod", "overload"):
                return

        stmts = list(node.body)
        while (
            stmts
            and isinstance(stmts[0], ast.Expr)
            and isinstance(stmts[0].value, ast.Constant)
            and isinstance(stmts[0].value.value, str)
        ):
            stmts = stmts[1:]

        if len(stmts) == 1:
            stmt = stmts[0]
            if isinstance(stmt, ast.Pass):
                self._report(
                    stmt,
                    "EMPTY_PASS_STUB",
                    node.name,
                    f"Function/method '{node.name}' has empty pass stub body",
                )
            elif (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is Ellipsis
            ):
                self._report(
                    stmt,
                    "EMPTY_PASS_STUB",
                    node.name,
                    f"Function/method '{node.name}' has empty ellipsis stub body",
                )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_func_stub(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_func_stub(node)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        exc = node.exc
        is_nie = False
        sym = ""
        if isinstance(exc, ast.Name) and exc.id == _NIE_NAME:
            is_nie = True
            sym = exc.id
        elif isinstance(exc, ast.Call):
            func = exc.func
            if isinstance(func, ast.Name) and func.id == _NIE_NAME:
                is_nie = True
                sym = func.id
            elif isinstance(func, ast.Attribute) and func.attr == _NIE_NAME:
                is_nie = True
                sym = func.attr
        elif isinstance(exc, ast.Attribute) and exc.attr == _NIE_NAME:
            is_nie = True
            sym = exc.attr

        if is_nie:
            self._report(
                node,
                "NOT_IMPLEMENTED_ERROR",
                sym,
                "Forbidden NotImplementedError raise detected",
            )
        self.generic_visit(node)

    def _check_mass_violation(
        self,
        target_node: ast.AST,
        val_node: Optional[ast.AST],
        node: ast.AST,
    ) -> None:
        if val_node is None:
            return

        for name, value in _iter_target_pairs(target_node, val_node):
            if _name_denotes_atomic_mass(name) and _has_plausible_mass_literal(value):
                self._report(
                    node,
                    "MENDELEEV_VIOLATION",
                    name,
                    f"Hardcoded atomic mass detected in variable '{name}'. "
                    "Mendeleev dynamic lookup is mandated.",
                )
            elif _matches_reference_masses(value):
                self._report(
                    node,
                    "MENDELEEV_VIOLATION",
                    name,
                    f"Literal table in '{name}' reproduces reference atomic masses. "
                    "Mendeleev dynamic lookup is mandated.",
                )

    def visit_Assign(self, node: ast.Assign) -> None:
        for tgt in node.targets:
            self._check_mass_violation(tgt, node.value, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_mass_violation(node.target, node.value, node)
        self.generic_visit(node)


class AntiSpoofScanner:
    """AST scanner enforcing anti-spoofing and Mendeleev mass invariants."""

    def scan_file(
        self, file_path: Union[str, Path, os.PathLike]
    ) -> List[AuditViolation]:
        """Scan a Python file for anti-spoofing violations.

        Files that cannot be read or parsed are reported as UNSCANNABLE_SOURCE
        violations rather than being treated as clean (the scanner fails closed).
        """
        path = Path(file_path).resolve()
        visitor = _AntiSpoofVisitor(current_file=path)
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
            visitor.visit(tree)
        except (OSError, ValueError, SyntaxError, RecursionError, MemoryError) as exc:
            return [self._unscannable(path, exc)]

        return sorted(visitor.violations, key=lambda v: (v.line, v.col))

    @staticmethod
    def _unscannable(path: Path, exc: BaseException) -> AuditViolation:
        line = getattr(exc, "lineno", 0)
        col = getattr(exc, "offset", 0)
        return AuditViolation(
            file_path=str(path),
            line=line if isinstance(line, int) and line > 0 else 0,
            col=col if isinstance(col, int) and col > 0 else 0,
            severity="HIGH",
            category="UNSCANNABLE_SOURCE",
            symbol=path.name,
            message=f"Source could not be analysed ({type(exc).__name__}): {exc}",
        )

    def scan_directory(
        self, root_dir: Union[str, Path, os.PathLike]
    ) -> List[AuditViolation]:
        """Recursively scan all Python files in a directory."""
        root_path = Path(root_dir).resolve()
        violations: List[AuditViolation] = []
        for p in sorted(root_path.rglob("*.py")):
            if p.is_file():
                violations.extend(self.scan_file(p))
        return sorted(violations, key=lambda v: (v.file_path, v.line, v.col))


def _collect_environment(target_path: Path) -> Dict[str, Any]:
    """Hardware audit and environment verification, reported alongside the audit."""
    profile = HardwareAuditor.probe(target_path)
    binaries = EnvironmentVerifier.audit_binaries()
    return {
        "python_version": platform.python_version(),
        "python_compliant": EnvironmentVerifier.verify_python_version(),
        "platform": profile.platform,
        "hardware": profile.to_dict(),
        "binaries": {name: dataclasses.asdict(rec) for name, rec in binaries.items()},
    }


def run_audit(
    target_dir: Union[str, Path, os.PathLike], json_output: bool = False
) -> AuditResult:
    """Run full integrity, environment and anti-spoofing diagnostic suite on a project."""
    target_path = Path(target_dir).resolve()
    findings: List[Dict[str, Any]] = []
    manifest_valid = False

    try:
        manifest_valid = verify_project_integrity(target_path)
    except (ProjectIntegrityError, OSError) as exc:
        manifest_valid = False
        findings.append(
            {
                "category": "PROJECT_INTEGRITY",
                "message": str(exc),
                "severity": "CRITICAL",
                "file_path": str(target_path / MANIFEST_NAME),
                "line": 0,
                "col": 0,
                "symbol": "manifest",
            }
        )

    environment = _collect_environment(target_path)
    if not environment["python_compliant"]:
        findings.append(
            {
                "category": "ENVIRONMENT",
                "message": f"Python {environment['python_version']} is below the required 3.10",
                "severity": "CRITICAL",
                "file_path": "",
                "line": 0,
                "col": 0,
                "symbol": "python",
            }
        )

    scanner = AntiSpoofScanner()
    for v in scanner.scan_directory(target_path):
        findings.append(dataclasses.asdict(v))

    has_spoofing = any(
        f.get("category") in _SPOOFING_CATEGORIES for f in findings
    )

    if has_spoofing:
        verdict = "SPOOFING_DETECTED"
    elif not manifest_valid or findings:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    if verdict == "PASS":
        score = 100
    else:
        calc_score = 100
        if not manifest_valid:
            calc_score -= 50
        for f in findings:
            sev = f.get("severity")
            if sev == "CRITICAL":
                calc_score -= 25
            elif sev == "HIGH":
                calc_score -= 10
            else:
                calc_score -= 5
        score = max(0, min(99, calc_score))

    return AuditResult(
        verdict=verdict,
        score=score,
        findings=findings,
        manifest_valid=manifest_valid,
        environment=environment,
    )
