
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Pinned identity source parse failure.'}
$functions=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Initialize-FileIdentity'},$true))
if($functions.Count -ne 1){throw 'Pinned identity initializer missing.'}
. ([scriptblock]::Create($functions[0].Extent.Text));Initialize-FileIdentity
$ast=[Management.Automation.Language.Parser]::ParseFile('C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\protected-code-inspection-v4.ps1',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Scanner source parse failure.'}
$addTypes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Add-Type'},$true))
if($addTypes.Count -ne 1){throw 'Scanner core initializer missing.'}
$source=@($addTypes[0].CommandElements | Where-Object {$_ -is [Management.Automation.Language.StringConstantExpressionAst] -and $_.StringConstantType -eq [Management.Automation.Language.StringConstantType]::SingleQuotedHereString})
if($source.Count -ne 1){throw 'Scanner CSharp source missing.'}
Add-Type -TypeDefinition $source[0].Value
Add-Type -TypeDefinition ([IO.File]::ReadAllText('C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\protected-inspection-v4-fixtures-ordinary-tmp\protected-inspection-v40\fixtures.cs'))
New-Item -ItemType Junction -Path 'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\protected-inspection-v4-fixtures-ordinary-tmp\protected-inspection-v40\reparse\junction' -Target 'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\protected-inspection-v4-fixtures-ordinary-tmp\protected-inspection-v40\target' | Out-Null
$method=[CoChemStagedFileIdentity].GetMethod('Check',[type[]]@([IO.FileStream],[string]))
$checker=[Delegate]::CreateDelegate([Action[IO.FileStream,string]],$method)
[InspectionFixturesV4]::Run([CoChemProtectedCodeInspectionV4],$checker,'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\protected-inspection-v4-fixtures-ordinary-tmp\protected-inspection-v40') | ConvertTo-Json -Depth 8
