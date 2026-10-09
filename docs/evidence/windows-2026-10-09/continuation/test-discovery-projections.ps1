#Requires -Version 5.1
# Pure projection/formatting checks only. Does not run either inspector, query
# tasks, invoke ImDisk, or write a discovery report.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$loaded=@()
foreach ($file in @('inspect-windows-admin-details.ps1','share-admin-discovery.ps1')) {
    $tokens=$null; $parseErrors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $file),[ref]$tokens,[ref]$parseErrors)
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
    foreach ($function in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) {
        if ($function.Name -in @('Get-TaskSearchText','Format-OptionalTime','Remove-PrivateDiscoveryFields')) {
            . ([ScriptBlock]::Create($function.Extent.Text))
            $loaded += $function.Name
        }
    }
}
if ($loaded.Count -ne 3) { throw 'Expected exactly the three pure helpers.' }
$cases=0
$comTask=[pscustomobject]@{TaskPath='\Folder\';TaskName='RAMDrive startup';Actions=@([pscustomobject]@{ClassId='COM-handler-placeholder'},$null)}
if ((Get-TaskSearchText $comTask) -notmatch 'RAMDrive startup') { throw 'COM/null action name lookup failed.' }; $cases++
$emptyTask=[pscustomobject]@{TaskPath='\';TaskName='No action';Actions=$null}
if ((Get-TaskSearchText $emptyTask) -notmatch 'No action') { throw 'Null action collection failed.' }; $cases++
$execTask=[pscustomobject]@{TaskPath='\Nested\';TaskName='Mount';Actions=@([pscustomobject]@{Execute='C:\Windows\System32\imdisk.exe';Arguments='-a -s 8G -m R:'})}
if ((Get-TaskSearchText $execTask) -notmatch 'imdisk.exe -a -s 8G -m R:') { throw 'Executable/argument lookup failed.' }; $cases++
if ($null -ne (Format-OptionalTime $null)) { throw 'Null NextRunTime must remain null.' }; $cases++
$time=[DateTime]::Parse('2026-10-07T04:22:56Z').ToUniversalTime()
if ((Format-OptionalTime $time) -ne $time.ToString('o')) { throw 'Date formatting failed.' }; $cases++
$inputObject='{"name":"keep","actions":[{"execute":"imdisk.exe","private_arguments":"DO_NOT_SHARE"}],"nested":{"private_token":"DO_NOT_SHARE","safe":true},"empty":[]}' | ConvertFrom-Json
$projected=Remove-PrivateDiscoveryFields $inputObject
$json=$projected | ConvertTo-Json -Depth 12
if ($json -match 'DO_NOT_SHARE|private_arguments|private_token') { throw 'Private fields leaked.' }; $cases++
if ($projected.name -ne 'keep' -or $projected.actions[0].execute -ne 'imdisk.exe' -or -not $projected.nested.safe) { throw 'Safe metadata was lost.' }; $cases++
if ($projected.empty.Count -ne 0) { throw 'Empty arrays were not preserved.' }; $cases++
$dictionary=Remove-PrivateDiscoveryFields ([ordered]@{private_arguments='DO_NOT_SHARE';safe='retained'})
if ($dictionary.Contains('private_arguments') -or $dictionary.safe -ne 'retained') { throw 'Dictionary projection failed.' }; $cases++
$parsed='{"holds":["Administrator discovery does not establish SYSTEM acceptance.","No sensor measured.","",null],"property_string":"plain text","scalars":[true,false,0,17,1.25],"nested":[{"message":"keep this","private_arguments":"DO_NOT_SHARE"}]}' | ConvertFrom-Json
$fixed=Remove-PrivateDiscoveryFields $parsed
$roundTrip=$fixed | ConvertTo-Json -Depth 12 | ConvertFrom-Json
if ($roundTrip.holds.Count -ne 4 -or $roundTrip.holds[0] -isnot [string] -or $roundTrip.holds[0] -ne $parsed.holds[0] -or $roundTrip.holds[1] -ne $parsed.holds[1]) { throw 'Parsed JSON string arrays changed type or content.' }; $cases++
if ($roundTrip.holds[2] -isnot [string] -or $roundTrip.holds[2] -ne '' -or $null -ne $roundTrip.holds[3]) { throw 'Empty string or null array position changed.' }; $cases++
if ($roundTrip.property_string -isnot [string] -or $roundTrip.property_string -ne 'plain text' -or $roundTrip.nested[0].message -ne 'keep this') { throw 'Parsed JSON string properties changed.' }; $cases++
if ($roundTrip.scalars.Count -ne 5 -or $roundTrip.scalars[0] -isnot [bool] -or -not $roundTrip.scalars[0] -or $roundTrip.scalars[1] -isnot [bool] -or $roundTrip.scalars[1] -ne $false -or $roundTrip.scalars[2] -ne 0 -or $roundTrip.scalars[3] -ne 17 -or $roundTrip.scalars[4] -ne 1.25) { throw 'Parsed JSON scalar arrays changed.' }; $cases++
if (($fixed | ConvertTo-Json -Depth 12) -match 'DO_NOT_SHARE|private_arguments') { throw 'Scalar preservation bypassed private-field removal.' }; $cases++
$wrapped=[psobject]::AsPSObject('wrapped scalar')
if ((Remove-PrivateDiscoveryFields $wrapped) -isnot [string] -or (Remove-PrivateDiscoveryFields $wrapped) -ne 'wrapped scalar') { throw 'Wrapped scalar was projected as an object.' }; $cases++
[pscustomobject]@{pure_checks_passed=$cases; parse_errors=0; powershell=$PSVersionTable.PSVersion.ToString(); inspectors_executed=$false; reports_written=$false} | ConvertTo-Json
