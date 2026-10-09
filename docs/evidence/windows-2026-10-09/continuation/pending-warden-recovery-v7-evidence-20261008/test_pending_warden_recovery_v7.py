"""Inert ordinary Windows PS5 recovery flow; no Schedule COM or Apply entrypoint.

Only extracted declarations execute. Task objects are mocks, while RawSD, file
streams, CreateNew journals, share restrictions and callback scopes are real.
All file operations are confined to pytest's disposable fixture directory.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

import pytest

HERE = Path(__file__).parent
WRAPPER = HERE / "resume-pending-warden-r3-v7.ps1"
FIRST_START = HERE / "commission-first-warden-r3-v5.ps1"
REGISTRATION = HERE / "register-stopped-warden-r3.ps1"
PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
ATTEMPT = "8ba7ce50d32c4a96a3c3fd5947a6cd4b"
NONCE = "0123456789abcdef0123456789abcdef"


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(body):
    prefix = r"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){
 Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop
}
function Import-InertDeclarations([string]$Path){
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'FIXTURE_PARSE'}
 foreach($n in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$n.Extent.Text}
}
"""
    for path in (REGISTRATION, FIRST_START, WRAPPER):
        prefix += f"foreach($text in Import-InertDeclarations {quote(path)}){{. ([scriptblock]::Create($text))}}\n"
    code = prefix + body
    scripts = HERE / "pending-recovery-v7-inert-scripts"
    scripts.mkdir(exist_ok=True)
    script = scripts / ("fixture-" + uuid.uuid4().hex + ".ps1")
    with script.open("x", encoding="utf-8", newline="") as stream:
        stream.write(code)
    result = subprocess.run(
        [PS, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
    )
    assert result.returncode == 0, result.stderr + "\n" + result.stdout
    return json.loads(result.stdout.strip().lstrip("\ufeff"))


def setup(tmp_path):
    target = tmp_path / "pending"
    target.mkdir()
    files = {
        "commission-first-warden-r3-v1.py": b"# inert frozen first-start fixture\r\n",
        "worker-native-status-r3.py": b"# inert support fixture\r\n",
        "pipeline.json": b'{"fixture":true}\r\n',
    }
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    packet = dict(schema="cochem-warden-commissioning-inputs/1", nonce=NONCE,
                  config_sha256=hashes["pipeline.json"], source_manifest_sha256="b" * 64,
                  auth_receipt_sha256="a" * 64, auth_attempt=ATTEMPT,
                  model_jobs_submitted=0, automatic_retry_allowed=False)
    files["inputs.json"] = (json.dumps(packet, indent=2) + "\r\n").encode()
    for name, raw in files.items():
        (target / name).write_bytes(raw)
    acl = tmp_path / "ordinary-acl.txt"
    acl.write_bytes(b"ordinary ACL fixture")
    code = f"""
$fixtureRoot={quote(tmp_path)};$targetRoot={quote(target)};$recoveryRoot={quote(tmp_path / 'recovery')}
$aclFixture={quote(acl)};$installRoot=Join-Path $fixtureRoot 'inert-runtime';$basePythonRoot=Join-Path $fixtureRoot 'inert-base'
$python=Join-Path $installRoot 'python.exe';$queueRoot=Join-Path $fixtureRoot 'inert-queue'
$sourceHash='{hashes['commission-first-warden-r3-v1.py']}';$pythonSupportHash='{hashes['worker-native-status-r3.py']}'
$configHash='{hashes['pipeline.json']}';$manifestHash=('b'*64);$revisionHash=('c'*64);$installHash=('d'*64)
$Attempt='{ATTEMPT}';$taskName='Fixture-Warden';$commissionTask='Fixture-Commission';$authHash=('a'*64)
$daemonArguments='-I -B -m cochem_pipeline daemon --config "'+(Join-Path $targetRoot 'pipeline.json')+'" --queue-launch-output "'+$queueRoot+'"'
$held=[Collections.Generic.List[IO.FileStream]]::new();$events=[Collections.Generic.List[string]]::new()
$setCalls=0;$registerCalls=0;$runCalls=0;$wardenRunCalls=0;$waitCalls=0;$newTaskCalls=0;$authReads=0
$failureMode='';$treeFailure=$false;$bindingFailure=$false;$authDrift=$false;$controlFailure=$false;$registrationCollision=$false;$backingFailure=$false
$runtime=[pscustomobject]@{{install_receipt_sha256=$installHash}}
"""
    code += r"""
function Assert-FixturePath([string]$Path){
 $absolute=[IO.Path]::GetFullPath($Path)
 if(-not $absolute.StartsWith($script:fixtureRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'FIXTURE_PATH_ESCAPE'}
}
function Assert-ProtectedPath([string]$Path){Assert-FixturePath $Path;$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}
function Assert-RecoveryTaskBackingAcl([string]$Sddl,[string]$Name){
 $script:events.Add('backing_acl_guard')
 if($Name -cne $script:taskName -or $script:backingFailure){throw 'FIXTURE_BACKING_ACL_REFUSED'}
 $null=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
}
function New-CodeAcl([bool]$Directory){Get-Acl -LiteralPath $script:aclFixture}
function New-ProtectedDirectory([string]$Path){Assert-FixturePath $Path;$null=New-Item -ItemType Directory -Path $Path -ErrorAction Stop;$script:events.Add('recovery_root')}
function Assert-CodeTreeOnce([string]$Path){Assert-FixturePath $Path;$script:events.Add('code_guard');if($script:treeFailure){throw 'FIXTURE_CODE_CUSTODY'};1}
function Assert-R3InstalledBindings{$script:events.Add('runtime_guard');if($script:bindingFailure){[pscustomobject]@{install_receipt_sha256=('e'*64)}}else{$script:runtime}}
function Read-FirstStartAuth{$script:authReads++;$script:events.Add('auth_guard');if($script:authDrift){'e'*64}else{$script:authHash}}
function Read-R3Control([string]$Path,[string]$Pin,[int]$Maximum){
 Assert-FixturePath $Path
 if($script:controlFailure){throw 'FIXTURE_CONTROL_CUSTODY'}
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  if($stream.Length -gt $Maximum){throw 'FIXTURE_CONTROL_BOUND'}
  $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($Pin -and $hash -cne $Pin){throw 'FIXTURE_CONTROL_PIN'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,1024,$true)
  try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $value=[pscustomobject]@{Text=$text;Sha256=$hash;Length=$stream.Length}
  $script:held.Add($stream);$stream=$null;$value
 }finally{if($null -ne $stream){$stream.Dispose()}}
}
function Read-R3Text($Control){$Control.Text}
function Open-VerifiedFile([string]$Path,[string]$Pin,[long]$Length){
 Assert-FixturePath $Path
 if((Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant() -cne $Pin -or (Get-Item -LiteralPath $Path).Length -ne $Length){throw 'FIXTURE_OPEN_PIN'}
 [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
}
function New-InertDefinition {
 $actions=[pscustomobject]@{Count=0;Value=$null}
 $actions|Add-Member ScriptMethod Create {param($type)$this.Count=1;$this.Value=[pscustomobject]@{Type=$type;Path='';Arguments='';WorkingDirectory=''};$this.Value}
 $actions|Add-Member ScriptMethod Item {param($number)if($number -ne 1){throw 'FIXTURE_ACTION_NUMBER'};$this.Value}
 [pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};
  Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$true;MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='PT0S'};
  Triggers=[pscustomobject]@{Count=0};Actions=$actions}
}
function New-InertTask($Definition,[string]$Name,[string]$Sddl){
 $task=[pscustomobject]@{Name=$Name;Definition=$Definition;State=1;Enabled=$false;LastTaskResult=267011;
  LastRunTime=[DateTime]::new(1999,11,30);Xml='<Task fixture="inert" name="'+$Name+'"/>';Instances=0;Sddl=$Sddl}
 $task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=$this.Instances}}
 $task|Add-Member ScriptMethod GetSecurityDescriptor {param($flags)
  if($flags -ne 7){throw 'FIXTURE_SECURITY_FLAGS'}
  if($script:failureMode -ceq 'readback' -and $script:setCalls -gt 0){throw 'FIXTURE_AMBIGUOUS_READBACK'}
  $this.Sddl
 }
 $task|Add-Member ScriptMethod SetSecurityDescriptor {param($sddl,$flags)
  $script:setCalls++;$script:events.Add('set16')
  if($flags -ne 16 -or $this.Name -cne $script:taskName -or -not [IO.File]::Exists((Join-Path $script:recoveryRoot 'recovery-intent.json'))){throw 'FIXTURE_UNSAFE_SETTER'}
  if($script:failureMode -ceq 'setter'){throw 'FIXTURE_AMBIGUOUS_SETTER'}
  $this.Sddl=$sddl
  if($script:failureMode -ceq 'postset_acl'){$this.Sddl='O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)'}
  if($script:failureMode -ceq 'postset_group'){$this.Sddl='O:BAG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)'}
  if($script:failureMode -ceq 'postset_xml'){$this.Xml='<Task fixture="changed"/>'}
  if($script:registrationCollision){$script:folder.Tasks[$script:commissionTask]=New-InertTask (New-InertDefinition) $script:commissionTask $sddl}
 }
 $task|Add-Member ScriptMethod Run {param($nothing)
  if($this.Name -ceq $script:taskName){$script:wardenRunCalls++;throw 'FIXTURE_WARDEN_RUN_FORBIDDEN'}
  $script:runCalls++;$script:events.Add('commission_run')
  if(-not $this.Enabled -or $this.Name -cne $script:commissionTask){throw 'FIXTURE_UNSAFE_RUN'}
  if($script:failureMode -ceq 'run'){throw 'FIXTURE_AMBIGUOUS_RUN'}
  [pscustomobject]@{State=4}
 }
 $task
}
$definition=New-InertDefinition;$action=$definition.Actions.Create(0);$action.Path=$python;$action.Arguments=$daemonArguments;$action.WorkingDirectory=$installRoot
$warden=New-InertTask $definition $taskName 'O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;SY)'
$folder=[pscustomobject]@{Tasks=@{}}
$folder.Tasks[$taskName]=$warden
$folder|Add-Member ScriptMethod GetTask {param($name)
 if($this.Tasks.ContainsKey($name)){return $this.Tasks[$name]}
 throw [Runtime.InteropServices.COMException]::new('missing fixture task',-2147024894)
}
$folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$definition,$flags,$user,$password,$logon,$sddl)
 $script:registerCalls++;$script:events.Add('register18')
 if($name -cne $script:commissionTask -or $flags -ne 18 -or $user -cne 'SYSTEM' -or $null -ne $password -or $logon -ne 5 -or
    $definition.Settings.Enabled -ne $false -or $this.Tasks.ContainsKey($name)){throw 'FIXTURE_UNSAFE_REGISTRATION'}
 Assert-RegisteredTaskAcl $sddl
 if($script:failureMode -ceq 'registration'){throw 'FIXTURE_AMBIGUOUS_REGISTRATION'}
 $this.Tasks[$name]=New-InertTask $definition $name $sddl;$this.Tasks[$name]
}
$scheduler=[pscustomobject]@{}
$scheduler|Add-Member ScriptMethod NewTask {param($flags)$script:newTaskCalls++;New-InertDefinition}
function Assert-CompletedStatusTask($Task){if($Task.State -ne 3 -or $Task.GetInstances(0).Count -ne 0){throw 'FIXTURE_TASK_NOT_TERMINAL'}}
function Wait-FirstStartTask($Instance){
 $script:waitCalls++;$script:events.Add('wait')
 if($script:failureMode -ceq 'wait'){throw 'FIXTURE_AMBIGUOUS_WAIT'}
 $task=$script:folder.Tasks[$script:commissionTask];$task.State=3;$task.LastTaskResult=0
 $packet=[IO.File]::ReadAllText((Join-Path $script:targetRoot 'inputs.json'))|ConvertFrom-Json
 $receipt=[ordered]@{schema='cochem-warden-commissioning/1';nonce=$packet.nonce;system_sid='S-1-5-18';helper_sha256=$script:sourceHash;
  input_sha256=(Get-FileHash -LiteralPath (Join-Path $script:targetRoot 'inputs.json')).Hash.ToLowerInvariant();runtime_root=$script:installRoot;
  install_receipt_sha256=$script:installHash;config_sha256=$script:configHash;source_manifest_sha256=$script:manifestHash;revision_sha256=$script:revisionHash;
  task_name=$script:taskName;queue_launch_output=$script:queueRoot;model_jobs_submitted=0;full_srs_acceptance=$false;automatic_repair_enabled=$false;
  automatic_retry_allowed=$false;status='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED';exactly_one_start_requested=$true;authenticated_profiles_verified=12;
  monitoring_started=$true;monitoring_scope='heartbeat_and_queue_only';controller=@{pid=42;creation_filetime=134000000000000000L;first_sequence=1;final_sequence=2;instance_id=('f'*32)}}
 $null=Write-RegistrationControl (Join-Path $script:targetRoot 'commissioning.json') ($receipt|ConvertTo-Json -Depth 8)
}
"""
    return code, files


COLLECT = r"""
$failure=$null;$result=$null;$restored=$false
$originalCallback=${function:Assert-RegisteredTaskAcl}.ToString()
try{try{$result=Invoke-PendingWardenRecovery $scheduler $folder $runtime $authHash}catch{$failure=$_.Exception.Message}
 $restored=(${function:Assert-RegisteredTaskAcl}.ToString() -ceq $originalCallback)
 [ordered]@{failure=$failure;result=$result;sets=$setCalls;registrations=$registerCalls;runs=$runCalls;warden_runs=$wardenRunCalls;waits=$waitCalls;
  new_task_calls=$newTaskCalls;events=@($events.ToArray());callback_restored=$restored;root_exists=[IO.Directory]::Exists($recoveryRoot);
  recovery_files=@(if([IO.Directory]::Exists($recoveryRoot)){Get-ChildItem -LiteralPath $recoveryRoot -Force|ForEach-Object Name});
  original_xml=$warden.Xml;warden_sddl=$warden.Sddl;warden_enabled=$warden.Enabled;
  commissioning_arguments=$(if($folder.Tasks.ContainsKey($commissionTask) -and $folder.Tasks[$commissionTask].Definition.Actions.Count -eq 1){$folder.Tasks[$commissionTask].Definition.Actions.Value.Arguments}else{$null})
 }|ConvertTo-Json -Depth 16
}finally{foreach($stream in $held){$stream.Dispose()}}
"""


def assert_originals(tmp_path, originals):
    for name, raw in originals.items():
        assert (tmp_path / "pending" / name).read_bytes() == raw


def test_success_reuses_original_auth_packet_repairs_once_and_runs_only_commission(tmp_path):
    code, originals = setup(tmp_path)
    result = run_ps(code + COLLECT)
    assert result["failure"] is None
    assert result["result"]["status"] == "PENDING_REGISTRATION_RECOVERED_AND_COMMISSIONED"
    assert (result["sets"], result["registrations"], result["runs"], result["waits"], result["warden_runs"]) == (1, 1, 1, 1, 0)
    assert result["callback_restored"] and not result["warden_enabled"]
    assert result["original_xml"] == '<Task fixture="inert" name="Fixture-Warden"/>'
    assert result["warden_sddl"] == "O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)"
    packet_hash = hashlib.sha256(originals["inputs.json"]).hexdigest()
    assert f"--nonce {NONCE} --input-sha256 {packet_hash}" in result["commissioning_arguments"]
    capture = json.loads((tmp_path / "recovery" / "preservation.json").read_bytes())
    assert capture["authentication_attempt"] == ATTEMPT
    assert capture["authentication_receipt_sha256"] == "a" * 64
    assert {r["name"]: r["sha256"] for r in capture["original_files"]} == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in originals.items()
    }
    assert (tmp_path / "recovery" / "original-warden.xml").read_bytes() == result["original_xml"].encode()
    events = result["events"]
    assert events.index("runtime_guard") < events.index("recovery_root") < events.index("set16") < events.index("register18") < events.index("commission_run") < events.index("wait")
    assert_originals(tmp_path, originals)


