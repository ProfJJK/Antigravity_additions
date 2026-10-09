import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

work=Path(__file__).parent
target=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06\return-setup-series-r3-preparation-2026-10-07')
target.mkdir(exist_ok=False)
names=['run-return-setup-r3.ps1','test_return_setup_series_r3.py','return-setup-series-r3-tests.xml',
       'return-setup-series-r3-tests-v2.xml','return-setup-series-r3-tests-v3.xml','return-setup-series-r3-preview-v2.json',
       'inspect-execution-prerequisites-r3.ps1','provision-execution-foundation-r3.ps1','install-disposable-project.ps1']
files={}
for name in names:
    data=(work/name).read_bytes()
    with (target/name).open('xb') as stream:stream.write(data)
    files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
assert files['run-return-setup-r3.ps1']['sha256']=='9d3e2bfbbbb58526605c9b88055c652b5cf962cd02b15dc424369cf8c6708d77'
assert files['return-setup-series-r3-tests-v3.xml']['sha256']=='87c117201d671029f864e8b9a6b26ecaeafaa2af6e0763936328248fad27b685'
suite=next(ET.parse(target/'return-setup-series-r3-tests-v3.xml').getroot().iter('testsuite'))
assert {k:suite.get(k) for k in ('tests','errors','failures','skipped')}=={'tests':'24','errors':'0','failures':'0','skipped':'0'}
report={'schema':'cochem-return-series-preparation/1','recorded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'status':'PREPARED_REVIEWED_NOT_EXECUTED','live_entrypoint':str(work/names[0]),'files':files,
    'phases':['inspection','foundation','project'],'maximum_shared_slots':4,'worker_identities':6,
    'existing_leaf_scripts_modified':False,'automatic_retry_or_resume':False,'privileged_execution_performed':False,
    'tests':{'passed':24,'errors':0,'failures':0,'skipped':0,'seconds':suite.get('time'),
       'scope':'Actual PS5.1 file holds, scoped child scripts, safe failure streaming and simulated leaf results; ordinary Apply refusal. No privileged leaf applied.'},
    'independent_review':{'reviewer':'host_inspection','result':'Passed at frozen source pin'},
    'findings_and_repairs':['Initial test collection failed on a fixture raw-string terminator; no deployment ran.',
       'Independent review found project failure JSON emitted before a throw was lost. Bounded incremental capture now republishes only its validated safe metadata; actual child-throw fixture covers the path. Frozen leaf scripts unchanged.'],
    'actual_read_only_preview':json.loads((work/'return-setup-series-r3-preview-v2.json').read_bytes()),
    'linux_results_used':False,'activation_ready':False,
    'archive_scope':'Exact outer/leaf/test/evidence snapshots; live scripts still require their separately pinned sibling and repository dependencies.'}
data=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
with (target/'preparation.json').open('xb') as stream:stream.write(data)
print(json.dumps({'path':str(target/'preparation.json'),'sha256':hashlib.sha256(data).hexdigest(),'files':len(files)}))
