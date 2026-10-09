#Requires -Version 5.1
<# Read-only by default. Preflight BOTH phases before any mutation; explicitly
   requested Apply completes the diagnosed empty helper root, resumes knowledge using r2, then checks unchanged host worker
   boundaries using the preserved original runtime/config. No automatic retry,
   model/login execution, RAM/Docker provisioning, budget migration or activation. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Assert-RecoveryPlan {
    param([string]$Phase,$Value)
    if($Value.mode -ne 'READ_ONLY_PLAN' -or @($Value.holds).Count){throw 'Both phase plans must pass before applying recovery.'}
    if($Phase -eq 'knowledge_resume'){
        if($Value.schema -ne 'cochem-private-knowledge-resume-plan/1' -or $Value.source_sha256 -ne $script:resumePythonHash -or
           $Value.runtime_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2' -or
           $Value.install_receipt_sha256 -ne 'd92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4' -or
           $Value.source_manifest_sha256 -ne 'df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8' -or
           $Value.source_files -ne 166 -or $Value.corpus_files_reverified -ne 138 -or $Value.corpus_acl_changes -ne $false -or
           $Value.existing_root_reused_only_if_empty -ne $true -or $Value.existing_root_private_empty_check_deferred -ne $true -or
           $Value.expected_empty_root_creation_and_write_utc -cne '2026-10-07T14:08:15.5776970Z' -or
           $Value.old_evidence_preserved -ne $true -or $Value.index_resume_executed -ne $false -or $Value.activation_ready -ne $false){throw 'Knowledge resume plan differs from reviewed r2 recovery.'}
    }elseif($Phase -eq 'six_host_boundary_checks'){
        if($Value.schema -ne 'cochem-six-worker-denials-plan/1' -or ($Value.slots -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6' -or
           $Value.parallel_workers -ne 1 -or $Value.stop_on_first_failure -ne $true -or $Value.all_six_targets_checked -ne $true -or
           $Value.worker_processes_executed -ne 0 -or $Value.wrapper_sha256 -ne '1576882fe655d27894aa6f9da25a14ae4ddd08540c865d51cc43b26fa3dd6f9d' -or
           $Value.source_sha256 -ne '78c0006a225fa98211f038c380552d9651a86c9198b518603e989747f315bb45'){throw 'Worker plan differs from the frozen original host-boundary package.'}
    }else{throw 'Unknown recovery phase.'}
}

function Assert-RecoveryResult {
    param([string]$Phase,$Value)
    if($Phase -eq 'knowledge_resume'){
        if($Value.schema -ne 'cochem-private-knowledge-resume-result/1' -or $Value.status -ne 'PRESERVED_CORPUS_AND_RESUMED_INDEX_VERIFIED' -or
           $Value.last_task_result -ne 0 -or $Value.activation_ready -ne $false -or $Value.receipt_sha256 -notmatch '^[a-f0-9]{64}$' -or
           $Value.receipt_path -cne 'C:\Program Files\CoChem\KnowledgeResume4.2.7-windows-20261007-r2\resume-acceptance.json'){
            throw 'Knowledge recovery did not produce its reviewed successful receipt; no worker phase will run.'}
        $a=$Value.acceptance
        if($a.schema -ne 'cochem-private-knowledge-resume/1' -or $a.status -ne $Value.status -or $a.system_sid -ne 'S-1-5-18' -or
           $a.nonce -notmatch '^[a-f0-9]{32}$' -or $a.helper_sha256 -ne $script:resumePythonHash -or
           $a.runtime_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2' -or
           $a.pipeline_config_sha256 -ne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
           $a.source_files_verified -ne 166 -or $a.original_root_and_lock_preserved -ne $true -or
           $a.documents -ne 137 -or $a.corpus_files -ne 138 -or $a.index_integrity_check -ne 'ok' -or
           $a.source_bytes_preserved -ne $true -or $a.existing_acl_modified -ne $false -or $a.corpus_reprovisioned -ne $false -or
           $a.old_tasks_modified_or_run -ne $false -or $a.budgets_modified -ne $false -or $a.daemon_started -ne $false -or $a.activation_ready -ne $false){
            throw 'Knowledge receipt lacks full preserved-state acceptance; no worker phase will run.'}
    }elseif($Phase -eq 'six_host_boundary_checks'){
        if($Value.schema -ne 'cochem-six-worker-denials-result/1' -or $Value.status -ne 'ALL_SIX_HANDLE_DENIALS_VERIFIED' -or
           $Value.slots_verified -ne 6 -or $Value.parallel_workers -ne 1 -or @($Value.receipts).Count -ne 6 -or
           ($Value.receipts.slot -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6' -or
           $Value.ioctls_sent -ne 0 -or $Value.target_contents_read -ne 0 -or $Value.pipeline_started -ne $false -or $Value.activation_ready -ne $false){throw 'Six host-boundary checks did not return complete success.'}
    }else{throw 'Unknown recovery phase.'}
}

function Invoke-RecoverySequence {
    param([scriptblock]$Runner,[bool]$Execute)
    $phases=@('knowledge_resume','six_host_boundary_checks');$plans=@();$results=@()
    foreach($phase in $phases){$value=& $Runner $phase $false;Assert-RecoveryPlan $phase $value;$plans+=@([ordered]@{phase=$phase;report=$value})}
    if($Execute){foreach($phase in $phases){$value=& $Runner $phase $true;Assert-RecoveryResult $phase $value;$results+=@([ordered]@{phase=$phase;report=$value})}}
    [ordered]@{schema='cochem-knowledge-recovery-and-worker-boundaries/1';mode=$(if($Execute){'KNOWLEDGE_RECOVERED_AND_HOST_BOUNDARIES_VERIFIED'}else{'READ_ONLY_PLAN'});plans=$plans;results=$results;holds=@();max_execution_slots=4;worker_identities=6;knowledge_runtime='r2';worker_boundary_runtime='preserved original 20261006 with unchanged launch/boundary bindings';new_ram_or_docker_acceptance=$false;stop_on_first_failure=$true;automatic_retry=$false;models_executed=0;logins_executed=0;repair_budgets_modified=$false;pipeline_started=$false;activation_ready=$false}
}

function Invoke-RecoveryChild {
    param([string]$Path,[bool]$Execute)
    $arguments=if($Execute){@{Apply=$true}}else{@{}}
    $lines=[Collections.Generic.List[string]]::new();$length=0
    try{
        & $Path @arguments | ForEach-Object {
            $line=[string]$_;$length+=$line.Length
            if($length -gt 262144){throw 'Child metadata exceeds its output bound.'}
            $lines.Add($line)
        }
        return (($lines -join [Environment]::NewLine)|ConvertFrom-Json)
    }catch{
        # A reviewed child emits sanitized failure JSON before throwing. Keep it
        # visible to the owner without echoing arbitrary captured text/contents.
        try{
            $value=($lines -join [Environment]::NewLine)|ConvertFrom-Json
            $f=$value.failure
            if($value.schema -eq 'cochem-private-knowledge-resume-result/1' -and $value.status -eq 'KNOWLEDGE_RESUME_HELD' -and
               $value.last_task_result -eq 2 -and $value.receipt_sha256 -match '^[a-f0-9]{64}$' -and
               $value.receipt_path -ceq 'C:\Program Files\CoChem\KnowledgeResume4.2.7-windows-20261007-r2\resume-acceptance.json' -and
               $f.phase -in @('runtime_binding','preserved_evidence','configuration','corpus_verification','blank_state','index_resume','final_verification') -and
               [string]$f.error_type -cmatch '^[A-Za-z0-9_]{1,80}$' -and
               ($null -eq $f.winerror -or (($f.winerror -is [int] -or $f.winerror -is [long]) -and $f.winerror -ge 0 -and $f.winerror -le 4294967295))){
                Write-Host (([ordered]@{status='KNOWLEDGE_RESUME_HELD';receipt_path=$value.receipt_path;receipt_sha256=$value.receipt_sha256;failure=[ordered]@{phase=$f.phase;error_type=$f.error_type;winerror=$f.winerror}})|ConvertTo-Json -Depth 4)
            }
        }catch{}
        throw
    }
}

foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if($Apply -and ($identity.Name -cne 'AETHERDESK\ansac' -or -not ([Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)))){throw 'Apply requires the owner in Administrator Windows PowerShell.'}
$resumeWrapperHash='71b4e2474a6802c36c8c9cebb2330449bd5e776fa2de3b5d293f333c1528404f'
$resumePythonHash='780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b'
$pinSource=Join-Path $PSScriptRoot 'continue-stopped-setup.ps1'
$definitionStream=[IO.File]::Open($pinSource,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($definitionStream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if($hash -ne '0f536acb274b194b8bd28e36d44c8c4e379a12103a184372433800a55f2e287b'){throw 'Reviewed held-pin function source changed.'}
    $definitionStream.Position=0;$reader=[IO.StreamReader]::new($definitionStream,[Text.Encoding]::UTF8,$true,4096,$true)
    try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw 'Reviewed pin function parse error.'}
    $function=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Open-ContinuationPin'},$true)
    if($null -eq $function){throw 'Reviewed pin function absent.'};. ([scriptblock]::Create($function.Extent.Text))
    $pins=@(
        @('resume-private-knowledge-copyfix.ps1',$resumeWrapperHash),@('resume-private-knowledge.py',$resumePythonHash),
        @('check-all-worker-denials.ps1','7acbaa1e4a08d1e25cbf384afacc9f80f2a266036af7e51379de80ad1e0010da'),
        @('check-worker-denials.ps1','1576882fe655d27894aa6f9da25a14ae4ddd08540c865d51cc43b26fa3dd6f9d'),
        @('worker-denial-acceptance.py','78c0006a225fa98211f038c380552d9651a86c9198b518603e989747f315bb45'),
        @('install-private-knowledge-candidate.ps1','5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a'),
        @('diagnose-knowledge-state.ps1','65887612ce9b1c6f563437d72d3a5a44f3dd01e47a2ee029416198ca27f02370'),
        @('install-stopped-runtime-r2.ps1','1cf9d391bf54b1a4b530636ebbb3746cadc3844b0fe13a60eabb13deb9987896'))
    foreach($pair in $pins){$held.Add((Open-ContinuationPin ([pscustomobject]@{name=$pair[0];path=(Join-Path $PSScriptRoot $pair[0]);sha256=$pair[1]})))}
    $held.Add((Open-ContinuationPin ([pscustomobject]@{name='custody_functions';path='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1';sha256='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'})))
    $report=Invoke-RecoverySequence {
        param($phase,$execute)
        $name=if($phase -eq 'knowledge_resume'){'resume-private-knowledge-copyfix.ps1'}else{'check-all-worker-denials.ps1'}
        if($execute){Write-Host ('Running reviewed phase: '+$phase)}
        Invoke-RecoveryChild (Join-Path $PSScriptRoot $name) $execute
    } ([bool]$Apply)
    $report|ConvertTo-Json -Depth 18
}finally{foreach($stream in $held){$stream.Dispose()};$definitionStream.Dispose()}
