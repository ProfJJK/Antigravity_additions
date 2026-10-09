#Requires -Version 5.1
<# Read-only public code/receipt binding capture after the owner installs r2.
   No Apply, knowledge service, doctor, provisioning, database or private-state
   access. Prior full-tree custody is recorded separately from current checks. #>
[CmdletBinding()]
param()
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Assert-R2Receipt {
    param($Receipt)
    if($Receipt.schema -ne 'cochem-stopped-runtime-update/1' -or $Receipt.mode -ne 'FRESH_STOPPED_RUNTIME_READY' -or
       $Receipt.target_root -cne $script:install -or $Receipt.source_manifest_sha256 -ne $script:manifestHash -or
       $Receipt.configuration_sha256 -ne $script:configHash -or $Receipt.source_files -ne 166 -or @($Receipt.holds).Count -or
       $Receipt.accounts_provisioned -ne 0 -or $Receipt.tasks_changed -ne 0 -or $Receipt.credentials_modified -ne $false -or
       $Receipt.databases_modified -ne $false -or $Receipt.ram_modified -ne $false -or $Receipt.pipeline_started -ne $false -or
       $Receipt.activation_ready -ne $false -or $Receipt.verification.revision.verified -ne $true -or
       $Receipt.verification.configuration_parsed -ne $true -or $Receipt.verification.ram_workspace_root -cne 'R:\CoChem427-windows-20261007' -or
       $Receipt.verification.no_system_or_model_execution -ne $true -or [int]$Receipt.verified_entries -lt 10000){
        throw 'A successful complete frozen r2 installation receipt is required.'}
}

function Assert-R2Venv {
    param([string]$Text)
    $values=@{}
    foreach($line in ($Text -split "`r?`n")){
        if(-not $line.Trim()){continue}
        if($line -notmatch '^([^=]+?)\s*=\s*(.*)$'){throw 'Unexpected pyvenv record.'}
        $key=$matches[1].Trim().ToLowerInvariant()
        if($values.ContainsKey($key)){throw 'Duplicate pyvenv key.'}
        $values[$key]=$matches[2].Trim()
    }
    if($values['home'] -cne $script:baseRoot -or $values['include-system-site-packages'] -cne 'false'){
        throw 'Venv does not bind the protected base interpreter exclusively.'}
    if($values.ContainsKey('executable') -and $values['executable'] -cne (Join-Path $script:baseRoot 'python.exe')){throw 'Venv executable binding differs.'}
    if($values.ContainsKey('version_info')){$version=$values['version_info']}else{$version=$values['version']}
    if($version -cne '3.12.13'){throw 'Unexpected protected Python version.'}
}

function Read-PublicBinding {
    param([string]$Path,[string]$Expected='',[long]$Limit=16777216)
    Assert-ProtectedPath $Path
    $item=Get-Item -LiteralPath $Path -Force
    if($item.PSIsContainer -or $item.Length -gt $Limit){throw 'Public binding exceeds its ordinary-file bound.'}
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        [CoChemStagedFileIdentity]::Check($stream,$Path)
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($Expected -and $hash -ne $Expected){throw 'Pinned public installation bytes differ.'}
        $stream.Position=0
        $script:held.Add($stream)
        return [pscustomobject]@{path=$Path;length=$stream.Length;sha256=$hash;stream=$stream}
    }catch{$stream.Dispose();throw}
}

function Read-BindingText {
    param($Binding)
    $Binding.stream.Position=0
    $reader=[IO.StreamReader]::new($Binding.stream,[Text.Encoding]::UTF8,$true,4096,$true)
    try{return $reader.ReadToEnd()}finally{$reader.Dispose();$Binding.stream.Position=0}
}

function Invoke-InterpreterIdentity {
    param([string]$Executable)
    # -S skips site processing and all installed .pth files. Only stdlib identity
    # is measured; no pipeline module or knowledge state is imported/opened.
    $code="import json,sys; print(json.dumps({'executable':sys.executable,'base_prefix':sys.base_prefix,'base_executable':sys._base_executable,'version':'.'.join(map(str,sys.version_info[:3]))}))"
    $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$Executable
    $start.Arguments='-I -B -S -c "'+$code+'"';$start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true;$start.WorkingDirectory=$script:install
    $process=[Diagnostics.Process]::new();$process.StartInfo=$start
    try{
        if(-not $process.Start()){throw 'Interpreter identity process did not start.'}
        $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
        if(-not $process.WaitForExit(30000)){$process.Kill();if(-not $process.WaitForExit(5000)){throw 'Owned identity process did not terminate.'};throw 'Interpreter identity exceeded its bound.'}
        $raw=$stdout.GetAwaiter().GetResult();$errorText=$stderr.GetAwaiter().GetResult()
        if($process.ExitCode -ne 0 -or $errorText.Length -ne 0 -or $raw.Length -gt 4096){throw 'Interpreter identity failed its bounded output contract.'}
        $value=$raw|ConvertFrom-Json
        if($value.executable -cne $Executable -or $value.base_prefix -cne $script:baseRoot -or
           $value.base_executable -cne (Join-Path $script:baseRoot 'python.exe') -or $value.version -cne '3.12.13'){
            throw 'Actual interpreter identity differs from protected venv/base bindings.'}
        return $value
    }finally{$process.Dispose()}
}

