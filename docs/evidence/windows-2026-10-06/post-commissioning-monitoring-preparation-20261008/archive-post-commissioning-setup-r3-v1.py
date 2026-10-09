"""Archive the prepared monitoring chain; no protected installation or task calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

W=Path(__file__).resolve().parent
OUT=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06\post-commissioning-monitoring-preparation-20261008')
PINS={
 'run-post-commissioning-setup-r3-v1.ps1':'6b1c7c7eb0a9f70d24abb9a778400b43917bb44007f2fa61bdf66c16e29b4ec7',
 'install-resource-observer-r3-v3.ps1':'04f5d032fb56404e3031fa3137416126734d45d6ebbb7ed74f5bbc5c7149db6b',
 'resource-observer-r3-v3/source-manifest.json':'f44a356bd0bdd4d27aff35e3f3d204509b608f3b72e31a39698003803681ee59',
 'install-independent-supervisor-staging-r3-v2.ps1':'00412dfa8d7d6668713f9fb850c6fc4a2dd4df4dcc1527c65106ce7497be893d',
 'install-held-supervisor-observation-r3-v2.ps1':'9fb90f828ff3b0a7776af9bc48acd99eb2a520508a80271a1e87b785a2ce7313',
 'resource-observer-r3-v2-dependencies.json':'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429',
 'protected-code-inspection-v4.ps1':'5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d',
}
def sha(raw):return hashlib.sha256(raw).hexdigest()

def main():
    files={}
    for name,pin in PINS.items():
        data=(W/name).read_bytes();assert sha(data)==pin,name;files[name]=data
    manifest=json.loads(files['resource-observer-r3-v3/source-manifest.json'])
    for row in manifest['files']:
        name='resource-observer-r3-v3/'+row['path'];data=(W/name).read_bytes()
        assert sha(data)==row['sha256'] and len(data)==row['size'];files[name]=data
    proofs=[]
    for name,count in [('resource-observer-r3-v3-tests.xml',163),('supervisor-scanner-successors-r3-v2-tests-final.xml',61),('post-commissioning-setup-r3-v1-tests-final.xml',59)]:
        data=(W/name).read_bytes();suites=list(ET.fromstring(data).iter('testsuite'))
        actual={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
        assert actual=={'tests':count,'failures':0,'errors':0,'skipped':0},actual
        files[name]=data;proofs.append({'path':name,'sha256':sha(data),**actual})
    for name in ['POST_COMMISSIONING_SETUP_R3.txt','prepare-post-commissioning-setup-r3-v1.py','test_post_commissioning_setup_r3_v1.py','test_resource_observer_r3_v3.py','test_resource_observer_installer_r3_v3.py','test_supervisor_scanner_successors_r3_v2.py','post-commissioning-setup-r3-v1-preview-final.json',Path(__file__).name]:
        files[name]=(W/name).read_bytes()
    preview=json.loads(files['post-commissioning-setup-r3-v1-preview-final.json'])
    assert preview['mode']=='READ_ONLY_PLAN' and preview['branch']=='commissioned'
    assert preview['commissioning_required_before_apply'] is True and preview['authentication_or_first_start_invoked'] is False
    assert len(preview['holds'])==3
    assert preview['holds'][0]['code']=='COMMISSIONING_REQUIRED_NO_AUTHENTICATION_OR_STARTUP_IN_THIS_BATCH'
    assert not OUT.exists(),'Preserve existing archives.'
    OUT.mkdir();inventory=[]
    for name,data in files.items():
        path=OUT/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(data)
        inventory.append({'path':name,'bytes':len(data),'sha256':sha(data)})
    report={'schema':'cochem-post-commissioning-monitoring-preparation/1','recorded_utc':datetime.now(timezone.utc).isoformat(),
        'status':'PREPARED_NOT_APPLIED','files':inventory,'ordinary_windows_tests':proofs,'distinct_tests_for_successors':283,
        'actual_preview_holds':preview['holds'],'current_controller_required_before_apply':True,'authentication_or_first_start_replayed':False,
        'source_review':'Producer receipt fields and constrained diffs reviewed; native scanner bounds and seven-file dependency closure retained.',
        'configuration_sha256':'135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        'shared_slots':4,'worker_identities':6,'chapter06_routing_changed':False,'credentials_or_databases_copied':False,
        'repair_budgets_modified':False,'ram_or_startup_task_modified':False,'privileged_tasks_registered_or_started':False,
        'observer_installed':False,'observer_started':False,'actual_48h_complete':False,'genuine_desktop_heap_available':False,
        'independent_recovery_timing_available':False,'paid_repair_enabled':False,'full_srs_acceptance':False,
        'linux_results_counted_as_windows':False,'current_owner_action':'Complete the existing v4 sign-in/startup command; this future batch does not add a current step.'}
    with (OUT/'preparation.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({'path':str(OUT),'files':len(inventory),'tests':283,'status':report['status']}))

if __name__=='__main__':main()
