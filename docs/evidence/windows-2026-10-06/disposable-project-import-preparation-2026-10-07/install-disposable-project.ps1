#Requires -Version 5.1
<# Fixed private bare-project import. Default is read-only. -Apply requires
   owner Administrator, fresh target/session, exact custody and stopped daemons.
   No task, account, credential, model, existing database or RAM operation. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-PinnedFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$digest=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($digest -cne $Hash){throw 'Reviewed function source differs.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
        try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed function source parse failure.'}
        $found=@();foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
            if($f.Name -in $Names){$found+=$f.Name;$f.Extent.Text}}
        if(@($Names|Where-Object {$_ -notin $found}).Count){throw 'Reviewed function missing.'}
    }finally{$stream.Dispose()}
}

function Initialize-ProjectNative {
    if('CoChemProjectNative' -as [type]){return}
    Add-Type -ReferencedAssemblies @('System.dll','System.Numerics.dll') -TypeDefinition @'
using System;using System.IO;using System.Text;using System.Collections.Generic;using System.Runtime.InteropServices;using Microsoft.Win32.SafeHandles;
public static class CoChemProjectNative {
 [StructLayout(LayoutKind.Sequential)] public struct Info {public uint attr,c1,c2,a1,a2,w1,w2,volume,sizeHigh,sizeLow,links,indexHigh,indexLow;}
 [StructLayout(LayoutKind.Sequential)] struct IdInfo {public ulong volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] id;}
 [StructLayout(LayoutKind.Sequential)] struct SA {public int length;public IntPtr descriptor;[MarshalAs(UnmanagedType.Bool)]public bool inherit;}
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandle(SafeFileHandle h,out Info i);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(SafeFileHandle h,int kind,out IdInfo i,uint size);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr FindFirstFileNameW(string p,uint flags,ref uint length,StringBuilder name);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool FindNextFileNameW(IntPtr h,ref uint length,StringBuilder name);
 [DllImport("kernel32.dll")]static extern bool FindClose(IntPtr h);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFileAttributesW(string p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateDirectoryW(string p,ref SA sa);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s,uint rev,out IntPtr p,out uint n);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern SafeFileHandle CreateFileW(string p,uint access,uint share,IntPtr sa,uint mode,uint flags,IntPtr template);
 public static Info Information(FileStream f){Info i;if(!GetFileInformationByHandle(f.SafeFileHandle,out i))throw new System.ComponentModel.Win32Exception();return i;}
 static string RawIdentity(SafeFileHandle h){IdInfo i;if(!GetFileInformationByHandleEx(h,18,out i,24))throw new System.ComponentModel.Win32Exception();var b=new byte[17];Array.Copy(i.id,b,16);return i.volume+":"+new System.Numerics.BigInteger(b).ToString();}
 public static string Identity(FileStream f){return RawIdentity(f.SafeFileHandle);}
 public static string Identity(string path){using(var h=CreateFileW(path,0,7,IntPtr.Zero,3,0x02200000,IntPtr.Zero)){if(h.IsInvalid)throw new System.ComponentModel.Win32Exception();Info i;if(!GetFileInformationByHandle(h,out i))throw new System.ComponentModel.Win32Exception();if((i.attr&0x400)!=0)throw new IOException("Reparse target refused");return RawIdentity(h);}}
 public static int Probe(string path){uint value=GetFileAttributesW(path);return value==0xffffffff?Marshal.GetLastWin32Error():0;}
 public static string[] Aliases(string path){uint n=32768;var b=new StringBuilder((int)n);IntPtr h=FindFirstFileNameW(path,0,ref n,b);if(h==new IntPtr(-1))throw new System.ComponentModel.Win32Exception();var result=new List<string>();try{while(true){result.Add(Path.GetPathRoot(path).TrimEnd('\\')+b.ToString());if(result.Count>128)throw new IOException("Alias bound");n=32768;if(!FindNextFileNameW(h,ref n,b)){int e=Marshal.GetLastWin32Error();if(e!=38)throw new System.ComponentModel.Win32Exception(e);break;}}}finally{FindClose(h);}return result.ToArray();}
 public static void CreatePrivateDirectory(string path){IntPtr sd;uint n;if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",1,out sd,out n))throw new System.ComponentModel.Win32Exception();try{var sa=new SA{length=Marshal.SizeOf(typeof(SA)),descriptor=sd,inherit=false};if(!CreateDirectoryW(path,ref sa))throw new System.ComponentModel.Win32Exception();}finally{LocalFree(sd);}}
}
'@
}

