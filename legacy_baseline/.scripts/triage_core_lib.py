import os
import sys
from pathlib import Path

def get_triage_root() -> Path:
    """
    Dynamically resolves the root directory of the triage system.
    Can be overridden via COCHEM_TRIAGE_ROOT environment variable.
    """
    env_root = os.environ.get("COCHEM_TRIAGE_ROOT")
    if env_root:
        root_path = Path(env_root).resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"Environment COCHEM_TRIAGE_ROOT={env_root} does not exist.")
        return root_path
    
    # Default to assuming this script is in <root>/.scripts/
    return Path(__file__).resolve().parent.parent

def get_ledger_path() -> Path:
    """
    Returns the path to the append-only cryptographic ledger.
    """
    return get_triage_root() / ".logs" / "triage_audit_ledger.jsonl"

def get_state_path(state_name: str) -> Path:
    """
    Returns the path for a given JSON state file.
    """
    return get_triage_root() / ".state" / f"{state_name}.json"

def ensure_directories():
    """
    Ensures that all essential dropzones and configuration directories exist.
    """
    root = get_triage_root()
    dirs = [
        root / ".logs",
        root / ".state",
        root / ".config",
        root / "dropzones" / "inbox_improve",
        root / "dropzones" / "inbox_srs",
        root / "dropzones" / "inbox_dating",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    ensure_directories()
    print(f"Triage root successfully resolved to: {get_triage_root()}")
