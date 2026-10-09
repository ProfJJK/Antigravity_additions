# Inert function-only fixtures. Never invokes the wrapper boundary or scheduler.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'accept-cpu-system.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw 'CPU wrapper parse failed.'}
$function=$ast.Find({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Wait-CpuTaskInstance'},$true)
if ($null -eq $function) {throw 'Missing actual wait function.'}
. ([scriptblock]::Create($function.Extent.Text))
$finished=[pscustomobject]@{State=3}
$finished|Add-Member ScriptMethod Refresh {}
Wait-CpuTaskInstance $finished
$vanished=[pscustomobject]@{State=4}
$vanished|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('Task instance no longer running.',-2147216629)}
Wait-CpuTaskInstance $vanished
$denied=[pscustomobject]@{State=4}
$denied|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('Access denied.',-2147024891)}
$refused=$false
try {Wait-CpuTaskInstance $denied}catch{$refused=$true}
if (-not $refused) {throw 'Unknown refresh error was swallowed.'}
$running=[pscustomobject]@{State=4}
$running|Add-Member ScriptMethod Refresh {}
$timedOut=$false
try {Wait-CpuTaskInstance $running -TimeoutSeconds 0}catch{if ($_.Exception.Message -notlike '*wait timed out*') {throw};$timedOut=$true}
if (-not $timedOut) {throw 'Running task ignored bounded deadline.'}
[ordered]@{schema='cochem-cpu-task-wait-fixtures/1';passed=4;real_tasks_or_processes_started=$false;cases=@('terminal state','completed instance HRESULT','access denied propagated','bounded running timeout')}|ConvertTo-Json
