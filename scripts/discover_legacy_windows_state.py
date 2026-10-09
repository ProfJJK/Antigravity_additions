"""Read-only legacy deployment inventory. Never imports legacy application code.

Only schema, counts, numeric counters and selected process-state metadata leave
the databases. Job payloads, results, telemetry details and credential files are
not selected. This is discovery, not a migration or a zero-spend attestation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time


DATABASES = ('job_board.db', 'knowledge_index.db', 'db/job_board.db',
             'db/oracle_tracking.db', 'db/wikirag.db')
MODERN_SUPERVISOR = {'supervisor_incidents', 'supervisor_attempts', 'supervisor_events'}
MODERN_COMPONENT = {'components', 'recovery_events'}
MAX_JSON = 4 * 1024 * 1024
SOURCE_FILES = ('start_pipeline.bat', 'auto_architect_mcp.py', 'cochem_master_loop_shard_1.py',
                'src/cochem/warden/mcp_server.py', 'src/cochem/warden/ladder.py',
                'src/cochem/warden/vm_control.py', 'src/cochem/warden/cochem_warden_oracle.py',
                'src/cochem/watchdog/watchdog_sre.py', 'src/cochem/dsp/worker_daemon.py',
                'src/cochem/knowledge/server.py')


def metadata(path):
    path = Path(path)
    stat = path.stat()
    return {'path': str(path), 'bytes': stat.st_size, 'modified_ns': stat.st_mtime_ns}


def read_json(path):
    if path.stat().st_size > MAX_JSON:
        raise ValueError('Discovery JSON exceeds bounded read size')
    raw = path.read_bytes()
    if len(raw) > MAX_JSON:
        raise ValueError('Discovery JSON grew beyond bounded read size')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Discovery JSON must be an object')
    return value, hashlib.sha256(raw).hexdigest()


def inspect_database(path):
    path = Path(path).resolve()
    before = metadata(path)  # A missing input must never create an empty database.
    deadline = time.monotonic() + 10
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute('BEGIN')
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if len(names) > 256:
            raise ValueError('Legacy database table count exceeds discovery scope')
        tables = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [{'name': row[1], 'type': row[2]} for row in connection.execute('PRAGMA table_info(' + quoted + ')')]
            tables[name] = {'columns': columns,
                            'rows': connection.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]}
        result = {**before, 'mode': 'SQLite URI mode=ro, query_only, bounded read transaction',
                  'tables': tables,
                  'modern_supervisor_compatible': MODERN_SUPERVISOR <= set(names),
                  'modern_component_compatible': MODERN_COMPONENT <= set(names),
                  'modern_pipeline_schema_present': 'pipeline_jobs' in names,
                  'repair_budget_remaining': None}
        if 'jobs' in tables and {'status', 'attempts'} <= {c['name'] for c in tables['jobs']['columns']}:
            result['jobs_by_status'] = [{'status': row[0], 'rows': row[1],
                'recorded_attempts_sum': row[2], 'recorded_attempts_max': row[3]}
                for row in connection.execute('SELECT status,COUNT(*),SUM(attempts),MAX(attempts) FROM jobs GROUP BY status')]
            result['attempt_scope'] = 'Current job counters only; not lifetime paid calls, currency, or proof of live processes'
        if 'recovery_telemetry' in names:
            result['recovery_tier_summary'] = [{'tier': row[0], 'rows': row[1], 'first_timestamp': row[2], 'last_timestamp': row[3]}
                for row in connection.execute('SELECT tier,COUNT(*),MIN(timestamp),MAX(timestamp) FROM recovery_telemetry GROUP BY tier')]
            result['recovery_scope'] = 'Legacy VM recovery telemetry; not a model-call budget ledger'
        if 'quota_state' in names:
            result['quota_numeric_state'] = [{'provider': row[0], 'current_usage': row[1], 'quota_limit': row[2],
                'cost_per_upload': row[3], 'last_reset_date': row[4]}
                for row in connection.execute('SELECT provider,current_usage,quota_limit,cost_per_upload,last_reset_date FROM quota_state')]
            result['quota_scope'] = 'Provider upload quota; never treat as supervisor repair spend'
        if 'documents' in tables and 'doc_path' in {c['name'] for c in tables['documents']['columns']}:
            prefixes = [path.parent.parent / 'v4.1.2' / 'wiki', Path.home() / '.gemini/antigravity']
            groups = [{'prefix': prefix.as_posix(), 'documents': connection.execute(
                "SELECT COUNT(*) FROM documents WHERE REPLACE(doc_path, char(92), '/') LIKE ?",
                (prefix.as_posix().rstrip('/') + '/%',)).fetchone()[0]} for prefix in prefixes]
            result['knowledge_path_prefix_counts'] = groups
            result['knowledge_other_path_count'] = tables['documents']['rows'] - sum(row['documents'] for row in groups)
            result['knowledge_scope'] = 'Source path ownership only; document titles and contents not selected'
        result['transaction_integrity_check'] = connection.execute('PRAGMA quick_check').fetchone()[0]
    finally:
        connection.close()
    result['main_file_metadata_unchanged_during_inspection'] = before == metadata(path)
    result['wal_files'] = [metadata(Path(str(path) + suffix)) for suffix in ('-wal', '-shm')
                           if Path(str(path) + suffix).exists()]
    return result


def inspect_root(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Legacy root must already exist')
    result = {'root': str(root), 'databases': [], 'watchdog_states': [], 'repair_loop_states': [],
              'launch_receipts': [], 'launch_receipt_errors': [], 'knowledge_roots': {}, 'source_commitments': []}
    for relative in SOURCE_FILES:
        path = root / relative
        if path.is_file():
            before = metadata(path)
            raw = path.read_bytes()
            result['source_commitments'].append({**before, 'sha256': hashlib.sha256(raw).hexdigest(),
                'metadata_unchanged_during_read': before == metadata(path)})
    for relative in DATABASES:
        path = root / relative
        if path.exists():
            result['databases'].append(inspect_database(path))
    for filename in ('watchdog_state.json', 'watchdog_heartbeat.json'):
        path = root / '.evidence/watchdog' / filename
        if not path.exists():
            continue
        value, sha = read_json(path)
        safe = {key: value[key] for key in ('recovery_pid', 'recovery_started', 'recovery_create_time',
            'last_trigger_ts', 'last_recovery_outcome', 'consecutive_cycle_failures', 'pid', 'ts', 'cycle', 'verdict')
            if key in value and (value[key] is None or type(value[key]) in (int, float, bool, str))}
        safe['frozen_pid_count'] = len(value.get('frozen_pids', [])) if isinstance(value.get('frozen_pids', []), list) else None
        result['watchdog_states'].append({**metadata(path), 'sha256': sha, 'selected_state': safe,
                                         'process_liveness_verified': False})
    for path in sorted((root / 'repair_tasks').glob('state*/loop_state.json')):
        value, sha = read_json(path)
        entries = list(value.get('targets', {}).values())
        rounds = [entry['rounds'] for entry in entries if isinstance(entry, dict) and type(entry.get('rounds')) is int]
        result['repair_loop_states'].append({**metadata(path), 'sha256': sha, 'targets': len(entries),
            'statuses': dict(Counter(entry.get('status', 'unknown') for entry in entries if isinstance(entry, dict))),
            'recorded_rounds_sum': sum(rounds), 'recorded_rounds_max': max(rounds, default=None),
            'scope': 'Per-target historical loop counters; not incident/day paid-call reservations'})
    for path in sorted((root / '.evidence/watchdog/bundles').glob('*/recovery_launch.json')):
        try:
            value, sha = read_json(path)
            launch = value['recovery_launch']
            result['launch_receipts'].append({**metadata(path), 'sha256': sha,
                'outcome': launch.get('outcome'), 'timestamp': launch.get('timestamp'),
                'recovery_pid': launch.get('recovery_pid'), 'bundle_day': path.parent.name[:8]})
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            result['launch_receipt_errors'].append({**metadata(path), 'error_type': type(exc).__name__})
    result['launch_outcomes'] = dict(Counter(row['outcome'] for row in result['launch_receipts']))
    for name in ('.sources', 'wiki', 'v4.1.2_manifest.json'):
        path = root / name
        result['knowledge_roots'][name] = {'path': str(path), 'exists': path.exists(), 'is_directory': path.is_dir()}
    result['modern_state_files'] = {name: (root / name).exists() for name in (
        'supervisor.db', 'component-recovery.db', 'repair-quarantine.json', 'release-journal.json', 'current.json')}
    return result


def discover(roots):
    results = [inspect_root(root) for root in roots]
    hashes = defaultdict(list)
    for root in results:
        for record in root['launch_receipts']:
            hashes[record['sha256']].append(record)
    return {'schema': 'cochem-legacy-migration-discovery/1',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Read-only native Windows schemas/counts and selected historical metadata; no state changes or inference',
        'roots': results,
        'launch_deduplication': {'distinct_receipt_hashes': len(hashes),
            'identical_copies_not_additional_attempts': sum(len(rows) - 1 for rows in hashes.values()),
            'distinct_launched_receipts': sum(rows[0]['outcome'] == 'LAUNCHED' for rows in hashes.values()),
            'scope': 'Byte-identical receipt deduplication only; no currency or complete paid-call totals inferred'},
        'repair_budget_remaining': None, 'absence_is_zero_spend': False, 'migration_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', action='append', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        destination = args.output.resolve()
        if any(destination.is_relative_to(root.resolve()) for root in args.root):
            raise ValueError('Discovery output must not be written into a legacy source tree')
        if destination.exists():
            raise ValueError('Preserve the existing discovery report; choose a new output')
    report = discover(args.root)
    if args.output:
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
