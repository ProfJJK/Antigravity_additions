"""Fixed future worker/grandchild fixture; no provider or network operations.

Not run during preparation. Requires reviewed protected path and two nonce
gates from its controller. At most 1,024 zero-byte files in its exact fresh cwd.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import time

ROOT = Path(r'C:\Program Files\CoChem\OracleNativeAcceptance4.2.7-windows-20261008-r3-v1')
BASE = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
SCRATCH = Path(r'R:\CoChem427-windows-20261007\slot1\.cochem-scratch\oracle-native-acceptance-20261008-r3-v1')


def gate(nonce, action):
    return sys.stdin.buffer.readline(80) == (nonce + ':' + action + '\n').encode('ascii')


def main():
    if (os.name != 'nt' or Path(__file__).resolve() != ROOT / 'oracle-rogue-fixture-r3-v1.py'
            or Path(sys.executable).resolve() != BASE.resolve() or Path.cwd() != SCRATCH
            or len(sys.argv) not in (2, 3) or re.fullmatch('[0-9a-f]{32}', sys.argv[1]) is None):
        return 2
    nonce = sys.argv[1]
    if len(sys.argv) == 3:
        if sys.argv[2] != '--grandchild' or not gate(nonce, 'STORM'):
            return 2
        for index in range(1024):
            with (SCRATCH / ('event-' + str(index))).open('xb'):
                pass
        time.sleep(60)
        return 3  # No Oracle termination is an acceptance failure.
    if not gate(nonce, 'SPAWN'):
        return 2
    child = subprocess.Popen([str(BASE), '-I', '-B', str(Path(__file__)), nonce, '--grandchild'],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=str(SCRATCH), creationflags=0x08000000, close_fds=True)
    print(json.dumps({'nonce': nonce, 'grandchild_pid': child.pid}), flush=True)
    if not gate(nonce, 'STORM'):
        return 2
    child.stdin.write((nonce + ':STORM\n').encode('ascii'))
    child.stdin.flush()
    return child.wait(timeout=75)


if __name__ == '__main__':
    raise SystemExit(main())
