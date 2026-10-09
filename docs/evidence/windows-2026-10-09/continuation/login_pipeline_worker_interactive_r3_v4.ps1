#Requires -Version 5.1
<# Default is an inert plan. -Apply -Interactive requires the owner at a real
   elevated console. This is a human Claude subscription-login flow, never an
   unattended login or an inference readiness claim. Every session is preserved. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('slot1','slot2','slot3','slot4','slot5','slot6')][string]$Slot,[switch]$Apply,[switch]$Interactive)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($name in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$name\$name.psd1") -ErrorAction Stop}
$Slot=$Slot.ToLowerInvariant()
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if($Apply -and (-not $Interactive -or -not $admin -or $identity.Name -cne 'AETHERDESK\ansac' -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or $Host.Name -ne 'ConsoleHost')){throw 'Apply requires the owner at an elevated interactive ConsoleHost, with -Interactive and no redirected input/output.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$basePython=Join-Path $basePythonRoot 'python.exe'
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$venvConfig=Join-Path $installRoot '.venv\pyvenv.cfg'
$nativeRoot='C:\Program Files\CoChem\Native4.2.7-windows-20261006'
$source=Join-Path $PSScriptRoot 'worker_claude_login_bridge_r3.py'
$sourceHash='b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'
$external='C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next'
$support=Join-Path $external 'worker-native-status-r3.py'
$supportHash='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'
$held=[Collections.Generic.List[IO.FileStream]]::new()
function Import-PinnedFunctions {
    param([string]$Path,[string]$Expected,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create();try{$digest=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($digest -cne $Expected){throw 'Reviewed custody helper changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed custody helper does not parse.'}
        $found=@()
        foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($f.Name -in $Names){$found+=@($f.Name);$f.Extent.Text}}
        if(@($Names|Where-Object{$_ -notin $found}).Count){throw 'Required custody function is absent.'}
    }finally{$stream.Dispose()}
}
function New-PrivateAcl {
    param([switch]$Directory)
    if($Directory){$acl=[Security.AccessControl.DirectorySecurity]::new()}else{$acl=[Security.AccessControl.FileSecurity]::new()}
    $acl.SetAccessRuleProtection($true,$false);$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){
        if($Directory){$rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow')}
        else{$rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow')}
        $acl.AddAccessRule($rule)
    };$acl
}
function Write-PrivatePacket {
    param([string]$Path,[string]$Nonce,[Security.SecureString]$Secret)
    $ptr=[IntPtr]::Zero;$chars=$null;$bytes=$null;$stream=$null
    try{
        if($Nonce -cnotmatch '^[a-f0-9]{32}$' -or $Secret.Length -lt 1 -or $Secret.Length -gt 2048){throw 'One bounded input line is required.'}
        $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
        $chars=[char[]]::new($Secret.Length)
        for($n=0;$n -lt $chars.Length;$n++){$chars[$n]=[char]([Runtime.InteropServices.Marshal]::ReadInt16($ptr,2*$n) -band 0xffff);if($chars[$n] -in @([char]0,[char]10,[char]13)){throw 'Input contains a forbidden delimiter.'}}
        $encoding=[Text.UTF8Encoding]::new($false,$true);$bytes=$encoding.GetBytes($chars)
        if($bytes.Length -gt 2048){throw 'Input exceeds the 2048-byte bound.'}
        $prefix=[Text.Encoding]::ASCII.GetBytes("cochem-login-input/1`n$Nonce`n")
        # Final SYSTEM/Admin-only DACL is applied by CreateNew before first byte.
        $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-PrivateAcl))
        $stream.Write($prefix,0,$prefix.Length);$stream.Write($bytes,0,$bytes.Length);$stream.WriteByte(10);$stream.Flush($true)
    }finally{
        if($stream){$stream.Dispose()};if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
        if($null -ne $chars){[Array]::Clear($chars,0,$chars.Length)};if($null -ne $bytes){[Array]::Clear($bytes,0,$bytes.Length)}
    }
}
function Write-CancelRequest {
    param([string]$Root,[string]$Nonce)
    $cancel=Join-Path $Root 'cancel.request'
    if(-not (Test-Path -LiteralPath $cancel)){
        $secret=[Security.SecureString]::new();try{foreach($char in 'CANCEL'.ToCharArray()){$secret.AppendChar($char)};Write-PrivatePacket $cancel $Nonce $secret}finally{$secret.Dispose()}
    }
}
function Get-ExactTaskOrAbsent {
    param($Folder,[string]$Name)
    try{return $Folder.GetTask($Name)}catch{
        $errorValue=$_.Exception;while($null -ne $errorValue){if($errorValue.HResult -eq -2147024894){return $null};$errorValue=$errorValue.InnerException}
        throw 'Cannot establish exact task state; preserve all existing evidence.'
    }
}
function Assert-TaskTerminal {
    param($Task)
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0){throw 'Login task is not conclusively terminal; cleanup remains unverified.'}
}
function Test-InstanceComplete {
    param($Instance)
    try{$Instance.Refresh();return ($Instance.State -notin @(2,4))}catch{
        $errorValue=$_.Exception;while($null -ne $errorValue){if($errorValue.HResult -eq -2147216629){return $true};$errorValue=$errorValue.InnerException};throw
    }
}
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
foreach($definition in @(Import-PinnedFunctions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($definition))}
foreach($definition in @(Import-PinnedFunctions (Join-Path $external 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Assert-VenvBinding','Read-R3Control','Read-R3Text','Assert-R3InstalledBindings'))){. ([scriptblock]::Create($definition))}
 foreach($d in @(Import-PinnedFunctions (Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1') '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d' @('Assert-CodeTreeOnce'))){. ([scriptblock]::Create($d))}
Initialize-FileIdentity
$inspectionPath=Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1'
$held.Add((Open-VerifiedFile $inspectionPath '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d' (Get-Item -LiteralPath $inspectionPath).Length))
try{
    $runtime=Assert-R3InstalledBindings
    foreach($pair in @(@($source,$sourceHash),@($support,$supportHash),@($python,'560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'),@($basePython,'d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'),@($venvConfig,'0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'),@((Join-Path $installRoot 'windows-layout.json'),'8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'),@((Join-Path $installRoot 'pipeline.json'),'135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'),@((Join-Path $nativeRoot 'claude.exe'),'0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469'))){
        if($pair[0] -notin @($source,$support)){Assert-ProtectedPath $pair[0]}
        $stream=Open-VerifiedFile $pair[0] $pair[1] (Get-Item -LiteralPath $pair[0]).Length;$held.Add($stream)
        if($pair[0] -eq $venvConfig){$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{Assert-VenvBinding $reader.ReadToEnd()}finally{$reader.Dispose()}}
    }
    $holds=@();$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
        $daemon=Get-ExactTaskOrAbsent $folder $name
        if($null -ne $daemon -and ($daemon.Enabled -or $daemon.State -notin @(1,3) -or $daemon.GetInstances(0).Count -ne 0)){$holds+='Protected daemons must remain stopped and disabled.'}
    }
    $previous=@(Get-ChildItem -LiteralPath 'C:\Program Files\CoChem' -Directory -Filter "InteractiveClaude427-r3-$Slot-*" -ErrorAction Stop)
    $previous+=@(Get-ChildItem -LiteralPath 'C:\Program Files\CoChem' -Directory -Filter "InteractiveClaude427-$Slot-*" -ErrorAction Stop)
    if($previous.Count){$holds+='An earlier login session exists for this slot; preserve and review it before another login.'}
    if(-not $Apply){[ordered]@{runtime_root=$installRoot;install_receipt_sha256=$runtime.install_receipt_sha256;source_manifest_sha256=$runtime.source_manifest_sha256;revision_sha256=$runtime.revision.source_sha256;schema='cochem-interactive-claude-login-plan/1';mode='READ_ONLY_PLAN';slot=$Slot;source_sha256=$sourceHash;support_sha256=$supportHash;human_console_required=$true;maximum_input_lines=1;maximum_input_bytes=2048;native_output_suppressed_after_input=$true;login_commands_executed=0;model_jobs_executed=0;activation_ready=$false;holds=$holds}|ConvertTo-Json -Depth 4;return}
    if($holds.Count){throw ($holds -join ' ')}
    $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce $basePythonRoot;$null=Assert-CodeTreeOnce $nativeRoot
    $nonce=[Guid]::NewGuid().ToString('N');$root="C:\Program Files\CoChem\InteractiveClaude427-r3-$Slot-$nonce";$taskName="CoChem-4.2.7-InteractiveClaude-r3-$Slot-$nonce"
    if(Test-Path -LiteralPath $root){throw 'Fresh login root unexpectedly exists.'}
    if($null -ne (Get-ExactTaskOrAbsent $folder $taskName)){throw 'Fresh login task unexpectedly exists.'}
    Assert-ProtectedPath (Split-Path -Parent $root)
    $null=[IO.Directory]::CreateDirectory($root,(New-PrivateAcl -Directory));Assert-ProtectedPath $root
    foreach($copy in @(@($source,'worker_claude_login_bridge_r3.py',$sourceHash),@($support,'worker_native_status_support.py',$supportHash))){
        $inputStream=Open-VerifiedFile $copy[0] $copy[2] (Get-Item -LiteralPath $copy[0]).Length
        try{$outputStream=[IO.FileStream]::new((Join-Path $root $copy[1]),[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-PrivateAcl));try{$inputStream.CopyTo($outputStream);$outputStream.Flush($true)}finally{$outputStream.Dispose()}}finally{$inputStream.Dispose()}
        $destination=Join-Path $root $copy[1]
        $held.Add((Open-VerifiedFile $destination $copy[2] (Get-Item -LiteralPath $copy[0]).Length))
    }
    $definition=$scheduler.NewTask(0);$definition.RegistrationInfo.Description='Explicit owner interactive Claude login; one private bounded input line; no model jobs or daemon activation.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT15M'
    $action=$definition.Actions.Create(0);$action.Path=$python;$action.Arguments='-I -B "'+(Join-Path $root 'worker_claude_login_bridge_r3.py')+'" --slot '+$Slot+' --nonce '+$nonce;$action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);$log=Join-Path $root 'operator.log';$inputPath=Join-Path $root 'input.once';$seen=0;$submitted=$false;$deadline=[DateTime]::UtcNow.AddMinutes(14)
    Write-Host "Claude login for $Slot. Follow its browser instructions. Press P only if Claude asks you to paste its returned code; press Q to cancel."
    Write-Host 'The P prompt masks input. Never paste the code at a PowerShell command prompt. Native output is suppressed after submission.'
    try{
        while(-not (Test-InstanceComplete $instance)){
            if([DateTime]::UtcNow -ge $deadline){Write-CancelRequest $root $nonce;throw 'Login wrapper deadline reached; cancellation requested. Preserve the task/root and review terminal cleanup; no automatic retry.'}
            if(Test-Path -LiteralPath $log){
                $stream=[IO.File]::Open($log,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
                try{if($stream.Length -gt 131072){throw 'Operator log exceeds its bound.'};$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);$text=$reader.ReadToEnd();if($text.Length -gt $seen){[Console]::Write($text.Substring($seen));$seen=$text.Length}}finally{$stream.Dispose()}
            }
            if([Console]::KeyAvailable){
                $key=[Console]::ReadKey($true).Key
                if($key -eq 'Q'){Write-CancelRequest $root $nonce;Write-Host 'Cancellation requested; waiting for owned Job/profile cleanup.'}
                elseif($key -eq 'P' -and -not $submitted -and (Test-Path -LiteralPath $log)){
                    $secret=Read-Host 'One Claude browser-returned code (masked)' -AsSecureString
                    try{if(Test-InstanceComplete $instance){throw 'Login ended while awaiting input; no code was written.'};Write-PrivatePacket $inputPath $nonce $secret;$submitted=$true;Write-Host 'One line submitted. Waiting for native login exit and cleanup.'}finally{$secret.Dispose()}
                }
            }
            Start-Sleep -Milliseconds 100
        }
    }catch{
        # Never terminate an unrelated process or delete an unverified session.
        try{Write-CancelRequest $root $nonce}catch{};throw
    }
    Assert-TaskTerminal $task
    $receiptPath=Join-Path $root 'receipt.json';Assert-ProtectedPath $receiptPath
    if((Get-Item -LiteralPath $receiptPath).Length -gt 32768){throw 'Login receipt exceeds its bound.'}
    $receipt=Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8|ConvertFrom-Json
    if($receipt.runtime_root -cne $installRoot -or $receipt.install_receipt_sha256 -cne $runtime.install_receipt_sha256 -or $receipt.source_manifest_sha256 -cne $runtime.source_manifest_sha256 -or $receipt.resource_limits_sha256 -cne 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f' -or $receipt.config_sha256 -cne $runtime.configuration_sha256 -or $receipt.revision.source_sha256 -cne $runtime.revision.source_sha256){throw 'Login receipt r3 runtime binding differs.'}
    if($receipt.schema -ne 'cochem-interactive-claude-login/1' -or $receipt.nonce -cne $nonce -or $receipt.slot -cne $Slot -or $receipt.system_sid -ne 'S-1-5-18' -or $receipt.helper_sha256 -cne $sourceHash -or $receipt.status -ne 'LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED' -or $receipt.cleanup_verified -ne $true -or $task.LastTaskResult -ne 0){throw 'Login did not produce a verified successful command/cleanup receipt. Preserve task/root and review; do not retry automatically.'}
    [ordered]@{runtime_root=$installRoot;install_receipt_sha256=$runtime.install_receipt_sha256;schema='cochem-interactive-claude-login-result/1';slot=$Slot;status=$receipt.status;cleanup_verified=$true;receipt_path=$receiptPath;receipt_sha256=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant();task_preserved=$true;authentication_verified=$false;next_step='Separate reviewed native authentication-status check';model_jobs_executed=0;activation_ready=$false}|ConvertTo-Json -Depth 4
}finally{foreach($stream in $held){$stream.Dispose()}}
