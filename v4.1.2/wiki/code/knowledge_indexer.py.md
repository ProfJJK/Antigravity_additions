# src/cochem/knowledge/indexer.py

`python
# [MC-RAG-11] DATA_MODEL:documents
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Define the
# SPEC: documents CREATE statement verbatim from section 7 and have ensure_schema(conn) execute it.
# SPEC: Columns: id INTEGER PRIMARY KEY AUTOINCREMENT; doc_path TEXT UNIQUE NOT NULL; title TEXT NOT
# SPEC: NULL; line_count INTEGER NOT NULL; sha256 TEXT NOT NULL; updated_at INTEGER DEFAULT
# SPEC: (strftime('%s', 'now'))
"""Dual Wiki Knowledge Indexer & SQLite FTS5 Storage (SRS-412-06-FR-002, FR-008)."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

# Ensure repo root src is on sys.path for internal imports when executed directly
def _find_repo_src() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "src"
        if candidate.is_dir() and (candidate / "cochem").is_dir():
            return candidate
    return None

_SRC_DIR = _find_repo_src()
if _SRC_DIR is not None and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

DOCUMENTS_TABLE_NAME: str = "documents"
DEFAULT_INDEX_DB_NAME: str = "knowledge_index.db"

DOCUMENTS_TABLE_DDL: str = """CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    line_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    updated_at INTEGER DEFAULT (strftime('%s', 'now'))
);"""

SCHEMA_STATEMENTS: list[str] = [DOCUMENTS_TABLE_DDL]


def create_documents_table(conn: sqlite3.Connection) -> None:
    """Executes the documents table DDL statement verbatim from section 7."""
    conn.execute(DOCUMENTS_TABLE_DDL)
    conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensures that the knowledge base tables exist in the SQLite database."""
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()


init_schema = ensure_schema
ensure_documents_table = create_documents_table

# [MC-RAG-12] DATA_MODEL:fts_index
# SPEC: Append after the previous chunk without editing earlier lines. Define the fts_index CREATE
# SPEC: statement verbatim from section 7 and have ensure_schema(conn) execute it. Columns: USING fts5:
# SPEC: doc_path UNINDEXED; section_title; content; tokenize = 'porter unicode61' (Gap: the live
# SPEC: knowledge_index.db holds fts_documents(document_id, title, body, tags, filepath), which
# SPEC: tests/test_stage6_activation_mcp.py and tests/test_cross_feature_interactions.py query.)
# SPEC: Implement section 7 as written; do not drop or rename fts_documents here.
FTS_INDEX_TABLE_NAME: str = "fts_index"
FTS_TABLE_NAME: str = FTS_INDEX_TABLE_NAME
FTS_INDEX_COLUMNS: tuple[str, ...] = ("doc_path", "section_title", "content")
FTS_INDEX_TOKENIZER: str = "porter unicode61"

FTS_INDEX_TABLE_DDL: str = """CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
    doc_path UNINDEXED,
    section_title,
    content,
    tokenize = 'porter unicode61'
);"""
FTS_INDEX_DDL: str = FTS_INDEX_TABLE_DDL

if FTS_INDEX_DDL not in SCHEMA_STATEMENTS:
    SCHEMA_STATEMENTS.append(FTS_INDEX_DDL)


def create_fts_index_table(conn: sqlite3.Connection) -> None:
    """Executes the fts_index virtual table DDL statement verbatim from section 7."""
    conn.execute(FTS_INDEX_DDL)
    conn.commit()


def verify_fts_index_exists(conn: sqlite3.Connection) -> bool:
    """Checks whether the fts_index virtual table exists in the database."""
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (FTS_INDEX_TABLE_NAME,),
    )
    return cur.fetchone() is not None


create_fts_index = create_fts_index_table
ensure_fts_index_table = create_fts_index_table
ensure_fts_index = create_fts_index_table
check_fts_index_table = verify_fts_index_exists
verify_fts_index = verify_fts_index_exists

# [MC-RAG-13] SRS-412-06-FR-002
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-06-FR-002: All
# SPEC: knowledge documents shall be indexed in the SQLite FTS5 database (`knowledge_index.db`) using
# SPEC: BM25 relevance scoring.
from cochem.knowledge.corpus import split_markdown_sections as _split_sections_for_index

_INDEX_DOCUMENT_SAVEPOINT: str = "mc_rag_13_index_document"


def index_document(
    conn: sqlite3.Connection,
    file_path: str | Path,
    doc_path: str | None = None,
) -> int:
    """Index one Markdown document section-by-section into the fts_index FTS5 table.

    Each ATX-heading section becomes one fts_index row (doc_path, section_title, content);
    FTS5 then ranks matches with its built-in bm25() function (SRS-412-06-FR-002).
    The documents row (title, line_count, sha256) is upserted in the same unit of work.

    The previous sections of the same doc_path are replaced. All writes run inside a
    SAVEPOINT: on any error they are rolled back and the error is re-raised unchanged.
    The outermost SAVEPOINT release commits when the connection had no open transaction;
    inside a caller's transaction the caller keeps control of the commit.

    The schema must already exist (call ensure_schema(conn) first); a missing table
    surfaces as sqlite3.OperationalError. Raises FileNotFoundError for a missing file and
    UnicodeDecodeError for a file that is not valid UTF-8.

    Returns the number of sections inserted into fts_index.
    """
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Markdown document not found: {p}")
    raw = p.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    content = raw.decode("utf-8", errors="strict")
    key = str(file_path) if doc_path is None else str(doc_path)
    sections = [s for s in _split_sections_for_index(content, default_title=p.stem) if s.content]
    title = sections[0].title if sections and sections[0].title else p.stem
    rows = [(key, s.title, s.content) for s in sections]

    conn.execute(f"SAVEPOINT {_INDEX_DOCUMENT_SAVEPOINT}")
    try:
        conn.execute("DELETE FROM fts_index WHERE doc_path = ?", (key,))
        conn.executemany(
            "INSERT INTO fts_index (doc_path, section_title, content) VALUES (?, ?, ?)", rows
        )
        conn.execute(
            """INSERT INTO documents (doc_path, title, line_count, sha256, updated_at)
               VALUES (?, ?, ?, ?, strftime('%s', 'now'))
               ON CONFLICT(doc_path) DO UPDATE SET title = excluded.title,
                   line_count = excluded.line_count, sha256 = excluded.sha256,
                   updated_at = strftime('%s', 'now')""",
            (key, title, len(content.splitlines()), sha256),
        )
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {_INDEX_DOCUMENT_SAVEPOINT}")
        conn.execute(f"RELEASE SAVEPOINT {_INDEX_DOCUMENT_SAVEPOINT}")
        raise
    conn.execute(f"RELEASE SAVEPOINT {_INDEX_DOCUMENT_SAVEPOINT}")
    return len(rows)


index_markdown_document = index_document
index_document_sections = index_document

# [MC-RAG-17] NFR-RAG-02
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy NFR-RAG-02: FTS5 index
# SPEC: size shall not exceed 4_0x the raw Markdown corpus size.
MAX_INDEX_TO_CORPUS_RATIO: float = 4.0
NFR_RAG_02_MAX_RATIO: float = MAX_INDEX_TO_CORPUS_RATIO


def measure_corpus_size_bytes(
    corpus: str | Path | list[str | Path] | tuple[str | Path, ...] | None = None,
) -> int:
    """Measures total size in bytes of raw Markdown corpus (.md, .markdown files)."""
    paths = [Path(".sources"), Path("wiki")] if corpus is None else [Path(p) for p in (corpus if isinstance(corpus, (list, tuple)) else [corpus])]
    total = 0
    for p in paths:
        if p.is_file():
            if p.suffix.lower() in (".md", ".markdown"):
                total += p.stat().st_size
        elif p.is_dir():
            for ext in ("*.md", "*.markdown"):
                total += sum(f.stat().st_size for f in p.rglob(ext) if f.is_file())
    return total


def measure_index_size_bytes(db_path: str | Path | sqlite3.Connection) -> int:
    """Measures physical size in bytes of SQLite FTS5 index database file and journals."""
    if isinstance(db_path, sqlite3.Connection):
        cur = db_path.cursor()
        cur.execute("PRAGMA page_count;")
        cnt = int(cur.fetchone()[0])
        cur.execute("PRAGMA page_size;")
        return cnt * int(cur.fetchone()[0])
    p = Path(db_path)
    if not p.is_file():
        raise FileNotFoundError(f"FTS5 index database not found: {p}")
    total = p.stat().st_size
    for extra in (p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")):
        if extra.is_file():
            total += extra.stat().st_size
    return total


def calculate_index_to_corpus_ratio(index_size_bytes: int, corpus_size_bytes: int) -> float:
    """Calculates index-to-corpus size ratio satisfying NFR-RAG-02."""
    if corpus_size_bytes <= 0:
        return 0.0 if index_size_bytes == 0 else float("inf")
    return round(float(index_size_bytes) / float(corpus_size_bytes), 4)


def probe_index_to_corpus_ratio(
    db_path: str | Path | sqlite3.Connection,
    corpus: str | Path | list[str | Path] | tuple[str | Path, ...] | None = None,
    max_ratio: float = MAX_INDEX_TO_CORPUS_RATIO,
) -> dict[str, Any]:
    """Probes whether SQLite FTS5 index size exceeds 4_0x raw Markdown corpus size (NFR-RAG-02)."""
    idx_sz = measure_index_size_bytes(db_path)
    crp_sz = measure_corpus_size_bytes(corpus)
    ratio = calculate_index_to_corpus_ratio(idx_sz, crp_sz)
    ok = bool(ratio <= max_ratio)
    return {
        "index_size_bytes": idx_sz,
        "corpus_size_bytes": crp_sz,
        "ratio": ratio,
        "max_ratio": max_ratio,
        "within_budget": ok,
        "compliant": ok,
        "passed": ok,
    }


def verify_index_to_corpus_ratio(
    db_path: str | Path | sqlite3.Connection,
    corpus: str | Path | list[str | Path] | tuple[str | Path, ...] | None = None,
    max_ratio: float = MAX_INDEX_TO_CORPUS_RATIO,
) -> bool:
    """Verifies that FTS5 index size <= 4_0x raw Markdown corpus size (NFR-RAG-02)."""
    return bool(probe_index_to_corpus_ratio(db_path, corpus, max_ratio=max_ratio)["within_budget"])


class IndexToCorpusRatioProbe:
    """Probe validating that SQLite FTS5 index size <= 4_0x raw Markdown corpus size (NFR-RAG-02)."""

    def __init__(self, max_ratio: float = MAX_INDEX_TO_CORPUS_RATIO) -> None:
        self.max_ratio = max_ratio

    def probe(self, db_path: str | Path | sqlite3.Connection, corpus: Any = None) -> dict[str, Any]:
        return probe_index_to_corpus_ratio(db_path, corpus, max_ratio=self.max_ratio)

    def verify(self, db_path: str | Path | sqlite3.Connection, corpus: Any = None) -> bool:
        return verify_index_to_corpus_ratio(db_path, corpus, max_ratio=self.max_ratio)


probe_ratio = probe_index_to_corpus_ratio
verify_ratio = verify_index_to_corpus_ratio
probe_index_ratio = probe_index_to_corpus_ratio
verify_index_ratio = verify_index_to_corpus_ratio
measure_corpus_size = measure_corpus_size_bytes
measure_index_size = measure_index_size_bytes
measure_markdown_corpus_size = measure_corpus_size_bytes
measure_fts5_index_size = measure_index_size_bytes
probe_4_0x_index_to_corpus_ratio = probe_index_to_corpus_ratio
verify_4_0x_index_to_corpus_ratio = verify_index_to_corpus_ratio

# [MC-RAG-16] FAILURE:FTS5 Index Desynchronization
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'FTS5
# SPEC: Index Desynchronization': If document hash differs from indexed hash, the indexer triggers a
# SPEC: non-blocking background re-index of the file. Compare documents.sha256 against the file on disk;
# SPEC: re-index only drifted files.
from cochem.knowledge.corpus import read_lossless_utf8, split_markdown_sections


def compute_file_sha256(file_path: str | Path) -> str:
    """Computes SHA-256 cryptographic digest of a physical file on disk."""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Missing file: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(65536):
            h.update(b)
    return h.hexdigest()


def check_document_drift(conn: sqlite3.Connection, file_path: str | Path) -> bool:
    """Returns True if physical file SHA-256 differs from documents.sha256 in index."""
    p = Path(file_path).resolve()
    if not p.is_file():
        return True
    cur = conn.cursor()
    cur.execute(
        "SELECT sha256 FROM documents WHERE doc_path = ? OR doc_path = ? LIMIT 1",
        (str(file_path), str(p)),
    )
    row = cur.fetchone()
    return bool(not row or row[0] != compute_file_sha256(p))


is_document_drifted = check_document_drift


def find_drifted_documents(conn: sqlite3.Connection) -> list[str]:
    """Compares documents.sha256 against physical files on disk; returns drifted paths."""
    cur = conn.cursor()
    cur.execute("SELECT doc_path, sha256 FROM documents")
    return [
        dp for dp, h in cur.fetchall() if not Path(dp).is_file() or compute_file_sha256(dp) != h
    ]


detect_hash_drift = find_drifted_documents

_REINDEX_FILE_SAVEPOINT: str = "mc_rag_16_reindex_file"


def reindex_drifted_file(conn: sqlite3.Connection, file_path: str | Path) -> bool:
    """Re-indexes a single drifted document; index_document is the one writer of documents/fts_index.

    The row key is str(file_path) as given; rows stored under the resolved alias of the same file
    are removed so a document is never indexed twice. A file that no longer exists is removed from
    documents, fts_index and (when present) the legacy fts_documents table. When the legacy
    fts_documents table exists its row is refreshed from exactly the text index_document stored.
    Everything runs inside one SAVEPOINT: any error rolls all of it back and propagates. The
    connection is committed at the end. Returns True.
    """
    p = Path(file_path).resolve()
    key, alias = str(file_path), str(p)
    keys = (key, alias)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('fts_index', 'fts_documents')"
        )
    }
    # Take the write lock before the first read: SQLite does not run the busy handler when a transaction
    # that already holds a read lock (FTS5 reads its shadow tables first) upgrades to a write lock.
    own_txn = not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"SAVEPOINT {_REINDEX_FILE_SAVEPOINT}")
    try:
        if not p.is_file():
            conn.execute("DELETE FROM documents WHERE doc_path IN (?, ?)", keys)
            if "fts_index" in tables:
                conn.execute("DELETE FROM fts_index WHERE doc_path IN (?, ?)", keys)
        else:
            if alias != key:
                conn.execute("DELETE FROM documents WHERE doc_path = ?", (alias,))
                conn.execute("DELETE FROM fts_index WHERE doc_path = ?", (alias,))
            index_document(conn, p, doc_path=key)
        if "fts_documents" in tables:
            conn.execute("DELETE FROM fts_documents WHERE filepath IN (?, ?)", keys)
            if p.is_file():
                title = conn.execute(
                    "SELECT title FROM documents WHERE doc_path = ?", (key,)
                ).fetchone()[0]
                body = "".join(
                    r[0]
                    for r in conn.execute(
                        "SELECT content FROM fts_index WHERE doc_path = ? ORDER BY rowid",
                        (key,),
                    )
                )
                conn.execute(
                    "INSERT INTO fts_documents (document_id, title, body, tags, filepath) VALUES (?, ?, ?, ?, ?)",
                    (p.stem, title, body, "wiki,rag", key),
                )
    except BaseException:
        # Rollback-then-re-raise: nothing is swallowed.
        conn.execute(f"ROLLBACK TO SAVEPOINT {_REINDEX_FILE_SAVEPOINT}")
        conn.execute(f"RELEASE SAVEPOINT {_REINDEX_FILE_SAVEPOINT}")
        if own_txn:
            conn.rollback()
        raise
    conn.execute(f"RELEASE SAVEPOINT {_REINDEX_FILE_SAVEPOINT}")
    conn.commit()
    return True


reindex_file = reindex_drifted_file
reindex_document = reindex_drifted_file


def reindex_drifted_files(
    conn: sqlite3.Connection, target_paths: list[str | Path] | None = None
) -> list[str]:
    """Scans and re-indexes only drifted files whose physical hash differs from documents.sha256."""
    paths = target_paths if target_paths is not None else find_drifted_documents(conn)
    return [
        str(fp)
        for fp in paths
        if check_document_drift(conn, fp) and reindex_drifted_file(conn, fp)
    ]


DEFAULT_REINDEX_BUSY_TIMEOUT_S: float = 5.0


class BackgroundReindexError(RuntimeError):
    """The background re-index failed; the index is still desynchronized. __cause__ is the worker's error."""


class BackgroundReindexThread(threading.Thread):
    """Daemon thread running one hash-drift re-index; its outcome is delivered to the caller.

    join() behaves like threading.Thread.join() and, once the worker has finished with an error,
    raises BackgroundReindexError chained to that error. result() waits and returns the list of
    re-indexed paths, or raises (BackgroundReindexError on failure, TimeoutError if still running).
    .error exposes the worker's exception (None while running or on success); .recovered is True
    when a corrupt database file was regenerated from source markdown before the re-index.
    """

    def __init__(
        self,
        db_path: Path,
        targets: list[str | Path] | None,
        busy_timeout: float,
        source_dirs: list[str | Path] | tuple[str | Path, ...] | None,
    ) -> None:
        super().__init__(name="FTS5-Reindex-Worker", daemon=True)
        self.db_path = db_path
        self.targets = targets
        self.busy_timeout = busy_timeout
        self.source_dirs = source_dirs
        self.recovered = False
        self._reindexed: list[str] | None = None
        self._error: BaseException | None = None

    def run(self) -> None:
        try:
            self._reindexed = self._reindex_with_corruption_recovery()
        except BaseException as exc:  # transported, not handled: join()/result() re-raise it to the caller
            self._error = exc

    @property
    def error(self) -> BaseException | None:
        return self._error

    def join(self, timeout: float | None = None) -> None:
        super().join(timeout)
        if not self.is_alive() and self._error is not None:
            raise BackgroundReindexError(
                f"Background FTS5 re-index of {self.db_path} failed; index remains desynchronized: "
                f"{type(self._error).__name__}: {self._error}"
            ) from self._error

    def result(self, timeout: float | None = None) -> list[str]:
        self.join(timeout)
        if self.is_alive():
            raise TimeoutError(
                f"Background FTS5 re-index of {self.db_path} still running after {timeout}s"
            )
        if self._reindexed is None:
            raise RuntimeError(f"Background FTS5 re-index of {self.db_path} finished without a result")
        return list(self._reindexed)

    def _reindex_once(self) -> list[str]:
        # mode=rw: never create a missing index file as a side effect of opening it.
        conn = sqlite3.connect(
            f"file:{self.db_path.as_posix()}?mode=rw", uri=True, timeout=self.busy_timeout
        )
        try:
            return reindex_drifted_files(conn, self.targets)
        finally:
            conn.close()

    def _reindex_with_corruption_recovery(self) -> list[str]:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"FTS5 index database not found: {self.db_path}")
        try:
            return self._reindex_once()
        except sqlite3.DatabaseError:
            # SRS-412-06 section 8 'Database File Corruption': only a failed binary header check means
            # corruption. Locks, missing tables or malformed pages behind a valid header propagate.
            if verify_database_binary_header(self.db_path):
                raise
            # Regenerate from source markdown; a failing recovery propagates (chained to the DatabaseError).
            recover_corrupt_index(self.db_path, self.source_dirs)
            self.recovered = True
        return self._reindex_once()


def trigger_background_reindex(
    db_path: str | Path,
    target_files: list[str | Path] | str | Path | None = None,
    *,
    busy_timeout: float = DEFAULT_REINDEX_BUSY_TIMEOUT_S,
    source_dirs: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> BackgroundReindexThread:
    """Non-blocking background re-index for FTS5 Index Desynchronization (SRS-412-06 section 8).

    Starts and returns a BackgroundReindexThread that re-indexes the drifted files among
    target_files (default: every drifted document in documents). The caller learns the outcome from
    join()/result(): a failure is raised there as BackgroundReindexError, never only logged.
    busy_timeout is the SQLite busy wait in seconds. If the database fails the binary header check,
    it is regenerated via recover_corrupt_index(db_path, source_dirs) and the re-index is retried.
    """
    targets = [target_files] if isinstance(target_files, (str, Path)) else target_files
    t = BackgroundReindexThread(Path(db_path).resolve(), targets, busy_timeout, source_dirs)
    t.start()
    return t


trigger_hash_drift_reindex = trigger_background_reindex
recover_fts5_desynchronization = trigger_background_reindex
recover_fts5_index_desynchronization = trigger_background_reindex
handle_fts5_index_desynchronization = trigger_background_reindex

# [MC-RAG-14] SRS-412-06-FR-008
# SPEC: Satisfy SRS-412-06-FR-008: The FTS5 indexer shall support both incremental document updates
# SPEC: and atomic full re-indexing. Atomic full re-index builds a temporary database and
# SPEC: os.replace()s it over knowledge_index.db so readers never observe a half-built index.
SRS_INDEX_TABLES: frozenset[str] = frozenset({"documents", "fts_index", "sqlite_sequence"})
FTS5_SHADOW_SUFFIXES: tuple[str, ...] = ("_data", "_idx", "_content", "_docsize", "_config")


class FullReindexError(RuntimeError):
    """Raised when an atomic full re-index cannot safely replace the target index."""


class IncrementalUpdateResult(NamedTuple):
    """Outcome of incremental_update: which documents were rewritten, skipped or removed."""

    updated: list[str]
    unchanged: list[str]
    removed: list[str]


class FullReindexResult(NamedTuple):
    """Outcome of full_reindex: the replaced index path and what it now holds."""

    db_path: str
    document_count: int
    section_count: int


def _canonical_doc_path(file_path: str | Path) -> str:
    return str(Path(file_path).resolve())


def incremental_update(
    conn: sqlite3.Connection, file_paths: Iterable[str | Path]
) -> IncrementalUpdateResult:
    """Incremental update (FR-008): rewrites only documents whose SHA-256 differs from documents.sha256.

    Change detection is by content hash, not mtime. Files that no longer exist are removed from
    documents and fts_index. All changes happen in one transaction; any error rolls it back and
    propagates, leaving the index as it was.
    """
    ensure_schema(conn)
    updated: list[str] = []
    unchanged: list[str] = []
    removed: list[str] = []
    with conn:
        conn.execute("BEGIN")
        for fp in file_paths:
            path = Path(fp).resolve()
            doc_key = str(path)
            row = conn.execute(
                "SELECT sha256 FROM documents WHERE doc_path = ?", (doc_key,)
            ).fetchone()
            if not path.is_file():
                if row is not None:
                    conn.execute("DELETE FROM fts_index WHERE doc_path = ?", (doc_key,))
                    conn.execute("DELETE FROM documents WHERE doc_path = ?", (doc_key,))
                    removed.append(doc_key)
                    continue
                raise FileNotFoundError(f"Document to index not found: {path}")
            sha = compute_file_sha256(path)
            if row is not None and row[0] == sha:
                unchanged.append(doc_key)
                continue
            index_document(conn, path, doc_path=doc_key)
            updated.append(doc_key)
    return IncrementalUpdateResult(updated, unchanged, removed)


def collect_markdown_files(sources: Iterable[str | Path]) -> list[Path]:
    """Expands files and directories (recursive *.md) into a sorted, de-duplicated file list."""
    found: dict[str, Path] = {}
    for src in sources:
        p = Path(src).resolve()
        if p.is_dir():
            for f in p.rglob("*.md"):
                if f.is_file():
                    found[str(f)] = f
        elif p.is_file():
            found[str(p)] = p
        else:
            raise FileNotFoundError(f"Re-index source not found: {p}")
    return [found[k] for k in sorted(found)]


def index_corpus(conn: sqlite3.Connection, roots: Iterable[str | Path]) -> int:
    """Indexes every Markdown document under roots (FR-002) in one transaction; returns the document count."""
    files = collect_markdown_files(roots)
    with conn:
        conn.execute("BEGIN")
        for f in files:
            index_document(conn, f, doc_path=str(f))
    return len(files)


def _foreign_tables(db_path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        names = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
    finally:
        conn.close()
    shadow = {f"fts_index{s}" for s in FTS5_SHADOW_SUFFIXES}
    return sorted(n for n in names if n not in SRS_INDEX_TABLES and n not in shadow)


def full_reindex(
    db_path: str | Path,
    sources: Iterable[str | Path],
    *,
    allow_drop_foreign_tables: bool = False,
) -> FullReindexResult:
    """Atomic full re-index (FR-008).

    Builds a complete index in a temporary database in the same directory, verifies it, and only
    then os.replace()s it over db_path. If anything fails before the replace (unreadable or
    non-UTF-8 document, sqlite error, empty corpus, integrity failure) the temporary file is deleted,
    the exception propagates and db_path is untouched, so readers never observe a half-built index.

    The existing index is refused (FullReindexError) if it holds tables outside the SRS section 7
    schema, e.g. the live fts_documents table, unless allow_drop_foreign_tables=True, because the
    replace would discard them.
    """
    target = Path(db_path).resolve()
    if target.exists():
        foreign = _foreign_tables(target)
        if foreign and not allow_drop_foreign_tables:
            raise FullReindexError(
                f"{target} holds non-SRS tables {foreign}; full re-index would drop them"
            )
        wal = target.with_name(target.name + "-wal")
        if wal.is_file() and wal.stat().st_size > 0:
            raise FullReindexError(f"{wal} is non-empty; the index is open or was not checkpointed")
    files = collect_markdown_files(sources)
    if not files:
        raise FullReindexError(
            "No Markdown documents found; refusing to replace the index with an empty one"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".reindex.tmp", dir=target.parent
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    replaced = False
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            ensure_schema(conn)
            section_count = 0
            with conn:
                conn.execute("BEGIN")
                for f in files:
                    section_count += index_document(conn, f, doc_path=str(f))
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise FullReindexError(f"Temporary index failed integrity_check: {check}")
            conn.execute("INSERT INTO fts_index(fts_index) VALUES ('integrity-check')")
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        finally:
            conn.close()
        os.replace(tmp_path, target)
        replaced = True
    finally:
        if not replaced and tmp_path.exists():
            tmp_path.unlink()
    return FullReindexResult(str(target), int(doc_count), int(section_count))


incremental_update_documents = incremental_update
atomic_full_reindex = full_reindex
rebuild_index_atomically = full_reindex

# [MC-RAG-15] FAILURE:Database File Corruption
# SPEC: Append after the previous chunk without editing earlier lines. Implement recovery for 'Database
# SPEC: File Corruption': Binary header check validates `b"SQLite format 3\x00"`; corrupt file is
# SPEC: regenerated from source markdown. Rebuild through the FR-008 atomic full re-index path.
from cochem.knowledge.corpus import discover_corpus_roots, validate_corpus_root

SQLITE_MAGIC_HEADER: bytes = b"SQLite format 3\x00"
SQLITE_HEADER_MAGIC: bytes = SQLITE_MAGIC_HEADER
SQLITE_HEADER_LENGTH: int = len(SQLITE_MAGIC_HEADER)  # 16


class CorruptIndexRecoveryError(RuntimeError):
    """Raised when a regenerated index does not pass the binary header check."""


def read_sqlite_header(db_path: str | Path) -> bytes:
    """Returns the first 16 bytes physically stored in the file (fewer if it is shorter)."""
    with open(Path(db_path), "rb") as fh:
        return fh.read(SQLITE_HEADER_LENGTH)


def verify_database_binary_header(db_path: str | Path) -> bool:
    """Binary header check: True only if the file begins with b"SQLite format 3\\x00".

    Empty or truncated files are invalid; a missing file raises FileNotFoundError.
    """
    return read_sqlite_header(db_path) == SQLITE_MAGIC_HEADER


def recover_corrupt_index(
    db_path: str | Path,
    source_dirs: list[str | Path] | tuple[str | Path, ...] | None = None,
    *,
    sources_base_dir: str | Path | None = None,
) -> bool:
    """Recovery for 'Database File Corruption'. Returns True if db_path was regenerated.

    If db_path exists with a valid header it is left untouched (False). Otherwise the index is
    regenerated from the source markdown under source_dirs (default: wiki/ beside db_path and
    .sources/ under sources_base_dir, which defaults to db_path's directory; the live layout keeps
    .sources/ one level above, i.e. sources_base_dir=db_path.parent.parent) through full_reindex (FR-008).
    full_reindex cannot open a corrupt target, so it builds into a fresh staging path in the same
    directory; the staging file is then os.replace()d over db_path, so readers see either the
    corrupt file or a complete index. Every error propagates (missing root, empty corpus, bad UTF-8,
    integrity failure, file in use) and db_path is then left as it was.
    """
    target = Path(db_path).resolve()
    if target.is_file() and verify_database_binary_header(target):
        return False
    roots = (
        [validate_corpus_root(r) for r in source_dirs]
        if source_dirs is not None
        else list(discover_corpus_roots(target.parent, sources_base_dir=sources_base_dir))
    )
    staging = target.with_name(f"{target.name}.recover-{uuid.uuid4().hex}.db")
    try:
        full_reindex(staging, roots)
        if not verify_database_binary_header(staging):
            raise CorruptIndexRecoveryError(f"Regenerated index {staging} lacks the SQLite header")
        # Journals left by the corrupt file must not be replayed against the new database.
        for suffix in ("-wal", "-shm", "-journal"):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return True


check_sqlite_header = verify_database_binary_header
recover_database_file_corruption = recover_corrupt_index
handle_database_file_corruption = recover_corrupt_index


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for knowledge base indexing operations."""
    parser = argparse.ArgumentParser(
        description="Dual Wiki RAG Knowledge Indexer (SRS-412-06-FR-002, FR-008)"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_INDEX_DB_NAME),
        help=f"Path to SQLite knowledge index database (default: {DEFAULT_INDEX_DB_NAME})",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Perform atomic full re-index of the corpus",
    )
    parser.add_argument(
        "--allow-drop-foreign-tables",
        "--force",
        dest="allow_drop_foreign_tables",
        action="store_true",
        help="Allow dropping foreign tables (e.g. legacy fts_documents) during full re-index",
    )
    parser.add_argument(
        "--check-drift",
        action="store_true",
        help="Check and list drifted documents in the index",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Check database binary header and recover from source markdown if corrupt",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Corpus root directories or files (default: dynamically discovered .sources and wiki)",
    )

    args = parser.parse_args(argv)
    db_path = args.db.resolve()

    if args.recover:
        print(f"Checking and recovering database at {db_path}...")
        source_dirs = args.roots if args.roots else None
        sources_base = Path.cwd().parent if (Path.cwd().parent / ".sources").is_dir() else None
        recovered = recover_corrupt_index(
            db_path, source_dirs=source_dirs, sources_base_dir=sources_base
        )
        if recovered:
            print(f"Database at {db_path} was regenerated successfully.")
        else:
            print(f"Database at {db_path} header is valid; no recovery needed.")
        return 0

    if args.check_drift:
        if not db_path.is_file():
            print(f"Database file not found: {db_path}", file=sys.stderr)
            return 1
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            drifted = find_drifted_documents(conn)
            if drifted:
                print(f"Found {len(drifted)} drifted document(s):")
                for doc in drifted:
                    print(f"  - {doc}")
                return 1
            print("All indexed documents are in sync.")
            return 0
        finally:
            conn.close()

    if args.reindex:
        if args.roots:
            roots = args.roots
        else:
            try:
                corpus_roots = discover_corpus_roots(
                    Path.cwd(), sources_base_dir=Path.cwd().parent
                )
                roots = [corpus_roots.sources, corpus_roots.wiki]
            except Exception:
                candidate_sources = (
                    Path.cwd().parent / ".sources"
                    if (Path.cwd().parent / ".sources").is_dir()
                    else Path(".sources")
                )
                roots = [candidate_sources, Path("wiki")]
        print(f"Performing atomic full re-index of {roots} into {db_path}...")
        result = full_reindex(
            db_path,
            roots,
            allow_drop_foreign_tables=args.allow_drop_foreign_tables,
        )
        print(
            f"Atomic full re-index complete: {result.document_count} documents, "
            f"{result.section_count} sections indexed into {result.db_path}"
        )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

`
