import sqlite3
import json

tasks_to_find = [
    "MC-RAG-08", "MC-RAG-09", "MC-RAG-11", "MC-RAG-12", "MC-RAG-16", 
    "MC-RAG-17", "MC-RAG-18", "MC-RAG-19", "MC-RAG-20", "MC-RAG-21", 
    "MC-SRE-01", "MC-SRE-02", "MC-SRE-03", "MC-SRE-04", "MC-SRE-05", 
    "MC-SRE-06", "MC-SRE-07", "MC-SRE-08", "MC-SRE-09", "MC-SRE-10"
]

print("=== knowledge_index.db ===")
try:
    conn = sqlite3.connect("knowledge_index.db")
    tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Tables:", tables)
    for table in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  {table} ({cols}): {count} rows")
    
    # Check if tasks are in any table
    for table in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for t_id in tasks_to_find[:3]:
            # search for t_id in text columns
            for c in cols:
                try:
                    res = conn.execute(f"SELECT * FROM {table} WHERE {c} LIKE ? LIMIT 1", (f"%{t_id}%",)).fetchall()
                    if res:
                        print(f"Found {t_id} in {table}.{c}")
                except Exception:
                    pass
    conn.close()
except Exception as e:
    print("Error:", e)

print("\n=== job_board.db ===")
try:
    conn2 = sqlite3.connect("job_board.db")
    tables2 = [t[0] for t in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Tables:", tables2)
    for table in tables2:
        cols = [c[1] for c in conn2.execute(f"PRAGMA table_info({table})").fetchall()]
        count = conn2.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  {table} ({cols}): {count} rows")
        
        # Check task statuses
        if "task_id" in cols or "id" in cols:
            id_col = "task_id" if "task_id" in cols else "id"
            found = conn2.execute(f"SELECT {id_col}, status FROM {table} WHERE {id_col} IN ({','.join(['?']*len(tasks_to_find))})", tasks_to_find).fetchall()
            print(f"  Matches in {table}: {len(found)} tasks")
            for item in found:
                print(f"    {item[0]}: {item[1]}")
    conn2.close()
except Exception as e:
    print("Error:", e)
