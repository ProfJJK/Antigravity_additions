# Read-only ordinary-token measurement; imports only pinned function definitions.
$ErrorActionPreference='Stop'
$programFiles='C:\Program Files'
$copy='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$scanner=Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1'
function Import-MeasurementFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 if((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Hash){throw 'Measurement input changed.'}
 $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e)
 if($e.Count){throw 'Measurement parse failure.'}
 foreach($name in $Names){$n=@($ast.FindAll({param($x)$x -is [Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -ceq $name},$true));if($n.Count -ne 1){throw 'Measurement definition missing.'};$n[0].Extent.Text}
}
foreach($d in @(Import-MeasurementFunctions $copy '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity'))){. ([scriptblock]::Create($d))}
Initialize-FileIdentity
$definition=@(Import-MeasurementFunctions $scanner '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Assert-CodeTreeOnce'))[0]
$definition=$definition.Replace('$count=0;$deadline=', '$count=0;$script:measuredCount=0;$deadline=')
$definition=$definition.Replace('if(++$count -gt 50000', '$script:measuredCount=$count+1;if(++$count -gt 50000')
. ([scriptblock]::Create($definition))
$root='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$timer=[Diagnostics.Stopwatch]::StartNew();$status='PASSED';$failure=$null;$count=$null
try{$count=Assert-CodeTreeOnce $root}catch{$status='HELD';$failure=$_.Exception.Message}
[ordered]@{schema='cochem-ordinary-windows-inspection-measurement/1';scanner_sha256='18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7';root=$root;status=$status;entries=$count;reached_entry=$script:measuredCount;seconds=$timer.Elapsed.TotalSeconds;failure=$failure;protected_state_modified=$false;native_commands_executed=0}|ConvertTo-Json
