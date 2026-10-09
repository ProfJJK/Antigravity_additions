"""Archive reviewed ordinary Windows evidence; CreateNew files only, no deployment."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W=Path(__file__).resolve().parent
OUT=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06\inspection-continuation-r3-v4-preparation-20261008')
PIN='f7c6364dd64a048695fd8f10e84360a1d8d02130fb97a59138a0796beb58e283'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def main():
    name='inspection-continuation-v4-preparation-20261008.json'
    raw=(W/name).read_bytes()
    assert sha(raw)==PIN
    preparation=json.loads(raw)
    files={name:raw}
    for row in preparation['sources']+preparation['windows_evidence']:
        data=(W/row['name']).read_bytes()
        assert sha(data)==row['sha256'],row['name']
        if 'bytes' in row:assert len(data)==row['bytes']
        files[row['name']]=data
    for file,count in [('inspection-continuation-v4-tests-final.xml',148),('protected-inspection-v4-fixtures-ordinary.xml',32)]:
        suites=list(ET.fromstring(files[file]).iter('testsuite'))
        actual={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
        assert actual=={'tests':count,'failures':0,'errors':0,'skipped':0}
    benchmark=json.loads(files['inspection-v4-windows-measurement-165c8e06e5314cd0ae0a1a75a36d86c6.json'])
    assert benchmark['administrator'] is False
    assert [r['entries'] for r in benchmark['roots']]==[15026,3727,6]
    assert all(r['status']=='PASSED' for r in benchmark['roots'])
    for extra in ['RETURN_SETUP_CONTINUE.txt','measure-protected-inspection-v3.ps1','measure-protected-inspection-v4.ps1',Path(__file__).name,'protected-inspection-v4-fixtures-20261008T174553-d5aeb500.stdout.json']:
        files[extra]=(W/extra).read_bytes()
    assert not OUT.exists(),'Preserve an existing archive; do not overwrite.'
    OUT.mkdir()
    records=[]
    for name,data in files.items():
        with (OUT/name).open('xb') as stream:stream.write(data)
        records.append({'path':name,'sha256':sha(data),'bytes':len(data)})
    report={'schema':'cochem-inspection-continuation-archive/1','archived_at_utc':datetime.now(timezone.utc).isoformat(),
        'status':'REVIEWED_REPAIR_PREPARED_NOT_APPLIED','files':records,'ordinary_windows_tests':180,
        'actual_installed_tree_scans':'PASSED_ORDINARY_WINDOWS_TOKEN','system_or_admin_acceptance':False,
        'pipeline_started':False,'model_jobs_executed':0,'credentials_or_databases_copied':False,
        'linux_results_counted_as_windows':False,'full_srs_acceptance':False,
        'next':'Owner runs run-pipeline-commissioning-r3-v4.ps1 -Apply -Interactive; browser approval only for currently logged-out profiles.'}
    with (OUT/'archive.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({'path':str(OUT),'files':len(records),'status':report['status']}))

if __name__=='__main__':main()
