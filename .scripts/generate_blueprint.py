import os
import sys
import argparse
from pathlib import Path

def parse_args():
    parser = argparse.ArgumentParser(description="Generate a Two-Pass File Blueprint checklist for a CoChem module.")
    parser.add_argument("--repo-dir", required=True, help="Path to the physical repository directory to scan.")
    parser.add_argument("--output-file", required=True, help="Path to the output Markdown file.")
    return parser.parse_args()

def is_ignored(path, root_dir, is_dir=False):
    ignored_dirs = {'.git', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.venv', 'env', '.agents', '.trash'}
    ignored_exts = {'.pyc', '.pyo', '.pyd'}
    
    if not is_dir:
        # Check extensions and exact matches
        if path.suffix in ignored_exts:
            return True
        if path.name == '.DS_Store':
            return True
    
    # Check parent directories
    rel_parts = path.relative_to(root_dir).parts
    parts_to_check = rel_parts if is_dir else rel_parts[:-1]
    for part in parts_to_check:
        if part in ignored_dirs:
            return True
            
    return False

def determine_tag(path):
    # Basic heuristics for rogue files. 
    # Real logical determination will be handled by the adversarial auditor agent in Pass 2.
    # This just provides initial hints.
    rogue_exts = {'.log', '.tmp', '.bak', '.swp', '.dmp'}
    if path.suffix in rogue_exts or path.name in rogue_exts:
        return "[ROGUE]"
    
    # Example heuristic: loose files in root that aren't standard
    # standard_root_files = {'README.md', 'LICENSE', 'pyproject.toml', 'setup.py', '.gitignore', 'Method_Matrix/Method_Matrix_Hub.md'}
    
    return "[EXISTING]"

def check_missing(existing_files, repo_path):
    # A basic check for standard CoChem architectural files that should exist
    missing = []
    standard_files = ['README.md', 'pyproject.toml', 'LICENSE']
    
    existing_paths = set(existing_files)
    for req in standard_files:
        if (repo_path / req) not in existing_paths:
            missing.append(f"[MISSING] {req}")
    return missing

def main():
    args = parse_args()
    try:
        repo_path = Path(args.repo_dir).resolve()
        out_path = Path(args.output_file).resolve()
    except OSError as e:
        print(f"Error: Invalid path provided: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not repo_path.exists() or not repo_path.is_dir():
        print(f"Error: Repository path does not exist or is not a directory: {repo_path}")
        sys.exit(1)
        
    print(f"Scanning repository: {repo_path}")
    
    # Gather files
    files_by_dir = {}
    all_files = []
    
    def walk_onerror(exc):
        print(f"Warning: Could not access directory: {exc.filename} ({exc})", file=sys.stderr)

    for root, dirs, files in os.walk(repo_path, onerror=walk_onerror):
        root_path = Path(root)
        
        # Modify dirs in-place to prevent walking ignored directories
        dirs[:] = [d for d in dirs if not is_ignored(root_path / d, repo_path, is_dir=True)]
        
        for file in files:
            file_path = root_path / file
            if not is_ignored(file_path, repo_path, is_dir=False):
                rel_dir = root_path.relative_to(repo_path)
                rel_dir_str = str(rel_dir).replace('\\', '/')
                    
                if rel_dir_str not in files_by_dir:
                    files_by_dir[rel_dir_str] = []
                    
                all_files.append(file_path)
                files_by_dir[rel_dir_str].append(file_path)
                
    # Check for missing
    missing_files = check_missing(all_files, repo_path)
    
    # Generate Markdown
    lines = []
    lines.append(f"# File Blueprint: {repo_path.name}\n")
    
    # Write Missing First
    if missing_files:
        lines.append("## Missing Architectural Files")
        for m in missing_files:
            lines.append(f"- [ ] {m}")
        lines.append("")
        
    # Write Existing/Rogue Grouped by Directory
    if "." in files_by_dir:
        lines.append(f"## Directory: `Root`")
        sorted_files = sorted(files_by_dir["."], key=lambda p: p.name)
        for f in sorted_files:
            tag = determine_tag(f)
            rel_file_path = str(f.relative_to(repo_path)).replace('\\', '/')
            lines.append(f"- [ ] {tag} {rel_file_path}")
        lines.append("")

    for directory in sorted(files_by_dir.keys()):
        if directory == ".":
            continue
        lines.append(f"## Directory: `{directory}`")
        # Sort files alphabetically
        sorted_files = sorted(files_by_dir[directory], key=lambda p: p.name)
        
        for f in sorted_files:
            tag = determine_tag(f)
            rel_file_path = str(f.relative_to(repo_path)).replace('\\', '/')
            lines.append(f"- [ ] {tag} {rel_file_path}")
            
        lines.append("")
        
    # Write Output
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
    except (PermissionError, IsADirectoryError, OSError) as e:
        print(f"Error: Failed to write output file {out_path}: {e}", file=sys.stderr)
        sys.exit(1)
        
    print(f"Blueprint generated successfully at: {out_path}")
    print(f"Total files mapped: {len(all_files)}")

if __name__ == "__main__":
    main()

