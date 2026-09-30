# src/cochem/research/verification.py

`python
"""Static verifiers: zero-mock scanner (MC-TDD-16/17; NFR-TDD-03), stub auditor (MC-TDD-18), mutmut gate (MC-TDD-19)."""
from __future__ import annotations

import ast
import base64
import binascii
import codecs
from dataclasses import dataclass
import glob
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.tdd_pivot.verification")

MOCK_MODULES: tuple[str, ...] = ("unittest.mock", "mock", "pytest_mock", "mockito", "flexmock", "asynctest")
MOCK_NAMES: tuple[str, ...] = (
    "MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock", "monkeypatch",
    "MonkeyPatch", "mocker", "class_mocker", "module_mocker", "package_mocker", "session_mocker",
)
_DYNAMIC: tuple[str, ...] = ("import_module", "__import__", "getattr")


@dataclass(frozen=True, order=True)
class MockFinding:
    """NFR-TDD-03 violation: file, line, token as written/decoded; kind: import|attribute|name|fixture|literal."""
    file: str
    line: int
    token: str
    kind: str


def _dotted(node: ast.AST) -> str:
    return s if re.fullmatch(r"[^\W\d]\w*(?:\.\w+)*", s := ast.unparse(node)) else ""


def _prefixed(dot: str, names=MOCK_MODULES) -> bool:
    return any(dot == n or dot.startswith(n + ".") for n in names)


def scan_mock_usage_source(source: str, filename: str) -> list[MockFinding]:
    """ast scan: mock imports (also importlib/__import__ of a constant), aliases, attribute chains, names, fixtures."""
    tree = ast.parse(source, filename=filename)
    found: set[MockFinding] = set()
    bound: set[str] = set(MOCK_NAMES)
    skip: set[int] = set()

    def hit(node: ast.AST, token: str, kind: str) -> None:
        found.add(MockFinding(filename, node.lineno, token, kind))

    for node in (n for n in ast.walk(tree) if id(n) not in skip):
        for a in node.names if isinstance(node, (ast.Import, ast.ImportFrom)) else ():
            fq = f"{getattr(node, 'module', None) or ''}.{a.name}".lstrip(".")
            if _prefixed(fq) or a.name in MOCK_NAMES:
                hit(node, fq, "import")
                bound |= {a.asname or a.name}
        if isinstance(node, ast.Attribute):
            dot = _dotted(node)
            if bound & {node.attr, *dot.split(".")} or _prefixed(dot):
                hit(node, dot or node.attr, "attribute")
            skip.update(id(n) for n in ast.walk(node) if dot and n is not node)
        elif isinstance(node, ast.Name) and node.id in bound:
            hit(node, node.id, "name")
        elif isinstance(node, ast.arg) and node.arg in MOCK_NAMES:
            hit(node, node.arg, "fixture")
        elif isinstance(node, ast.Call) and (fn := _dotted(node.func).split(".")[-1]) in _DYNAMIC:
            v = ".".join(_dotted(a) or str(getattr(a, "value", "")) for a in node.args[:2 if fn == "getattr" else 1])
            if _prefixed(v) or bound & set(v.split(".")):
                hit(node, v, "attribute" if fn == "getattr" else "import")
    return sorted(found)


def scan_mock_usage_file(path: str | Path) -> list[MockFinding]:
    return scan_mock_usage_source(Path(path).read_text(encoding="utf-8"), str(path))


def scan_mock_usage_tree(root: str | Path, pattern: str = "*.py") -> list[MockFinding]:
    """Scan every file of Path(root).rglob(pattern) in sorted order, skipping __pycache__; root must be a directory."""
    if not (base := Path(root)).is_dir():
        raise NotADirectoryError(f"scan root is not a directory: {base}")
    files = [p for p in sorted(base.rglob(pattern)) if p.is_file() and "__pycache__" not in p.relative_to(base).parts]
    return sorted(f for p in files for f in scan_mock_usage_file(p))


# Obfuscated token and monkeypatch detection
MOCK_TOKENS: tuple[str, ...] = tuple(t for t in (*MOCK_MODULES, *MOCK_NAMES) if t.lower() != "mock")
_PLAIN_RE = re.compile("(?:" + "|".join(map(re.escape, MOCK_TOKENS)) + r")(?![a-z])", re.I)
_HIDDEN_RE = re.compile(_PLAIN_RE.pattern[:-9] + "|mock", re.I)
_DECODERS = {
    n: getattr(base64, n) for n in ("b64decode", "standard_b64decode", "urlsafe_b64decode", "b32decode")
} | {
    n: binascii.unhexlify for n in ("unhexlify", "b16decode", "fromhex")
} | {"a2b_base64": binascii.a2b_base64}


def _text(raw: Any) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


def _resolve(node: ast.AST) -> str | None:
    """Fold a string expression statically (never executed); None when any part is not constant."""
    try:
        if isinstance(node, ast.Constant):
            return _text(node.value) if isinstance(node.value, (str, bytes)) else None
        if isinstance(node, ast.JoinedStr):
            return "".join(map(_resolve, node.values))
        if isinstance(node, ast.BinOp) and type(node.op) is ast.Add:
            return _resolve(node.left) + _resolve(node.right)
        if isinstance(node, ast.Subscript) and ast.unparse(node.slice) == "::-1":
            return _resolve(node.value)[::-1]
        if not isinstance(node, ast.Call) or node.keywords:
            return None
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        base = _resolve(getattr(node.func, "value", None))
        args = [*map(_resolve, node.args)]
        if name in ("chr", "reversed"):
            return chr(ast.literal_eval(node.args[0])) if name == "chr" else args[0][::-1]
        if name == "decode" and base is None and len(args) == 2:
            return _text(codecs.decode(args[0].encode(), args[1]))
        if name in ("decode", "encode", "lower", "upper", "strip", "str"):
            return args[0] if base is None else base
        if name == "replace":
            return base.replace(args[0], args[1])
        if name == "join":
            return base.join(list(map(_resolve, getattr(node.args[0], "elts", ()))) or list(args[0]))
        return _text(_DECODERS[name](args[0])) if name in _DECODERS else None
    except (ValueError, TypeError, LookupError, AttributeError, IndexError):
        return None


def _decoded(text: str, depth: int = 3):
    for fn in set(_DECODERS.values()) if depth and text else ():
        try:
            inner = _text(fn(text.strip()))
        except ValueError:
            continue
        yield inner
        yield from _decoded(inner, depth - 1)


def _literal_hits(source: str, filename: str):
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.expr) and (text := _resolve(node)) is not None:
            plain = _PLAIN_RE if isinstance(node, (ast.Constant, ast.JoinedStr)) else _HIDDEN_RE
            yield from ((node, m) for m in [plain.search(text), *map(_HIDDEN_RE.search, _decoded(text))] if m)


def scan_mock_literals_source(source: str, filename: str) -> list[MockFinding]:
    """Flag every expression whose statically folded text (or any whole-string decoding of it) holds a mock token."""
    return sorted({MockFinding(filename, n.lineno, m.group(), "literal") for n, m in _literal_hits(source, filename)})


def zero_mock_checker(target: str | Path) -> list[MockFinding]:
    """NFR-TDD-03 verdict: Path = UTF-8 file to read, str = source; sorted code + literal findings, [] = clean."""
    src, name = (target.read_text("utf-8"), str(target)) if isinstance(target, Path) else (target, "<source>")
    return sorted({*scan_mock_usage_source(src, name), *scan_mock_literals_source(src, name)})


# Stub Auditor for NotImplementedError and Empty pass
STUB_KINDS: tuple[str, ...] = ("pass", "ellipsis", "raise NotImplementedError")


@dataclass(frozen=True)
class StubFinding:
    """One function whose whole body is a stub: file, line of its def, qualified name, STUB_KINDS entry."""
    file: str
    line: int
    function: str
    kind: str


def _stub_kind(body: list[ast.stmt]) -> str | None:
    """STUB_KINDS entry when body is, after an optional docstring, exactly one stub statement; else None."""
    docstring = isinstance(body[0], ast.Expr) and isinstance(getattr(body[0].value, "value", None), str)
    stmt = body[-1] if len(body) == 1 or (len(body) == 2 and docstring) else None
    if isinstance(stmt, ast.Pass) or (isinstance(stmt, ast.Expr) and getattr(stmt.value, "value", 0) is Ellipsis):
        return STUB_KINDS[0] if isinstance(stmt, ast.Pass) else STUB_KINDS[1]
    exc = getattr(stmt.exc, "func", stmt.exc) if isinstance(stmt, ast.Raise) else None
    name = exc.id if isinstance(exc, ast.Name) else getattr(exc, "attr", None)
    return STUB_KINDS[2] if name == "NotImplementedError" else None


def audit_stub_source(source: str, filename: str) -> list[StubFinding]:
    """Parse source with ast (never imported or run); a parse error raises SyntaxError naming filename."""
    tree = ast.parse(source, filename=filename)
    scope: dict[ast.AST, str] = {tree: ""}
    findings: list[StubFinding] = []
    for node in ast.walk(tree):
        own = scope[node] + node.name if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else ""
        for child in ast.iter_child_nodes(node):
            scope[child] = f"{own}." if own else scope[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (kind := _stub_kind(node.body)):
            findings.append(StubFinding(filename, node.lineno, own, kind))
    return sorted(findings, key=lambda f: (f.file, f.line))


def audit_stub_file(path: str | Path) -> list[StubFinding]:
    """Audit one UTF-8 source file (BOM tolerated); a missing file raises FileNotFoundError."""
    return audit_stub_source(Path(path).read_text(encoding="utf-8-sig"), str(path))


def audit_stub_tree(root: str | Path, pattern: str = "*.py") -> list[StubFinding]:
    """Audit every file of Path(root).rglob(pattern) in sorted order, skipping __pycache__ directories."""
    if not (base := Path(root)).is_dir():
        raise NotADirectoryError(f"stub audit root is not a directory: {base}")
    paths = [p for p in sorted(base.rglob(pattern)) if p.is_file() and "__pycache__" not in p.relative_to(base).parts]
    return sorted((f for p in paths for f in audit_stub_file(p)), key=lambda f: (f.file, f.line))


# mutmut 85% Kill Score Gate (SRS-412-05-FR-008)
KILL_SCORE_THRESHOLD: float = 85.0
MUTMUT_TIMEOUT_SEC: float = 1800.0
MUTATION_TARGETS: tuple[str, ...] = (
    "src/cochem/tdd_pivot/state_machine.py",
    "src/cochem/tdd_pivot/research.py",
    "src/cochem/tdd_pivot/verification.py",
)
_SUMMARY_RE = re.compile(r"(\d+)/(\d+)" + "".join(rf"[ \t]+{icon}[ \t]*(\d+)" for icon in "🎉⏰🤔🙁🔇"))


class MutationGateError(RuntimeError):
    """FR-008: no finished mutmut summary, nothing measured, or score too low."""


@dataclass(frozen=True)
class MutationSummary:
    total: int
    killed: int
    timeout: int
    suspicious: int
    survived: int
    skipped: int


def parse_mutmut_summary(text: str) -> MutationSummary:
    states = [[int(g) for g in m.groups()] for m in map(_SUMMARY_RE.search, re.split(r"[\r\n]", text)) if m]
    if not states or states[-1][0] != states[-1][1] or sum(states[-1][2:]) != states[-1][1]:
        raise MutationGateError(f"no finished, consistent mutmut summary line; output tail: {text[-800:]!r}")
    return MutationSummary(*states[-1][1:])


def kill_score(summary: MutationSummary) -> float:
    if summary.total <= summary.skipped:
        raise MutationGateError(f"no mutant was measured: {summary}")
    return 100.0 * summary.killed / (summary.total - summary.skipped)


def enforce_kill_score_gate(summary: MutationSummary, threshold: float = KILL_SCORE_THRESHOLD) -> float:
    if (score := kill_score(summary)) >= threshold:
        return score
    raise MutationGateError(f"kill score {score:.2f}% is below threshold {threshold}%: {summary.survived} survived")


def run_mutmut(
    project_dir: str | Path,
    paths_to_mutate: tuple[str, ...] | list[str],
    tests_dir: str,
    timeout: float = MUTMUT_TIMEOUT_SEC,
    runner: str | None = None,
) -> MutationSummary:
    python = resolve_mutmut_interpreter()
    argv = [
        python, "-m", "mutmut", "run", "--paths-to-mutate", ",".join(paths_to_mutate),
        "--tests-dir", tests_dir, "--runner", runner or f'"{python}" -m pytest -x -q -p no:cacheprovider',
    ]
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000200 if os.name == "nt" else 0,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        return parse_mutmut_summary(proc.communicate(timeout=timeout)[0])
    except OSError as exc:
        raise MutationGateError(f"mutmut could not start in {project_dir}: {type(exc).__name__}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)] if os.name == "nt" else ["kill", str(proc.pid)],
            capture_output=True,
        )
        proc.kill()
        tail = _drain_after_kill(proc)
        _restore_mutmut_backups(project_dir, paths_to_mutate)
        raise MutationGateError(f"mutmut exceeded {timeout} s; output tail: {tail[-800:]!r}") from exc


def _drain_after_kill(proc: subprocess.Popen) -> str:
    try:
        return proc.communicate(timeout=30)[0] or ""
    except subprocess.TimeoutExpired:
        return ""


def _restore_mutmut_backups(project_dir: str | Path, paths_to_mutate: tuple[str, ...] | list[str]) -> None:
    """mutmut 2.5.1 restores `<file>.bak` in a finally that a forced kill skips: put every original back."""
    for target in (Path(project_dir, rel) for rel in paths_to_mutate):
        for bak in target.rglob("*.py.bak") if target.is_dir() else (target.with_name(target.name + ".bak"),):
            if bak.is_file():
                os.replace(bak, bak.with_name(bak.name[:-4]))


MUTMUT_PYTHON_ENV: str = "COCHEM_MUTMUT_PYTHON"
MUTMUT_REQUIRED_VERSION: str = "2.5.1"
MUTMUT_PROBE_TIMEOUT_SEC: float = 60.0
_NO_WINDOW: int = 0x08000200 if os.name == "nt" else 0
_RESOLVED_INTERPRETERS: dict[tuple[str, str, str], str] = {}


class MutmutUnavailableError(RuntimeError):
    """FR-008: no candidate interpreter physically answered -m mutmut version with the pinned version."""
    def __init__(self, rejected: list[tuple[str, str]]):
        self.rejected = list(rejected)
        tried = "; ".join(f"[{path}] rejected: {why}" for path, why in self.rejected) or "no candidate was found"
        super().__init__(f"no Python interpreter with mutmut {MUTMUT_REQUIRED_VERSION} and pytest; tried: {tried}")


def _run_probe(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        timeout=MUTMUT_PROBE_TIMEOUT_SEC,
        creationflags=_NO_WINDOW,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )


def probe_mutmut_interpreter(python: str) -> str | None:
    """None when python -m mutmut version prints the pinned version and python -m pytest works."""
    if not os.path.isfile(python):
        return "no such file"
    want = f"mutmut version {MUTMUT_REQUIRED_VERSION}"
    for args, ok in (
        (["-m", "mutmut", "version"], lambda out: out == want),
        (["-m", "pytest", "--version"], lambda out: out.startswith("pytest ")),
    ):
        try:
            proc = _run_probe([python, *args])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"`{' '.join(args)}` could not run: {type(exc).__name__}: {exc}"
        out = (proc.stdout.strip() or proc.stderr.strip())
        if proc.returncode != 0 or not ok(out):
            detail = (proc.stdout + proc.stderr).strip()[-300:]
            return f"`{' '.join(args)}` exit {proc.returncode}, need {want!r} / pytest: {detail!r}"
    return None


def _launcher_interpreters(rejected: list[tuple[str, str]]) -> list[str]:
    if (launcher := shutil.which("py")) is None:
        rejected.append(("py launcher", "not on PATH"))
        return []
    found: list[str] = []
    for args in (["-3", "-c", "import sys; print(sys.executable)"], ["-0p"]):
        try:
            proc = _run_probe([launcher, *args])
        except (OSError, subprocess.TimeoutExpired) as exc:
            rejected.append((f"{launcher} {' '.join(args)}", f"{type(exc).__name__}: {exc}"))
            continue
        found += [
            m.group(1).strip()
            for m in (re.search(r"([A-Za-z]:\\.+|/.+)$", line.strip()) for line in proc.stdout.splitlines())
            if m
        ]
    return found


def _path_interpreters() -> list[str]:
    names = ("python.exe", "python3.exe") if os.name == "nt" else ("python3", "python")
    first = [p for p in (shutil.which("python"),) if p]
    return first + [
        os.path.join(d, n)
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d
        for n in names
        if os.path.isfile(os.path.join(d, n))
    ]


def _registry_interpreters() -> list[str]:
    if os.name != "nt":
        return []
    import winreg
    found: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, r"Software\Python", 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                for company in _registry_subkeys(root):
                    with winreg.OpenKey(root, company, 0, winreg.KEY_READ | view) as company_key:
                        for tag in _registry_subkeys(company_key):
                            try:
                                with winreg.OpenKey(
                                    company_key, tag + r"\InstallPath", 0, winreg.KEY_READ | view
                                ) as ip:
                                    found.append(_registry_executable(ip))
                            except OSError:
                                continue
    return found


def _registry_subkeys(key: Any) -> list[str]:
    import winreg
    names: list[str] = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            return names
        index += 1


def _registry_executable(install_path_key: Any) -> str:
    import winreg
    try:
        return winreg.QueryValueEx(install_path_key, "ExecutablePath")[0]
    except OSError:
        return os.path.join(winreg.QueryValueEx(install_path_key, "")[0], "python.exe")


def _standard_location_interpreters() -> list[str]:
    exe = "python.exe" if os.name == "nt" else "python3"
    roots = [
        os.environ.get("SystemDrive", "C:") + os.sep,
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python"),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
    ]
    return [
        p
        for root in roots
        if root.strip(os.sep)
        for p in sorted(glob.glob(os.path.join(root, "Python3*", exe)), reverse=True)
    ]


def _candidate_interpreters(rejected: list[tuple[str, str]]) -> Iterator[str]:
    yield sys.executable
    for discover in (
        lambda: _launcher_interpreters(rejected),
        _path_interpreters,
        _registry_interpreters,
        _standard_location_interpreters,
    ):
        yield from discover()


def resolve_mutmut_interpreter() -> str:
    """Absolute path of an interpreter physically proven to run mutmut MUTMUT_REQUIRED_VERSION and pytest."""
    override = os.environ.get(MUTMUT_PYTHON_ENV, "").strip()
    key = (override, sys.executable, os.environ.get("PATH", ""))
    if key in _RESOLVED_INTERPRETERS:
        return _RESOLVED_INTERPRETERS[key]
    rejected: list[tuple[str, str]] = []
    if override:
        if (reason := probe_mutmut_interpreter(override)) is not None:
            raise MutmutUnavailableError([(f"{override} (from {MUTMUT_PYTHON_ENV})", reason)])
        _RESOLVED_INTERPRETERS[key] = os.path.abspath(override)
        return _RESOLVED_INTERPRETERS[key]
    seen: set[str] = set()
    for candidate in _candidate_interpreters(rejected):
        if (norm := os.path.normcase(os.path.abspath(candidate))) in seen:
            continue
        seen.add(norm)
        if (reason := probe_mutmut_interpreter(candidate)) is None:
            _RESOLVED_INTERPRETERS[key] = os.path.abspath(candidate)
            return _RESOLVED_INTERPRETERS[key]
        rejected.append((candidate, reason))
    raise MutmutUnavailableError(rejected)

`
