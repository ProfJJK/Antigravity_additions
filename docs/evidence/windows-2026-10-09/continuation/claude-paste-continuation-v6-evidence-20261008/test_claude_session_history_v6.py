"""V5 custody fixtures plus narrow cancelled/deadline receipt cases; all inert."""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import queue
from types import SimpleNamespace

import pytest

W=Path(__file__).resolve().parent
SOURCE=W/'claude-session-history-v6.ps1'
FROZEN=W/'claude-session-history-v5.ps1'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


base=load('claude_history_v6_baseline',W/'test_claude_session_history_v5.py')
base.HELPER=SOURCE
for name in dir(base):
    if name.startswith('test_'):globals()['test_baseline_'+name[5:]]=getattr(base,name)

support=load('claude_history_v6_support_fixture',W/'worker-native-status-r3.py')
bridge=load('claude_history_v6_bridge_fixture',W/'worker_claude_login_bridge_r3.py')


def failed_wait(kind='cancel',submitted=False):
    value=base.receipt()
    value.update(status='LOGIN_FAILED_OR_CANCELLED',operator_cancelled=kind=='cancel',
                 one_line_submitted=submitted,input_file_deleted=kind=='cancel' or submitted,
                 failure=dict(phase='native_wait',error_type='InterruptedError' if kind=='cancel' else 'TimeoutError',winerror=None))
    del value['native_exit_code']
    return value


def evaluate(value,last=2,extra=''):
    code=base.task_code(last)+f"$receipt={base.literal(value)};$runtime={base.literal(base.runtime())};"+extra
    code+=f"$ok=$true;$proof=$null;$safe=$null;try{{Assert-ClaudeHistoryTask $task '{base.ROOT}' slot1 '{base.NONCE}';$proof=Assert-ClaudeHistoryReceipt $receipt $task slot1 '{base.NONCE}' $runtime '{base.SID}'}}catch{{$ok=$false;$safe=$_.Exception.Data['CoChemClaudeHistorySafeMetadata']}};[ordered]@{{accepted=$ok;proof=$proof;safe=$safe}}|ConvertTo-Json -Depth 5 -Compress"
    text=base.ps(code);assert 'synthetic-private-value' not in text
    return json.loads(text)


@pytest.mark.parametrize('kind,submitted',[('cancel',False),('cancel',True),('timeout',False),('timeout',True)])
def test_reviewed_cancel_or_timeout_with_complete_terminal_cleanup_is_history_only(kind,submitted):
    result=evaluate(failed_wait(kind,submitted))
    assert result['accepted'] is True
    assert result['proof']['classification']==('TERMINAL_CANCELLED_NATIVE_LOGIN_CLEANUP_VERIFIED' if kind=='cancel' else 'TERMINAL_TIMED_OUT_NATIVE_LOGIN_CLEANUP_VERIFIED')
    assert result['proof']['cleanup_verified'] is True and result['proof']['login_commands_executed']==1
    assert result['proof']['model_jobs_executed']==0 and result['proof']['authentication_authorized_by_history'] is False


