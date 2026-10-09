"""Host-specific read-only preview. Save private metadata only in private staging."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0, str(REPO / 'src'))
from cochem_pipeline.legacy_history import preview_legacy_archive

STAGE = Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006')
manifest = STAGE / 'legacy-online-snapshots-20261007/snapshot-manifest.json'
report = preview_legacy_archive(manifest, '28488116be2717225d5ecd0fb6b2d1409bfabbc115bc9a0d4335f1d1ccc760db')
target = STAGE / ('legacy-held-preview-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
with target.open('x', encoding='utf-8') as output:
    json.dump(report, output, indent=2, sort_keys=True)
    output.write('\n')
summary = {'schema': 'cochem-legacy-held-preview-summary/1', 'private_report_path': str(target),
    'private_report_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
    'snapshots_verified': len(report['snapshots']),
    'job_metadata_rows': sum(len(row['rows']) for row in report['snapshots']),
    'dispositions': dict(Counter(job['disposition'] for item in report['snapshots'] for job in item['rows'])),
    'modern_jobs_created': report['modern_jobs_created'], 'live_databases_opened': False,
    'source_bytes_preserved': True, 'budgets_reset': False, 'cross_database_point_in_time_verified': False,
    'implementation_sha256': hashlib.sha256((REPO/'src/cochem_pipeline/legacy_history.py').read_bytes()).hexdigest()}
print(json.dumps(summary, indent=2))
