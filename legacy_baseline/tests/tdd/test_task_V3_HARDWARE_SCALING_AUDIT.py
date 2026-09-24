"""TDD contract for task V3_HARDWARE_SCALING_AUDIT.

The task audits the v3 pipeline's hardware-adaptive concurrency and daemon
start-up path and delivers two artefacts at the workspace root:

* ``Pipeline_3_0_0_Startup.bat`` - a headless (CI=1 / NO_COLOR=1), windowless,
  non-interactive, credential-free launcher for the three pipeline daemons
  (task_work_loop.py, cochem_ha_daemon_watchdog.py, cochem_idle_sidecar.py).
* ``HARDWARE_SCALING_AUDIT_REPORT.md`` - a formal audit report whose claims are
  cross-checked against the audited source code.

Audited existing code (must keep passing, never downgraded):

* ``.scripts/task_work_loop.py`` - ``_compute_max_workers`` (live psutil /
  nvidia-smi telemetry -> tiered worker count clamped to [_HW_FLOOR, _HW_CEIL])
  and ``_refresh_worker_semaphore`` (resizes the global asyncio.Semaphore).
* ``__agentic/v3/docker_runner.py`` - ``DockerSandboxRunner`` builds hardened,
  ephemeral ``docker run`` argv and executes jobs WITHOUT any global
  serialisation (no locks / semaphores / fixed container names).

Tests:
    [T1] AC1 start-up script safety and daemon configuration
    [T2] AC2 task_work_loop hardware-scaling invariants (real code paths)
    [T3] AC3 docker runner isolation without concurrency throttling
    [T4] AC4 formal audit report completeness and code cross-check
    [T5] AC5 zero-mock / anti-spoofing / subprocess compliance

No mocking library is used anywhere: every assertion exercises real modules,
real subprocesses and real files.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import inspect
import itertools
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# -- Path setup ---------------------------------------------------------------
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = WORKSPACE_ROOT / ".scripts"
V3_DIR = WORKSPACE_ROOT / "__agentic" / "v3"
for _p in (V3_DIR, SCRIPTS_DIR, WORKSPACE_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

BAT = WORKSPACE_ROOT / "Pipeline_3_0_0_Startup.bat"
REPORT = WORKSPACE_ROOT / "HARDWARE_SCALING_AUDIT_REPORT.md"
TWL = SCRIPTS_DIR / "task_work_loop.py"
DOCKER_RUNNER = V3_DIR / "docker_runner.py"

_ENV_MAX = "COCHEM_MAX_CONCURRENT_PROCESSES"
_ENV_MIN = "COCHEM_MIN_CONCURRENT_PROCESSES"
_MODULE_COUNTER = itertools.count()


# -- Helpers ------------------------------------------------------------------
@contextlib.contextmanager
def _env(**vals):
    """Set environment variables; restore originals (or delete) on exit.

    Restores every key in ``vals`` even if code inside the block mutated
    os.environ for those keys directly.
    """
    saved = {k: os.environ.get(k) for k in vals}
    try:
        for key, value in vals.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, original in saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def _load_twl(ceil: int | None, floor: int | None):
    """Load a FRESH task_work_loop module instance with the given env ceiling/floor."""
    saved = {k: os.environ.get(k) for k in (_ENV_MAX, _ENV_MIN)}
    try:
        if ceil is not None:
            os.environ[_ENV_MAX] = str(ceil)
        if floor is not None:
            os.environ[_ENV_MIN] = str(floor)
        name = "twl_hw_audit_%d" % next(_MODULE_COUNTER)
        spec = importlib.util.spec_from_file_location(name, TWL)
        assert spec is not None and spec.loader is not None, f"cannot load {TWL}"
        module = importlib.util.module_from_spec(spec)
        # Registered before exec: dataclasses resolve their defining module via sys.modules.
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, original in saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def _load_docker_runner():
    """Load a fresh docker_runner module instance (registered in sys.modules)."""
    name = "docker_runner_hw_audit_%d" % next(_MODULE_COUNTER)
    spec = importlib.util.spec_from_file_location(name, DOCKER_RUNNER)
    assert spec is not None and spec.loader is not None, f"cannot load {DOCKER_RUNNER}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _has_pair(argv, flag, value) -> bool:
    """True if argv holds adjacent tokens [flag, value] or the token 'flag=value'."""
    for idx, token in enumerate(argv):
        if token == f"{flag}={value}":
            return True
        if token == flag and idx + 1 < len(argv) and argv[idx + 1] == value:
            return True
    return False


def _flag_value(argv, flag):
    """Return the value following ``flag`` (or from 'flag=value'); None if absent."""
    for idx, token in enumerate(argv):
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
        if token == flag and idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _section(text: str, title: str) -> str:
    """Return the body of the level-2/3 heading ``title`` up to the next level 1-3 heading."""
    heading = re.search(r"^#{2,3}\s+" + re.escape(title) + r"\s*$", text, re.M | re.I)
    assert heading, f"report is missing section heading: {title!r}"
    rest = text[heading.end():]
    nxt = re.search(r"^#{1,3}\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


_ISOLATION_PAIRS = (("--network", "none"), ("--user", "1000:1000"), ("--cap-drop", "ALL"))


# -- T1 -----------------------------------------------------------------------
_SET_RE = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$', re.I)
# Interpreter token: python / pythonw / py (optionally .exe) NOT preceded by '.' or a
# word character, so the '.py' suffix of a script name is not mistaken for the
# 'py' launcher (which would turn every line naming a script into a "launch line").
_INTERP_RE = re.compile(r"(?<![.\w])(python|pythonw|py)(\.exe)?\b", re.I)
_DAEMONS = ("task_work_loop.py", "cochem_ha_daemon_watchdog.py", "cochem_idle_sidecar.py")


def _expand_bat_path(raw: str, assignments: dict) -> str:
    """Expand %~dp0 and %NAME% references using the batch file's own set assignments."""
    value = raw
    for _ in range(5):
        value = re.sub(r"%~dp0", lambda _m: str(BAT.parent) + os.sep, value, flags=re.I)
        value = re.sub(
            r"%([A-Za-z_][A-Za-z0-9_]*)%",
            lambda m: assignments.get(m.group(1).upper(), m.group(0)),
            value,
        )
    return value


