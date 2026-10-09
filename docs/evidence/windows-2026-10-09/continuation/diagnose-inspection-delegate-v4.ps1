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
 $method=[CoChemStagedFileIdentity].GetMethod('Check',[type[]]@([IO.FileStream],[string]));$checker=[Delegate]::CreateDelegate([Action[IO.FileStream,string]],$method)
 $file='C:\Program Files\CoChem\Native4.2.7-windows-20261006\codex.exe'
 $stream=[IO.File]::Open($file,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
   try{[CoChemStagedFileIdentity]::Check($stream,$file);'direct=PASS'}catch{$_|Out-String}
   try{$checker.Invoke($stream,$file);'delegate=PASS'}catch{$_|Out-String}
   Add-Type -TypeDefinition 'using System;using System.IO;public static class CoChemDelegateDiagnosticV4{public static void Test(string p,Action<FileStream,string> check){using(var s=new FileStream(p,FileMode.Open,FileAccess.Read,FileShare.Read)){check(s,p);}}}'
   try{[CoChemDelegateDiagnosticV4]::Test($file,$checker);'CSharp=PASS'}catch{$_|Out-String}
 }finally{$stream.Dispose()}
}finally{foreach($stream in $held){$stream.Dispose()}}