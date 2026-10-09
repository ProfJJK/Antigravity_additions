#Requires -Version 5.1
<# One sequential six-identity acceptance command. Default only plans.
   -Apply prechecks ALL six fresh roots/tasks before slot1, then stops on the
   first failed task/receipt. No cleanup, automatic retry or daemon activation.
   Keep every successful, failed and partial per-slot artifact for review. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files'
$wrapper=Join-Path $PSScriptRoot 'check-worker-denials-r3.ps1'
$source=Join-Path $PSScriptRoot 'worker-denial-acceptance-r3.py'
$wrapperHash='5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d'
$sourceHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$helper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
function Get-PinnedCopyFunctions {
    param([string]$Path)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'){throw 'Reviewed custody helper changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Custody helper parse error.'}
        foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
            if($f.Name -in @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory','Get-LocalPath')){$f.Extent.Text}
        }
    }finally{$stream.Dispose()}
}
function Get-AllFreshTargetHolds {
    param($Folder)
    foreach($n in 1..6){
        $slot="slot$n";$name="CoChem-4.2.7-WorkerDenial-r3-$slot"
        $root="C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-$slot"
        if(Test-Path -LiteralPath $root -ErrorAction Stop){"Existing evidence root must be preserved: $slot"}
        try{$null=$Folder.GetTask($name);"Existing one-shot task must be preserved: $slot"}catch{
            $errorValue=$_.Exception;$missing=$false
            while($null -ne $errorValue){if($errorValue.HResult -eq -2147024894){$missing=$true};$errorValue=$errorValue.InnerException}
            if(-not $missing){"Cannot establish task absence: $slot"}
        }
    }
}
function Assert-PassedSlotResult {
    param($Result,[string]$Slot)
    if($Result.schema -ne 'cochem-worker-denial-acceptance-task-result/1' -or $Result.slot -cne $Slot -or $Result.status -ne 'HANDLE_DENIALS_VERIFIED' -or $Result.cleanup_verified -ne $true -or $Result.last_task_result -ne 0 -or $Result.ioctls_sent -ne 0 -or $Result.pipeline_started -ne $false -or $Result.receipt_path -cne "C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-$Slot\worker-denial-acceptance.json" -or $Result.receipt_sha256 -notmatch '^[a-f0-9]{64}$'){throw "Slot $Slot did not return a complete successful bound receipt; batch stopped."}
}
function Invoke-SixSequential {
    param([scriptblock]$Action)
    $results=@()
    foreach($n in 1..6){
        $slot="slot$n"
        $result=& $Action $slot
        Assert-PassedSlotResult $result $slot
        $results+=@($result)
    }
    $results
}
function Invoke-NativeDenialWrapper {
    param([string]$Executable,[string]$Script,[string]$Slot)
    $previous=$ErrorActionPreference
    try{
        # Windows PowerShell 5.1 represents native stderr as ErrorRecord values.
        # Preserve them until the explicit native exit decision; never swallow
        # the child's sanitized receipt summary before that decision.
        $ErrorActionPreference='Continue';$global:LASTEXITCODE=$null
        $output=@(& $Executable -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script -Slot $Slot -Apply 2>&1)
        $exitCode=$global:LASTEXITCODE
    }finally{$ErrorActionPreference=$previous}
    $text=($output|ForEach-Object{[string]$_}) -join [Environment]::NewLine
    if($text.Length -gt 262144){throw 'Bounded wrapper output exceeded; preserve the slot task/root and receipt.'}
    if($null -eq $exitCode -or $exitCode -ne 0){
        $output|Out-Host
        throw "$Slot wrapper exited $exitCode. Batch stopped; preserve all artifacts and do not rerun this batch."
    }
    $text|ConvertFrom-Json
}
foreach($definition in @(Get-PinnedCopyFunctions $helper)){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    foreach($pair in @(@($wrapper,$wrapperHash),@($source,$sourceHash))){
        $stream=Open-VerifiedFile $pair[0] $pair[1] (Get-Item -LiteralPath $pair[0]).Length;$held.Add($stream)
    }
    # Import exact already-held leaf definitions only, never its invocation body.
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($wrapper,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw 'Pinned leaf function parse error.'}
    $names=@('Read-R3Control','Read-R3Text','Assert-OriginalFailedDenialTask','Assert-OriginalFailedDenial','Assert-R3InstalledBindings');$found=@()
    foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
        if($f.Name -in $names){. ([scriptblock]::Create($f.Extent.Text));$found+=@($f.Name)}
    }
    if(@($names|Where-Object{$_ -notin $found}).Count){throw 'Pinned leaf definition missing.'}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    $holds=@(Get-AllFreshTargetHolds $folder)
    $original=Assert-OriginalFailedDenial -RequireTask:$Apply
    $runtime=$null
    if(Test-Path -LiteralPath 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\install-after.json'){$runtime=Assert-R3InstalledBindings}else{$holds+='The reviewed fresh r3 runtime is not installed.'}
    if(-not $Apply){[ordered]@{schema='cochem-six-worker-denials-plan/1';mode='READ_ONLY_PLAN';slots=@('slot1','slot2','slot3','slot4','slot5','slot6');parallel_workers=1;stop_on_first_failure=$true;wrapper_sha256=$wrapperHash;source_sha256=$sourceHash;all_six_targets_checked=$true;original_failure=$original;runtime=$runtime;holds=$holds;worker_processes_executed=0;activation_ready=$false}|ConvertTo-Json -Depth 7;return}
    if($holds.Count){throw ($holds -join ' ')}
    $powershell=Join-Path $PSHOME 'powershell.exe'
    $results=@(Invoke-SixSequential {
        param($slot)
        Write-Host "Checking $slot of six, sequentially. Existing evidence will be preserved."
        $result=Invoke-NativeDenialWrapper $powershell $wrapper $slot
        Assert-PassedSlotResult $result $slot
        Assert-ProtectedPath $result.receipt_path
        $receipt=Read-Inventory $result.receipt_path $result.receipt_sha256
        if($receipt.schema -ne 'cochem-worker-denial-acceptance/1' -or $receipt.slot -cne $slot -or $receipt.status -ne 'HANDLE_DENIALS_VERIFIED' -or $receipt.system_sid -ne 'S-1-5-18' -or $receipt.helper_sha256 -ne $sourceHash -or $receipt.cleanup_verified -ne $true -or
           $receipt.install_receipt_sha256 -cne $runtime.install_receipt_sha256 -or $receipt.source_manifest_sha256 -cne $runtime.source_manifest_sha256 -or
           $receipt.config_sha256 -cne $runtime.configuration_sha256 -or $receipt.resource_limits_sha256 -cne 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f' -or
           $receipt.revision.verified -ne $true -or $receipt.revision.source_sha256 -cne $runtime.revision.source_sha256){throw "$slot protected r3 receipt differs; batch stopped."}
        $result
    })
    [ordered]@{schema='cochem-six-worker-denials-result/1';status='ALL_SIX_HANDLE_DENIALS_VERIFIED';runtime=$runtime;original_failure=$original;slots_verified=$results.Count;parallel_workers=1;receipts=$results;ioctls_sent=0;target_contents_read=0;docker_denial_tested=$false;repair_identity_tested=$false;pipeline_started=$false;activation_ready=$false}|ConvertTo-Json -Depth 8
}finally{foreach($stream in $held){$stream.Dispose()}}
