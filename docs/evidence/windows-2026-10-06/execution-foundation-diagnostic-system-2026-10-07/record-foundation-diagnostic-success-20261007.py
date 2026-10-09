"""Exact existing receipt archival and documentation only; no deployment calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE=REPO/'docs/evidence/windows-2026-10-06'
ROOT=Path(r'C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1')
ARCHIVE=EVIDENCE/'execution-foundation-diagnostic-system-2026-10-07'
RECEIPT_SHA='cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9'
PACKET_SHA='8744b6a0b569b820404734a491dd7b1671ab2a439ccc27f7251dccbf75a7550d'
HELPER_SHA='fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5'
PREFLIGHT_SHA='3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'
FAILED_SHA='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'
FAILED_PACKET_SHA='a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6'
INSTALL_SHA='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def read_pin(path,pin):
    with path.open('rb') as f:raw=f.read(1048577)
    assert len(raw)<=1048576 and sha(raw)==pin,path.name
    return raw,json.loads(raw)


def create(name,raw):
    with (ARCHIVE/name).open('xb') as f:f.write(raw)


def json_bytes(value):return (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode('utf-8')


def main():
    raw,v=read_pin(ROOT/'foundation-diagnostic.json',RECEIPT_SHA)
    packet_raw,packet=read_pin(ROOT/'inputs.json',PACKET_SHA)
    old=EVIDENCE/'execution-foundation-failure-2026-10-07'
    _,pre=read_pin(old/'execution-prerequisites.json',PREFLIGHT_SHA)
    _,failed=read_pin(old/'execution-foundation.json',FAILED_SHA)
    _,old_packet=read_pin(old/'foundation-inputs.json',FAILED_PACKET_SHA)
    assert v['schema']=='cochem-foundation-diagnostic/1' and v['status']=='FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED'
    assert v['nonce']=='3e2e411d9c274167a703a4308404dbe5' and v['system_sid']=='S-1-5-18'
    assert v['helper_sha256']==HELPER_SHA and sha((ROOT/'diagnose-foundation-docker-r3-v1.py').read_bytes())==HELPER_SHA
    assert v['packet_sha256']==PACKET_SHA and v['install_receipt_sha256']==INSTALL_SHA
    assert v['preflight_receipt_sha256']==PREFLIGHT_SHA and v['failed_foundation_receipt_sha256']==FAILED_SHA and v['failed_foundation_packet_sha256']==FAILED_PACKET_SHA
    assert v['runtime_root']==pre['runtime_root']==failed['runtime_root'] and v['revision']==pre['revision']==failed['revision']
    assert packet==dict(old_packet,schema='cochem-foundation-diagnostic-inputs/1',failed_foundation_receipt_sha256=FAILED_SHA,failed_foundation_packet_sha256=FAILED_PACKET_SHA)
    assert v['worker_receipt_sha256']==packet['workers']==pre['worker_receipt_sha256']
    assert v['diagnostic_only'] is True and v['preservation_after_observation_verified'] is True
    assert v['historical_exact_failure_unrecoverable_from_original_receipt'] is True
    for flag in ('activation_ready','ram_provision_started','registry_provision_started','existing_files_or_acls_modified','existing_databases_modified','existing_tasks_modified_or_run','knowledge_service_constructed','docker_runner_constructed','ram_ensure_called','native_provider_authentication_performed','automatic_retry_performed'):
        assert v[flag] is False,flag
    assert type(v['native_model_jobs_executed']) is int and v['native_model_jobs_executed']==0
    assert 'failure' not in v and 'preservation_failure' not in v
    trace=v['docker_trace'];assert len(trace['events'])==18 and trace['commands_started']==trace['commands_returned']==4
    assert trace['last_stage']=='docker_pipe_denials_after' and trace['retry_attempts']==0 and trace['original_guard_decisions_unchanged'] is True
    assert all(event['completed'] is True and 'failure' not in event for event in trace['events'])
    commands=[event for event in trace['events'] if event['kind']=='bounded_cli']
    assert [e['stage'] for e in commands]==['docker_info','docker_image','docker_owner_census','docker_name_census']
    assert all(e['position']=='single_attempt' for e in commands)
    assert v['docker']['commands_executed']==4 and v['docker']['containers_created_or_modified'] is False
    assert v['docker']['ownership_census']['empty'] is True and v['docker']['ownership_census']['union_count']==0
    assert v['docker']['worker_access_denied_before_and_after'] is True and v['docker']['workers_per_alias']==6
    for key in ('server','engine','api_aliases'):assert v['docker'][key]==pre['docker'][key]
    assert v['ram_before']==v['ram_after']==pre['ram_after']
    assert v['fresh_state_before']==v['fresh_state_after']==pre['fresh_state_after']
    assert all(value is True for value in v['fresh_state_after'].values())
    assert type(v['docker']['server']['process_created_filetime']) is int and v['docker']['server']['process_created_filetime']==134357190020978742
    assert type(v['ram_before']['root_device']) is int and v['ram_before']['root_device']==18215568536144889826
    safe={'schema':'cochem-foundation-diagnostic-task-result/1','status':v['status'],'receipt_path':str(ROOT/'foundation-diagnostic.json'),'receipt_sha256':RECEIPT_SHA,
        'last_task_result':0,'failed_foundation_receipt_sha256':FAILED_SHA,'preflight_receipt_sha256':PREFLIGHT_SHA,'activation_ready':False,
        'provisioning_performed':False,'existing_state_modified':False,'automatic_retry_allowed':False,
        'docker_trace':{'last_stage':trace['last_stage'],'events_recorded':len(trace['events']),'failed_events':[],'commands_started':4,'commands_returned':4,'retry_attempts':0,'original_guard_decisions_unchanged':True},
        'preservation_after_observation_verified':True}
    observed_path=REPO/'config/windows/aetherdesk-427.observed-20261007.json';handoff_path=REPO/'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
    old_observed=observed_path.read_bytes();old_handoff=handoff_path.read_bytes();observed=json.loads(old_observed)
    ARCHIVE.mkdir(exist_ok=False)
    for name,data in [('foundation-diagnostic.json',raw),('inputs.json',packet_raw),('safe-owner-result.json',json_bytes(safe)),('observed-before.json',old_observed),('handoff-before.md',old_handoff),('record-foundation-diagnostic-success-20261007.py',Path(__file__).read_bytes())]:create(name,data)
    now=datetime.now(timezone.utc).isoformat();relative=ARCHIVE.relative_to(REPO).as_posix()
    summary={'schema':'cochem-foundation-diagnostic-system-observation/1','captured_utc':now,'host':'AETHERDESK','status':v['status'],
        'receipt_sha256':RECEIPT_SHA,'inputs_sha256':PACKET_SHA,'safe_owner_result_sha256':sha(json_bytes(safe)),
        'scope':'Ordinary-user exact bounded reads of existing public SYSTEM receipt/input metadata and helper bytes; no task, Docker, provider or provisioning executed by this collector.',
        'safe_owner_result_provenance':'Allowlisted projection reconstructed from the exact protected receipt. Task result0 is owner-wrapper-reported, not read from COM by this collector; this is not a captured raw console transcript.',
        'task_evidence':{'old_foundation_result_verified_by_owner_wrapper':2,'new_diagnostic_result_verified_by_owner_wrapper':0,'current_com_task_state_directly_observed_by_collector':False,'ordinary_com_access':'DENIED_REPORTED_BY_PARENT'},
        'bindings':{'nonce':v['nonce'],'helper_sha256':HELPER_SHA,'runtime_root':v['runtime_root'],'install_receipt_sha256':INSTALL_SHA,'revision':v['revision'],'preflight_receipt_sha256':PREFLIGHT_SHA,'failed_foundation_receipt_sha256':FAILED_SHA,'failed_foundation_packet_sha256':FAILED_PACKET_SHA,'all_six_worker_receipt_hashes_match':True},
        'observations':{'events':18,'read_only_docker_commands':4,'failed_events':0,'preservation_verified':True,'ram_provision_started':False,'registry_provision_started':False,'scoped_owned_container_count':0,'docker_server':v['docker']['server'],'ram_before':v['ram_before'],'ram_after':v['ram_after'],'fresh_state_before':v['fresh_state_before'],'fresh_state_after':v['fresh_state_after'],'started_at_unix_ms':v['started_at_unix_ms'],'finished_at_unix_ms':v['finished_at_unix_ms']},
        'historical_failure':{'preserved_unchanged':True,'cause':'UNKNOWN; the sanitized original receipt cannot recover the exact historical failing guard. The later successful observation does not retrospectively diagnose or erase the failure.'},
        'next_action':'A fresh separately reviewed foundation continuation is pending. Do not rerun the old return series, failed foundation, successful inspection or diagnostic. No provisioning/activation authorized by this observation.',
        'exact_integer_handling':'Python arbitrary-precision parsing; original receipt/input bytes copied unchanged; no JavaScript Number conversion.',
        'native_model_jobs_executed_by_collector':0,'pipeline_activated':False,'linux_evidence_used':False}
    summary['files']={p.name:{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size} for p in ARCHIVE.iterdir() if p.is_file()}
    summary_raw=json_bytes(summary);create('summary.json',summary_raw)
    actual={'status':v['status'],'actual_system_receipt':True,'receipt_evidence':relative+'/foundation-diagnostic.json','receipt_sha256':RECEIPT_SHA,'input_packet_sha256':PACKET_SHA,
        'nonce':v['nonce'],'helper_sha256':HELPER_SHA,'runtime_root':v['runtime_root'],'install_receipt_sha256':INSTALL_SHA,'finished_at_unix_ms':v['finished_at_unix_ms'],
        'docker_trace_events':18,'read_only_docker_commands':4,'failed_trace_events':0,'preservation_after_observation_verified':True,
        'ram_provision_started':False,'registry_provision_started':False,'original_foundation_failure_cause':'UNKNOWN','original_failure_evidence_preserved':True,
        'owner_wrapper_verified_old_foundation_task_result':2,'owner_wrapper_verified_diagnostic_task_result':0,'current_com_task_state_independently_verified':False,
        'task_state_provenance':'Owner wrapper reports; ordinary collector did not directly observe current COM task state.',
        'observation_evidence':relative+'/summary.json','observation_sha256':sha(summary_raw),'automatic_retry_allowed':False,'foundation_continuation_pending':True}
    observed['actual_windows']['foundation_docker_diagnostic_r3_v1']=actual
    observed['status']='STOPPED_PIPELINE_R3_FOUNDATION_DIAGNOSTIC_PASSED_CONTINUATION_PENDING';observed['latest_owner_result_recorded_utc']=now
    prepared=observed['prepared_not_applied']['foundation_docker_diagnostic_r3_v1']
    prepared.update(status='OWNER_EXECUTED_READ_ONLY_DIAGNOSTIC_VERIFIED',owner_action_status='COMPLETED_NO_PROVISIONING',privileged_phase_executed=True,actual_result_evidence=relative+'/summary.json',actual_result_sha256=sha(summary_raw),automatic_retry_allowed=False)
    observed['return_instructions'].update(current_action='Fresh reviewed foundation continuation pending; do not rerun the old series or successful diagnostic',current_diagnostic_status='OWNER_REPORTED_VERIFIED',next_read_only_diagnostic_pending=False,foundation_continuation_pending=True,current_sequence_must_not_be_followed=True,all_later_steps_held=True)
    observed['remaining'][0]='The new SYSTEM diagnostic passed18events/4read-onlyDockercommands with preservation verified. Preserve it and the original failure; await a fresh reviewed foundation continuation. Do not rerun either earlier phase or the old return series.'
    observed['remaining'][1]='Complete separately reviewed scoped RAM/empty registry continuation and protected disposable project setup before physical Docker RED/GREEN acceptance. The successful diagnostic performed no provisioning; the original failure cause remains unknown.'
    top='''## Current state - diagnostic passed, foundation continuation pending

The new actual SYSTEM diagnostic returned
`FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED`: all 18 trace events completed,
four fixed read-only Docker commands returned, no trace event failed, and
before/after preservation checks passed. It performed no RAM or registry
provisioning and created no containers. The observed scoped Docker census remains
empty; the six worker pipe-denial checks and original R: baseline were preserved.

The [exact diagnostic receipt, inputs and safe result](evidence/windows-2026-10-06/execution-foundation-diagnostic-system-2026-10-07/summary.json)
bind receipt SHA256
`cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9`.
The owner wrapper verified original foundation task result 2 and new diagnostic
task result 0. This ordinary archival collector did not directly observe current
COM task state because that access was reported denied. The safe result is an
allowlisted reconstruction from the exact receipt plus the owner-reported task
result, not a captured console transcript. FILETIME/device/file IDs remain exact.

The [original failure](evidence/windows-2026-10-06/execution-foundation-failure-2026-10-07/summary.json)
is unchanged: foundation held at `docker_owner_census` with
`WindowsIsolationError`, before RAM/registry provisioning, and project import
was not reached. Its precise historical cause remains unknown. A later successful
observation does not identify that missing error detail or erase the failed attempt.

**Do not rerun the old return series, failed foundation, successful inspection or
successful diagnostic.** Preserve their roots, tasks, input packets and receipts.
A fresh separately reviewed foundation continuation is being prepared; its owner
command is pending. Project import, physical Docker acceptance, the legacy
diagnostic and attended provider sign-ins remain held pending the revised sequence.
The existing `RETURN_SETUP.txt` has not been changed by this archival step.

The pipeline remains stopped. The installed r3 runtime, published knowledge and
six worker-denial passes remain valid historical evidence. Six identities share
**four** slots, all model jobs retain Chapter 06 routing, and existing credentials,
databases, repair budgets, the 8 GiB R: drive and its startup task are preserved.
This archival step invoked no task, Docker/provider command or deployment.

The [previous handoff](evidence/windows-2026-10-06/execution-foundation-diagnostic-system-2026-10-07/handoff-before.md)
is retained. Preparation tables below describe their earlier preparation-time
state; the owner outcomes above are current and do not authorize activation.

'''
    # Byte concatenation preserves the existing UTF-8 prefix/suffix and CRLF
    # exactly instead of round-tripping already decoded CRLF through write_text.
    start=old_handoff.index(b'## Current state');end=old_handoff.index(b'## Actual Windows deployment evidence')
    updated_handoff=old_handoff[:start]+top.replace('\n','\r\n').encode('utf-8')+old_handoff[end:]
    assert observed_path.read_bytes()==old_observed and handoff_path.read_bytes()==old_handoff,'Concurrent documentation update; preserve new archive for review.'
    observed_path.write_bytes(json_bytes(observed));handoff_path.write_bytes(updated_handoff)
    assert handoff_path.read_bytes()[handoff_path.read_bytes().index(b'## Actual Windows deployment evidence'):]==old_handoff[end:]
    assert sha((ROOT/'foundation-diagnostic.json').read_bytes())==RECEIPT_SHA
    print(json.dumps({'archive':str(ARCHIVE),'summary_sha256':sha(summary_raw),'receipt_sha256':RECEIPT_SHA,'inputs_sha256':PACKET_SHA,'safe_owner_result_sha256':sha(json_bytes(safe)),'observed_sha256':sha(observed_path.read_bytes()),'handoff_sha256':sha(handoff_path.read_bytes()),'handoff_suffix_preserved':True,'raw_receipt_unchanged':True},indent=2))


if __name__=='__main__':main()
