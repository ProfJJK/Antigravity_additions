import os
import glob
import re

dir_path = r"D:\__CoChem\__agentic\.prompts\.SRS\CoChem-BASE\.prompts"
files = glob.glob(os.path.join(dir_path, "Doc10_*.md"))

for file in files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. Remove pytest-mock backdoor
    content = content.replace("`pytest-mock`, ", "")
    
    # 2. Fix Bipartite to Tripartite
    content = content.replace("Bipartite", "Tripartite")
    
    # 3. Add Method Matrix rule if not present
    if "Method Matrix" not in content:
        # Add a constraint
        content = re.sub(
            r"(## Constraints\n(?:- .*\n)*?(?:  \d+\. .*\n)+)",
            r"\1  - Ensure complete adherence to the Method Matrix rules for all execution pathways.\n",
            content
        )
        # renumbering constraints might be complex, so I'll just append it with a hyphen or next number.
        
    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)
        
print("Updated all files with fixes.")
