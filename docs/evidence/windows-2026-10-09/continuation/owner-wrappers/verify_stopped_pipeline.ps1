#Requires -Version 5.1
<# Read-only postinstall metadata and revision check. Never reads controller
token/credential/database contents or changes ACLs/tasks/processes. Run after the
administrator command finishes. Optional revision execution uses only protected
installed Python and the exact installed verifier after custody checks pass. #>
[CmdletBinding()]
param([switch]$RunRevisionCheck,[ValidateRange(1,600)][int]$TreeDeadlineSeconds=30)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
if($PSVersionTable.PSEdition -ne 'Desktop'){throw 'Use Windows PowerShell 5.1.'}
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security','CimCmdlets')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$programFiles='C:\Program Files';$base=Join-Path $programFiles 'CoChem'
$pipeline=Join-Path $base 'Pipeline4.2.7-windows-20261006';$guard=Join-Path $base 'InstallGuard4.2.7-windows-20261006';$resume=Join-Path $base 'InstallResume4.2.7-windows-20261006'
function Path-Metadata {
    param([string]$Path)
    try{$item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop;[ordered]@{path=$Path;status='METADATA_VISIBLE';directory=$item.PSIsContainer;attributes=[string]$item.Attributes;last_write_utc=$item.LastWriteTimeUtc.ToString('o')}}
    catch{[ordered]@{path=$Path;status=$(if($_.Exception -is [UnauthorizedAccessException]){'INACCESSIBLE'}elseif($_.CategoryInfo.Category -eq 'ObjectNotFound'){'MISSING'}else{'READ_FAILED'});hresult=$_.Exception.HResult}}
}
function Read-ReceiptMetadata {
    param([string]$Path)
    try{
        Assert-ProtectedPath $Path
        if((Get-Item -LiteralPath $Path).Length -gt 4096){throw 'Receipt size exceeded.'}
        $r=Get-Content -LiteralPath $Path -Raw -Encoding UTF8|ConvertFrom-Json
        [ordered]@{path=$Path;status='READ';sha256=(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant();schema=$r.schema;nonce=$r.nonce;system_sid=$r.system_sid;result=$r.status;local_accounts_checked=$r.local_accounts_checked;credential_targets_checked=$r.credential_targets_checked;credential_blobs_dereferenced=$r.credential_blobs_dereferenced;checked_at_utc=$r.checked_at_utc}
    }catch{[ordered]@{path=$Path;status='UNAVAILABLE_OR_UNVERIFIED';hresult=$_.Exception.HResult}}
}
function Inspect-CodeTree {
    param([string]$Path)
    $visited=0;$checkedFiles=0;$deadline=[DateTime]::UtcNow.AddSeconds($TreeDeadlineSeconds)
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path)
    try{
        while($queue.Count){
            if([DateTime]::UtcNow -gt $deadline -or $visited -ge 30000){return [ordered]@{root=$Path;status='INCOMPLETE_BOUNDED';entries=$visited;files=$checkedFiles;remaining=$queue.Count}}
            $item=Get-Item -LiteralPath ($queue.Dequeue()) -Force -ErrorAction Stop;$visited++
            Assert-ProtectedPath $item.FullName
            if($item.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$queue.Enqueue($child.FullName)}}else{
                # Code dependencies may contain .db resources. This read-access
                # handle is used only for link count/final-path metadata: Check
                # performs no stream reads, SQLite opens or database queries.
                # Traversal is confined to this protected installation tree.
                $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
                try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()};$checkedFiles++
            }
        }
        [ordered]@{root=$Path;status='CUSTODY_VERIFIED';entries=$visited;files=$checkedFiles}
    }catch{[ordered]@{root=$Path;status='UNVERIFIED';entries=$visited;files=$checkedFiles;hresult=$_.Exception.HResult;reason=$_.Exception.Message}}
}
# Import only pinned read-only custody functions; never invoke the copy helper.
$helper=Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1'
$stream=[IO.File]::Open($helper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{$sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()};if($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'){throw 'Frozen custody helper changed.'};$stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors);if($errors.Count){throw 'Custody helper parse error.'};foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($f.Name -in @('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory')){. ([scriptblock]::Create($f.Extent.Text))}}}finally{$stream.Dispose()}
Initialize-FileIdentity
$inventory=Read-Inventory (Join-Path $repo 'config\windows\aetherdesk-427.stopped-install-source.json') 'ee994719727936497b0436448a15e80dc18f11a8b108601fd43e5a62b918260b'
$sourceStatus='VERIFIED';$verified=0
try{foreach($f in $inventory.files){$path=Join-Path $guard $f.destination;Assert-ProtectedPath $path;$stream=Open-VerifiedFile $path $f.sha256 $f.length;$stream.Dispose();$verified++}}catch{$sourceStatus='UNVERIFIED'}
$layoutStatus='UNAVAILABLE_OR_UNVERIFIED';$slots=@();$layoutPath=Join-Path $pipeline 'windows-layout.json'
try{Assert-ProtectedPath $layoutPath;if((Get-Item -LiteralPath $layoutPath).Length -gt 65536){throw 'Layout size exceeded.'};$layout=Get-Content -LiteralPath $layoutPath -Raw -Encoding UTF8|ConvertFrom-Json
    $accounts=@(Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' -ErrorAction Stop)
    foreach($number in 1..6){$slot=$layout.slots.PSObject.Properties["slot$number"].Value;$expected="CoChem422Worker$number";$account=@($accounts|Where-Object {$_.Name -eq $expected});$slots+=@([ordered]@{slot="slot$number";identity=$slot.identity;sid=$slot.sid;root=$slot.root;credential_target=$slot.credential_target;account_metadata_matches=($slot.identity -eq $expected -and $account.Count -eq 1 -and $account[0].SID -eq $slot.sid);path_metadata=(Path-Metadata $slot.root)})}
    $layoutStatus=if(@($layout.slots.PSObject.Properties).Count -eq 6 -and -not @($slots|Where-Object {-not $_.account_metadata_matches}).Count){'SIX_IDENTITIES_MATCH'}else{'MISMATCH'}
}catch{$layoutStatus='UNAVAILABLE_OR_UNVERIFIED'}
$taskMetadata=@();$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
foreach($name in @('CoChem-4.2.7-Install-Preflight','CoChem-4.2.7-Install-Resume-Preflight','CoChem-4.2.2-Provision','CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChemHostWarden_V412','Mount_CoChem_RAMDisk')){try{$t=$folder.GetTask($name);$taskMetadata+=@([ordered]@{name=$name;status='READ';principal=$t.Definition.Principal.UserId;enabled=$t.Enabled;state=$t.State;last_result=$t.LastTaskResult;last_run=$t.LastRunTime;trigger_count=$t.Definition.Triggers.Count;action_count=$t.Definition.Actions.Count})}catch{$e=$_.Exception;$missing=$false;while($null -ne $e){if($e.HResult -eq -2147024894){$missing=$true};$e=$e.InnerException};$taskMetadata+=@([ordered]@{name=$name;status=$(if($missing){'MISSING'}else{'INACCESSIBLE_OR_READ_FAILED'});hresult=$_.Exception.HResult})}}
$custody=Inspect-CodeTree $pipeline
$revision=[ordered]@{status='NOT_REQUESTED'}
if($RunRevisionCheck){
    if($sourceStatus -ne 'VERIFIED' -or $custody.status -ne 'CUSTODY_VERIFIED'){$revision=[ordered]@{status='HELD_UNVERIFIED_CUSTODY'}}else{
        $python=Join-Path $pipeline '.venv\Scripts\python.exe'
        $expectedVerifier=@($inventory.files|Where-Object {$_.destination -eq 'repository/src/cochem_pipeline/deployment_revision.py'})[0]
        $verifier=Join-Path $pipeline '.venv\Lib\site-packages\cochem_pipeline\deployment_revision.py'
        $stream=Open-VerifiedFile $verifier $expectedVerifier.sha256 $expectedVerifier.length;$stream.Dispose()
        $raw=& $python -I -B -m cochem_pipeline.deployment_revision --source-repository (Join-Path $guard 'repository') --installed-source (Join-Path $pipeline 'source')
        if($LASTEXITCODE -eq 0){$revision=[ordered]@{status='VERIFIED';report=(($raw -join [Environment]::NewLine)|ConvertFrom-Json)}}else{$revision=[ordered]@{status='FAILED';exit_code=$LASTEXITCODE}}
    }
}
[ordered]@{schema='cochem-stopped-postinstall-readonly/1';checked_at_utc=[DateTime]::UtcNow.ToString('o');source_snapshot=$sourceStatus;source_files_verified=$verified;receipts=@((Read-ReceiptMetadata (Join-Path $guard 'identity-precheck.json')),(Read-ReceiptMetadata (Join-Path $resume 'identity-precheck.json')));layout_status=$layoutStatus;slots=$slots;private_directory_metadata=(Path-Metadata 'C:\ProgramData\CoChemPipeline427\private');private_contents_read=$false;token_contents_read=$false;credential_store_accessed=$false;tasks=$taskMetadata;runtime_custody=$custody;installed_revision=$revision;system_acceptance=$false;activation_ready=$false}|ConvertTo-Json -Depth 8
