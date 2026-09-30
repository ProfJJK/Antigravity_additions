import re
from pathlib import Path

file_path = Path(r"D:\__CoChem\__agentic\v4.1.2\wiki\srs\ch02_quarantine_vm.md")
content = file_path.read_text(encoding="utf-8")

# 1. Update Subtitle
content = re.sub(
    r"\*\*Virtual Machine Isolation, 32GB RAM Ceiling, and Zero-Mock Physics Canaries\*\*", 
    r"**Virtual Machine Isolation, 32GB RAM Ceiling, and Ephemeral Container Execution**", 
    content
)

# 2. Update Definitions
content = re.sub(
    r"- \*\*Physics Canary\*\*.*", 
    r"> [!WARNING] (REMOVED: The 'Physics Canary' requirement was a hallucinated CoChem artifact. This is a general software orchestration pipeline. Physical chemistry concepts have been stripped.)", 
    content
)

# 3. Update FR-005 and FR-006
content = re.sub(
    r"- \*\*SRS-412-02-FR-005\*\*.*",
    r"> [!WARNING] (REMOVED: SRS-412-02-FR-005 hallucinated PySCF and ASE EMT requirements. Stripped to enforce general software standards.)",
    content
)

content = re.sub(
    r"- \*\*SRS-412-02-FR-006\*\*.*",
    r"> [!WARNING] (REMOVED: SRS-412-02-FR-006 hallucinated mendeleev library usage. Stripped to enforce general software standards.)",
    content
)

# 4. Remove Section 6 Python Code that is chemistry-specific
content = re.sub(
    r"## 6\. Interfaces & Authentic Physics Verification.*?## 7\. Failure Modes",
    r"## 6. Interfaces & Container Execution\n\n> [!WARNING] (REMOVED: Previous python pseudo-code was hallucinating ASE and PySCF physical tests. This pipeline executes general software tasks.)\n\n## 7. Failure Modes",
    content,
    flags=re.DOTALL
)

file_path.write_text(content, encoding="utf-8")
print("Chapter 2 successfully scrubbed of chemistry hallucinations.")
