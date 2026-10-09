#Requires -Version 5.1
<# Fresh code/configuration only. Never calls install_pipeline_windows.ps1.
   Default is metadata/hash inspection. -Apply requires the owner elevated.
   No account, credential, token, scheduled task, database, RAM or daemon setup.
   A completed source freeze and independent review are prerequisites. #>
[CmdletBinding()]
param([string]$Manifest,[ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestSha256,[switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-ReviewedFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -ne $Hash){throw 'Reviewed function source changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed helper parse failure.'}
        $found=@()
        foreach($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
            if($function.Name -in $Names){$found+=$function.Name;$function.Extent.Text}}
        if(@($Names|Where-Object {$_ -notin $found}).Count){throw 'Reviewed function was not found.'}
    }finally{$stream.Dispose()}
}

function Get-RuntimeFiles {
    param($Inventory)
    if($Inventory.schema -ne 'cochem-stopped-runtime-source/1' -or $Inventory.host -ne 'AETHERDESK' -or
       $Inventory.complete_source_freeze -ne $true -or $Inventory.source_repository -cne $script:repo -or
       $Inventory.target_root -cne $script:targetRoot -or
       $Inventory.previous_source_manifest_sha256 -ne 'ee994719727936497b0436448a15e80dc18f11a8b108601fd43e5a62b918260b'){
        throw 'A complete reviewed source freeze tied to the preserved installation is required.'}
    $rows=@($Inventory.files)
    if($rows.Count -lt 100 -or $rows.Count -gt 10000){throw 'Unexpected source inventory size.'}
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase);$total=[long]0
    foreach($row in $rows){
        $key=[string]$row.relative
        if($key.Contains('\') -or $key -match '[\x00-\x1f<>:"|?*]' -or
           @($key.Split('/')|Where-Object {$_ -in @('','.','..') -or $_.EndsWith('.') -or $_.EndsWith(' ') -or $_ -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)'}).Count -or
           (-not $key.StartsWith('src/',[StringComparison]::Ordinal) -and $key -notin @('README.md','pyproject.toml','uv.lock')) -or
           $key -match '(^|/)(__pycache__|[^/]+\.egg-info)(/|$)|\.pyc$' -or -not $seen.Add($key)){
            throw 'Unsafe, unexpected or duplicate source inventory path.'}
        if([string]$row.sha256 -notmatch '^[a-f0-9]{64}$' -or [long]$row.length -lt 0 -or [long]$row.length -gt 16777216){throw 'Invalid source hash/length.'}
        $total+=[long]$row.length;if($total -gt 134217728){throw 'Source snapshot exceeds 128 MiB.'}
        [pscustomobject]@{source=(Join-Path $script:repo $key);destination=(Join-Path $script:sourceRoot $key);relative=$key;sha256=[string]$row.sha256;length=[long]$row.length}
    }
    foreach($required in @('README.md','pyproject.toml','uv.lock','src/cochem_pipeline/ramdisk.py','src/cochem_pipeline/deployment.py','src/cochem_pipeline/config.py','src/cochem_pipeline/deployment_revision.py','src/cochem_supervisor/engine.py')){
        if(-not $seen.Contains($required)){throw 'Source inventory omits a required frozen file.'}}
}

function Assert-ScopedConfigDelta {
    param([string]$Original,[string]$Candidate)
    $before=$Original|ConvertFrom-Json;$after=$Candidate|ConvertFrom-Json
    if($null -ne $before.ramdisk.PSObject.Properties['workspace_subdirectory'] -or
       $after.ramdisk.workspace_subdirectory -cne 'CoChem427-windows-20261007'){
        throw 'Expected exactly one new adopted RAM workspace subdirectory.'}
    $after.ramdisk.PSObject.Properties.Remove('workspace_subdirectory')
    if(($before|ConvertTo-Json -Depth 100 -Compress) -cne ($after|ConvertTo-Json -Depth 100 -Compress)){
        throw 'Candidate changes another host binding; preserve existing credentials, routes and state.'}
}

function Assert-StoppedRuntimeTasks {
    param($Folder)
    foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
        $task=$null;try{$task=$Folder.GetTask($name)}catch{
            $missing=$false;$errorItem=$_.Exception
            while($null -ne $errorItem){if($errorItem.HResult -eq -2147024894){$missing=$true;break};$errorItem=$errorItem.InnerException}
            if(-not $missing){throw 'Daemon task state is unknown.'}}
        if($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){
            throw 'All managed pipeline daemons must remain stopped and disabled.'}}
}

function Get-FrozenSyncArguments {
    @('--no-config','--no-cache','sync','--project',$script:sourceRoot,'--frozen','--no-editable','--link-mode','copy','--extra','mcp','--python',$script:python,'--no-python-downloads')
}

function Assert-PostBuildFrozenFiles {
    param([object[]]$Files)
    foreach($file in $Files){
        $stream=Open-VerifiedFile $file.destination $file.sha256 $file.length
        $stream.Dispose()
    }
}

function Write-NewRuntimeReport {
    param([string]$Path,$Value)
    Assert-ProtectedPath (Split-Path -Parent $Path)
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-CodeAcl $false))
    try{$raw=[Text.UTF8Encoding]::new($false).GetBytes(($Value|ConvertTo-Json -Depth 12));$stream.Write($raw,0,$raw.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

function Protect-NewRuntimeTree {
    param([string]$Path)
    if([IO.Path]::GetFullPath($Path).TrimEnd('\') -cne $script:targetRoot){throw 'Only this newly created revision may receive installation ACLs.'}
    # Directory ACL inheritance may affect descendants. Prove the ENTIRE tree
    # has trusted custody, no reparse paths and no external hardlinks before
    # the first Set-Acl, rather than discovering an alias after propagation.
    $null=Assert-CodeTreeOnce $Path
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path);$count=0
    while($queue.Count){
        if(++$count -gt 50000){throw 'New runtime tree exceeds its custody bound.'}
        $item=Get-Item -LiteralPath ($queue.Dequeue()) -Force
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'New runtime contains a reparse entry; no redirect will be protected.'}
        if($item.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$queue.Enqueue($child.FullName)}}else{
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()}}
        # Never apply an ACL through a hardlink. All objects here belong to the
        # fresh versioned root, whose parent was protected before the build.
        Set-Acl -LiteralPath $item.FullName -AclObject (New-CodeAcl $item.PSIsContainer)
    }
}

