"""Archive existing public receipts and update handoff only; no deployment calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
BASE=Path(r'C:\Program Files\CoChem')
ARCHIVE=REPO/'docs/evidence/windows-2026-10-06/execution-foundation-failure-2026-10-07'
INSTALL=BASE/'Pipeline4.2.7-windows-20261007-r3'
INSTALL_SHA='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
PREFLIGHT_SHA='3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'
FOUNDATION_SHA='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'
CONFIG_SHA='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
MANIFEST_SHA='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
KNOWLEDGE_SHA='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
INDEX_SHA='af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(path, expected, maximum=1048576):
    with path.open('rb') as source:
        raw=source.read(maximum+1)
    if len(raw)>maximum or sha(raw)!=expected:
        raise ValueError(f'Exact bounded evidence bytes differ: {path.name}')
    return raw,json.loads(raw)


def create(name, raw):
    with (ARCHIVE/name).open('xb') as out:
        out.write(raw)


def main():
    ip=BASE/'ExecutionPrerequisites4.2.7-windows-20261007-r3'
    fp=BASE/'ExecutionFoundation4.2.7-windows-20261007-r3'
    iraw,i=pinned(ip/'execution-prerequisites.json',PREFLIGHT_SHA)
    fraw,f=pinned(fp/'execution-foundation.json',FOUNDATION_SHA)
    install_raw,install=pinned(INSTALL/'install-after.json',INSTALL_SHA)
    _,config=pinned(INSTALL/'pipeline.json',CONFIG_SHA)
    _,manifest=pinned(INSTALL/'source-manifest.json',MANIFEST_SHA)
    assert len(config['workers'])==6 and config['max_execution_slots']==4
    assert len(manifest['files'])==166
    revision=install['verification']['revision']
    assert revision['files']==109 and revision['source_sha256']=='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
    expected_helpers={ip:('inspect-execution-prerequisites-r3.py','17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'),fp:('provision-execution-foundation-r3.py','fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1')}
    for root,value in ((ip,i),(fp,f)):
        assert value['runtime_root']==str(INSTALL) and value['install_receipt_sha256']==INSTALL_SHA
        assert value['system_sid']=='S-1-5-18' and value['revision']==revision
        assert value['activation_ready'] is False and value['native_model_jobs_executed']==0
        assert re.fullmatch('[a-f0-9]{32}',value['nonce'])
        assert type(value['started_at_unix_ms']) is int and type(value['finished_at_unix_ms']) is int
        assert value['started_at_unix_ms']<=value['finished_at_unix_ms']
        filename,pin=expected_helpers[root]
        assert value['helper_sha256']==pin and sha((root/filename).read_bytes())==pin
    assert i['schema']=='cochem-execution-prerequisites/1' and i['status']=='READ_ONLY_PREREQUISITES_VERIFIED'
    assert i['nonce']=='fc64e3d3990049ed809b7189906dd0e9' and i['holds']==[]
    assert i['fresh_state_before']==i['fresh_state_after']==dict(container_registry_root_absent=True,ram_ledger_absent=True,ram_workspace_absent=True,readiness_receipt_absent=True)
    assert i['ram_before']==i['ram_after'] and i['ram_before']['ram_modified'] is False
    assert i['ram_before']['volume']['size_bytes']==8589934592 and i['ram_before']['filesystem_bytes']==8589930496
    assert i['ram_before']['task_xml_sha256']=='1da7687a165ddfb425cb01147f4f5cbd3f7ba035947063ef396e268d7babc928'
    assert i['docker']['ownership_census']['empty'] is True and i['docker']['ownership_census']['union_count']==0
    assert i['docker']['workers_per_alias']==6 and i['docker']['worker_access_denied_before_and_after'] is True
    assert i['docker']['commands_executed']==4 and i['docker']['containers_created_or_modified'] is False
    assert i['knowledge']['receipt_sha256']==KNOWLEDGE_SHA and i['knowledge']['index_sha256']==INDEX_SHA
    assert i['knowledge']['current_bytes_unchanged'] is True
    for key in ('existing_databases_modified','existing_files_or_acls_modified','existing_tasks_modified_or_run','knowledge_service_constructed','ram_ensure_called','docker_runner_constructed','native_provider_authentication_performed'):
        assert i[key] is False
    assert f['schema']=='cochem-execution-foundation/1' and f['status']=='EXECUTION_FOUNDATION_HELD'
    assert f['nonce']=='a92fe71e71224573a3738b5131ecfb36'
    assert f['failure']==dict(phase='docker_owner_census',error_type='WindowsIsolationError',winerror=None)
    assert f['preflight_receipt_sha256']==PREFLIGHT_SHA and f['knowledge_index_sha256']==INDEX_SHA
    for key in ('ram_provision_started','registry_provision_started','ram_volume_or_startup_task_modified','credentials_or_native_profiles_modified','legacy_databases_or_budgets_modified','existing_tasks_modified_or_run','knowledge_service_constructed_or_refreshed','general_readiness_called','automatic_retry_allowed'):
        assert f[key] is False
    assert f['containers_created']==f['warm_pool_created']==0
    assert f['partial_outputs_preserved'] is True and f['operator_review_required'] is True
    ipraw,packet_i=pinned(ip/'inputs.json',i['packet_sha256'],32768)
    fpraw,packet_f=pinned(fp/'inputs.json',f['packet_sha256'],32768)
    assert packet_i['schema']=='cochem-execution-prerequisites-inputs/1'
    assert packet_f==dict(packet_i,schema='cochem-execution-foundation-inputs/1',preflight_receipt_sha256=PREFLIGHT_SHA)
    assert packet_i['install_receipt_sha256']==INSTALL_SHA and packet_i['knowledge_receipt_sha256']==KNOWLEDGE_SHA
    worker_summary=json.loads((REPO/'docs/evidence/windows-2026-10-06/worker-denials-r3-system-2026-10-07/summary.json').read_bytes())
    workers={row['slot']:row['receipt_sha256'] for row in worker_summary['receipts']}
    assert packet_i['workers']==i['worker_receipt_sha256']==workers and len(workers)==6
    # Python parses and reserializes arbitrary-size integers exactly. Raw receipt
    # bytes are archived separately and remain the authoritative byte commitment.
    assert type(i['docker']['server']['process_created_filetime']) is int
    assert i['docker']['server']['process_created_filetime']==134357190020978742
    assert type(i['ram_before']['root_device']) is int and i['ram_before']['root_device']==18215568536144889826
    observed_path=REPO/'config/windows/aetherdesk-427.observed-20261007.json'
    handoff_path=REPO/'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
    observed_before=observed_path.read_bytes();handoff_before=handoff_path.read_bytes()
    observed=json.loads(observed_before);handoff=handoff_before.decode('utf-8')
    assert '## Current state and owner return' in handoff and '## Actual Windows deployment evidence' in handoff
    ARCHIVE.mkdir(exist_ok=False)
    for name,raw in [('execution-prerequisites.json',iraw),('execution-foundation.json',fraw),('inspection-inputs.json',ipraw),('foundation-inputs.json',fpraw),('observed-before.json',observed_before),('handoff-before.md',handoff_before),('record-execution-foundation-failure-20261007.py',Path(__file__).read_bytes())]:
        create(name,raw)
    relative=ARCHIVE.relative_to(REPO).as_posix()
    now=datetime.now(timezone.utc).isoformat()
    summary={'schema':'cochem-execution-foundation-failure-observation/1','captured_utc':now,'host':'AETHERDESK','scope':'Ordinary-user bounded exact-byte reads of existing public protected receipts, safe metadata-only input packets, helper pins and installed configuration/revision. No helper imports or entrypoints, tasks, Docker commands, state constructors, credentials or database contents opened.','status':'INSPECTION_PASSED_FOUNDATION_HELD_BEFORE_RAM_AND_REGISTRY','inspection':{'status':i['status'],'receipt_sha256':PREFLIGHT_SHA,'nonce':i['nonce'],'started_at_unix_ms':i['started_at_unix_ms'],'finished_at_unix_ms':i['finished_at_unix_ms'],'docker_fixed_read_only_commands':4,'scoped_owned_container_count':0,'worker_handle_denials_before_and_after':True,'ram_before':i['ram_before'],'ram_after':i['ram_after'],'docker_server':i['docker']['server'],'knowledge_bytes_preserved':True},'foundation':{'status':f['status'],'receipt_sha256':FOUNDATION_SHA,'nonce':f['nonce'],'started_at_unix_ms':f['started_at_unix_ms'],'finished_at_unix_ms':f['finished_at_unix_ms'],'failure':f['failure'],'ram_provision_started':False,'registry_provision_started':False,'containers_created':0,'warm_pool_created':0,'project_phase':'NOT_REACHED_IN_REPORTED_OWNER_SERIES','owner_wrapper_reported_last_task_result':2,'last_task_result_provenance':'Owner wrapper result relayed in the task; not independently read from Task Scheduler by this archival collector.','direct_current_task_terminal_state_verified':False,'direct_task_state':'UNKNOWN_ORDINARY_COM_ACCESS_DENIED_REPORTED_BY_PARENT','failure_cause_beyond_sanitized_phase':'NOT_ESTABLISHED_BY_THIS_ARCHIVAL_READ'},'bindings':{'install_receipt_sha256':INSTALL_SHA,'configuration_sha256':CONFIG_SHA,'source_manifest_sha256':MANIFEST_SHA,'revision':revision,'knowledge_receipt_sha256':KNOWLEDGE_SHA,'index_sha256':INDEX_SHA,'all_six_worker_receipt_pins_match_archived_acceptance':True,'protected_helper_bytes_match_frozen_pins':True,'input_packet_bytes_match_receipt_pins':True,'task_action_nonce_terminal_binding_rechecked_by_this_collector':False},'preservation':{'original_receipts_copied_byte_for_byte':True,'integer_precision':'Python arbitrary-precision integers; original FILETIME/device/file IDs retained without JavaScript Number conversion.','protected_deployment_artifacts_modified_by_archival':False,'ram_volume_or_startup_task_modified_by_failed_foundation_reported':False,'credentials_databases_budgets_changed':False,'automatic_retry_allowed':False,'later_return_steps_held':True,'linux_results_used':False,'pipeline_activated':False},'next_action':'A separately reviewed read-only diagnostic is being prepared. Do not rerun the original fresh-only return series or either completed/failed leaf; preserve all roots/tasks/receipts. Project/Docker acceptance/legacy diagnostic/attended login commands from the prior return sequence remain held.'}
    summary['files']={p.name:{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size} for p in ARCHIVE.iterdir() if p.is_file()}
    summary_raw=(json.dumps(summary,indent=2)+'\n').encode();create('summary.json',summary_raw)
    observed['status']='STOPPED_PIPELINE_R3_INSPECTION_PASSED_FOUNDATION_HELD'
    observed['latest_owner_result_recorded_utc']=now
    observed['actual_windows']['execution_prerequisites_r3']={'status':i['status'],'actual_system_receipt':True,'receipt_evidence':relative+'/execution-prerequisites.json','receipt_sha256':PREFLIGHT_SHA,'nonce':i['nonce'],'finished_at_unix_ms':i['finished_at_unix_ms'],'runtime_root':str(INSTALL),'install_receipt_sha256':INSTALL_SHA,'docker_read_only_commands_executed':4,'scoped_owned_container_count':0,'six_worker_pipe_access_denied_before_and_after':True,'ram_before_after_equal':True,'existing_files_databases_tasks_modified':False,'current_task_terminal_state_independently_verified':False}
    observed['actual_windows']['execution_foundation_r3']={'status':f['status'],'actual_system_receipt':True,'receipt_evidence':relative+'/execution-foundation.json','receipt_sha256':FOUNDATION_SHA,'nonce':f['nonce'],'finished_at_unix_ms':f['finished_at_unix_ms'],'failure':f['failure'],'preflight_receipt_sha256':PREFLIGHT_SHA,'ram_provision_started':False,'registry_provision_started':False,'project_phase_started_in_reported_series':False,'containers_created':0,'owner_wrapper_reported_last_task_result':2,'task_result_provenance':'Owner wrapper report; ordinary archival collector cannot directly verify current Task Scheduler terminal state.','current_task_terminal_state_independently_verified':False,'automatic_retry_allowed':False,'observation_evidence':relative+'/summary.json','observation_sha256':sha(summary_raw)}
    prepared=observed['prepared_not_applied']
    prepared['return_setup_series_r3'].update(status='OWNER_EXECUTED_HELD_AT_FOUNDATION',privileged_phase_executed=True,completed_phases=['inspection'],failed_phase='foundation',project_phase_started=False,automatic_retry_allowed=False,actual_result_evidence=relative+'/summary.json')
    prepared['execution_prerequisites_r3'].update(owner_action_status='OWNER_EXECUTED_READ_ONLY_PREREQUISITES_VERIFIED',actual_system_preflight_executed=True,actual_result_evidence=relative+'/execution-prerequisites.json')
    foundation=prepared['execution_foundation_r3']
    if 'preview_hold' in foundation:foundation['historical_preparation_preview_hold']=foundation.pop('preview_hold')
    foundation.update(owner_action_status='OWNER_EXECUTED_HELD_BEFORE_RAM_AND_REGISTRY',helper_executed_as_system=True,provisioning_executed=False,ram_provision_started=False,registry_provision_started=False,automatic_retry_allowed=False,actual_result_evidence=relative+'/execution-foundation.json')
    prepared['disposable_project_importer']['owner_action_status']='NOT_REACHED_AFTER_FOUNDATION_FAILURE'
    for key in ('docker_physical_r3_v1','legacy_continuity_diagnostic','attended_native_series_r3'):
        prepared[key]['owner_action_status']='HELD_AFTER_RETURN_FOUNDATION_FAILURE'
    observed['return_instructions'].update(status='PREVIOUS_SEQUENCE_HELD_NO_RERUN',current_sequence_must_not_be_followed=True,all_later_steps_held=True,next_read_only_diagnostic_pending=True,existing_guide_hash_is_historical=True,automatic_retries=False)
    observed['remaining'][0]='Preserve passed inspection and failed foundation artifacts; wait for the separately reviewed read-only foundation diagnostic. Never rerun the old fresh-only return series or skip to later commands.'
    observed['remaining'][1]='After diagnosis and separately reviewed recovery, finish scoped RAM/empty registry and protected disposable project setup before physical Docker RED/GREEN acceptance. Neither provisioning step nor project import was reached in the failed owner series.'
    top='''## Current state — return sequence held

The owner's first return command passed the actual SYSTEM Docker/R inspection,
then stopped in the foundation task at `docker_owner_census` with
`WindowsIsolationError` (Win32 error unavailable). Its receipt is
`EXECUTION_FOUNDATION_HELD`: RAM provisioning and registry provisioning had not
started, and the project-import phase was not reached. No container or warm pool
was created. This is a failed setup attempt, not a successful three-phase result.

**Do not rerun `run-return-setup-r3.ps1`, either original leaf, or any later
command in the previous return guide.** Preserve both roots, tasks, input packets
and receipts. A separate read-only diagnostic is being prepared; its reviewed
owner command is pending. Docker acceptance, the legacy diagnostic and both
attended login series from the previous command list remain held.

The [exact archived receipts and binding checks](evidence/windows-2026-10-06/execution-foundation-failure-2026-10-07/summary.json)
record inspection SHA256
`3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e`
and failed foundation SHA256
`bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336`.
The passed inspection observed an empty scoped Docker resource census, denied
all six worker tokens access to the checked Docker pipe aliases before and after,
and preserved the 8 GiB R: volume and startup-task fingerprint. That prior pass
does not override the later foundation failure or establish its precise cause.

The owner wrapper reported foundation task result 2. Current task terminal state
was **not independently verified** by this ordinary-user collector: Task Scheduler
access was reported denied. The archived receipt binds its SYSTEM identity,
nonce, helper, input packet, installed r3 revision and successful inspection.
Original FILETIME/device/file identifiers remain exact Python integers and the
original receipt bytes are retained unchanged.

The pipeline remains stopped. The repaired r3 runtime, published knowledge
verification and six successful worker-denial receipts remain valid historical
evidence. Six identities share **four** slots; model jobs retain Chapter 06 routing.
Existing credentials, databases, repair budgets, R: and its startup task remain
preserved. This archival step ran no tasks, Docker/provider commands or deployment.

The [previous handoff](evidence/windows-2026-10-06/execution-foundation-failure-2026-10-07/handoff-before.md)
and [old return guide](evidence/windows-2026-10-06/return-setup-owner-guide-2026-10-07.txt)
are historical references, not instructions to retry or continue. The preparation
table below retains its pre-attempt history; the owner outcomes above are current.

'''
    begin=handoff.index('## Current state and owner return');end=handoff.index('## Actual Windows deployment evidence')
    handoff=handoff[:begin]+top+handoff[end:]
    # Preserve the rest of the handoff verbatim. The table describes preparation
    # history; the current owner outcomes are explicitly recorded above.
    assert observed_path.read_bytes()==observed_before and handoff_path.read_bytes()==handoff_before, 'Documentation changed during archival; preserve archive and review before writing.'
    observed_path.write_text(json.dumps(observed,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    handoff_path.write_text(handoff,encoding='utf-8')
    print(json.dumps({'archive':str(ARCHIVE),'summary_sha256':sha(summary_raw),'inspection_sha256':sha(iraw),'foundation_sha256':sha(fraw),'observed_sha256':sha(observed_path.read_bytes()),'handoff_sha256':sha(handoff_path.read_bytes()),'original_filetime_preserved':i['docker']['server']['process_created_filetime'],'protected_sources_unmodified':sha((ip/'execution-prerequisites.json').read_bytes())==PREFLIGHT_SHA and sha((fp/'execution-foundation.json').read_bytes())==FOUNDATION_SHA},indent=2))


if __name__=='__main__':
    main()
