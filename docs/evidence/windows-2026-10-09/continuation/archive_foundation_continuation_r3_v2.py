"""Archive reviewed preparation and update only ordinary owner guidance."""
from pathlib import Path
import hashlib,json,xml.etree.ElementTree as ET

W=Path(__file__).parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ARCHIVE=REPO/'docs/evidence/windows-2026-10-06/execution-foundation-r3-v2-continuation-preparation-2026-10-07'
PINS={
 'provision-execution-foundation-r3-v2.py':'620c43db688bac8fca2c7870197c92a7cb4b54f0a1c6b81bc3453ab6b2a3ff0c',
 'provision-execution-foundation-r3-v2.ps1':'0df813342fa8070d1f2f6041ccbc24a7d8ec0d5c94baa269eec02c243e043ca0',
 'run-return-continuation-r3-v2.ps1':'a4e402081761ae01386ced7538e321d7dd766a7092c2c7b84afa6d8f0e621ad3',
 'install-disposable-project.ps1':'66bd5b039411b726b246fe4c4a57f8514a8fdbd40978283d2f74c1d922a2ec0d',
 'diagnose-foundation-docker-r3-v1.py':'fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5',
 'diagnose-foundation-docker-r3-v1.ps1':'06522b04378d8d0b6cb915102e7fce1125700d96f68159c13e9d26a5c1c52e0b',
 'provision-execution-foundation-r3.py':'fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1',
 'provision-execution-foundation-r3.ps1':'402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c',
 'inspect-execution-prerequisites-r3.py':'17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a',
 'inspect-execution-prerequisites-r3.ps1':'5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887',
 'worker-denial-acceptance-r3.py':'b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77',
 'check-worker-denials-r3.ps1':'5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d',
 'return-continuation-r3-v2-independent-review.json':'f0ea4a79c11ce3902674c74b02f831772f3cf99bf5a73b1a5e91a4d4dc1b7f0f',
 'foundation-continuation-r3-v2-install-repairs-review.json':'491b1d3b1bd2f643ebb73de9c3fcd47f110674bd5356511cecdd5f89caeca92c',
 'execution-foundation-r3-v2-python-preparation.json':'e79cfd2850135ca2f74b8ca8a827d01e64bd942389a0deaedf01a04a9474d152',
 'execution-foundation-r3-v2-tests-final.xml':'45414d097cdca1c3bdb0cab40a6360cde42856de31f774c7fbbdf0d50095f4a9',
 'foundation-continuation-wrapper-tests-final.xml':'56e1c8e697e1946553534640270e1f66aeace019554f6f47289825033a3cc832',
 'return-continuation-r3-v2-tests-final.xml':'037c28cd4f84e013595ae54d495e7c7932c5f3a2b24dab399bf0e1d1570b3535',
 'return-continuation-r3-v2-preview-final.json':'2f1d74f64358be9989639337bd2f46b75faecf95d6615fb590b74852d998b0c7',
 'test_execution_foundation_r3_v2.py':'4008910fecd5ff111e95fe9bf883b4a77965074985791a3365646a7a208be8ba',
 'test_foundation_continuation_wrapper_r3_v2.py':'8f30eb20b29af8bf3119488b98968fdf961fcdde88bd70efe876002409817051',
 'test_foundation_diagnostic_wrapper_r3.py':'9f6dea78a5bf83668fe4d3e53b0f535cc4353b828ecbbedeb6886a4ff7292fc7',
}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def create(path,raw):
 with path.open('xb') as stream:stream.write(raw)
