"""Pure control-flow/WinPS guards for morning setup; no live phase is invoked."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SCRIPT = ROOT / 'finish-reviewed-setup.ps1'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    code = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)},[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',code+body],
        text=True,capture_output=True,timeout=30)


@pytest.mark.parametrize('case,execute,seen,failed', [
    ('pass',False,['plan-private_knowledge','plan-six_worker_denials'],False),
    ('pass',True,['plan-private_knowledge','plan-six_worker_denials','apply-private_knowledge','apply-six_worker_denials'],False),
    ('last_plan_hold',True,['plan-private_knowledge','plan-six_worker_denials'],True),
    ('knowledge_throw',True,['plan-private_knowledge','plan-six_worker_denials','apply-private_knowledge'],True),
    ('knowledge_bad_result',True,['plan-private_knowledge','plan-six_worker_denials','apply-private_knowledge'],True),
    ('denial_bad_result',True,['plan-private_knowledge','plan-six_worker_denials','apply-private_knowledge','apply-six_worker_denials'],True),
    ('missing_slot',True,['plan-private_knowledge','plan-six_worker_denials','apply-private_knowledge','apply-six_worker_denials'],True),
])
def test_all_plans_before_any_mutation_and_stop_first_failed_phase(case, execute, seen, failed):
    result = run(f"$script:case='{case}';$execute=${str(execute).lower()};" + r'''
    $steps=@([pscustomobject]@{name='private_knowledge';plan_schema='knowledge-plan';result_schema='knowledge-result';success='K_PASS'},[pscustomobject]@{name='six_worker_denials';plan_schema='denial-plan';result_schema='denial-result';success='D_PASS'});
    $script:seen=@();$failed=$false;$report=$null;
    try{$report=Invoke-ReviewedPhases $steps {param($step,$apply)
        $phase=if($apply){'apply'}else{'plan'};$script:seen+=@("$phase-$($step.name)");
        if(-not$apply){
            $p=[pscustomobject]@{schema=$step.plan_schema;mode='READ_ONLY_PLAN';holds=@();documents=137;files_verified=138;private_files_copied=0;models_executed=0;daemon_started=$false;slots=@('slot1','slot2','slot3','slot4','slot5','slot6');parallel_workers=1;stop_on_first_failure=$true;all_six_targets_checked=$true;worker_processes_executed=0};
            if($script:case -eq 'last_plan_hold' -and $step.name -eq 'six_worker_denials'){$p.holds=@('existing target')};return $p
        }
        if($step.name -eq 'private_knowledge' -and $script:case -eq 'knowledge_throw'){throw 'controlled failure'};
        $r=[pscustomobject]@{schema=$step.result_schema;status=$step.success;last_task_result=0;configuration_modified=$false;daemon_started=$false;receipt_sha256=('a'*64);receipt_path='C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006\knowledge-acceptance.json';slots_verified=6;parallel_workers=1;pipeline_started=$false;activation_ready=$false;receipts=@(1..6|ForEach-Object {[pscustomobject]@{slot="slot$_"}})};
        if($step.name -eq 'private_knowledge' -and $script:case -eq 'knowledge_bad_result'){$r.last_task_result=2};
        if($step.name -eq 'six_worker_denials' -and $script:case -eq 'denial_bad_result'){$r.status='D_FAIL'};
        if($step.name -eq 'six_worker_denials' -and $script:case -eq 'missing_slot'){$r.receipts=@($r.receipts|Select-Object -First 5)};
        $r
    } $execute}catch{$failed=$true};
    [ordered]@{seen=$script:seen;failed=$failed;mode=$(if($null -eq $report){$null}else{$report.mode})}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual['seen'] == seen
    assert actual['failed'] is failed
    if not failed:
        assert actual['mode'] == ('REVIEWED_PHASES_COMPLETED' if execute else 'READ_ONLY_PLAN')


@pytest.mark.parametrize('pin', ['correct','wrong','pending'])
def test_step_bytes_bound_and_held_against_concurrent_write(tmp_path, pin):
    path = tmp_path/'step.ps1';path.write_bytes(b'# inert fixture\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if pin=='correct' else ('0'*64 if pin=='wrong' else 'PENDING')
    result = run(f"$step=[pscustomobject]@{{name='fixture';path={quote(path)};sha256='{digest}'}};" + r'''
    $stream=$null;$opened=$false;$writeDenied=$false;
    try{$stream=Open-HeldStep $step;$opened=$true;try{[IO.File]::WriteAllText($step.path,'changed')}catch{$writeDenied=$true}}catch{}finally{if($null -ne $stream){$stream.Dispose()}};
    [ordered]@{opened=$opened;write_denied=$writeDenied}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'opened':pin=='correct','write_denied':pin=='correct'}
    assert path.read_bytes() == b'# inert fixture\n'


def test_nonadmin_apply_refuses_before_any_phase():
    result = subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Apply'],
        text=True,capture_output=True,timeout=15)
    assert result.returncode != 0
    assert '-Apply requires the owner in Administrator Windows PowerShell' in result.stderr
