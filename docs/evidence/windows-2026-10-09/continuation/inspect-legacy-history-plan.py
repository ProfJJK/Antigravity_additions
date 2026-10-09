"""Snapshot schema/count/hash inspection only; no live DB, payload or mutation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

STAGE = Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006')
SNAPSHOTS = STAGE / 'legacy-online-snapshots-20261007'
manifest_raw = (SNAPSHOTS / 'snapshot-manifest.json').read_bytes()
manifest = json.loads(manifest_raw)
selected = {'01-job_board.db', '03-job_board.db', '05-job_board.db', '06-oracle_tracking.db', '07-wikirag.db'}
result = {'schema': 'cochem-legacy-history-readonly-review/1',
          'checked_at_utc': datetime.now(timezone.utc).isoformat(),
          'snapshot_manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
          'snapshot_capture_utc': manifest['created_at_utc'],
          'cross_database_point_in_time_verified': False,
          'live_databases_opened': False, 'payloads_or_rules_read': False,
          'migration_executed': False, 'repair_budget_authority_verified': False,
          'source_files_preserved': True, 'snapshots': []}
for row in manifest['snapshots']:
    if row['snapshot'] not in selected:
        continue
    path = SNAPSHOTS / row['snapshot']
    before = path.read_bytes()
    if hashlib.sha256(before).hexdigest() != row['sha256'] or len(before) != row['bytes']:
        raise ValueError('Preserved snapshot bytes differ from receipt')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=5)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        tables = [name for (name,) in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        record = {'snapshot': row['snapshot'], 'sha256': row['sha256'], 'bytes': row['bytes'],
                  'integrity': connection.execute('PRAGMA integrity_check').fetchone()[0],
                  'modern_pipeline_schema': 'pipeline_jobs' in tables, 'tables': {}}
        for name in tables:
            # Only schema-enumerated identifiers, safely SQL quoted; no payload SELECT.
            escaped = '"' + name.replace('"', '""') + '"'
            record['tables'][name] = {'rows': connection.execute('SELECT count(*) FROM ' + escaped).fetchone()[0],
                'columns': [item[1] for item in connection.execute('PRAGMA table_info(' + escaped + ')')]}
        if 'jobs' in tables:
            record['job_status_counts'] = dict(connection.execute('SELECT status,count(*) FROM jobs GROUP BY status'))
            record['attempt_sum_not_repair_spend'] = connection.execute('SELECT coalesce(sum(attempts),0) FROM jobs').fetchone()[0]
            record['nonzero_job_attempt_rows'] = connection.execute('SELECT count(*) FROM jobs WHERE attempts>0').fetchone()[0]
        result['snapshots'].append(record)
    finally:
        connection.close()
    if path.read_bytes() != before:
        raise ValueError('Preserved snapshot changed during read-only inspection')
timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
target = STAGE / ('legacy-history-review-' + timestamp + '.json')
with target.open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2, sort_keys=True)
print(json.dumps({'evidence_path': str(target), 'evidence_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'snapshots_checked': len(result['snapshots']), 'source_files_preserved': True,
                  'snapshot_counts': [{'snapshot': row['snapshot'], 'counts': {name: table['rows'] for name, table in row['tables'].items()},
                                       'job_status_counts': row.get('job_status_counts'), 'modern_pipeline_schema': row['modern_pipeline_schema']}
                                      for row in result['snapshots']]}, indent=2))
