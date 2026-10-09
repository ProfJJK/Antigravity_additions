#Requires -Version 5.1
<# DRAFT: read-only plan unless the owner explicitly supplies -Apply after review.
   Private corpus copy uses SYSTEM/Admin-only ACLs at creation, never the public
   payload copier. It preserves existing targets/tasks/indexes and never enables
   a daemon, invokes a model, imports source databases or changes configuration. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$administrator=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK') {throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if ($Apply -and (-not $administrator -or $identity.Name -ne 'AETHERDESK\ansac')) {throw '-Apply requires the owner in Administrator Windows PowerShell. No private bytes were copied.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006'
$corpusRoot='C:\Program Files\CoChem\Knowledge4.2.7-windows-20261006'
$acceptRoot='C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006'
$stateRoot='C:\ProgramData\CoChemPipeline427\private\knowledge-windows-20261006'
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$basePython=Join-Path $basePythonRoot 'python.exe'
$venvConfig=Join-Path $installRoot '.venv\pyvenv.cfg'
$configPath=Join-Path $installRoot 'pipeline.json'
$configHash='2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
$candidate='C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\knowledge-continuation-candidate-20261007T053341Z'
$sourceRoot=Join-Path $candidate 'corpus'
$inventoryPath=Join-Path $candidate 'custody\private-install-inventory.json'
$inventoryHash='edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892'
$source=Join-Path $PSScriptRoot 'accept-private-knowledge.py'
$sourceHash='832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca'
$taskName='CoChem-4.2.7-PrivateKnowledge-Acceptance'
$helper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
# Import exact reviewed READ/identity checks only. Do not import its public ACL
# builders, directory creator or Copy-VerifiedPayload (which grants Users read).
$stream=[IO.File]::Open($helper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
    if ($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b') {throw 'Reviewed file-custody helper changed.'}
    $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
    if ($errors.Count) {throw 'File-custody helper parse error.'}
    $names=@('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile')
    foreach ($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) {if ($function.Name -in $names) {. ([scriptblock]::Create($function.Extent.Text))}}
} finally {$stream.Dispose()}
Initialize-FileIdentity

function New-PrivateAcl {
    param([bool]$Directory)
    $acl=if ($Directory) {[Security.AccessControl.DirectorySecurity]::new()} else {[Security.AccessControl.FileSecurity]::new()}
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    $inherit=if ($Directory) {[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'} else {[Security.AccessControl.InheritanceFlags]::None}
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),[Security.AccessControl.FileSystemRights]::FullControl,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
    }
    return $acl
}
function Assert-PrivateItem {
    param([string]$Path)
    Assert-NoReparseAncestors $Path
    $acl=Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544') -or -not $acl.AreAccessRulesProtected) {throw 'Private copy destination owner/inheritance is not trusted.'}
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) {throw 'Private destination requires exactly SYSTEM and Administrators grants.'}
    $sids=@()
    foreach ($rule in $rules) {
        $sid=$rule.IdentityReference.Value;$sids+=$sid
        if ($sid -notin @('S-1-5-18','S-1-5-32-544') -or $rule.AccessControlType -ne 'Allow' -or [int64]$rule.FileSystemRights -ne 2032127 -or $rule.PropagationFlags -ne 'None') {throw 'Private destination grants an unreviewed permission.'}
    }
    if (@($sids|Sort-Object -Unique).Count -ne 2) {throw 'Missing private destination trustee.'}
}
function New-PrivateDirectory {
    param([string]$Path,[switch]$Root)
    if (Test-Path -LiteralPath $Path) {throw 'Private destination already exists; preserve and review it.'}
    if ($Root) {Assert-ProtectedPath (Split-Path -Parent $Path)} else {Assert-PrivateItem (Split-Path -Parent $Path)}
    [IO.DirectoryInfo]::new($Path).Create((New-PrivateAcl $true))
    Assert-PrivateItem $Path
}
function Copy-PrivateFile {
    param([string]$Source,[string]$Destination,[string]$Sha256,[long]$Length)
    $inputFile=Open-VerifiedFile $Source $Sha256 $Length
    try {
        Assert-PrivateItem (Split-Path -Parent $Destination)
        $outputFile=[IO.FileStream]::new($Destination,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,1048576,[IO.FileOptions]::None,(New-PrivateAcl $false))
        try {$inputFile.CopyTo($outputFile);$outputFile.Flush($true)} finally {$outputFile.Dispose()}
    } finally {$inputFile.Dispose()}
    Assert-PrivateItem $Destination
    $verified=Open-VerifiedFile $Destination $Sha256 $Length;$verified.Dispose()
}
function Assert-RelativeCorpusPath {
    param([string]$Path,[switch]$Directory)
    if (-not $Path -or $Path.Length -gt 1024 -or $Path -match '[\\\x00-\x1f<>:"|?*]' -or $Path.StartsWith('/')) {throw 'Unsafe relative corpus path.'}
    foreach ($part in $Path.Split('/')) {
        if ($part -in @('','.', '..') -or $part.EndsWith(' ') -or $part.EndsWith('.') -or $part -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)') {throw 'Windows-ambiguous corpus path.'}
    }
    if ($Directory) {if ($Path -notmatch '^(?:\.sources|wiki)(?:/|$)') {throw 'Unknown corpus directory.'}}
    elseif ($Path -ne 'v4.1.2_manifest.json' -and $Path -notmatch '^(?:\.sources|wiki)/.+\.md$') {throw 'Unknown corpus file.'}
}
function Get-CorpusInventoryPlan {
    param($Inventory)
    if ($Inventory.schema -ne 'cochem-private-knowledge-payload/1' -or $Inventory.host -ne 'AETHERDESK' -or $Inventory.source_root -ne $sourceRoot -or $Inventory.target_root -ne $corpusRoot -or $Inventory.target_state -ne $stateRoot -or $Inventory.documents -ne 137) {throw 'Private candidate inventory identity differs.'}
    if (@($Inventory.files).Count -ne 138 -or @($Inventory.directories).Count -ne 4 -or $Inventory.total_file_bytes -ne 2156744) {throw 'Private candidate inventory count/size differs.'}
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $Inventory.files) {
        Assert-RelativeCorpusPath ([string]$file.relative)
        if (-not $seen.Add([string]$file.relative) -or [string]$file.sha256 -notmatch '^[0-9a-f]{64}$' -or $file.length -lt 1 -or $file.length -gt 1048576) {throw 'Invalid private candidate file record.'}
        [pscustomobject]@{relative=[string]$file.relative;source=(Join-Path $sourceRoot $file.relative);destination=(Join-Path $corpusRoot $file.relative);sha256=[string]$file.sha256;length=[long]$file.length}
    }
    $seen.Clear()
    foreach ($directory in $Inventory.directories) {Assert-RelativeCorpusPath ([string]$directory) -Directory;if (-not $seen.Add([string]$directory)) {throw 'Duplicate private candidate directory.'}}
}
function Assert-ExactCorpusTree {
    param([string]$Path,$Inventory,[switch]$Private)
    Assert-NoReparseAncestors $Path
    $foundFiles=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $foundDirs=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path);$count=0
    while ($queue.Count -gt 0) {
        $current=$queue.Dequeue();if ($Private) {Assert-PrivateItem $current}
        foreach ($item in Get-ChildItem -LiteralPath $current -Force) {
            $count++;if ($count -gt 142) {throw 'Extra candidate entry.'}
            Assert-NoReparseAncestors $item.FullName
            $relative=$item.FullName.Substring($Path.Length+1).Replace('\','/')
            if ($item.PSIsContainer) {$null=$foundDirs.Add($relative);$queue.Enqueue($item.FullName)}
            else {$null=$foundFiles.Add($relative);if ($Private) {Assert-PrivateItem $item.FullName}}
        }
    }
    if ($foundFiles.Count -ne 138 -or $foundDirs.Count -ne 4) {throw 'Candidate tree differs from exact inventory.'}
    foreach ($file in $Inventory.files) {if (-not $foundFiles.Contains([string]$file.relative)) {throw 'Candidate inventory file missing.'}}
    foreach ($directory in $Inventory.directories) {if (-not $foundDirs.Contains([string]$directory)) {throw 'Candidate inventory directory missing.'}}
}
function Get-ExactTaskOrAbsent {
    param([string]$Name)
    try {return $folder.GetTask($Name)} catch {
        $errorItem=$_.Exception
        while ($null -ne $errorItem) {if ($errorItem.HResult -eq -2147024894) {return $null};$errorItem=$errorItem.InnerException}
        throw 'Cannot establish exact task state.'
    }
}
function Assert-StoppedDaemons {
    foreach ($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')) {
        $task=Get-ExactTaskOrAbsent $name
        if ($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)) {throw 'Protected daemons must remain stopped and disabled.'}
    }
}
function Wait-KnowledgeTask {
    param($Instance,[int]$TimeoutSeconds=295)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        try {$Instance.Refresh()} catch {
            $completed=$false;$errorItem=$_.Exception
            while ($null -ne $errorItem) {if ($errorItem.HResult -eq -2147216629) {$completed=$true;break};$errorItem=$errorItem.InnerException}
            if ($completed) {return};throw
        }
        if ([DateTime]::UtcNow -gt $deadline) {throw 'Knowledge acceptance wait timed out. Preserve every new target and task; do not retry or enable daemons.'}
    }while($Instance.State -in @(2,4))
}
function Assert-CompletedKnowledgeTask {
    param($Task)
    if ($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0) {throw 'Knowledge task is not conclusively terminal. Preserve task/root; do not accept its receipt or retry.'}
}
function Assert-VenvBinding {
    param([string]$Text)
    $settings=@{}
    foreach($line in ($Text -split '\r?\n')) {
        if(-not $line.Trim()){continue}
        if($line -notmatch '^([a-z-]+) = (.+)$' -or $settings.ContainsKey($matches[1])){throw 'Malformed or duplicate virtual environment setting.'}
        $settings[$matches[1]]=$matches[2]
    }
    if($settings['home'] -cne $basePythonRoot -or $settings['executable'] -cne $basePython -or $settings['version'] -cne '3.12.13' -or $settings['include-system-site-packages'] -cne 'false'){throw 'Virtual environment does not bind the exact reviewed protected Python.'}
}
function Assert-CodeTreeOnce {
    param([string]$Path)
    # Check ancestry once; protected parents prevent untrusted replacement.
    # Every child still receives its own ACL and file-handle identity check.
    Assert-ProtectedPath $Path
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path)
    $count=0;$deadline=[DateTime]::UtcNow.AddMinutes(4)
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    while($queue.Count){
        if(++$count -gt 50000 -or [DateTime]::UtcNow -gt $deadline){throw 'Protected code inspection exceeded its bound; no Python was executed.'}
        $item=Get-Item -LiteralPath ($queue.Dequeue()) -Force -ErrorAction Stop
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Protected code reparse entry refused.'}
        $acl=Get-Acl -LiteralPath $item.FullName
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Protected code has an untrusted owner.'}
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
            if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)){throw 'Protected code has an untrusted writer.'}
        }
        if($item.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$queue.Enqueue($child.FullName)}}else{
            # Metadata only, including bundled resources; no database content read.
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()}
        }
    }
    $count
}

