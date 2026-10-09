"""Inert orchestration and real PS5.1 child-output failure propagation."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SCRIPT = ROOT / 'resume-knowledge-and-check-workers.ps1'
PY_SHA = '780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    prefix = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)},[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    $script:resumePythonHash='{PY_SHA}';
    """
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', prefix + body],
                          capture_output=True, text=True, timeout=30)


def reports():
    plans = [json.loads((ROOT / name).read_text(encoding='utf-8-sig')) for name in
             ('knowledge-resume-readonly-preview.json', 'six-worker-denials-preview-after-r2.json')]
    acceptance = dict(schema='cochem-private-knowledge-resume/1', status='PRESERVED_CORPUS_AND_RESUMED_INDEX_VERIFIED',
        system_sid='S-1-5-18', nonce='a'*32, helper_sha256=PY_SHA,
        runtime_root=r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2',
        pipeline_config_sha256='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
        source_files_verified=166, original_root_and_lock_preserved=True, documents=137, corpus_files=138,
        index_integrity_check='ok', source_bytes_preserved=True, existing_acl_modified=False,
        corpus_reprovisioned=False, old_tasks_modified_or_run=False, budgets_modified=False,
        daemon_started=False, activation_ready=False)
    resumed = dict(schema='cochem-private-knowledge-resume-result/1', status=acceptance['status'],
        last_task_result=0, activation_ready=False, receipt_sha256='b'*64,
        receipt_path=r'C:\Program Files\CoChem\KnowledgeResume4.2.7-windows-20261007-r2\resume-acceptance.json', acceptance=acceptance)
    denied = dict(schema='cochem-six-worker-denials-result/1', status='ALL_SIX_HANDLE_DENIALS_VERIFIED',
        slots_verified=6, parallel_workers=1, receipts=[{'slot':f'slot{i}'} for i in range(1,7)],
        ioctls_sent=0, target_contents_read=0, pipeline_started=False, activation_ready=False)
    return {'plans': plans, 'results': [resumed, denied]}


@pytest.mark.parametrize('case', ['plan', 'pass', 'first_plan_hold', 'second_plan_hold', 'resume_throw',
                                  'resume_held', 'wrong_runtime', 'changed_original_state'] + [f'slot{i}_failure' for i in range(1,7)])
def test_preflight_both_then_stop_on_any_incomplete_phase(tmp_path, case):
    data = reports()
    if case == 'first_plan_hold': data['plans'][0]['holds'] = ['existing root']
    if case == 'second_plan_hold': data['plans'][1]['holds'] = ['existing slot']
    if case == 'resume_held': data['results'][0]['status'] = 'KNOWLEDGE_RESUME_HELD'
    if case == 'wrong_runtime': data['results'][0]['acceptance']['runtime_root'] = 'old runtime'
    if case == 'changed_original_state': data['results'][0]['acceptance']['original_root_and_lock_preserved'] = False
    if case.startswith('slot'): data['results'][1]['receipts'] = data['results'][1]['receipts'][:int(case[4])-1]
    path = tmp_path/'reports.json'; path.write_text(json.dumps(data))
    result = run(f"$script:data=Get-Content -LiteralPath {quote(path)} -Raw|ConvertFrom-Json;$script:case='{case}';" + r'''
    $script:seen=@();$failed=$false;$report=$null;
    try{$report=Invoke-RecoverySequence {param($phase,$apply)
      $verb=if($apply){'apply'}else{'plan'};$script:seen+=@("$verb-$phase");
      $index=if($phase -eq 'knowledge_resume'){0}else{1};
      if($apply){if($index -eq 0 -and $script:case -eq 'resume_throw'){throw 'inert failure'};return $script:data.results[$index]};return $script:data.plans[$index]
    } ($script:case -ne 'plan')}catch{$failed=$true};
    @{seen=$script:seen;failed=$failed}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    expected = ['plan-knowledge_resume']
    if case != 'first_plan_hold': expected.append('plan-six_host_boundary_checks')
    if case not in ('plan','first_plan_hold','second_plan_hold'): expected.append('apply-knowledge_resume')
    if case == 'pass' or case.startswith('slot'): expected.append('apply-six_host_boundary_checks')
    assert value == {'seen': expected, 'failed': case not in ('plan','pass')}


@pytest.mark.parametrize('case', ['valid', 'bad_phase', 'bad_error_type', 'bad_winerror', 'bad_schema'])
def test_real_child_json_before_throw_is_visible_only_as_validated_metadata(tmp_path, case):
    value = dict(schema='cochem-private-knowledge-resume-result/1',status='KNOWLEDGE_RESUME_HELD',last_task_result=2,
        receipt_sha256='b'*64,receipt_path=r'C:\Program Files\CoChem\KnowledgeResume4.2.7-windows-20261007-r2\resume-acceptance.json',
        failure={'phase':'index_resume','error_type':'WindowsIsolationError','winerror':5,'message':'DO_NOT_ECHO'})
    if case == 'bad_phase': value['failure']['phase'] = 'secret payload'
    if case == 'bad_error_type': value['failure']['error_type'] = 'error with content'
    if case == 'bad_winerror': value['failure']['winerror'] = -1
    if case == 'bad_schema': value['schema'] = 'untrusted'
    child=tmp_path/'child.ps1'
    child.write_text('Write-Output '+quote(json.dumps(value))+"\nthrow 'inert child failure'\n")
    result = run(f"$failed=$false;try{{$null=Invoke-RecoveryChild {quote(child)} $false}}catch{{$failed=$true}};Write-Output ('CAUGHT='+$failed)")
    assert result.returncode == 0, result.stderr
    assert 'CAUGHT=True' in result.stdout
    assert 'DO_NOT_ECHO' not in result.stdout
    assert ('WindowsIsolationError' in result.stdout) is (case == 'valid')


def test_nonadmin_apply_refuses_before_any_child_or_private_read():
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Apply'],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode != 0
    assert 'Apply requires the owner in Administrator Windows PowerShell' in result.stderr