@pytest.mark.parametrize("mutation", [
    "extra_file", "changed_input", "wrong_auth", "active_task", "previously_run", "commission_collision", "outside_acl", "backing_acl", "control_custody", "runtime_custody", "runtime_binding", "auth_drift", "recovery_exists",
])
def test_preflight_failures_precede_every_mutation(tmp_path, mutation):
    code, originals = setup(tmp_path)
    mutations = {
        "extra_file": "[IO.File]::WriteAllText((Join-Path $targetRoot 'extra.keep'),'preserve')",
        "changed_input": "$p=[IO.File]::ReadAllText((Join-Path $targetRoot 'inputs.json'))|ConvertFrom-Json;$p.model_jobs_submitted=1;[IO.File]::WriteAllText((Join-Path $targetRoot 'inputs.json'),($p|ConvertTo-Json))",
        "wrong_auth": "$p=[IO.File]::ReadAllText((Join-Path $targetRoot 'inputs.json'))|ConvertFrom-Json;$p.auth_attempt=('0'*32);[IO.File]::WriteAllText((Join-Path $targetRoot 'inputs.json'),($p|ConvertTo-Json))",
        "active_task": "$warden.Instances=1",
        "previously_run": "$warden.LastTaskResult=0;$warden.LastRunTime=[DateTime]::UtcNow",
        "commission_collision": "$folder.Tasks[$commissionTask]=New-InertTask (New-InertDefinition) $commissionTask 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'",
        "outside_acl": "$warden.Sddl='O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;WD)'",
        "backing_acl": "$backingFailure=$true",
        "control_custody": "$controlFailure=$true",
        "runtime_custody": "$treeFailure=$true",
        "runtime_binding": "$bindingFailure=$true",
        "auth_drift": "$authDrift=$true",
        "recovery_exists": "$null=[IO.Directory]::CreateDirectory($recoveryRoot);[IO.File]::WriteAllText((Join-Path $recoveryRoot 'partial.keep'),'preserve')",
    }
    result = run_ps(code + mutations[mutation] + "\n" + COLLECT)
    assert result["failure"] is not None
    assert result["sets"] == result["registrations"] == result["runs"] == result["warden_runs"] == 0
    assert result["root_exists"] is (mutation == "recovery_exists")
    assert result["callback_restored"]
    for name, raw in originals.items():
        if name != "inputs.json" or mutation not in ("changed_input", "wrong_auth"):
            assert (tmp_path / "pending" / name).read_bytes() == raw


