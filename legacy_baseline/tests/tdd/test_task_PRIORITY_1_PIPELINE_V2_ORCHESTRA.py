"""TDD contract for CoChem Pipeline v2 Phase 1a infrastructure deliverables.

Targets (resolved via get_v2_path):
    __agentic/v2/schema_v2.sql
    __agentic/v2/init_db.py
    __agentic/v2/docker-compose.yml
    __agentic/v2/Dockerfile
    __agentic/v2/MODEL_REGISTRY_V2.py

Contract for init_db.py exercised here:
    * CLI:  python init_db.py --db-path <path> --schema-path <schema_v2.sql>
            (creates/initialises the SQLite file)
    * API:  init_db.open_connection(db_path) -> sqlite3.Connection with
            journal_mode=WAL and busy_timeout=5000.

All tests are offline: no Docker daemon, no network, no third-party parsers.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
V2_DIR = WORKSPACE_ROOT / "__agentic" / "v2"
ALT_V2_DIR = Path(r"D:\__CoChem\__agentic\v2")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

REQUIRED_TABLES = (
    "kanban_tasks_v2",
    "kanban_telemetry_v2",
    "audit_verdicts",
    "credit_ledger_v2",
    "quota_state",
    "watchdog_events_v2",
)

CLAIM_INDEX_NAME = "idx_kanban_tasks_v2_claim"
CLAIM_INDEX_COLUMNS = ["status", "priority", "lease_expires_at"]

SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}


def get_v2_path(filename: str) -> Path:
    p = V2_DIR / filename
    if not p.exists() and ALT_V2_DIR.exists():
        alt = ALT_V2_DIR / filename
        if alt.exists():
            return alt
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_schema_db(tmp_path: Path, name: str = "schema_v2_test.db") -> Path:
    schema_path = get_v2_path("schema_v2.sql")
    assert schema_path.exists(), f"schema_v2.sql not found at {schema_path}"
    with open(schema_path, "r", encoding="utf-8") as fh:
        script = fh.read()
    assert script.strip(), "schema_v2.sql is empty"

    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _table_columns(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"PRAGMA table_info('{table}')").fetchall()


def _dummy_value(declared_type: str):
    t = (declared_type or "").upper()
    if "INT" in t:
        return 1
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return 1.0
    return "x"


def _insert_orphan(conn: sqlite3.Connection, table: str, overrides: dict) -> None:
    """Insert a row supplying only overrides plus any NOT NULL columns without defaults."""
    values: dict = {}
    for cid, name, ctype, notnull, dflt, pk in _table_columns(conn, table):
        if name in overrides:
            values[name] = overrides[name]
            continue
        is_rowid_alias = pk == 1 and "INT" in (ctype or "").upper()
        if notnull and dflt is None and not is_rowid_alias:
            values[name] = _dummy_value(ctype)
    for key, val in overrides.items():
        values.setdefault(key, val)
    cols = ", ".join(f'"{c}"' for c in values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', tuple(values.values()))


def _fk_map(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, str]]:
    """Return {parent_table: {'from': col, 'on_delete': action}}."""
    result: dict[str, dict[str, str]] = {}
    for row in conn.execute(f"PRAGMA foreign_key_list('{table}')").fetchall():
        # (id, seq, table, from, to, on_update, on_delete, match)
        result[row[2]] = {"from": row[3], "to": row[4], "on_delete": row[6].upper()}
    return result


def _load_module(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    parent = str(path.parent)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(parent)
    return module


def _command_text(cmd) -> str:
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd or "")


def _normalise_mount(mount) -> str:
    if isinstance(mount, dict):
        assert mount.get("type", "volume") == "volume", f"non-volume mount: {mount}"
        return f"{mount.get('source')}:{mount.get('target')}"
    return str(mount)


def _subprocess_call_violations(tree: ast.AST) -> list[str]:
    """Find subprocess calls lacking creationflags or encoding='utf-8'."""
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_CALLS:
                    direct_names.add(alias.asname or alias.name)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_sub = (
            isinstance(func, ast.Attribute)
            and func.attr in SUBPROCESS_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ) or (isinstance(func, ast.Name) and func.id in direct_names)
        if not is_sub:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if "creationflags" not in kw:
            violations.append(f"line {node.lineno}: missing creationflags")
        enc = kw.get("encoding")
        if not (isinstance(enc, ast.Constant) and str(enc.value).lower().replace("_", "-") == "utf-8"):
            violations.append(f"line {node.lineno}: missing encoding='utf-8'")
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_schema_v2_table_structures(tmp_path):
    db_path = _build_schema_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        assert not missing, f"schema_v2.sql did not create tables: {missing}"

        task_cols = {row[1] for row in _table_columns(conn, "kanban_tasks_v2")}
        for col in ("lease_expires_at", "heartbeat_at", "owner"):
            assert col in task_cols, f"kanban_tasks_v2 missing column {col!r}; has {sorted(task_cols)}"

        for table in REQUIRED_TABLES:
            assert _table_columns(conn, table), f"{table} has no columns"
    finally:
        conn.close()


def test_schema_v2_fk_enforcement_and_index_order(tmp_path):
    db_path = _build_schema_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        telemetry = _fk_map(conn, "kanban_telemetry_v2")
        assert "kanban_tasks_v2" in telemetry, f"kanban_telemetry_v2 FKs: {telemetry}"
        assert telemetry["kanban_tasks_v2"]["on_delete"] == "CASCADE"

        audit = _fk_map(conn, "audit_verdicts")
        assert "kanban_tasks_v2" in audit, f"audit_verdicts FKs: {audit}"
        assert audit["kanban_tasks_v2"]["on_delete"] == "CASCADE"

        ledger = _fk_map(conn, "credit_ledger_v2")
        assert "kanban_tasks_v2" in ledger, f"credit_ledger_v2 FKs: {ledger}"
        assert ledger["kanban_tasks_v2"]["on_delete"] == "SET NULL"
        assert "quota_state" in ledger, f"credit_ledger_v2 FKs: {ledger}"
        assert ledger["quota_state"]["on_delete"] == "SET NULL"

        watchdog = _fk_map(conn, "watchdog_events_v2")
        assert "kanban_tasks_v2" in watchdog, f"watchdog_events_v2 FKs: {watchdog}"
        assert watchdog["kanban_tasks_v2"]["on_delete"] == "SET NULL"

        # Physical FK enforcement.
        conn.execute("PRAGMA foreign_keys=ON")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        for table, fks in (
            ("kanban_telemetry_v2", telemetry),
            ("audit_verdicts", audit),
            ("credit_ledger_v2", ledger),
            ("watchdog_events_v2", watchdog),
        ):
            fk_col = fks["kanban_tasks_v2"]["from"]
            raised = None
            try:
                _insert_orphan(conn, table, {fk_col: 999999})
            except sqlite3.IntegrityError as exc:
                raised = exc
            finally:
                conn.rollback()
            assert raised is not None, f"{table}: orphan {fk_col}=999999 was accepted"
            assert "FOREIGN KEY" in str(raised).upper(), (
                f"{table}: expected FOREIGN KEY failure, got {raised!r}"
            )

        # Composite claim index column order.
        index_columns: dict[str, list[str]] = {}
        for row in conn.execute("PRAGMA index_list('kanban_tasks_v2')").fetchall():
            idx_name = row[1]
            info = conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
            # (seqno, cid, name)
            index_columns[idx_name] = [r[2] for r in sorted(info, key=lambda r: r[0])]

        if CLAIM_INDEX_NAME in index_columns:
            assert index_columns[CLAIM_INDEX_NAME] == CLAIM_INDEX_COLUMNS, (
                f"{CLAIM_INDEX_NAME} columns {index_columns[CLAIM_INDEX_NAME]}"
            )
        else:
            assert CLAIM_INDEX_COLUMNS in index_columns.values(), (
                f"no composite index {CLAIM_INDEX_COLUMNS} on kanban_tasks_v2: {index_columns}"
            )
    finally:
        conn.close()


def test_init_db_subprocess_and_pragmas(tmp_path):
    init_path = get_v2_path("init_db.py")
    assert init_path.exists(), f"init_db.py not found at {init_path}"

    db_path = tmp_path / "cochem_kanban_v2.db"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(init_path), "--db-path", str(db_path), "--schema-path", str(get_v2_path("schema_v2.sql"))],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        creationflags=CREATE_NO_WINDOW,
        encoding="utf-8",
    )
    assert proc.returncode == 0, (
        f"init_db.py exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert db_path.exists(), f"init_db.py did not create {db_path}"

    conn = sqlite3.connect(str(db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal", f"journal_mode is {mode!r}"
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        assert not missing, f"init_db.py did not create tables: {missing}"
    finally:
        conn.close()

    with open(db_path, "rb") as fh:
        header = fh.read(100)
    assert header[:16] == b"SQLite format 3\x00", "not a SQLite database file"
    assert header[18] == 2 and header[19] == 2, (
        f"WAL header bytes expected (2, 2), got ({header[18]}, {header[19]})"
    )

    init_db = _load_module(init_path, "cochem_init_db_v2_under_test")
    assert hasattr(init_db, "open_connection"), "init_db.open_connection missing"
    managed = init_db.open_connection(db_path)
    try:
        assert isinstance(managed, sqlite3.Connection)
        assert managed.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert str(managed.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    finally:
        managed.close()

    naive = sqlite3.connect(str(db_path), timeout=0)
    try:
        assert naive.execute("PRAGMA busy_timeout").fetchone()[0] == 0
    finally:
        naive.close()


def test_docker_compose_services_and_limits():
    compose_path = get_v2_path("docker-compose.yml")
    assert compose_path.exists(), f"docker-compose.yml not found at {compose_path}"
    with open(compose_path, "r", encoding="utf-8") as fh:
        compose = json.loads(fh.read())

    services = compose.get("services")
    assert isinstance(services, dict), "compose file has no services mapping"
    for name in ("kanban-worker", "watchdog", "mcp-server"):
        assert name in services, f"service {name!r} missing; have {sorted(services)}"

    def limits(svc_name: str) -> dict:
        return services[svc_name].get("deploy", {}).get("resources", {}).get("limits", {})

    worker = services["kanban-worker"]
    assert worker.get("deploy", {}).get("replicas") == 3
    assert "container_name" not in worker, "replicated worker must not pin container_name"
    assert _command_text(worker.get("command")).strip() == "sleep infinity"
    assert str(limits("kanban-worker").get("cpus")) == "1.0"
    assert str(limits("kanban-worker").get("memory")).lower() == "1g"

    watchdog = services["watchdog"]
    assert _command_text(watchdog.get("command")).strip() == "sleep infinity"
    assert str(limits("watchdog").get("cpus")) == "0.25"
    assert str(limits("watchdog").get("memory")).lower() == "256m"

    mcp = services["mcp-server"]
    assert _command_text(mcp.get("command")).strip().startswith("python -m http.server 8787")
    assert "8787:8787" in [str(p) for p in mcp.get("ports", [])]
    health = _command_text(mcp.get("healthcheck", {}).get("test"))
    assert "/healthz" in health, f"healthcheck does not probe /healthz: {health!r}"
    assert "python" in health or "wget" in health, f"healthcheck tool unexpected: {health!r}"
    assert str(limits("mcp-server").get("cpus")) == "0.5"
    assert str(limits("mcp-server").get("memory")).lower() == "512m"

    volumes = compose.get("volumes", {})
    assert "cochem-data" in volumes, f"named volume cochem-data missing: {volumes}"
    vol = volumes["cochem-data"] or {}
    opts = vol.get("driver_opts", {})
    assert "bind" in str(opts.get("o", "")), f"cochem-data is not a bind volume: {vol}"
    device = str(opts.get("device", "")).replace("\\", "/")
    assert "__agentic/v2/data" in device, f"cochem-data device not __agentic/v2/data/: {device!r}"

    for name, svc in services.items():
        mounts = [_normalise_mount(m) for m in svc.get("volumes", [])]
        assert mounts == ["cochem-data:/workspace/data"], (
            f"service {name!r} mounts {mounts}; only cochem-data:/workspace/data allowed"
        )


def test_dockerfile_base_user_and_rdkit():
    dockerfile_path = get_v2_path("Dockerfile")
    assert dockerfile_path.exists(), f"Dockerfile not found at {dockerfile_path}"
    with open(dockerfile_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    # Join continuation lines, drop comments for directive parsing.
    logical = re.sub(r"\\\s*\n", " ", text)
    directives = [
        ln.strip() for ln in logical.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]

    froms = [d for d in directives if d.upper().startswith("FROM ")]
    assert froms, "Dockerfile has no FROM"
    assert any(
        re.match(r"FROM\s+python:3\.12(-slim)?(\s|@|$)", f, re.IGNORECASE) for f in froms
    ), f"base image is not python:3.12[-slim]: {froms}"

    users = [d for d in directives if d.upper().startswith("USER ")]
    assert users, "Dockerfile has no USER directive"
    assert re.fullmatch(r"USER\s+1000(:1000)?", users[-1], re.IGNORECASE), (
        f"final USER must be 1000, got {users[-1]!r}"
    )

    runs = [d for d in directives if d.upper().startswith("RUN ")]
    assert any("rdkit" in r.lower() and "install" in r.lower() for r in runs), (
        "no RUN step installs RDKit"
    )

    assert any(re.fullmatch(r"WORKDIR\s+/workspace/?", d, re.IGNORECASE) for d in directives), (
        "WORKDIR /workspace missing"
    )
    assert re.search(r"read[-_ ]only|readonly", text, re.IGNORECASE) or any(
        "chmod" in r or "USER 1000" in text for r in runs
    ), "Dockerfile does not declare read-only rootfs posture"
    for r in runs:
        for m in re.finditer(r"\b(chown|chmod)\b([^&;|]*)", r):
            paths = [tok for tok in m.group(2).split() if tok.startswith("/")]
            for p in paths:
                assert p.startswith("/workspace") or p.startswith("/opt/cochem"), (
                    f"writable permission change outside allowed paths: {m.group(0).strip()!r}"
                )


def test_offline_and_no_mock_invariants():
    this_file = Path(__file__).resolve()
    with open(this_file, "r", encoding="utf-8") as fh:
        own_tree = ast.parse(fh.read(), filename=str(this_file))

    network_modules = {"socket", "requests", "urllib", "http", "httpx", "aiohttp", "docker"}
    forbidden_modules = {"unittest.mock", "mock"}
    for node in ast.walk(own_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert alias.name not in forbidden_modules, f"forbidden import {alias.name}"
                assert root not in network_modules, f"network/docker import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in forbidden_modules, f"forbidden import from {mod}"
            assert not (mod == "unittest" and any(a.name == "mock" for a in node.names))
            assert mod.split(".")[0] not in network_modules, f"network/docker import {mod}"
        elif isinstance(node, ast.Name):
            assert node.id not in {"NotImplementedError", "monkeypatch", "mock"}, (
                f"forbidden name {node.id} at line {node.lineno}"
            )
        elif isinstance(node, ast.arg):
            assert node.arg != "monkeypatch", "monkeypatch fixture used"
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "pytest":
                assert node.attr not in {"skip", "skipif", "xfail", "importorskip"}, (
                    f"pytest.{node.attr} used at line {node.lineno}"
                )
            assert node.attr not in {"mock", "monkeypatch"}, f"forbidden attribute {node.attr}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = [
                s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
            ]
            assert body and not all(isinstance(s, ast.Pass) for s in body), (
                f"{node.name} has an empty body"
            )
        elif isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
                    first = arg.elts[0]
                    assert not (
                        isinstance(first, ast.Constant) and first.value == "docker"
                    ), f"docker CLI invoked at line {node.lineno}"

    own_violations = _subprocess_call_violations(own_tree)
    assert not own_violations, f"test module subprocess violations: {own_violations}"

    init_path = get_v2_path("init_db.py")
    assert init_path.exists(), f"init_db.py not found at {init_path}"
    with open(init_path, "r", encoding="utf-8") as fh:
        init_tree = ast.parse(fh.read(), filename=str(init_path))
    init_violations = _subprocess_call_violations(init_tree)
    assert not init_violations, f"init_db.py subprocess violations: {init_violations}"

    registry_path = get_v2_path("MODEL_REGISTRY_V2.py")
    if not registry_path.exists():
        registry_path = ALT_V2_DIR / "MODEL_REGISTRY_V2.py"
    assert registry_path.exists(), f"MODEL_REGISTRY_V2.py not found at {registry_path}"

    registry = _load_module(registry_path, "cochem_model_registry_v2_under_test")
    chains: list = []
    # 1. From MODEL_REGISTRY
    for name, entry in getattr(registry, "MODEL_REGISTRY", {}).items():
        fb = entry.get("fallbacks")
        if fb:
            chains.append((f"MODEL_REGISTRY.{name}", fb))

    # 2. From module globals starting with _FB_
    for attr in dir(registry):
        if attr.startswith("_FB_"):
            val = getattr(registry, attr)
            if isinstance(val, (list, tuple)):
                chains.append((attr, val))

    assert chains, "MODEL_REGISTRY_V2.py defines no fallback chains"
    for where, chain in chains:
        for item in chain:
            rep = str(item).lower()
            assert "ollama" not in rep, f"fallback chain {where} points to ollama: {item}"
        terminal = chain[-1]
        terminal_prov = (
            terminal[0]
            if isinstance(terminal, (list, tuple))
            else (terminal.get("provider") if isinstance(terminal, dict) else str(terminal))
        )
        assert terminal_prov == "halt", f"fallback chain {where} does not terminate with 'halt': {terminal}"
