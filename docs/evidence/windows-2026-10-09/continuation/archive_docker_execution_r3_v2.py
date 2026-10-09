"""Exclusive archive of the final conditional physical acceptance package."""
from pathlib import Path
import hashlib,json,xml.etree.ElementTree as ET
W=Path(__file__).parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
A=REPO/'docs/evidence/windows-2026-10-06/docker-execution-r3-v2-preparation-2026-10-07'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
 prep_name='docker-execution-r3-v2-preparation-v2.json';prep_raw=(W/prep_name).read_bytes()
 if sha(prep_raw)!='2ba4b318dacf314450f623d160fcdd25c583f2c091a3d61773cf4788170935af':raise ValueError('Preparation pin differs')
 prep=json.loads(prep_raw)
 pins={name:row['sha256'] for name,row in prep['artifacts'].items()}
 pins.update({prep_name:sha(prep_raw),'docker-execution-r3-v2-independent-review-final.json':'89eaa8d2237984872e4eb421802fedc00c9e9a7927e52e4754f1203421efde63',
  'provision-execution-foundation-r3-v2.py':'620c43db688bac8fca2c7870197c92a7cb4b54f0a1c6b81bc3453ab6b2a3ff0c','provision-execution-foundation-r3-v2.ps1':'0df813342fa8070d1f2f6041ccbc24a7d8ec0d5c94baa269eec02c243e043ca0',
  'diagnose-foundation-docker-r3-v1.py':'fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5','diagnose-foundation-docker-r3-v1.ps1':'06522b04378d8d0b6cb915102e7fce1125700d96f68159c13e9d26a5c1c52e0b',
  'accept-docker-execution-r3-v1.py':'3ac01c18be77c18b6c80601a681f7b3c082424e88d2f79a29fe15dc9a63aa0c5','accept-docker-execution-r3-v1.ps1':'380386520ace322d7967382d8778b9751bb510f13ce391cd7e92ee3b000bf035'})
 records=[]
 for name,pin in pins.items():
  raw=(W/name).read_bytes()
  if sha(raw)!=pin:raise ValueError('Artifact changed: '+name)
  records.append((name,raw,pin))
 for name in ('RETURN_SETUP.txt','MORNING_SETUP.txt'):
  raw=(W/name).read_bytes();records.append((name,raw,sha(raw)))
 attrs=ET.parse(W/'docker-execution-r3-v2-tests-final-v2.xml').getroot().find('testsuite').attrib
 if int(attrs['tests'])!=58 or any(int(attrs[x]) for x in ('failures','errors','skipped')):raise ValueError('Final tests not green')
 preview=json.loads((W/'docker-execution-r3-v2-preview-v2.json').read_text(encoding='utf-8-sig'))
 if preview['holds']!=['Successful scoped foundation receipt is not present.']:raise ValueError('Actual preview holds differ')
 foundation=REPO/'docs/evidence/windows-2026-10-06/execution-foundation-r3-v2-continuation-preparation-2026-10-07/preparation.json'
 if sha(foundation.read_bytes())!='5265820aa096715bc02f8a894df12a506f2f0ba4ccb07c963f769ce5eff99f83':raise ValueError('Foundation preparation changed')
 report={'schema':'cochem-docker-physical-preparation-archive/2','status':'PREPARED_CONDITIONAL_ON_ACTUAL_FOUNDATION_AND_PRIVATE_PROJECT_SUCCESS','source_records':[{'path':n,'sha256':p,'bytes':len(b)} for n,b,p in records],
  'ordinary_windows_preparation_tests':{'passed':58,'failures':0,'errors':0,'skipped':0,'junit_seconds':attrs['time']},
  'foundation_preparation_sha256':'5265820aa096715bc02f8a894df12a506f2f0ba4ccb07c963f769ce5eff99f83',
  'independent_review_sha256':pins['docker-execution-r3-v2-independent-review-final.json'],'default_preview_holds':preview['holds'],
  'execution':{'apply_performed':False,'actual_physical_acceptance':False,'provider_or_model_calls':0,'pipeline_started':False,'activation_ready':False},
  'scope':{'max_owned_container_creation_attempts':2,'shared_capacity':4,'warm_preparation':2,'fixed_red_green_fixture':True,'automatic_retry_allowed':False,'native_identities_excluded':6,'existing_R_volume_and_startup_task_preserved':True,'existing_databases_and_repair_budgets_preserved':True},
  'failure_diagnostics':{'fifth_pinned_diagnostic_support_definitions_only':True,'numeric_cause_codes_preserved':True,'raw_messages_withheld':True,'guard_or_transport_decisions_changed':False},
  'limitations':['Ordinary tests simulate privileged/Docker boundaries; no physical Docker operation occurred.','Missing successful foundation receipt is an expected live prerequisite, not a passed deployment phase.','The historical exact foundation failure cause remains unknown.','Linux results are separate.']}
 A.mkdir(exist_ok=False)
 for name,raw,pin in records:
  with (A/name).open('xb') as f:f.write(raw)
 with (A/'preparation.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2);f.write('\n')
 print(json.dumps({'archive':str(A/'preparation.json'),'sha256':sha((A/'preparation.json').read_bytes()),'ordinary_tests_passed':58}))
if __name__=='__main__':main()