@pytest.mark.parametrize("failure,sets,registrations,runs,waits", [
    ("setter", 1, 0, 0, 0), ("readback", 1, 0, 0, 0), ("postset_acl", 1, 0, 0, 0), ("postset_group", 1, 0, 0, 0), ("postset_xml", 1, 0, 0, 0),
    ("registration", 1, 1, 0, 0), ("run", 1, 1, 1, 0), ("wait", 1, 1, 1, 1),
])
def test_uncertain_operations_stop_without_retry_and_preserve_journal(tmp_path, failure, sets, registrations, runs, waits):
    code, originals = setup(tmp_path)
    result = run_ps(code + f"$failureMode='{failure}'\n" + COLLECT)
    assert result["failure"] is not None
    assert (result["sets"], result["registrations"], result["runs"], result["waits"]) == (sets, registrations, runs, waits)
    assert result["warden_runs"] == 0 and result["callback_restored"] and result["root_exists"]
    assert "recovery-intent.json" in result["recovery_files"]
    intent = json.loads((tmp_path / "recovery" / "recovery-intent.json").read_bytes())
    assert intent["setter_flags"] == 16 and intent["registration_flags"] == 18
    assert intent["automatic_retry_allowed"] is False
    assert_originals(tmp_path, originals)


def test_commission_collision_after_descriptor_repair_stops_before_register_or_run(tmp_path):
    code, originals = setup(tmp_path)
    value = run_ps(code + "$registrationCollision=$true\n" + COLLECT)
    assert value["failure"] is not None
    assert (value["sets"], value["registrations"], value["runs"], value["warden_runs"]) == (1, 0, 0, 0)
    assert value["callback_restored"] and "recovery-intent.json" in value["recovery_files"]
    assert_originals(tmp_path, originals)


