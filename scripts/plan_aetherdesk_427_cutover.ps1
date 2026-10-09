#Requires -Version 5.1
<#
Read-only deployment planner. This script never executes generated commands,
installs software, reads token contents, provisions accounts, or changes tasks.
An optional output file is created once; existing evidence is never overwritten.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$StageManifest,
    [Parameter(Mandatory=$true)][string]$PipelineProposal,
    [Parameter(Mandatory=$true)][string]$SupervisorProposal,
    [Parameter(Mandatory=$true)][string]$SourceManifest,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Uv,
    [string]$PreviousSupervisorDataRoot,
    [string]$MigrationEvidence,
    [string]$Output
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# A caller may inherit PowerShell 7's PSModulePath while launching Windows
# PowerShell 5.1. Resolve built-in inspection modules from this interpreter.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop

function Read-PlanJson {
    param([string]$Path)
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ((Get-Item -LiteralPath $resolved).Length -gt 4194304) { throw "Plan input exceeds 4 MiB: $resolved" }
    Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Add-Hold {
    param([string]$Id,[string]$Reason)
    $script:holds.Add([ordered]@{id=$Id;reason=$Reason})
}
function Full-LocalPath {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Contains("`0") -or $Path.Substring(2).Contains(':')) { throw 'Deployment paths must be explicit absolute local Windows paths without alternate streams.' }
    [IO.Path]::GetFullPath($Path).TrimEnd('\')
}
function Is-Within {
    param([string]$Path,[string]$Root)
    $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)
}
function Inspect-ProtectedFile {
    param([string]$Path,[string]$Label)
    $full = Full-LocalPath $Path
    if (-not (Is-Within $full $script:programFiles)) { Add-Hold "$Label-protection" "$Label requires a protected Program Files path: $full"; return }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { Add-Hold "$Label-missing" "$Label is not staged at $full"; return }
    $current = Get-Item -LiteralPath $full -Force
    while ($null -ne $current -and (Is-Within $current.FullName $script:programFiles)) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) { Add-Hold "$Label-link" "Reparse path requires rejection: $($current.FullName)"; return }
        try {
            $acl = Get-Acl -LiteralPath $current.FullName
            $trusted = @('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
            if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { Add-Hold "$Label-owner" "Untrusted code owner: $($current.FullName)"; return }
            foreach ($rule in $acl.Access) {
                $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
                if ($rule.AccessControlType -eq 'Allow' -and $sid -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)) {
                    Add-Hold "$Label-writer" "Untrusted code write access: $($current.FullName)"; return
                }
            }
        }
        catch { Add-Hold "$Label-acl-unverified" "Cannot attest ACLs for $($current.FullName)"; return }
        if ($current.FullName.TrimEnd('\') -eq $script:programFiles) { break }
        # DirectoryInfo.Parent is a raw .NET object and need not retain the
        # PowerShell provider's PSIsContainer adaptation under strict mode.
        $current = if ($current -is [IO.DirectoryInfo]) { $current.Parent } else { $current.Directory }
    }
}

$holds = [Collections.Generic.List[object]]::new()
$stage = Read-PlanJson $StageManifest
$pipeline = Read-PlanJson $PipelineProposal
$supervisor = Read-PlanJson $SupervisorProposal
$manifest = Read-PlanJson $SourceManifest
$repo = Full-LocalPath $stage.source_repository
$programFiles = Full-LocalPath $env:ProgramFiles
$pipelineRoot = Full-LocalPath $stage.protected_roots.pipeline
$supervisorRoot = Full-LocalPath $stage.protected_roots.supervisor
$supervisorData = Split-Path -Parent (Full-LocalPath $supervisor.private_root)
$Python = Full-LocalPath $Python
$Uv = Full-LocalPath $Uv

