#Requires -Version 5.1
<# Inspect an already published index; never run KnowledgeService or refresh.
   Default only plans. Owner -Apply creates one fresh private helper/task/receipt,
   after binding every input and preserved task. Existing state is read-only. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-PublishedDefinitions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed function source changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed function parse failure.'};$found=@()
        foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
            if($f.Name -in $Names){$found+=@($f.Name);$f.Extent.Text}}
        if(@($Names|Where-Object{$_ -notin $found}).Count){throw 'Reviewed function missing.'}
    }finally{$stream.Dispose()}
}

function Assert-FailedResumeTask {
    param($Task,$Receipt)
    if($Receipt.schema -cne 'cochem-private-knowledge-resume/1' -or $Receipt.status -cne 'KNOWLEDGE_RESUME_HELD' -or
       $Receipt.system_sid -cne 'S-1-5-18' -or $Receipt.helper_sha256 -cne '780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b' -or
       $Receipt.runtime_root -cne $installRoot -or $Receipt.install_receipt_sha256 -cne $receiptHash -or
       $Receipt.pipeline_config_sha256 -cne $configHash -or $Receipt.source_manifest_sha256 -cne $manifestHash -or
       $Receipt.original_receipt_sha256 -cne $originalHash -or $Receipt.diagnostic_receipt_sha256 -cne $diagnosticHash -or
       $Receipt.nonce -cnotmatch '^[a-f0-9]{32}$' -or $Receipt.failure.phase -cne 'final_verification' -or
       $Receipt.failure.error_type -cne 'ValueError' -or $null -ne $Receipt.failure.winerror -or
       $Receipt.index_resume_started -ne $true -or $Receipt.documents -ne 137 -or $Receipt.sections -ne 1708 -or
       $Receipt.index_integrity_check -cne 'ok' -or $Receipt.index_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
       $Receipt.generation -cnotmatch '^g-[a-f0-9]{32}$'){throw 'Pinned failed resume is not the reviewed post-publication failure.'}
    if($null -eq $Task){throw 'Preserved resume task missing.'};$d=$Task.Definition
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 2 -or
       $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
       $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'Preserved resume task is not terminal with exact exit 2.'}
    $action=$d.Actions.Item(1)
    $expected='-I -B "'+(Join-Path $resumeRoot 'resume-private-knowledge.py')+'" '+$Receipt.nonce
    if($action.Type -ne 0 -or $action.Path -cne $python -or $action.Arguments -cne $expected -or $action.WorkingDirectory -cne $resumeRoot){throw 'Preserved resume action differs from its pinned receipt nonce.'}
}

function Get-PublishedCopyRecords {
    [pscustomobject]@{Name='verify-published-knowledge.py';Source=$source;Sha256=$sourceHash;Length=[long]0;LocalSource=$true}
    [pscustomobject]@{Name='resume-private-knowledge.py';Source=(Join-Path $resumeRoot 'resume-private-knowledge.py');Sha256='780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b';Length=[long]0;LocalSource=$false}
    [pscustomobject]@{Name='diagnose-knowledge-state.py';Source=(Join-Path $diagnosticRoot 'diagnose-knowledge-state.py');Sha256='e799d3d932a42af251c5ea752530eb9bcee022196d0c22db60c75e481e67da87';Length=[long]0;LocalSource=$false}
    [pscustomobject]@{Name='accept-private-knowledge.py';Source=(Join-Path $originalRoot 'accept-private-knowledge.py');Sha256='832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca';Length=[long]0;LocalSource=$false}
}
function Confirm-PublishedCopyRecords {
    param([object[]]$Records)
    $expected=@(Get-PublishedCopyRecords)
    if($Records.Count -ne 4){throw 'Exactly four typed helper records required.'}
    for($i=0;$i -lt 4;$i++){
        $r=$Records[$i];$e=$expected[$i]
        if($r -isnot [pscustomobject] -or $r.Name -cne $e.Name -or $r.Source -cne $e.Source -or
           $r.Sha256 -cne $e.Sha256 -or $r.LocalSource -isnot [bool] -or $r.LocalSource -ne $e.LocalSource){throw 'Typed helper binding differs.'}
    }
    foreach($r in $Records){$stream=Hold-PinnedFile $r.Source $r.Sha256 -LocalSource:$r.LocalSource;$r.Length=[long]$stream.Length;if($r.Length -le 0 -or $r.Length -gt 1048576){throw 'Helper exceeds length bound.'}}
}
function Copy-PublishedRecords {
    param([object[]]$Records)
    foreach($r in $Records){$destination=Join-Path $root $r.Name;Copy-PrivateFile $r.Source $destination $r.Sha256 $r.Length;$null=Hold-PinnedFile $destination $r.Sha256}
}