function Invoke-FrozenRuntimeBuild {
    # Avoid ambient Python/uv policy and user-cache writes. The current process
    # environment is restored on every outcome; profiles/credentials are untouched.
    $saved=@{};$keys=@(Get-ChildItem Env:|Where-Object {$_.Name -match '^(UV_|PYTHON)' -or $_.Name -in @('VIRTUAL_ENV','TEMP','TMP')}|ForEach-Object {$_.Name})
    foreach($key in $keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$null,'Process')}
    try{
        $env:UV_PROJECT_ENVIRONMENT=Join-Path $script:targetRoot '.venv'
        $env:TEMP=Join-Path $script:targetRoot 'build-temp';$env:TMP=$env:TEMP
        $env:PYTHONNOUSERSITE='1';$env:PYTHONDONTWRITEBYTECODE='1'
        # Output is local to the new protected root; no inherited uv index/key
        # configuration is printed. A failed/interruptible build is never reused.
        $log=Join-Path $script:targetRoot 'frozen-build.log'
        if(Test-Path -LiteralPath $log){throw 'Preserve existing build log.'}
        $syncArguments=@(Get-FrozenSyncArguments)
        $previousPreference=$ErrorActionPreference;$global:LASTEXITCODE=$null;$buildExit=$null
        try{
            # Windows PowerShell 5.1 represents redirected native stderr as
            # ErrorRecords even for exit 0. Judge the native exit code; do not
            # mistake normal uv progress for a terminating PowerShell failure.
            $ErrorActionPreference='Continue'
            & $script:uv @syncArguments *> $log
            $buildExit=$global:LASTEXITCODE
        }finally{$ErrorActionPreference=$previousPreference}
        if($null -eq $buildExit -or $buildExit -ne 0){throw ('Frozen uv synchronization failed, exit '+$buildExit+'. Preserve the fresh root/log; no provisioning occurred.')}
    }finally{
        foreach($item in @(Get-ChildItem Env:|Where-Object {$_.Name -match '^(UV_|PYTHON)' -or $_.Name -in @('VIRTUAL_ENV','TEMP','TMP')})){
            [Environment]::SetEnvironmentVariable($item.Name,$null,'Process')}
        foreach($key in $saved.Keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
    }
}

