import os
import re
import json
from collections import defaultdict

reports_dir = r'D:\__CoChem\__agentic\v4.1.2\shortcomings_reports'
script_pattern = re.compile(r'([a-zA-Z0-9_]+\.py)')

chapter_scripts = defaultdict(set)
for root, _, files in os.walk(reports_dir):
    for file in files:
        if file.startswith('ch') and file.endswith('_shortcomings.md'):
            ch_num = int(file[2:4])
            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                content = f.read()
                matches = script_pattern.findall(content)
                # filter out test files
                matches = [m for m in matches if not m.startswith('test_')]
                chapter_scripts[ch_num].update(matches)

# Print cleanly
for ch, scripts in sorted(chapter_scripts.items()):
    print(f"Chapter {ch}: {list(scripts)}")