if ($stage.initial_shared_slots -ne 4 -or $stage.max_shared_slots -ne 4 -or $pipeline.max_execution_slots -ne 4 -or $pipeline.docker.max_containers -ne 4 -or $pipeline.docker.warm_pool_size -ne 2) { throw 'Initial deployment must retain four shared slots, Docker ceiling four and two warm containers within that shared capacity.' }
if ($stage.provisioned_worker_identities -ne 6 -or @($pipeline.workers.PSObject.Properties).Count -ne 6 -or @($pipeline.slot_roots.PSObject.Properties).Count -ne 6) { throw 'The canonical six-chapter DAG requires six isolated worker identities; this does not increase the four shared slots.' }
foreach ($number in 1..6) {
    $slot = "slot$number"
    $worker = $pipeline.workers.PSObject.Properties[$slot]
    $root = $pipeline.slot_roots.PSObject.Properties[$slot]
    if ($null -eq $worker -or $null -eq $root -or $worker.Value.name -ne "CoChem422Worker$number" -or $worker.Value.credential_target -ne "CoChem422/$slot" -or (Full-LocalPath $root.Value) -ne (Full-LocalPath (Join-Path $stage.protected_roots.data "workers\$slot"))) { throw 'The six planned worker identities must retain distinct canonical names, credential targets and roots.' }
}
if ($pipeline.ramdisk.mount_root -ne 'R:\' -or $pipeline.ramdisk.size_mb -ne 8192 -or $stage.ramdisk.format_resize_recreate -ne $false) { throw 'The existing 8 GiB R: volume and its startup task must be preserved.' }
if ($supervisor.max_per_incident -ne 2 -or $supervisor.max_per_day -ne 4 -or $supervisor.cooldown_seconds -lt 1800) { throw 'Canonical 4.2.7 repair ceilings and cooldown cannot be increased or reset.' }
if ($supervisor._staging.migration.create_empty_replacement_ledgers -ne $false) { throw 'Empty replacement budget ledgers are forbidden.' }
if ($supervisor.warden_task -eq $stage.migration.existing_warden_task) { throw 'The legacy Hyper-V MCP task cannot be silently adopted as a protected pipeline Warden.' }
if ($supervisor.repair_worker.name -in @($pipeline.workers.PSObject.Properties.Value.name)) { throw 'The repair identity must remain separate from pipeline workers.' }
$expectedPaths = [ordered]@{
    baseline_source=(Join-Path $supervisorRoot 'source'); acceptance_root=(Join-Path $supervisorRoot 'acceptance')
    test_python=(Join-Path $supervisorRoot '.venv\Scripts\python.exe'); pipeline_python=(Join-Path $pipelineRoot '.venv\Scripts\python.exe')
    pipeline_config=(Join-Path $supervisorRoot 'pipeline.reviewed.json'); pointer_file=(Join-Path $supervisor.private_root 'active-release.json')
}
foreach ($entry in $expectedPaths.GetEnumerator()) {
    if ((Full-LocalPath $supervisor.($entry.Key)) -ne (Full-LocalPath $entry.Value)) { throw "Supervisor path disagrees with this staged installation: $($entry.Key)" }
}
$tiers = @{
    '1-3'=@(@('gemini','gemini-3.8-flash',''),@('claude','claude-haiku-4-5',''),@('codex','gpt-6-luna','low'))
    '4-6'=@(@('claude','claude-sonnet-5-5',''),@('codex','gpt-6.1-sol','medium'),@('gemini','gemini-3.8-flash','extended'))
    '7-9'=@(@('codex','gpt-6.1-sol','high'),@('claude','claude-opus-5-5','extended'),@('gemini','gemini-3.1-pro-preview','high'))
    '10'=@(@('claude','claude-fable-5-1','extended'),@('codex','gpt-6-astra','ultra'))
}
if ($pipeline.routing.policy_version -ne 2 -or $pipeline.routing.provider_limits.claude.max_concurrency -ne 20 -or $null -ne $pipeline.routing.provider_limits.codex.max_concurrency -or $null -ne $pipeline.routing.provider_limits.gemini.max_concurrency) { throw 'Chapter 06 routing/provider ceilings differ from the owner decisions.' }
foreach ($tier in $tiers.Keys) {
    $actual = @($pipeline.routing.tiers.$tier)
    if ($actual.Count -ne $tiers[$tier].Count) { throw "Routing tier length differs: $tier" }
    for ($index=0; $index -lt $actual.Count; $index++) {
        if ($actual[$index].provider -ne $tiers[$tier][$index][0] -or $actual[$index].model -ne $tiers[$tier][$index][1] -or [string]$actual[$index].reasoning_effort -ne $tiers[$tier][$index][2]) { throw "Chapter 06 route differs: $tier position $index" }
    }
}

$sourceMismatches = [Collections.Generic.List[string]]::new()
$checked = 0
foreach ($entry in $manifest.files.PSObject.Properties) {
    $path = Full-LocalPath (Join-Path $repo $entry.Name)
    if (-not (Is-Within $path $repo)) { throw 'Source manifest escapes the repository.' }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.Value) { $sourceMismatches.Add($entry.Name) }
    $checked++
}
if ($checked -eq 0) { throw 'An empty source manifest cannot establish a staged release.' }
if ($sourceMismatches.Count) { Add-Hold 'source-evidence-stale' 'Current source differs from captured evidence; refresh regression evidence and the exact source manifest before staging.' }
Inspect-ProtectedFile $Python 'python'
Inspect-ProtectedFile $Uv 'uv'
foreach ($provider in @('codex','claude','gemini')) { Inspect-ProtectedFile $pipeline.providers.$provider.executable $provider }
$nativePayloads = @()
$destinations = @{'uv Python'=$Python;uv=$Uv;Codex=$pipeline.providers.codex.executable;Claude=$pipeline.providers.claude.executable;Agy=$pipeline.providers.gemini.executable}
foreach ($source in @($stage.native_sources)) {
    $observed = $null
    if (Test-Path -LiteralPath $source.path -PathType Leaf) { $observed = (Get-FileHash -LiteralPath $source.path -Algorithm SHA256).Hash }
    if ($observed -ne $source.sha256) { Add-Hold 'native-source-drift' "Observed native source differs from its capture: $($source.name)" }
    $destination = $destinations[$source.name]
    $destinationHash = $null
    if ($destination -and (Test-Path -LiteralPath $destination -PathType Leaf)) { $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash }
    if ($destinationHash -and $destinationHash -ne $source.sha256) { Add-Hold 'native-destination-drift' "Protected native destination differs from captured source: $($source.name)" }
    $nativePayloads += [ordered]@{name=$source.name;source=$source.path;captured_sha256=$source.sha256;observed_source_sha256=$observed;destination=$destination;observed_destination_sha256=$destinationHash}
}
foreach ($root in @($pipelineRoot,$supervisorRoot,$supervisor.release_root)) {
    if (Test-Path -LiteralPath $root) { Add-Hold 'existing-protected-destination' "Preserve existing destination and verify exact contents before any reuse: $root" }
}
if ($null -eq $pipeline.providers.gemini.subscription_probe -or $null -eq $pipeline.providers.gemini.inference_only -or $null -eq $pipeline.providers.gemini.arguments) { Add-Hold 'agy-native-contract' 'Owner confirms Agy is installed, signed in and functioning. Only its isolated pipeline integration remains unverified; keep affected attempts on a compatibility hold with canonical Chapter 06 spillover. This finding does not establish a provider outage or a global startup hold.' }
if (@($pipeline.providers.claude.effort_contracts.PSObject.Properties).Count -eq 0 -or @($pipeline.providers.gemini.effort_contracts.PSObject.Properties).Count -eq 0) { Add-Hold 'extended-effort-contracts' 'Protected native Extended/high bindings remain unverified; ordinary Chapter 06 spillover must remain visible.' }
if (-not $PreviousSupervisorDataRoot) { Add-Hold 'previous-budget-root' 'Previous supervisor budget authority is unresolved. Do not use a default nonexistent directory or an empty replacement ledger.' }
else {
    $PreviousSupervisorDataRoot = Full-LocalPath $PreviousSupervisorDataRoot
    if ($PreviousSupervisorDataRoot -eq $supervisorData) { throw 'Previous and new supervisor data roots must be distinct.' }
    foreach ($ledger in @('supervisor.db','component-recovery.db')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PreviousSupervisorDataRoot "private\$ledger") -PathType Leaf)) { Add-Hold "previous-$ledger" "Existing $ledger not found; legacy budget mapping requires an explicit reviewed migration, not fresh ledger creation." }
    }
}
if (-not $MigrationEvidence) { Add-Hold 'migration-evidence' 'Consistent legacy backups, budget/history mapping, writer drain and rollback provenance require review before installation.' }
else {
    $MigrationEvidence = (Resolve-Path -LiteralPath $MigrationEvidence).Path
    Add-Hold 'migration-review' 'Migration evidence was supplied as provenance only; this planner cannot certify semantic budget mapping or stopped-writer containment.'
}
foreach ($reason in @($stage.holds)) { Add-Hold 'retained-stage-hold' $reason }
Add-Hold 'system-acceptance' 'Protected full-tree/hardlink ACLs, independent identities, Docker pipe/backend denial, RAM mapping and native containment require actual SYSTEM evidence.'
Add-Hold 'activation-review' 'This planner has no execution mode. Review exact installation/task/state changes and the legacy coexistence decision before any activation.'

