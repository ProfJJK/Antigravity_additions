#Requires -Version 5.1
<# One provider, six sequential worker authentication-status checks after human login.
   Default only plans. No native login or model command is invoked by this batch.
   -Apply prechecks ALL six fresh roots/tasks before slot1, then stops on the
   first failed task/receipt. No cleanup, automatic retry or daemon activation.
   Keep every successful, failed and partial per-slot artifact for review. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Provider,[switch]$Apply)
$Provider=$Provider.ToLowerInvariant()
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files'
$wrapper=Join-Path $PSScriptRoot 'check-worker-native-status.ps1'
$source=Join-Path $PSScriptRoot 'worker-native-status.py'
$wrapperHash='fa9568207019c554b2f3246e50f414e94901f0f5a5f6f843771610c21e95e60c'
$sourceHash='8b19d224effd975423b6d9fb02f79fa949d92e73a131d710ac95de55c8580a52'
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
        $slot="slot$n";$name="CoChem-4.2.7-NativeStatus-$slot-$Provider"
        $root="C:\Program Files\CoChem\NativeStatus4.2.7-windows-20261006-$slot-$Provider"
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
    if($Result.schema -ne 'cochem-worker-native-status-task-result/1' -or $Result.slot -cne $Slot -or $Result.provider -cne $Provider -or $Result.status -ne 'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or $Result.cleanup_verified -ne $true -or $Result.last_task_result -ne 0 -or $Result.native_model_jobs_executed -ne 0 -or $Result.pipeline_started -ne $false -or $Result.ready_for_inference -ne $false -or $Result.receipt_path -cne "C:\Program Files\CoChem\NativeStatus4.2.7-windows-20261006-$Slot-$Provider\worker-native-status.json" -or $Result.receipt_sha256 -notmatch '^[a-f0-9]{64}$'){throw "Slot $Slot did not return a complete successful bound receipt; batch stopped."}
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
foreach($definition in @(Get-PinnedCopyFunctions $helper)){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    foreach($pair in @(@($wrapper,$wrapperHash),@($source,$sourceHash))){
        $stream=Open-VerifiedFile $pair[0] $pair[1] (Get-Item -LiteralPath $pair[0]).Length;$held.Add($stream)
    }
    $config='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\pipeline.json'
    Assert-ProtectedPath $config
    $stream=Open-VerifiedFile $config '2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6' (Get-Item -LiteralPath $config).Length;$held.Add($stream)
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    $holds=@(Get-AllFreshTargetHolds $folder)
    if(-not $Apply){[ordered]@{schema='cochem-six-worker-native-status-plan/1';mode='READ_ONLY_PLAN';provider=$Provider;slots=@('slot1','slot2','slot3','slot4','slot5','slot6');parallel_workers=1;stop_on_first_failure=$true;wrapper_sha256=$wrapperHash;source_sha256=$sourceHash;all_six_targets_checked=$true;holds=$holds;worker_processes_executed=0;activation_ready=$false}|ConvertTo-Json -Depth 4;return}
    if($holds.Count){throw ($holds -join ' ')}
    $powershell=Join-Path $PSHOME 'powershell.exe'
    $results=@(Invoke-SixSequential {
        param($slot)
        Write-Host "Checking $slot of six, sequentially. Existing evidence will be preserved."
        $output=& $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $wrapper -Slot $slot -Provider $Provider -Apply 2>&1
        $exitCode=$LASTEXITCODE
        if($exitCode -ne 0){$output|Out-Host;throw "$slot wrapper exited $exitCode. Batch stopped; preserve that slot's task/root and all earlier receipts. Do not rerun this six-slot batch."}
        $result=($output -join [Environment]::NewLine)|ConvertFrom-Json
        Assert-PassedSlotResult $result $slot
        Assert-ProtectedPath $result.receipt_path
        $receipt=Read-Inventory $result.receipt_path $result.receipt_sha256
        if($receipt.schema -ne 'cochem-worker-native-status/1' -or $receipt.slot -cne $slot -or $receipt.provider -cne $Provider -or $receipt.status -ne 'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or $receipt.system_sid -ne 'S-1-5-18' -or $receipt.helper_sha256 -ne $sourceHash -or $receipt.cleanup_verified -ne $true){throw "$slot protected receipt differs; batch stopped."}
        $result
    })
    [ordered]@{schema='cochem-six-worker-native-status-result/1';status='ALL_SIX_NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED';provider=$Provider;slots_verified=$results.Count;parallel_workers=1;receipts=$results;native_model_jobs_executed=0;login_commands_executed=0;provider_account_identity_verified=$false;serving_model_verified=$false;pipeline_started=$false;activation_ready=$false}|ConvertTo-Json -Depth 6
}finally{foreach($stream in $held){$stream.Dispose()}}
