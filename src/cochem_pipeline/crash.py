"""Private, bounded PEP 657 crash metadata for the independent supervisor."""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import time
import traceback

import psutil

MAX_ENVELOPE_BYTES = 65536
_CATEGORIES = frozenset({'code', 'compatibility', 'configuration', 'auth', 'resource',
    'quota', 'busy', 'context', 'provider', 'backlog', 'timeout', 'protocol'})


def crash_envelope(error: BaseException, category='code') -> dict:
    category = category if category in _CATEGORIES else 'configuration'
    exception_type = re.sub(r'[^A-Za-z0-9_]', '_', type(error).__name__)[:128]
    summary = traceback.TracebackException(type(error), error, error.__traceback__,
        limit=-32, lookup_lines=False, capture_locals=False)
    frames = []
    for frame in summary.stack:
        # Host paths, source text, locals and exception messages may contain
        # private user data. Only a basename and physical PEP 657 coordinates
        # cross into a diagnostic envelope.
        filename = PureWindowsPath(frame.filename).name if '\\' in frame.filename else Path(frame.filename).name
        frames.append({'filename': filename[:256], 'function': frame.name[:128],
            'lineno': frame.lineno, 'end_lineno': frame.end_lineno,
            'colno': frame.colno, 'end_colno': frame.end_colno})
    return {'schema': 1, 'timestamp': time.time(), 'pid': os.getpid(),
        'process_started_at': psutil.Process().create_time(), 'category': category,
        'exception_type': exception_type,
        'diagnostic': f'{exception_type} in controller ({category}); private values omitted',
        'frames': frames}


def record_crash(private_root: Path, error: BaseException, category='code') -> Path:
    """Caller must already have validated the SYSTEM-only private directory."""
    root = Path(private_root)
    payload = json.dumps(crash_envelope(error, category), ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError('Crash metadata exceeds the private evidence bound')
    target = root / 'crash-envelope.json'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix='.crash-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
