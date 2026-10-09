param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ScannerSha256)
# Read-only actual Windows scan; writes one new report only in this workspace.
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$programFiles='C:\Program Files'
$copy='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$scanner=Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1'
$held=[Collections.Generic.List[IO.FileStream]]::new()
function Import-MeasurementFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$held.Add($stream)
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$digest=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($digest -cne $Hash){throw 'Measurement input changed.'}
 $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
 try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
 $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$t,[ref]$e)
 if($e.Count){throw 'Measurement parse failure.'}
 foreach($name in $Names){$n=@($ast.FindAll({param($x)$x -is [Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -ceq $name},$true));if($n.Count -ne 1){throw 'Measurement definition missing.'};$n[0].Extent.Text}
}
try{
 foreach($d in @(Import-MeasurementFunctions $copy '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity'))){. ([scriptblock]::Create($d))}
 Initialize-FileIdentity
 foreach($d in @(Import-MeasurementFunctions $scanner $ScannerSha256 @('Assert-CodeTreeOnce'))){. ([scriptblock]::Create($d))}
 $rows=[Collections.Generic.List[object]]::new()
 foreach($root in @('C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3','C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312','C:\Program Files\CoChem\Native4.2.7-windows-20261006')){
  $timer=[Diagnostics.Stopwatch]::StartNew();$status='PASSED';$failure=$null;$count=$null
  try{$count=Assert-CodeTreeOnce $root}catch{$status='HELD';$failure=$_.Exception.Message}
  $rows.Add([ordered]@{root=$root;status=$status;entries=$count;seconds=$timer.Elapsed.TotalSeconds;failure=$failure})
 }
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 $result=[ordered]@{schema='cochem-ordinary-windows-inspection-measurement/2';scanner_sha256=$ScannerSha256;administrator=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);powershell_version=$PSVersionTable.PSVersion.ToString();observed_utc=[DateTime]::UtcNow.ToString('o');roots=@($rows.ToArray());protected_state_modified=$false;native_commands_executed=0;tasks_created=0;model_jobs_executed=0}
 $report=Join-Path $PSScriptRoot ('inspection-v4-windows-measurement-'+[Guid]::NewGuid().ToString('N')+'.json')
 $stream=[IO.File]::Open($report,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
 try{$writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false));try{$writer.WriteLine(($result|ConvertTo-Json -Depth 6));$writer.Flush()}finally{$writer.Dispose()}}finally{$stream.Dispose()}
 $result|ConvertTo-Json -Depth 6
 Write-Host ('Evidence: '+$report)
}finally{foreach($stream in $held){$stream.Dispose()}}
