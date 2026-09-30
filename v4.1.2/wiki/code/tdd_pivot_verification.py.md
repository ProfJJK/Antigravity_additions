# src/cochem/tdd_pivot/verification.py

`python
# [MC-TDD-16] NFR-TDD-03
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Satisfy NFR-
# SPEC: TDD-03: Zero mock libraries (`unittest.mock`, `MagicMock`, `monkeypatch`) shall be present in
# SPEC: any production test code. Scan with the ast module so imports, attribute access and aliases are
# SPEC: all caught.
"""Static verifiers: zero-mock scanner (MC-TDD-16/17; NFR-TDD-03), stub auditor (MC-TDD-18), mutmut gate (MC-TDD-19)."""
import ast, base64, binascii, codecs, dataclasses, pathlib, re
MOCK_MODULES: tuple[str, ...] = ("unittest.mock", "mock", "pytest_mock", "mockito", "flexmock", "asynctest")
MOCK_NAMES: tuple[str, ...] = ("MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock", "monkeypatch",
    "MonkeyPatch", "mocker", "class_mocker", "module_mocker", "package_mocker", "session_mocker")
_DYNAMIC: tuple[str, ...] = ("import_module", "__import__", "getattr")  # calls whose constant arguments name code
@dataclasses.dataclass(frozen=True, order=True)
class MockFinding:
    """NFR-TDD-03 violation: file, line, token as written/decoded; kind: import|attribute|name|fixture|literal."""
    file: str
    line: int
    token: str
    kind: str
def _dotted(node: ast.AST) -> str: return s if re.fullmatch(r"[^\W\d]\w*(?:\.\w+)*", s := ast.unparse(node)) else ""
def _prefixed(dot: str, names=MOCK_MODULES) -> bool: return any(dot == n or dot.startswith(n + ".") for n in names)
def scan_mock_usage_source(source: str, filename: str) -> list[MockFinding]:
    """ast scan: mock imports (also importlib/__import__ of a constant), aliases, attribute chains, names, fixtures."""
    tree, found, bound, skip = ast.parse(source, filename=filename), set(), set(MOCK_NAMES), set()
    hit = lambda node, token, kind: found.add(MockFinding(filename, node.lineno, token, kind))
    for node in (n for n in ast.walk(tree) if id(n) not in skip):  # breadth-first: imports bind before deeper uses
        for a in node.names if isinstance(node, (ast.Import, ast.ImportFrom)) else ():  # `patch` counts only once bound
            fq = f"{getattr(node, 'module', None) or ''}.{a.name}".lstrip(".")
            if _prefixed(fq) or a.name in MOCK_NAMES: hit(node, fq, "import"); bound |= {a.asname or a.name}
        if isinstance(node, ast.Attribute):  # a pure Name chain is judged whole, once; its inner nodes are then skipped
            dot = _dotted(node)
            if bound & {node.attr, *dot.split(".")} or _prefixed(dot): hit(node, dot or node.attr, "attribute")
            skip.update(id(n) for n in ast.walk(node) if dot and n is not node)
        elif isinstance(node, ast.Name) and node.id in bound: hit(node, node.id, "name")
        elif isinstance(node, ast.arg) and node.arg in MOCK_NAMES: hit(node, node.arg, "fixture")
        elif isinstance(node, ast.Call) and (fn := _dotted(node.func).split(".")[-1]) in _DYNAMIC:
            v = ".".join(_dotted(a) or str(getattr(a, "value", "")) for a in node.args[:2 if fn == "getattr" else 1])
            if _prefixed(v) or bound & set(v.split(".")): hit(node, v, "attribute" if fn == "getattr" else "import")
    return sorted(found)
def scan_mock_usage_file(path: str | pathlib.Path) -> list[MockFinding]:  # FileNotFoundError / SyntaxError propagate
    return scan_mock_usage_source(pathlib.Path(path).read_text(encoding="utf-8"), str(path))
def scan_mock_usage_tree(root: str | pathlib.Path, pattern: str = "*.py") -> list[MockFinding]:
    """Scan every file of Path(root).rglob(pattern) in sorted order, skipping __pycache__; root must be a directory."""
    if not (base := pathlib.Path(root)).is_dir(): raise NotADirectoryError(f"scan root is not a directory: {base}")
    files = [p for p in sorted(base.rglob(pattern)) if p.is_file() and "__pycache__" not in p.relative_to(base).parts]
    return sorted(f for p in files for f in scan_mock_usage_file(p))
# [MC-TDD-17] OBLIGATION:zero_mock_checker
# SPEC: Append after the previous chunk without editing earlier lines. Implement the behaviour required
# SPEC: by the section 9 test obligation: Test asserts `zero_mock_checker` flags obfuscated tokens and
# SPEC: `monkeypatch`. Decode base64 and hex string literals and concatenations before matching, so an
# SPEC: obfuscated 'monkeypatch' or 'MagicMock' is still flagged.
MOCK_TOKENS: tuple[str, ...] = tuple(t for t in (*MOCK_MODULES, *MOCK_NAMES) if t.lower() != "mock")
_PLAIN_RE = re.compile("(?:" + "|".join(map(re.escape, MOCK_TOKENS)) + r")(?![a-z])", re.I)  # no 'mockery'
_HIDDEN_RE = re.compile(_PLAIN_RE.pattern[:-9] + "|mock", re.I)  # decoded/constructed text: a hidden bare mock counts
_DECODERS = {n: getattr(base64, n) for n in ("b64decode", "standard_b64decode", "urlsafe_b64decode", "b32decode")} | {
    n: binascii.unhexlify for n in ("unhexlify", "b16decode", "fromhex")} | {"a2b_base64": binascii.a2b_base64}
_text = lambda raw: raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
def _resolve(node: ast.AST) -> str | None:
    """Fold a string expression statically (never executed); None when any part is not constant. See MOCK_TOKENS."""
    try:  # a part that does not fold makes the whole unfoldable: these exceptions mean None, not a bug
        if isinstance(node, ast.Constant): return _text(node.value) if isinstance(node.value, (str, bytes)) else None
        if isinstance(node, ast.JoinedStr): return "".join(map(_resolve, node.values))
        if isinstance(node, ast.BinOp) and type(node.op) is ast.Add: return _resolve(node.left) + _resolve(node.right)
        if isinstance(node, ast.Subscript) and ast.unparse(node.slice) == "::-1": return _resolve(node.value)[::-1]
        if not isinstance(node, ast.Call) or node.keywords: return None
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        base, args = _resolve(getattr(node.func, "value", None)), [*map(_resolve, node.args)]
        if name in ("chr", "reversed"): return chr(ast.literal_eval(node.args[0])) if name == "chr" else args[0][::-1]
        if name == "decode" and base is None and len(args) == 2: return _text(codecs.decode(args[0].encode(), args[1]))
        if name in ("decode", "encode", "lower", "upper", "strip", "str"): return args[0] if base is None else base
        if name == "replace": return base.replace(args[0], args[1])
        if name == "join": return base.join(list(map(_resolve, getattr(node.args[0], "elts", ()))) or list(args[0]))
        return _text(_DECODERS[name](args[0])) if name in _DECODERS else None
    except (ValueError, TypeError, LookupError, AttributeError, IndexError): return None
def _decoded(text: str, depth: int = 3):  # texts reachable by whole-string base64/base32/hex decoding, <= depth deep
    for fn in set(_DECODERS.values()) if depth and text else ():
        try: inner = _text(fn(text.strip()))
        except ValueError: continue
        yield inner; yield from _decoded(inner, depth - 1)
def _literal_hits(source: str, filename: str):  # (node, match) for every folded expression text that holds a token
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.expr) and (text := _resolve(node)) is not None:
            plain = _PLAIN_RE if isinstance(node, (ast.Constant, ast.JoinedStr)) else _HIDDEN_RE
            yield from ((node, m) for m in [plain.search(text), *map(_HIDDEN_RE.search, _decoded(text))] if m)
def scan_mock_literals_source(source: str, filename: str) -> list[MockFinding]:
    """Flag every expression whose statically folded text (or any whole-string decoding of it) holds a mock token."""
    return sorted({MockFinding(filename, n.lineno, m.group(), "literal") for n, m in _literal_hits(source, filename)})
def zero_mock_checker(target: str | pathlib.Path) -> list[MockFinding]:
    """NFR-TDD-03 verdict: Path = UTF-8 file to read, str = source; sorted code + literal findings, [] = clean."""
    src, name = (target.read_text("utf-8"), str(target)) if isinstance(target, pathlib.Path) else (target, "<source>")
    return sorted({*scan_mock_usage_source(src, name), *scan_mock_literals_source(src, name)})
# [MC-TDD-18] OBLIGATION:NotImplementedError
# SPEC: Append after the previous chunk without editing earlier lines. Implement the behaviour required
# SPEC: by the section 9 test obligation: Test asserts audit flags `raise NotImplementedError` and empty
# SPEC: `pass` blocks. Flag functions whose body is only `pass`, `...` or `raise NotImplementedError`;
# SPEC: report file, line and function name.
import ast
from dataclasses import dataclass
from pathlib import Path
STUB_KINDS: tuple[str, ...] = ("pass", "ellipsis", "raise NotImplementedError")
@dataclass(frozen=True)
class StubFinding:
    """One function whose whole body is a stub: file, line of its ``def``, qualified name, STUB_KINDS entry."""
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
    scope: dict[ast.AST, str] = {(tree := ast.parse(source, filename=filename)): ""}
    findings: list[StubFinding] = []
    for node in ast.walk(tree):  # breadth-first: a node's scope is set before its children are visited
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
# [MC-TDD-19] SRS-412-05-FR-008
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-05-FR-008:
# SPEC: Critical numerical routines shall achieve a mutation testing kill score of at least 85% via
# SPEC: `mutmut`. Apply the gate to the tdd_pivot modules (cycle counters, token bound, kill score
# SPEC: arithmetic). Parse the mutmut results summary; do not reimplement mutation testing.
import os, re, subprocess, sys  # this chunk's own imports until MC-TDD-16 owns the module header
KILL_SCORE_THRESHOLD: float = 85.0
MUTMUT_TIMEOUT_SEC: float = 1800.0
MUTATION_TARGETS: tuple[str, ...] = ("src/cochem/tdd_pivot/state_machine.py", "src/cochem/tdd_pivot/research.py",
                                     "src/cochem/tdd_pivot/verification.py")
_SUMMARY_RE = re.compile(r"(\d+)/(\d+)" + "".join(rf"[ \t]+{icon}[ \t]*(\d+)" for icon in "🎉⏰🤔🙁🔇"))
class MutationGateError(RuntimeError): """FR-008: no finished mutmut summary, nothing measured, or score too low."""
@dataclass(frozen=True)
class MutationSummary:
    total: int
    killed: int
    timeout: int
    suspicious: int
    survived: int
    skipped: int
def parse_mutmut_summary(text: str) -> MutationSummary:  # LAST state `done/total 🎉 k ⏰ t 🤔 s 🙁 v 🔇 x`
    states = [[int(g) for g in m.groups()] for m in map(_SUMMARY_RE.search, re.split(r"[\r\n]", text)) if m]
    if not states or states[-1][0] != states[-1][1] or sum(states[-1][2:]) != states[-1][1]:
        raise MutationGateError(f"no finished, consistent mutmut summary line; output tail: {text[-800:]!r}")
    return MutationSummary(*states[-1][1:])
def kill_score(summary: MutationSummary) -> float:  # timeout and suspicious mutants count as not killed
    if summary.total <= summary.skipped: raise MutationGateError(f"no mutant was measured: {summary}")
    return 100.0 * summary.killed / (summary.total - summary.skipped)
def enforce_kill_score_gate(summary: MutationSummary, threshold: float = KILL_SCORE_THRESHOLD) -> float:
    if (score := kill_score(summary)) >= threshold: return score
    raise MutationGateError(f"kill score {score:.2f}% is below threshold {threshold}%: {summary.survived} survived")
def run_mutmut(project_dir: str | Path, paths_to_mutate: tuple[str, ...] | list[str], tests_dir: str,
               timeout: float = MUTMUT_TIMEOUT_SEC, runner: str | None = None) -> MutationSummary:
    python = resolve_mutmut_interpreter()  # MutmutUnavailableError lists every interpreter tried and why
    argv = [python, "-m", "mutmut", "run", "--paths-to-mutate", ",".join(paths_to_mutate), "--tests-dir",
            tests_dir, "--runner", runner or f'"{python}" -m pytest -x -q -p no:cacheprovider']
    try:  # no shell here; mutmut 2.5.1 itself hands the quoted --runner string to cmd.exe on Windows
        proc = subprocess.Popen(argv, cwd=str(project_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", creationflags=0x08000200 if os.name == "nt" else 0,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        return parse_mutmut_summary(proc.communicate(timeout=timeout)[0])  # non-zero exit only means survivors
    except OSError as exc:
        raise MutationGateError(f"mutmut could not start in {project_dir}: {type(exc).__name__}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:  # kill the whole tree: mutmut, its cmd.exe and the pytest inside
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)] if os.name == "nt" else ["kill", str(proc.pid)],
                       capture_output=True)
        proc.kill()
        tail = _drain_after_kill(proc)
        _restore_mutmut_backups(project_dir, paths_to_mutate)  # mutmut's own `.bak` restore never ran after the kill
        raise MutationGateError(f"mutmut exceeded {timeout} s; output tail: {tail[-800:]!r}") from exc
def _drain_after_kill(proc: subprocess.Popen) -> str:  # a surviving grandchild may hold the pipe: never block forever
    try:
        return proc.communicate(timeout=30)[0] or ""
    except subprocess.TimeoutExpired:
        return ""
def _restore_mutmut_backups(project_dir: str | Path, paths_to_mutate: tuple[str, ...] | list[str]) -> None:
    """mutmut 2.5.1 restores `<file>.bak` in a `finally` that a forced kill skips: put every original back."""
    for target in (Path(project_dir, rel) for rel in paths_to_mutate):
        for bak in target.rglob("*.py.bak") if target.is_dir() else (target.with_name(target.name + ".bak"),):
            if bak.is_file():
                os.replace(bak, bak.with_name(bak.name[:-4]))
# [MC-TDD-19] mutmut interpreter resolver (audit repair). mutmut is pinned to 2.5.1 (3.x refuses to run on native
# Windows) and may be installed in a different interpreter than the one running pytest (the auditor env has none).
# run_mutmut therefore launches `-m mutmut` with the interpreter this resolver PHYSICALLY proved, and the default
# inner runner (`"<python>" -m pytest ...`) uses that SAME interpreter: it is proven to import pytest as well, and
# mutants are then tested by the interpreter that mutmut itself runs under. Candidate order: $COCHEM_MUTMUT_PYTHON
# (authoritative, never falls through), sys.executable, `py -3` and `py -0p` (Windows launcher), python on PATH,
# PEP 514 registry entries, standard install directories. Nothing is hardcoded; every path is discovered.
import glob, shutil
MUTMUT_PYTHON_ENV: str = "COCHEM_MUTMUT_PYTHON"
MUTMUT_REQUIRED_VERSION: str = "2.5.1"
MUTMUT_PROBE_TIMEOUT_SEC: float = 60.0
_NO_WINDOW: int = 0x08000200 if os.name == "nt" else 0  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP (FR-007)
_RESOLVED_INTERPRETERS: dict[tuple[str, str, str], str] = {}  # successful resolutions only, keyed by their inputs
class MutmutUnavailableError(RuntimeError):
    """FR-008: no candidate interpreter physically answered `-m mutmut version` with the pinned version."""
    def __init__(self, rejected: list[tuple[str, str]]):
        self.rejected = list(rejected)
        tried = "; ".join(f"[{path}] rejected: {why}" for path, why in self.rejected) or "no candidate was found"
        super().__init__(f"no Python interpreter with mutmut {MUTMUT_REQUIRED_VERSION} and pytest; tried: {tried}")
def _run_probe(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace", timeout=MUTMUT_PROBE_TIMEOUT_SEC, creationflags=_NO_WINDOW,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
def probe_mutmut_interpreter(python: str) -> str | None:
    """None when `python -m mutmut version` prints the pinned version and `python -m pytest --version` works;
    otherwise the reason the interpreter is rejected. Both checks really execute the interpreter."""
    if not os.path.isfile(python):
        return "no such file"
    want = f"mutmut version {MUTMUT_REQUIRED_VERSION}"
    for args, ok in ((["-m", "mutmut", "version"], lambda out: out == want),
                     (["-m", "pytest", "--version"], lambda out: out.startswith("pytest "))):
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
        found += [m.group(1).strip() for m in (re.search(r"([A-Za-z]:\\.+|/.+)$", line.strip())
                                               for line in proc.stdout.splitlines()) if m]
    return found
def _path_interpreters() -> list[str]:
    names = ("python.exe", "python3.exe") if os.name == "nt" else ("python3", "python")
    first = [p for p in (shutil.which("python"),) if p]
    return first + [os.path.join(d, n) for d in os.environ.get("PATH", "").split(os.pathsep) if d for n in names
                    if os.path.isfile(os.path.join(d, n))]
def _registry_interpreters() -> list[str]:
    if os.name != "nt":
        return []
    import winreg
    found: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, r"Software\Python", 0, winreg.KEY_READ | view)
            except OSError:  # PEP 514 key absent in this hive/view: nothing registered there
                continue
            with root:
                for company in _registry_subkeys(root):
                    with winreg.OpenKey(root, company, 0, winreg.KEY_READ | view) as company_key:
                        for tag in _registry_subkeys(company_key):
                            try:
                                with winreg.OpenKey(company_key, tag + r"\InstallPath", 0,
                                                    winreg.KEY_READ | view) as ip:
                                    found.append(_registry_executable(ip))
                            except OSError:  # a tag without InstallPath (PEP 514 allows it) has no interpreter
                                continue
    return found
def _registry_subkeys(key) -> list[str]:
    import winreg
    names, index = [], 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:  # EnumKey signals the end of the subkey list with OSError (ERROR_NO_MORE_ITEMS)
            return names
        index += 1
def _registry_executable(install_path_key) -> str:
    import winreg
    try:
        return winreg.QueryValueEx(install_path_key, "ExecutablePath")[0]
    except OSError:  # PEP 514: without ExecutablePath the interpreter is python.exe in the default value dir
        return os.path.join(winreg.QueryValueEx(install_path_key, "")[0], "python.exe")
def _standard_location_interpreters() -> list[str]:
    exe = "python.exe" if os.name == "nt" else "python3"
    roots = [os.environ.get("SystemDrive", "C:") + os.sep, os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
             "Python"), os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
    return [p for root in roots if root.strip(os.sep) for p in sorted(glob.glob(os.path.join(root, "Python3*", exe)),
                                                                       reverse=True)]
def _candidate_interpreters(rejected: list[tuple[str, str]]):
    """Lazy candidate stream: later discovery steps only run when every earlier candidate was rejected."""
    yield sys.executable
    for discover in (lambda: _launcher_interpreters(rejected), _path_interpreters, _registry_interpreters,
                     _standard_location_interpreters):
        yield from discover()
def resolve_mutmut_interpreter() -> str:
    """Absolute path of an interpreter physically proven to run mutmut MUTMUT_REQUIRED_VERSION and pytest.
    Raises MutmutUnavailableError naming every candidate tried and why it was rejected."""
    override = os.environ.get(MUTMUT_PYTHON_ENV, "").strip()
    key = (override, sys.executable, os.environ.get("PATH", ""))
    if key in _RESOLVED_INTERPRETERS:
        return _RESOLVED_INTERPRETERS[key]
    rejected: list[tuple[str, str]] = []
    if override:  # an explicit override is authoritative: never silently fall through to another interpreter
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