def main():
 records=[]
 for name,pin in PINS.items():
  raw=(W/name).read_bytes()
  if sha(raw)!=pin:raise ValueError('Prepared artifact pin changed: '+name)
  records.append((name,raw,pin))
 for name in ('test_return_continuation_r3_v2.py','test_execution_foundation_r3.py'):
  raw=(W/name).read_bytes();records.append((name,raw,sha(raw)))
 helper=REPO/'scripts/stage_aetherdesk_427_payloads.ps1';raw=helper.read_bytes()
 if sha(raw)!='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b':raise ValueError('Copy support changed')
 records.append(('stage_aetherdesk_427_payloads.ps1',raw,sha(raw)))
 tests=[]
 for name,count in [('execution-foundation-r3-v2-tests-final.xml',39),('foundation-continuation-wrapper-tests-final.xml',45),('return-continuation-r3-v2-tests-final.xml',24)]:
  node=ET.parse(W/name).getroot().find('testsuite');a=node.attrib
  if int(a['tests'])!=count or any(int(a[k]) for k in ('failures','errors','skipped')):raise ValueError('Tests did not pass')
  tests.append({'path':name,'passed':count,'failures':0,'errors':0,'skipped':0,'junit_seconds':a['time'],'sha256':PINS[name]})
 preview=json.loads((W/'return-continuation-r3-v2-preview-final.json').read_text(encoding='utf-8-sig'))
 if preview['status']!='READ_ONLY_PLAN' or [x['phase'] for x in preview['results']]!=['foundation','project']:raise ValueError('Preview differs')
 if preview['results'][0]['report']['holds']!=[] or preview['results'][1]['report']['holds']!=['Data-parent custody requires the owner Administrator token.']:raise ValueError('Preview holds differ')
 old_guide=(W/'RETURN_SETUP.txt').read_bytes()
 command=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -File "'+str(W/'run-return-continuation-r3-v2.ps1')+'" -Apply'
 report={'schema':'cochem-foundation-continuation-preparation/2','status':'PREPARED_NOT_EXECUTED','platform':'Windows ordinary user','source_records':[{'path':name,'sha256':pin,'bytes':len(raw)} for name,raw,pin in records],
  'tests':tests,'ordinary_preparation_tests_passed':108,'independent_reviews':['return-continuation-r3-v2-independent-review.json','foundation-continuation-r3-v2-install-repairs-review.json'],
  'actual_system_diagnostic':{'receipt_sha256':'cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9','status':'FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED','trace_events':18,'read_only_docker_commands_returned':4,'provisioning_performed':False,'archived_evidence':'../execution-foundation-diagnostic-system-2026-10-07/summary.json'},
  'authority':{'preserved_original_failed_foundation':'bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336','original_failure_cause_established':False},
  'default_preview':{'foundation_holds':[],'project_holds':['Data-parent custody requires the owner Administrator token.'],'prior_task_receipt_checks_deferred_to_administrator':True,'current_state_and_provisioning_deferred_to_system':True},
  'proposed_write_set':['one fresh protected v2 helper/input/receipt root and no-trigger SYSTEM task','seven new scoped RAM directories, ACL/no-content-index flags, only missing six Defender exclusions','new private adopted-R ledger and empty four-slot container registry','separate new private disposable bare Git project from frozen bundle'],
  'capacity':{'worker_identities':6,'shared_slots':4,'chapter06_routes_unchanged':True},'apply_performed':False,'pipeline_started':False,'activation_ready':False,'automatic_retry_allowed':False,'owner_command':command,
  'limitations':['Preparation tests replace native boundaries; they are not privileged deployment acceptance.','Current historical task state is rechecked by the future Administrator wrapper, not inferred from an ordinary process.','No Docker physical acceptance, provider sign-in/model calls, budget reconciliation or sustained monitoring completed here.','Historical Linux results remain separate.'],'guide_before_sha256':sha(old_guide)}
 ARCHIVE.mkdir(exist_ok=False)
 for name,raw,pin in records:create(ARCHIVE/name,raw)
 create(ARCHIVE/'RETURN_SETUP.before-continuation.txt',old_guide)
 create(ARCHIVE/'preparation.json',(json.dumps(report,indent=2)+'\n').encode())
 print(json.dumps({'archive':str(ARCHIVE/'preparation.json'),'sha256':sha((ARCHIVE/'preparation.json').read_bytes()),'ordinary_tests_passed':108}))
if __name__=='__main__':main()
