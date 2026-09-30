import sqlite3
import json

db_path = r"D:\__CoChem\__agentic\v4.1.2\knowledge_index.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print("Tables:", tables)

for table in tables:
    tname = table[0]
    print(f"\n--- Table: {tname} ---")
    cur.execute(f"PRAGMA table_info({tname})")
    print("Schema:", cur.fetchall())
    
    if tname == 'microtasks':
        cur.execute("SELECT * FROM microtasks WHERE task_id = 'MC-SRE-22'")
        print("MC-SRE-22:", cur.fetchall())
    
    if tname == 'srs_chapters':
        cur.execute("SELECT * FROM srs_chapters WHERE chapter_id = 'srs_ch08_watchdog_sre'")
        print("srs_ch08_watchdog_sre:", cur.fetchall())
