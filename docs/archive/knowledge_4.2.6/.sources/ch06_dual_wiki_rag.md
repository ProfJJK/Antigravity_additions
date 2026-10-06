# Chapter 6: Dual Wiki RAG Database & FastMCP Retrieval (ch06)
**Permanent Knowledge Base, SQLite FTS5 Indexing, and Sub-Millisecond Search**

- **Document ID**: SRS-412-06
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 4: Task Matrix Blackboard](ch04_task_matrix_blackboard.md) | [Chapter 7: Domain-Specific Pipelines (DSPs)](ch07_dsp_domain_pipelines.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Dual Wiki RAG Database & FastMCP Retrieval Service**, providing permanent, indexed knowledge search across all pipeline specifications and architecture artifacts.

---

## 2. Boundary
The Dual Wiki boundary encompasses the static `.sources/` knowledge archive, the live `wiki/` documentation repository, the SQLite FTS5 database (`knowledge_index.db`), and the `cochem-knowledge-mcp` tool server.

---

## 3. Definitions
- **Dual Wiki Architecture**: Maintaining raw immutable source captures in `.sources/` while exposing structured, cross-linked Markdown in `wiki/`.
- **SQLite FTS5**: Full-Text Search extension providing BM25 relevance-ranked document indexing and sub-millisecond retrieval.
- **FastMCP**: High-performance Model Context Protocol server exposing search and document reading tools to agents.

---

## 4. Functional Requirements

- **SRS-412-06-FR-001**: The system shall maintain permanent reference knowledge in `.sources/` and live ratified specifications in `wiki/`.
- **SRS-412-06-FR-002**: All knowledge documents shall be indexed in the SQLite FTS5 database (`knowledge_index.db`) using BM25 relevance scoring.
- **SRS-412-06-FR-003**: The knowledge retrieval service shall provide sub-millisecond (<5ms) text searches via `cochem-knowledge-mcp`.
- **SRS-412-06-FR-004**: All relative Markdown links across wiki chapters shall be programmatically validated with 0 broken cross-references.
- **SRS-412-06-FR-005**: Every SRS and wiki chapter document shall strictly enforce a maximum length limit of <= 400 lines.
- **SRS-412-06-FR-006**: The knowledge base structure and document catalog shall be synchronized with `v4.1.2_manifest.json`.
- **SRS-412-06-FR-007**: Scientific notation, quantum formulas, and Greek characters (`Ψ(r)`, `ΔG°`, `kJ·mol⁻¹`) shall be preserved losslessly in UTF-8.
- **SRS-412-06-FR-008**: The FTS5 indexer shall support both incremental document updates and atomic full re-indexing.

---

## 5. Non-Functional Requirements
- **NFR-RAG-01**: Average search query execution time over 10,000 indexed sections shall be less than 5 milliseconds.
- **NFR-RAG-02**: FTS5 index size shall not exceed 4.0x the raw Markdown corpus size.
- **NFR-RAG-03**: Memory consumption of the `cochem-knowledge-mcp` daemon shall remain below 256 MB.

---

## 6. Interfaces & FTS5 Query Implementation

```python
import sqlite3
from typing import Any

def query_knowledge_index(db_path: str, search_query: str, limit: int = 5) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    sql = """
        SELECT doc_path, section_title, snippet(fts_index, 2, '<b>', '</b>', '...', 15) as snippet, bm25(fts_index) as rank
        FROM fts_index
        WHERE fts_index MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    cur.execute(sql, (search_query, limit))
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results
```

---

## 7. Data Models
```sql
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    line_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    updated_at INTEGER DEFAULT (strftime('%s', 'now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
    doc_path UNINDEXED,
    section_title,
    content,
    tokenize = 'porter unicode61'
);
```

---

## 8. Failure Modes & Recovery
- **FTS5 Index Desynchronization**: If document hash differs from indexed hash, the indexer triggers a non-blocking background re-index of the file.
- **Database File Corruption**: Binary header check validates `b"SQLite format 3\x00"`; corrupt file is regenerated from source markdown.

---

## 9. Test Obligations & Verification
- Test asserts `00_skeleton.md` indexes all chapters.
- Test verifies all generated chapters are strictly <= 400 lines.
- Test asserts relative links in markdown chapters resolve to physically existing files.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-06-FR-004 | V4.1.2 Master Architecture Plan §6 | `test_f05_srs_detects_broken_relative_links_between_chapters` |
| SRS-412-06-FR-005 | Rule 18 & Execution Manifest | `test_f05_srs_chapters_strictly_less_than_400_lines` |
| SRS-412-06-FR-007 | System Invariants | `test_f05_srs_handles_unicode_and_scientific_formula_symbols` |
