"""Inert orchestration/refusal tests. Never invoke any production phase."""
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SCRIPT = ROOT / 'continue-stopped-setup.ps1'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    prefix = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)},[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', prefix + body],
                          text=True, capture_output=True, timeout=30)


def reports():
    first = dict(schema='cochem-reviewed-morning-setup/1', mode='READ_ONLY_PLAN',
                 stop_on_first_failure=True, models_executed=0, logins_executed=0,
                 repair_budgets_modified=False, pipeline_started=False, activation_ready=False,
                 plans=[{'phase': p, 'report': {'mode': 'READ_ONLY_PLAN', 'holds': []}}
                        for p in ('private_knowledge', 'six_worker_denials')], results=[])
    done = copy.deepcopy(first)
    done['mode'] = 'REVIEWED_PHASES_COMPLETED'
    done['results'] = [
        {'phase': 'private_knowledge', 'report': dict(schema='cochem-private-knowledge-install-result/1',
         status='PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED', last_task_result=0,
         configuration_modified=False, daemon_started=False, receipt_sha256='a' * 64)},
        {'phase': 'six_worker_denials', 'report': dict(schema='cochem-six-worker-denials-result/1',
         status='ALL_SIX_HANDLE_DENIALS_VERIFIED', slots_verified=6, parallel_workers=1,
         receipts=[{'slot': f'slot{i}'} for i in range(1, 7)], pipeline_started=False, activation_ready=False)}]
    runtime = dict(schema='cochem-stopped-runtime-update/1', mode='READ_ONLY_PLAN', holds=[],
        target_root=r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r1', source_files=166,
        source_manifest_sha256='b643d5903b886e5fc3841dc0f379de992bde17dee0c202800b3cc7991998e18f',
        configuration_sha256='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        only_config_change='ramdisk.workspace_subdirectory', accounts_provisioned=0, tasks_changed=0,
        credentials_modified=False, databases_modified=False, ram_modified=False, pipeline_started=False,
        activation_ready=False)
    built = copy.deepcopy(runtime)
    built.update(mode='FRESH_STOPPED_RUNTIME_READY', verification=dict(revision={'verified': True},
        configuration_parsed=True, ram_workspace_root=r'R:\CoChem427-windows-20261007', no_system_or_model_execution=True))
    return {'first_plan': first, 'first_result': done, 'runtime_plan': runtime, 'runtime_result': built}


@pytest.mark.parametrize('case', ['plan_only', 'success', 'first_hold', 'runtime_hold', 'first_throw',
                                  'knowledge_failure', 'missing_result', 'runtime_failure', 'runtime_drift',
                                  'runtime_unverified'] + [f'slot{i}_failure' for i in range(1, 7)])
def test_all_plans_before_writes_and_no_later_phase_after_failure(tmp_path, case):
    data = reports()
    if case == 'first_hold':
        data['first_plan']['plans'][1]['report']['holds'] = ['existing target']
    elif case == 'runtime_hold':
        data['runtime_plan']['holds'] = ['existing target']
    elif case == 'knowledge_failure':
        data['first_result']['results'][0]['report']['last_task_result'] = 1
    elif case == 'missing_result':
        data['first_result']['results'].pop()
    elif case.startswith('slot'):
        completed = int(case[4]) - 1
        data['first_result']['results'][1]['report']['receipts'] = data['first_result']['results'][1]['report']['receipts'][:completed]
    elif case == 'runtime_failure':
        data['runtime_result']['mode'] = 'FAILED'
    elif case == 'runtime_drift':
        data['runtime_result']['configuration_sha256'] = '0' * 64
    elif case == 'runtime_unverified':
        data['runtime_result']['verification']['revision']['verified'] = False
    fixture = tmp_path / 'reports.json'
    fixture.write_text(json.dumps(data))
    result = run(f"$script:data=Get-Content -LiteralPath {quote(fixture)} -Raw|ConvertFrom-Json;$script:case='{case}';" + r'''
    $script:seen=@();$failed=$false;$value=$null;
    try{$value=Invoke-StoppedContinuation {param($phase,$apply)
      $verb=if($apply){'apply'}else{'plan'};$script:seen+=@("$verb-$phase");
      if($phase -eq 'knowledge_and_six_denials'){
        if($apply){if($script:case -eq 'first_throw'){throw 'inert failure'};return $script:data.first_result};return $script:data.first_plan
      }
      if($apply){return $script:data.runtime_result};return $script:data.runtime_plan
    } ($script:case -ne 'plan_only')}catch{$failed=$true};
    [ordered]@{seen=$script:seen;failed=$failed;mode=$(if($null -eq $value){$null}else{$value.mode})}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    expected = ['plan-knowledge_and_six_denials']
    if case != 'first_hold':
        expected.append('plan-fresh_stopped_runtime')
    if case not in ('plan_only', 'first_hold', 'runtime_hold'):
        expected.append('apply-knowledge_and_six_denials')
    if case in ('success', 'runtime_failure', 'runtime_drift', 'runtime_unverified'):
        expected.append('apply-fresh_stopped_runtime')
    assert value['seen'] == expected
    assert value['failed'] is (case not in ('success', 'plan_only'))
    if case == 'success':
        assert value['mode'] == 'REVIEWED_STOPPED_SETUP_COMPLETED'


@pytest.mark.parametrize('pin', ['correct', 'wrong', 'pending'])
def test_pin_is_verified_and_read_stream_blocks_replacement(tmp_path, pin):
    path = tmp_path / 'helper.ps1'
    path.write_bytes(b'# inert fixture\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if pin == 'correct' else ('0' * 64 if pin == 'wrong' else 'PENDING')
    result = run(f"$pin=[pscustomobject]@{{name='fixture';path={quote(path)};sha256='{digest}'}};" + r'''
    $stream=$null;$opened=$false;$denied=$false;
    try{$stream=Open-ContinuationPin $pin;$opened=$true;try{[IO.File]::WriteAllText($pin.path,'changed')}catch{$denied=$true}}catch{}finally{if($null -ne $stream){$stream.Dispose()}};
    @{opened=$opened;write_denied=$denied}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'opened': pin == 'correct', 'write_denied': pin == 'correct'}
    assert path.read_bytes() == b'# inert fixture\n'


def test_nonadmin_apply_refuses_before_dependency_or_phase_calls():
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(SCRIPT), '-Apply'],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert '-Apply requires the owner in Administrator Windows PowerShell' in result.stderr


@pytest.mark.parametrize('case', ['correct', 'missing_slot', 'extra_slot', 'wrong_capacity', 'wrong_manifest'])
def test_actual_named_worker_configuration_shape_preserves_four_shared_slots(case):
    result = run(f"$candidate=Get-Content -LiteralPath {quote(ROOT / 'pipeline.scoped-ram.candidate.json')} -Raw|ConvertFrom-Json;$case='{case}';" + r'''
    $inventory=[pscustomobject]@{max_execution_slots=4;worker_identities=6};
    if($case -eq 'missing_slot'){$candidate.workers.PSObject.Properties.Remove('slot6')};
    if($case -eq 'extra_slot'){$candidate.workers|Add-Member -NotePropertyName slot7 -NotePropertyValue @{} };
    if($case -eq 'wrong_capacity'){$candidate.max_execution_slots=6};
    if($case -eq 'wrong_manifest'){$inventory.worker_identities=4};
    $failed=$false;try{Assert-ContinuationCapacity $inventory $candidate}catch{$failed=$true};
    @{failed=$failed}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['failed'] is (case != 'correct')
