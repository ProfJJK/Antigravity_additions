"""Real PS5.1 file holds and inert orchestration. No privileged leaf is applied."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).parent
SCRIPT=ROOT/'run-return-continuation-r3-v2.ps1'
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
INSTALL_HASH='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
RUNTIME=r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'

def ps(code):
    return subprocess.run([PS,'-NoProfile','-NonInteractive','-Command',
        "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"+code],capture_output=True,text=True,timeout=45)

def definitions():
    return f"$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$t,[ref]$e);if($e.Count){{throw $e[0]}};foreach($f in $a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};"

def phases():
    rows=[]
    for name,stem,root,status in [
        ('foundation','execution-foundation','ExecutionFoundation','SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'),
        ('project','private-project-import','ProjectImport','PRIVATE_BARE_PROJECT_VERIFIED')]:
        receipt=str(Path(fr'C:\Program Files\CoChem\{root}4.2.7-windows-20261007-r3')/('project-import.json' if name=='project' else stem+'.json'))
        row={'name':name,'result_schema':f'cochem-{stem}'+('-result/1' if name=='project' else '-task-result/1'),
             'plan_schema':f'cochem-{stem}-plan/1','success':status,'receipt':receipt}
        value={'schema':row['result_schema'],'status':status,'receipt_path':receipt,'receipt_sha256':'a'*64,'activation_ready':False}
        if name!='project':
            value.update(runtime_root=RUNTIME,install_receipt_sha256=INSTALL_HASH,last_task_result=0)
            if name=='inspection':value.update(existing_state_modified=False,holds=[])
            else:value.update(ram_scoped_roots_verified=6,empty_registry_capacity=4,automatic_retry_allowed=False,ram_provision_started=True,registry_provision_started=True)
        else:value.update(r3_install_receipt_sha256=INSTALL_HASH,config_sha256='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
            target=r'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance',baseline_commit='c52a3a97eb085e6bafbbd14bd6a75f3274288530',
            baseline_tree='0e7fe3edd935e4a839d17cb99b30b08db18d8ae9',objects=7,files=3)
        if name=='foundation':
            row['result_schema']='cochem-execution-foundation-task-result/2'
            row['plan_schema']='cochem-execution-foundation-plan/2'
            row['receipt']=receipt.replace('r3\\execution-foundation.json','r3-v2\\execution-foundation.json')
            value.update(schema=row['result_schema'],receipt_path=row['receipt'],partial_outputs_preserved=True,
                diagnostic_receipt_sha256='cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9',
                failed_foundation_receipt_sha256='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336')
        row['fixture']=value;rows.append(row)
    return rows

def invoke(tmp_path,rows,execute=True,throw_at=''):
    data=tmp_path/'phases.json';data.write_text(json.dumps(rows))
    result=ps(definitions()+f"$steps=Get-Content -LiteralPath '{data}' -Raw|ConvertFrom-Json;$script:called=@();$script:throwAt='{throw_at}';"+r"""
    try{
      $value=Invoke-ReturnSeries $steps {param($step,$execute)
        $script:called+=@($step.name)
        if($step.name -eq $script:throwAt){throw 'synthetic leaf refusal'}
        if($execute){$step.fixture}else{[pscustomobject]@{schema=$step.plan_schema;mode='READ_ONLY_PLAN';holds=@('Expected deferred prerequisite')}}
      } EXECUTE
      Write-Output ('RESULT '+([ordered]@{ok=$true;called=$script:called;result=$value}|ConvertTo-Json -Depth 12 -Compress))
    }catch{Write-Output ('RESULT '+([ordered]@{ok=$false;called=$script:called;message=$_.Exception.Message}|ConvertTo-Json -Depth 6 -Compress))}
    """.replace('EXECUTE','$true' if execute else '$false'))
    assert result.returncode==0,result.stderr
    lines=[x[7:] for x in result.stdout.splitlines() if x.startswith('RESULT ')]
    assert len(lines)==1,result.stdout
    return json.loads(lines[0]),result.stdout

def test_all_success_runs_once_in_order_and_never_claims_activation(tmp_path):
    value,_=invoke(tmp_path,phases())
    assert value['ok'] and value['called']==['foundation','project']
    result=value['result']
    assert result['status']=='TWO_CONTINUATION_PHASES_VERIFIED'
    assert result['max_shared_slots']==4 and not result['activation_ready']
    assert result['provider_calls_executed']==0 and not result['automatic_retry_allowed']

@pytest.mark.parametrize('stage',['foundation','project'])
def test_leaf_failure_preserves_prior_success_and_never_calls_later_stage(tmp_path,stage):
    value,output=invoke(tmp_path,phases(),throw_at=stage)
    assert not value['ok']
    assert value['called']==['foundation','project'][:['foundation','project'].index(stage)+1]
    assert 'SERIES_HELD' in output and 'synthetic leaf refusal' not in output

@pytest.mark.parametrize('stage,key,replacement',[
    (0,'status','READ_ONLY_PLAN'),(0,'receipt_path',r'C:\different.json'),(0,'activation_ready','false'),
    (0,'empty_registry_capacity',8),(0,'ram_scoped_roots_verified',4),(0,'automatic_retry_allowed',True),
    (0,'install_receipt_sha256','b'*64),(0,'last_task_result',2),(0,'partial_outputs_preserved',False),
    (0,'diagnostic_receipt_sha256','0'*64),(0,'failed_foundation_receipt_sha256','0'*64),
    (1,'baseline_commit','0'*40),(1,'files',4),(1,'config_sha256','c'*64)])
def test_invalid_success_cannot_advance(tmp_path,stage,key,replacement):
    rows=phases();rows[stage]['fixture'][key]=replacement
    value,_=invoke(tmp_path,rows)
    assert not value['ok'] and len(value['called'])==stage+1

def test_preview_preserves_deferred_holds_and_does_not_apply(tmp_path):
    value,_=invoke(tmp_path,phases(),execute=False)
    assert value['ok'] and value['result']['status']=='READ_ONLY_PLAN'
    assert all(row['report']['holds'] for row in value['result']['results'])

def test_real_file_hold_blocks_writes_and_rejects_changed_helper(tmp_path):
    path=tmp_path/'leaf.ps1';path.write_text('write-output "inert"\n')
    pin=hashlib.sha256(path.read_bytes()).hexdigest()
    result=ps(definitions()+f"$s=Open-SeriesPin '{path}' '{pin}';"+r"""
    try{try{$w=[IO.File]::Open(PATH,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$w.Dispose();throw 'unexpected writer'}catch [IO.IOException]{} }
    finally{$s.Dispose()}
    'HELD_OK'
    """.replace('PATH',"'"+str(path)+"'"))
    assert result.returncode==0 and 'HELD_OK' in result.stdout,result.stderr
    path.write_text('changed')
    result=ps(definitions()+f"$s=Open-SeriesPin '{path}' '{pin}'")
    assert result.returncode!=0 and 'Reviewed series helper changed' in result.stderr

def test_same_process_leaf_scope_keeps_progress_separate_and_parent_hold_intact(tmp_path):
    leaf=tmp_path/'leaf.ps1';leaf.write_text("$seriesStreams='child-local';Write-Host 'safe progress';[ordered]@{schema='fixture';ok=$true}|ConvertTo-Json\n")
    pin=hashlib.sha256(leaf.read_bytes()).hexdigest()
    result=ps(definitions()+f"$seriesStreams=123;$s=Open-SeriesPin '{leaf}' '{pin}';try{{$raw=@(& '{leaf}');$v=($raw -join [Environment]::NewLine)|ConvertFrom-Json;if($seriesStreams -ne 123 -or $v.schema -ne 'fixture' -or -not $v.ok){{throw 'scope or streams changed'}};'SCOPE_OK'}}finally{{$s.Dispose()}}")
    assert result.returncode==0 and 'SCOPE_OK' in result.stdout,result.stderr

def test_nonadministrator_apply_rejects_before_invoking_any_leaf():
    result=subprocess.run([PS,'-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Apply'],capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and 'No phase was invoked' in result.stderr
    assert 'Running reviewed continuation phase' not in result.stdout

@pytest.mark.parametrize('kind',['receipt','bootstrap','unsafe'])
def test_real_leaf_failure_retains_only_safe_project_metadata(tmp_path,kind):
    step=phases()[1]
    report={'schema':step['result_schema'],'status':'HELD_PRESERVE_PARTIAL'}
    if kind=='bootstrap':report.update(phase='helper_bootstrap',exit_code=3,raw_child_output_withheld=True)
    else:report.update(receipt_path=step['receipt'],receipt_sha256='a'*64,error_type='ImportHeld',code='git_nonzero_2')
    if kind=='unsafe':report['receipt_path']='unreviewed-secret-value'
    report['untrusted_extra']='private-extra-must-not-print'
    leaf=tmp_path/'failing.ps1';leaf.write_text("param([switch]$Apply)\n'"+json.dumps(report)+"'\nthrow 'private-exception-must-not-print'\n")
    spec=tmp_path/'step.json';step['path']=str(leaf);spec.write_text(json.dumps(step))
    result=ps(definitions()+f"$step=Get-Content '{spec}' -Raw|ConvertFrom-Json;try{{Invoke-ReturnSeries @($step) {{param($s,$e)Invoke-SeriesLeaf $s $e}} $true}}catch{{'CAUGHT'}}")
    assert result.returncode==0 and 'CAUGHT' in result.stdout,result.stderr
    assert 'private-extra-must-not-print' not in result.stdout+result.stderr
    assert 'private-exception-must-not-print' not in result.stdout+result.stderr
    assert 'unreviewed-secret-value' not in result.stdout+result.stderr
    assert ('HELD_PRESERVE_PARTIAL' in result.stdout)==(kind!='unsafe')
    if kind=='receipt':assert 'git_nonzero_2' in result.stdout and 'a'*64 in result.stdout
    if kind=='bootstrap':assert 'helper_bootstrap' in result.stdout and '"exit_code":3' in result.stdout