if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$programFiles='C:\Program Files';$install=Join-Path $programFiles 'CoChem\Pipeline4.2.7-windows-20261007-r2'
$baseRoot=Join-Path $programFiles 'CoChem\Toolchain4.2.7-windows-20261006\Python312'
$manifestHash='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$copy='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$definitions=[IO.File]::Open($copy,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$hash=[BitConverter]::ToString($sha.ComputeHash($definitions)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'){throw 'Reviewed custody functions changed.'}
    $definitions.Position=0;$reader=[IO.StreamReader]::new($definitions,[Text.Encoding]::UTF8,$true,4096,$true)
    try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw 'Custody function parse failure.'}
    foreach($function in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Assert-ProtectedPath','Initialize-FileIdentity')},$true)){
        . ([scriptblock]::Create($function.Extent.Text))}
    Initialize-FileIdentity
    Assert-ProtectedPath $install;Assert-ProtectedPath $baseRoot
    $receipt=Read-PublicBinding (Join-Path $install 'install-after.json') '' 1048576
    $after=(Read-BindingText $receipt)|ConvertFrom-Json;Assert-R2Receipt $after
    $manifest=Read-PublicBinding (Join-Path $install 'source-manifest.json') $manifestHash
    $inventory=(Read-BindingText $manifest)|ConvertFrom-Json
    if($inventory.schema -ne 'cochem-stopped-runtime-source/1' -or $inventory.target_root -cne $install -or
       $inventory.complete_source_freeze -ne $true -or @($inventory.files).Count -ne 166){throw 'Frozen r2 source inventory differs.'}
    $config=Read-PublicBinding (Join-Path $install 'pipeline.json') $configHash
    $layout=Read-PublicBinding (Join-Path $install 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
    $venv=Read-PublicBinding (Join-Path $install '.venv\pyvenv.cfg') '' 65536
    Assert-R2Venv (Read-BindingText $venv)
    $base=Read-PublicBinding (Join-Path $baseRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
    $python=Read-PublicBinding (Join-Path $install '.venv\Scripts\python.exe') '' 16777216
    $assets=@();$expected=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($row in $inventory.files){
        $source=Read-PublicBinding (Join-Path (Join-Path $install 'source') $row.relative) $row.sha256
        if($source.length -ne $row.length){throw 'Frozen source length differs.'}
        if($row.relative -match '^src/(cochem_pipeline|cochem_mcp|cochem_supervisor)/.+\.(py|md|json|xml)$'){
            $key=$row.relative.Substring(4);$null=$expected.Add($key)
            $asset=Read-PublicBinding (Join-Path (Join-Path $install '.venv\Lib\site-packages') $key) $row.sha256
            if($asset.length -ne $row.length){throw 'Installed source asset length differs.'}
            $assets+=@([ordered]@{relative=$key;length=$asset.length;sha256=$asset.sha256})
        }
    }
    # Enumerate only the three application packages, not the 20k dependency tree.
    $found=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $site=Join-Path $install '.venv\Lib\site-packages'
    $entries=0;$walk=[Diagnostics.Stopwatch]::StartNew()
    foreach($package in @('cochem_pipeline','cochem_mcp','cochem_supervisor')){
        $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue((Join-Path $site $package))
        while($queue.Count){
            $directory=$queue.Dequeue();Assert-ProtectedPath $directory
            foreach($entry in Get-ChildItem -LiteralPath $directory -Force){
                if(++$entries -gt 5000 -or $walk.Elapsed.TotalSeconds -gt 120){throw 'Application package inventory exceeds its bound.'}
                if($entry.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Package has a reparse entry.'}
                if($entry.PSIsContainer){if($entry.Name -ne '__pycache__'){$queue.Enqueue($entry.FullName)}}
                elseif($entry.Extension -in @('.py','.md','.json','.xml')){$null=$found.Add($entry.FullName.Substring($site.Length+1).Replace('\','/'))}
            }
        }
    }
    if(-not $found.SetEquals($expected) -or $assets.Count -ne $after.verification.revision.files){throw 'Installed source asset set differs from frozen source and receipt.'}
    $identity=Invoke-InterpreterIdentity $python.path
    [ordered]@{schema='cochem-r2-independent-bindings/1';captured_utc=[DateTime]::UtcNow.ToString('o');status='R2_PUBLIC_BINDINGS_VERIFIED';installation_root=$install;install_after_sha256=$receipt.sha256;source_manifest_sha256=$manifest.sha256;pipeline_config_sha256=$config.sha256;layout_sha256=$layout.sha256;pyvenv_sha256=$venv.sha256;venv_python_sha256=$python.sha256;base_python_sha256=$base.sha256;interpreter_identity=$identity;source_files_verified=166;installed_assets=$assets;installed_revision_from_receipt=$after.verification.revision;custody=[ordered]@{prior_installer_full_tree_entries=$after.verified_entries;current_scope='Read file-handle/hash/ACL custody for listed source/application assets and public bindings plus their ancestors; no repeated dependency-wide scan.';dependency_tree_rescanned=$false};knowledge_service_invoked=$false;private_state_opened=$false;databases_opened=$false;models_executed=0;pipeline_started=$false;activation_ready=$false}|ConvertTo-Json -Depth 10
}finally{foreach($stream in $held){$stream.Dispose()};$definitions.Dispose()}