function Get-PathState {
    param([string]$Path)
    $code=[CoChemProjectNative]::Probe($Path)
    switch($code){0{'PRESENT'}2{'ABSENT'}3{'ABSENT'}5{'INACCESSIBLE'}default{throw ('Path probe returned Windows error '+$code)}}
}

function Read-PinnedControl {
    param([string]$Path,[string]$Hash,[long]$Maximum=1048576)
    $item=Get-Item -LiteralPath $Path -Force
    if($item.PSIsContainer -or $item.Length -gt $Maximum){throw 'Pinned control size/type differs.'}
    $stream=Open-VerifiedFile $Path $Hash $item.Length;$script:held.Add($stream)
    Read-HeldText $stream
}

function Assert-GitRuntime {
    param($Evidence)
    if($Evidence.schema -ne 'cochem-git-static-custody-inspection/1' -or @($Evidence.binaries).Count -ne 12){throw 'Git custody evidence shape differs.'}
    if(@($Evidence.acl_entries).Count -ne 24){throw 'Git ancestry inventory differs.'}
    foreach($entry in $Evidence.acl_entries){
        Assert-ProtectedPath $entry.path
        if((Get-Acl -LiteralPath $entry.path).Sddl -cne $entry.sddl){throw 'Git file/alias/ancestor DACL changed.'}
    }
    foreach($row in $Evidence.binaries){
        $path=[string]$row.path
        if(-not $path.StartsWith('C:\Program Files\Git\',[StringComparison]::Ordinal)){throw 'Git runtime path escaped the observed installation.'}
        Assert-ProtectedPath $path
        $stream=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try{
            $info=[CoChemProjectNative]::Information($stream)
            $id=[CoChemProjectNative]::Identity($stream)
            $expectedId=([string]$row.metadata.volume)+':'+([string]$row.metadata.file_id)
            if($id -cne $expectedId -or $stream.Length -ne $row.metadata.bytes -or $info.links -ne $row.metadata.links -or ($info.attr -band 0x400)){throw 'Git binary identity changed.'}
            $sha=[Security.Cryptography.SHA256]::Create()
            try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
            if($actual -cne $row.sha256){throw 'Git binary hash changed.'}
            $actualAliases=@([CoChemProjectNative]::Aliases($path)|Sort-Object)
            $expectedAliases=@($row.hardlink_aliases|Sort-Object)
            if($actualAliases.Count -ne $info.links -or (Compare-Object $actualAliases $expectedAliases -CaseSensitive)){throw 'Git hardlink alias set changed.'}
            foreach($alias in $actualAliases){Assert-ProtectedPath $alias;if([CoChemProjectNative]::Identity($alias) -cne $id){throw 'Git alias identity changed.'}}
            $stream.Position=0;$script:held.Add($stream);$stream=$null
        }finally{if($null -ne $stream){$stream.Dispose()}}
    }
}

function Assert-DataParent {
    param([string]$Path,$Layout)
    Assert-NoReparseAncestors $Path
    $acl=Get-Acl -LiteralPath $Path
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-18' -or -not $acl.AreAccessRulesProtected){throw 'Preserved data boundary owner/protection differs.'}
    $expected=@('S-1-5-18|2032127|3|0','S-1-5-32-544|2032127|3|0')
    foreach($slot in $Layout.slots.PSObject.Properties){$expected+=([string]$slot.Value.sid)+'|1048608|0|0'}
    $actual=@(foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if($rule.AccessControlType -ne 'Allow'){throw 'Data boundary has an unexpected ACE.'}
        $rule.IdentityReference.Value+'|'+[int64]$rule.FileSystemRights+'|'+[int]$rule.InheritanceFlags+'|'+[int]$rule.PropagationFlags})
    if($actual.Count -ne 8 -or (Compare-Object ($actual|Sort-Object) ($expected|Sort-Object))){throw 'Data boundary differs from the six-identity layout.'}
    Assert-ControlAncestors (Split-Path -Parent $Path)
}

function Assert-ControlAncestors {
    param([string]$Path)
    Assert-NoReparseAncestors $Path
    $current=Get-Item -LiteralPath $Path -Force
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    while($null -ne $current){
        $acl=Get-Acl -LiteralPath $current.FullName
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Control ancestor owner is untrusted.'}
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
            if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0040)){throw 'Control ancestor permits untrusted replacement.'}}
        $current=$current.Parent
    }
}

