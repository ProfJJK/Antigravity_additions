import sqlite3

conn = sqlite3.connect("knowledge_index.db")
c = conn.cursor()
for i in range(1, 13):
    tid = f"MC-SRE-{i:02d}"
    row = c.execute("SELECT document_id, title, filepath FROM fts_documents WHERE document_id = ?", (tid,)).fetchone()
    if row:
        print(f"{tid:10} | {row[1]:50} | {row[2]}")
    else:
        print(f"{tid:10} | NOT FOUND")
conn.close()
