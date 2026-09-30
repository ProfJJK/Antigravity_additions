import json
from pathlib import Path

for f in sorted(Path('wiki/wbs/leaf_nodes').glob('MC-RAG-*.json')):
    d = json.loads(f.read_text(encoding='utf-8'))
    print(f"{d['task_id']:10} | {d.get('title'):55} | {d.get('target_file'):35} | {d.get('chunk_start')}-{d.get('chunk_end')}")
