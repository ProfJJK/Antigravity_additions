import os
import msvcrt
import json
import time
import hashlib
from datetime import datetime, timezone
from contextlib import contextmanager

class LedgerError(Exception):
    pass

@contextmanager
def acquire_file_lock(filepath: str, timeout: float = 10.0):
    """
    Acquires an exclusive OS-level lock on a file using msvcrt (Windows).
    Creates the file if it does not exist.
    """
    start_time = time.time()
    fd = None
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    
    while time.time() - start_time < timeout:
        try:
            # Open file for read/write, create if doesn't exist
            fd = os.open(filepath, os.O_RDWR | os.O_CREAT)
            # Acquire exclusive, non-blocking lock
            # We lock the first byte, which is standard for file locking
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            yield fd
            break
        except (IOError, OSError) as e:
            if fd is not None:
                os.close(fd)
                fd = None
            time.sleep(0.1)
    else:
        raise TimeoutError(f"Could not acquire lock on {filepath} within {timeout} seconds.")
    
    if fd is not None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)

def append_to_ledger(ledger_path: str, action: str, data: dict):
    """
    Safely appends a cryptographic transition state to the audit ledger.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Create the payload to hash
    payload_str = json.dumps(data, sort_keys=True)
    content_hash = hashlib.sha256(f"{timestamp}{action}{payload_str}".encode('utf-8')).hexdigest()
    
    entry = {
        "timestamp": timestamp,
        "action": action,
        "hash": content_hash,
        "payload": data
    }
    
    with acquire_file_lock(ledger_path) as fd:
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, (json.dumps(entry) + "\n").encode('utf-8'))

def safe_json_read(filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {}
    with acquire_file_lock(filepath) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        size = os.path.getsize(filepath)
        if size == 0:
            return {}
        content = os.read(fd, size).decode('utf-8')
        if not content.strip():
            return {}
        return json.loads(content)

def safe_json_write(filepath: str, data: dict):
    with acquire_file_lock(filepath) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        # Truncate file before writing new state
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(data, indent=2).encode('utf-8'))