def test_pipeline_startup_script_safety_and_daemon_configuration():
    """[T1] AC1: headless, windowless, non-interactive, credential-free daemon launcher."""
    assert BAT.is_file(), f"missing deliverable: {BAT}"
    with open(BAT, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    assert text.strip(), "startup script is empty"

    # Logical lines without comments; indices refer to this filtered list.
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^@?(rem\b|::)", line, re.I):
            continue
        lines.append(line)

    assignments: dict[str, str] = {}
    set_index: dict[tuple[str, str], int] = {}
    for idx, line in enumerate(lines):
        match = _SET_RE.match(line.lstrip("@"))
        if match:
            name = match.group(1).upper()
            assignments[name] = match.group(2)
            set_index.setdefault((name, match.group(2).strip()), idx)

    # Headless environment.
    assert assignments.get("CI") == "1", "startup script must `set CI=1`"
    assert assignments.get("NO_COLOR") == "1", "startup script must `set NO_COLOR=1`"
    assert assignments.get("NONINTERACTIVE") == "1" or assignments.get("TERM", "").lower() == "dumb", \
        "startup script must set NONINTERACTIVE=1 or TERM=dumb"

    # Launch lines: interpreter invocations of .py scripts (set assignments excluded).
    launch = [
        (idx, line) for idx, line in enumerate(lines)
        if ".py" in line.lower() and _INTERP_RE.search(line) and not _SET_RE.match(line.lstrip("@"))
    ]
    assert launch, "no python daemon launch lines found"
    for _idx, line in launch:
        assert "cmd /k" not in line.lower(), f"interactive console left open: {line}"

    first_launch = launch[0][0]
    assert ("CI", "1") in set_index and set_index[("CI", "1")] < first_launch, \
        "CI=1 must be set before the first daemon launch"
    assert ("NO_COLOR", "1") in set_index and set_index[("NO_COLOR", "1")] < first_launch, \
        "NO_COLOR=1 must be set before the first daemon launch"

    for daemon in _DAEMONS:
        daemon_lines = [line for _i, line in launch if daemon.lower() in line.lower()]
        assert daemon_lines, f"no launch line for daemon {daemon}"
        for line in daemon_lines:
            windowless = bool(
                (re.search(r"\bstart\b", line, re.I) and re.search(r"(^|\s)/b(\s|$)", line, re.I))
                or "pythonw" in line.lower()
                or re.search(r"-windowstyle\s+hidden", line, re.I)
            )
            assert windowless, f"daemon launch is not windowless: {line}"

            # Entrypoint validity: the referenced script must exist on this host.
            candidates = []
            for m in re.finditer(r'"([^"]+?\.py)"|(\S+?\.py)\b', line, re.I):
                token = m.group(1) or m.group(2)
                if daemon.lower() in token.lower():
                    candidates.append(token)
            assert candidates, f"cannot extract {daemon} path from: {line}"
            expanded = _expand_bat_path(candidates[0].strip('"'), assignments)
            assert "%" not in expanded, f"unresolved variable in daemon path: {expanded}"
            path = Path(expanded)
            if not path.is_absolute():
                path = BAT.parent / path
            assert path.is_file(), f"daemon entrypoint does not exist: {path}"

    # No interactive commands.
    for line in lines:
        tokens = line.lstrip("@").split()
        first = tokens[0].lower() if tokens else ""
        assert first not in ("pause", "choice"), f"interactive command in startup script: {line}"
    assert not re.search(r"\bset\s+/p\b", text, re.I), "`set /p` prompts are forbidden"

    # No credentials.
    secret_patterns = (
        r"sk-[A-Za-z0-9_\-]{16,}", r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
        r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{30,}", r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}",
        r"(?i)password\s*[:=]\s*\S+",
    )
    for pattern in secret_patterns:
        assert not re.search(pattern, text), f"credential-like literal matches {pattern!r}"
    for name, value in assignments.items():
        if re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", name):
            literal = value.strip()
            assert not literal or re.fullmatch(r"%[A-Za-z_][A-Za-z0-9_]*%", literal), \
                f"secret-like variable {name} carries a literal value"


# -- T2 -----------------------------------------------------------------------
def test_task_work_loop_hardware_scaling_invariants():
    """[T2] AC2: live telemetry, tier table, floor/ceiling clamp and semaphore resizing."""
    # Static: live telemetry is still wired in (not downgraded to a static limit).
    probe = _load_twl(40, 3)
    source = inspect.getsource(probe._compute_max_workers)
    for token in ("cpu_percent", "virtual_memory", "nvidia-smi", "CREATE_NO_WINDOW"):
        assert token in source, f"_compute_max_workers lost telemetry token {token!r}"

    # Deterministic tiers via the module's real psutil-unavailable fallback
    # (cpu=50.0, ram_free=8.0 GB). Per the documented tier table this lands in the
    # "CPU < 70% and RAM free > 4 GB" tier => expected = max(floor, ceil // 2).
    for ceil, floor, expected in [(40, 3, 20), (100, 1, 50), (12, 9, 9)]:
        m = _load_twl(ceil, floor)
        assert m._HW_CEIL == ceil and m._HW_FLOOR == floor
        m._PSUTIL_AVAILABLE = False
        with _env(**{_ENV_MAX: str(ceil), _ENV_MIN: str(floor)}):
            result = m._compute_max_workers()
        assert result == expected, f"ceil={ceil} floor={floor}: got {result}, want {expected}"
        assert floor <= result <= ceil

    # Live telemetry: real psutil + nvidia-smi path; result must be a valid tier value.
    m = _load_twl(40, 3)
    assert m._PSUTIL_AVAILABLE is True, "psutil must be installed for live hardware telemetry"
    with _env(**{_ENV_MAX: "40", _ENV_MIN: "3"}):
        live = m._compute_max_workers()
    allowed = set()
    for c in (40, max(3, 40 // 4)):  # un-capped ceiling and GPU-VRAM-capped ceiling
        allowed |= {c, max(3, c * 3 // 4), max(3, c // 2), max(3, max(4, c // 4)), 3}
    assert live in allowed, f"live worker count {live} not in tier table {sorted(allowed)}"
    assert 3 <= live <= 40

    # Semaphore resize on a real event loop.
    m = _load_twl(40, 3)
    m._PSUTIL_AVAILABLE = False

    async def _exercise():
        limit = m._refresh_worker_semaphore()
        assert limit == 20
        assert m._current_worker_limit == 20
        assert isinstance(m._worker_semaphore, asyncio.Semaphore)
        sem1 = m._worker_semaphore
        assert m._refresh_worker_semaphore() == 20
        assert m._worker_semaphore is sem1, "semaphore rebuilt although limit unchanged"

        # Lower the ceiling both in env and module global so the test holds whether
        # the ceiling is re-read from the environment or from _HW_CEIL.
        os.environ[_ENV_MAX] = "16"
        m._HW_CEIL = 16
        limit2 = m._refresh_worker_semaphore()
        assert limit2 == 8
        assert m._current_worker_limit == 8
        sem = m._worker_semaphore
        assert sem is not sem1, "semaphore was not resized"
        for _ in range(8):
            await asyncio.wait_for(sem.acquire(), 1.0)
        assert sem.locked(), "new semaphore should be exhausted after 8 acquisitions"
        timed_out = False
        try:
            await asyncio.wait_for(sem.acquire(), 0.2)
        except asyncio.TimeoutError:
            timed_out = True
        assert timed_out, "9th acquisition must block on an 8-slot semaphore"
        for _ in range(8):
            sem.release()
        assert not sem.locked()

    # _env covers (and restores) the in-coroutine os.environ change as well.
    with _env(**{_ENV_MAX: "40", _ENV_MIN: "3"}):
        asyncio.run(_exercise())


# -- T3 -----------------------------------------------------------------------
_FORBIDDEN_SYNC = {"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition", "Barrier",
                   "Queue", "LifoQueue", "PriorityQueue", "Event"}


def _builder(runner):
    """Resolve the runner's argv builder."""
    return getattr(runner, "build_run_command", None) or getattr(runner, "build_docker_cmd")


def _executor(runner):
    """Resolve the runner's execution entry point."""
    return getattr(runner, "execute", None) or getattr(runner, "run_command")


def _assert_isolation(argv, where):
    """Assert the core isolation flags are present in ``argv``."""
    assert "--rm" in argv and "--read-only" in argv, f"{where}: missing --rm/--read-only"
    for flag, value in _ISOLATION_PAIRS:
        assert _has_pair(argv, flag, value), f"{where}: missing {flag} {value}"


def test_docker_runner_isolation_without_concurrency_throttling():
    """[T3] AC3: hardened ephemeral argv and truly parallel execution (no serialisation)."""
    dr = _load_docker_runner()
    runner = dr.DockerSandboxRunner()
    user_cmd = ["python", "-c", "print(1)"]
    argv = list(_builder(runner)(list(user_cmd)))

    assert "run" in argv
    _assert_isolation(argv, "default argv")
    cpus = _flag_value(argv, "--cpus")
    assert cpus is not None and float(cpus) > 0, "--cpus limit missing"
    memory = _flag_value(argv, "--memory")
    assert memory, "--memory limit missing"
    assert argv[-len(user_cmd):] == user_cmd, "user command must be the argv tail"
    image = runner.config.image
    image_idx = len(argv) - len(user_cmd) - 1
    assert argv[image_idx] == image, "image must immediately precede the user command"
    for flag in ("--rm", "--read-only", "--network", "--user", "--cap-drop", "--cpus", "--memory"):
        positions = [i for i, t in enumerate(argv) if t == flag or t.startswith(flag + "=")]
        assert positions and max(positions) < image_idx, f"{flag} must precede the image"
    assert "--privileged" not in argv
    assert not _has_pair(argv, "--network", "host")
    assert "docker.sock" not in " ".join(argv)
    assert not any(t == "--name" or t.startswith("--name=") for t in argv), \
        "a fixed container name would serialise jobs"

    # Static: no synchronisation primitives or global state in the runner module.
    with open(DOCKER_RUNNER, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=str(DOCKER_RUNNER))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_SYNC, f"docker_runner constructs {name} (line {node.lineno})"
        assert not isinstance(node, ast.Global), f"`global` statement at line {node.lineno}"

    # Real parallel execution. ``docker_cli`` is the runner's real public
    # configuration hook: substituting the Python interpreter for the docker
    # binary runs the genuine execute() code path (real subprocesses, real Popen
    # with CREATE_NO_WINDOW) on hosts without Docker, and the probe echoes back
    # the exact isolation argv the runner produced.
    probe = "import sys, time, json; time.sleep(1.5); print(json.dumps(sys.argv[1:]))"
    config = dr.DockerCommandConfig(docker_cli=(sys.executable, "-c", probe))
    shared = dr.DockerSandboxRunner(config)
    # Jobs 0 and 1 use separate runner instances; jobs 2 and 3 share one instance.
    runners = [dr.DockerSandboxRunner(config), dr.DockerSandboxRunner(config), shared, shared]

    def _job(i):
        return _executor(runners[i])(["echo", f"job-{i}"], timeout=60)

    n_jobs = 4
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_jobs) as pool:
        results = list(pool.map(_job, range(n_jobs)))
    elapsed = time.perf_counter() - started

    for i, result in enumerate(results):
        assert result.exit_code == 0, f"job {i} failed: {getattr(result, 'stderr', '')}"
        echoed = json.loads(result.stdout.strip().splitlines()[-1])
        assert echoed[0] == "run"
        _assert_isolation(echoed, f"job {i}")
        assert echoed[-2:] == ["echo", f"job-{i}"]
    # Sequential execution would need >= 4 * 1.5 = 6.0 s.
    assert elapsed < 4.5, f"jobs appear serialised: {elapsed:.2f}s for {n_jobs} x 1.5s probes"


# -- T4 -----------------------------------------------------------------------
_SECTIONS = ("Executive Summary", "Dynamic Hardware Scaling Invariants", "Daemon Startup Audit",
             "Docker Concurrency Evaluation", "Audit Verdict")


def test_hardware_scaling_audit_formal_report_completeness():
    """[T4] AC4: formal audit report with ordered sections cross-checked against code."""
    assert REPORT.is_file(), f"missing deliverable: {REPORT}"
    with open(REPORT, encoding="utf-8") as fh:
        text = fh.read()
    assert len(text.strip()) >= 2000, "report too short"
    assert re.search(r"^#\s+HARDWARE SCALING AUDIT REPORT", text, re.M | re.I), "missing report title"

    positions = []
    for title in _SECTIONS:
        match = re.search(r"^#{2,3}\s+" + re.escape(title) + r"\s*$", text, re.M | re.I)
        assert match, f"missing section heading {title!r}"
        positions.append(match.start())
        assert len(_section(text, title).strip()) >= 150, f"section {title!r} is too thin"
    assert positions == sorted(positions), "report sections are out of order"

    scaling = _section(text, "Dynamic Hardware Scaling Invariants")
    for token in ("_compute_max_workers", "_refresh_worker_semaphore", "_HW_FLOOR", "_HW_CEIL",
                  _ENV_MAX, _ENV_MIN, "psutil", "nvidia-smi", "30%", "50%", "70%", "85%", "80%"):
        assert token in scaling, f"scaling section lacks {token!r}"
    assert re.search(r"^\s*\|", scaling, re.M), "scaling section needs a markdown tier table"
    with open(TWL, encoding="utf-8") as fh:
        twl_source = fh.read()
    ceil_match = re.search(
        r'_HW_CEIL\s*=\s*int\(os\.environ\.get\(\s*"COCHEM_MAX_CONCURRENT_PROCESSES"\s*,\s*"(\d+)"',
        twl_source)
    assert ceil_match, "cannot parse default _HW_CEIL from task_work_loop.py"
    assert re.search(r"\b" + ceil_match.group(1) + r"\b", scaling), \
        f"scaling section does not state the real default ceiling {ceil_match.group(1)}"

    daemon = _section(text, "Daemon Startup Audit")
    for token in ("Pipeline_3_0_0_Startup.bat", "user32.dll", "CI=1", "NO_COLOR=1",
                  "task_work_loop", "cochem_ha_daemon_watchdog", "cochem_idle_sidecar"):
        assert token in daemon, f"daemon section lacks {token!r}"

    docker = _section(text, "Docker Concurrency Evaluation")
    for token in ("DockerSandboxRunner", "--rm", "--read-only", "--network none", "--cap-drop ALL"):
        assert token in docker, f"docker section lacks {token!r}"
    assert re.search(r"semaphore|lock", docker, re.I), "docker section must discuss locks/semaphores"

    verdict = _section(text, "Audit Verdict")
    assert re.search(r"\bpreserved\b", verdict, re.I), "verdict must state scaling is preserved"
    assert not re.search(r"\b(was|has been|is|were)\s+downgraded\b", verdict, re.I), \
        "verdict reports a downgrade"

    lowered = text.lower()
    for marker in ("todo", "tbd", "lorem ipsum", "<fill", "placeholder"):
        assert marker not in lowered, f"report contains placeholder marker {marker!r}"


# -- T5 -----------------------------------------------------------------------
# Forbidden names are assembled by concatenation so this module stays clean.
_MOCK = "mo" + "ck"
_UNITTEST_MOCK = "unittest." + _MOCK
_MOCK_NAMES = {"Magic" + "Mock", "Mo" + "ck", "Async" + "Mock", "pat" + "ch"}
_NOT_IMPL = "NotImpl" + "ementedError"
_SUBPROCESS_CALLEES = {"subprocess." + n for n in ("run", "Popen", "call", "check_call", "check_output")}


def _is_mock_module(name: str | None) -> bool:
    """True if ``name`` is a mocking module (mock / unittest.mock or a submodule)."""
    if not name:
        return False
    return (name in (_MOCK, _UNITTEST_MOCK)
            or name.startswith(_MOCK + ".") or name.startswith(_UNITTEST_MOCK + "."))


def _is_stub_body(body) -> bool:
    """True if a function body (after a leading docstring) is empty or only pass/ellipsis."""
    stmts = list(body)
    if stmts and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, ast.Constant) \
            and isinstance(stmts[0].value.value, str):
        stmts = stmts[1:]
    return all(
        isinstance(s, ast.Pass)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis)
        for s in stmts
    )


def _dict_has(tree, key, check) -> bool:
    """True if any Dict inside ``tree`` maps constant ``key`` to a value satisfying ``check``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == key and check(v):
                    return True
    return False


def _mapping_provides(scope, name, key, check) -> bool:
    """True if ``name`` in ``scope`` is assigned a dict (or subscript) carrying key -> check."""
    for node in ast.walk(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name and _dict_has(node.value, key, check):
                return True
            if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                    and target.value.id == name and isinstance(target.slice, ast.Constant)
                    and target.slice.value == key and check(node.value)):
                return True
    return False


def _is_no_window(value) -> bool:
    """True if the expression references CREATE_NO_WINDOW."""
    return "CREATE_NO_WINDOW" in ast.unparse(value)


def _is_utf8(value) -> bool:
    """True if the expression is the constant 'utf-8' / 'utf8' (any case)."""
    return isinstance(value, ast.Constant) and isinstance(value.value, str) \
        and value.value.lower() in ("utf-8", "utf8")


def test_hardware_scaling_zero_mock_and_anti_spoofing_compliance():
    """[T5] AC5: no mocks, no stubs, and hidden-window utf-8 subprocess calls."""
    audited = (TWL, DOCKER_RUNNER, Path(__file__).resolve())
    trees = {}
    for path in audited:
        assert path.is_file(), f"audited file missing: {path}"
        with open(path, encoding="utf-8") as fh:
            trees[path] = ast.parse(fh.read(), filename=str(path))

    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not _is_mock_module(alias.name), f"{path.name}:{node.lineno} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not _is_mock_module(module), f"{path.name}:{node.lineno} imports from {module}"
                if module == "unittest":
                    assert all(a.name != _MOCK for a in node.names), \
                        f"{path.name}:{node.lineno} imports unittest.{_MOCK}"
                    assert not any(a.name in _MOCK_NAMES for a in node.names), \
                        f"{path.name}:{node.lineno} imports mock helpers"
            elif isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                assert not (isinstance(exc, ast.Name) and exc.id == _NOT_IMPL), \
                    f"{path.name}:{node.lineno} raises {_NOT_IMPL}"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert not _is_stub_body(node.body), f"{path.name}:{node.lineno} stub function {node.name}"

    # Subprocess compliance for the audited production files.
    violations = []
    for path in (TWL, DOCKER_RUNNER):
        tree = trees[path]
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        found = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = ast.unparse(node.func)
            if callee not in _SUBPROCESS_CALLEES and not callee.endswith(
                    ("create_subprocess_exec", "create_subprocess_shell")):
                continue
            found += 1
            # Innermost enclosing function (module scope if top-level).
            scope = parents.get(node)
            while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = parents.get(scope)
            scope = scope if scope is not None else tree

            flags_ok = any(kw.arg == "creationflags" and _is_no_window(kw.value) for kw in node.keywords)
            enc_ok = any(kw.arg == "encoding" and _is_utf8(kw.value) for kw in node.keywords)
            for kw in node.keywords:
                if kw.arg is None and isinstance(kw.value, ast.Name):
                    flags_ok = flags_ok or _mapping_provides(scope, kw.value.id, "creationflags", _is_no_window)
                    enc_ok = enc_ok or _mapping_provides(scope, kw.value.id, "encoding", _is_utf8)
            missing = [k for k, ok in (("creationflags", flags_ok), ("encoding", enc_ok)) if not ok]
            if missing:
                violations.append(f"{path.name}:{node.lineno} {callee} missing={','.join(missing)}")
        assert found > 0, f"no subprocess calls found in {path.name} (vacuous audit)"
    assert not violations, "subprocess compliance violations:\n" + "\n".join(violations)
