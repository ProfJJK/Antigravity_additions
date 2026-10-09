#Requires -Version 5.1
<# Ordinary-user Task Scheduler representation fixture. New unique disabled,
   trigger-free, demand-start-disabled user tasks only. No task Run/Delete,
   credentials, SYSTEM tasks, pipeline state or existing task changes. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce,
    [Parameter(Mandatory=$true)][string]$Report,
    [switch]$ContinueAfterPrincipalNormalization
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){
    Import-Module ([IO.Path]::Combine($PSHOME,"Modules\$module\$module.psd1")) -ErrorAction Stop
}

function Convert-FixtureDescriptor {
    param([Parameter(Mandatory=$true)][string]$Sddl)
    $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
    $aces=@()
    if($null -ne $descriptor.DiscretionaryAcl){
        foreach($ace in $descriptor.DiscretionaryAcl){
            $aces+=[ordered]@{type=[int]$ace.AceType;flags=[int]$ace.AceFlags;mask=$ace.AccessMask;sid=$ace.SecurityIdentifier.Value}
        }
    }
    [ordered]@{
        sddl=$Sddl
        owner=$descriptor.Owner.Value
        group=$descriptor.Group.Value
        control_flags=[int]$descriptor.ControlFlags
        dacl_protected=[bool]($descriptor.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected)
        dacl_present=[bool]($descriptor.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclPresent)
        ace_count=$aces.Count
        aces=$aces
    }
}

