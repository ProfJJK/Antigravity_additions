import sqlite3

db_path = r"D:\__CoChem\__agentic\v4.1.2\knowledge_index.db"
conn = sqlite3.connect(db_path)
c = conn.cursor()

tables = c.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
for t in tables:
    table_name = t[0]
    print(f"\nTABLE: {table_name}")
    try:
        rows = c.execute(f"SELECT * FROM {table_name}").fetchall()
        for row in rows:
            row_str = str(row)
            if 'MC-SRE-13' in row_str or 'srs_ch08_watchdog_sre' in row_str:
                print(row)
    except Exception as e:
        print(f"Error querying {table_name}: {e}")

conn.close()