$common = @('-Python',$Python,'-Uv',$Uv,'-OperatorName',$pipeline.operator_name,'-WardenTaskName',$supervisor.warden_task,'-SupervisorTaskName',$supervisor.supervisor_task)
$previousRootArgument = if ($PreviousSupervisorDataRoot) { $PreviousSupervisorDataRoot } else { $null }
$plans = @(
    [ordered]@{id='pipeline-install';script=(Join-Path $repo 'scripts\install_pipeline_windows.ps1');arguments=@($common + @('-InstallRoot',$pipelineRoot,'-DataRoot',(Split-Path -Parent $pipeline.private_root),'-TokenFile',$pipeline.token_file,'-Slots','6','-Config',(Full-LocalPath $PipelineProposal)));prerequisites=@('migration-reviewed','protected-toolchain-and-native-contracts','configuration-valid');effect='Creates protected source and copied frozen environment; SYSTEM provisioning creates six isolated identities/layout for immutable six-chapter ownership. Reviewed max_execution_slots remains four shared concurrent native/Docker seats. Does not register daemon without explicit switch.'},
    [ordered]@{id='supervisor-install';script=(Join-Path $repo 'scripts\install_supervisor_windows.ps1');arguments=@($common + @('-InstallRoot',$supervisorRoot,'-PipelineRoot',$pipelineRoot,'-PipelineConfig',(Full-LocalPath $PipelineProposal),'-DataRoot',$supervisorData,'-PreviousDataRoot',$previousRootArgument,'-ReleaseRoot',$supervisor.release_root,'-LoginLogRoot','C:\Users\ansac\CoChem427\native-login-logs'));prerequisites=@('pipeline-provisioned','previous-budget-root-resolved','budget-migration-reviewed');effect='Creates independent copied frozen environment and acceptance snapshot; SYSTEM provision and budget migration. Missing PreviousDataRoot means this command is incomplete.'}
)
$plans += [ordered]@{id='pipeline-register-disabled';script=$plans[0].script;arguments=@($plans[0].arguments + @('-RegisterDaemon'));prerequisites=@('migration-complete','system-prerequisites-and-native-contracts-reviewed','legacy-writer-disposition-reviewed');effect='Registers the proposed SYSTEM Warden stopped and disabled, with explicit reviewed configuration. Requires administrator execution; this planner does not execute it.'}
$plans += [ordered]@{id='supervisor-register-final';script=$plans[1].script;arguments=@($plans[1].arguments + @('-Config',(Full-LocalPath $SupervisorProposal),'-RegisterSupervisor'));prerequisites=@('pipeline-register-disabled-complete','budget-state-verified','rollback-and-restart-reviewed');effect='Final privileged configure bootstraps the reviewed release pointer, preserves original task XML, changes intended Warden action, and enables intended Warden/supervisor startup. This is the explicit activation boundary, never an automatic planner step.'}
$hashes = [ordered]@{}
foreach ($path in @($StageManifest,$PipelineProposal,$SupervisorProposal,$SourceManifest,$PSCommandPath,(Join-Path $repo 'scripts\install_pipeline_windows.ps1'),(Join-Path $repo 'scripts\install_supervisor_windows.ps1'))) { $hashes[(Full-LocalPath $path)] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
$result = [ordered]@{
    schema='cochem-windows-cutover-plan/1';mode='READ_ONLY_PLAN';activation_ready=$false
    checked_at_utc=[DateTime]::UtcNow.ToString('o');host=$env:COMPUTERNAME;initial_shared_slots=4;max_shared_slots=4;provisioned_worker_identities=6;worker_identity_status='PLANNED_NOT_PROVISIONED'
    source_files_checked=$checked;source_mismatches=$sourceMismatches.ToArray();input_sha256=$hashes;native_payloads=$nativePayloads
    holds=$holds.ToArray();commands_for_review=$plans;commands_executed=@()
    required_order=@('Resolve deployment-wide sensor/migration holds and source evidence; keep unresolved native integration compatibility holds scoped to affected attempts.','Stage complete exact Python distribution, uv and native payload dependencies with protected ACLs; frozen sync must use --link-mode copy.','Back up and reconcile legacy databases, WAL, budgets, task XML, release/quarantine state and original baselines; preserve credentials and R startup.','After explicit reviewed writer/task disposition, install/provision without daemon registration; run SYSTEM doctor/containment and per-account login.','Review registration of one intended SYSTEM pipeline Warden and independent supervisor. Preserve legacy task identity; do not silently replace it.','Bounded routed preflight, native planning/coding/repair/rollback; actual four-worker queue, reboot and 48-hour observation.');migration_evidence=$MigrationEvidence
}
$json = $result | ConvertTo-Json -Depth 18
if ($Output) {
    $Output = Full-LocalPath $Output
    $excludedOutputRoots = @($programFiles,$env:ProgramData,$pipelineRoot,$supervisorRoot,$supervisorData,
        (Split-Path -Parent $pipeline.private_root),(Split-Path -Parent $pipeline.token_file),
        $supervisor.release_root,$stage.ramdisk.mount,(Join-Path $repo '.git')) +
        @($stage.protected_roots.PSObject.Properties.Value) + @($stage.migration.legacy_roots)
    if ($PreviousSupervisorDataRoot) { $excludedOutputRoots += $PreviousSupervisorDataRoot }
    foreach ($root in $excludedOutputRoots) {
        if (Is-Within $Output (Full-LocalPath $root)) { throw 'Plan evidence output must be outside protected deployment/state, credentials, legacy roots and the adopted R: volume.' }
    }
    $parent = Get-Item -LiteralPath (Split-Path -Parent $Output) -Force
    while ($null -ne $parent) {
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Plan evidence output must not traverse a reparse point.' }
        $parent = $parent.Parent
    }
    $stream = [IO.File]::Open($Output,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false)); try { $writer.WriteLine($json) } finally { $writer.Dispose() } }
    finally { $stream.Dispose() }
}
$json
