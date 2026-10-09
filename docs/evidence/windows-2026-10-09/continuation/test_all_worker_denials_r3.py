"""Inert batch control tests; never invoke a task, worker or device API."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
BATCH = ROOT / 'check-all-worker-denials-r3.ps1'


def run(body):
    path = str(BATCH).replace("'", "''")
    code = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code + body],
                          text=True, capture_output=True, timeout=30)


@pytest.mark.parametrize('fail_slot,failure', [(0,'none'),(1,'throw'),(3,'throw'),(6,'throw'),
    (3,'status'),(3,'cleanup'),(3,'wrong_slot'),(3,'exit'),(3,'wrong_receipt')])
def test_sequential_runner_stops_on_first_failure(fail_slot, failure):
    result = run(f"$script:failSlot={fail_slot};$script:failure='{failure}';" + r'''
    $script:seen=@();$stopped=$false;$count=0;
    try{$records=@(Invoke-SixSequential {param($slot)
        $script:seen+=@($slot);
        $row=[pscustomobject]@{schema='cochem-worker-denial-acceptance-task-result/1';slot=$slot;status='HANDLE_DENIALS_VERIFIED';cleanup_verified=$true;last_task_result=0;ioctls_sent=0;pipeline_started=$false;receipt_path="C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-$slot\worker-denial-acceptance.json";receipt_sha256=('a'*64)};
        if($slot -eq "slot$script:failSlot"){
            switch($script:failure){
                'throw'{throw 'controlled failure'}
                'status'{$row.status='DENIAL_CHECK_FAILED'}
                'cleanup'{$row.cleanup_verified=$false}
                'wrong_slot'{$row.slot='slot1'}
                'exit'{$row.last_task_result=2}
                'wrong_receipt'{$row.receipt_path='C:\wrong\receipt.json'}
            }
        };$row
    });$count=$records.Count}catch{$stopped=$true};
    [ordered]@{stopped=$stopped;seen=$script:seen;result_count=$count}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['seen'] == [f'slot{i}' for i in range(1, (fail_slot or 6)+1)]
    assert value['stopped'] == bool(fail_slot)
    assert value['result_count'] == (0 if fail_slot else 6)


@pytest.mark.parametrize('case,holds', [('all_absent',0),('last_root_exists',1),('last_task_exists',1),
    ('access_denied',6),('account_information_error',6)])
def test_all_six_targets_checked_before_any_launch(case, holds):
    result = run(f"$script:case='{case}';" + r'''
    $script:paths=0;$script:tasks=0;
    function Test-Path {param($LiteralPath,$ErrorAction)$script:paths++;return ($script:case -eq 'last_root_exists' -and $LiteralPath.EndsWith('slot6'))};
    $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)
        $script:tasks++;
        if($script:case -eq 'last_task_exists' -and $name.EndsWith('slot6')){return [pscustomobject]@{}}
        $code=if($script:case -eq 'access_denied'){-2147024891}elseif($script:case -eq 'account_information_error'){-2147216625}else{-2147024894};
        throw [Runtime.InteropServices.COMException]::new('controlled scheduler error',$code)
    };
    $holds=@(Get-AllFreshTargetHolds $folder);
    [ordered]@{holds=$holds.Count;roots_checked=$script:paths;tasks_checked=$script:tasks}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'holds':holds,'roots_checked':6,'tasks_checked':6}


def test_exact_reviewed_payload_pins_match():
    text = BATCH.read_text()
    for name in ('check-worker-denials-r3.ps1','worker-denial-acceptance-r3.py'):
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() in text


def test_nonadmin_apply_stops_before_any_target_checks():
    result = subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-File',str(BATCH),'-Apply'],
        text=True,capture_output=True,timeout=15)
    assert result.returncode != 0
    assert '-Apply requires the owner in Administrator Windows PowerShell' in result.stderr
