#Requires -Version 5.1
<# Default collects read-only previews. -Apply runs two reviewed continuation
steps, once and in order: fresh scoped foundation, private project.
No retry, resume, rollback, login, model work, supervisor or daemon activation.
All leaf scripts retain their own strict preconditions and private receipts. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Open-SeriesPin {
    param([string]$Path,[string]$Hash)
    if($Hash -cnotmatch '^[a-f0-9]{64}$'){throw 'Unfrozen series helper.'}
    $full=[IO.Path]::GetFullPath($Path)
    if($full -cne $Path -or $full.Substring(2).Contains(':')){throw 'Noncanonical series path.'}
    $ancestor=$full
    while($ancestor){
        $item=Get-Item -LiteralPath $ancestor -Force
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Series path contains a reparse point.'}
        $ancestor=Split-Path -Parent $ancestor
    }
    $item=Get-Item -LiteralPath $full -Force
    if($item.PSIsContainer -or $item.Length -gt 1048576){throw 'Series helper exceeds its bound.'}
    $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed series helper changed.'}
        $stream.Position=0;return ,$stream
    }catch{$stream.Dispose();throw}
}

function Assert-SeriesSuccess {
    param($Step,$Report)
    if($Report.schema -cne $Step.result_schema -or $Report.status -cne $Step.success -or
       $Report.receipt_path -cne $Step.receipt -or $Report.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
       $Report.activation_ready -isnot [bool] -or $Report.activation_ready){throw 'Series phase did not return its exact successful receipt binding.'}
    $installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
    if($Step.name -ceq 'foundation'){
        if($Report.runtime_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3' -or
           $Report.install_receipt_sha256 -cne $installHash -or $Report.last_task_result -ne 0){throw 'Series task/runtime binding differs.'}
        if($Report.ram_scoped_roots_verified -ne 6 -or $Report.empty_registry_capacity -ne 4 -or
           $Report.automatic_retry_allowed -isnot [bool] -or $Report.automatic_retry_allowed -or
           $Report.partial_outputs_preserved -isnot [bool] -or -not $Report.partial_outputs_preserved -or
           $Report.ram_provision_started -ne $true -or $Report.registry_provision_started -ne $true -or
           $Report.diagnostic_receipt_sha256 -cne 'cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9' -or
           $Report.failed_foundation_receipt_sha256 -cne 'bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'){
            throw 'Continuation foundation scope/prior evidence differs.'}
    }elseif($Step.name -eq 'project'){
        if($Report.r3_install_receipt_sha256 -cne $installHash -or
           $Report.config_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
           $Report.target -cne 'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance' -or
           $Report.baseline_commit -cne 'c52a3a97eb085e6bafbbd14bd6a75f3274288530' -or
           $Report.baseline_tree -cne '0e7fe3edd935e4a839d17cb99b30b08db18d8ae9' -or
           $Report.objects -ne 7 -or $Report.files -ne 3){throw 'Project baseline differs.'}
    }else{throw 'Unknown series phase.'}
}

function Show-SeriesLeafFailure {
    param($Step,[string]$Text)
    if($Step.name -cne 'project' -or $Text.Length -gt 131072){return}
    try{
        $value=$Text|ConvertFrom-Json
        if($value.schema -cne 'cochem-private-project-import-result/1' -or $value.status -cne 'HELD_PRESERVE_PARTIAL'){return}
        $safe=[ordered]@{schema=$value.schema;status=$value.status}
        if($null -ne $value.PSObject.Properties['receipt_path']){
            if($value.receipt_path -cne $Step.receipt -or $value.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
               $value.error_type -cnotin @('ImportHeld','OSError','PermissionError','RuntimeError','ValueError','FileNotFoundError','UnexpectedException')){return}
            $codes=@('receipt_or_child_outcome','bundle_pin','bundle_header','bundle_pack_shape','bundle_pack_checksum','git_deadline','git_timeout_preserve_partial','git_output_bound','fresh_target_identity','fresh_target_not_empty','template_not_empty','not_bare','object_format','repository_path','reference_identity','baseline_identity','non_blob_tree','tree_files','blob_bytes','object_count','object_types','repository_entry_custody','unexpected_repository_control','unexpected_local_config','target_identity_changed','unexpected_exception')
            if($value.code -cnotin $codes -and $value.code -cnotmatch '^git_nonzero_-?[0-9]{1,11}$'){return}
            $safe.receipt_path=$value.receipt_path;$safe.receipt_sha256=$value.receipt_sha256
            $safe.error_type=$value.error_type;$safe.code=$value.code
        }else{
            if($value.phase -cne 'helper_bootstrap' -or $value.raw_child_output_withheld -ne $true -or
               ($null -ne $value.exit_code -and (($value.exit_code -isnot [int] -and $value.exit_code -isnot [long]) -or $value.exit_code -lt -2147483648 -or $value.exit_code -gt 4294967295))){return}
            $safe.phase='helper_bootstrap';$safe.exit_code=$value.exit_code;$safe.raw_child_output_withheld=$true
        }
        Write-Host ($safe|ConvertTo-Json -Depth 4 -Compress)
    }catch{return}
}

