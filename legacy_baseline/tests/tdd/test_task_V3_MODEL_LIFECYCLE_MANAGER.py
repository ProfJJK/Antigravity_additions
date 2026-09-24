"""TDD contract for V3_MODEL_LIFECYCLE_MANAGER (RED phase).

Deliverable under test: __agentic/v3/model_lifecycle_manager.py (workspace-local).
The tests load it with importlib.util.spec_from_file_location; every test fails
individually (clear assertion in ``_load_module``) while the file is absent.

PUBLIC API CONTRACT
===================

DockerRunResult  (dataclass)
    Fields: exit_code: int, stdout: str, stderr: str, cmd: list[str].
    Extra fields are allowed only if they have defaults. Tests construct it
    with keywords: DockerRunResult(exit_code=0, stdout="...", stderr="", cmd=[...]).

DockerSandboxRunner(docker_cli: Sequence[str] = ("docker",), image: str = "cochem-v3:latest")
    .docker_cli / .image hold the constructor values.
    .build_docker_args(image, test_command, volume_mount="cochem-data:/workspace/data") -> list[str]
        == [*docker_cli, "run", "--rm", "--read-only", "--user", "1000:1000",
            "--network", "none", "-v", volume_mount, ...other hardening / -e flags...,
            image, *test_command]
        i.e. the prefix equals docker_cli, the next token is "run", every
        hardening flag (and its value) appears BEFORE the image, and the argv
        ends with ``image`` followed by ``test_command`` exactly.
    .run(cmd_args, timeout=120) -> DockerRunResult
        Executes cmd_args with subprocess (creationflags=subprocess.CREATE_NO_WINDOW,
        encoding='utf-8', captured stdout/stderr) and returns the RAW exit code
        and streams, with cmd == cmd_args. Must NOT raise on a non-zero exit.

V3ModelLifecycleManager(db_path, schema_path=None, docker_runner=None,
                        router_path=None, patch_dir=None)
    - Opens SQLite with PRAGMA journal_mode=WAL, busy_timeout=5000,
      foreign_keys=ON. If schema_path is given it executes that script
      (CREATE ... IF NOT EXISTS) so a brand-new db file gets the tables.
    - Exposes .conn and/or .get_connection() -> sqlite3.Connection; .close().
    - router_path: the llm_router.py whose MODEL_REGISTRY (a pure literal
      assigned at module top level) is the source of truth. It is READ via
      ast, NEVER imported and NEVER written.
    - patch_dir: directory where patch files are saved (create it if missing).

    .discover_models(cli_prefixes: dict[str, list[str]]) -> dict[str, list[str]]
        Keys: "claude-subscription", "gemini", "ollama". Each argv is executed
        via subprocess (it may be run as-is; if the implementation appends
        subcommand args the stand-ins ignore them). Parsing of stdout:
          claude-subscription -> tokens matching r"claude-[a-z0-9][a-z0-9.-]*[a-z0-9]"
          gemini              -> tokens matching r"gemini-[a-z0-9][a-z0-9.-]*[a-z0-9]"
          ollama              -> first whitespace-separated token of every
                                 non-empty line, except the header line that
                                 starts with "NAME" (``ollama list`` table)
        Returns per provider the de-duplicated, order-preserving list of ids
        NOT present anywhere (primary "model" or any fallback model) in the
        router_path MODEL_REGISTRY. A provider whose CLI is missing
        (FileNotFoundError/OSError) or exits non-zero yields [] (key absent or
        []) and must not raise; the other providers are still discovered.

    .execute_benchmark(candidate, provider, test_suite="tests/benchmark") -> DockerRunResult
        Builds argv via docker_runner.build_docker_args(docker_runner.image, ...)
        and runs it via docker_runner.run. The container command (after the
        image) must invoke pytest on test_suite, and the candidate id must
        appear in the argv (e.g. -e COCHEM_CANDIDATE_MODEL=<id>, or as an arg).

    .evaluate_benchmark(candidate, run_result, task_id) -> bool
        True iff exit_code == 0 AND the output shows no failed/error tests
        (pytest summary such as "1 failed" / "1 error") AND at least one
        "passed". Returns a real bool. Inserts (and commits) exactly one
        kanban_telemetry_v3 row per call: task_id, event_type
        'MODEL_BENCHMARK_EVALUATED', payload = JSON object with keys
        "candidate", "exit_code", "approved" (plus an optional stdout snippet).
        task_id must already exist in kanban_tasks_v3 (FK).

    .generate_patch(candidate, provider, target_role, estimated_cost=1) -> str
        Unified diff against router_path content: header lines '--- ' and
        '+++ ' ending with 'llm_router.py', one or more '@@ -a,b +c,d @@' hunks.
        Changes ONLY MODEL_REGISTRY[target_role]: provider/model become
        provider/candidate, with a fallback chain satisfying (V2 rules, see
        __agentic/v2/MODEL_REGISTRY_V2.py; costs = MODEL_TIERS cost_in plus
        {candidate: estimated_cost}):
          R1 first fallback on the OTHER cloud provider (claude vs gemini)
          R2 every cloud fallback is a known tier model on its listed provider
             with cost not above the primary cost (floor exception as in V2)
          R4 exactly one ("halt", ...) sentinel and it is LAST
          R5 no duplicate (provider, model) in [primary] + fallbacks
          R6 every V2 ASYMMETRY_PAIRS (producer, verifier) keeps primaries on
             DIFFERENT providers
        Code outside the MODEL_REGISTRY literal and all other entries stay
        untouched. Raises ValueError if the change would violate R6, or if
        provider is not "claude-subscription"/"gemini". Never writes router_path.

    .submit_patch_task(patch_content, patch_filename, task_id=None) -> str
        Writes patch_dir/patch_filename with the exact content (utf-8; use
        newline='' or bytes), inserts a kanban_tasks_v3 row with
        workflow_type='audit', priority=1, status='todo', payload_uri = the
        saved patch path (plain local path or file URI) and returns the task_id.
        Idempotent: resubmitting identical content returns the SAME task_id
        without a duplicate row (also when the same explicit task_id is given).
        Never modifies router_path.

Test rules: real SQLite built from schema_v3.sql, real files in tmp_path, real
subprocesses running ``sys.executable`` stand-in scripts instead of
docker/claude/gemini/ollama. No mocking and no monkeypatching; a process audit
hook fails any test that attempts network access.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
import urllib.parse
import uuid
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_V3 = WORKSPACE_ROOT / "__agentic" / "v3"
for _p in (WORKSPACE_ROOT, WORKSPACE_V3):
    if str(_p) in sys.path:
        sys.path.remove(str(_p))
    sys.path.insert(0, str(_p))

MODULE_NAME = "model_lifecycle_manager"
MODULE_PATH = WORKSPACE_V3 / f"{MODULE_NAME}.py"
SCHEMA_PATH = WORKSPACE_V3 / "schema_v3.sql"
REAL_ROUTER = WORKSPACE_ROOT / "llm_router.py"
V2_REGISTRY_PATH = WORKSPACE_ROOT / "__agentic" / "v2" / "MODEL_REGISTRY_V2.py"

CLAUDE = "claude-subscription"
GEMINI = "gemini"
OLLAMA = "ollama"
HALT = "halt"
CLOUD_PROVIDERS = {CLAUDE, GEMINI}
NO_NEWLINE_MARKER = "\\"  # unified-diff "\ No newline at end of file" marker lines

SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
FORBIDDEN_IMPORTS = {
    "unittest.mock", "mock", "pytest", "requests", "httpx", "urllib.request",
    "urllib3", "http.client", "socket", "aiohttp",
}

# ── Offline guard (audit hook; no monkeypatching) ─────────────────────────────
_NETWORK_EVENTS: list[str] = []
_NETWORK_GUARD = {"armed": False}


def _network_audit_hook(event: str, args) -> None:
    if _NETWORK_GUARD["armed"] and event in (
        "socket.connect", "socket.getaddrinfo", "socket.sendto", "urllib.Request",
    ):
        _NETWORK_EVENTS.append(f"{event}: {args!r}")
        raise RuntimeError(f"network access forbidden in V3ModelLifecycleManager: {event}")


sys.addaudithook(_network_audit_hook)


@pytest.fixture(autouse=True)
def _offline_guard():
    _NETWORK_EVENTS.clear()
    _NETWORK_GUARD["armed"] = True
    try:
        yield
    finally:
        _NETWORK_GUARD["armed"] = False
    assert not _NETWORK_EVENTS, f"network activity detected: {_NETWORK_EVENTS}"


@pytest.fixture
def managers():
    """Collects every manager a test creates and closes them at teardown."""
    created: list = []
    yield created
    for mgr in reversed(created):
        mgr.close()


# ── Loaders ───────────────────────────────────────────────────────────────────
def _load_module():
    """Load the deliverable from MODULE_PATH (fails each test while absent)."""
    assert MODULE_PATH.is_file(), (
        f"deliverable missing: {MODULE_PATH} - implement V3ModelLifecycleManager, "
        "DockerSandboxRunner and DockerRunResult there (see module docstring)"
    )
    spec = importlib.util.spec_from_file_location(MODULE_NAME, str(MODULE_PATH))
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    for name in ("V3ModelLifecycleManager", "DockerSandboxRunner", "DockerRunResult"):
        obj = getattr(module, name, None)
        assert isinstance(obj, type), f"{MODULE_PATH.name} must define class {name}"
    return module


_V2_CACHE: dict = {}


def _load_v2_tiers():
    """MODEL_TIERS and ASYMMETRY_PAIRS from the side-effect-free V2 registry."""
    if not _V2_CACHE:
        assert V2_REGISTRY_PATH.is_file(), f"V2 registry missing at {V2_REGISTRY_PATH}"
        spec = importlib.util.spec_from_file_location("_mlm_model_registry_v2", str(V2_REGISTRY_PATH))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _V2_CACHE["tiers"] = dict(module.MODEL_TIERS)
        _V2_CACHE["pairs"] = tuple(module.ASYMMETRY_PAIRS)
    return _V2_CACHE["tiers"], _V2_CACHE["pairs"]


# ── Router registry helpers (ast only; llm_router is never imported) ─────────
def _registry_node(source: str) -> ast.stmt:
    """Top-level ``MODEL_REGISTRY = {...}`` statement of a router source."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "MODEL_REGISTRY" for t in node.targets
        ):
            return node
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "MODEL_REGISTRY" and node.value is not None):
            return node
    raise AssertionError("no top-level MODEL_REGISTRY assignment found")