@pytest.mark.parametrize('kind,key,changed', [
    ('cancel','cleanup_verified',False),('cancel','cleanup_verified',1),('cancel','status','CLEANUP_UNVERIFIED'),
    ('cancel','status','LOGIN_COMMAND_FAILED'),('cancel','operator_cancelled',False),('cancel','operator_cancelled',1),
    ('cancel','input_file_deleted',False),('cancel','input_file_deleted',1),('cancel','one_line_submitted',1),
    ('cancel','failure.phase','native_cleanup'),('cancel','failure.phase','native_launch'),('cancel','failure.phase','native_attestation'),
    ('cancel','failure.error_type','OSError'),('cancel','failure.error_type','TimeoutError'),('cancel','failure.winerror',995),
    ('cancel','failure.winerror',False),('cancel','failure.winerror','__delete__'),('cancel','failure.message','synthetic-private-value'),
    ('cancel','native_exit_code',None),('cancel','native_exit_code',0),('cancel','native_exit_code',1),
    ('cancel','login_commands_executed',0),('cancel','login_commands_executed',True),('cancel','model_jobs_executed',1),
    ('cancel','process','__delete__'),('cancel','process.token_sid','S-1-5-21-111-222-333-1002'),
    ('cancel','process.owned_job_membership_verified',False),('cancel','process.token_matches_selected_worker',1),
    ('cancel','process.image_matches_reviewed_executable',False),('cancel','process.pid',True),('cancel','process.pid',0),
    ('cancel','process.creation_time_filetime',0),('cancel','process.source','pid_lookup'),('cancel','process.profile_directory_sha256','C'*64),
    ('cancel','runtime_root',base.INSTALL.replace('-r3','-r2')),('cancel','config_sha256','0'*64),
    ('cancel','helper_sha256','0'*64),('cancel','resource_limits_sha256','0'*64),('cancel','revision.verified',False),
    ('cancel','activation_ready',True),('cancel','authentication_verified',True),('cancel','secret_published',True),
    ('cancel','native_output_after_input_published',True),('cancel','finished_at_unix_ms',999),
    ('timeout','operator_cancelled',True),('timeout','input_file_deleted',True),('timeout','failure.error_type','InterruptedError'),
    ('timeout','native_exit_code',None),('timeout','failure.winerror',1460),('timeout','cleanup_verified',False),
])
def test_cancel_or_timeout_never_relaxes_custody_cleanup_or_exact_producer_semantics(kind,key,changed):
    value=failed_wait(kind);base.mutate(value,key,changed)
    assert evaluate(value)['accepted'] is False


def test_timeout_after_submitted_input_requires_consumed_input_deletion():
    value=failed_wait('timeout',True);value['input_file_deleted']=False
    assert evaluate(value)['accepted'] is False


@pytest.mark.parametrize('key',[
    'schema','slot','nonce','system_sid','helper_sha256','runtime_root','install_receipt_sha256',
    'source_manifest_sha256','config_sha256','resource_limits_sha256','layout_sha256','status',
    'cleanup_verified','one_line_submitted','input_file_deleted','operator_cancelled','authentication_verified',
    'activation_ready','secret_published','native_output_after_input_published','login_commands_executed',
    'model_jobs_executed','started_at_unix_ms','finished_at_unix_ms','revision','process','failure',
    'revision.schema','revision.source_sha256','revision.verified','revision.read_only','revision.files','revision.acceptance_files',
    'process.token_sid','process.source','process.profile_directory_sha256','process.token_matches_selected_worker',
    'process.image_matches_reviewed_executable','process.owned_job_membership_verified','process.pid','process.creation_time_filetime',
    'failure.phase','failure.error_type',
])
def test_failed_wait_json_arrays_cannot_flatten_into_valid_scalar_or_object_evidence(key):
    value=failed_wait();selected=value
    for part in key.split('.'):selected=selected[part]
    base.mutate(value,key,[selected])
    assert evaluate(value)['accepted'] is False


@pytest.mark.parametrize('key',['failure.winerror','revision.acceptance_sha256'])
def test_failed_wait_empty_json_array_cannot_masquerade_as_required_null(key):
    value=failed_wait();base.mutate(value,key,[])
    assert evaluate(value)['accepted'] is False


@pytest.mark.parametrize('last',[0,1,3,True,-1])
def test_failed_wait_requires_exact_integer_task_result_two(last):
    assert evaluate(failed_wait(),extra=f'$task.LastTaskResult={"$true" if last is True else last};')['accepted'] is False


@pytest.mark.parametrize('change',['$script:instances=1;','$task.State=4;','$definition.Settings.RestartCount=1;'])
def test_cleanup_receipt_cannot_authorize_an_active_or_restarting_task(change):
    assert evaluate(failed_wait(),extra=change)['accepted'] is False


def test_actual_support_sanitizer_matches_explicit_cancel_and_deadline_producer_errors():
    assert hashlib.sha256((W/'worker-native-status-r3.py').read_bytes()).hexdigest()==base.SUPPORT_PIN
    assert hashlib.sha256((W/'worker_claude_login_bridge_r3.py').read_bytes()).hexdigest()==base.BRIDGE_PIN
    assert support.safe_failure(InterruptedError('fixture cancellation'),'native_wait')==dict(phase='native_wait',error_type='InterruptedError',winerror=None)
    assert support.safe_failure(TimeoutError('fixture deadline'),'native_wait')==dict(phase='native_wait',error_type='TimeoutError',winerror=None)