function Invoke-SeriesLeaf {
    param($Step,[bool]$Execute)
    if($Execute){Write-Host ('Running reviewed continuation phase: '+$Step.name)}
    $arguments=if($Execute){@{Apply=$true}}else{@{}}
    $chunks=[Collections.Generic.List[string]]::new();$characters=0
    try{
        # Incremental capture retains sanitized success-stream failure JSON
        # emitted by the frozen project importer immediately before it throws.
        & $Step.path @arguments | ForEach-Object {
            $line=[string]$_;$characters+=$line.Length+2
            if($characters -gt 131072 -or $chunks.Count -ge 4096){throw 'Phase output exceeded its reviewed bound.'}
            $chunks.Add($line)
        }
        ($chunks -join [Environment]::NewLine)|ConvertFrom-Json
    }catch{
        Show-SeriesLeafFailure $Step ($chunks -join [Environment]::NewLine)
        throw
    }
}

function Invoke-ReturnSeries {
    param($Steps,[scriptblock]$Runner,[bool]$Execute)
    $results=@();$phase='not_started'
    try{
        foreach($step in $Steps){
            $phase=$step.name
            $value=& $Runner $step $Execute
            if($Execute){Assert-SeriesSuccess $step $value}
            elseif($value.schema -cne $step.plan_schema -or $value.mode -cne 'READ_ONLY_PLAN'){throw 'Unexpected read-only phase preview.'}
            $results+=@([ordered]@{phase=$phase;report=$value})
        }
    }catch{
        # Leaf scripts display their bounded failure summaries and preserve their
        # private receipts. Never continue, reset or silently skip a failed phase.
        Write-Host ([ordered]@{schema='cochem-return-continuation-series/2';status='SERIES_HELD';phase=$phase;
            completed_phases=@($results|ForEach-Object{$_.phase});automatic_retry_allowed=$false;activation_ready=$false;
            next='Preserve all artifacts and return the printed failure. Do not rerun this series.'}|ConvertTo-Json -Depth 4)
        throw 'Continuation stopped. Preserve the phase evidence and do not rerun.'
    }
    [ordered]@{schema='cochem-return-continuation-series/2';status=$(if($Execute){'TWO_CONTINUATION_PHASES_VERIFIED'}else{'READ_ONLY_PLAN'});
        results=$results;worker_identities=6;max_shared_slots=4;automatic_retry_allowed=$false;
        provider_calls_executed=0;model_jobs_executed=0;pipeline_started=$false;activation_ready=$false;
        remaining='Attended native sign-ins, Docker physical execution, native routed workflow, history/budget reconciliation and sustained acceptance remain separate.'}
}

foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell. No phase was invoked.'}
$steps=@(
    [pscustomobject]@{name='foundation';path=(Join-Path $PSScriptRoot 'provision-execution-foundation-r3-v2.ps1');sha256='0df813342fa8070d1f2f6041ccbc24a7d8ec0d5c94baa269eec02c243e043ca0';plan_schema='cochem-execution-foundation-plan/2';result_schema='cochem-execution-foundation-task-result/2';success='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED';receipt='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2\execution-foundation.json'},
    [pscustomobject]@{name='project';path=(Join-Path $PSScriptRoot 'install-disposable-project.ps1');sha256='66bd5b039411b726b246fe4c4a57f8514a8fdbd40978283d2f74c1d922a2ec0d';plan_schema='cochem-private-project-import-plan/1';result_schema='cochem-private-project-import-result/1';success='PRIVATE_BARE_PROJECT_VERIFIED';receipt='C:\Program Files\CoChem\ProjectImport4.2.7-windows-20261007-r3\project-import.json'}
)
$seriesStreams=[Collections.Generic.List[IO.FileStream]]::new()
try{
    # Hold both wrappers before any plan or mutation. Each frozen leaf
    # verifies and holds its own transitive dependencies and prior evidence.
    foreach($step in $steps){$seriesStreams.Add((Open-SeriesPin $step.path $step.sha256))}
    $report=Invoke-ReturnSeries $steps {
        param($step,$execute)
        # Call in a child script scope. This preserves safe leaf Write-Host
        # failure metadata without native stderr conversion or raw forwarding.
        Invoke-SeriesLeaf $step $execute
    } ([bool]$Apply)
    $report|ConvertTo-Json -Depth 10
}finally{foreach($stream in $seriesStreams){$stream.Dispose()}}
