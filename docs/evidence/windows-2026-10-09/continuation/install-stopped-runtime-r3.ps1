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
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
        $tokens=$null;$errors=$null;try{$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)}finally{$reader.Dispose()}
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
       $Inventory.previous_source_manifest_sha256 -ne 'df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'){
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
    foreach($required in @('README.md','pyproject.toml','uv.lock','src/cochem_pipeline/resource_limits.py','src/cochem_pipeline/ramdisk.py','src/cochem_pipeline/deployment.py','src/cochem_pipeline/config.py','src/cochem_pipeline/deployment_revision.py','src/cochem_supervisor/engine.py')){
        if(-not $seen.Contains($required)){throw 'Source inventory omits a required frozen file.'}}
}

function Assert-ReviewedR3Delta {
    param($Inventory,$Previous)
    $rows=@($Inventory.files);$prior=@($Previous.files)
    if($rows.Count -ne 166 -or $prior.Count -ne 166){throw 'The r3 snapshot must retain all 166 r2 source assets.'}
    $byName=@{};foreach($row in $prior){$byName[[string]$row.relative]=$row}
    $changed=@()
    foreach($row in $rows){
        if(-not $byName.ContainsKey([string]$row.relative)){throw 'A source asset was added or removed outside the reviewed repair.'}
        $before=$byName[[string]$row.relative]
        if($row.sha256 -cne $before.sha256 -or [long]$row.length -ne [long]$before.length){$changed+=[string]$row.relative}
    }
    if($changed.Count -ne 1 -or $changed[0] -cne 'src/cochem_pipeline/resource_limits.py' -or
       @($rows|Where-Object {$_.relative -ceq $changed[0]})[0].sha256 -cne 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f'){
        throw 'Only the reviewed Windows Job Object repair may differ from r2.'}
}

