"""Actual WinPS5 pure duration/task guards; in-memory COM only, never registration."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

HERE = Path(__file__).parent
SOURCE = HERE/'resource-observer-duration-r3-v1.ps1'
ORIGINAL = HERE/'install-resource-observer-r3-v5.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def quote(value): return "'"+str(value).replace("'","''")+"'"


PREFIX = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Import-FixtureFunctions([string]$Path){
 $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e)
 if($e.Count){throw 'Parse failure'}
 foreach($n in $ast.FindAll({param($x)$x -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$n.Extent.Text}
}
'''
PREFIX += 'foreach($f in Import-FixtureFunctions '+quote(SOURCE)+'){. ([scriptblock]::Create($f))}\n'
FAKE = r'''
$script:python='C:\fixed\python.exe';$script:packageRoot='C:\fixed\package';$script:aclChecks=0
function Assert-RegisteredTaskAcl {
 param([string]$Sddl,[string]$Name)
 if($Name -cne 'fixture-resource-task'){throw 'Wrong task name'}
 $sd=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
 if(-not ($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $sd.Owner.Value -cne 'S-1-5-32-544' -or $sd.DiscretionaryAcl.Count -ne 2){throw 'Fixture ACL guard'}
 $sids=@();foreach($ace in $sd.DiscretionaryAcl){if($ace.AccessMask -ne 2032127 -or $ace.AceFlags -ne 0 -or $ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed){throw 'Fixture ACL mask'};$sids+=$ace.SecurityIdentifier.Value}
 if(($sids|Sort-Object) -join ',' -cne 'S-1-5-18,S-1-5-32-544'){throw 'Fixture ACL trustees'}
 $script:aclChecks++
}
function New-FixtureTask {
 $actions=[pscustomobject]@{Count=1;Value=[pscustomobject]@{Type=0;Path=$script:python;Arguments='fixed';WorkingDirectory=$script:packageRoot}}
 $actions|Add-Member ScriptMethod Item {param($index)if($index -ne 1){throw 'Index'};$this.Value}
 $d=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};
  Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$true;MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='P2DT1H'};
  Actions=$actions;Triggers=[pscustomobject]@{Count=0}}
 $task=[pscustomobject]@{Name='fixture-resource-task';Definition=$d;Enabled=$false;State=1;LastTaskResult=267011;Instances=0;Guid='{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}';Sddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'}
 $task|Add-Member ScriptMethod GetSecurityDescriptor {param($flags)if($flags -ne 7){throw 'SD flags'};$this.Sddl}
 $task|Add-Member ScriptMethod GetInstances {param($flags)$v=[pscustomobject]@{Count=$this.Instances;Guid=$this.Guid};$v|Add-Member ScriptMethod Item {param($index)[pscustomobject]@{InstanceGuid=$this.Guid}};$v}
 $task
}
'''


def run_ps(body):
    code=PREFIX+FAKE+body
    result=subprocess.run([PS,'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',
                           base64.b64encode(code.encode('utf-16le')).decode()],capture_output=True,
                          text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    return json.loads(result.stdout.strip().lstrip('\ufeff'))


def test_only_original_duration_predicate_changed_and_source5_preserved():
    original=ORIGINAL.read_bytes()
    assert hashlib.sha256(original).hexdigest()=='ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f'
    pattern=r'(?ms)^function Assert-ObserverTask \{.*?^\}'
    old=re.search(pattern,original.decode('utf-8-sig')).group()
    new=re.search(pattern,SOURCE.read_text()).group()
    old=old.replace("$d.Settings.ExecutionTimeLimit -cne 'PT49H'",'-not (Test-ExactObserverDuration $d.Settings.ExecutionTimeLimit 176400)')
    assert old.replace('\r\n','\n')==new.replace('\r\n','\n')
    text=SOURCE.read_text()
    assert 'RegisterTaskDefinition' not in text and '.Run(' not in text and 'SetSecurityDescriptor' not in text


@pytest.mark.parametrize('value,accept',[
    ('PT49H',True),('P2DT1H',True),('PT176400S',True),('P2DT60M',True),('PT2940M',True),('PT49H0M0S',True),
    ('PT48H',False),('PT50H',False),('PT0S',False),('P49D',False),('PT176400.0000001S',False),
    ('-PT49H',False),('P0Y2DT1H',False),('P2DT',False),('PT49H ',False),('',False),('PT49.0H',False)])
def test_semantic_exact_duration_and_imported_real_guard(value,accept):
    result=run_ps('$task=New-FixtureTask;$task.Definition.Settings.ExecutionTimeLimit='+quote(value)+r'''
$duration=Test-ExactObserverDuration $task.Definition.Settings.ExecutionTimeLimit 176400
$refused=$false;try{Assert-ObserverTask $task 'fixed'}catch{$refused=$true}
@{duration=$duration;refused=$refused;acl_checks=$script:aclChecks;registered_tasks=0;runs=0}|ConvertTo-Json
''')
    assert result['duration'] is accept and result['refused'] is not accept
    assert result['acl_checks']==int(accept)
    assert result['registered_tasks']==result['runs']==0


@pytest.mark.parametrize('mutation',[
    '$d.Principal.UserId="ansac"','$d.Principal.LogonType=3','$d.Principal.RunLevel=0',
    '$d.Settings.AllowDemandStart=$false','$d.Settings.MultipleInstances=0','$d.Settings.RestartCount=1',
    '$d.Triggers.Count=1','$d.Actions.Count=2','$d.Actions.Value.Type=1',
    '$d.Actions.Value.Path="C:\\other.exe"','$d.Actions.Value.Arguments="changed"',
    '$d.Actions.Value.WorkingDirectory="C:\\other"','$task.Enabled=$true','$task.State=4',
    '$task.Instances=1','$task.LastTaskResult=0',
    '$task.Sddl="O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;BU)"'])
def test_every_original_guard_stays_strict_on_equivalent_duration(mutation):
    result=run_ps('$task=New-FixtureTask;$d=$task.Definition\n'+mutation+r'''
$refused=$false;try{Assert-ObserverTask $task 'fixed'}catch{$refused=$true}
@{refused=$refused;task_runs=0;task_registrations=0}|ConvertTo-Json
''')
    assert result=={'refused':True,'task_runs':0,'task_registrations':0}


def test_nonstring_or_other_expected_limit_refused():
    result=run_ps(r'''
@{null_refused=(-not (Test-ExactObserverDuration $null));numeric_refused=(-not (Test-ExactObserverDuration 176400));
  other_expected_refused=(-not (Test-ExactObserverDuration 'PT48H' 172800));
  overflow_refused=(-not (Test-ExactObserverDuration ('P'+('9'*110)+'D')))}|ConvertTo-Json
''')
    assert all(result.values())


def test_native_in_memory_com_roundtrip_is_distinguished_from_synthetic_registered_duration():
    result=run_ps(r'''
# No Connect, GetTask, RegisterTaskDefinition, Run or task mutation. NewTask
# supplies only an unregistered in-memory Task Scheduler COM definition.
$scheduler=New-Object -ComObject Schedule.Service
$definition=$scheduler.NewTask(0)
$definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
$definition.Settings.Enabled=$false;$definition.Settings.AllowDemandStart=$true
$definition.Settings.MultipleInstances=2;$definition.Settings.RestartCount=0
$definition.Settings.ExecutionTimeLimit='PT49H'
$action=$definition.Actions.Create(0);$action.Path=$script:python;$action.Arguments='fixed';$action.WorkingDirectory=$script:packageRoot
$roundtrip=$scheduler.NewTask(0);$roundtrip.XmlText=$definition.XmlText
$nativeRoundtripDuration=[string]$roundtrip.Settings.ExecutionTimeLimit
$task=New-FixtureTask;$task.Definition=$roundtrip
Assert-ObserverTask $task 'fixed'
$synthetic=[Xml.XmlConvert]::ToString([TimeSpan]::FromHours(49))
$task.Definition.Settings.ExecutionTimeLimit=$synthetic
Assert-ObserverTask $task 'fixed'
@{native_in_memory_duration=[string]$definition.Settings.ExecutionTimeLimit;
  native_xml_roundtrip_duration=$nativeRoundtripDuration;
  synthetic_canonical_duration=$synthetic;synthetic_equivalence_accepted=$true;
  registered_task_metadata_observed=$false;registered_tasks_created=0;task_runs=0}|ConvertTo-Json
''')
    assert result['native_in_memory_duration']=='PT49H'
    assert result['native_xml_roundtrip_duration']=='PT49H'
    assert result['synthetic_canonical_duration']=='P2DT1H'
    assert result['synthetic_equivalence_accepted']
    assert result['registered_task_metadata_observed'] is False
    assert result['registered_tasks_created']==result['task_runs']==0


def test_running_instance_contract_unchanged():
    result=run_ps(r'''
$task=New-FixtureTask;$task.Enabled=$true;$task.Definition.Settings.Enabled=$true;$task.State=4;$task.Instances=1
Assert-ObserverTask $task 'fixed' $true $task.Guid
$task.Guid='{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}'
$refused=$false;try{Assert-ObserverTask $task 'fixed' $true '{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}'}catch{$refused=$true}
@{wrong_running_instance_refused=$refused;registered_tasks_created=0;task_runs=0}|ConvertTo-Json
''')
    assert result['wrong_running_instance_refused'] and result['registered_tasks_created']==result['task_runs']==0
