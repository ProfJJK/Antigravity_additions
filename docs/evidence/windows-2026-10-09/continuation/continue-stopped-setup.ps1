#Requires -Version 5.1
<# Three reviewed phases, with every plan checked before the first write.
   Default is read-only. -Apply installs the private corpus/new index, runs six
   sequential handle-only worker checks, then builds a fresh stopped runtime.
   The first two phases use their reviewed unique SYSTEM tasks. The third adds
   no task/account/state/RAM setup. No models, login, activation or budget reset.
   Any failure stops; preserve partial roots/receipts and do not blindly rerun. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Open-ContinuationPin {
    param($Pin)
    if($Pin.sha256 -notmatch '^[0-9a-f]{64}$'){throw 'Continuation dependency is not frozen.'}
    $path=[IO.Path]::GetFullPath($Pin.path)
    if($path -cne $Pin.path -or $path.Substring(2).Contains(':')){throw 'Dependency path is not canonical.'}
    $current=$path
    while($current){
        $entry=Get-Item -LiteralPath $current -Force
        if($entry.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Continuation dependency has a reparse ancestor.'}
        $current=Split-Path -Parent $current
    }
    $item=Get-Item -LiteralPath $path -Force
    if($item.PSIsContainer -or $item.Length -gt 1048576){throw 'Continuation dependency exceeds its file bound.'}
    $stream=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -ne $Pin.sha256){throw ('Reviewed continuation dependency changed: '+$Pin.name)}
        $stream.Position=0;return ,$stream
    }catch{$stream.Dispose();throw}
}

