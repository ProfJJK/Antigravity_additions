#Requires -Version 5.1
<# Reviewed, bounded morning setup. Default plans every phase before changes.
   -Apply performs only private knowledge copy/new-index acceptance followed by
   six sequential handle-only worker checks. Stop at first failure and preserve
   all evidence. Never activate daemons, log in, run models, migrate/reset paid
   budgets, stop legacy services or alter the existing RAM disk/startup task. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$steps=@(
    [pscustomobject]@{name='private_knowledge';path=(Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1');sha256='5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a';plan_schema='cochem-private-knowledge-install-plan/1';result_schema='cochem-private-knowledge-install-result/1';success='PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED'},
    [pscustomobject]@{name='six_worker_denials';path=(Join-Path $PSScriptRoot 'check-all-worker-denials.ps1');sha256='7acbaa1e4a08d1e25cbf384afacc9f80f2a266036af7e51379de80ad1e0010da';plan_schema='cochem-six-worker-denials-plan/1';result_schema='cochem-six-worker-denials-result/1';success='ALL_SIX_HANDLE_DENIALS_VERIFIED'}
)
function Open-HeldStep {
    param($Step)
    if($Step.sha256 -notmatch '^[0-9a-f]{64}$'){throw 'A phase is not frozen/reviewed yet. No setup phase was invoked.'}
    $item=Get-Item -LiteralPath $Step.path -Force
    if($item.PSIsContainer -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint -or $item.Length -gt 1048576){throw 'Invalid bounded setup helper.'}
    $stream=[IO.File]::Open($Step.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -ne $Step.sha256){throw "Reviewed phase changed: $($Step.name)."}
        $stream.Position=0;return ,$stream
    }catch{$stream.Dispose();throw}
}
function Assert-PhasePlan {
    param($Step,$Plan)
    if($Plan.schema -ne $Step.plan_schema -or $Plan.mode -ne 'READ_ONLY_PLAN' -or @($Plan.holds).Count){throw "Preflight held for $($Step.name). Preserve existing artifacts; no setup phase was applied."}
    if($Step.name -eq 'private_knowledge'){
        if($Plan.documents -ne 137 -or $Plan.files_verified -ne 138 -or $Plan.private_files_copied -ne 0 -or $Plan.models_executed -ne 0 -or $Plan.daemon_started -ne $false){throw 'Unexpected private knowledge plan.'}
    }else{
        if(($Plan.slots -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6' -or $Plan.parallel_workers -ne 1 -or $Plan.stop_on_first_failure -ne $true -or $Plan.all_six_targets_checked -ne $true -or $Plan.worker_processes_executed -ne 0){throw 'Unexpected sequential denial plan.'}
    }
}
function Assert-PhaseResult {
    param($Step,$Result)
    if($Result.schema -ne $Step.result_schema -or $Result.status -ne $Step.success){throw "Phase $($Step.name) did not produce its reviewed success result; setup stopped."}
    if($Step.name -eq 'private_knowledge'){
        if($Result.last_task_result -ne 0 -or $Result.configuration_modified -ne $false -or $Result.daemon_started -ne $false -or $Result.receipt_sha256 -notmatch '^[a-f0-9]{64}$' -or $Result.receipt_path -cne 'C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006\knowledge-acceptance.json'){throw 'Knowledge success receipt differs; setup stopped.'}
    }else{
        if($Result.slots_verified -ne 6 -or $Result.parallel_workers -ne 1 -or @($Result.receipts).Count -ne 6 -or $Result.pipeline_started -ne $false -or $Result.activation_ready -ne $false -or ($Result.receipts.slot -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6'){throw 'Six-worker result is incomplete; setup stopped.'}
    }
}
function Invoke-ReviewedPhases {
    param($Steps,[scriptblock]$Runner,[bool]$Execute)
    $plans=@()
    foreach($step in $Steps){$plan=& $Runner $step $false;Assert-PhasePlan $step $plan;$plans+=@([ordered]@{phase=$step.name;report=$plan})}
    $results=@()
    if($Execute){foreach($step in $Steps){$result=& $Runner $step $true;Assert-PhaseResult $step $result;$results+=@([ordered]@{phase=$step.name;report=$result})}}
    [ordered]@{schema='cochem-reviewed-morning-setup/1';mode=$(if($Execute){'REVIEWED_PHASES_COMPLETED'}else{'READ_ONLY_PLAN'});plans=$plans;results=$results;stop_on_first_failure=$true;models_executed=0;logins_executed=0;repair_budgets_modified=$false;pipeline_started=$false;activation_ready=$false;remaining='Human browser login, native integration/effort acceptance, Docker/RAM/Oracle execution acceptance, historical continuity/repair-ledger decisions and final activation remain separate.'}
}
$streams=[Collections.Generic.List[IO.FileStream]]::new()
try{
    # Hold BOTH reviewed helper byte streams before even invoking their plans.
    foreach($step in $steps){$streams.Add((Open-HeldStep $step))}
    $report=Invoke-ReviewedPhases $steps {
        param($step,$execute)
        $arguments=if($execute){@{Apply=$true}}else{@{}}
        if($execute){Write-Host ("Running reviewed phase: {0}" -f $step.name)}
        # Same PS5.1 process preserves success-stream JSON separately from each
        # phase's Write-Host progress. Terminating errors stop before the next.
        $raw=& $step.path @arguments
        ($raw -join [Environment]::NewLine)|ConvertFrom-Json
    } ([bool]$Apply)
    $report|ConvertTo-Json -Depth 9
}finally{foreach($stream in $streams){$stream.Dispose()}}