function Assert-PublishedReceiptBinding {
    param($Receipt,[string]$Nonce)
    if($Receipt.schema -cne 'cochem-published-knowledge-verification/1' -or $Receipt.nonce -cne $Nonce -or
       $Receipt.system_sid -cne 'S-1-5-18' -or $Receipt.helper_sha256 -cne $sourceHash -or
       $Receipt.failed_resume_receipt_sha256 -cne $failedResumeHash){throw 'Published-index receipt is not nonce/source/SYSTEM/failed-receipt bound.'}
    foreach($name in @('knowledge_service_constructed','index_refreshed_or_repaired','existing_files_or_acls_modified','existing_tasks_modified_or_run','activation_ready')){
        if($Receipt.$name -isnot [bool] -or $Receipt.$name){throw 'Read-only verification scope differs.'}}
    if($Receipt.native_model_jobs_executed -ne 0){throw 'Unexpected native model execution.'}
}
function Assert-PublishedSuccess {
    param($Receipt,$Failed)
    if($Receipt.status -cne 'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED' -or $Receipt.substep -cne 'complete' -or
       $Receipt.runtime_root -cne $installRoot -or $Receipt.install_receipt_sha256 -cne $receiptHash -or
       $Receipt.pipeline_config_sha256 -cne $configHash -or $Receipt.source_manifest_sha256 -cne $manifestHash -or
       $Receipt.source_files_verified -ne 166 -or $Receipt.revision.verified -ne $true -or
       $Receipt.failed_resume_nonce -cne $Failed.nonce -or $Receipt.generation -cne $Failed.generation -or
       $Receipt.index_sha256 -cne $Failed.index_sha256 -or $Receipt.documents -ne 137 -or $Receipt.sections -ne 1708 -or
       $Receipt.corpus_files -ne 138 -or $Receipt.integrity_check -cne 'ok' -or $Receipt.sqlite_mode -cne 'ro' -or
       $Receipt.sqlite_temp_store -cne 'memory'){throw 'Published index success commitments differ.'}
    foreach($name in @('original_root_identity_and_security_preserved','original_writer_lock_preserved','corpus_bytes_preserved','canonical_authority_matches_capture','index_size_sla_met','sqlite_immutable','sqlite_query_only')){
        if($Receipt.$name -isnot [bool] -or -not $Receipt.$name){throw 'Published index preservation or read-only proof missing.'}}
}
function Get-PublishedMetadataDifferences {
    param($Receipt)
    $differences=[ordered]@{}
    if($null -ne $Receipt.PSObject.Properties['root_difference_from_blank_diagnostic']){
        foreach($property in $Receipt.root_difference_from_blank_diagnostic.PSObject.Properties){
            if($property.Name -notin @('bytes','written_filetime','metadata_sha256')){throw 'Unexpected stable root metadata difference.'}
            $before=$property.Value.before;$after=$property.Value.after
            if($property.Name -eq 'metadata_sha256'){
                if($before -cnotmatch '^[a-f0-9]{64}$' -or $after -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid metadata digest.'}
            }elseif($before -isnot [long] -and $before -isnot [int] -or $after -isnot [long] -and $after -isnot [int] -or $before -lt 0 -or $after -lt 0){throw 'Invalid metadata integer.'}
            $differences[$property.Name]=[ordered]@{before=$before;after=$after}
        }
    }
    $differences
}
function Write-PublishedFailure {
    param($Receipt,[string]$Path,[string]$Hash,[long]$TaskResult)
    if($Receipt.status -cne 'PUBLISHED_KNOWLEDGE_VERIFICATION_HELD' -or $Receipt.failure.substep -cnotmatch '^[a-z][a-z0-9_.]{0,95}$' -or
       $Receipt.failure.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$' -or
       ($null -ne $Receipt.failure.winerror -and $Receipt.failure.winerror -isnot [int] -and $Receipt.failure.winerror -isnot [long])){throw 'Invalid sanitized verifier failure metadata; preserve private receipt.'}
    $summary=[ordered]@{schema='cochem-published-knowledge-verification-result/1';status=$Receipt.status;receipt_path=$Path;receipt_sha256=$Hash;last_task_result=$TaskResult;failed_substep=$Receipt.failure.substep;error_type=$Receipt.failure.error_type;winerror=$Receipt.failure.winerror;root_metadata_differences=(Get-PublishedMetadataDifferences $Receipt);existing_state_read_only=$true;activation_ready=$false}
    # Host output remains visible if an outer workflow collects stdout and the
    # following terminating error interrupts that collection.
    Write-Host ($summary|ConvertTo-Json -Depth 6)
}

# Invocation boundary. Never run this script's body while extracting test functions.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$installRoot=Join-Path $programFiles 'CoChem\Pipeline4.2.7-windows-20261007-r2'
$oldInstall=Join-Path $programFiles 'CoChem\Pipeline4.2.7-windows-20261006'
$basePythonRoot=Join-Path $programFiles 'CoChem\Toolchain4.2.7-windows-20261006\Python312'
$python=Join-Path $installRoot '.venv\Scripts\python.exe';$basePython=Join-Path $basePythonRoot 'python.exe'
$originalRoot=Join-Path $programFiles 'CoChem\KnowledgeAcceptance4.2.7-windows-20261006'
$diagnosticRoot=Join-Path $programFiles 'CoChem\KnowledgeDiagnostic4.2.7-windows-20261007-a'
$resumeRoot=Join-Path $programFiles 'CoChem\KnowledgeResume4.2.7-windows-20261007-r2'
$root=Join-Path $programFiles 'CoChem\KnowledgePublishedVerification4.2.7-windows-20261007-r2'
$taskName='CoChem-4.2.7-KnowledgePublishedVerification-20261007-r2'
$source=Join-Path $PSScriptRoot 'verify-published-knowledge.py';$sourceHash='9f3b6c00255d6e5c9bc0a6e6bec546ae5a60a8287c2b24a65a2f675f6d3e0496'
$receiptHash='d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4'
$manifestHash='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$originalHash='2cf8f1f4cafbb0537900af98349f6a3f4faec135a4061dd153f0cce78c1488dd'
$diagnosticHash='18ceba4394b92bb6e1d320f49226359902af0084a72195a8139f699594bc0307'
$failedResumeHash='8cdaebaaf9338046ef928368160f9ea7eff792c22d0f46a3a23820a78c94e66b'
if($sourceHash -notmatch '^[a-f0-9]{64}$'){throw 'Awaiting reviewed verifier source freeze.'}
foreach($text in @(Import-PublishedDefinitions (Join-Path $PSScriptRoot 'resume-private-knowledge.ps1') 'fdfbd0e313acdb75047d5ef51768654c58c9193991607e04d08c7dc35590bdfa' @('Assert-PreservedTask','Assert-R2VenvBinding','Hold-PinnedFile','Read-HeldText'))){. ([scriptblock]::Create($text))}
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
foreach($text in @(Import-PublishedDefinitions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($text))}
foreach($text in @(Import-PublishedDefinitions (Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1') '5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a' @('New-PrivateAcl','Assert-PrivateItem','New-PrivateDirectory','Copy-PrivateFile','Assert-CodeTreeOnce','Get-ExactTaskOrAbsent','Assert-StoppedDaemons','Wait-KnowledgeTask','Assert-CompletedKnowledgeTask'))){. ([scriptblock]::Create($text))}
foreach($text in @(Import-PublishedDefinitions (Join-Path $PSScriptRoot 'diagnose-knowledge-state.ps1') '65887612ce9b1c6f563437d72d3a5a44f3dd01e47a2ee029416198ca27f02370' @('Assert-EffectivePrivateReceipt'))){. ([scriptblock]::Create($text))}
$script:repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions';$script:targetRoot=$installRoot;$script:sourceRoot=Join-Path $installRoot 'source'
foreach($text in @(Import-PublishedDefinitions (Join-Path $PSScriptRoot 'install-stopped-runtime-r2.ps1') '1cf9d391bf54b1a4b530636ebbb3746cadc3844b0fe13a60eabb13deb9987896' @('Get-RuntimeFiles'))){. ([scriptblock]::Create($text))}
Initialize-FileIdentity;$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $null=Hold-PinnedFile $source $sourceHash -LocalSource
    $null=Hold-PinnedFile $python '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'
    $null=Hold-PinnedFile $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
    $stream=Hold-PinnedFile (Join-Path $installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d' 8192;Assert-R2VenvBinding (Read-HeldText $stream)
    $null=Hold-PinnedFile (Join-Path $installRoot 'pipeline.json') $configHash
    $stream=Hold-PinnedFile (Join-Path $installRoot 'install-after.json') $receiptHash;$installed=(Read-HeldText $stream)|ConvertFrom-Json
    if($installed.mode -cne 'FRESH_STOPPED_RUNTIME_READY' -or $installed.source_files -ne 166 -or $installed.source_manifest_sha256 -cne $manifestHash -or $installed.configuration_sha256 -cne $configHash -or $installed.verification.revision.verified -ne $true){throw 'Frozen r2 installation receipt differs.'}
    $stream=Hold-PinnedFile (Join-Path $installRoot 'source-manifest.json') $manifestHash;$manifest=(Read-HeldText $stream)|ConvertFrom-Json
    $files=@(Get-RuntimeFiles $manifest);if($files.Count -ne 166){throw 'Exact r2 source inventory count differs.'}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');$holds=@()
    if(Test-Path -LiteralPath $root){$holds+='Fresh verification root exists; preserve it.'}
    if($null -ne (Get-ExactTaskOrAbsent $taskName)){$holds+='Fresh verification task exists; preserve it.'}
    Assert-StoppedDaemons
    if(-not $Apply){[ordered]@{schema='cochem-published-knowledge-verification-plan/1';mode='READ_ONLY_PLAN';source_sha256=$sourceHash;runtime_root=$installRoot;install_receipt_sha256=$receiptHash;source_manifest_sha256=$manifestHash;source_files=166;target_root=$root;task_name=$taskName;failed_resume_receipt_sha256=$failedResumeHash;existing_state_read_only=$true;knowledge_service_or_refresh_invoked=$false;private_receipt_and_terminal_task_checks_deferred=$true;verification_executed=$false;activation_ready=$false;holds=$holds}|ConvertTo-Json -Depth 4;return}
    if($holds.Count){throw ($holds -join ' ')}
    $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce $basePythonRoot
    foreach($file in $files){$null=Hold-PinnedFile $file.destination $file.sha256 16777216}
    Assert-PreservedTask (Get-ExactTaskOrAbsent 'CoChem-4.2.7-PrivateKnowledge-Acceptance') $originalRoot 'accept-private-knowledge.py' 'd4606fa578ae401b81ace37cd8e2909b edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892' 2
    Assert-PreservedTask (Get-ExactTaskOrAbsent 'CoChem-4.2.7-KnowledgeDiagnostic-20261007-a') $diagnosticRoot 'diagnose-knowledge-state.py' ('e9f31a03b0d04b8697967407c47e3518 '+$originalHash) 0
    foreach($pin in @([pscustomobject]@{Path=(Join-Path $originalRoot 'knowledge-acceptance.json');Hash=$originalHash},[pscustomobject]@{Path=(Join-Path $diagnosticRoot 'diagnostic.json');Hash=$diagnosticHash},[pscustomobject]@{Path=(Join-Path $originalRoot 'private-install-inventory.json');Hash='edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892'})){$null=Hold-PinnedFile $pin.Path $pin.Hash}
    $stream=Hold-PinnedFile (Join-Path $resumeRoot 'resume-acceptance.json') $failedResumeHash 131072;$failed=(Read-HeldText $stream)|ConvertFrom-Json
    Assert-FailedResumeTask (Get-ExactTaskOrAbsent 'CoChem-4.2.7-KnowledgeResume-20261007-r2') $failed
    $records=@(Get-PublishedCopyRecords);Confirm-PublishedCopyRecords $records
    Assert-StoppedDaemons
    if((Test-Path -LiteralPath $root) -or $null -ne (Get-ExactTaskOrAbsent $taskName)){throw 'Fresh verification namespace appeared; preserve it.'}
    New-PrivateDirectory $root -Root
    Copy-PublishedRecords $records
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Read-only verification of already-published knowledge; no KnowledgeService/refresh, index rebuild, state cleanup, model job or activation. Preserve every prior task/root/receipt.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT5M'
    $action=$definition.Actions.Create(0);$action.Path=$python;$action.Arguments='-I -B "'+(Join-Path $root 'verify-published-knowledge.py')+'" '+$nonce;$action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-KnowledgeTask $instance;Assert-CompletedKnowledgeTask $task
    $receiptPath=Join-Path $root 'published-verification.json';Assert-EffectivePrivateReceipt $receiptPath
    if((Get-Item -LiteralPath $receiptPath).Length -gt 131072){throw 'Verification receipt exceeds bound.'}
    $verifiedHash=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $stream=Hold-PinnedFile $receiptPath $verifiedHash 131072;$receipt=(Read-HeldText $stream)|ConvertFrom-Json
    Assert-PublishedReceiptBinding $receipt $nonce
    if($receipt.status -ceq 'PUBLISHED_KNOWLEDGE_VERIFICATION_HELD'){
        Write-PublishedFailure $receipt $receiptPath $verifiedHash $task.LastTaskResult
        throw 'Published knowledge verification held; review the reported substep and preserve every artifact.'
    }
    if($task.LastTaskResult -ne 0){throw 'Published verification task exit differs; preserve its private receipt.'}
    Assert-PublishedSuccess $receipt $failed
    [ordered]@{schema='cochem-published-knowledge-verification-result/1';status=$receipt.status;receipt_path=$receiptPath;receipt_sha256=$verifiedHash;last_task_result=[long]$task.LastTaskResult;task_preserved=$true;runtime_root=$installRoot;pipeline_config_sha256=$configHash;source_manifest_sha256=$manifestHash;failed_resume_receipt_sha256=$failedResumeHash;generation=$receipt.generation;index_sha256=$receipt.index_sha256;documents=$receipt.documents;sections=$receipt.sections;corpus_files=$receipt.corpus_files;integrity_check=$receipt.integrity_check;root_metadata_differences=(Get-PublishedMetadataDifferences $receipt);existing_state_read_only=$true;activation_ready=$false}|ConvertTo-Json -Depth 6
}finally{foreach($stream in $held){$stream.Dispose()}}