function Assert-ExactPreservedConfig {
    param([string]$Original,[string]$Candidate)
    if($Original -cne $Candidate){throw 'The r3 configuration must remain byte-for-byte equivalent to the pinned r2 configuration.'}
    $config=$Candidate|ConvertFrom-Json
    if($config.max_execution_slots -ne 4 -or @($config.workers.PSObject.Properties.Name).Count -ne 6 -or
       $config.ramdisk.workspace_subdirectory -cne 'CoChem427-windows-20261007' -or
       $config.ramdisk.mount_root -cne 'R:\') {throw 'Preserved configuration has unexpected capacity or RAM workspace.'}
}

function Assert-PreservedDenialTask {
    param($Folder)
    # Absence, access-denied and every other lookup error are failures here.
    # This reads the original failed task; it never runs, stops or changes it.
    $task=$Folder.GetTask('CoChem-4.2.7-WorkerDenial-slot1');$definition=$task.Definition
    $root='C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261006-slot1'
    if($task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0 -or $task.LastTaskResult -ne 2 -or
       $definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $definition.Principal.LogonType -ne 5 -or
       $definition.Principal.RunLevel -ne 1 -or $definition.Triggers.Count -ne 0 -or $definition.Actions.Count -ne 1){
        throw 'Original slot1 denial task must retain its exact terminal failed SYSTEM definition.'}
    $action=$definition.Actions.Item(1)
    if($action.Type -ne 0 -or $action.Path -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Scripts\python.exe' -or
       $action.Arguments -cne ('-I -B "'+$root+'\worker-denial-acceptance.py" --slot slot1 --nonce 06478e2f265546cd8257aa09111373f4') -or
       $action.WorkingDirectory -cne $root){throw 'Original failed slot1 task action differs; preserve it for review.'}
}

function Read-HeldText {
    param([IO.Stream]$Stream)
    $Stream.Position=0;$reader=[IO.StreamReader]::new($Stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
    try{$reader.ReadToEnd()}finally{$reader.Dispose();$Stream.Position=0}
}

function Assert-UvVenvText {
    param([string]$Text)
    $values=@{}
    foreach($line in ($Text -split "`r?`n")){
        if(-not $line.Trim()){continue}
        if($line -notmatch '^([^=]+?)\s*=\s*(.*)$'){throw 'Unexpected pyvenv record.'}
        $key=$matches[1].Trim()
        if($values.ContainsKey($key)){throw 'Duplicate pyvenv key.'}
        $values[$key]=$matches[2].Trim()
    }
    $expected=@{home=(Split-Path -Parent $script:python);implementation='CPython';uv='0.12.17';version_info='3.12.13';'include-system-site-packages'='false';prompt='cochem'}
    if($values.Count -ne $expected.Count){throw 'Expected the six-key uv venv format.'}
    foreach($key in $expected.Keys){if(-not $values.ContainsKey($key) -or $values[$key] -cne $expected[$key]){throw 'Venv interpreter/policy differs from the reviewed protected toolchain.'}}
}

function Assert-NewRuntimeInterpreter {
    param([string]$Root)
    $cfg=Join-Path $Root '.venv\pyvenv.cfg';$exe=Join-Path $Root '.venv\Scripts\python.exe'
    $cfgHash='0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'
    $exeHash='560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'
    $cfgStream=Open-VerifiedFile $cfg $cfgHash (Get-Item -LiteralPath $cfg).Length
    try{Assert-UvVenvText (Read-HeldText $cfgStream)}finally{$cfgStream.Dispose()}
    $exeStream=Open-VerifiedFile $exe $exeHash (Get-Item -LiteralPath $exe).Length;$exeStream.Dispose()
    [pscustomobject]@{pyvenv_sha256=$cfgHash;venv_python_sha256=$exeHash;base_python_sha256='d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa';base_root=(Split-Path -Parent $script:python)}
}

function New-ControlCopyRecords {
    param([string]$ManifestPath,[string]$ManifestHash)
    # Typed records avoid PowerShell 5.1 flattening nested singleton arrays.
    @(
        [pscustomobject]@{source=$script:oldConfig;destination=(Join-Path $script:targetRoot 'pipeline.json');sha256=$script:configHash;length=(Get-Item -LiteralPath $script:oldConfig).Length}
        [pscustomobject]@{source=$script:layout;destination=(Join-Path $script:targetRoot 'windows-layout.json');sha256=$script:layoutHash;length=(Get-Item -LiteralPath $script:layout).Length}
        [pscustomobject]@{source=$ManifestPath;destination=(Join-Path $script:targetRoot 'source-manifest.json');sha256=$ManifestHash;length=(Get-Item -LiteralPath $ManifestPath).Length}
    )
}

function Assert-PriorReceipts {
    param($Installation,$Denial)
    if($Installation.schema -ne 'cochem-stopped-runtime-update/1' -or $Installation.mode -ne 'FRESH_STOPPED_RUNTIME_READY' -or
       $Installation.target_root -cne $script:oldRoot -or $Installation.source_manifest_sha256 -ne $script:priorManifestHash -or
       $Installation.configuration_sha256 -ne $script:configHash -or $Installation.source_files -ne 166 -or
       $Installation.verification.revision.verified -ne $true -or $Installation.verification.revision.files -ne 109 -or
       $Installation.verification.revision.source_sha256 -ne '064a2c9c18b1adb489d0514b1b0e2979e80e72b6af33b2bdc9d95211c0a61f5d'){
        throw 'The preserved r2 installation receipt differs.'}
    if($Denial.schema -ne 'cochem-worker-denial-acceptance/1' -or $Denial.status -ne 'DENIAL_CHECK_FAILED' -or
       $Denial.nonce -ne '06478e2f265546cd8257aa09111373f4' -or $Denial.slot -ne 'slot1' -or $Denial.system_sid -ne 'S-1-5-18' -or
       $Denial.failure.phase -ne 'worker_launch' -or $Denial.failure.error_type -ne 'ResourcePolicyError' -or
       $null -ne $Denial.failure.winerror -or $Denial.worker_released -ne $false -or $Denial.cleanup_verified -ne $false){
        throw 'Original failed denial receipt differs; do not treat it as accepted worker evidence.'}
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

function Invoke-NewRuntimeValidation {
    param([string]$Root)
    $newPython=Join-Path $Root '.venv\Scripts\python.exe'
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
    $checked=& $newPython -I -B -c $validation $Root
    if($LASTEXITCODE -ne 0){throw 'New revision/config validation failed. Preserve fresh root; old runtime remains selected.'}
    $verification=($checked -join "`n")|ConvertFrom-Json
    if($verification.revision.verified -ne $true -or $verification.configuration_parsed -ne $true){throw 'New revision did not produce valid verification metadata.'}
    return $verification
}

function Invoke-NewRuntimeInstallation {
    param([object[]]$Files,[object[]]$ControlFiles,$Folder,$Plan)
$mutex=[Threading.Mutex]::new($false,$script:setupMutexName);$locked=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another code-only revision update owns the setup mutex.'}
    Assert-StoppedRuntimeTasks $folder
    Assert-PreservedDenialTask $folder
    if(Test-Path -LiteralPath $targetRoot){throw 'Fresh revision root appeared; preserve it.'}
    $null=Assert-CodeTreeOnce (Split-Path -Parent $python);Assert-ProtectedPath $uv
    $null=Assert-CodeTreeOnce $oldRoot
    New-ProtectedDirectory $targetRoot;New-ProtectedDirectory $sourceRoot
    $directories=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach($file in $files){$parent=Split-Path -Parent $file.destination;while($parent -ne $sourceRoot){$null=$directories.Add($parent);$parent=Split-Path -Parent $parent}}
    foreach($directory in @($directories|Sort-Object Length)){New-ProtectedDirectory $directory}
    foreach($file in $files){Copy-VerifiedPayload $file}
    foreach($file in $controlFiles){Copy-VerifiedPayload $file}
    Write-NewRuntimeReport (Join-Path $targetRoot 'install-before.json') $plan
    New-ProtectedDirectory (Join-Path $targetRoot 'build-temp')
    Invoke-FrozenRuntimeBuild
    Protect-NewRuntimeTree $targetRoot
    $verifiedEntries=Assert-CodeTreeOnce $targetRoot
    # Build backends may change their source tree. Bind the post-build bytes to
    # the reviewed manifest again before importing any freshly installed code.
    Assert-PostBuildFrozenFiles $files
    Assert-PostBuildFrozenFiles $controlFiles
    $runtimeBindings=Assert-NewRuntimeInterpreter $targetRoot
    $verification=Invoke-NewRuntimeValidation $targetRoot
    Assert-StoppedRuntimeTasks $folder
    Assert-PreservedDenialTask $folder
    foreach($pin in $pins){$stream=Open-VerifiedFile $pin.source $pin.sha256 (Get-Item -LiteralPath $pin.source).Length;$stream.Dispose()}
    $plan.mode='FRESH_STOPPED_RUNTIME_READY';$plan.preserved_denial_task_terminal_check_deferred=$false;$plan.holds=@();$plan['verified_entries']=$verifiedEntries;$plan['verification']=$verification;$plan['runtime_bindings']=$runtimeBindings
    Write-NewRuntimeReport (Join-Path $targetRoot 'install-after.json') $plan
    $plan|ConvertTo-Json -Depth 8
}finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}

}

# Invocation boundary. Tests extract actual functions only; no installation runs.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$setupMutexName='Global\CoChem427-CodeOnlyRuntimeUpdate'
$programFiles='C:\Program Files';$base=Join-Path $programFiles 'CoChem'
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$oldRoot=Join-Path $base 'Pipeline4.2.7-windows-20261007-r2'
$targetRoot=Join-Path $base 'Pipeline4.2.7-windows-20261007-r3';$sourceRoot=Join-Path $targetRoot 'source'
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
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$layoutHash='8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
$priorManifestHash='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'
$priorInstallHash='d92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4'
$denialHash='691c70560436f948464c529b9d2d6a3e33ce240749642af86ca79c748791461d'
$denialPath='C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261006-slot1\worker-denial-acceptance.json'
$pins=@(
    [pscustomobject]@{source=$python;sha256='d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'}
    [pscustomobject]@{source=$uv;sha256='2019cdf564cb8f749262f5f021cedc75a99abb1c6081227ca340bbcda972611d'}
    [pscustomobject]@{source=$oldConfig;sha256=$configHash}
    [pscustomobject]@{source=$layout;sha256=$layoutHash}
    [pscustomobject]@{source=(Join-Path $oldRoot 'source-manifest.json');sha256=$priorManifestHash}
    [pscustomobject]@{source=(Join-Path $oldRoot 'install-after.json');sha256=$priorInstallHash}
    [pscustomobject]@{source=$denialPath;sha256=$denialHash}
)
$held=[Collections.Generic.List[IO.Stream]]::new()
try{
    foreach($pin in $pins){$stream=Open-VerifiedFile $pin.source $pin.sha256 (Get-Item -LiteralPath $pin.source).Length;$held.Add($stream)}
    $configText=Read-HeldText $held[2];Assert-ExactPreservedConfig $configText $configText
    Assert-PriorReceipts ((Read-HeldText $held[5])|ConvertFrom-Json) ((Read-HeldText $held[6])|ConvertFrom-Json)
    if($inventory){Assert-ReviewedR3Delta $inventory ((Read-HeldText $held[4])|ConvertFrom-Json)}
    $controlFiles=@()
    if($Manifest){$controlFiles=@(New-ControlCopyRecords $Manifest $ManifestSha256);foreach($file in $controlFiles){$stream=Open-VerifiedFile $file.source $file.sha256 $file.length;$held.Add($stream)}}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-StoppedRuntimeTasks $folder
    $priorTaskCheckDeferred=$false
    if($admin){Assert-PreservedDenialTask $folder}else{$priorTaskCheckDeferred=$true}
    $plan=[ordered]@{schema='cochem-stopped-runtime-update/1';mode='READ_ONLY_PLAN';target_root=$targetRoot;previous_runtime_root=$oldRoot;previous_install_receipt_sha256=$priorInstallHash;previous_source_manifest_sha256=$priorManifestHash;source_manifest_sha256=$ManifestSha256;source_files=$files.Count;configuration_sha256=$configHash;configuration_unchanged=$true;only_config_change=$null;preserved_denial_receipt_sha256=$denialHash;preserved_denial_task_terminal_check_deferred=$priorTaskCheckDeferred;preserved_worker_cleanup_verified=$false;accounts_provisioned=0;tasks_changed=0;credentials_modified=$false;databases_modified=$false;ram_modified=$false;pipeline_started=$false;activation_ready=$false;holds=$holds}
    if(-not $Apply){$plan|ConvertTo-Json -Depth 5;return}
    if($holds.Count){throw ($holds -join ' ')}
    Invoke-NewRuntimeInstallation $files $controlFiles $folder $plan
}finally{foreach($stream in $held){$stream.Dispose()}}
