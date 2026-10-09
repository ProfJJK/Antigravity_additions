'Windows PS5.1 metadata-only diagnostic tests; no original DB is opened.'
import hashlib
import json
from pathlib import Path
import subprocess
import pytest
W=Path(__file__).parent
SCRIPT=W/'inspect-legacy-continuity.ps1'
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
def run(body):
    code=f"$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;$diagnosticSource='{SCRIPT}';foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){{Import-Module (Join-Path $PSHOME \"Modules\\$m\\$m.psd1\")}};$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$tokens,[ref]$errors);if($errors.Count){{throw $errors[0]}};foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};"
    return subprocess.run([PS,'-NoProfile','-NonInteractive','-Command',code+body],capture_output=True,text=True,timeout=20)
def test_metadata_only_preserves_bytes(tmp_path):
    source=tmp_path/'supervisor.db';source.write_bytes(b'SYNTHETIC NOT SQLITE private text');before=source.stat()
    r=run(f"Get-PathMetadata '{source}'|ConvertTo-Json -Compress")
    assert r.returncode==0,r.stderr
    v=json.loads(r.stdout);assert v['status']=='PRESENT' and v['bytes']==before.st_size and not v['content_read']
    assert 'private text' not in r.stdout and source.read_bytes()==b'SYNTHETIC NOT SQLITE private text'
    assert source.stat().st_mtime_ns==before.st_mtime_ns

def test_actual_missing_and_junction_are_distinct(tmp_path):
    target=tmp_path/'target';target.mkdir();(target/'supervisor.db').write_bytes(b'synthetic');link=tmp_path/'junction'
    r=run(f"$null=New-Item -ItemType Junction -Path '{link}' -Target '{target}';@(Get-PathMetadata '{tmp_path/'absent.db'}';Get-PathMetadata '{link/'supervisor.db'}')|ConvertTo-Json -Depth 4 -Compress")
    assert r.returncode==0,r.stderr
    v=json.loads(r.stdout);assert [x['status'] for x in v]==['ABSENT','REPARSE_REFUSED'] and v[1]['at']==str(link)

@pytest.mark.parametrize('code,expected',[(-2147024894,'ABSENT'),(-2147024891,'INACCESSIBLE'),(-2147216625,'ERROR'),(-2147216629,'ERROR')])
def test_scheduler_error_classification(code,expected):
    r=run(f"Get-ErrorClassification ([Runtime.InteropServices.COMException]::new('synthetic private text',{code}))|ConvertTo-Json -Compress")
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)==expected and 'private text' not in r.stdout

@pytest.mark.parametrize('line,expected',[
 (r'C:\Python314\python.exe D:\__CoChem\__agentic\v4.2.0\auto_architect_mcp.py --private fake','LEGACY_V420_MCP_INGRESS'),
 (r'cmd /c set PYTHONPATH=D:\__CoChem\__agentic\v4.1.2\src && python -m cochem.warden.mcp_server','LEGACY_VM_WARDEN_ENTRYPOINT'),
 (r'python D:/__CoChem/__agentic/v4.2.0/src/cochem/warden/cochem_warden_oracle.py','LEGACY_ORACLE_ENTRYPOINT'),
 (r'python C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3 -m cochem_pipeline',None),
 (r'python unrelated.py --token privatevalue',None)])
def test_narrow_process_classification(line,expected):
    r=run("Get-LegacyProcessClassification '"+line.replace("'","''")+"'|ConvertTo-Json -Compress")
    assert r.returncode==0,r.stderr
    value=json.loads(r.stdout) if r.stdout.strip() else None
    assert value==expected and 'private' not in r.stdout

def test_process_capture_redacts_arguments_and_marks_hidden():
    r=run(r'''
    function Get-CimInstance {param($ClassName,$Filter,$OperationTimeoutSec,$ErrorAction)
      @([pscustomobject]@{ProcessId=42;ParentProcessId=1;CreationDate=[DateTime]::UtcNow;ExecutablePath='C:\Python314\python.exe';CommandLine='python D:\__CoChem\__agentic\v4.2.0\auto_architect_mcp.py --token synthetic-private'},[pscustomobject]@{ProcessId=43;CommandLine=$null})
    };Get-LegacyProcessMetadata|ConvertTo-Json -Depth 5 -Compress
    ''')
    assert r.returncode==0,r.stderr
    v=json.loads(r.stdout);assert v['unreadable_command_lines']==1 and v['visibility_incomplete']
    assert len(v['matched'])==1 and len(v['matched'][0]['command_line_sha256'])==64
    assert 'synthetic-private' not in r.stdout+r.stderr and not v['live_database_writer_proven']

def test_complete_control_flow_metadata_only_fixed_tasks(tmp_path):
    r=run(f"$fixture='{tmp_path}';"+r'''
    $script:paths=@();$script:tasks=@()
    function Get-KnownRoots {[pscustomobject]@{roots=@($fixture);discovery=[ordered]@{status='FIXTURE'}}}
    function Get-PathMetadata {param($Path)$script:paths+=@($Path);[ordered]@{path=$Path;status='FIXTURE_METADATA_ONLY'}}
    function Get-LegacyProcessMetadata {[ordered]@{status='FIXTURE_NO_PROCESSES';matched=@()}}
    $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)$script:tasks+=@($name);throw [Runtime.InteropServices.COMException]::new('not found',-2147024894)}
    $scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod Connect {return};$scheduler|Add-Member ScriptMethod GetFolder {param($name)$folder}
    function New-Object {param($ComObject)if($ComObject -cne 'Schedule.Service'){throw 'unexpected COM'};$scheduler}
    $v=Invoke-ContinuityInspection
    [ordered]@{schema=$v.schema;database_contents_opened=$v.database_contents_opened;changed=$v.tasks_changed;paths=$script:paths.Count;tasks=$script:tasks;source=$v.source_sha256;activation=$v.activation_ready}|ConvertTo-Json -Depth 4 -Compress
    ''')
    assert r.returncode==0,r.stderr
    v=json.loads(r.stdout);assert v['paths']==30 and len(v['tasks'])==5
    assert not v['database_contents_opened'] and not v['changed'] and not v['activation']
    assert v['source']==hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
