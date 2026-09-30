import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from triage_core_lib import get_triage_root
from concurrency_guard import append_to_ledger

class TriageKanbanWatcher:
    """
    Watches dropzones for new proposals or emails and dispatches workflows.
    Maintains the MAX_META_PIVOT ceiling.
    """
    def __init__(self):
        self.root = get_triage_root()
        self.dropzone = self.root / "dropzones" / "inbox_improve"
        self.max_meta_pivot = 3
        
    def poll(self):
        """
        Polls the directory once. Returns list of processed files.
        """
        if not self.dropzone.exists():
            return []
            
        processed = []
        for file in self.dropzone.glob("*.md"):
            # Attempt to process the file
            attempts = 0
            success = False
            while attempts < self.max_meta_pivot and not success:
                try:
                    # In a real system, invoke kanban state machine here
                    print(f"Processing {file.name}, attempt {attempts+1}...")
                    
                    # Simulated processing validation
                    if "corrupt" in file.name:
                        raise ValueError("Simulated pipeline failure")
                        
                    success = True
                except Exception as e:
                    attempts += 1
                    print(f"Error processing {file.name}: {e}")
                    
            if not success:
                print(f"[HARD_ABORT: TRIAGE WALL] File {file.name} failed {self.max_meta_pivot} times.")
                append_to_ledger(str(self.root / ".logs" / "triage_audit_ledger.jsonl"), "HARD_ABORT", {"file": file.name})
                # Move to failed directory or generate autopsy
            else:
                append_to_ledger(str(self.root / ".logs" / "triage_audit_ledger.jsonl"), "KANBAN_PROCESS", {"file": file.name})
                
            processed.append((file.name, success))
        return processed
