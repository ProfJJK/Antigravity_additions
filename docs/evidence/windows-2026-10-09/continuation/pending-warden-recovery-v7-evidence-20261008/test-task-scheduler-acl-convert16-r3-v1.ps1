#Requires -Version 5.1
<# One-time ACL conversion on our exact owned inert flags2 fixture only.
   Never registers, runs, enables or deletes a task. No deployment task/state. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){
    Import-Module ([IO.Path]::Combine($PSHOME,"Modules\$module\$module.psd1")) -ErrorAction Stop
}

function Write-NewDurable {
    param([string]$Path,[byte[]]$Bytes)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
function Get-BytesSha256 {
    param([byte[]]$Bytes)
    $hasher=[Security.Cryptography.SHA256]::Create()
    try{([BitConverter]::ToString($hasher.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()}
}
function Convert-FixtureDescriptor {
    param([string]$Sddl)
    $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
    $aces=@()
    foreach($ace in $descriptor.DiscretionaryAcl){
        $aces+=@([ordered]@{type=[int]$ace.AceType;flags=[int]$ace.AceFlags;mask=$ace.AccessMask;sid=$ace.SecurityIdentifier.Value})
    }
    [ordered]@{sddl=$Sddl;owner=$descriptor.Owner.Value;group=$descriptor.Group.Value;
        dacl_protected=[bool]($descriptor.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected);
        ace_count=$aces.Count;aces=$aces}
}
function Read-OwnedFixture {
    param($Task)
    if($Task.Name -cne $script:taskName){throw 'FIXTURE_TASK_NAME'}
    $definition=$Task.Definition
    if($definition.Actions.Count -ne 1){throw 'FIXTURE_ACTION_COUNT'}
    $action=$definition.Actions.Item(1)
    $principal=[Security.Principal.NTAccount]::new($definition.Principal.UserId).Translate([Security.Principal.SecurityIdentifier]).Value
    if($Task.Enabled -or $Task.State -ne 1 -or $Task.GetInstances(0).Count -ne 0 -or
        $definition.Settings.Enabled -or $definition.Settings.AllowDemandStart -or $definition.Triggers.Count -ne 0 -or
        $principal -cne $script:identity.User.Value -or $definition.Principal.LogonType -ne 3 -or
        $definition.Principal.RunLevel -ne 0 -or $action.Type -ne 0 -or
        $action.Path -cne $script:cmd -or $action.Arguments -cne '/d /c exit 0' -or
        $Task.LastTaskResult -ne 267011){throw 'FIXTURE_DISABLED_NEVER_RUN_DEFINITION'}
    $service=Convert-FixtureDescriptor ($Task.GetSecurityDescriptor(7))
    $file=Join-Path $env:SystemRoot ('System32\Tasks\'+$script:taskName)
    $fileAcl=Convert-FixtureDescriptor (Get-Acl -LiteralPath $file -ErrorAction Stop).Sddl
    if($service.owner -cne $script:identity.User.Value -or $service.group -cne $script:identity.User.Value -or
        $fileAcl.owner -cne $script:identity.User.Value -or $fileAcl.group -cne $script:identity.User.Value){throw 'FIXTURE_OWNER_SID'}
    [ordered]@{name=$Task.Name;enabled=[bool]$Task.Enabled;state=[int]$Task.State;
        instance_count=[int]$Task.GetInstances(0).Count;last_task_result=[int]$Task.LastTaskResult;
        last_run_time_utc=$Task.LastRunTime.ToUniversalTime().ToString('o');triggers=[int]$definition.Triggers.Count;
        allow_demand_start=[bool]$definition.Settings.AllowDemandStart;principal_sid=$principal;
        logon_type=[int]$definition.Principal.LogonType;run_level=[int]$definition.Principal.RunLevel;
        action_path=$action.Path;action_arguments=$action.Arguments;service_descriptor=$service;task_file_security=$fileAcl}
}
function Assert-ExactRequestedAcl {
    param($Descriptor)
    if(-not $Descriptor.dacl_protected -or $Descriptor.ace_count -ne 3){throw 'FIXTURE_FINAL_PROTECTED_ACL'}
    $seen=@{}
    foreach($ace in $Descriptor.aces){
        if($ace.type -ne 0 -or $ace.flags -ne 0 -or $ace.mask -ne 2032127 -or
            $ace.sid -cnotin @($script:identity.User.Value,'S-1-5-18','S-1-5-32-544') -or $seen.ContainsKey($ace.sid)){
            throw 'FIXTURE_FINAL_ACL_GRANTS'
        }
        $seen[$ace.sid]=$true
    }
}

$nonce='bbd9bf8061764e6ba8cdefa47fb6a149'
$taskName='CoChem-Fixture-TaskAcl-20261008-'+$nonce+'-create2'
$prefix=Join-Path $PSScriptRoot ('task-scheduler-acl-convert16-r3-v1-'+$nonce)
$report=$prefix+'.json';$intent=$prefix+'-intent.json'
$beforeXmlPath=$prefix+'-before.xml';$afterXmlPath=$prefix+'-after.xml'
$priorPath=Join-Path $PSScriptRoot ('task-scheduler-acl-roundtrip-r3-v1-'+$nonce+'-final.json')
$priorSha='00bd4af0b3d05de9d072e16cd562e105f43060f78df3ea099eb0b0d6ff015bd9'
$utf8=[Text.UTF8Encoding]::new($false)
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$administrator=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$cmd=Join-Path $env:SystemRoot 'System32\cmd.exe'
$receipt=[ordered]@{schema='cochem-task-scheduler-acl-conversion-fixture/1';status='HELD';nonce=$nonce;
    task_name=$taskName;powershell_version=$PSVersionTable.PSVersion.ToString();administrator=$administrator;
    is_64_bit_process=[Environment]::Is64BitProcess;current_user_sid=$identity.User.Value;prior_receipt_sha256=$priorSha;
    tasks_registered=0;security_descriptor_sets=0;task_run_calls=0;tasks_enabled=0;tasks_deleted=0;
    deployment_tasks_changed=$false;credentials_accessed=$false;pipeline_state_accessed=$false;
    started_utc=[DateTime]::UtcNow.ToString('o')}
$phase='ordinary_boundary';$exitCode=2;$mayWriteReport=$false
try{
    if($administrator -or $PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess){throw 'FIXTURE_ORDINARY_PS5_REQUIRED'}
    foreach($path in @($report,$intent,$beforeXmlPath,$afterXmlPath)){
        if([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)){throw 'FIXTURE_ONE_TIME_ARTIFACT_EXISTS'}
    }
    $mayWriteReport=$true
    $phase='prior_receipt_binding'
    $priorBytes=[IO.File]::ReadAllBytes($priorPath)
    if((Get-BytesSha256 $priorBytes) -cne $priorSha){throw 'FIXTURE_PRIOR_RECEIPT_HASH'}
    $prior=$utf8.GetString($priorBytes)|ConvertFrom-Json
    if($prior.nonce -cne $nonce -or $prior.current_user_sid -cne $identity.User.Value -or
        $prior.status -cne 'ORDINARY_USER_TASK_ACL_ROUNDTRIP_OBSERVED'){throw 'FIXTURE_PRIOR_RECEIPT_IDENTITY'}
    $saved=@($prior.observations|Where-Object {$_.stage -ceq 'registration' -and $_.registration_flags -eq 2})
    if($saved.Count -ne 1 -or $saved[0].task.name -cne $taskName){throw 'FIXTURE_PRIOR_TASK_BINDING'}
    $phase='before_observation'
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    $task=$folder.GetTask($taskName)
    $before=Read-OwnedFixture $task
    if($before.service_descriptor.sddl -cne $saved[0].task.service_descriptor.sddl -or
        $before.task_file_security.sddl -cne $saved[0].task.task_file_security.sddl -or
        $before.service_descriptor.dacl_protected -or $before.service_descriptor.ace_count -ne 4){throw 'FIXTURE_BEFORE_DESCRIPTOR_CHANGED'}
    $beforeXml=[string]$task.Xml;$beforeBytes=$utf8.GetBytes($beforeXml)
    $receipt.before=$before;$receipt.xml_before_sha256=Get-BytesSha256 $beforeBytes
    $receipt.xml_before_bytes=$beforeBytes.Length
    $sid=$identity.User.Value
    $sddl='O:'+$sid+'G:'+$sid+'D:P(A;;FA;;;'+$sid+')(A;;FA;;;SY)(A;;FA;;;BA)'
    $receipt.requested_descriptor=Convert-FixtureDescriptor $sddl
    $phase='immutable_capture_and_intent'
    Write-NewDurable $beforeXmlPath $beforeBytes
    $intentData=[ordered]@{schema='cochem-task-acl-conversion-intent/1';nonce=$nonce;task_name=$taskName;
        current_user_sid=$identity.User.Value;prior_receipt_sha256=$priorSha;xml_before_sha256=$receipt.xml_before_sha256;
        source_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $PSCommandPath).Hash.ToLowerInvariant();
        setter_flags=16;requested_sddl=$sddl;automatic_retry_allowed=$false;created_utc=[DateTime]::UtcNow.ToString('o')}
    Write-NewDurable $intent ($utf8.GetBytes(($intentData|ConvertTo-Json -Depth 8)))
    $phase='set_security_descriptor_16'
    $receipt.security_descriptor_set_attempted=$true
    $task.SetSecurityDescriptor($sddl,16)
    $receipt.security_descriptor_sets=1
    $phase='after_observation'
    $afterTask=$folder.GetTask($taskName)
    $after=Read-OwnedFixture $afterTask
    $afterXml=[string]$afterTask.Xml;$afterBytes=$utf8.GetBytes($afterXml)
    Write-NewDurable $afterXmlPath $afterBytes
    $receipt.after=$after;$receipt.xml_after_sha256=Get-BytesSha256 $afterBytes
    $receipt.xml_after_bytes=$afterBytes.Length
    $receipt.xml_byte_identical=($beforeXml -ceq $afterXml -and $receipt.xml_before_sha256 -ceq $receipt.xml_after_sha256 -and $beforeBytes.Length -eq $afterBytes.Length)
    if(-not $receipt.xml_byte_identical){throw 'FIXTURE_XML_CHANGED'}
    Assert-ExactRequestedAcl $after.service_descriptor
    Assert-ExactRequestedAcl $after.task_file_security
    if($after.last_run_time_utc -cne $before.last_run_time_utc){throw 'FIXTURE_LAST_RUN_CHANGED'}
    $receipt.status='ORDINARY_USER_FLAGS2_TASK_CONVERTED_WITH_SETSD16'
    $exitCode=0
}catch{
    $receipt.failure=[ordered]@{phase=$phase;error_type=$_.Exception.GetType().Name;hresult=$_.Exception.HResult}
    if($_.Exception.Message -cmatch '^FIXTURE_[A-Z_]+$'){$receipt.failure.code=$_.Exception.Message}
}finally{
    $receipt.finished_utc=[DateTime]::UtcNow.ToString('o')
    if($mayWriteReport){Write-NewDurable $report ($utf8.GetBytes(($receipt|ConvertTo-Json -Depth 12)))}
}
exit $exitCode
