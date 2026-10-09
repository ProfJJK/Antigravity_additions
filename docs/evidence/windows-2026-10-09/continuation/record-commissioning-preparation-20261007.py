"""Preserve prior guidance, then record reviewed preparation without deployment claims."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
OBSERVED=REPO/'config/windows/aetherdesk-427.observed-20261007.json'
HANDOFF=REPO/'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
GUIDE=HERE/'DEPLOYMENT_REMAINING_20261007.txt'
ARCHIVE=REPO/'docs/evidence/windows-2026-10-06/commissioning-preparation-state-2026-10-07'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def create(path,data):
    with path.open('xb') as stream:stream.write(data)
def encoded(value):return (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()

def main():
    expected={OBSERVED:'dbb459bb822102392960a7995f805d203ea8fab173b7e5ac0c60c19442cab509',
              HANDOFF:'c9858ec106c1ff9b400899834eda09ac24a9ba6b79fb6edcf0ddaaac22c024f2',
              GUIDE:'4ce981f24ea9dfa0e4d079d6ef8dfc00c81b68312b359437ca0c2924a7d77e75'}
    for path,pin in expected.items():assert sha(path)==pin,'Current evidence/guidance changed; preserve and review'
    auth=REPO/'docs/evidence/windows-2026-10-06/six-profile-status-first-auth-2026-10-07/preparation.json'
    assert sha(auth)=='d27318e6bb63820896d03a817b9158407567ec4d975884864ddd54e5398df518'
    auth_value=json.loads(auth.read_text())
    for row in auth_value['files']:
        assert sha(HERE/row['name'])==row['sha256']
    suite=ET.parse(HERE/'status-first-native-six-r3-v1-tests-final.xml').getroot().find('testsuite')
    assert {key:suite.attrib[key] for key in ('tests','failures','errors','skipped')}=={'tests':'68','failures':'0','errors':'0','skipped':'0'}
    ARCHIVE.mkdir(exist_ok=False)
    for path,name in ((OBSERVED,'observed-before.json'),(HANDOFF,'handoff-before.md'),(GUIDE,'guide-before.txt')):create(ARCHIVE/name,path.read_bytes())
    observed=json.loads(OBSERVED.read_text(encoding='utf-8-sig'))
    observed['return_instructions_history'].append({'captured_utc':datetime.now(timezone.utc).isoformat(),
                                                  'instructions':observed['return_instructions']})
    observed['current_policy_disposition']['authentication_minimum']=(
        'Six immutable chapter identities share four concurrent slots. The four-profile proposal was rejected and never applied. '
        'Use reviewed status-first checks; only explicitly logged-out isolated subscription sessions need browser authorization. '
        'At most twelve Codex/Claude profile authorizations, with the actual count unknown before SYSTEM status checks.')
    observed['worker_identity_count_correction']['status_first_preparation_complete']=True
    observed['worker_identity_count_correction']['status_first_preparation']=auth.relative_to(REPO).as_posix()
    observed['prepared_not_applied']['status_first_native_six_r3_v1']={
        'status':'PREPARED_INDEPENDENTLY_REVIEWED_NOT_APPLIED','entrypoint':str(HERE/auth_value['entrypoint']),
        'entrypoint_sha256':sha(HERE/auth_value['entrypoint']),'preparation':auth.relative_to(REPO).as_posix(),
        'preparation_sha256':sha(auth),'ordinary_windows_fixture_tests_passed':68,
        'native_admin_task_boundaries_inert':True,'identity_count':6,'shared_capacity':4,
        'maximum_browser_authorizations':12,'actual_browser_authorizations_required':None,
        'provider_logins_executed':0,'native_model_jobs_executed':0,'included_in_final_startup_series':False}
    observed['return_instructions']={
        'status':'CONSOLIDATED_COMMISSIONING_UNDER_IMPLEMENTATION_NO_CURRENT_OWNER_COMMAND',
        'guide':str(GUIDE),'current_command':None,'administrator_series_ready':False,
        'status_first_authentication_prepared':True,'owner_timing_projection_required':False,
        'physical_fixture_rerun_required':False,'repeat_completed_setup_required':False,
        'human_participation':['Browser/device authorization only for verified logged-out isolated profiles',
                               'Reviewed administrator commissioning series once complete',
                               'One convenient controlled reboot for recovery acceptance',
                               'Unattended 48-hour host availability for stability acceptance'],
        'controller_started':False,'automatic_repair_enabled':False,'full_srs_acceptance':False}
    observed['latest_preparation_utc']=datetime.now(timezone.utc).isoformat()
    GUIDE.write_text(GUIDE.read_text().replace(
        'A new reviewed\n   status-first flow will skip proven authenticated profiles and prompt only for\n   explicit logged-out profiles.',
        'The reviewed\n   status-first flow skips proven authenticated profiles and prompts only for\n   explicit logged-out profiles. Its 68 ordinary Windows fixture checks passed;\n   the administrator/SYSTEM/login boundaries have not yet been executed.'
    ).replace('- Prepare status-first authentication for all six chapter identities, preserving\n',
              '- Include the reviewed status-first authentication in the combined startup series, preserving\n'),encoding='utf-8')
    HANDOFF.write_text(HANDOFF.read_text().replace(
        'A status-first all-six flow is being prepared to\nskip proven valid sessions, requiring at most twelve Codex/Claude authorizations',
        'The status-first all-six flow is prepared and independently reviewed to\nskip proven valid sessions, requiring at most twelve Codex/Claude authorizations'
    ).replace('Unknown status needs engineering review,',
              'Its 68 ordinary Windows fixture checks passed; actual SYSTEM status/login\nexecution remains unperformed. Unknown status needs engineering review,'),encoding='utf-8')
    current=HERE/'RETURN_SETUP.txt'
    assert sha(current)==expected[GUIDE],'Live pointer changed; do not overwrite'
    current.write_bytes(GUIDE.read_bytes())
    observed['return_instructions']['guide_sha256']=sha(GUIDE)
    OBSERVED.write_bytes(encoded(observed))
    create(ARCHIVE/'guide-current.txt',GUIDE.read_bytes())
    create(ARCHIVE/'summary.json',encoded({'schema':'cochem-commissioning-preparation-state/1',
        'status':'PREPARATION_RECORDED_NOT_APPLIED','auth_preparation_sha256':sha(auth),
        'observed_sha256':sha(OBSERVED),'handoff_sha256':sha(HANDOFF),'guide_sha256':sha(GUIDE),
        'installed_runtime_or_configuration_changed':False,'production_tasks_changed':False,
        'native_or_model_jobs_executed':0,'full_srs_acceptance':False,
        'stale_four_profile_and_older_command_guidance_replaced_in_current_fields':True}))
    print(json.dumps({'archive':str(ARCHIVE),'observed_sha256':sha(OBSERVED),'guide_sha256':sha(GUIDE)}))

if __name__=='__main__':main()
