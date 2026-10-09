#Requires -Version 5.1
<# Default only plans. Apply creates ONE new private diagnostic root/task.
   The failed acceptance task, corpus and index state are never changed or rerun. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$basePython=Join-Path $basePythonRoot 'python.exe';$python=Join-Path $installRoot '.venv\Scripts\python.exe';$venvConfig=Join-Path $installRoot '.venv\pyvenv.cfg'
$originalRoot='C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006'
$root='C:\Program Files\CoChem\KnowledgeDiagnostic4.2.7-windows-20261007-a'
$taskName='CoChem-4.2.7-KnowledgeDiagnostic-20261007-a'
$source=Join-Path $PSScriptRoot 'diagnose-knowledge-state.py';$sourceHash='e799d3d932a42af251c5ea752530eb9bcee022196d0c22db60c75e481e67da87'
$originalNonce='d4606fa578ae401b81ace37cd8e2909b';$originalHelperHash='832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca'
$inventoryHash='edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892';$configHash='2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
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
function Assert-OriginalReceipt {
    param($Receipt)
    if($Receipt.schema -cne 'cochem-private-knowledge-system-acceptance/1' -or $Receipt.nonce -cne $originalNonce -or $Receipt.system_sid -cne 'S-1-5-18' -or $Receipt.status -cne 'KNOWLEDGE_ACCEPTANCE_FAILED' -or $Receipt.helper_sha256 -cne $originalHelperHash -or $Receipt.payload_inventory_sha256 -cne $inventoryHash -or $Receipt.pipeline_config_sha256 -cne $configHash -or $Receipt.index_created_new -isnot [bool] -or $Receipt.index_created_new -ne $true -or $Receipt.started_at_unix_ms -ne 1791378634141 -or $Receipt.finished_at_unix_ms -ne 1791378635667 -or $Receipt.failure.phase -cne 'new_index' -or $Receipt.failure.error_type -cne 'WindowsIsolationError'){throw 'Original failed receipt differs from the preserved observed failure.'}
}
function Assert-OriginalTask {
    param($Task)
    if($null -eq $Task){throw 'Original failed task is missing; do not infer a safe retry.'}
    $definition=$Task.Definition
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 2 -or $definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $definition.Principal.LogonType -ne 5 -or $definition.Principal.RunLevel -ne 1 -or $definition.Triggers.Count -ne 0 -or $definition.Actions.Count -ne 1){throw 'Original failed task is not the expected terminal SYSTEM task.'}
    $action=$definition.Actions.Item(1)
    $expected='-I -B "'+(Join-Path $originalRoot 'accept-private-knowledge.py')+'" '+$originalNonce+' '+$inventoryHash
    if($action.Type -ne 0 -or $action.Path -cne $python -or $action.Arguments -cne $expected -or $action.WorkingDirectory -cne $originalRoot){throw 'Original task action binding differs.'}
}
function Assert-DiagnosticReceipt {
    param($Receipt,[string]$Nonce,[string]$OriginalSha)
    if($Receipt.schema -cne 'cochem-knowledge-state-diagnostic/1' -or $Receipt.nonce -cne $Nonce -or $Receipt.system_sid -cne 'S-1-5-18' -or $Receipt.helper_sha256 -cne $sourceHash -or $Receipt.status -cne 'METADATA_DIAGNOSTIC_COMPLETE' -or $Receipt.original_receipt_sha256 -cne $OriginalSha -or $Receipt.index_contents_read_or_hashed -ne $false -or $Receipt.knowledge_service_constructed -ne $false -or $Receipt.existing_acl_or_files_modified -ne $false){throw 'Diagnostic receipt lacks complete nonce-bound read-only evidence.'}
}
function Assert-EffectivePrivateReceipt {
    param([string]$Path)
    # Python's exclusive file creation inherits the private parent DACL. Match
    # production validate_private_path: effective grants matter, not whether
    # inheritance is disabled on this individual file. Never rewrite its ACL.
    Assert-NoReparseAncestors $Path
    $acl=Get-Acl -LiteralPath $Path
    $trusted=@('S-1-5-18','S-1-5-32-544')
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Diagnostic receipt owner is not private.'}
    $systemGrant=$false;$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if(-not $rules.Count){throw 'Diagnostic receipt has no explicit effective grants.'}
    foreach($rule in $rules){
        $sid=$rule.IdentityReference.Value
        if($rule.AccessControlType -ne 'Allow' -or $sid -notin $trusted -or [int64]$rule.FileSystemRights -ne 2032127 -or ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)){throw 'Diagnostic receipt grants differ from SYSTEM/Admin private access.'}
        if($sid -eq 'S-1-5-18'){$systemGrant=$true}
    }
    if(-not $systemGrant){throw 'Diagnostic receipt lacks SYSTEM full access.'}
}
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
foreach($text in @(Import-VerifiedDefinitions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($text))}
$knowledgeWrapper=Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1'
foreach($text in @(Import-VerifiedDefinitions $knowledgeWrapper '5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a' @('New-PrivateAcl','Assert-PrivateItem','New-PrivateDirectory','Copy-PrivateFile','Assert-CodeTreeOnce','Assert-VenvBinding','Get-ExactTaskOrAbsent','Assert-StoppedDaemons','Wait-KnowledgeTask','Assert-CompletedKnowledgeTask'))){. ([scriptblock]::Create($text))}
Initialize-FileIdentity
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    foreach($pair in @(@($source,$sourceHash),@($python,'560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'),@($basePython,'d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'),@($venvConfig,'d6ebb0d905488486e5a438a8a30a589f256f3499515baf1dd9d4ab71693b5f95'),@((Join-Path $installRoot 'pipeline.json'),$configHash))){
        if($pair[0] -ne $source){Assert-ProtectedPath $pair[0]}
        $stream=Open-VerifiedFile $pair[0] $pair[1] (Get-Item -LiteralPath $pair[0]).Length;$held.Add($stream)
        if($pair[0] -eq $venvConfig){$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);Assert-VenvBinding $reader.ReadToEnd()}
    }
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');$holds=@()
    if(Test-Path -LiteralPath $root){$holds+='New diagnostic root already exists; preserve it.'}
    if($null -ne (Get-ExactTaskOrAbsent $taskName)){$holds+='New diagnostic task already exists; preserve it.'}
    Assert-StoppedDaemons
    if(-not $Apply){[ordered]@{schema='cochem-knowledge-state-diagnostic-plan/1';mode='READ_ONLY_PLAN';source_sha256=$sourceHash;target_root=$root;task_name=$taskName;original_task_preserved=$true;state_scope='Exact failed new state root, direct children and one generation directory level; maximum 128 entries';state_file_data_access=$false;new_empty_synthetic_directories=@('fixture-inherited','fixture-mode700');synthetic_directory_scope='New private diagnostic root only; preserved for review';admin_checks_deferred=@('exact original failed task terminal/action/exit binding','private original receipt/helper/inventory custody');system_diagnostic_executed=$false;holds=$holds}|ConvertTo-Json -Depth 4;return}
    if($holds.Count){throw ($holds -join ' ')}
    $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce $basePythonRoot
    Assert-OriginalTask (Get-ExactTaskOrAbsent 'CoChem-4.2.7-PrivateKnowledge-Acceptance')
    $originalReceipt=Join-Path $originalRoot 'knowledge-acceptance.json';Assert-ProtectedPath $originalReceipt
    if((Get-Item -LiteralPath $originalReceipt).Length -gt 32768){throw 'Original receipt exceeds bound.'}
    $originalSha=(Get-FileHash -LiteralPath $originalReceipt -Algorithm SHA256).Hash.ToLowerInvariant()
    foreach($pair in @(@($originalReceipt,$originalSha),@((Join-Path $originalRoot 'accept-private-knowledge.py'),$originalHelperHash),@((Join-Path $originalRoot 'private-install-inventory.json'),$inventoryHash))){
        Assert-ProtectedPath $pair[0];$stream=Open-VerifiedFile $pair[0] $pair[1] (Get-Item -LiteralPath $pair[0]).Length;$held.Add($stream)
        if($pair[0] -eq $originalReceipt){$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);Assert-OriginalReceipt ($reader.ReadToEnd()|ConvertFrom-Json)}
    }
    Assert-StoppedDaemons
    New-PrivateDirectory $root -Root
    $destination=Join-Path $root 'diagnose-knowledge-state.py';$sourceLength=(Get-Item -LiteralPath $source).Length
    Copy-PrivateFile $source $destination $sourceHash $sourceLength
    $held.Add((Open-VerifiedFile $destination $sourceHash $sourceLength))
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Bounded metadata/ACL diagnostic of failed new knowledge state plus two empty mode/inheritance fixtures in this NEW diagnostic root only. No index content reads, constructor/refresh, existing ACL changes or existing task mutations.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT5M'
    $action=$definition.Actions.Create(0);$action.Path=$python;$action.Arguments='-I -B "'+$destination+'" '+$nonce+' '+$originalSha;$action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-KnowledgeTask $instance;Assert-CompletedKnowledgeTask $task
    if($task.LastTaskResult -ne 0){throw 'Diagnostic did not exit zero. Preserve its new task/root and original state; no retry or cleanup.'}
    $receiptPath=Join-Path $root 'diagnostic.json';Assert-EffectivePrivateReceipt $receiptPath
    if((Get-Item -LiteralPath $receiptPath).Length -gt 131072){throw 'Diagnostic receipt exceeds bound.'}
    $receiptSha=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $stream=Open-VerifiedFile $receiptPath $receiptSha (Get-Item -LiteralPath $receiptPath).Length;$held.Add($stream)
    $reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);$receipt=$reader.ReadToEnd()|ConvertFrom-Json
    Assert-DiagnosticReceipt $receipt $nonce $originalSha
    # Print the allowlisted metadata report; do not export private source files.
    [ordered]@{schema='cochem-knowledge-state-diagnostic-result/1';status=$receipt.status;receipt_path=$receiptPath;receipt_sha256=$receiptSha;last_task_result=[int64]$task.LastTaskResult;task_preserved=$true;original_state_preserved=$true;diagnostic=$receipt}|ConvertTo-Json -Depth 12
}finally{foreach($stream in $held){$stream.Dispose()}}
