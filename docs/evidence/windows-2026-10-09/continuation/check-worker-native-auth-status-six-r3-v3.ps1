#Requires -Version 5.1
<# Read-only plan by default. -Apply is an explicit one-shot, non-inference
   native authentication status check after stopped provisioning and human login.
   No native command, task, or protected directory is created in plan mode. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('slot1','slot2','slot3','slot4','slot5','slot6')][string]$Slot,
    [Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Provider,
    [Parameter(Mandatory=$true)][ValidateSet('before','after')][string]$Stage,
    [switch]$Apply,
    [ValidatePattern('^[a-f0-9]{32}$')][string]$Attempt='00000000000000000000000000000000'
)
if($Attempt -cnotmatch '^[a-f0-9]{32}$' -or ($Apply -and $Attempt -ceq '00000000000000000000000000000000')){throw 'A lowercase32-hex Attempt is required; Apply forbids the all-zero preview attempt.'}
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Slot=$Slot.ToLowerInvariant();$Provider=$Provider.ToLowerInvariant();$Stage=$Stage.ToLowerInvariant()
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK') {throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if ($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')) {throw '-Apply requires the owner in Administrator Windows PowerShell; nothing was executed.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$targetRoot="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$Slot-$Provider-$Stage"
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$basePython=Join-Path $basePythonRoot 'python.exe'
$venvConfig=Join-Path $installRoot '.venv\pyvenv.cfg'
$layout=Join-Path $installRoot 'windows-layout.json'
$config=Join-Path $installRoot 'pipeline.json'
$native="C:\Program Files\CoChem\Native4.2.7-windows-20261006\$Provider.exe"
$nativeHash=if ($Provider -eq 'codex') {'fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d'} else {'0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469'}
$taskName="CoChem-4.2.7-NativeAuthStatusSix-20261008-r3-v3-$Attempt-$Slot-$Provider-$Stage"
$source=Join-Path $PSScriptRoot 'worker-native-auth-status-six-r3-v3.py'
$sourceHash='7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1'
$helper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
# Reuse only exact reviewed copy/ACL function definitions, never helper actions.
$stream=[IO.File]::Open($helper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
    if ($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b') {throw 'Reviewed protected-copy helper changed.'}
    $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
    if ($errors.Count) {throw 'Protected-copy helper parse error.'}
    $names=@('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')
    foreach ($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) {if ($function.Name -in $names) {. ([scriptblock]::Create($function.Extent.Text))}}
} finally {$stream.Dispose()}
Initialize-FileIdentity
function Read-R3Control {
    param([string]$Path,[string]$Hash='',[long]$Maximum=16777216)
    Assert-ProtectedPath $Path
    $length=(Get-Item -LiteralPath $Path -Force).Length
    if($length -lt 0 -or $length -gt $Maximum){throw 'Bounded r3 control length differs.'}
    if(-not $Hash){$Hash=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
    $stream=Open-VerifiedFile $Path $Hash $length;$held.Add($stream)
    [pscustomobject]@{Stream=$stream;Sha256=$Hash;Length=$length}
}

function Read-R3Text {
    param($Control)
    $Control.Stream.Position=0;$reader=[IO.StreamReader]::new($Control.Stream,[Text.Encoding]::UTF8,$true,4096,$true)
    try{$reader.ReadToEnd()}finally{$reader.Dispose()}
}

function Assert-R3InstalledBindings {
    $root='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
    $manifestPin='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionPin='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
    if($manifestPin -cnotmatch '^[a-f0-9]{64}$' -or $revisionPin -cnotmatch '^[a-f0-9]{64}$'){throw 'Reviewed r3 source freeze is pending.'}
    $configPin='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
    $afterControl=Read-R3Control (Join-Path $root 'install-after.json') '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' 1048576
    $after=(Read-R3Text $afterControl)|ConvertFrom-Json;$revision=$after.verification.revision
    if($after.schema -cne 'cochem-stopped-runtime-update/1' -or $after.mode -cne 'FRESH_STOPPED_RUNTIME_READY' -or
       $after.target_root -cne $root -or $after.source_manifest_sha256 -cne $manifestPin -or $after.configuration_sha256 -cne $configPin -or
       $after.previous_runtime_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2' -or
       $after.previous_install_receipt_sha256 -cne 'd92260ee2c0fc7df300c8aeafaa7cac4293e581d69f7a02bea98260b743244b4' -or
       $after.previous_source_manifest_sha256 -cne 'df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8' -or
       $after.configuration_unchanged -ne $true -or $null -ne $after.only_config_change -or $after.verified_entries -lt 10000 -or
       $after.preserved_denial_receipt_sha256 -cne '691c70560436f948464c529b9d2d6a3e33ce240749642af86ca79c748791461d' -or
       $after.preserved_denial_task_terminal_check_deferred -ne $false -or $after.preserved_worker_cleanup_verified -ne $false -or
       $after.source_files -ne 166 -or @($after.holds).Count -ne 0 -or $after.accounts_provisioned -ne 0 -or $after.tasks_changed -ne 0 -or
       $after.credentials_modified -ne $false -or $after.databases_modified -ne $false -or $after.ram_modified -ne $false -or
       $after.pipeline_started -ne $false -or $after.activation_ready -ne $false -or $after.verification.configuration_parsed -ne $true -or
       $after.verification.no_system_or_model_execution -ne $true -or $after.verification.ram_workspace_root -cne 'R:\CoChem427-windows-20261007' -or
       $revision.schema -cne 'cochem-installed-revision/1' -or $revision.verified -ne $true -or $revision.read_only -ne $true -or
       $revision.files -ne 109 -or $revision.source_sha256 -cne $revisionPin -or $revision.acceptance_files -ne 0 -or $null -ne $revision.acceptance_sha256){throw 'Successful r3 source/config/revision receipt bindings differ.'}
    if($after.runtime_bindings.pyvenv_sha256 -cne '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d' -or
       $after.runtime_bindings.venv_python_sha256 -cne '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e' -or
       $after.runtime_bindings.base_python_sha256 -cne 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' -or
       $after.runtime_bindings.base_root -cne 'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'){throw 'R3 runtime binding receipt differs.'}
    $manifest=(Read-R3Text (Read-R3Control (Join-Path $root 'source-manifest.json') $manifestPin))|ConvertFrom-Json
    if($manifest.schema -cne 'cochem-stopped-runtime-source/1' -or $manifest.target_root -cne $root -or $manifest.complete_source_freeze -ne $true -or @($manifest.files).Count -ne 166){throw 'Frozen r3 manifest binding differs.'}
    $null=Read-R3Control (Join-Path $root 'pipeline.json') $configPin
    $null=Read-R3Control (Join-Path $root 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
    $null=Read-R3Control (Join-Path $root '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'
    $null=Read-R3Control (Join-Path $root '.venv\Scripts\python.exe') '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'
    $null=Read-R3Control (Join-Path $root '.venv\Lib\site-packages\cochem_pipeline\resource_limits.py') 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f'
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase);$assets=0
    foreach($row in $manifest.files){
        $key=[string]$row.relative
        if($key -match '[\\:]' -or $key.StartsWith('/') -or @($key.Split('/')|Where-Object{$_ -in @('','.','..')}).Count -or
           ($key -cnotmatch '^src/' -and $key -cnotin @('README.md','pyproject.toml','uv.lock')) -or -not $seen.Add($key) -or
           $row.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $row.length -lt 0 -or $row.length -gt 16777216){throw 'Unsafe frozen r3 source row.'}
        $control=Read-R3Control (Join-Path (Join-Path $root 'source') $key) $row.sha256
        if($control.Length -ne $row.length){throw 'Source length differs.'}
        if($key -cmatch '^src/(cochem_pipeline|cochem_mcp|cochem_supervisor)/.+\.(py|md|json|xml)$'){
            $control=Read-R3Control (Join-Path (Join-Path $root '.venv\Lib\site-packages') $key.Substring(4)) $row.sha256
            if($control.Length -ne $row.length){throw 'Installed asset differs from manifest.'};$assets++
        }
    }
    if($assets -ne 109){throw 'Installed package inventory differs.'}
    [ordered]@{install_receipt_sha256=$afterControl.Sha256;source_manifest_sha256=$manifestPin;configuration_sha256=$configPin;revision=$revision;source_files_verified=166}
}


function Add-AuthStatusFailureMetadata {
 param($Summary,$Receipt)
 if($null -ne $Receipt.PSObject.Properties['failure']){
  $f=$Receipt.failure
  if($f.phase -cnotin @('trusted_preflight','reservation','runtime_custody','layout','boundaries','native_custody','native_launch','native_attestation','native_wait','native_cleanup','native_result','daemon_postcheck') -or
     $f.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$' -or
     ($null -ne $f.winerror -and (($f.winerror -isnot [int] -and $f.winerror -isnot [long]) -or $f.winerror -lt 0 -or $f.winerror -gt 4294967295))){throw 'Invalid sanitized authentication failure metadata.'}
  $Summary.failure=[ordered]@{phase=$f.phase;error_type=$f.error_type;winerror=$f.winerror}
 }
 if($null -ne $Receipt.PSObject.Properties['native_exit_code']){
  if(($Receipt.native_exit_code -isnot [int] -and $Receipt.native_exit_code -isnot [long]) -or $Receipt.native_exit_code -lt -2147483648 -or $Receipt.native_exit_code -gt 4294967295){throw 'Invalid native exit metadata.'}
  $Summary.native_exit_code=$Receipt.native_exit_code
 }
}
function Assert-AuthStatusReceipt {
 param($Value,$Task,$Runtime,[string]$Slot,[string]$Provider,[string]$Stage,[string]$Nonce,[string]$SourceHash,[string]$Attempt)
 if($Value.attempt -cne $Attempt -or $Value.schema -cne 'cochem-worker-native-auth-status/1' -or $Value.nonce -cne $Nonce -or $Value.stage -cne $Stage -or $Value.slot -cne $Slot -or $Value.provider -cne $Provider -or $Value.system_sid -cne 'S-1-5-18' -or $Value.helper_sha256 -cne $SourceHash -or
    $Value.runtime_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3' -or $Value.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or $Value.source_manifest_sha256 -cne $Runtime.source_manifest_sha256 -or $Value.resource_limits_sha256 -cne 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f'){throw 'Status receipt identity/runtime binding differs.'}
 if($Value.status -ceq 'STATUS_CHECK_FAILED'){return 'HOLD'}
 if($Task.LastTaskResult -ne 0 -or $Value.config_sha256 -cne $Runtime.configuration_sha256 -or $Value.layout_sha256 -cne '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4' -or $Value.revision.verified -ne $true -or $Value.revision.source_sha256 -cne $Runtime.revision.source_sha256){throw 'Status receipt configuration/result binding differs.'}
 foreach($key in @('cleanup_verified','runtime_custody_verified','daemon_states_verified_before_and_after')){if($Value.$key -isnot [bool] -or $Value.$key -ne $true){throw 'Status receipt lacks verified physical custody/cleanup.'}}
 foreach($key in @('native_model_jobs_executed','login_commands_executed')){if(($Value.$key -isnot [int] -and $Value.$key -isnot [long]) -or $Value.$key -ne 0){throw 'Out-of-scope native execution was reported.'}}
 foreach($key in @('token_matches_selected_worker','image_matches_reviewed_executable','owned_job_membership_verified')){if($Value.process.$key -isnot [bool] -or $Value.process.$key -ne $true){throw 'Native status process identity was not verified.'}}
 $nativePin=if($Provider -ceq 'codex'){'fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d'}else{'0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469'}
 if($Value.native_executable_sha256_before -cne $nativePin -or $Value.native_executable_sha256_after -cne $nativePin -or $Value.native_status_commands_executed -ne 1){throw 'Native status command/executable binding differs.'}
 $a=$Value.authentication
 foreach($key in @('protocol_valid','logged_in','native_subscription_authentication_verified')){if($a.$key -isnot [bool]){throw 'Native status classification has invalid field types.'}}
 $result='HOLD';$expectedAuthKind=if($Provider -ceq 'codex'){'chatgpt'}else{'claude.ai'}
 if($a.protocol_valid -and $a.logged_in -and $a.native_subscription_authentication_verified -and $a.auth_kind -ceq $expectedAuthKind -and $Value.status -ceq 'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED'){$result='REUSE_VERIFIED_SESSION'}
 elseif($a.protocol_valid -and -not $a.logged_in -and -not $a.native_subscription_authentication_verified -and $a.auth_kind -ceq 'none' -and $Value.status -ceq 'AUTHENTICATION_NOT_VERIFIED'){$result='ATTENDED_LOGIN_REQUIRED'}
 if($Value.decision -cne $result){throw 'Native status decision contradicts retained evidence.'}
 return $result
}

$held=[Collections.Generic.List[IO.FileStream]]::new()
try {
$runtime=Assert-R3InstalledBindings
$sourceLength=(Get-Item -LiteralPath $source).Length
$stream=Open-VerifiedFile $source $sourceHash $sourceLength;$held.Add($stream)
$holds=@()
foreach ($path in @($python,$layout,$config,$native,$basePython,$venvConfig)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {$holds+='A required stopped installation file is absent: '+$path}
    else {Assert-ProtectedPath $path}
}
if (Test-Path -LiteralPath $native -PathType Leaf) {
    $stream=Open-VerifiedFile $native $nativeHash (Get-Item -LiteralPath $native).Length;$stream.Dispose()
}
if (Test-Path -LiteralPath $targetRoot) {$holds+='This one-shot status root already exists; preserve its evidence, do not overwrite or rerun automatically.'}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
function Get-ExactTaskOrAbsent {
    param([string]$Name)
    try {return $folder.GetTask($Name)} catch {
        $exception=$_.Exception
        while ($null -ne $exception) {if ($exception.HResult -eq -2147024894) {return $null};$exception=$exception.InnerException}
        throw 'Cannot establish exact Task Scheduler state; no native check is authorized by uncertainty.'
    }
}
if ($null -ne (Get-ExactTaskOrAbsent $taskName)) {$holds+='This one-shot status task already exists; preserve it.'}
foreach ($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')) {
    $daemon=Get-ExactTaskOrAbsent $name
    if ($null -ne $daemon -and ($daemon.Enabled -or $daemon.State -notin @(1,3) -or $daemon.GetInstances(0).Count -ne 0)) {$holds+='Protected daemons must remain stopped and disabled: '+$name}
}
function Assert-VenvBinding {
    param([string]$Text)
    $values=@{}
    foreach($line in ($Text -split '\r?\n')){
        if(-not $line.Trim()){continue}
        if($line -notmatch '^([^=]+?)\s*=\s*(.*)$'){throw 'Unexpected pyvenv record.'}
        $key=$matches[1].Trim()
        if($values.ContainsKey($key)){throw 'Duplicate pyvenv key.'}
        $values[$key]=$matches[2].Trim()
    }
    $expected=@{home=$basePythonRoot;implementation='CPython';uv='0.12.17';version_info='3.12.13';'include-system-site-packages'='false';prompt='cochem'}
    if($values.Count -ne $expected.Count){throw 'Expected the six-key uv venv format.'}
    foreach($key in $expected.Keys){if(-not $values.ContainsKey($key) -or $values[$key] -cne $expected[$key]){throw 'Venv interpreter/policy differs from the reviewed protected toolchain.'}}
}
foreach($pin in @(@($python,'560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'),@($basePython,'d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'),@($venvConfig,'0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))){
    if(Test-Path -LiteralPath $pin[0] -PathType Leaf){$stream=Open-VerifiedFile $pin[0] $pin[1] (Get-Item -LiteralPath $pin[0]).Length;try{if($pin[0] -eq $venvConfig){$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);Assert-VenvBinding $reader.ReadToEnd()}}finally{$stream.Dispose()}}
}
$plan=[ordered]@{runtime_root=$installRoot;install_receipt_sha256=$runtime.install_receipt_sha256;source_manifest_sha256=$runtime.source_manifest_sha256;revision_sha256=$runtime.revision.source_sha256;schema='cochem-worker-native-auth-status-plan/1';attempt=$Attempt;stage=$Stage;mode='READ_ONLY_PLAN';slot=$Slot;provider=$Provider;owner=$identity.Name;administrator=$admin;source_sha256=$sourceHash;native_sha256=$nativeHash;task_name=$taskName;target_root=$targetRoot;native_status_timeout_seconds=30;native_output_limit_bytes=65536;native_status_commands_executed=0;native_model_jobs_executed=0;logins_executed=0;pipeline_started=$false;holds=$holds}
if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;return}
if ($holds.Count) {throw ($holds -join ' ')}
function Assert-CodeTreeOnce {
    param([string]$Path)
    # Root ancestry is checked once. Protected parents cannot be replaced by an
    # untrusted writer; each child still receives its own ACL and handle check.
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
            # Handle metadata only, including bundled .db resources; no content read.
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()}
        }
    }
    $count
}
$installedEntries=Assert-CodeTreeOnce $installRoot
$baseEntries=Assert-CodeTreeOnce $basePythonRoot
$nativeEntries=Assert-CodeTreeOnce (Split-Path -Parent $native)
New-ProtectedDirectory $targetRoot
$destination=Join-Path $targetRoot 'worker-native-auth-status-six-r3-v3.py'
Copy-VerifiedPayload ([pscustomobject]@{source=$source;destination=$destination;sha256=$sourceHash;length=$sourceLength})
$held.Add((Open-VerifiedFile $destination $sourceHash $sourceLength))
$nonce=[Guid]::NewGuid().ToString('N')
$definition=$scheduler.NewTask(0)
$definition.RegistrationInfo.Description='One bounded native authentication-status command in one isolated worker profile. No login, model inference, daemon activation, credential copying or budget changes.'
$definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
$definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true
$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT3M'
$action=$definition.Actions.Create(0);$action.Path=$python
$action.Arguments='-I -B "'+$destination+'" --slot '+$Slot+' --provider '+$Provider+' --nonce '+$nonce+' --stage '+$Stage+' --attempt '+$Attempt
$action.WorkingDirectory=$targetRoot
$task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
function Wait-StatusTaskInstance {
    param($Instance,[int]$TimeoutSeconds=175)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        try {$Instance.Refresh()} catch {
            $completed=$false;$exception=$_.Exception
            while ($null -ne $exception) {if ($exception.HResult -eq -2147216629) {$completed=$true;break};$exception=$exception.InnerException}
            # SCHED_E_TASK_NOT_RUNNING ends polling only; it never proves success.
            if ($completed) {return}
            throw
        }
        if ([DateTime]::UtcNow -gt $deadline) {throw 'Status task wait timed out. Preserve task/root; its limit is three minutes. Cleanup is unverified: keep all pipeline tasks disabled and do not retry automatically.'}
    }while($Instance.State -in @(2,4))
}
$instance=$task.Run($null)
Wait-StatusTaskInstance $instance
function Assert-CompletedStatusTask {
    param($Task)
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0){throw 'Status task is not conclusively terminal; preserve root/task and refuse its receipt.'}
}
Assert-CompletedStatusTask $task
$receiptPath=Join-Path $targetRoot 'worker-native-status.json'
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {throw ('Native status check has no receipt. Preserve task and keep daemons disabled. LastTaskResult='+$task.LastTaskResult)}
Assert-ProtectedPath $receiptPath
if ((Get-Item -LiteralPath $receiptPath).Length -gt 32768) {throw 'Native status receipt exceeds its bound.'}
$receiptControl=Read-R3Control $receiptPath '' 32768
$receipt=(Read-R3Text $receiptControl)|ConvertFrom-Json
$decision=Assert-AuthStatusReceipt $receipt $task $runtime $Slot $Provider $Stage $nonce $sourceHash $Attempt
$summary=[ordered]@{schema='cochem-worker-native-auth-status-task-result/1';attempt=$Attempt;runtime_root=$installRoot;install_receipt_sha256=$runtime.install_receipt_sha256;source_manifest_sha256=$runtime.source_manifest_sha256;slot=$Slot;provider=$Provider;stage=$Stage;nonce=$nonce;decision=$decision;receipt_path=$receiptPath;receipt_sha256=$receiptControl.Sha256;status=$receipt.status;cleanup_verified=$receipt.cleanup_verified;last_task_result=$task.LastTaskResult;task_preserved=$true;native_model_jobs_executed=0;login_commands_executed=0;serving_model_verified=$false;ready_for_inference=$false;pipeline_started=$false}
Add-AuthStatusFailureMetadata $summary $receipt
if($decision -ceq 'HOLD'){
 Write-Host ('COCHEM_NATIVE_AUTH_STATUS_RESULT '+($summary|ConvertTo-Json -Depth 5 -Compress))
 throw 'Status is not an established subscription or explicit logged-out state. Preserve evidence; no browser login or automatic retry is authorized.'
}
$summary|ConvertTo-Json -Depth 5

}finally{foreach($stream in $held){$stream.Dispose()}}
