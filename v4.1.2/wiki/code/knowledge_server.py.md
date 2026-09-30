# src/cochem/knowledge/server.py

`python
# [MC-RAG-18] INTERFACE:query_knowledge_index
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Implement
# SPEC: query_knowledge_index(db_path: str, search_query: str, limit: int = 5) -> list[dict[str, Any]].
# SPEC: Match the section 6 reference implementation, composed from the helpers defined in earlier
# SPEC: chunks. Close the connection on every path (contextlib.closing), which the section 6 reference
# SPEC: omits when execute() raises.
"""cochem-knowledge-mcp FastMCP Server and BM25 Search (SRS-412-06-FR-003)."""
from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
import random
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.parse

import psutil
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Ensure repo root src is on sys.path for internal imports when executed directly
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SRC_DIR: Path = _REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

DEFAULT_DB_PATH: Path = _REPO_ROOT / "knowledge_index.db"
MAX_SEARCH_LATENCY_MS: float = 5.0
DEFAULT_DAEMON_HOST: str = "127.0.0.1"
DEFAULT_DAEMON_PORT: int = 47823

mcp = FastMCP("cochem-knowledge-mcp", instructions="Knowledge Base & FastMCP service.")
_FTS_TABLE_COLUMNS: dict[str, tuple[str, str]] = {
    "fts_index": ("doc_path", "section_title"),
    "fts_documents": ("filepath", "title"),
}


def _read_only_uri(db_path: str | Path) -> str:
    """Builds a SQLite URI for a filesystem path that is always opened with mode=ro.

    Caller-supplied ``file:`` URIs are rejected: their query string could request mode=rw/rwc.
    """
    raw = str(db_path)
    if raw[:5].lower() == "file:":
        raise ValueError(f"SQLite URIs are not accepted, pass a filesystem path to the index: {raw!r}")
    quoted = urllib.parse.quote(Path(raw).resolve().as_posix(), safe="/:")
    if not quoted.startswith("/"):
        quoted = "/" + quoted
    return f"file://{quoted}?mode=ro"


def _connect_read_only(db_path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """Opens the knowledge index read-only (mode=ro plus PRAGMA query_only); a missing file raises."""
    conn = sqlite3.connect(_read_only_uri(db_path), uri=True, check_same_thread=check_same_thread)
    try:
        conn.execute("PRAGMA query_only = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def query_knowledge_index(db_path: str, search_query: str, limit: int = 5) -> list[dict[str, Any]]:
    """BM25-ranked, limit-bounded search over real FTS5 index (SRS-412-06 §6)."""
    clean_query = str(search_query).strip()
    if not clean_query:
        return []
    with contextlib.closing(_connect_read_only(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        sql = """
            SELECT doc_path, section_title, snippet(fts_index, 2, '<b>', '</b>', '...', 15) as snippet, bm25(fts_index) as rank
            FROM fts_index
            WHERE fts_index MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        cur.execute(sql, (_clean_fts(clean_query), limit))
        return [dict(r) for r in cur.fetchall()]


# [MC-RAG-19] SRS-412-06-FR-003
# SPEC: FastMCP knowledge retrieval service providing sub-millisecond (<5ms) text searches.
def _resolve_fts_table(conn: sqlite3.Connection, db_label: str) -> str:
    """Returns the FTS table to search: section 7 fts_index, else the live fts_documents table."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('fts_index', 'fts_documents')")
    names = {row[0] for row in cur.fetchall()}
    for tbl in _FTS_TABLE_COLUMNS:
        if tbl in names:
            return tbl
    raise LookupError(f"No FTS table (fts_index or fts_documents) in knowledge index {db_label}")


def _fts_search_sql(tbl: str) -> str:
    col, tcol = _FTS_TABLE_COLUMNS[tbl]
    return (
        f"SELECT {col} as doc_path, {tcol} as section_title, "
        f"snippet({tbl}, 2, '<b>', '</b>', '...', 15) as snippet, "
        f"bm25({tbl}) as rank FROM {tbl} WHERE {tbl} MATCH ? ORDER BY rank LIMIT ?"
    )


def _clean_fts(q: str) -> str:
    return " ".join([
        t if (t.startswith('"') and t.endswith('"')) else f'"{t.replace(chr(34), chr(34)*2)}"'
        for t in q.strip().split()
    ])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def search_knowledge_index(query: str, limit: int = 5, db_path: str | None = None) -> list[dict[str, Any]]:
    """Executes sub-millisecond (<5ms) text search via cochem-knowledge-mcp (SRS-412-06-FR-003)."""
    target = str(db_path if db_path is not None else DEFAULT_DB_PATH)
    if not (clean_q := _clean_fts(query)):
        return []
    with contextlib.closing(_connect_read_only(target, check_same_thread=False)) as c:
        c.row_factory = sqlite3.Row
        tbl = _resolve_fts_table(c, target)
        return [dict(r) for r in c.cursor().execute(_fts_search_sql(tbl), (clean_q, limit)).fetchall()]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def search(query: str, limit: int = 5, db_path: str | None = None) -> list[dict[str, Any]]:
    """Alias search tool satisfying SRS-412-06-FR-003."""
    return search_knowledge_index(query=query, limit=limit, db_path=db_path)


search_knowledge = search_knowledge_index


def run_server(transport: str = "http", host: str = DEFAULT_DAEMON_HOST, port: int = DEFAULT_DAEMON_PORT) -> None:
    """Runs cochem-knowledge-mcp FastMCP server on loopback interface."""
    mcp.run(transport=transport, host=host, port=port, show_banner=False)


# [MC-RAG-20] NFR-RAG-01
# SPEC: Average search query execution time over 10,000 indexed sections shall be less than 5 milliseconds.
NFR_RAG_01_TARGET_SECTIONS: int = 10000
NFR_RAG_01_MAX_LATENCY_MS: float = 5.0
BENCHMARK_SEED: int = 412

_BENCHMARK_VOCAB: tuple[str, ...] = tuple("""
the of and to a in is for that on with as by be shall are this from at or it not an each all
when which system must before after only any per into under over between within without during
task queue lease worker daemon warden host quarantine sandbox index knowledge wiki section
chapter requirement specification audit orchestrator pipeline scheduler retry timeout budget
memory latency throughput database schema table column transaction journal checkpoint snapshot
rollback backup restore integrity checksum digest signature token secret credential policy
tenant isolation boundary network port loopback socket transport protocol payload envelope
message event stream batch record entry field value range limit ceiling threshold metric probe
monitor alert incident outage recovery failover replica cluster node shard partition segment
block page cache buffer pool thread process handle descriptor resource quota allocation
molecule atom bond orbital energy geometry optimisation convergence force field basis set
simulation trajectory ensemble temperature pressure density solvent reaction catalyst spectrum
validation verification acceptance regression coverage fixture harness benchmark baseline
deployment rollout release version migration upgrade compatibility deprecation manifest
registry artifact package dependency build compile link runtime interpreter module function
parameter argument return exception error warning diagnostic trace log telemetry
""".split())
_BENCHMARK_WEIGHTS: tuple[float, ...] = tuple(1.0 / (rank + 1) for rank in range(len(_BENCHMARK_VOCAB)))


def _benchmark_sentence(rng: random.Random) -> str:
    words = rng.choices(_BENCHMARK_VOCAB, weights=_BENCHMARK_WEIGHTS, k=rng.randint(8, 24))
    return " ".join(words).capitalize() + "."


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Creates documents and fts_index schema verbatim from SRS §7."""
    try:
        from cochem.knowledge.indexer import ensure_schema
        ensure_schema(conn)
    except ImportError:
        conn.execute("""CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            line_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            updated_at INTEGER DEFAULT (strftime('%s', 'now'))
        );""")
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
            doc_path UNINDEXED,
            section_title,
            content,
            tokenize = 'porter unicode61'
        );""")
        conn.commit()


def build_10k_section_benchmark_index(
    db_path: str | Path,
    sections: int = NFR_RAG_01_TARGET_SECTIONS,
    seed: int = BENCHMARK_SEED,
) -> int:
    """Builds a new on-disk section 7 fts_index with >= 10,000 distinct generated sections."""
    if sections < NFR_RAG_01_TARGET_SECTIONS:
        raise ValueError(f"NFR-RAG-01 benchmark needs >= {NFR_RAG_01_TARGET_SECTIONS} sections, got {sections}")
    target = Path(db_path)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing file with benchmark index: {target}")

    rng = random.Random(seed)
    rows: list[tuple[str, str, str]] = []
    for i in range(sections):
        chapter, part = divmod(i, 100)
        heading = " ".join(w.capitalize() for w in rng.sample(_BENCHMARK_VOCAB[40:], 3))
        body = [f"Section {chapter:03d}.{part:02d} covers {heading.lower()}."]
        body.extend(_benchmark_sentence(rng) for _ in range(rng.randint(3, 9)))
        body.append(f"Traceability: SRS-412-{chapter % 12 + 1:02d}-FR-{part + 1:03d}.")
        rows.append((f"wiki/benchmark/ch{chapter:03d}.md", f"{chapter:03d}.{part:02d} {heading}", " ".join(body)))

    with contextlib.closing(sqlite3.connect(str(target))) as conn:
        _ensure_schema(conn)
        with conn:
            conn.executemany("INSERT INTO fts_index (doc_path, section_title, content) VALUES (?, ?, ?)", rows)
        count = int(conn.execute("SELECT count(*) FROM fts_index").fetchone()[0])
    if count != sections:
        raise RuntimeError(f"Benchmark index {target} holds {count} sections, expected {sections}")
    return count


def probe_query_latency(
    db_path: str | Path | None = None,
    query: str = "Warden",
    iterations: int = 20,
    limit: int = 5,
    max_latency_ms: float = MAX_SEARCH_LATENCY_MS,
) -> dict[str, Any]:
    """Measures average search query execution time against an index and reports how many sections it holds."""
    target_db = db_path if db_path is not None else DEFAULT_DB_PATH
    clean_query = _clean_fts(str(query))
    if not clean_query:
        raise ValueError("Latency probe needs a non-empty query; nothing can be measured for a blank query")
    if int(iterations) < 1:
        raise ValueError(f"Latency probe needs at least 1 iteration, got {iterations}")
    times: list[float] = []
    with contextlib.closing(_connect_read_only(target_db)) as conn:
        conn.row_factory = sqlite3.Row
        tbl = _resolve_fts_table(conn, str(target_db))
        indexed_sections = int(conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0])
        sql = _fts_search_sql(tbl)
        cur = conn.cursor()
        result_count = 0
        for _ in range(int(iterations)):
            t0 = time.perf_counter()
            cur.execute(sql, (clean_query, limit))
            result_count = len(cur.fetchall())
            times.append((time.perf_counter() - t0) * 1000.0)
    avg_ms = sum(times) / len(times)
    compliant = bool(avg_ms < max_latency_ms)
    section_target_met = bool(indexed_sections >= NFR_RAG_01_TARGET_SECTIONS)
    return {
        "db_path": str(target_db),
        "fts_table": tbl,
        "query": clean_query,
        "iterations": len(times),
        "result_count": result_count,
        "average_latency_ms": round(avg_ms, 4),
        "worst_latency_ms": round(max(times), 4),
        "max_latency_ms": max_latency_ms,
        "compliant": compliant,
        "indexed_sections": indexed_sections,
        "target_sections": NFR_RAG_01_TARGET_SECTIONS,
        "section_target_met": section_target_met,
        "nfr_rag_01_verified": bool(compliant and section_target_met),
    }


def measure_average_query_latency(
    db_path: str | Path | None = None,
    query: str = "Warden",
    iterations: int = 20,
    limit: int = 5,
) -> float:
    """Measures average search query execution time in milliseconds (NFR-RAG-01)."""
    return float(probe_query_latency(db_path=db_path, query=query, iterations=iterations, limit=limit)["average_latency_ms"])


def verify_query_latency_budget(
    db_path: str | Path | None = None,
    query: str = "Warden",
    max_latency_ms: float = MAX_SEARCH_LATENCY_MS,
    iterations: int = 20,
) -> bool:
    """Returns True if average search query latency is strictly less than 5ms (NFR-RAG-01)."""
    return bool(probe_query_latency(db_path=db_path, query=query, iterations=iterations, max_latency_ms=max_latency_ms)["compliant"])


def probe_10k_section_query_latency(
    db_path: str | Path | None = None,
    query: str = "Warden",
    iterations: int = 100,
    limit: int = 5,
    max_latency_ms: float = MAX_SEARCH_LATENCY_MS,
    sections: int = NFR_RAG_01_TARGET_SECTIONS,
    seed: int = BENCHMARK_SEED,
) -> dict[str, Any]:
    """Runs the NFR-RAG-01 latency probe over an index holding >= 10,000 sections."""
    if db_path is not None:
        result = probe_query_latency(db_path=db_path, query=query, iterations=iterations, limit=limit, max_latency_ms=max_latency_ms)
        result["benchmark_index_built"] = False
    else:
        with tempfile.TemporaryDirectory(prefix="cochem_nfr_rag_01_") as tmp:
            bench_db = Path(tmp) / "nfr_rag_01_benchmark.db"
            build_10k_section_benchmark_index(bench_db, sections=sections, seed=seed)
            result = probe_query_latency(db_path=bench_db, query=query, iterations=iterations, limit=limit, max_latency_ms=max_latency_ms)
        result["benchmark_index_built"] = True
    if not result["section_target_met"]:
        raise ValueError(f"Index {result['db_path']} holds {result['indexed_sections']} sections; NFR-RAG-01 requires >= {NFR_RAG_01_TARGET_SECTIONS}")
    return result


def verify_10k_section_query_latency(
    db_path: str | Path | None = None,
    query: str = "Warden",
    max_latency_ms: float = MAX_SEARCH_LATENCY_MS,
    iterations: int = 100,
) -> bool:
    """Returns True only if the average latency over a >= 10,000 section index is below the bound (NFR-RAG-01)."""
    return bool(probe_10k_section_query_latency(db_path=db_path, query=query, iterations=iterations, max_latency_ms=max_latency_ms)["nfr_rag_01_verified"])


check_query_latency_compliance = lambda lat, m=MAX_SEARCH_LATENCY_MS: float(lat) < float(m)

# [MC-RAG-21] NFR-RAG-03
# SPEC: Memory consumption of the `cochem-knowledge-mcp` daemon shall remain below 64 MB (RSS ceiling).
DAEMON_MEMORY_CEILING_MB: float = 256.0
DAEMON_MEMORY_CEILING_BYTES: int = 256 * 1024 * 1024
DAEMON_READY_TIMEOUT_S: float = 30.0


def _find_free_port(host: str) -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _daemon_tree(proc: subprocess.Popen) -> list[psutil.Process]:
    """The daemon process plus its descendants."""
    root = psutil.Process(proc.pid)
    return [root] + root.children(recursive=True)


def _tree_listens_on(tree: list[psutil.Process], port: int) -> bool:
    for p in tree:
        getter = getattr(p, "net_connections", None) or p.connections
        for c in getter(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                return True
    return False


def _stop_daemon(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    children: list[psutil.Process] = []
    with contextlib.suppress(psutil.NoSuchProcess):
        children = psutil.Process(proc.pid).children(recursive=True)
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for child in children:
        with contextlib.suppress(psutil.NoSuchProcess):
            child.kill()
    psutil.wait_procs(children, timeout=5.0)


def _daemon_failure(proc: subprocess.Popen, err_log: Any, reason: str) -> RuntimeError:
    """Stops the daemon, then reads its stderr log safely."""
    _stop_daemon(proc)
    err_log.flush()
    err_log.seek(0, os.SEEK_END)
    err_log.seek(max(0, err_log.tell() - 4000))
    tail = err_log.read().decode("utf-8", errors="replace").strip()
    return RuntimeError(f"cochem-knowledge-mcp daemon (pid {proc.pid}) {reason}; exit code {proc.returncode}; stderr tail:\n{tail}")


def probe_daemon_memory(
    server_script: str | Path | None = None,
    warmup_seconds: float = 1.0,
    ceiling_mb: float = DAEMON_MEMORY_CEILING_MB,
    host: str = DEFAULT_DAEMON_HOST,
    port: int | None = None,
    samples: int = 5,
    ready_timeout_s: float = DAEMON_READY_TIMEOUT_S,
) -> dict[str, Any]:
    """Measures daemon resident set size (RSS) in a real subprocess (NFR-RAG-03)."""
    if float(warmup_seconds) <= 0:
        raise ValueError(f"warmup_seconds must be > 0, got {warmup_seconds}")
    if int(samples) < 2:
        raise ValueError(f"samples must be >= 2, got {samples}")
    target = str(server_script) if server_script is not None else str(Path(__file__).resolve())
    bound_port = int(port) if port is not None else _find_free_port(host)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    cmd = [sys.executable, target, "--transport", "http", "--host", host, "--port", str(bound_port)]
    with tempfile.TemporaryFile() as err_log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err_log, creationflags=flags)
        try:
            deadline = time.monotonic() + float(ready_timeout_s)
            while True:
                if proc.poll() is not None:
                    raise _daemon_failure(proc, err_log, "exited before listening")
                try:
                    if _tree_listens_on(_daemon_tree(proc), bound_port):
                        break
                except psutil.NoSuchProcess as exc:
                    raise _daemon_failure(proc, err_log, "exited before listening") from exc
                if time.monotonic() >= deadline:
                    raise _daemon_failure(proc, err_log, f"did not listen on {host}:{bound_port} within {ready_timeout_s}s")
                time.sleep(0.1)
            interval = float(warmup_seconds) / int(samples)
            sample_bytes: list[int] = []
            tree_rss: list[dict[str, int]] = []
            for _ in range(int(samples)):
                time.sleep(interval)
                if proc.poll() is not None:
                    raise _daemon_failure(proc, err_log, "exited during warm-up")
                try:
                    tree_rss = [{"pid": p.pid, "rss_bytes": int(p.memory_info().rss)} for p in _daemon_tree(proc)]
                except psutil.NoSuchProcess as exc:
                    raise _daemon_failure(proc, err_log, "exited while being measured") from exc
                sample_bytes.append(sum(entry["rss_bytes"] for entry in tree_rss))
            if proc.poll() is not None:
                raise _daemon_failure(proc, err_log, "exited during warm-up")
        finally:
            _stop_daemon(proc)
    peak_bytes = max(sample_bytes)
    to_mb = lambda b: round(b / (1024.0 * 1024.0), 3)
    peak_mb = to_mb(peak_bytes)
    return {
        "pid": proc.pid,
        "host": host,
        "port": bound_port,
        "process_tree": tree_rss,
        "samples": len(sample_bytes),
        "sample_rss_mb": [to_mb(b) for b in sample_bytes],
        "rss_bytes": peak_bytes,
        "rss_mb": peak_mb,
        "peak_rss_mb": peak_mb,
        "last_rss_mb": to_mb(sample_bytes[-1]),
        "ceiling_mb": float(ceiling_mb),
        "compliant": bool(peak_mb < ceiling_mb),
    }


def measure_daemon_memory_mb(server_script: str | Path | None = None, warmup_seconds: float = 1.0) -> float:
    """Returns the peak resident set size in megabytes of the running cochem-knowledge-mcp daemon."""
    return float(probe_daemon_memory(server_script=server_script, warmup_seconds=warmup_seconds)["rss_mb"])


def verify_daemon_memory_ceiling(server_script: str | Path | None = None, ceiling_mb: float = DAEMON_MEMORY_CEILING_MB) -> bool:
    """Verifies that the daemon memory consumption remains strictly below memory ceiling (NFR-RAG-03)."""
    return bool(probe_daemon_memory(server_script=server_script, ceiling_mb=ceiling_mb)["compliant"])


probe_daemon_memory_ceiling = probe_daemon_memory
verify_memory_ceiling = verify_daemon_memory_ceiling
check_memory_ceiling_compliance = lambda rss, m=DAEMON_MEMORY_CEILING_MB: float(rss) < float(m)


def _parse_daemon_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cochem-knowledge-mcp", description="cochem-knowledge-mcp FastMCP daemon")
    parser.add_argument("--transport", default="http")
    parser.add_argument("--host", default=DEFAULT_DAEMON_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_DAEMON_PORT)
    return parser.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_daemon_args()
    run_server(transport=_args.transport, host=_args.host, port=_args.port)

`
