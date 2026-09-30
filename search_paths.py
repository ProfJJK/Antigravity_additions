import os, re
pattern = re.compile(r"['\"]([A-Za-z]:\\[^'\"]*)['\"]")
for root, _, files in os.walk(r'D:\__CoChem\GitHub-Repo\CoChem-SCRIBE'):
    if '__pycache__' in root or '.git' in root: continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as file:
                    for i, line in enumerate(file):
                        if pattern.search(line):
                            print(f'{path}:{i+1}:{line.strip()}')
            except Exception as e:
                pass
