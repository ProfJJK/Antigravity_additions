"""Create new, per-database consistent snapshots without modifying legacy state.

Only the destination is written. Credentials and job payloads are never printed.
The caller must select a private destination and coordinate writers separately
before treating multiple snapshots as a cutover-wide consistency point.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import sqlite3
import time


def snapshot_databases(sources: list[Path], destination: Path, deadline_seconds: float = 60) -> dict:
    if not sources or len(sources) > 32 or not 1 <= deadline_seconds <= 300:
        raise ValueError('Supply 1..32 databases and a bounded 1..300 second per-database deadline')
    if not destination.is_absolute() or destination.exists():
        raise ValueError('Destination must be a new absolute private directory')
    resolved = []
    for source in sources:
        if not source.is_absolute() or not source.is_file() or source.is_symlink():
            raise ValueError('Each source must be an existing absolute regular database')
        resolved.append(source.resolve(strict=True))
    if len(set(resolved)) != len(resolved):
        raise ValueError('Duplicate database source')
    destination.mkdir()
    report = {'schema': 'cochem-legacy-online-snapshots/1',
              'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'consistency': 'Each SQLite backup is consistent individually; no cross-database point-in-time claim',
              'source_open_mode': 'ro', 'production_state_switched': False,
              'repair_budget_reconstructed': False, 'snapshots': []}
    report_path = destination/'snapshot-manifest.json'
    try:
        for index, source in enumerate(resolved):
            target = destination/f'{index+1:02d}-{source.name}'
            start = time.monotonic()
            def progress(status, remaining, total):
                if time.monotonic()-start > deadline_seconds:
                    raise TimeoutError('SQLite online snapshot deadline exceeded')
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=3)) as reader:
                writer = sqlite3.connect(str(target))
                try:
                    reader.backup(writer, pages=128, progress=progress, sleep=.01)
                    checks = writer.execute('PRAGMA integrity_check').fetchall()
                    if checks != [('ok',)]:
                        raise ValueError('Snapshot integrity check did not pass')
                finally:
                    writer.close()
            with target.open('r+b') as output:
                output.flush()
                os.fsync(output.fileno())
            with target.open('rb') as snapshot:
                digest = hashlib.file_digest(snapshot,'sha256').hexdigest()
            report['snapshots'].append({'source': str(source), 'snapshot': target.name,
                'bytes': target.stat().st_size, 'sha256': digest,
                'integrity_check': 'ok', 'elapsed_seconds': round(time.monotonic()-start, 3)})
        report['status'] = 'COMPLETE_PER_DATABASE'
    except Exception as error:
        report['status'] = 'INCOMPLETE_PRESERVED'
        report['error_type'] = type(error).__name__
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    result = snapshot_databases(args.source,args.destination)
    print(json.dumps({'status': result['status'], 'databases': len(result['snapshots']),
                      'manifest': str(args.destination/'snapshot-manifest.json')}))


if __name__ == '__main__':
    main()