function Assert-ContinuationReport {
    param([string]$Phase,$Report,[bool]$Execute)
    if($Phase -eq 'knowledge_and_six_denials'){
        $mode=if($Execute){'REVIEWED_PHASES_COMPLETED'}else{'READ_ONLY_PLAN'}
        if($Report.schema -ne 'cochem-reviewed-morning-setup/1' -or $Report.mode -ne $mode -or
           $Report.stop_on_first_failure -ne $true -or $Report.models_executed -ne 0 -or
           $Report.logins_executed -ne 0 -or $Report.repair_budgets_modified -ne $false -or
           $Report.pipeline_started -ne $false -or $Report.activation_ready -ne $false -or
           @($Report.plans).Count -ne 2 -or ($Report.plans.phase -join ',') -cne 'private_knowledge,six_worker_denials'){
            throw 'First two reviewed phases returned an unexpected report.'}
        foreach($plan in $Report.plans){if($plan.report.mode -ne 'READ_ONLY_PLAN' -or @($plan.report.holds).Count){throw 'A first-two-phase preflight is held.'}}
        if($Execute){
            if(@($Report.results).Count -ne 2 -or ($Report.results.phase -join ',') -cne 'private_knowledge,six_worker_denials'){throw 'Both first-phase results are required before the code-only update.'}
            $knowledge=$Report.results[0].report;$denials=$Report.results[1].report
            if($knowledge.schema -ne 'cochem-private-knowledge-install-result/1' -or $knowledge.status -ne 'PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED' -or $knowledge.last_task_result -ne 0 -or
               $knowledge.configuration_modified -ne $false -or $knowledge.daemon_started -ne $false -or $knowledge.receipt_sha256 -notmatch '^[a-f0-9]{64}$'){
                throw 'Private knowledge did not return its reviewed successful receipt.'}
            if($denials.schema -ne 'cochem-six-worker-denials-result/1' -or $denials.status -ne 'ALL_SIX_HANDLE_DENIALS_VERIFIED' -or $denials.slots_verified -ne 6 -or
               $denials.parallel_workers -ne 1 -or @($denials.receipts).Count -ne 6 -or ($denials.receipts.slot -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6' -or
               $denials.pipeline_started -ne $false -or $denials.activation_ready -ne $false){throw 'All six sequential worker receipts are required before the code-only update.'}
        }elseif(@($Report.results).Count){throw 'Read-only plan unexpectedly contains applied phases.'}
    }elseif($Phase -eq 'fresh_stopped_runtime'){
        $mode=if($Execute){'FRESH_STOPPED_RUNTIME_READY'}else{'READ_ONLY_PLAN'}
        if($Report.schema -ne 'cochem-stopped-runtime-update/1' -or $Report.mode -ne $mode -or @($Report.holds).Count -or
           $Report.target_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r1' -or $Report.source_files -ne 166 -or
           $Report.source_manifest_sha256 -ne 'b643d5903b886e5fc3841dc0f379de992bde17dee0c202800b3cc7991998e18f' -or
           $Report.configuration_sha256 -ne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
           $Report.only_config_change -cne 'ramdisk.workspace_subdirectory' -or $Report.accounts_provisioned -ne 0 -or $Report.tasks_changed -ne 0 -or
           $Report.credentials_modified -ne $false -or $Report.databases_modified -ne $false -or $Report.ram_modified -ne $false -or
           $Report.pipeline_started -ne $false -or $Report.activation_ready -ne $false){throw 'Fresh runtime report differs from the reviewed scope.'}
        if($Execute -and ($Report.verification.revision.verified -ne $true -or $Report.verification.configuration_parsed -ne $true -or
           $Report.verification.ram_workspace_root -cne 'R:\CoChem427-windows-20261007' -or $Report.verification.no_system_or_model_execution -ne $true)){
            throw 'Fresh runtime revision/config verification is incomplete.'}
    }else{throw 'Unknown continuation phase.'}
}

function Invoke-StoppedContinuation {
    param([scriptblock]$Runner,[bool]$Execute)
    $phases=@('knowledge_and_six_denials','fresh_stopped_runtime');$plans=@();$results=@()
    foreach($phase in $phases){$report=& $Runner $phase $false;Assert-ContinuationReport $phase $report $false;$plans+=@([ordered]@{phase=$phase;report=$report})}
    if($Execute){foreach($phase in $phases){$report=& $Runner $phase $true;Assert-ContinuationReport $phase $report $true;$results+=@([ordered]@{phase=$phase;report=$report})}}
    [ordered]@{schema='cochem-stopped-setup-continuation/1';mode=$(if($Execute){'REVIEWED_STOPPED_SETUP_COMPLETED'}else{'READ_ONLY_PLAN'});reviewed_phases=3;max_execution_slots=4;worker_identities=6;plans=$plans;results=$results;holds=@();stop_on_first_failure=$true;automatic_resume=$false;models_executed=0;logins_executed=0;repair_budgets_modified=$false;ram_modified=$false;pipeline_started=$false;activation_ready=$false;remaining='Select the new stopped runtime only through separately reviewed task/config binding; RAM/Docker/Oracle provisioning, browser login, native integration, historical continuity and activation remain separate.'}
}

function Assert-ContinuationCapacity {
    param($Inventory,$Candidate)
    $slots=@($Candidate.workers.PSObject.Properties.Name|Sort-Object)
    if($Inventory.max_execution_slots -ne 4 -or $Inventory.worker_identities -ne 6 -or
       $Candidate.max_execution_slots -ne 4 -or ($slots -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6'){
        throw 'Reviewed four-slot/six-identity binding differs.'}
}

# Only the owner can opt into applying the already-reviewed constituent steps.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$stage='C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006'
$manifest=Join-Path $stage 'stopped-runtime-r1-source-20261007.json'
$manifestHash='b643d5903b886e5fc3841dc0f379de992bde17dee0c202800b3cc7991998e18f'
$pins=@(
    @('first_two','finish-reviewed-setup.ps1','88fd8daafb4a4d1fdcd8070426958f1628886ec5109989f9bd419130fca27bf1'),
    @('runtime','install-stopped-runtime-r1.ps1','b8ff6b7d2518e881e17ea5d62d22b645a0b29d5277c2b3a4643892e8c1c6e247'),
    @('knowledge','install-private-knowledge-candidate.ps1','5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a'),
    @('knowledge_python','accept-private-knowledge.py','832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca'),
    @('denial_batch','check-all-worker-denials.ps1','7acbaa1e4a08d1e25cbf384afacc9f80f2a266036af7e51379de80ad1e0010da'),
    @('denial_wrapper','check-worker-denials.ps1','1576882fe655d27894aa6f9da25a14ae4ddd08540c865d51cc43b26fa3dd6f9d'),
    @('denial_python','worker-denial-acceptance.py','78c0006a225fa98211f038c380552d9651a86c9198b518603e989747f315bb45'),
    @('candidate_config','pipeline.scoped-ram.candidate.json','135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c')
)|ForEach-Object {[pscustomobject]@{name=$_[0];path=(Join-Path $PSScriptRoot $_[1]);sha256=$_[2]}}
$pins+=@([pscustomobject]@{name='runtime_manifest';path=$manifest;sha256=$manifestHash},
    [pscustomobject]@{name='knowledge_manifest';path=(Join-Path $stage 'knowledge-continuation-candidate-20261007T053341Z\custody\private-install-inventory.json');sha256='edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892'},
    [pscustomobject]@{name='custody_functions';path='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1';sha256='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'})
$streams=[Collections.Generic.List[IO.FileStream]]::new()
try{
    # Hold every constituent helper, candidate and manifest against replacement
    # throughout both preflights and all three phases. Each helper also performs
    # its own live custody and per-payload hash checks before using any bytes.
    foreach($pin in $pins){$streams.Add((Open-ContinuationPin $pin))}
    $inventory=Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json
    $candidate=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'pipeline.scoped-ram.candidate.json') -Raw|ConvertFrom-Json
    Assert-ContinuationCapacity $inventory $candidate
    $report=Invoke-StoppedContinuation {
        param($phase,$execute)
        if($execute){Write-Host ('Running reviewed continuation phase: '+$phase)}
        if($phase -eq 'knowledge_and_six_denials'){
            $arguments=if($execute){@{Apply=$true}}else{@{}}
            $raw=& (Join-Path $PSScriptRoot 'finish-reviewed-setup.ps1') @arguments
        }else{
            $arguments=@{Manifest=$manifest;ManifestSha256=$manifestHash}
            if($execute){$arguments.Apply=$true}
            $raw=& (Join-Path $PSScriptRoot 'install-stopped-runtime-r1.ps1') @arguments
        }
        ($raw -join [Environment]::NewLine)|ConvertFrom-Json
    } ([bool]$Apply)
    $report|ConvertTo-Json -Depth 16
}finally{foreach($stream in $streams){$stream.Dispose()}}
