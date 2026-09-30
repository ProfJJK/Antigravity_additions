import os
import sqlite3
import json
from pathlib import Path

BASE_DIR = Path(r"D:\__CoChem\__agentic\v4.1.2")
DB_PATH = BASE_DIR / "knowledge_index.db"
SRS_DIR = BASE_DIR / "wiki" / "srs"
WBS_DIR = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"

PASSED_CHAPTERS = ["ch03_concurrency_layers.md"]
PASSED_DOMAINS = ["concurrency"]

def ingest_to_rag():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Ingest PASSED SRS Chapters
    for md_file in SRS_DIR.glob("*.md"):
        if md_file.name not in PASSED_CHAPTERS:
            continue
            
        title = md_file.stem
        body = md_file.read_text(encoding="utf-8")
        doc_id = f"srs_{title}"
        filepath = f"wiki/srs/{md_file.name}"
        
        cursor.execute("DELETE FROM fts_documents WHERE document_id = ?", (doc_id,))
        cursor.execute(
            "INSERT INTO fts_documents (document_id, title, body, tags, filepath) VALUES (?, ?, ?, ?, ?)",
            (doc_id, title, body, "srs,architecture", filepath)
        )
        print(f"Indexed SRS: {title}")

    # 2. Ingest PASSED JSON Microtasks
    count = 0
    for json_file in WBS_DIR.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
            
        domain = data.get("domain", "unknown")
        if domain not in PASSED_DOMAINS:
            continue
            
        doc_id = data.get("task_id", json_file.stem)
        title = data.get("title", doc_id)
        body = json.dumps(data, indent=2)
        filepath = f"wiki/wbs/leaf_nodes/{json_file.name}"
        
        cursor.execute("DELETE FROM fts_documents WHERE document_id = ?", (doc_id,))
        cursor.execute(
            "INSERT INTO fts_documents (document_id, title, body, tags, filepath) VALUES (?, ?, ?, ?, ?)",
            (doc_id, title, body, f"wbs,microtask,{domain}", filepath)
        )
        count += 1
        
    conn.commit()
    conn.close()
    print(f"RAG Ingestion Complete. Indexed {count} compliant WBS nodes.")

if __name__ == "__main__":
    ingest_to_rag()