function Read-FixtureTask {
    param($Task,[string]$Name)
    if($Name -cne $Task.Name -or -not $Name.StartsWith($script:prefix,[StringComparison]::Ordinal)){throw 'FIXTURE_TASK_NAME'}
    $definition=$Task.Definition
    $action=$definition.Actions.Item(1)
    $principal=[Security.Principal.NTAccount]::new($definition.Principal.UserId).Translate([Security.Principal.SecurityIdentifier]).Value
    if($Task.Enabled -or $Task.State -ne 1 -or $Task.GetInstances(0).Count -ne 0 -or
       $definition.Settings.Enabled -or $definition.Settings.AllowDemandStart -or $definition.Triggers.Count -ne 0 -or
       $principal -cne $script:identity.User.Value -or $definition.Principal.LogonType -ne 3 -or
       $definition.Principal.RunLevel -ne 0 -or $definition.Actions.Count -ne 1 -or $action.Type -ne 0 -or
       $action.Path -cne $script:cmd -or $action.Arguments -cne '/d /c exit 0' -or
       $Task.LastTaskResult -ne 267011){throw 'FIXTURE_DISABLED_NEVER_RUN_DEFINITION'}
    $result=[ordered]@{
        name=$Name
        enabled=[bool]$Task.Enabled
        state=[int]$Task.State
        instance_count=[int]$Task.GetInstances(0).Count
        last_task_result=[int]$Task.LastTaskResult
        last_run_time_utc=$Task.LastRunTime.ToUniversalTime().ToString('o')
        triggers=[int]$definition.Triggers.Count
        allow_demand_start=[bool]$definition.Settings.AllowDemandStart
        run_level=[int]$definition.Principal.RunLevel
        logon_type=[int]$definition.Principal.LogonType
        service_descriptor=(Convert-FixtureDescriptor ($Task.GetSecurityDescriptor(7)))
    }
    # Task file SECURITY metadata only; XML/action contents are never opened.
    $file=[IO.Path]::Combine($env:SystemRoot,('System32\Tasks\'+$Name))
    try{
        $acl=Get-Acl -LiteralPath $file -ErrorAction Stop
        $result.task_file_security=(Convert-FixtureDescriptor $acl.Sddl)
        $result.task_file_security_readable=$true
    }catch{
        $result.task_file_security_readable=$false
        $result.task_file_security_failure=[ordered]@{error_type=$_.Exception.GetType().Name;hresult=$_.Exception.HResult}
    }
    $result
}

$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$administrator=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$prefix='CoChem-Fixture-TaskAcl-20261008-'+$Nonce+'-'
$cmd=[IO.Path]::Combine($env:SystemRoot,'System32\cmd.exe')
$receipt=[ordered]@{
    schema='cochem-task-scheduler-acl-roundtrip-fixture/1'
    status='HELD'
    nonce=$Nonce
    powershell_version=$PSVersionTable.PSVersion.ToString()
    is_64_bit_process=[Environment]::Is64BitProcess
    administrator=$administrator
    current_user_sid=$identity.User.Value
    tasks_created=@()
    prior_owned_fixture_tasks_read_only=@()
    observations=@()
    security_descriptor_sets=0
    task_run_calls=0
    tasks_deleted=0
    existing_tasks_changed=$false
    credentials_accessed=$false
    pipeline_state_accessed=$false
    system_tasks_created=0
    started_utc=[DateTime]::UtcNow.ToString('o')
}
$phase='ordinary_user_boundary';$exitCode=2
try{
    if($administrator -or $PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess){throw 'FIXTURE_ORDINARY_PS5_REQUIRED'}
    if([IO.File]::Exists($Report)){throw 'FIXTURE_REPORT_ALREADY_EXISTS'}
    $sid=$identity.User.Value
    $sddl='O:'+$sid+'G:'+$sid+'D:P(A;;FA;;;'+$sid+')(A;;FA;;;SY)(A;;FA;;;BA)'
    $receipt.requested_descriptor=Convert-FixtureDescriptor $sddl
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    foreach($flags in @(2,18)){
        $name=$prefix+'create'+$flags
        if($flags -eq 2 -and $ContinueAfterPrincipalNormalization){
            $phase='readback_original_2'
            $existing=$folder.GetTask($name)
            $receipt.observations+=@([ordered]@{stage='registration';registration_flags=$flags;task=(Read-FixtureTask $existing $name)})
            $receipt.prior_owned_fixture_tasks_read_only+=@($name)
            continue
        }
        $phase='absence_'+$flags
        $present=$null
        try{$present=$folder.GetTask($name)}catch{
            $failure=$_.Exception;$notFound=$false
            while($null -ne $failure){if($failure.HResult -eq -2147024894){$notFound=$true};$failure=$failure.InnerException}
            if(-not $notFound){throw 'FIXTURE_TASK_ABSENCE_UNKNOWN'}
        }
        if($null -ne $present){throw 'FIXTURE_TASK_ALREADY_EXISTS'}
        $definition=$scheduler.NewTask(0)
        $definition.RegistrationInfo.Description='Inert ordinary-user ACL representation fixture. Disabled, no triggers, demand start disabled, never run; preserve for review.'
        $definition.Principal.UserId=$identity.Name;$definition.Principal.LogonType=3;$definition.Principal.RunLevel=0
        $definition.Settings.Enabled=$false;$definition.Settings.AllowDemandStart=$false
        $definition.Settings.MultipleInstances=2;$definition.Settings.RestartCount=0;$definition.Settings.ExecutionTimeLimit='PT1M'
        $action=$definition.Actions.Create(0);$action.Path=$cmd;$action.Arguments='/d /c exit 0'
        $phase='register_'+$flags
        $task=$folder.RegisterTaskDefinition($name,$definition,$flags,$identity.Name,$null,3,$sddl)
        $receipt.tasks_created+=@($name)
        $phase='readback_'+$flags
        $observation=Read-FixtureTask $task $name
        $receipt.observations+=@([ordered]@{stage='registration';registration_flags=$flags;task=$observation})
        if($flags -eq 18){
            $phase='set_descriptor_16'
            $task.SetSecurityDescriptor($sddl,16)
            $receipt.security_descriptor_sets++
            $phase='readback_after_set'
            $after=$folder.GetTask($name)
            $receipt.observations+=@([ordered]@{stage='after_set_security_descriptor_16';registration_flags=$flags;task=(Read-FixtureTask $after $name)})
        }
    }
    $receipt.status='ORDINARY_USER_TASK_ACL_ROUNDTRIP_OBSERVED'
    $exitCode=0
}catch{
    $receipt.failure=[ordered]@{phase=$phase;error_type=$_.Exception.GetType().Name;hresult=$_.Exception.HResult}
    if($_.Exception.Message -cmatch '^FIXTURE_[A-Z_]+$'){$receipt.failure.code=$_.Exception.Message}
    $chain=@();$failure=$_.Exception
    while($null -ne $failure -and $chain.Count -lt 4){$chain+=@([ordered]@{error_type=$failure.GetType().Name;hresult=$failure.HResult});$failure=$failure.InnerException}
    $receipt.failure.chain=$chain
}finally{
    $receipt.finished_utc=[DateTime]::UtcNow.ToString('o')
    $stream=[IO.File]::Open($Report,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{
        $writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false))
        try{$writer.Write(($receipt|ConvertTo-Json -Depth 12));$writer.Flush();$stream.Flush($true)}finally{$writer.Dispose()}
    }finally{$stream.Dispose()}
}
exit $exitCode
