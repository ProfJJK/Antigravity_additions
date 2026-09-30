import sqlite3

db_path = r'D:\__CoChem\__agentic\v4.1.2\knowledge_index.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("--- MC-TDD-06 ---")
cursor.execute("SELECT title, body FROM fts_documents WHERE body LIKE '%MC-TDD-06%' OR title LIKE '%MC-TDD-06%';")
for row in cursor.fetchall():
    print(f"TITLE: {row[0]}\nBODY: {row[1]}\n")
    
print("--- srs_ch05_research_tdd_pivot ---")
cursor.execute("SELECT title, body FROM fts_documents WHERE body LIKE '%srs_ch05_research_tdd_pivot%' OR title LIKE '%srs_ch05_research_tdd_pivot%';")
for row in cursor.fetchall():
    print(f"TITLE: {row[0]}\nBODY: {row[1]}\n")

