import json
import os
from pathlib import Path
import sys

# Add scripts directory to path to use core lib
sys.path.insert(0, str(Path(__file__).resolve().parent))
from triage_core_lib import get_triage_root
from concurrency_guard import safe_json_read, safe_json_write, append_to_ledger

class TriageDashboardTUI:
    """
    1-Click Human Approval TUI.
    Reads from the drafts queues and allows the user to approve or reject them.
    """
    def __init__(self):
        self.root = get_triage_root()
        self.drafts_file = self.root / ".state" / "dating_drafts.json"
        
    def render(self):
        print("="*50)
        print(" TRIAGE DASHBOARD - HUMAN APPROVAL QUEUE ")
        print("="*50)
        
        drafts = safe_json_read(str(self.drafts_file))
        if not drafts or not isinstance(drafts, list):
            print("No pending drafts to review.")
            return

        print(f"Found {len(drafts)} pending drafts.\n")
        
        approved = []
        for idx, draft in enumerate(drafts):
            print(f"[{idx+1}] Partner: {draft.get('partner')}")
            print(f"Draft: {draft.get('draft_text')}")
            
            # For automation testing, we can inject responses, but normally we use input()
            # We'll default to an environment variable to allow automated testing without hanging
            if os.environ.get("CI_AUTO_APPROVE") == "1":
                choice = "y"
            else:
                try:
                    choice = input("Approve and send? (y/n/skip): ").strip().lower()
                except EOFError:
                    choice = "skip"
                    
            if choice == 'y':
                print(f"-> Approved message to {draft.get('partner')}")
                # In real system, this dispatches to the sender sidecar
                approved.append(idx)
                append_to_ledger(str(self.root / ".logs" / "triage_audit_ledger.jsonl"), "APPROVE_DRAFT", draft)
            elif choice == 'n':
                print("-> Rejected.")
                approved.append(idx)
                append_to_ledger(str(self.root / ".logs" / "triage_audit_ledger.jsonl"), "REJECT_DRAFT", draft)
            else:
                print("-> Skipped.")
                
        # Remove processed items
        remaining = [d for i, d in enumerate(drafts) if i not in approved]
        safe_json_write(str(self.drafts_file), remaining)

if __name__ == "__main__":
    tui = TriageDashboardTUI()
    tui.render()
