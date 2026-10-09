"""Own-process Windows telemetry diagnostic; no production process/state writes."""
import ctypes
import gc
import hashlib
import json
from pathlib import Path
import psutil

from cochem_pipeline import resource_telemetry as telemetry

here = Path(__file__).absolute().parent
source = Path(telemetry.__file__)
expected = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\cochem_pipeline\resource_telemetry.py')
assert source == expected
process = psutil.Process()
rows = []

def snapshot(calls):
    collected = gc.collect()
    memory = process.memory_info()
    rows.append({'calls': calls, 'pointer_type_cache_entries': len(ctypes._pointer_type_cache),
                 'rss_mib': memory.rss / 1048576, 'private_mib': memory.private / 1048576,
                 'garbage_objects_collected': collected})

snapshot(0)
for call in range(1, 1001):
    result = telemetry._commit()
    assert result['available'] and result['process_memory_available']
    if call in (100, 500, 1000):
        snapshot(call)
result = {'schema': 'cochem-windows-commit-probe-own-process-growth/1',
          'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'calls': 1000, 'snapshots_after_gc': rows,
          'type_cache_growth': rows[-1]['pointer_type_cache_entries'] - rows[0]['pointer_type_cache_entries'],
          'private_mib_growth': rows[-1]['private_mib'] - rows[0]['private_mib'],
          'rss_mib_growth': rows[-1]['rss_mib'] - rows[0]['rss_mib'],
          'production_process_modified': False, 'model_jobs_submitted': 0,
          'credentials_databases_or_tasks_accessed': False, 'linux_results_counted_as_windows': False}
target = here / 'commit-probe-type-growth-actual-windows-20261009.json'
with target.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(result, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps(result, sort_keys=True))