# Invocation boundary. Tests extract actual functions only; no installation runs.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$base=Join-Path $programFiles 'CoChem'
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$oldRoot=Join-Path $base 'Pipeline4.2.7-windows-20261006'
$targetRoot=Join-Path $base 'Pipeline4.2.7-windows-20261007-r1';$sourceRoot=Join-Path $targetRoot 'source'
$python=Join-Path $base 'Toolchain4.2.7-windows-20261006\Python312\python.exe'
$uv=Join-Path $base 'Toolchain4.2.7-windows-20261006\uv\uv.exe'
$copyHelper=Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1'
$definitions=@(Import-ReviewedFunctions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory','Copy-VerifiedPayload'))
foreach($definition in $definitions){. ([scriptblock]::Create($definition))}
$definitions=@(Import-ReviewedFunctions (Join-Path $PSScriptRoot 'check-worker-denials.ps1') '1576882fe655d27894aa6f9da25a14ae4ddd08540c865d51cc43b26fa3dd6f9d' @('Assert-CodeTreeOnce'))
foreach($definition in $definitions){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$holds=@()
if(-not $Manifest -or -not $ManifestSha256){$holds+='Awaiting complete reviewed source freeze and its SHA256.'}
if(Test-Path -LiteralPath $targetRoot){$holds+='Fresh revision root already exists; preserve it and do not reuse.'}
$files=@();$inventory=$null
if($Manifest -and $ManifestSha256){
    $inventory=Read-Inventory $Manifest $ManifestSha256
    $files=@(Get-RuntimeFiles $inventory)
    foreach($file in $files){$stream=Open-VerifiedFile $file.source $file.sha256 $file.length;$stream.Dispose()}}
$oldConfig=Join-Path $oldRoot 'pipeline.json';$layout=Join-Path $oldRoot 'windows-layout.json'
$candidate=Join-Path $PSScriptRoot 'pipeline.scoped-ram.candidate.json'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$pins=@(@($python,'d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'),@($uv,'2019cdf564cb8f749262f5f021cedc75a99abb1c6081227ca340bbcda972611d'),@($oldConfig,'2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'),@($layout,'8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'),@($candidate,$configHash))
foreach($pin in $pins){$stream=Open-VerifiedFile $pin[0] $pin[1] (Get-Item -LiteralPath $pin[0]).Length;$stream.Dispose()}
Assert-ScopedConfigDelta (Get-Content -LiteralPath $oldConfig -Raw) (Get-Content -LiteralPath $candidate -Raw)
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-StoppedRuntimeTasks $folder
$plan=[ordered]@{schema='cochem-stopped-runtime-update/1';mode='READ_ONLY_PLAN';target_root=$targetRoot;source_manifest_sha256=$ManifestSha256;source_files=$files.Count;configuration_sha256=$configHash;only_config_change='ramdisk.workspace_subdirectory';accounts_provisioned=0;tasks_changed=0;credentials_modified=$false;databases_modified=$false;ram_modified=$false;pipeline_started=$false;activation_ready=$false;holds=$holds}
if(-not $Apply){$plan|ConvertTo-Json -Depth 5;return}
if($holds.Count){throw ($holds -join ' ')}
$mutex=[Threading.Mutex]::new($false,'Global\CoChem427-CodeOnlyRuntimeUpdate');$locked=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another code-only revision update owns the setup mutex.'}
    Assert-StoppedRuntimeTasks $folder
    if(Test-Path -LiteralPath $targetRoot){throw 'Fresh revision root appeared; preserve it.'}
    $null=Assert-CodeTreeOnce (Split-Path -Parent $python);Assert-ProtectedPath $uv
    $null=Assert-CodeTreeOnce $oldRoot
    New-ProtectedDirectory $targetRoot;New-ProtectedDirectory $sourceRoot
    $directories=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach($file in $files){$parent=Split-Path -Parent $file.destination;while($parent -ne $sourceRoot){$null=$directories.Add($parent);$parent=Split-Path -Parent $parent}}
    foreach($directory in @($directories|Sort-Object Length)){New-ProtectedDirectory $directory}
    foreach($file in $files){Copy-VerifiedPayload $file}
    foreach($pair in @(@($candidate,'pipeline.json',$configHash),@($layout,'windows-layout.json',$pins[3][1]),@($Manifest,'source-manifest.json',$ManifestSha256))){
        Copy-VerifiedPayload ([pscustomobject]@{source=$pair[0];destination=(Join-Path $targetRoot $pair[1]);sha256=$pair[2];length=(Get-Item -LiteralPath $pair[0]).Length})}
    Write-NewRuntimeReport (Join-Path $targetRoot 'install-before.json') $plan
    New-ProtectedDirectory (Join-Path $targetRoot 'build-temp')
    Invoke-FrozenRuntimeBuild
    Protect-NewRuntimeTree $targetRoot
    $verifiedEntries=Assert-CodeTreeOnce $targetRoot
    # Build backends may change their source tree. Bind the post-build bytes to
    # the reviewed manifest again before importing any freshly installed code.
    Assert-PostBuildFrozenFiles $files
    foreach($pair in @(@($candidate,'pipeline.json',$configHash),@($layout,'windows-layout.json',$pins[3][1]),@($Manifest,'source-manifest.json',$ManifestSha256))){
        $stream=Open-VerifiedFile (Join-Path $targetRoot $pair[1]) $pair[2] (Get-Item -LiteralPath $pair[0]).Length;$stream.Dispose()}
    $newPython=Join-Path $targetRoot '.venv\Scripts\python.exe'
    $validation=@'
import json,sys
from pathlib import Path
from cochem_pipeline.config import load_config
from cochem_pipeline.deployment_revision import verify_installed_revision
root=Path(sys.argv[1]); config=load_config(str(root/'pipeline.json'))
assert config.max_execution_slots==4 and len(config.workers)==6
assert config.ramdisk.workspace_subdirectory=='CoChem427-windows-20261007'
assert config.ramdisk.mount_root=='R:\\' and config.ramdisk.adopted_drive
result=verify_installed_revision(root/'source',Path(sys.executable).parent.parent/'Lib/site-packages',root/'source')
print(json.dumps({'revision':result,'configuration_parsed':True,'ram_workspace_root':str(config.ramdisk.workspace_root),'no_system_or_model_execution':True}))
'@
    $checked=& $newPython -I -B -c $validation $targetRoot
    if($LASTEXITCODE -ne 0){throw 'New revision/config validation failed. Preserve fresh root; old runtime remains selected.'}
    $verification=($checked -join "`n")|ConvertFrom-Json
    if($verification.revision.verified -ne $true -or $verification.configuration_parsed -ne $true){throw 'New revision did not produce valid verification metadata.'}
    Assert-StoppedRuntimeTasks $folder
    foreach($pin in @($pins[2],$pins[3])){$stream=Open-VerifiedFile $pin[0] $pin[1] (Get-Item -LiteralPath $pin[0]).Length;$stream.Dispose()}
    $plan.mode='FRESH_STOPPED_RUNTIME_READY';$plan.holds=@();$plan['verified_entries']=$verifiedEntries;$plan['verification']=$verification
    Write-NewRuntimeReport (Join-Path $targetRoot 'install-after.json') $plan
    $plan|ConvertTo-Json -Depth 8
}finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
