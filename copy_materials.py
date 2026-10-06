import shutil
from pathlib import Path

src_dir1 = Path(r"D:\__CoChem\__agentic\v4.2.0")
src_dir2 = Path(r"C:\Users\ansac\.gemini\antigravity\brain\22bbbd71-c007-41f6-b153-7668d2d0cc9c")
repo_dir = Path(r"D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions")

# We want to grab the critical audit files and SRS materials that were generated in the current agent session
files_to_add = [
    "Astra_OOB_Audit_Real.md",
    "Opus_OOB_Audit_Real.md",
    "Opus_Astra_Cross_Audit.md",
    "Astra_Oracle_Final_Blueprint.md",
    "Opus_Oracle_Audit.md",
    "Oracle_Summit_Findings.md",
    "Global_Ecosystem_Refactor_Plan.md",
    "Global_Ecosystem_Refactor_Plan_v2.md",
    "Phase3_Audit_Report.md",
    "Phase_2_DAG_Deployment_Summary.md",
    "Phase22_Audit_Report.md",
    "GPT_6_Astra_Rules_Audit.md",
    "VICTORY_AUDIT_REPORT.md",
    "FABLE_REMEDIATION_REPORT.md",
    "FABLE_DEPLOYMENT_COMPLETION.md"
]

for fname in files_to_add:
    # check dir1
    if (src_dir1 / fname).exists():
        shutil.copy2(src_dir1 / fname, repo_dir / fname)
        print(f"Copied {fname} from v4.2.0")
    elif (src_dir2 / fname).exists():
        shutil.copy2(src_dir2 / fname, repo_dir / fname)
        print(f"Copied {fname} from artifacts")
    else:
        print(f"Missing {fname}")

