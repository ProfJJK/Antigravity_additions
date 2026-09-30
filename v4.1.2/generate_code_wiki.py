import os
import glob
from pathlib import Path
import subprocess

ROOT = Path("D:/__CoChem/__agentic/v4.1.2")
CODE_WIKI = ROOT / "wiki" / "code"
CODE_WIKI.mkdir(parents=True, exist_ok=True)

py_files = glob.glob(str(ROOT / "src" / "cochem" / "**" / "*.py"), recursive=True)
count = 0
for py_file in py_files:
    py_path = Path(py_file)
    rel_path = py_path.relative_to(ROOT)
    
    md_name = f"{rel_path.parent.name}_{rel_path.name}.md"
    md_path = CODE_WIKI / md_name
    
    content = f"# {rel_path.as_posix()}\n\n"
    content += "`python\n"
    try:
        content += py_path.read_text(encoding="utf-8")
    except Exception:
        content += py_path.read_text(encoding="latin-1")
    content += "\n`\n"
    
    md_path.write_text(content, encoding="utf-8")
    count += 1

print(f"Generated {count} markdown files for code. Running full re-index...")
# We use the python executable to run indexer.py, pointing it to the wiki dir
subprocess.run(["python", str(ROOT / "src" / "cochem" / "knowledge" / "indexer.py"), "--reindex", str(ROOT / "wiki")], check=True)
print("Code successfully entered into the Wiki RAG.")