# All source identity/bytes checks precede any protected write.
$inventoryLength=(Get-Item -LiteralPath $inventoryPath).Length
if ($inventoryLength -gt 1048576) {throw 'Private inventory exceeds its bound.'}
$stream=Open-VerifiedFile $inventoryPath $inventoryHash $inventoryLength
try {$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);$inventory=$reader.ReadToEnd()|ConvertFrom-Json} finally {$stream.Dispose()}
$files=@(Get-CorpusInventoryPlan $inventory)
Assert-ExactCorpusTree $sourceRoot $inventory
foreach ($file in $files) {$stream=Open-VerifiedFile $file.source $file.sha256 $file.length;$stream.Dispose()}
$sourceLength=(Get-Item -LiteralPath $source).Length
$stream=Open-VerifiedFile $source $sourceHash $sourceLength;$stream.Dispose()
$holds=@()
foreach ($path in @($corpusRoot,$acceptRoot)) {if (Test-Path -LiteralPath $path) {$holds+='A target already exists; preserve it: '+$path}}
foreach ($path in @($python,$configPath,$basePython,$venvConfig)) {if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {$holds+='Stopped installation is missing a required protected file: '+$path} else {Assert-ProtectedPath $path}}
if (Test-Path -LiteralPath $configPath -PathType Leaf) {$stream=Open-VerifiedFile $configPath $configHash (Get-Item -LiteralPath $configPath).Length;$stream.Dispose()}
if (Test-Path -LiteralPath $venvConfig -PathType Leaf) {
    $stream=Open-VerifiedFile $venvConfig 'd6ebb0d905488486e5a438a8a30a589f256f3499515baf1dd9d4ab71693b5f95' (Get-Item -LiteralPath $venvConfig).Length
    try {$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);Assert-VenvBinding $reader.ReadToEnd()} finally {$stream.Dispose()}
}
foreach($pin in @(@($python,'560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'),@($basePython,'d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'))) {
    if(Test-Path -LiteralPath $pin[0] -PathType Leaf){$stream=Open-VerifiedFile $pin[0] $pin[1] (Get-Item -LiteralPath $pin[0]).Length;$stream.Dispose()}
}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
if ($null -ne (Get-ExactTaskOrAbsent $taskName)) {$holds+='Private knowledge acceptance task already exists; preserve it.'}
Assert-StoppedDaemons
$plan=[ordered]@{schema='cochem-private-knowledge-install-plan/1';mode='READ_ONLY_PLAN';owner=$identity.Name;administrator=$administrator;source_inventory_sha256=$inventoryHash;acceptance_helper_sha256=$sourceHash;files_verified=$files.Count;bytes_verified=2156744;documents=137;target_corpus=$corpusRoot;target_index_state=$stateRoot;index_absence='Must be verified by SYSTEM before corpus provisioning/index creation';target_acl='Protected SYSTEM/Admin FullControl only, before first private byte';task_name=$taskName;holds=$holds;private_files_copied=0;source_databases_read_or_modified=$false;models_executed=0;configuration_changed=$false;daemon_started=$false}
if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;return}
if ($holds.Count) {throw ($holds -join ' ')}
# Validate trusted interpreter/package custody before SYSTEM scheduling.
$verifiedInstallEntries=Assert-CodeTreeOnce $installRoot
$verifiedBasePythonEntries=Assert-CodeTreeOnce $basePythonRoot
Assert-StoppedDaemons
New-PrivateDirectory $corpusRoot -Root
New-PrivateDirectory $acceptRoot -Root
foreach ($directory in @($inventory.directories|Sort-Object Length)) {New-PrivateDirectory (Join-Path $corpusRoot $directory)}
foreach ($file in $files) {Copy-PrivateFile $file.source $file.destination $file.sha256 $file.length}
Assert-ExactCorpusTree $corpusRoot $inventory -Private
$installedHelper=Join-Path $acceptRoot 'accept-private-knowledge.py'
Copy-PrivateFile $source $installedHelper $sourceHash $sourceLength
Copy-PrivateFile $inventoryPath (Join-Path $acceptRoot 'private-install-inventory.json') $inventoryHash $inventoryLength
Assert-StoppedDaemons
$nonce=[Guid]::NewGuid().ToString('N')
$definition=$scheduler.NewTask(0)
$definition.RegistrationInfo.Description='One SYSTEM validation of private corpus bytes/ACLs and a fresh knowledge index. No source DB imports, model jobs, budgets, config changes or daemon starts.'
$definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
$definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT5M'
$action=$definition.Actions.Create(0);$action.Path=$python;$action.Arguments='-I -B "'+$installedHelper+'" '+$nonce+' '+$inventoryHash;$action.WorkingDirectory=$acceptRoot
$task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
$instance=$task.Run($null);Wait-KnowledgeTask $instance
Assert-CompletedKnowledgeTask $task
$receiptPath=Join-Path $acceptRoot 'knowledge-acceptance.json'
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {throw ('Knowledge acceptance has no receipt. Preserve targets/task. LastTaskResult='+$task.LastTaskResult)}
Assert-ProtectedPath $receiptPath
if ((Get-Item -LiteralPath $receiptPath).Length -gt 32768) {throw 'Knowledge acceptance receipt exceeds bound.'}
$receipt=Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8|ConvertFrom-Json
if ($receipt.schema -ne 'cochem-private-knowledge-system-acceptance/1' -or $receipt.nonce -ne $nonce -or $receipt.system_sid -ne 'S-1-5-18' -or $receipt.helper_sha256 -ne $sourceHash -or $receipt.payload_inventory_sha256 -ne $inventoryHash) {throw 'Knowledge receipt binding differs.'}
[ordered]@{schema='cochem-private-knowledge-install-result/1';status=$receipt.status;receipt_path=$receiptPath;receipt_sha256=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant();last_task_result=$task.LastTaskResult;task_preserved=$true;legacy_full_continuity_verified=$false;configuration_modified=$false;daemon_started=$false}|ConvertTo-Json -Depth 4
if ($receipt.status -ne 'PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED' -or $task.LastTaskResult -ne 0) {throw 'Private knowledge acceptance did not pass. Preserve every new target and keep daemons disabled; no automatic retry or reset.'}
