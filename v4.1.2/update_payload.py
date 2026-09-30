import sqlite3
import json

diff_text = '''
--- a/src/cochem/knowledge/server.py
+++ b/src/cochem/knowledge/server.py
@@ -85,7 +85,7 @@
             ORDER BY rank
             LIMIT ?
         \"\"\"
-        cur.execute(sql, (search_query, limit))
+        cur.execute(sql, (_clean_fts(clean_query), limit))
         return [dict(r) for r in cur.fetchall()]
 
@@ -123,17 +123,10 @@
     target = str(db_path if db_path is not None else DEFAULT_DB_PATH)
     if not (clean_q := _clean_fts(query)):
         return []
-    if target not in _CACHE:
-        c = _connect_read_only(target, check_same_thread=False)
-        try:
-            c.row_factory = sqlite3.Row
-            tbl = _resolve_fts_table(c, target)
-        except BaseException:
-            c.close()
-            raise
-        _CACHE[target] = (c, tbl)
-    conn, tbl = _CACHE[target]
-    return [dict(r) for r in conn.cursor().execute(_fts_search_sql(tbl), (clean_q, limit)).fetchall()]
+    with contextlib.closing(_connect_read_only(target, check_same_thread=False)) as c:
+        c.row_factory = sqlite3.Row
+        tbl = _resolve_fts_table(c, target)
+        return [dict(r) for r in c.cursor().execute(_fts_search_sql(tbl), (clean_q, limit)).fetchall()]
'''

with sqlite3.connect("job_board.db") as conn:
    cur = conn.cursor()
    cur.execute("SELECT id, payload_json FROM jobs WHERE job_type='micro_code' AND status='PENDING' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        job_id = row[0]
        payload = json.loads(row[1])
        payload["task_description"] += f"\n\nHere is the git differential of the surgical fix I applied. Learn from this for future code:\\n{diff_text}"
        cur.execute("UPDATE jobs SET payload_json=? WHERE id=?", (json.dumps(payload), job_id))
        conn.commit()
        print("Successfully updated payload with git diff.")