@pytest.mark.parametrize('kind',['cancel','timeout'])
def test_actual_loop_failure_has_no_native_exit_code_and_matches_reviewed_error_type(tmp_path,monkeypatch,kind):
    report={}
    if kind=='cancel':
        packet=b'cochem-login-input/1\n'+base.NONCE.encode()+b'\nCANCEL\n'
        (tmp_path/'cancel.request').write_bytes(packet)
        monkeypatch.setattr(bridge,'ChannelFile',lambda path,win:SimpleNamespace(raw=path.read_bytes()))
        expected=InterruptedError
    else:
        clock=iter([0,601]);monkeypatch.setattr(bridge.time,'monotonic',lambda:next(clock))
        expected=TimeoutError
    with pytest.raises(expected) as raised:
        report['native_exit_code']=bridge.run_loop(None,io.BytesIO(),queue.Queue(),io.BytesIO(),tmp_path,base.NONCE,None,report,[])
    assert 'native_exit_code' not in report
    assert report.get('operator_cancelled',False)==(kind=='cancel')
    assert support.safe_failure(raised.value,'native_wait')==failed_wait(kind)['failure']


def test_main_preserves_cancelled_session_and_returns_current_status_and_readiness_requirements():
    code=base.main_fixture(failed_wait())+'$task.LastTaskResult=2;'+r"""$proof=@(Assert-ReviewedClaudeHistory -Slot slot1 -Folder $folder -Runtime $runtime);[ordered]@{proof=$proof;task_reads=$script:taskReads;reads=@($script:reads.ToArray())}|ConvertTo-Json -Depth 6 -Compress"""
    result=json.loads(base.ps(code))
    assert result['task_reads']==2 and len(result['reads'])==4 and len(result['proof'])==1
    value=result['proof'][0]
    assert value['classification']=='TERMINAL_CANCELLED_NATIVE_LOGIN_CLEANUP_VERIFIED'
    assert value['prior_state_preserved'] is True and value['authentication_authorized_by_history'] is False
    assert value['current_logged_out_status_required'] is True and value['operator_ready_required'] is True


def test_only_one_additive_receipt_branch_changed_and_v5_all_bytes_remain_frozen():
    old=FROZEN.read_bytes();new=SOURCE.read_bytes()
    assert hashlib.sha256(old).hexdigest()=='82f66a4c135454ad406ad47cc8b6abce25fdad55951dbd6dc70f30a0c68634d4'
    start=new.index(b'        # The pinned bridge writes a final failed-wait receipt only after')
    end=new.index(b'        # native_launch is deliberately excluded.',start)
    assert new[:start]+new[end:]==old
    assert b"Test-ClaudeHistoryInteger $Task.LastTaskResult 2 2" in new[start:end]
    assert b"-not $nativeExitPresent" in new[start:end]
    assert b"'InterruptedError','TimeoutError'" in new[start:end]


def test_pinned_producer_still_finalizes_receipt_only_after_cleanup_and_failure_override():
    text=(W/'worker_claude_login_bridge_r3.py').read_text()
    tree=ast.parse(text);functions={node.name:ast.get_source_segment(text,node) for node in tree.body if isinstance(node,ast.FunctionDef)}
    cleanup=functions['cleanup_session'];run=functions['run']
    assert cleanup.index('process.close()')<cleanup.index("report['cleanup_verified'] = True")<cleanup.index('thread.join(timeout=3)')<cleanup.index('postcheck()')<cleanup.index('channel.delete_after_verified_cleanup()')
    assert run.index('cleanup_session(')<run.index("report['status'] = 'CLEANUP_UNVERIFIED'")<run.index("report['cleanup_verified'] = False")<run.index('json.dump(report, receipt')<run.index('receipt.flush(); os.fsync(receipt.fileno())')
