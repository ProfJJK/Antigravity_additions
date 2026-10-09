#Requires -Version 5.1
<# One explicit resume of the SYSTEM-diagnosed blank knowledge state.
   COPY-ONLY FAILURE RECOVERY: continue only the existing empty private root.
   Default is read-only. No root recreation, cleanup or existing task overwrite.
   Refuse any existing child or resume task; preserve all original evidence. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2'
$oldInstall='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$basePython=Join-Path $basePythonRoot 'python.exe';$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$venvConfig=Join-Path $installRoot '.venv\pyvenv.cfg'
$originalRoot='C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006'
$diagnosticRoot='C:\Program Files\CoChem\KnowledgeDiagnostic4.2.7-windows-20261007-a'
$root='C:\Program Files\CoChem\KnowledgeResume4.2.7-windows-20261007-r2'
$rootObservedUtc='2026-10-07T14:08:15.5776970Z'
$taskName='CoChem-4.2.7-KnowledgeResume-20261007-r2'
$source=Join-Path $PSScriptRoot 'resume-private-knowledge.py'
$sourceHash='780daf29270b0c89cbbc98608eb9ca816543e36e8332323d944410658e665a7b'
$receiptHash='d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4'
$manifestHash='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$originalHash='2cf8f1f4cafbb0537900af98349f6a3f4faec135a4061dd153f0cce78c1488dd'
$diagnosticHash='18ceba4394b92bb6e1d320f49226359902af0084a72195a8139f699594bc0307'
function Import-VerifiedDefinitions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed custody function source changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Custody function source parse error.'};$found=@()
        foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($f.Name -in $Names){$found+=@($f.Name);$f.Extent.Text}}
        if(@($Names|Where-Object{$_ -notin $found}).Count){throw 'Required custody function is absent.'}
    }finally{$stream.Dispose()}
}
function Assert-PreservedTask {
    param($Task,[string]$Root,[string]$File,[string]$Arguments,[int64]$Result)
    if($null -eq $Task){throw 'Preserved task is missing; no safe resume inferred.'}
    $d=$Task.Definition
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne $Result -or $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'Preserved task is not the expected terminal SYSTEM task.'}
    $action=$d.Actions.Item(1)
    $expected='-I -B "'+(Join-Path $Root $File)+'" '+$Arguments
    if($action.Type -ne 0 -or $action.Path -cne (Join-Path $oldInstall '.venv\Scripts\python.exe') -or $action.Arguments -cne $expected -or $action.WorkingDirectory -cne $Root){throw 'Preserved task action differs.'}
}
function Assert-ResumeReceipt {
    param($Receipt,[string]$Nonce)
    if($Receipt.schema -cne 'cochem-private-knowledge-resume/1' -or $Receipt.nonce -cne $Nonce -or $Receipt.system_sid -cne 'S-1-5-18' -or
       $Receipt.helper_sha256 -cne $sourceHash -or $Receipt.runtime_root -cne $installRoot -or $Receipt.install_receipt_sha256 -cne $receiptHash -or
       $Receipt.original_receipt_sha256 -cne $originalHash -or $Receipt.diagnostic_receipt_sha256 -cne $diagnosticHash -or
       $Receipt.status -cne 'PRESERVED_CORPUS_AND_RESUMED_INDEX_VERIFIED' -or $Receipt.original_root_and_lock_preserved -ne $true -or
       $Receipt.documents -ne 137 -or $Receipt.corpus_files -ne 138 -or $Receipt.index_integrity_check -cne 'ok' -or
       $Receipt.source_bytes_preserved -ne $true -or $Receipt.canonical_authority_matches_capture -ne $true -or
       $Receipt.existing_acl_modified -ne $false -or $Receipt.corpus_reprovisioned -ne $false -or
       $Receipt.old_tasks_modified_or_run -ne $false -or $Receipt.budgets_modified -ne $false -or $Receipt.activation_ready -ne $false){throw 'Resume receipt lacks complete nonce-bound acceptance.'}
}
function Assert-R2VenvBinding {
    param([string]$Text)
    # r2 was created by frozen uv, whose format differs from old python -m venv.
    $settings=@{}
    foreach($line in ($Text -split '\r?\n')){
        if(-not $line.Trim()){continue}
        if($line -notmatch '^([a-z_-]+) = (.+)$' -or $settings.ContainsKey($matches[1])){throw 'Malformed or duplicate r2 virtual environment field.'}
        $settings[$matches[1]]=$matches[2]
    }
    if($settings.Count -ne 6 -or $settings['home'] -cne $basePythonRoot -or $settings['implementation'] -cne 'CPython' -or $settings['uv'] -cne '0.12.17' -or $settings['version_info'] -cne '3.12.13' -or $settings['include-system-site-packages'] -cne 'false' -or $settings['prompt'] -cne 'cochem'){throw 'r2 virtual environment binding differs from actual pinned installation.'}
}
function Get-ResumeFailure {
    param($Receipt,[string]$Nonce)
    if($Receipt.schema -cne 'cochem-private-knowledge-resume/1' -or $Receipt.nonce -cne $Nonce -or
       $Receipt.system_sid -cne 'S-1-5-18' -or $Receipt.helper_sha256 -cne $sourceHash -or
       $Receipt.runtime_root -cne $installRoot -or $Receipt.install_receipt_sha256 -cne $receiptHash -or
       $Receipt.original_receipt_sha256 -cne $originalHash -or $Receipt.diagnostic_receipt_sha256 -cne $diagnosticHash -or
       $Receipt.status -cne 'KNOWLEDGE_RESUME_HELD'){throw 'No valid bound failure receipt; preserve task/root and inspect independently.'}
    $failure=$Receipt.failure
    if($failure.phase -notin @('runtime_binding','preserved_evidence','configuration','corpus_verification','blank_state','index_resume','final_verification') -or
       [string]$failure.error_type -cnotmatch '^[A-Za-z0-9_]{1,80}$' -or
       ($null -ne $failure.winerror -and (($failure.winerror -isnot [int] -and $failure.winerror -isnot [long]) -or $failure.winerror -lt 0 -or $failure.winerror -gt 4294967295))){throw 'Failure metadata is not bounded; preserve task/root for review.'}
    [ordered]@{phase=[string]$failure.phase;error_type=[string]$failure.error_type;winerror=$failure.winerror}
}
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
foreach($text in @(Import-VerifiedDefinitions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($text))}
foreach($text in @(Import-VerifiedDefinitions (Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1') '5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a' @('New-PrivateAcl','Assert-PrivateItem','Copy-PrivateFile','Assert-CodeTreeOnce','Get-ExactTaskOrAbsent','Assert-StoppedDaemons','Wait-KnowledgeTask','Assert-CompletedKnowledgeTask'))){. ([scriptblock]::Create($text))}
foreach($text in @(Import-VerifiedDefinitions (Join-Path $PSScriptRoot 'diagnose-knowledge-state.ps1') '65887612ce9b1c6f563437d72d3a5a44f3dd01e47a2ee029416198ca27f02370' @('Assert-EffectivePrivateReceipt'))){. ([scriptblock]::Create($text))}
# Reuse the reviewed r2 source-path allowlist; never execute its installer body.
$script:repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions';$script:targetRoot=$installRoot;$script:sourceRoot=Join-Path $installRoot 'source'
foreach($text in @(Import-VerifiedDefinitions (Join-Path $PSScriptRoot 'install-stopped-runtime-r2.ps1') '1cf9d391bf54b1a4b530636ebbb3746cadc3844b0fe13a60eabb13deb9987896' @('Get-RuntimeFiles','Assert-ScopedConfigDelta'))){. ([scriptblock]::Create($text))}
Initialize-FileIdentity
$held=[Collections.Generic.List[IO.FileStream]]::new()
function Hold-PinnedFile {
    param([string]$Path,[string]$Hash,[long]$Maximum=1048576,[switch]$LocalSource)
    if(-not $LocalSource){Assert-ProtectedPath $Path}
    $length=(Get-Item -LiteralPath $Path -Force).Length
    if($length -gt $Maximum){throw 'Pinned artifact exceeds its bound.'}
    $stream=Open-VerifiedFile $Path $Hash $length;$held.Add($stream);$stream
}
function Read-HeldText {
    param([IO.FileStream]$Stream)
    $Stream.Position=0;$reader=[IO.StreamReader]::new($Stream,[Text.Encoding]::UTF8,$true,4096,$true)
    try{$reader.ReadToEnd()}finally{$reader.Dispose()}
}
function Assert-ResumeRootInheritance {
    param([string]$Path)
    $acl=Get-Acl -LiteralPath $Path
    foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if([int]$rule.InheritanceFlags -ne 3 -or $rule.PropagationFlags -ne 'None'){throw 'Preserved private root must grant inherited SYSTEM/Admin access to files and directories.'}
    }
}
function Assert-ExistingEmptyResumeRoot {
    param([string]$Path)
    if($Path -cne $root){throw 'Copyfix is restricted to the one preserved resume root.'}
    Assert-NoReparseAncestors $Path
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if(-not $item.PSIsContainer -or $item.FullName -cne $root -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Preserved resume root is not the exact ordinary directory.'}
    $expectedTime=[DateTime]::Parse($rootObservedUtc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
    if($item.CreationTimeUtc.Ticks -ne $expectedTime.Ticks -or $item.LastWriteTimeUtc.Ticks -ne $expectedTime.Ticks){throw 'Preserved empty root creation/write metadata differs from the observed failed-copy attempt.'}
    Assert-PrivateItem $Path
    Assert-ResumeRootInheritance $Path
    # Enumerate only until the first entry, including hidden/system entries.
    # Any helper, receipt or unexpected entry means this recovery is inapplicable.
    $entries=[IO.Directory]::EnumerateFileSystemEntries($Path).GetEnumerator()
    try{if($entries.MoveNext()){throw 'Preserved resume root is not empty; do not copy, delete or retry.'}}finally{if($entries -is [IDisposable]){$entries.Dispose()}}
}
function Get-ResumeCopyRecords {
    # Named records avoid PowerShell's single-nested-array enumeration rules.
    [pscustomobject]@{Name='resume-private-knowledge.py';Source=$source;Sha256=$sourceHash;Length=[long]0;LocalSource=$true}
    [pscustomobject]@{Name='diagnose-knowledge-state.py';Source=(Join-Path $diagnosticRoot 'diagnose-knowledge-state.py');Sha256='e799d3d932a42af251c5ea752530eb9bcee022196d0c22db60c75e481e67da87';Length=[long]0;LocalSource=$false}
    [pscustomobject]@{Name='accept-private-knowledge.py';Source=(Join-Path $originalRoot 'accept-private-knowledge.py');Sha256='832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca';Length=[long]0;LocalSource=$false}
}
function Confirm-ResumeCopyRecords {
    param([object[]]$Records)
    $expected=@(Get-ResumeCopyRecords)
    if($Records.Count -ne 3){throw 'Exactly three reviewed helpers are required.'}
    for($index=0;$index -lt 3;$index++){
        $record=$Records[$index];$binding=$expected[$index]
        if($record -isnot [pscustomobject] -or $record.Name -cne $binding.Name -or $record.Source -cne $binding.Source -or $record.Sha256 -cne $binding.Sha256 -or $record.LocalSource -isnot [bool] -or $record.LocalSource -ne $binding.LocalSource){throw 'Copy helper record differs from the fixed allowlist.'}
    }
    # Complete every source read/hash/ordinary/identity check before first copy.
    foreach($record in $Records){
        $stream=Hold-PinnedFile $record.Source $record.Sha256 -LocalSource:$record.LocalSource
        $record.Length=[long]$stream.Length
        if($record.Length -le 0 -or $record.Length -gt 1048576){throw 'Copy helper length exceeds its bound.'}
    }
}
function Copy-ResumeRecords {
    param([object[]]$Records)
    foreach($record in $Records){
        $destination=Join-Path $root $record.Name
        Copy-PrivateFile $record.Source $destination $record.Sha256 $record.Length
        $null=Hold-PinnedFile $destination $record.Sha256
    }
}
try{
    $null=Hold-PinnedFile $source $sourceHash -LocalSource
    $null=Hold-PinnedFile $python '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'
    $null=Hold-PinnedFile $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
    $stream=Hold-PinnedFile $venvConfig '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d' 8192;Assert-R2VenvBinding (Read-HeldText $stream)
    $stream=Hold-PinnedFile (Join-Path $installRoot 'pipeline.json') $configHash;$configText=Read-HeldText $stream
    $stream=Hold-PinnedFile (Join-Path $oldInstall 'pipeline.json') '2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
    Assert-ScopedConfigDelta (Read-HeldText $stream) $configText
    $stream=Hold-PinnedFile (Join-Path $installRoot 'install-after.json') $receiptHash;$installed=(Read-HeldText $stream)|ConvertFrom-Json
    if($installed.mode -cne 'FRESH_STOPPED_RUNTIME_READY' -or $installed.source_files -ne 166 -or $installed.source_manifest_sha256 -cne $manifestHash -or $installed.configuration_sha256 -cne $configHash -or $installed.verification.revision.verified -ne $true){throw 'Actual r2 installation receipt differs.'}
    $stream=Hold-PinnedFile (Join-Path $installRoot 'source-manifest.json') $manifestHash;$manifest=(Read-HeldText $stream)|ConvertFrom-Json
    $files=@(Get-RuntimeFiles $manifest);if($files.Count -ne 166){throw 'Exact r2 source inventory count differs.'}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');$holds=@()
    if(-not (Test-Path -LiteralPath $root)){$holds+='Expected pre-existing resume root is absent; this copyfix never creates it.'}
    if($null -ne (Get-ExactTaskOrAbsent $taskName)){$holds+='Fresh resume task already exists; preserve it and review.'}
    Assert-StoppedDaemons
    if(-not $Apply){[ordered]@{schema='cochem-private-knowledge-resume-plan/1';mode='READ_ONLY_PLAN';source_sha256=$sourceHash;runtime_root=$installRoot;install_receipt_sha256=$receiptHash;source_manifest_sha256=$manifestHash;source_files=$files.Count;target_root=$root;task_name=$taskName;diagnostic_receipt_sha256=$diagnosticHash;original_receipt_sha256=$originalHash;state_gate='Exact diagnosed root plus 1-byte writer.lock=0, rechecked under real production writer lock';corpus_files_reverified=138;corpus_acl_changes=$false;old_evidence_preserved=$true;existing_root_reused_only_if_empty=$true;existing_root_private_empty_check_deferred=$true;expected_empty_root_creation_and_write_utc=$rootObservedUtc;index_resume_executed=$false;activation_ready=$false;admin_system_checks_deferred=@('exact existing root private ACL and zero children, no existing resume task','full r2/base tree custody and held source166','private original+diagnostic receipt/task binding','exact diagnosed SYSTEM state metadata and authorized one-byte lock read');holds=$holds}|ConvertTo-Json -Depth 4;return}
    if($holds.Count){throw ($holds -join ' ')}
    Assert-ExistingEmptyResumeRoot $root
    $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce $basePythonRoot
    foreach($file in $files){$null=Hold-PinnedFile $file.destination $file.sha256 16777216}
    Assert-PreservedTask (Get-ExactTaskOrAbsent 'CoChem-4.2.7-PrivateKnowledge-Acceptance') $originalRoot 'accept-private-knowledge.py' 'd4606fa578ae401b81ace37cd8e2909b edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892' 2
    Assert-PreservedTask (Get-ExactTaskOrAbsent 'CoChem-4.2.7-KnowledgeDiagnostic-20261007-a') $diagnosticRoot 'diagnose-knowledge-state.py' ('e9f31a03b0d04b8697967407c47e3518 '+$originalHash) 0
    foreach($pair in @(@((Join-Path $originalRoot 'knowledge-acceptance.json'),$originalHash),@((Join-Path $diagnosticRoot 'diagnostic.json'),$diagnosticHash),@((Join-Path $originalRoot 'private-install-inventory.json'),'edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892'))){$null=Hold-PinnedFile $pair[0] $pair[1]}
    $records=@(Get-ResumeCopyRecords)
    Confirm-ResumeCopyRecords $records
    Assert-StoppedDaemons
    Assert-ExistingEmptyResumeRoot $root
    if($null -ne (Get-ExactTaskOrAbsent $taskName)){throw 'Resume task appeared; preserve all evidence.'}
    Copy-ResumeRecords $records
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='One exact diagnosed blank-state knowledge resume using fixed r2 runtime. Preserve corpus, original state identities, failed/diagnostic tasks and all partial evidence. No activation or automatic retry.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT5M'
    $action=$definition.Actions.Create(0);$action.Path=$python;$action.Arguments='-I -B "'+(Join-Path $root 'resume-private-knowledge.py')+'" '+$nonce;$action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-KnowledgeTask $instance;Assert-CompletedKnowledgeTask $task
    $receiptPath=Join-Path $root 'resume-acceptance.json';Assert-EffectivePrivateReceipt $receiptPath
    if((Get-Item -LiteralPath $receiptPath).Length -gt 131072){throw 'Resume receipt exceeds bound; preserve for review.'}
    $resumeSha=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $stream=Hold-PinnedFile $receiptPath $resumeSha 131072;$receipt=(Read-HeldText $stream)|ConvertFrom-Json
    if($task.LastTaskResult -ne 0){
        if($task.LastTaskResult -ne 2){throw 'Unexpected task exit; preserve all evidence for review.'}
        $failure=Get-ResumeFailure $receipt $nonce
        [ordered]@{schema='cochem-private-knowledge-resume-result/1';status='KNOWLEDGE_RESUME_HELD';receipt_path=$receiptPath;receipt_sha256=$resumeSha;last_task_result=2;task_preserved=$true;activation_ready=$false;failure=$failure}|ConvertTo-Json -Depth 4
        throw 'Knowledge resume held. Preserve every task/root/state and receipt; do not rerun, delete or enable daemons.'
    }
    Assert-ResumeReceipt $receipt $nonce
    [ordered]@{schema='cochem-private-knowledge-resume-result/1';status=$receipt.status;receipt_path=$receiptPath;receipt_sha256=$resumeSha;last_task_result=[int64]$task.LastTaskResult;task_preserved=$true;activation_ready=$false;acceptance=$receipt}|ConvertTo-Json -Depth 8
}finally{foreach($stream in $held){$stream.Dispose()}}
