"""CreateNew archival evidence and update current owner instructions only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE=REPO/'docs/evidence/windows-2026-10-06'
PY_SHA='fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5'
PS_SHA='06522b04378d8d0b6cb915102e7fce1125700d96f68159c13e9d26a5c1c52e0b'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    assert sha(HERE/'diagnose-foundation-docker-r3-v1.py')==PY_SHA
    assert sha(HERE/'diagnose-foundation-docker-r3-v1.ps1')==PS_SHA
    preview=json.loads((HERE/'foundation-diagnostic-r3-v1-preview-final.json').read_text())
    assert preview['helper_sha256']==PY_SHA and preview['holds']==[] and preview['mode']=='READ_ONLY_PLAN'
    tests={}
    for name,expected in [('foundation-docker-diagnostic-r3-v1-tests-final.xml',41),('foundation-diagnostic-wrapper-tests-final.xml',83)]:
        suite=list(ET.parse(HERE/name).getroot().iter('testsuite'))
        count={key:sum(int(s.get(key,'0')) for s in suite) for key in ('tests','failures','errors','skipped')}
        assert count=={'tests':expected,'failures':0,'errors':0,'skipped':0}
        tests[name]={**count,'junit_seconds':str(sum(float(s.get('time','0')) for s in suite)),'sha256':sha(HERE/name)}
    names=[
        'diagnose-foundation-docker-r3-v1.ps1','diagnose-foundation-docker-r3-v1.py',
        'test_foundation_docker_diagnostic_r3_v1.py','test_foundation_diagnostic_wrapper_r3.py',
        'foundation-docker-diagnostic-r3-v1-tests.xml','foundation-docker-diagnostic-r3-v1-tests-final.xml',
        'foundation-diagnostic-wrapper-tests-initial.xml','foundation-diagnostic-wrapper-tests-final.xml',
        'foundation-diagnostic-python-preparation.json','foundation-diagnostic-python-preparation-final.json',
        'foundation-diagnostic-r3-v1-preview-final.json','RETURN_SETUP.txt',
        'inspect-execution-prerequisites-r3.py','inspect-execution-prerequisites-r3.ps1',
        'provision-execution-foundation-r3.ps1','worker-denial-acceptance-r3.py','check-worker-denials-r3.ps1']
    for name in names:assert (HERE/name).is_file()
    archive=EVIDENCE/'foundation-docker-diagnostic-r3-v1-preparation-2026-10-07'
    archive.mkdir(exist_ok=False)
    files={}
    for name in names:
        source=HERE/name;destination=archive/name
        with destination.open('xb') as stream:stream.write(source.read_bytes())
        assert sha(source)==sha(destination)
        files[name]={'sha256':sha(destination),'bytes':destination.stat().st_size}
    record={'schema':'cochem-foundation-diagnostic-preparation/1','created_utc':datetime.now(timezone.utc).isoformat(),
        'status':'READY_FOR_OWNER_READ_ONLY_DIAGNOSTIC_NOT_EXECUTED','entrypoint':str(HERE/'diagnose-foundation-docker-r3-v1.ps1'),
        'files':files,'tests':tests,'ordinary_windows_preparation_tests_passed':124,
        'test_scope':'Ordinary Windows3.12.13 and actual WinPS5.1 disposable files/private named pipe with inert privileged/Docker/task boundaries. No actual Docker, SYSTEM or deployment execution.',
        'post_test_wrapper_change':'Only companion helper pin changed5b7e32... to finalfc17... after83-case suite. Function logic unchanged; final actual default preview validates final pin compatibility.',
        'independent_review':{'reviewer':'host_inspection','result':'No concrete finding at final06522/fc17 pins; review excludes actual diagnostic success'},
        'historical_cause':'One server attestation before or after the owner-label Docker census failed; original native number was discarded. No specific Win32 cause or backend fault is inferred.',
        'planned_writes':['Fresh protected helper/config/input/receipt directory','One fresh no-trigger SYSTEM diagnostic task'],
        'existing_state_writes':False,'foundation_retry':False,'provisioning_performed':False,
        'actual_docker_commands_executed':0,'actual_system_tasks_created':0,'native_model_jobs_executed':0,
        'prior_task_terminal_state_directly_verified':False,'administrator_gate_requires_exact_old_task_terminal_result_2':True,
        'activation_ready':False,'archive_role':'Custody snapshot; live wrapper resolves frozen sibling helpers and repository copy helper. Not a standalone portable installer.'}
    (archive/'preparation.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    observed_path=REPO/'config/windows/aetherdesk-427.observed-20261007.json'
    observed=json.loads(observed_path.read_text(encoding='utf-8-sig'))
    observed['prepared_not_applied']['foundation_docker_diagnostic_r3_v1']={
        'status':'READY_FOR_OWNER_READ_ONLY_DIAGNOSTIC_NOT_EXECUTED','entrypoint':record['entrypoint'],
        'entrypoint_sha256':PS_SHA,'helper_sha256':PY_SHA,'ordinary_windows_preparation_tests_passed':124,
        'independent_review_passed':True,'actual_preview_holds':0,
        'private_prior_task_checks_deferred_to_administrator':True,'apply_executed':False,
        'preparation':(archive/'preparation.json').relative_to(REPO).as_posix(),
        'preparation_sha256':sha(archive/'preparation.json'),'activation_ready':False}
    observed['return_instructions']['sha256']=sha(HERE/'RETURN_SETUP.txt')
    observed['return_instructions']['existing_guide_hash_is_historical']=False
    observed['return_instructions']['next_read_only_diagnostic_pending']=True
    observed['return_instructions']['current_action']='Only the new read-only foundation diagnostic; original five-command sequence remains held'
    observed['return_instructions']['current_diagnostic_commands']=1
    observed['latest_preparation_utc']=datetime.now(timezone.utc).isoformat()
    observed_path.write_text(json.dumps(observed,indent=2)+'\n',encoding='utf-8')
    handoff=REPO/'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
    text=handoff.read_text(encoding='utf-8')
    needle='and receipts. A separate read-only diagnostic is being prepared; its reviewed\nowner command is pending. Docker acceptance, the legacy diagnostic and both\n'
    replacement='and receipts. A new [read-only diagnostic](evidence/windows-2026-10-06/foundation-docker-diagnostic-r3-v1-preparation-2026-10-07/preparation.json)\npassed 41 Python and 83 Windows PowerShell preparation tests and independent review.\nIts final ordinary preview has zero holds; protected prior-task gates remain\nadministrator checks. Run only `diagnose-foundation-docker-r3-v1.ps1 -Apply` from\nthe live updated return guide and provide its sanitized result. It creates its\nown protected diagnostic root and on-demand SYSTEM task, makes the fixed read-only\nDocker observations once, and captures safe operation/native-code metadata.\nA diagnostic pass still does not release provisioning. Docker acceptance, the legacy diagnostic and both\n'
    assert needle in text
    handoff.write_text(text.replace(needle,replacement,1),encoding='utf-8')
    print(json.dumps({'status':'DIAGNOSTIC_FROZEN_AND_RECORDED','preparation':str(archive/'preparation.json'),
        'preparation_sha256':sha(archive/'preparation.json'),'tests_passed':124,'apply_executed':False},indent=2))

if __name__=='__main__':main()
