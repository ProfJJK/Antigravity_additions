import os
import glob

dir_path = r"D:\__CoChem\__agentic\.prompts\.SRS\CoChem-BASE\.prompts"
files = glob.glob(os.path.join(dir_path, "Doc10_*.md"))

for file in files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "Method Matrix rules" not in content:
        # insert it after the last constraint or at the end of the Constraints section
        lines = content.split('\n')
        new_lines = []
        in_constraints = False
        for line in lines:
            if line.startswith("## Instructions"):
                # Append constraint right before Instructions
                new_lines.append("  - Ensure complete adherence to the Method Matrix rules for all execution pathways.")
                new_lines.append("")
                in_constraints = False
            new_lines.append(line)
        
        content = '\n'.join(new_lines)

    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)
        
print("Appended Method Matrix rules.")