def _extract_registry(source: str) -> dict:
    return ast.literal_eval(_registry_node(source).value)


def _known_models(registry: dict) -> set[str]:
    """Every model string of the registry: primaries and all fallbacks."""
    known: set[str] = set()
    for entry in registry.values():
        known.add(entry["model"])
        for _prov, model in entry.get("fallbacks", []):
            known.add(model)
    return known


def _read_text_exact(path: Path) -> str:
    """Read utf-8 text keeping the original (CRLF) line endings."""
    return path.read_bytes().decode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_router(tmp_path: Path, name: str = "llm_router.py") -> Path:
    assert REAL_ROUTER.is_file(), f"router missing at {REAL_ROUTER}"
    target_dir = tmp_path / f"router_{uuid.uuid4().hex[:8]}"
    target_dir.mkdir()
    target = target_dir / name
    shutil.copyfile(REAL_ROUTER, target)
    return target


# ── Strict unified-diff applier ───────────────────────────────────────────────
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _split_lines(text: str) -> list[str]:
    """Split into lines without terminators (CRLF/LF), no trailing empty item."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _apply_unified_diff(original_text: str, patch_text: str) -> str:
    """Apply ``patch_text`` to ``original_text``; AssertionError on any mismatch.

    Context and '-' lines must equal the original lines (compared after
    stripping CR/LF), hunk line counts must match their headers, hunks must be
    ordered and non-overlapping. Returns the patched text with LF endings.
    """
    orig = _split_lines(original_text)
    plines = patch_text.replace("\r\n", "\n").split("\n")
    if plines and plines[-1] == "":
        plines.pop()

    # File headers precede the first hunk.
    i = 0
    minus_hdr = plus_hdr = None
    while i < len(plines) and not plines[i].startswith("@@"):
        if plines[i].startswith("--- "):
            minus_hdr = plines[i]
        elif plines[i].startswith("+++ "):
            plus_hdr = plines[i]
        i += 1
    assert minus_hdr is not None and plus_hdr is not None, "patch lacks '--- '/'+++ ' headers"
    for hdr in (minus_hdr, plus_hdr):
        assert hdr.rstrip().endswith("llm_router.py"), f"header must end with llm_router.py: {hdr!r}"
    assert i < len(plines), "patch contains no '@@' hunk"

    out: list[str] = []
    cursor = 0  # 0-based index of the next unconsumed original line
    hunks = 0
    while i < len(plines):
        m = _HUNK_RE.match(plines[i])
        assert m, f"patch line {i + 1} is not a hunk header: {plines[i]!r}"
        old_start = int(m.group(1))
        old_len = int(m.group(2)) if m.group(2) is not None else 1
        new_len = int(m.group(4)) if m.group(4) is not None else 1
        i += 1
        start_idx = old_start - 1 if old_len > 0 else old_start
        assert start_idx >= cursor, f"hunk at original line {old_start} overlaps/out of order"
        out.extend(orig[cursor:start_idx])
        cursor = start_idx
        seen_old = seen_new = 0
        while i < len(plines) and not plines[i].startswith("@@"):
            pl = plines[i]
            i += 1
            if pl.startswith(NO_NEWLINE_MARKER):
                continue  # end-of-file newline marker; endings are normalised anyway
            tag, body = (pl[:1], pl[1:]) if pl else (" ", "")
            body = body.rstrip("\r\n")
            if tag in (" ", "-"):
                assert cursor < len(orig), f"hunk runs past end of original at {pl!r}"
                assert orig[cursor].rstrip("\r\n") == body, (
                    f"patch mismatch at original line {cursor + 1}: "
                    f"expected {orig[cursor]!r}, patch has {body!r}"
                )
                cursor += 1
                seen_old += 1
                if tag == " ":
                    out.append(body)
                    seen_new += 1
            elif tag == "+":
                out.append(body)
                seen_new += 1
            else:
                raise AssertionError(f"invalid patch line: {pl!r}")
        assert (seen_old, seen_new) == (old_len, new_len), (
            f"hunk counts mismatch: header -{old_len}/+{new_len}, body -{seen_old}/+{seen_new}"
        )
        hunks += 1
    assert hunks >= 1, "patch contains no hunks"
    out.extend(orig[cursor:])
    return "\n".join(out) + "\n"


# ── DB helpers ────────────────────────────────────────────────────────────────
def _make_db(tmp_path: Path, name: str = "kanban_v3_mlm.db") -> Path:
    assert SCHEMA_PATH.is_file(), f"schema_v3.sql missing at {SCHEMA_PATH}"
    sql_text = SCHEMA_PATH.read_text(encoding="utf-8")
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.executescript(sql_text)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _insert_task(db_path: Path, task_id: str, *, workflow_type: str = "srs",
                 status: str = "todo", priority: int = 3) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.execute(
            "INSERT INTO kanban_tasks_v3 (task_id, workflow_type, status, priority, payload_uri) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, workflow_type, status, priority, f"payload://{task_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def _query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _conn_of(mgr) -> sqlite3.Connection:
    conn = getattr(mgr, "conn", None)
    if conn is None and hasattr(mgr, "get_connection"):
        conn = mgr.get_connection()
    assert isinstance(conn, sqlite3.Connection), (
        "manager must expose a sqlite3.Connection via .conn or .get_connection()"
    )
    return conn


def _assert_pragmas(conn: sqlite3.Connection) -> None:
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal", f"journal_mode={mode!r}"
    busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert int(busy) == 5000, f"busy_timeout={busy!r}"
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert int(fk) == 1, f"foreign_keys={fk!r}"


def _new_manager(managers: list, mod, **kwargs):
    mgr = mod.V3ModelLifecycleManager(**kwargs)
    managers.append(mgr)
    return mgr


# ── Stand-in CLI helpers ──────────────────────────────────────────────────────
def _echo_argv(text: str, exit_code: int = 0) -> list[str]:
    """argv of a stand-in CLI that prints ``text`` and exits with ``exit_code``."""
    code = "import sys; sys.stdout.write(" + repr(text) + ")"
    if exit_code:
        code += f"; sys.exit({exit_code})"
    return [sys.executable, "-c", code]


def _write_docker_standin(path: Path, marker: str, summary_lines: list[str], exit_code: int) -> Path:
    """Stand-in 'docker' CLI: echoes its argv as JSON, a stderr marker, a summary."""
    lines = [
        "import json, sys",
        'print("ARGV=" + json.dumps(sys.argv[1:]))',
        f"sys.stderr.write({marker + chr(10)!r})",
    ]
    lines += [f"print({s!r})" for s in summary_lines]
    lines.append("sys.stdout.flush()")
    lines.append(f"sys.exit({exit_code})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _echoed_argv(stdout: str) -> list[str]:
    found = [ln for ln in stdout.splitlines() if ln.startswith("ARGV=")]
    assert len(found) == 1, f"stand-in ARGV line missing in stdout: {stdout!r}"
    return json.loads(found[0][len("ARGV="):])


def _assert_hardened(args: list[str], prefix: list[str], image: str) -> int:
    """Check docker hardening flags precede the image; return the image index."""
    assert isinstance(args, list) and all(isinstance(a, str) for a in args), args
    assert args[: len(prefix)] == list(prefix), f"argv prefix {args[:len(prefix)]} != {prefix}"
    assert args[len(prefix)] == "run", f"token after docker_cli must be 'run': {args}"
    assert image in args[len(prefix):], f"image {image!r} missing from {args}"
    image_idx = args.index(image, len(prefix))
    for flag in ("--rm", "--read-only"):
        assert flag in args, f"{flag} missing from {args}"
        assert args.index(flag) < image_idx, f"{flag} must precede the image"
    for flag, value in (("--user", "1000:1000"), ("--network", "none")):
        assert flag in args, f"{flag} missing from {args}"
        idx = args.index(flag)
        assert args[idx + 1] == value, f"{flag} value {args[idx + 1]!r} != {value!r}"
        assert idx + 1 < image_idx, f"{flag} must precede the image"
    assert "-v" in args and args.index("-v") + 1 < image_idx, f"-v must precede the image: {args}"
    return image_idx


def _volume_value(args: list[str]) -> str:
    assert "-v" in args, f"-v missing from {args}"
    return args[args.index("-v") + 1]


# ── T1 ────────────────────────────────────────────────────────────────────────
def test_model_lifecycle_manager_db_connection_and_pragmas(tmp_path, managers):
    mod = _load_module()
    sfx = uuid.uuid4().hex[:10]

    # (a) Pre-built database.
    db_path = _make_db(tmp_path)
    mgr = _new_manager(managers, mod, db_path=str(db_path))
    conn = _conn_of(mgr)
    _assert_pragmas(conn)

    db_list = conn.execute("PRAGMA database_list").fetchall()
    main_file = [r[2] for r in db_list if r[1] == "main"][0]
    assert Path(main_file).resolve() == db_path.resolve(), "manager opened the wrong database"

    # A row committed by an independent connection is visible to the manager.
    probe = f"MLM_T1_{sfx}"
    _insert_task(db_path, probe, status="done", priority=4)
    row = conn.execute(
        "SELECT task_id, status, priority FROM kanban_tasks_v3 WHERE task_id = ?", (probe,)
    ).fetchone()
    assert row is not None and tuple(row)[:3] == (probe, "done", 4)
    assert conn.execute("SELECT COUNT(*) FROM kanban_telemetry_v3").fetchone()[0] == 0

    # Foreign keys are enforced on the manager connection.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO kanban_telemetry_v3 (task_id, event_type, payload) VALUES (?, ?, ?)",
            (f"NO_SUCH_TASK_{sfx}", "PROBE", "{}"),
        )
    conn.rollback()

    # (b) Brand-new database file initialised from schema_path.
    fresh = tmp_path / f"fresh_{sfx}.db"
    assert not fresh.exists()
    mgr2 = _new_manager(managers, mod, db_path=str(fresh), schema_path=str(SCHEMA_PATH))
    conn2 = _conn_of(mgr2)
    _assert_pragmas(conn2)
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"kanban_tasks_v3", "kanban_telemetry_v3"} <= tables, tables
    assert fresh.is_file()


# ── T2 ────────────────────────────────────────────────────────────────────────
def test_model_lifecycle_manager_model_discovery(tmp_path, managers):
    mod = _load_module()
    sfx = uuid.uuid4().hex[:10]
    router = _copy_router(tmp_path)
    known = _known_models(_extract_registry(_read_text_exact(router)))

    claude_new = f"claude-opus-7-{sfx}"
    gemini_new = f"gemini-4-2-ultra-{sfx}"
    ollama_new = f"llamacand{sfx}:8b"
    for registered in ("claude-sonnet-5", "claude-opus-5-5", "gemini-3.1-pro-preview", "gemini-3.8-flash"):
        assert registered in known, f"precondition: {registered} should be in the router registry"
    assert not {claude_new, gemini_new, ollama_new} & known

    claude_text = (
        "2.1.0 (Claude Code)\n"
        "Available models:\n"
        "  claude-sonnet-5\n"
        "  claude-opus-5-5\n"
        f"  {claude_new}\n"
        f"  default -> {claude_new}\n"
    )
    gemini_text = (
        "Models:\n"
        "  gemini-3.1-pro-preview   (stable)\n"
        "  gemini-3.8-flash         (stable)\n"
        f"  {gemini_new}   (preview)\n"
        f"  {gemini_new}   (alias)\n"
    )
    ollama_text = (
        "NAME  ID  SIZE  MODIFIED\n"
        "qwen3.5:9b            5a9c1e2f     6.6 GB    3 days ago\n"
        "\n"
        f"{ollama_new}   0b1d7c3e     4.9 GB    5 minutes ago\n"
    )
    cli = {CLAUDE: _echo_argv(claude_text), GEMINI: _echo_argv(gemini_text), OLLAMA: _echo_argv(ollama_text)}

    db_path = _make_db(tmp_path)
    mgr = _new_manager(managers, mod, db_path=str(db_path), router_path=str(router))
    result = mgr.discover_models(cli)
    assert isinstance(result, dict), f"discover_models must return a dict, got {type(result)}"
    assert result.get(CLAUDE) == [claude_new], result
    assert result.get(GEMINI) == [gemini_new], result
    ollama_ids = result.get(OLLAMA, [])
    assert ollama_new in ollama_ids, result
    assert "NAME" not in ollama_ids and "" not in ollama_ids, result
    returned = [m for ids in result.values() for m in ids]
    assert not set(returned) & known, f"registered ids leaked into discovery: {set(returned) & known}"

    # Failing CLIs: a missing executable and a non-zero exit (whose stdout would
    # otherwise contain a new id) yield nothing; the healthy provider still works.
    broken = {
        CLAUDE: [str(tmp_path / f"no_such_cli_{sfx}.exe"), "models"],
        GEMINI: _echo_argv(gemini_text, exit_code=3),
        OLLAMA: _echo_argv(ollama_text),
    }
    broken_result = mgr.discover_models(broken)
    assert isinstance(broken_result, dict)
    assert broken_result.get(CLAUDE, []) == [], broken_result
    assert broken_result.get(GEMINI, []) == [], broken_result
    assert ollama_new in broken_result.get(OLLAMA, []), broken_result

    # Anti-hardcode: registering the gemini id in a second router copy removes it.
    source = _read_text_exact(router)
    node = _registry_node(source)
    lines = source.splitlines(keepends=True)
    head = "".join(lines[: node.lineno - 1])
    body = "".join(lines[node.lineno - 1: node.end_lineno])
    tail = "".join(lines[node.end_lineno:])
    needle = '"model": "gemini-3.8-flash"'
    assert needle in body, "precondition: flash primary entry not found in MODEL_REGISTRY"
    router2 = _copy_router(tmp_path)
    router2.write_bytes((head + body.replace(needle, f'"model": "{gemini_new}"', 1) + tail).encode("utf-8"))
    assert gemini_new in _known_models(_extract_registry(_read_text_exact(router2)))

    mgr2 = _new_manager(managers, mod, db_path=str(db_path), router_path=str(router2))
    result2 = mgr2.discover_models(cli)
    assert gemini_new not in result2.get(GEMINI, []), result2
    assert result2.get(CLAUDE) == [claude_new], result2


# ── T3 ────────────────────────────────────────────────────────────────────────
def test_model_lifecycle_manager_docker_benchmark_execution(tmp_path, managers):
    mod = _load_module()
    sfx = uuid.uuid4().hex[:10]
    marker = f"STANDIN_STDERR_{sfx}"
    pass_script = _write_docker_standin(
        tmp_path / "docker_pass.py", marker, ["===== 7 passed in 0.42s ====="], 0
    )
    code3_script = _write_docker_standin(tmp_path / "docker_code3.py", marker, [], 3)

    # (1) Pure argv construction with the default runner.
    image = "cochem-v3:latest"
    test_command = ["pytest", "-q", "tests/benchmark"]
    default_runner = mod.DockerSandboxRunner()
    args = default_runner.build_docker_args(image, test_command)
    assert args[0] == "docker", args
    image_idx = _assert_hardened(args, ["docker"], image)
    assert _volume_value(args) == "cochem-data:/workspace/data"
    assert args[image_idx:] == [image, *test_command], args
    custom_mount = f"bench-{sfx}:/workspace/data"
    custom = default_runner.build_docker_args(image, test_command, volume_mount=custom_mount)
    _assert_hardened(custom, ["docker"], image)
    assert _volume_value(custom) == custom_mount
    assert custom[-len(test_command) - 1:] == [image, *test_command]

    # (2) Real execution through a stand-in docker CLI.
    prefix = [sys.executable, str(pass_script)]
    bench_image = f"cochem-v3-bench-{sfx}:latest"
    pass_runner = mod.DockerSandboxRunner(docker_cli=prefix, image=bench_image)
    run_args = pass_runner.build_docker_args(bench_image, test_command)
    _assert_hardened(run_args, prefix, bench_image)
    assert run_args[-len(test_command) - 1:] == [bench_image, *test_command]
    result = pass_runner.run(run_args)
    assert isinstance(result, mod.DockerRunResult)
    assert result.exit_code == 0, result
    assert _echoed_argv(result.stdout) == run_args[len(prefix):], "stand-in saw a different argv"
    assert "7 passed" in result.stdout
    assert marker in result.stderr, result.stderr
    assert list(result.cmd) == run_args

    # (3) Non-zero exit is reported raw, not raised.
    code3_prefix = [sys.executable, str(code3_script)]
    code3_runner = mod.DockerSandboxRunner(docker_cli=code3_prefix, image=bench_image)
    code3_args = code3_runner.build_docker_args(bench_image, test_command)
    code3_result = code3_runner.run(code3_args)
    assert code3_result.exit_code == 3, code3_result
    assert _echoed_argv(code3_result.stdout) == code3_args[len(code3_prefix):]
    assert marker in code3_result.stderr

    # (4) execute_benchmark drives the runner end to end.
    db_path = _make_db(tmp_path)
    router = _copy_router(tmp_path)
    mgr = _new_manager(managers, mod, db_path=str(db_path), docker_runner=pass_runner,
                       router_path=str(router))
    candidate = f"claude-opus-7-{sfx}"
    for suite in ("tests/benchmark", f"tests/bench_{sfx}"):
        bench = mgr.execute_benchmark(candidate, CLAUDE, test_suite=suite)
        assert isinstance(bench, mod.DockerRunResult)
        assert bench.exit_code == 0, bench
        cmd = list(bench.cmd)
        img_idx = _assert_hardened(cmd, prefix, bench_image)
        assert img_idx > cmd.index("--network")
        container_cmd = cmd[img_idx + 1:]
        assert "pytest" in container_cmd, f"container command must run pytest: {container_cmd}"
        assert suite in container_cmd, f"test suite {suite!r} missing from {container_cmd}"
        assert any(candidate in tok for tok in cmd), f"candidate missing from {cmd}"
        assert _echoed_argv(bench.stdout) == cmd[len(prefix):], "stand-in saw a different argv"
        assert marker in bench.stderr


# ── T4 ────────────────────────────────────────────────────────────────────────
def test_model_lifecycle_manager_benchmark_evaluation_and_telemetry(tmp_path, managers):
    mod = _load_module()
    sfx = uuid.uuid4().hex[:10]
    db_path = _make_db(tmp_path)
    task_id = f"MLM_BENCH_{sfx}"
    _insert_task(db_path, task_id, workflow_type="audit", status="in_progress", priority=1)

    marker = f"STANDIN_STDERR_{sfx}"
    pass_script = _write_docker_standin(
        tmp_path / "docker_pass.py", marker, ["===== 7 passed in 0.42s ====="], 0
    )
    fail_script = _write_docker_standin(
        tmp_path / "docker_fail.py", marker,
        ["FAILED tests/benchmark/test_x.py::test_y - AssertionError",
         "===== 1 failed, 6 passed in 0.50s ====="],
        1,
    )
    image = f"cochem-v3-bench-{sfx}:latest"
    pass_runner = mod.DockerSandboxRunner(docker_cli=[sys.executable, str(pass_script)], image=image)
    fail_runner = mod.DockerSandboxRunner(docker_cli=[sys.executable, str(fail_script)], image=image)
    router = _copy_router(tmp_path)

    mgr_pass = _new_manager(managers, mod, db_path=str(db_path), docker_runner=pass_runner,
                            router_path=str(router))
    mgr_fail = _new_manager(managers, mod, db_path=str(db_path), docker_runner=fail_runner,
                            router_path=str(router))

    calls: list[tuple[str, int, bool]] = []  # (candidate, exit_code, approved)

    def evaluate(mgr, candidate, run_result, expected: bool) -> None:
        verdict = mgr.evaluate_benchmark(candidate, run_result, task_id)
        assert verdict is expected, (
            f"evaluate_benchmark({candidate!r}, exit={run_result.exit_code}, "
            f"stdout={run_result.stdout!r}) returned {verdict!r}, expected {expected!r}"
        )
        calls.append((candidate, run_result.exit_code, verdict))

    # Real runs through the stand-in docker CLI.
    cand_pass = f"claude-opus-7-{sfx}"
    real_pass = mgr_pass.execute_benchmark(cand_pass, CLAUDE, test_suite="tests/benchmark")
    assert real_pass.exit_code == 0
    evaluate(mgr_pass, cand_pass, real_pass, True)

    cand_fail = f"gemini-4-2-pro-{sfx}"
    real_fail = mgr_fail.execute_benchmark(cand_fail, GEMINI, test_suite="tests/benchmark")
    assert real_fail.exit_code == 1
    evaluate(mgr_fail, cand_fail, real_fail, False)

    # Constructed results: exit code alone is not enough, nor is "passed" alone.
    cmd = ["docker", "run", "--rm", image, "pytest", "tests/benchmark"]
    constructed = [
        (0, "===== 1 failed, 6 passed in 0.50s =====", False),
        (0, "===== 2 passed, 1 error in 0.3s =====", False),
        (0, "===== 3 passed in 0.10s =====", True),
        (2, "===== 3 passed in 0.10s =====", False),
    ]
    for n, (code, stdout, expected) in enumerate(constructed):
        rr = mod.DockerRunResult(exit_code=code, stdout=stdout, stderr="", cmd=list(cmd))
        evaluate(mgr_pass, f"claude-cand{n}-{sfx}", rr, expected)

    # Exactly one telemetry row per evaluation, in call order, with JSON payloads.
    rows = _query(
        db_path,
        "SELECT event_type, payload FROM kanban_telemetry_v3 WHERE task_id = ? ORDER BY telemetry_id",
        (task_id,),
    )
    assert len(rows) == len(calls), f"expected {len(calls)} telemetry rows, got {len(rows)}"
    for row, (candidate, exit_code, approved) in zip(rows, calls):
        assert row["event_type"] == "MODEL_BENCHMARK_EVALUATED", row["event_type"]
        payload = json.loads(row["payload"])
        assert isinstance(payload, dict), payload
        assert payload.get("candidate") == candidate, payload
        assert int(payload.get("exit_code")) == exit_code, payload
        assert payload.get("approved") is approved, payload


# ── T5 ────────────────────────────────────────────────────────────────────────
def _assert_chain_invariants(entry: dict, provider: str, candidate: str, cost: int,
                             tiers: dict) -> None:
    """R1, R2, R4, R5 on the proposed target entry."""
    fallbacks = [tuple(fb) for fb in entry["fallbacks"]]
    chain = [(entry["provider"], entry["model"])] + fallbacks
    for link in chain:
        assert len(link) == 2 and all(isinstance(x, str) for x in link), f"bad chain link {link!r}"

    # R4: exactly one halt sentinel, and it is last.
    halt_idx = [i for i, (p, _m) in enumerate(chain) if p == HALT]
    assert halt_idx == [len(chain) - 1], f"R4 violated: halt positions {halt_idx} in {chain}"

    # R5: no duplicate (provider, model).
    assert len(set(chain)) == len(chain), f"R5 violated: duplicates in {chain}"

    cloud_fb = [(p, m) for p, m in fallbacks if p != HALT]
    assert cloud_fb, f"target chain needs at least one cloud fallback: {chain}"

    # R1: first cloud fallback on the other cloud provider.
    assert cloud_fb[0][0] != provider and cloud_fb[0][0] in CLOUD_PROVIDERS, (
        f"R1 violated: first fallback {cloud_fb[0]} vs primary provider {provider}"
    )

    # R2: cost-non-increasing vs primary (with V2 floor exception).
    cost_map = {m: t["cost_in"] for m, t in tiers.items()}
    cost_map[candidate] = cost
    tier_provider = {m: t["provider"] for m, t in tiers.items()}
    tier_provider[candidate] = provider

    def min_cost(prov: str) -> int:
        return min(c for m, c in cost_map.items() if tier_provider[m] == prov)

    prim_is_floor = cost == min_cost(provider)
    for p, m in cloud_fb:
        assert p in CLOUD_PROVIDERS, f"unknown fallback provider {p!r}"
        assert m in cost_map, f"R2 violated: fallback model {m!r} has no cost tier"
        assert tier_provider[m] == p, f"R2 violated: {m!r} listed under {p!r}, tier says {tier_provider[m]!r}"
        mc = cost_map[m]
        floor_ok = prim_is_floor and p != provider and mc == min_cost(p)
        assert mc <= cost or floor_ok, (
            f"R2 violated: fallback {m!r} (cost {mc}) exceeds primary {candidate!r} (cost {cost})"
        )


def test_model_lifecycle_manager_patch_generation_and_registry_invariants(tmp_path, managers):
    mod = _load_module()
    tiers, pairs = _load_v2_tiers()
    sfx = uuid.uuid4().hex[:10]
    router = _copy_router(tmp_path)
    before = _sha256(router)
    original_text = _read_text_exact(router)
    original_reg = _extract_registry(original_text)
    reg_node = _registry_node(original_text)
    orig_lines = _split_lines(original_text)
    orig_head = orig_lines[: reg_node.lineno - 1]
    orig_tail = orig_lines[reg_node.end_lineno:]

    db_path = _make_db(tmp_path)
    mgr = _new_manager(managers, mod, db_path=str(db_path), router_path=str(router),
                       patch_dir=str(tmp_path / "patches"))

    scenarios = [
        (CLAUDE, f"claude-opus-7-{sfx}", "cochem-coder", 20),
        (GEMINI, f"gemini-4-2-pro-{sfx}", "cochem-audit", 6),
    ]
    for provider, candidate, role, cost in scenarios:
        assert role in original_reg, f"precondition: {role} missing from router registry"
        patch = mgr.generate_patch(candidate, provider, role, estimated_cost=cost)
        assert isinstance(patch, str) and patch.strip(), "generate_patch must return diff text"
        patched = _apply_unified_diff(original_text, patch)
        ast.parse(patched)  # the proposed router must still be valid Python

        # Nothing outside the MODEL_REGISTRY literal changes.
        new_node = _registry_node(patched)
        new_lines = _split_lines(patched)
        assert new_lines[: new_node.lineno - 1] == orig_head, "patch touched code before MODEL_REGISTRY"
        assert new_lines[new_node.end_lineno:] == orig_tail, "patch touched code after MODEL_REGISTRY"

        proposed = ast.literal_eval(new_node.value)
        assert set(proposed) == set(original_reg), "registry key set changed"
        for name, entry in original_reg.items():
            if name != role:
                assert proposed[name] == entry, f"patch modified unrelated entry {name!r}"
        target = proposed[role]
        assert target["provider"] == provider and target["model"] == candidate, target
        _assert_chain_invariants(target, provider, candidate, cost, tiers)

        # R6: asymmetry pairs keep primaries on different providers.
        for producer, verifier in pairs:
            if producer in proposed and verifier in proposed:
                assert proposed[producer]["provider"] != proposed[verifier]["provider"], (
                    f"R6 violated: {producer} and {verifier} share provider"
                )
        assert _sha256(router) == before, "generate_patch must not write the router"

    # Negatives: R6 violations and unsupported providers are rejected.
    with pytest.raises(ValueError):
        mgr.generate_patch(f"gemini-4-2-pro-{sfx}", GEMINI, "cochem-coder", estimated_cost=6)
    with pytest.raises(ValueError):
        mgr.generate_patch(f"claude-opus-7-{sfx}", CLAUDE, "cochem-audit", estimated_cost=6)
    with pytest.raises(ValueError):
        mgr.generate_patch(f"bogus-model-{sfx}", "bogus-provider", "cochem-coder", estimated_cost=6)
    assert _sha256(router) == before, "router modified by generate_patch"


# ── T6 ────────────────────────────────────────────────────────────────────────
def _uri_to_path(uri: str) -> Path:
    """Resolve a plain local path or a file URI (urllib.parse only)."""
    if uri.lower().startswith("file:"):
        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        netloc = parsed.netloc
        if netloc and netloc.lower() != "localhost":
            # A drive letter may land in netloc for URIs written without the empty host.
            assert re.match(r"^[A-Za-z]:$", netloc), f"payload_uri must be a local file: {uri!r}"
            path = netloc + path
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]  # strip the slash before the drive letter
        return Path(path)
    return Path(uri)


def _audit_count(db_path: Path) -> int:
    return _query(db_path, "SELECT COUNT(*) FROM kanban_tasks_v3 WHERE workflow_type = 'audit'")[0][0]


def _assert_saved_patch(db_path: Path, tid: str, saved: Path, patch: str) -> None:
    norm = patch.replace("\r\n", "\n")
    assert saved.is_file(), f"patch not saved at {saved}"
    assert saved.read_bytes().decode("utf-8").replace("\r\n", "\n") == norm
    rows = _query(db_path, "SELECT * FROM kanban_tasks_v3 WHERE task_id = ?", (tid,))
    assert len(rows) == 1, f"task {tid!r} not found"
    row = rows[0]
    assert row["workflow_type"] == "audit" and row["priority"] == 1 and row["status"] == "todo", dict(row)
    target = _uri_to_path(row["payload_uri"])
    assert target.resolve() == saved.resolve(), f"payload_uri {row['payload_uri']!r} != {saved}"
    assert target.read_bytes().decode("utf-8").replace("\r\n", "\n") == norm


def test_model_lifecycle_manager_asymmetric_audit_submission(tmp_path, managers):
    mod = _load_module()
    sfx = uuid.uuid4().hex[:10]
    router = _copy_router(tmp_path)
    router_before = _sha256(router)
    real_before = _sha256(REAL_ROUTER)
    patch_dir = tmp_path / "patches"
    db_path = _make_db(tmp_path)
    mgr = _new_manager(managers, mod, db_path=str(db_path), router_path=str(router),
                       patch_dir=str(patch_dir))

    patch = mgr.generate_patch(f"claude-opus-7-{sfx}", CLAUDE, "cochem-coder", estimated_cost=20)
    filename = f"model_upgrade_{sfx}.patch"
    tid = mgr.submit_patch_task(patch, filename)
    assert isinstance(tid, str) and tid, f"submit_patch_task must return a task id, got {tid!r}"
    _assert_saved_patch(db_path, tid, patch_dir / filename, patch)

    # Idempotent resubmission.
    assert mgr.submit_patch_task(patch, filename) == tid
    assert _audit_count(db_path) == 1

    # Different content with an explicit task id.
    patch2 = mgr.generate_patch(f"gemini-4-2-pro-{sfx}", GEMINI, "cochem-audit", estimated_cost=6)
    assert patch2.replace("\r\n", "\n") != patch.replace("\r\n", "\n")
    explicit = f"MLM_AUDIT_{sfx}"
    filename2 = f"model_upgrade_audit_{sfx}.patch"
    assert mgr.submit_patch_task(patch2, filename2, task_id=explicit) == explicit
    assert mgr.submit_patch_task(patch2, filename2, task_id=explicit) == explicit
    assert _audit_count(db_path) == 2
    _assert_saved_patch(db_path, explicit, patch_dir / filename2, patch2)

    # The router (both the working copy and the real one) is never modified.
    assert _sha256(router) == router_before, "router copy modified"
    assert _sha256(REAL_ROUTER) == real_before, "real llm_router.py modified"


# ── T7 ────────────────────────────────────────────────────────────────────────
def _is_placeholder_body(body: list[ast.stmt]) -> bool:
    """True when a function body is only a docstring and/or Ellipsis."""
    return all(
        isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
        and (isinstance(s.value.value, str) or s.value.value is Ellipsis)
        for s in body
    )


def test_model_lifecycle_manager_anti_spoofing_and_subprocess_flags():
    assert MODULE_PATH.is_file(), f"deliverable missing: {MODULE_PATH}"
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.strip(), f"{MODULE_PATH.name} is empty"
    tree = ast.parse(source, filename=str(MODULE_PATH))

    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for name in ("V3ModelLifecycleManager", "DockerSandboxRunner", "DockerRunResult"):
        assert name in classes, f"class {name} not defined in {MODULE_PATH.name}"

    subprocess_aliases: set[str] = set()
    for node in ast.walk(tree):
        # Stubs and placeholders.
        if isinstance(node, ast.Name) and node.id == "NotImplementedError":
            pytest.fail(f"NotImplementedError at line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr == "NotImplementedError":
            pytest.fail(f"NotImplementedError at line {node.lineno}")
        if isinstance(node, ast.Pass):
            pytest.fail(f"'pass' statement at line {node.lineno}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not _is_placeholder_body(node.body), f"stub function {node.name!r} at line {node.lineno}"
            assert "monkeypatch" not in node.name.lower(), f"function {node.name!r}"
        # Imports.
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORTS, f"forbidden import {alias.name}"
                if alias.name == "subprocess":
                    subprocess_aliases.add(alias.asname or "subprocess")
        if isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            assert mod_name not in FORBIDDEN_IMPORTS, f"forbidden import from {mod_name}"
            assert mod_name != "subprocess", f"'from subprocess import ...' at line {node.lineno}"
            if mod_name == "unittest":
                assert all(a.name != "mock" for a in node.names), "unittest.mock import"
            if mod_name == "urllib":
                assert all(a.name != "request" for a in node.names), "urllib.request import"
            if mod_name == "http":
                assert all(a.name != "client" for a in node.names), "http.client import"
        # Mock identifiers.
        ident = None
        if isinstance(node, ast.Name):
            ident = node.id
        elif isinstance(node, ast.Attribute):
            ident = node.attr
        elif isinstance(node, ast.arg):
            ident = node.arg
        if ident is not None:
            assert ident not in {"MagicMock", "Mock"}, f"mock identifier {ident!r}"
            assert "monkeypatch" not in ident.lower(), f"monkeypatch identifier {ident!r}"
        # Shell escapes.
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "os" and func.attr in {"system", "popen"}):
                pytest.fail(f"os.{func.attr} at line {node.lineno}")
            for kw in node.keywords:
                if kw.arg == "shell":
                    assert not (isinstance(kw.value, ast.Constant) and kw.value.value is True), (
                        f"shell=True at line {node.lineno}"
                    )

    assert subprocess_aliases, "module must import subprocess to run docker/CLI commands"

    # Every subprocess call is windowless and utf-8.
    subprocess_calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in SUBPROCESS_CALLS and isinstance(node.func.value, ast.Name)
                and node.func.value.id in subprocess_aliases):
            continue
        subprocess_calls += 1
        alias = node.func.value.id
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        assert "creationflags" in kwargs, f"subprocess call at line {node.lineno} lacks creationflags"
        flag_src = ast.unparse(kwargs["creationflags"])
        assert flag_src == f"{alias}.CREATE_NO_WINDOW", (
            f"line {node.lineno}: creationflags={flag_src!r}, expected {alias}.CREATE_NO_WINDOW"
        )
        enc = kwargs.get("encoding")
        assert isinstance(enc, ast.Constant) and enc.value == "utf-8", (
            f"line {node.lineno}: encoding must be the constant 'utf-8'"
        )
    assert subprocess_calls >= 1, "no subprocess.<run|Popen|call|check_call|check_output> call found"
