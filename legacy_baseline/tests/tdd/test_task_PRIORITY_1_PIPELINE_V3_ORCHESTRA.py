"""TDD contract for PRIORITY_1_PIPELINE_V3_ORCHESTRA.

Deliverables under test (resolved against D:\\__CoChem first, falling back to
the workspace-local __agentic/v3 directory):
  1. __agentic/v3/schema_v3.sql
  2. __agentic/v3/init_db.py
  3. __agentic/v3/docker-compose.yml
  4. __agentic/v3/Dockerfile

All checks run against real artifacts: real SQLite execution with independent
PRAGMA validation, AST inspection of init_db.py, and YAML parsing of the
compose file. Nothing under test is mocked.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# ── Path setup ────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
TARGET_ROOT = Path(r"D:\__CoChem")
TARGET_AGENTIC = TARGET_ROOT / "__agentic"
TARGET_V3 = TARGET_AGENTIC / "v3"
WORKSPACE_AGENTIC = WORKSPACE_ROOT / "__agentic"
WORKSPACE_V3 = WORKSPACE_AGENTIC / "v3"

for _p in (WORKSPACE_V3, TARGET_V3, TARGET_AGENTIC, WORKSPACE_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

REQUIRED_TABLES = {
    "kanban_tasks_v3",
    "kanban_telemetry_v3",
    "audit_verdicts",
    "credit_ledger_v3",
    "quota_state",
    "watchdog_events_v3",
}
FK_CHILD_TABLES = (
    "kanban_telemetry_v3",
    "audit_verdicts",
    "credit_ledger_v3",
    "watchdog_events_v3",
)
REQUIRED_SERVICES = ("kanban-worker", "watchdog", "mcp-server")
NETWORK_OR_DOCKER_MODULES = {
    "docker", "requests", "httpx", "urllib.request", "urllib3", "http.client",
    "socket", "aiohttp", "paramiko",
}


def get_v3_dir() -> Path:
    """Prefer whichever v3 dir actually holds the deliverables (target first)."""
    for candidate in (TARGET_V3, WORKSPACE_V3):
        try:
            if (candidate / "schema_v3.sql").is_file():
                return candidate
        except OSError:
            continue
    try:
        if TARGET_V3.is_dir():
            return TARGET_V3
    except OSError:
        pass
    return WORKSPACE_V3


def _no_window_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _load_schema_db(tmp_path: Path) -> sqlite3.Connection:
    schema_path = get_v3_dir() / "schema_v3.sql"
    assert schema_path.is_file(), f"schema_v3.sql missing at {schema_path}"
    sql_text = schema_path.read_text(encoding="utf-8")
    assert sql_text.strip(), "schema_v3.sql is empty"
    conn = sqlite3.connect(str(tmp_path / "schema_under_test.db"), timeout=5.0)
    conn.executescript(sql_text)
    conn.commit()
    return conn


def _load_compose() -> dict:
    compose_path = get_v3_dir() / "docker-compose.yml"
    assert compose_path.is_file(), f"docker-compose.yml missing at {compose_path}"
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "docker-compose.yml did not parse to a mapping"
    services = data.get("services")
    assert isinstance(services, dict) and services, "compose has no services mapping"
    return data


def _command_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _is_host_path(source: str) -> bool:
    return bool(source) and (
        source.startswith(("/", ".", "~", "$", "\\")) or bool(re.match(r"[A-Za-z]:", source))
    )


def _is_data_host_path(source: str) -> bool:
    """True only for host paths that are the v3 data dir (e.g. ./data, __agentic/v3/data)."""
    norm = source.replace("\\", "/").rstrip("/")
    if not norm or norm in ("/", "~", ".", "..") or re.fullmatch(r"[A-Za-z]:", norm):
        return False
    parts = [p for p in norm.split("/") if p]
    return "data" in parts and ".." not in parts


def _split_short_volume(spec: str) -> tuple[str, str]:
    """Split 'src:target[:mode]' short syntax, tolerating Windows drive letters."""
    m = re.match(r"^([A-Za-z]:[\\/][^:]*):([^:]+)(?::[^:]*)?$", spec)
    if m:
        return m.group(1), m.group(2)
    parts = spec.split(":")
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[1]


def _placeholder_for(decl_type: str):
    t = (decl_type or "").upper()
    if "INT" in t:
        return 1
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return 1.0
    return "tdd-value"


# ── [T1] AC1 ──────────────────────────────────────────────────────────────────
def test_schema_v3_tables_and_columns(tmp_path):
    schema_path = get_v3_dir() / "schema_v3.sql"
    assert schema_path.is_file(), f"schema_v3.sql missing at {schema_path}"
    sql_text = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(tmp_path / "t1.db"), timeout=5.0)
    try:
        conn.executescript(sql_text)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = REQUIRED_TABLES - tables
        assert not missing, f"schema_v3.sql did not create tables: {sorted(missing)}"

        columns = {row[1] for row in conn.execute("PRAGMA table_info('kanban_tasks_v3')")}
        for col in ("lease_expires_at", "heartbeat_at", "owner"):
            assert col in columns, f"kanban_tasks_v3 lacks column {col!r}; has {sorted(columns)}"
    finally:
        conn.close()


# ── [T2] AC2 ──────────────────────────────────────────────────────────────────
def test_schema_v3_index_and_column_order(tmp_path):
    conn = _load_schema_db(tmp_path)
    try:
        indexes = conn.execute("PRAGMA index_list('kanban_tasks_v3')").fetchall()
        assert indexes, "kanban_tasks_v3 has no indexes"

        index_columns = {}
        for idx in indexes:
            name = idx[1]
            info = conn.execute(f"PRAGMA index_info('{name}')").fetchall()
            index_columns[name] = [r[2] for r in sorted(info, key=lambda r: r[0])]

        expected = ["status", "priority", "lease_expires_at"]
        matching = [n for n, cols in index_columns.items() if cols == expected]
        assert matching, (
            f"No index on kanban_tasks_v3 with columns {expected} in order; "
            f"found {index_columns}"
        )
    finally:
        conn.close()


# ── [T3] AC3 ──────────────────────────────────────────────────────────────────
def test_schema_v3_foreign_keys_and_enforcement(tmp_path):
    conn = _load_schema_db(tmp_path)
    try:
        for child in FK_CHILD_TABLES:
            fks = conn.execute(f"PRAGMA foreign_key_list('{child}')").fetchall()
            # columns: id, seq, table, from, to, on_update, on_delete, match
            refs = [(fk[2], fk[3]) for fk in fks]
            assert ("kanban_tasks_v3", "task_id") in refs, (
                f"{child} lacks FOREIGN KEY(task_id) REFERENCES kanban_tasks_v3; got {refs}"
            )

        # Build an orphan kanban_telemetry_v3 row filling only required columns.
        info = conn.execute("PRAGMA table_info('kanban_telemetry_v3')").fetchall()
        # columns: cid, name, type, notnull, dflt_value, pk
        cols, vals = [], []
        for _cid, name, decl, notnull, dflt, pk in info:
            if name == "task_id":
                cols.append(name)
                vals.append(987654321 if "INT" in (decl or "").upper() else "orphan-task-does-not-exist")
            elif notnull and dflt is None and not (pk and "INT" in (decl or "").upper()):
                cols.append(name)
                vals.append(_placeholder_for(decl))
        assert "task_id" in cols, "kanban_telemetry_v3 has no task_id column"
        placeholders = ", ".join("?" for _ in cols)
        insert_sql = (
            f"INSERT INTO kanban_telemetry_v3 ({', '.join(cols)}) VALUES ({placeholders})"
        )

        # Control: with enforcement OFF the same row is accepted, so the only
        # thing that can reject it below is the foreign key.
        conn.execute("PRAGMA foreign_keys=OFF;")
        conn.execute("BEGIN")
        conn.execute(insert_sql, vals)
        conn.execute("ROLLBACK")

        conn.execute("PRAGMA foreign_keys=ON;")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(insert_sql, vals)
            conn.commit()
    finally:
        conn.close()


# ── [T4] AC4 ──────────────────────────────────────────────────────────────────
def test_init_db_subprocess_execution_and_pragmas(tmp_path):
    v3 = get_v3_dir()
    init_db_path = v3 / "init_db.py"
    schema_path = v3 / "schema_v3.sql"
    assert init_db_path.is_file(), f"init_db.py missing at {init_db_path}"
    assert schema_path.is_file(), f"schema_v3.sql missing at {schema_path}"

    db_path = tmp_path / "cochem_kanban_v3.db"
    result = subprocess.run(
        [sys.executable, str(init_db_path),
         "--db-path", str(db_path), "--schema-path", str(schema_path)],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        creationflags=_no_window_flags(),
    )
    assert result.returncode == 0, (
        f"init_db.py exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert db_path.is_file(), "init_db.py did not create the database file"

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "wal", f"journal_mode is {journal_mode!r}, expected 'wal'"
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout == 5000, f"busy_timeout is {busy_timeout}, expected 5000"

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        assert not missing, f"init_db.py did not apply schema; missing {sorted(missing)}"
    finally:
        conn.close()

    # busy_timeout is per-connection in SQLite, so init_db.py must set it itself.
    source = init_db_path.read_text(encoding="utf-8")
    assert "busy_timeout" in source and "5000" in source, (
        "init_db.py does not configure PRAGMA busy_timeout=5000"
    )


# ── [T5] AC5 ──────────────────────────────────────────────────────────────────
def test_docker_compose_services_replicas_and_limits():
    services = _load_compose()["services"]
    for svc in REQUIRED_SERVICES:
        assert svc in services, f"service {svc!r} missing; have {sorted(services)}"

    worker_deploy = services["kanban-worker"].get("deploy") or {}
    assert worker_deploy.get("replicas") == 3, (
        f"kanban-worker deploy.replicas is {worker_deploy.get('replicas')!r}, expected 3"
    )

    for svc in REQUIRED_SERVICES:
        limits = (((services[svc] or {}).get("deploy") or {}).get("resources") or {}).get("limits") or {}
        assert limits.get("cpus") not in (None, ""), f"{svc} lacks deploy.resources.limits.cpus"
        assert limits.get("memory") not in (None, ""), f"{svc} lacks deploy.resources.limits.memory"


# ── [T6] AC6 ──────────────────────────────────────────────────────────────────
def test_docker_compose_ports_healthcheck_commands_and_mounts():
    data = _load_compose()
    services = data["services"]
    for svc in REQUIRED_SERVICES:
        assert svc in services, f"service {svc!r} missing"

    mcp = services["mcp-server"]

    # Port 8787
    port_entries = list(mcp.get("ports") or []) + list(mcp.get("expose") or [])
    port_strings = []
    for entry in port_entries:
        if isinstance(entry, dict):
            port_strings.extend(str(entry.get(k, "")) for k in ("target", "published"))
        else:
            port_strings.append(str(entry))
    assert any(re.search(r"(^|[:\s])8787(/tcp)?$", p) for p in port_strings), (
        f"mcp-server does not expose 8787; ports={port_entries}"
    )

    # Healthcheck: /healthz via python or wget, never curl
    hc = mcp.get("healthcheck") or {}
    hc_text = _command_text(hc.get("test"))
    assert "/healthz" in hc_text, f"mcp-server healthcheck does not probe /healthz: {hc_text!r}"
    assert re.search(r"\b(python3?|wget)\b", hc_text), (
        f"mcp-server healthcheck must use python or wget: {hc_text!r}"
    )
    assert "curl" not in hc_text.lower(), "mcp-server healthcheck must not use curl"

    # Commands present and not trivially failing
    for svc in REQUIRED_SERVICES:
        cmd = _command_text(services[svc].get("command")) or _command_text(services[svc].get("entrypoint"))
        assert cmd.strip(), f"{svc} has no command"
        stripped = cmd.strip().lower()
        assert stripped not in ("false", "exit 1", "/bin/false"), f"{svc} command always fails: {cmd!r}"
        assert not re.search(r"\bexit\s+[1-9]", stripped), f"{svc} command exits non-zero: {cmd!r}"

    # Named volume cochem-data, bound (optionally via driver_opts) to __agentic/v3/data
    top_volumes = data.get("volumes") or {}
    assert "cochem-data" in top_volumes, f"top-level volume 'cochem-data' missing; have {list(top_volumes)}"
    ext = top_volumes.get("cochem-data") or {}
    driver_opts = (ext.get("driver_opts") or {}) if isinstance(ext, dict) else {}
    device = str(driver_opts.get("device", "") or "")
    if device:
        assert _is_data_host_path(device), (
            f"cochem-data driver_opts.device must point at the v3 data dir; got {device!r}"
        )

    cochem_data_targets = []
    for svc_name, svc in services.items():
        for vol in (svc or {}).get("volumes") or []:
            if isinstance(vol, dict):
                vtype = vol.get("type", "volume")
                source = str(vol.get("source", "") or "")
                target = str(vol.get("target", "") or "")
                assert vtype in ("volume", "tmpfs", "bind"), f"{svc_name} uses {vtype} mount: {vol}"
                if vtype == "bind":
                    assert _is_data_host_path(source), (
                        f"{svc_name} bind-mounts host path outside data: {vol}"
                    )
                    continue
            else:
                source, target = _split_short_volume(str(vol))
                if _is_host_path(source):
                    assert _is_data_host_path(source), (
                        f"{svc_name} mounts host path outside data: {vol!r}"
                    )
                    continue
                if source:
                    assert source in top_volumes, f"{svc_name} volume {source!r} is not a named volume"
            if source == "cochem-data":
                cochem_data_targets.append(target)

    assert cochem_data_targets, "no service mounts the cochem-data volume"
    assert all("data" in t for t in cochem_data_targets), (
        f"cochem-data must bind to a data path; targets={cochem_data_targets}"
    )


# ── [T7] AC7 ──────────────────────────────────────────────────────────────────
def test_dockerfile_base_image_user_rdkit_and_rootfs():
    dockerfile_path = get_v3_dir() / "Dockerfile"
    assert dockerfile_path.is_file(), f"Dockerfile missing at {dockerfile_path}"
    text = dockerfile_path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    from_lines = [ln for ln in lines if ln.upper().startswith("FROM ")]
    assert from_lines, "Dockerfile has no FROM instruction"
    assert any(
        re.match(r"FROM\s+(--\S+\s+)*python:3\.12(-slim[\w.-]*)?(\s+AS\s+\S+)?$", ln, re.I)
        for ln in from_lines
    ), f"Dockerfile does not use python:3.12 base: {from_lines}"

    user_lines = [ln for ln in lines if ln.upper().startswith("USER ")]
    assert user_lines, "Dockerfile has no USER instruction"
    assert re.match(r"USER\s+1000(:1000)?$", user_lines[-1], re.I), (
        f"final USER must be 1000, got {user_lines[-1]!r}"
    )

    run_text = " ".join(ln for ln in lines if ln.upper().startswith("RUN ")).lower()
    body_text = "\n".join(lines).lower()
    assert "rdkit" in run_text or ("rdkit" in body_text and "requirements" in body_text), (
        "Dockerfile does not install RDKit"
    )

    assert any(re.match(r"WORKDIR\s+/workspace/?$", ln, re.I) for ln in lines), (
        "Dockerfile must set WORKDIR /workspace"
    )
    workspace_volume = any(
        re.match(r"VOLUME\s+.*?/workspace\b", ln, re.I) for ln in lines
    )
    writable_workspace = workspace_volume or re.search(r"(chown|chmod)[^\n]*?/workspace", body_text)
    assert writable_workspace, "Dockerfile does not make /workspace writable (VOLUME or chown/chmod)"

    # Read-only rootfs: accept read_only: true on every compose service, or
    # Dockerfile VOLUME isolation of the writable /workspace.
    compose_read_only = False
    compose_path = get_v3_dir() / "docker-compose.yml"
    if compose_path.is_file():
        services = _load_compose()["services"]
        declared = [svc for svc in REQUIRED_SERVICES if svc in services
                    and "read_only" in (services[svc] or {})]
        for svc in declared:
            assert services[svc]["read_only"] is True, (
                f"{svc} sets read_only to {services[svc]['read_only']!r}; must be true if declared"
            )
        compose_read_only = all(
            svc in services and (services[svc] or {}).get("read_only") is True
            for svc in REQUIRED_SERVICES
        )
    assert compose_read_only or workspace_volume, (
        "read-only rootfs not configured: set read_only: true on all services in "
        "docker-compose.yml or isolate /workspace via Dockerfile VOLUME"
    )


# ── [T8] AC8 ──────────────────────────────────────────────────────────────────
def _subprocess_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_aliases, direct_funcs = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    module_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                direct_funcs.add(a.asname or a.name)
    return module_aliases, direct_funcs


def _load_llm_router():
    candidates = [TARGET_AGENTIC / "llm_router.py", WORKSPACE_ROOT / "llm_router.py"]
    for path in candidates:
        try:
            if path.is_file():
                spec = importlib.util.spec_from_file_location("llm_router", path)
                module = importlib.util.module_from_spec(spec)
                sys.modules.setdefault("llm_router", module)
                spec.loader.exec_module(module)
                return module
        except OSError:
            continue
    raise ImportError(f"llm_router.py not found in {candidates}")


def test_subprocess_flags_and_routing_invariants():
    init_db_path = get_v3_dir() / "init_db.py"
    assert init_db_path.is_file(), f"init_db.py missing at {init_db_path}"
    source = init_db_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(init_db_path))

    # Subprocess calls must carry creationflags and encoding='utf-8'
    mod_aliases, direct = _subprocess_names(tree)
    spawners = {"run", "Popen", "call", "check_call", "check_output"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        is_sp = (
            isinstance(f, ast.Attribute) and f.attr in spawners
            and isinstance(f.value, ast.Name) and f.value.id in mod_aliases
        ) or (isinstance(f, ast.Name) and f.id in direct and f.id in spawners)
        if not is_sp:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        assert "creationflags" in kw, f"subprocess call at line {node.lineno} lacks creationflags"
        enc = kw.get("encoding")
        assert isinstance(enc, ast.Constant) and str(enc.value).lower().replace("-", "") == "utf8", (
            f"subprocess call at line {node.lineno} lacks encoding='utf-8'"
        )
        assert "docker" not in ast.unparse(node).lower(), (
            f"init_db.py invokes docker at line {node.lineno}"
        )

    # No Docker daemon / network access
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    forbidden = {m for m in imported if m in NETWORK_OR_DOCKER_MODULES
                 or m.split(".")[0] in {"docker", "requests", "httpx", "aiohttp", "paramiko", "socket", "urllib3"}}
    assert not forbidden, f"init_db.py imports network/docker modules: {sorted(forbidden)}"
    assert "sqlite3" in imported, "init_db.py does not use sqlite3"

    # Router invariants
    llm_router = _load_llm_router()
    registry = llm_router.MODEL_REGISTRY
    assert isinstance(registry, dict) and registry, "MODEL_REGISTRY is empty"
    for agent, entry in registry.items():
        chain = entry.get("fallbacks") or []
        assert chain, f"{agent} has no fallback chain"
        assert tuple(chain[-1]) == ("halt", "graceful"), (
            f"{agent} fallback chain ends with {chain[-1]!r}, expected ('halt', 'graceful')"
        )
        assert "ollama" not in str(entry.get("provider", "")).lower(), f"{agent} primary is ollama"
        for provider, model in chain:
            assert "ollama" not in f"{provider} {model}".lower(), f"{agent} fallback contains ollama"