function Assert-EffectivePrivateTree {
    param([string]$Path,[int]$Maximum=256,[switch]$OnlyRoot)
    $pending=[Collections.Generic.Queue[string]]::new();$pending.Enqueue($Path);$count=0
    while($pending.Count){
        if(++$count -gt $Maximum){throw 'Private project metadata exceeds its bound.'}
        $item=Get-Item -LiteralPath ($pending.Dequeue()) -Force
        if($item.Attributes -band 0x400){throw 'Private project has a reparse path.'}
        $acl=Get-Acl -LiteralPath $item.FullName;$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544') -or $rules.Count -ne 2){throw 'Private project owner/ACL differs.'}
        $sids=@();foreach($r in $rules){$sids+=$r.IdentityReference.Value
            if($r.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544') -or $r.AccessControlType -ne 'Allow' -or [int64]$r.FileSystemRights -ne 2032127 -or [int]$r.PropagationFlags -ne 0 -or ($item.PSIsContainer -and [int]$r.InheritanceFlags -ne 3)){throw 'Private project has an unexpected effective grant.'}}
        if(@($sids|Sort-Object -Unique).Count -ne 2){throw 'Private project trustee missing.'}
        if($item.PSIsContainer){if(-not $OnlyRoot){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$pending.Enqueue($child.FullName)}}}else{
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()}}
    }
    return $count
}

function Invoke-PrivateImporter {
    param([string]$Executable,[string[]]$Arguments)
    $prior=$ErrorActionPreference;$global:LASTEXITCODE=$null;$exitCode=$null
    try{
        # WinPS5.1 native stderr is an ErrorRecord even for native exit0.
        # Capture it without displaying raw bootstrap/native output. The
        # pinned helper emits no success output and bounded metadata on failure.
        $ErrorActionPreference='Continue'
        $nativeOutput=@(& $Executable @Arguments 2>&1)
        $exitCode=$global:LASTEXITCODE
    }finally{$ErrorActionPreference=$prior}
    $bytes=[Text.Encoding]::UTF8.GetByteCount(($nativeOutput|Out-String))
    if($bytes -gt 65536){throw 'Helper output exceeded its fixed-output bound; preserve partial state.'}
    [pscustomobject]@{exit_code=$exitCode;unexpected_output=($nativeOutput.Count -ne 0);output_bytes=$bytes}
}