@pytest.mark.parametrize("fail", [False, True])
def test_temporary_acl_callback_is_visible_to_actual_shape_check_and_restored(tmp_path, fail):
    code, originals = setup(tmp_path)
    if fail:
        code += "$warden.Sddl='O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;WD)'\n"
    value = run_ps(code + r"""
$saved=${function:Assert-RegisteredTaskAcl}.ToString();$refused=$false
try{try{$null=Get-RecoveryPendingState $folder $authHash}catch{$refused=$true}
 $canonicalStillRefuses=$false;try{Assert-RegisteredTaskAcl 'O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;SY)'}catch{$canonicalStillRefuses=$true}
 @{refused=$refused;restored=(${function:Assert-RegisteredTaskAcl}.ToString() -ceq $saved);canonical_refuses=$canonicalStillRefuses}|ConvertTo-Json
}finally{foreach($stream in $held){$stream.Dispose()}}
""")
    assert value == {"refused": fail, "restored": True, "canonical_refuses": True}
    assert_originals(tmp_path, originals)


def test_completed_or_partial_recovery_namespace_prevents_second_attempt(tmp_path):
    code, originals = setup(tmp_path)
    result = run_ps(code + r"""
try{
 $null=Invoke-PendingWardenRecovery $scheduler $folder $runtime $authHash
 $before=@($setCalls,$registerCalls,$runCalls);$refused=$false
 try{$null=Invoke-PendingWardenRecovery $scheduler $folder $runtime $authHash}catch{$refused=$true}
 @{refused=$refused;before=$before;after=@($setCalls,$registerCalls,$runCalls)}|ConvertTo-Json
}finally{foreach($stream in $held){$stream.Dispose()}}
""")
    assert result["refused"] and result["before"] == result["after"] == [1, 1, 1]
    assert_originals(tmp_path, originals)


def test_wrapper_contains_no_provider_login_or_warden_run_or_destructive_task_calls():
    source = WRAPPER.read_text(encoding="utf-8")
    assert "8ba7ce50d32c4a96a3c3fd5947a6cd4b" in source
    assert "8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e" in source
    for forbidden in ("login_pipeline_worker", "authenticate-native", "DeleteTask(", ".Stop(", "$warden.Run(", "$pending.Warden.Run(", "Register-ScheduledTask"):
        assert forbidden not in source
