#Requires -Version 5.1
<# Default is read-only. -Apply preserves the interrupted guard, creates a
separate fresh SYSTEM presence-check receipt, then invokes its frozen installer.
No daemon, supervisor migration, sensor, RAM or native model is activated. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSEdition -ne 'Desktop') {throw 'Use Windows PowerShell 5.1.'}
foreach ($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security','CimCmdlets')) {Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}

function Get-FrozenFunctions {
    param([string]$Path,[string]$Expected,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $sha=[Security.Cryptography.SHA256]::Create()
        try {$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if ($hash -ne $Expected) {throw "Frozen helper changed: $Path"}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Frozen helper has parser errors.'}
        foreach($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($function.Name -in $Names){$function.Extent.Text}}
    }finally{$stream.Dispose()}
}
function Wait-CompletedPrecheck {
    param($Instance,$Task,[string]$ReceiptPath,[string]$Nonce)
    $deadline=[DateTime]::UtcNow.AddSeconds(75)
    do {
        Start-Sleep -Milliseconds 250
        $finished=$false
        try {$Instance.Refresh();$finished=$Instance.State -notin @(2,4)}
        catch {
            $exception=$_.Exception;$completed=$false
            while($null -ne $exception){if($exception.HResult -eq -2147216629){$completed=$true;break};$exception=$exception.InnerException}
            # 0x8004130B: the returned running instance has already completed.
            # Missing task, access denied and all other errors remain failures.
            if(-not $completed){throw};$finished=$true
        }
        if([DateTime]::UtcNow -gt $deadline){throw 'Fresh resume precheck timed out; preserve its task and receipt directory.'}
    }while(-not $finished)
    if($Task.State -in @(2,4) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 0){throw 'Fresh precheck has not completed successfully; no installer was invoked.'}
    Assert-ProtectedPath $ReceiptPath
    $item=Get-Item -LiteralPath $ReceiptPath -Force
    if($item.Length -gt 4096){throw 'Unexpected precheck receipt size.'}
    $receipt=Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8|ConvertFrom-Json
    Assert-IdentityReceipt $receipt $Nonce
}
function Assert-OriginalPrecheckTask {
    param($Task)
    $definition=$Task.Definition
    if($definition.Principal.UserId -notin @('SYSTEM','S-1-5-18','NT AUTHORITY\SYSTEM') -or $definition.Principal.LogonType -ne 5 -or $definition.Principal.RunLevel -ne 1 -or $definition.Triggers.Count -ne 0 -or $definition.Actions.Count -ne 1){throw 'Original precheck task identity or shape differs.'}
    $action=$definition.Actions.Item(1)
    $expected='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $script:guardRoot 'identity-precheck.ps1')+'" -Nonce 27c861adcbd741798cf66d7c9bb1c8b4'
    if($action.Type -ne 0 -or $action.Path -ne "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -or $action.Arguments -ne $expected -or $action.WorkingDirectory -ne $script:guardRoot){throw 'Original precheck action or nonce differs.'}
    if($Task.State -in @(2,4) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 0){throw 'Original precheck is not stopped with a successful result.'}
    $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($Task.GetSecurityDescriptor(7))
    if($descriptor.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or $null -eq $descriptor.DiscretionaryAcl){throw 'Original task security descriptor is not protected authority.'}
    foreach($ace in $descriptor.DiscretionaryAcl){if($ace.SecurityIdentifier.Value -notin @('S-1-5-18','S-1-5-32-544')){throw 'Original task includes an unexpected trustee.'}}
}
function Assert-ExactGuardTree {
    param($Files,[string]$Root)
    $expected=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach($file in $Files){$null=$expected.Add($file.destination);Assert-ProtectedPath $file.destination;$stream=Open-VerifiedFile $file.destination $file.sha256 $file.length;$stream.Dispose()}
    $null=$expected.Add((Join-Path $Root 'identity-precheck.json'))
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Root);$count=0
    while($queue.Count){$item=Get-Item -LiteralPath ($queue.Dequeue()) -Force;if(++$count -gt 10000 -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Unexpected protected guard tree or reparse path.'};Assert-ProtectedPath $item.FullName;if($item.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$queue.Enqueue($child.FullName)}}elseif(-not $expected.Remove($item.FullName)){throw 'Unexpected file in preserved guard tree.'}}
    if($expected.Count){throw 'Preserved guard tree is incomplete.'}
}
function Invoke-ResumePrecheck {
    param($Scheduler,$Folder)
    Assert-TaskAbsent $Folder 'CoChem-4.2.7-Install-Resume-Preflight'
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$Scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Fresh read-only presence recheck after observer fast-completion race; original task/receipt preserved.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT2M'
    $action=$definition.Actions.Create(0);$action.Path="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $action.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $script:resumeRoot 'identity-precheck.ps1')+'" -Nonce '+$nonce
    $action.WorkingDirectory=$script:resumeRoot
    $task=$Folder.RegisterTaskDefinition('CoChem-4.2.7-Install-Resume-Preflight',$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null)
    Wait-CompletedPrecheck $instance $task (Join-Path $script:resumeRoot 'identity-precheck.json') $nonce
}

$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$copyNames=@('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory','Copy-VerifiedPayload')
foreach($definition in @(Get-FrozenFunctions (Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1') '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' $copyNames)){. ([scriptblock]::Create($definition))}
$guardNames=@('Assert-TaskAbsent','Assert-FreshDeployment','Get-StoppedInstallerParameters','Get-StoppedInstallerArguments','Invoke-StoppedInstaller','Get-GuardFiles','Assert-IdentityReceipt')
foreach($definition in @(Get-FrozenFunctions (Join-Path $repo 'scripts\install_aetherdesk_427_stopped.ps1') '07330a95301ae0cfdaf4e5f594d4959fdfc43366afdebe47352461f9a8fd6195' $guardNames)){. ([scriptblock]::Create($definition))}
if($env:COMPUTERNAME -ne 'AETHERDESK' -or $env:ProgramFiles -ne 'C:\Program Files'){throw 'This resume is bound to AETHERDESK and its preserved installation.'}
$programFiles='C:\Program Files';$base='C:\Program Files\CoChem'
$guardRoot=Join-Path $base 'InstallGuard4.2.7-windows-20261006';$resumeRoot=Join-Path $base 'InstallResume4.2.7-windows-20261006'
$installRoot=Join-Path $base 'Pipeline4.2.7-windows-20261006';$dataRoot='C:\ProgramData\CoChemPipeline427';$tokenFile='C:\Users\ansac\CoChem427\controller.token'
Initialize-FileIdentity
$inventory=Read-Inventory (Join-Path $repo 'config\windows\aetherdesk-427.stopped-install-source.json') 'ee994719727936497b0436448a15e80dc18f11a8b108601fd43e5a62b918260b'
$files=@(Get-GuardFiles $inventory);Assert-ExactGuardTree $files $guardRoot
$originalReceipt=Read-Inventory (Join-Path $guardRoot 'identity-precheck.json') '8c908d1217ce65619d96d1e943da9d4876f7a6943c6057f8884c0920b83d147b'
Assert-IdentityReceipt $originalReceipt '27c861adcbd741798cf66d7c9bb1c8b4'
$checker=Join-Path $PSScriptRoot 'check_resume_fresh_identities.ps1';$checkerSize=(Get-Item -LiteralPath $checker).Length
$stream=Open-VerifiedFile $checker '3073a16b9da6c5c43d9dac1f36819f65725ab0fdaf6d8c7658077b465bf45c08' $checkerSize;$stream.Dispose()
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
Assert-FreshDeployment $folder -GuardCreated
Assert-TaskAbsent $folder 'CoChem-4.2.7-Install-Resume-Preflight'
if(Test-Path -LiteralPath $resumeRoot -ErrorAction Stop){throw 'Preserve existing resume root; this recovery does not overwrite or retry it.'}
$taskStatus='VERIFIED'
try{Assert-OriginalPrecheckTask ($folder.GetTask('CoChem-4.2.7-Install-Preflight'))}catch{if($Apply){throw};$taskStatus='UNVERIFIED_REQUIRES_ADMINISTRATOR';$taskError=$_.Exception.Message}
$invoked=$false
if($Apply){
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw '-Apply requires an elevated administrator; no resume task or installation was invoked.'}
    # Reattest exact protected toolchain bytes before any new task or installer.
    $payload=Read-Inventory (Join-Path $repo 'config\windows\aetherdesk-427.payloads.json') 'db9e6567095326bbeaf528a400525ee047a7887b5a5479bf8ac0ba6f8b4625d0'
    foreach($file in $payload.files){if($file.destination.StartsWith('Toolchain4.2.7-windows-20261006/')){$path=Join-Path $base $file.destination;Assert-ProtectedPath $path;$stream=Open-VerifiedFile $path $file.sha256 $file.length;$stream.Dispose()}}
    New-ProtectedDirectory $resumeRoot
    Copy-VerifiedPayload ([pscustomobject]@{source=$checker;destination=(Join-Path $resumeRoot 'identity-precheck.ps1');sha256='3073a16b9da6c5c43d9dac1f36819f65725ab0fdaf6d8c7658077b465bf45c08';length=$checkerSize})
    Invoke-ResumePrecheck $scheduler $folder
    Assert-FreshDeployment $folder -GuardCreated
    $invoked=$true;Invoke-StoppedInstaller (Join-Path $guardRoot 'repository\scripts\install_pipeline_windows.ps1')
}
[ordered]@{schema='cochem-stopped-install-resume/1';mode=$(if($invoked){'STOPPED_PIPELINE_INSTALL_INVOKED'}else{'READ_ONLY_PREVIEW'});original_source_files_verified=$files.Count;original_receipt_sha256='8c908d1217ce65619d96d1e943da9d4876f7a6943c6057f8884c0920b83d147b';original_task=$taskStatus;original_artifacts_preserved=$true;fresh_system_precheck=$(if($invoked){'PASSED'}else{'UNRUN'});installer_invoked=$invoked;worker_identities=6;max_execution_slots=4;daemon_registered=$false;supervisor_migration_invoked=$false;activation_ready=$false}|ConvertTo-Json -Depth 4