function Get-SanitizedImportFailure {
    param($Receipt)
    $type='UnexpectedException';$code='receipt_or_child_outcome'
    if($null -ne $Receipt.PSObject.Properties['failure']){
        if([string]$Receipt.failure.error_type -in @('ImportHeld','OSError','PermissionError','RuntimeError','ValueError','FileNotFoundError')){$type=[string]$Receipt.failure.error_type}
        $candidate=[string]$Receipt.failure.code
        if($candidate -match '^git_nonzero_-?[0-9]{1,11}$' -or $candidate -in @('bundle_pin','bundle_header','bundle_pack_shape','bundle_pack_checksum','git_deadline','git_timeout_preserve_partial','git_output_bound','fresh_target_identity','fresh_target_not_empty','template_not_empty','not_bare','object_format','repository_path','reference_identity','baseline_identity','non_blob_tree','tree_files','blob_bytes','object_count','object_types','repository_entry_custody','unexpected_repository_control','unexpected_local_config','target_identity_changed','unexpected_exception')){$code=$candidate}
    }
    [pscustomobject]@{error_type=$type;code=$code}
}

# Invocation boundary: all fixtures extract functions; privileged Apply is unrun.
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires owner Administrator.'}
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions';$programFiles='C:\Program Files'
$copySource=Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1'
foreach($d in @(Import-PinnedFunctions $copySource '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($d))}
foreach($d in @(Import-PinnedFunctions (Join-Path $PSScriptRoot 'install-stopped-runtime-r3.ps1') '372a0fe352124e81a2d097ebe7c075369f6f20275a8a2858f78f41febbc723e4' @('Read-HeldText','Assert-StoppedRuntimeTasks'))){. ([scriptblock]::Create($d))}
foreach($d in @(Import-PinnedFunctions (Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1') '5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a' @('New-PrivateAcl','Assert-PrivateItem','Copy-PrivateFile','Assert-CodeTreeOnce'))){. ([scriptblock]::Create($d))}
Initialize-FileIdentity;Initialize-ProjectNative
$runtime='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$target='C:\ProgramData\CoChemPipeline427\projects\windows-acceptance';$dataParent='C:\ProgramData\CoChemPipeline427';$projects=Split-Path -Parent $target
$session='C:\Program Files\CoChem\ProjectImport4.2.7-windows-20261007-r3'
$python='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$source=Join-Path $PSScriptRoot 'import-disposable-project.py';$sourceHash='3224bbc544a9f46ef402cb6bc483bdaa854bbfdf94737323e5408ae94e87ce1d'
$bundle='C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\disposable-acceptance-c52a3a97.bundle';$bundleHash='08b7006e1eaf4a69503c7dbefd4e0e340734fcff9d6cb011a509a63cff0dff87'
$gitEvidencePath=Join-Path $repo 'docs\evidence\windows-2026-10-06\git-custody-followup-2026-10-07\git-static-custody.json'
$held=[Collections.Generic.List[IO.Stream]]::new();$mutex=$null;$locked=$false
try{
    $null=Read-PinnedControl $source $sourceHash
    $stream=Open-VerifiedFile $bundle $bundleHash 622;$held.Add($stream)
    $stream=Open-VerifiedFile $python 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' (Get-Item -LiteralPath $python).Length;$held.Add($stream)
    $gitEvidence=(Read-PinnedControl $gitEvidencePath 'ebfd2ef955b22d9765d0c171ee7c4c2f4bf70507369c21411897848240af121e')|ConvertFrom-Json
    Assert-GitRuntime $gitEvidence
    $holds=@();$runtimeReady=$false;$installationHash=$null;$layout=$null
    $installationPath=Join-Path $runtime 'install-after.json'
    if((Get-PathState $installationPath) -eq 'PRESENT'){
        Assert-ProtectedPath $installationPath
        $installationHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
        $installation=(Read-PinnedControl $installationPath $installationHash)|ConvertFrom-Json
        $config=(Read-PinnedControl (Join-Path $runtime 'pipeline.json') '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c')|ConvertFrom-Json
        $layout=(Read-PinnedControl (Join-Path $runtime 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4')|ConvertFrom-Json
        $null=Read-PinnedControl (Join-Path $runtime 'source-manifest.json') '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
        if($installation.schema -ne 'cochem-stopped-runtime-update/1' -or $installation.mode -ne 'FRESH_STOPPED_RUNTIME_READY' -or $installation.target_root -cne $runtime -or $installation.source_files -ne 166 -or $installation.configuration_sha256 -ne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or $installation.source_manifest_sha256 -ne '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1' -or $installation.verification.revision.verified -ne $true -or $installation.verification.revision.files -ne 109 -or $installation.verification.revision.source_sha256 -ne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'){throw 'R3 installation receipt binding differs.'}
        if($config.max_execution_slots -ne 4 -or @($config.workers.PSObject.Properties.Name).Count -ne 6 -or @($layout.slots.PSObject.Properties.Name).Count -ne 6 -or $config.coding_projects.'windows-acceptance'.repository -cne $target -or $config.coding_projects.'windows-acceptance'.branch -cne 'pipeline/accepted'){throw 'R3 project/capacity layout differs.'}
        $runtimeReady=$true
    }else{$holds+='R3 installation is not yet available for binding.'}
    $targetState=Get-PathState $target;$sessionState=Get-PathState $session
    if($targetState -eq 'PRESENT'){$holds+='Project target exists; preserve it and do not retry.'}
    if($sessionState -ne 'ABSENT'){$holds+='Fresh helper session is not absent; preserve it.'}
    if($targetState -eq 'INACCESSIBLE'){$holds+='Project absence is inaccessible under this token; Administrator must establish it.'}
    if($admin -and $runtimeReady){Assert-DataParent $dataParent $layout;if((Get-PathState $projects) -eq 'PRESENT'){$null=Assert-EffectivePrivateTree $projects -OnlyRoot}}else{$holds+='Data-parent custody requires the owner Administrator token.'}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-StoppedRuntimeTasks $folder
    $plan=[ordered]@{schema='cochem-private-project-import-plan/1';mode='READ_ONLY_PLAN';target=$target;target_state=$targetState;session=$session;runtime=$runtime;r3_install_receipt_sha256=$installationHash;config_sha256='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c';layout_sha256='8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4';bundle_sha256=$bundleHash;helper_sha256=$sourceHash;git_custody_paths=12;worker_identities=6;shared_slots=4;project_code_executed=$false;models_executed=0;tasks_created=0;activation_ready=$false;holds=$holds}
    if(-not $Apply){$plan|ConvertTo-Json -Depth 6;return}
    if($holds.Count){throw ($holds -join ' ')}
    $mutex=[Threading.Mutex]::new($false,'Global\CoChem427-PrivateProjectImport')
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another project import owns the setup mutex.'}
    Assert-StoppedRuntimeTasks $folder;Assert-DataParent $dataParent $layout
    if((Get-PathState $target) -ne 'ABSENT' -or (Get-PathState $session) -ne 'ABSENT'){throw 'Fresh target/session state changed; preserve it.'}
    $null=Assert-CodeTreeOnce (Split-Path -Parent $python)
    Assert-ProtectedPath (Split-Path -Parent $session)
    if((Get-PathState $projects) -eq 'ABSENT'){[CoChemProjectNative]::CreatePrivateDirectory($projects)}else{$null=Assert-EffectivePrivateTree $projects -OnlyRoot}
    [CoChemProjectNative]::CreatePrivateDirectory($session);[CoChemProjectNative]::CreatePrivateDirectory($target)
    Assert-PrivateItem $session;Assert-PrivateItem $target
    $rootId=[CoChemProjectNative]::Identity($target);$nonce=[Guid]::NewGuid().ToString('N')
    foreach($name in @('empty-template','temporary')){[CoChemProjectNative]::CreatePrivateDirectory((Join-Path $session $name))}
    $destination=Join-Path $session 'import-disposable-project.py';Copy-PrivateFile $source $destination $sourceHash (Get-Item -LiteralPath $source).Length
    Copy-PrivateFile $bundle (Join-Path $session 'acceptance.bundle') $bundleHash 622
    $stream=Open-VerifiedFile $destination $sourceHash (Get-Item -LiteralPath $destination).Length;$held.Add($stream)
    $stream=Open-VerifiedFile (Join-Path $session 'acceptance.bundle') $bundleHash 622;$held.Add($stream)
    $native=Invoke-PrivateImporter $python @('-I','-S','-B',$destination,'--apply','--nonce',$nonce,'--root-id',$rootId)
    $childExit=$native.exit_code
    $receiptPath=Join-Path $session 'project-import.json'
    if((Get-PathState $receiptPath) -ne 'PRESENT'){
        [ordered]@{schema='cochem-private-project-import-result/1';status='HELD_PRESERVE_PARTIAL';phase='helper_bootstrap';exit_code=$childExit;raw_child_output_withheld=$true}|ConvertTo-Json -Compress
        throw 'Private receipt was not created. Preserve partial state; no retry.'}
    $null=Assert-EffectivePrivateTree $session
    $receiptHash=(Get-FileHash -LiteralPath $receiptPath).Hash.ToLowerInvariant()
    $receipt=(Read-PinnedControl $receiptPath $receiptHash 16384)|ConvertFrom-Json
    if($receipt.schema -ne 'cochem-private-project-import/1' -or $receipt.nonce -ne $nonce -or $receipt.helper_sha256 -ne $sourceHash -or $receipt.target -cne $target -or $receipt.root_id -cne $rootId -or $receipt.bundle_sha256 -ne $bundleHash){throw 'Private import receipt binding differs; preserve all evidence.'}
    if($null -eq $childExit -or $childExit -ne 0 -or $native.unexpected_output -or $receipt.status -ne 'PRIVATE_BARE_PROJECT_VERIFIED'){
        $failure=Get-SanitizedImportFailure $receipt
        [ordered]@{schema='cochem-private-project-import-result/1';status='HELD_PRESERVE_PARTIAL';receipt_sha256=$receiptHash;receipt_path=$receiptPath;error_type=$failure.error_type;code=$failure.code}|ConvertTo-Json -Compress
        throw 'Private import held. Preserve every existing output; do not rerun.'}
    if($receipt.bare -ne $true -or $receipt.branch -cne 'pipeline/accepted' -or $receipt.baseline_commit -cne 'c52a3a97eb085e6bafbbd14bd6a75f3274288530' -or $receipt.baseline_tree -cne '0e7fe3edd935e4a839d17cb99b30b08db18d8ae9' -or $receipt.objects -ne 7 -or $receipt.files -ne 3 -or $receipt.model_jobs -ne 0 -or $receipt.checkout_performed -ne $false -or $receipt.network_protocols_allowed -ne $false){throw 'Private project acceptance fields differ; preserve all evidence.'}
    $verifiedEntries=Assert-EffectivePrivateTree $target
    if([CoChemProjectNative]::Identity($target) -cne $rootId){throw 'Project root identity changed.'}
    Assert-StoppedRuntimeTasks $folder;Assert-DataParent $dataParent $layout
    [ordered]@{schema='cochem-private-project-import-result/1';status='PRIVATE_BARE_PROJECT_VERIFIED';target=$target;receipt_path=$receiptPath;receipt_sha256=$receiptHash;r3_install_receipt_sha256=$installationHash;config_sha256=$plan.config_sha256;git_custody_paths=12;private_entries=$verifiedEntries;baseline_commit=$receipt.baseline_commit;baseline_tree=$receipt.baseline_tree;objects=$receipt.objects;files=$receipt.files;activation_ready=$false}|ConvertTo-Json -Depth 6
}finally{if($locked){$mutex.ReleaseMutex()};if($null -ne $mutex){$mutex.Dispose()};foreach($stream in $held){$stream.Dispose()}}
