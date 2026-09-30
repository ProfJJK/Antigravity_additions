import json
from pathlib import Path
for f in sorted(Path('wiki/wbs/leaf_nodes').glob('MC-SRE-*.json')):
    d = json.loads(f.read_text(encoding='utf-8'))
    print(f"{d['task_id']}: {d.get('title')} -> {d.get('target_file')} (chunk {d.get('chunk_start')}-{d.get('chunk_end')})")
