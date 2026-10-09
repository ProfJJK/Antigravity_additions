#Requires -Version 5.1
<# Config-only staging. Default is read-only, including parser-only validation.
   -Apply creates only pipeline.json with protected ACLs before its first byte.
   Existing identical configuration is preserved; different bytes are refused.
   No task, account, credential, database, corpus, RAM or daemon is changed.
   A valid config is not activation acceptance. Preserve partial files on error. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security','CimCmdlets')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$base=Join-Path $programFiles 'CoChem'
$installRoot=Join-Path $base 'Pipeline4.2.7-windows-20261006'
$guard=Join-Path $base 'InstallGuard4.2.7-windows-20261006\repository'
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$candidate=Join-Path $PSScriptRoot 'pipeline.staged.json';$destination=Join-Path $installRoot 'pipeline.json'
$candidateHash='2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
$proposalHash='f3161c436ffeb6a519dcaece7bf2ffbef61445cee6b4cb9513147bd3b49371a3'
$layoutHash='8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'
$evidence=Join-Path $base 'Native4.2.7-windows-20261006\native-contract-agy-1.3.1-followup.json'
$evidenceHash='82db0ae4d4f42c40d4265a2116a68b7f27947f5718bd35ca375e2ad87e89d3f5'
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
function Import-PinnedFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -ne $Hash){throw 'Reviewed copy helper changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed copy helper parse error.'}
        foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($f.Name -in $Names){$f.Extent.Text}}
    }finally{$stream.Dispose()}
}
function Read-HeldUtf8 {
    param($Stream)
    $Stream.Position=0;$reader=[IO.StreamReader]::new($Stream,[Text.Encoding]::UTF8,$true,4096,$true)
    try{$reader.ReadToEnd()}finally{$reader.Dispose();$Stream.Position=0}
}
function Assert-OnlyEvidenceRebind {
    param([string]$Original,[string]$Candidate)
    $old='D:\\Gdrive\\__CoChem\\GitHub-Repo\\Antigravity_additions\\docs\\evidence\\windows-2026-10-06\\native-contract-agy-1.3.1-followup.json'
    $new='C:\\Program Files\\CoChem\\Native4.2.7-windows-20261006\\native-contract-agy-1.3.1-followup.json'
    if(($Original.Split(@($old),[StringSplitOptions]::None)).Count -ne 2 -or $Original.Replace($old,$new) -cne $Candidate){throw 'Candidate must change only the single evidence path in the exact frozen proposal.'}
}
function Assert-LayoutMapping {
    param($Config,$Layout,$Accounts)
    $keys=@('slot1','slot2','slot3','slot4','slot5','slot6')
    foreach($mapping in @($Config.workers,$Config.slot_roots,$Layout.slots)){
        if(@($mapping.PSObject.Properties).Count -ne 6 -or @($mapping.PSObject.Properties.Name|Where-Object {$_ -notin $keys}).Count){throw 'Expected exactly six isolated identities.'}
    }
    if($Config.max_execution_slots -ne 4 -or $Config.private_root -cne $Layout.private_root -or $Config.operator_name -cne $Layout.operator_name){throw 'Installed layout differs from the four-slot policy.'}
    foreach($number in 1..6){
        $key="slot$number";$row=$Layout.slots.PSObject.Properties[$key].Value;$worker=$Config.workers.PSObject.Properties[$key].Value
        $account=@($Accounts|Where-Object {$_.Name -ceq "CoChem422Worker$number"})
        if($row.identity -cne "CoChem422Worker$number" -or $row.identity -cne $worker.name -or $row.credential_target -cne "CoChem422/$key" -or $row.credential_target -cne $worker.credential_target -or $row.root -cne $Config.slot_roots.PSObject.Properties[$key].Value -or $account.Count -ne 1 -or $account[0].SID -cne $row.sid){throw "Actual account/layout mismatch for $key."}
    }
}
function Assert-CodeTreeOnce {
    param([string]$Path)
    # Root ancestry is checked once. Protected parents cannot be replaced by an
    # untrusted writer; each child still receives its own ACL and handle check.
    Assert-ProtectedPath $Path
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path)
    $count=0;$deadline=[DateTime]::UtcNow.AddMinutes(4)
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    while($queue.Count){
        if(++$count -gt 50000 -or [DateTime]::UtcNow -gt $deadline){throw 'Protected code inspection exceeded its bound; no Python was executed.'}
        $item=Get-Item -LiteralPath ($queue.Dequeue()) -Force -ErrorAction Stop
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Protected code reparse entry refused.'}
        $acl=Get-Acl -LiteralPath $item.FullName
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Protected code has an untrusted owner.'}
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
            if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)){throw 'Protected code has an untrusted writer.'}
        }
        if($item.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$queue.Enqueue($child.FullName)}}else{
            # Handle metadata only, including bundled .db resources; no content read.
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()}
        }
    }
    $count
}
function Get-DaemonHolds {
    param($Folder)
    foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
        try{$task=$Folder.GetTask($name)}catch{
            $e=$_.Exception;$missing=$false
            while($null -ne $e){if($e.HResult -eq -2147024894){$missing=$true};$e=$e.InnerException}
            if($missing){continue};"Cannot establish stopped task state: $name";continue
        }
        if($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){"Daemon must remain disabled and stopped: $name"}
    }
}
function Invoke-ParserOnly {
    param([string]$Executable,[string]$Config)
    # Single-quoted Python keys survive Windows PowerShell 5.1 native argv rules.
    $code="import json,sys; from cochem_pipeline.config import load_config; c=load_config(sys.argv[1]); print(json.dumps({'parser_valid':True,'worker_identities':len(c.workers),'max_shared_slots':c.max_execution_slots,'knowledge_enabled':c.knowledge.enabled,'activation_ready':False}))"
    $output=& $Executable -I -B -c $code $Config
    if($LASTEXITCODE -ne 0){throw 'Protected configuration parser failed; activation remains held.'}
    $result=($output -join [Environment]::NewLine)|ConvertFrom-Json
    if(-not $result.parser_valid -or $result.worker_identities -ne 6 -or $result.max_shared_slots -ne 4 -or -not $result.knowledge_enabled -or $result.activation_ready){throw 'Unexpected configuration parser result.'}
    $result
}
$names=@('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory','Copy-VerifiedPayload')
foreach($definition in @(Import-PinnedFunctions (Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1') '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' $names)){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $proposal=Join-Path $guard 'config\windows\aetherdesk-427.proposed.json';Assert-ProtectedPath $proposal
    $proposalStream=Open-VerifiedFile $proposal $proposalHash (Get-Item -LiteralPath $proposal).Length;$held.Add($proposalStream)
    $candidateLength=(Get-Item -LiteralPath $candidate).Length
    $candidateStream=Open-VerifiedFile $candidate $candidateHash $candidateLength;$held.Add($candidateStream)
    $raw=Read-HeldUtf8 $candidateStream;Assert-OnlyEvidenceRebind (Read-HeldUtf8 $proposalStream) $raw
    $config=$raw|ConvertFrom-Json
    $layoutPath=Join-Path $installRoot 'windows-layout.json';Assert-ProtectedPath $layoutPath
    $layout=Read-Inventory $layoutPath $layoutHash
    Assert-LayoutMapping $config $layout @(Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' -ErrorAction Stop)
    Assert-ProtectedPath $evidence;$stream=Open-VerifiedFile $evidence $evidenceHash (Get-Item -LiteralPath $evidence).Length;$held.Add($stream)
    $targetState='ABSENT'
    if(Test-Path -LiteralPath $destination -ErrorAction Stop){Assert-ProtectedPath $destination;$stream=Open-VerifiedFile $destination $candidateHash $candidateLength;$held.Add($stream);$targetState='IDENTICAL_PRESERVED'}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    $holds=@(Get-DaemonHolds $folder)
    if($Apply -and $holds.Count){throw ($holds -join ' ')}
    $codeEntries=Assert-CodeTreeOnce $installRoot
    $pythonRoot=Join-Path $base 'Toolchain4.2.7-windows-20261006\Python312'
    $baseEntries=Assert-CodeTreeOnce $pythonRoot
    $stream=Open-VerifiedFile $python '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e' 262144;$held.Add($stream)
    # Exact frozen source/lock comparison establishes which implementation parses.
    $source=Read-Inventory (Join-Path $repo 'config\windows\aetherdesk-427.stopped-install-source.json') 'ee994719727936497b0436448a15e80dc18f11a8b108601fd43e5a62b918260b'
    foreach($file in $source.files){$path=Join-Path (Split-Path -Parent $guard) $file.destination;Assert-ProtectedPath $path;$stream=Open-VerifiedFile $path $file.sha256 $file.length;$stream.Dispose()}
    $revision=& $python -I -B -m cochem_pipeline.deployment_revision --source-repository $guard --installed-source (Join-Path $installRoot 'source')
    if($LASTEXITCODE -ne 0 -or -not (($revision -join [Environment]::NewLine)|ConvertFrom-Json).verified){throw 'Installed source revision differs; no configuration was created.'}
    $parsed=Invoke-ParserOnly $python $candidate
    if($Apply){
        $holds=@(Get-DaemonHolds $folder);if($holds.Count){throw ($holds -join ' ')}
        Assert-LayoutMapping $config (Read-Inventory $layoutPath $layoutHash) @(Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' -ErrorAction Stop)
        if($targetState -eq 'ABSENT'){
            Copy-VerifiedPayload ([pscustomobject]@{source=$candidate;destination=$destination;sha256=$candidateHash;length=$candidateLength})
            $targetState='CREATED_PROTECTED_CONFIG'
        }
        $parsed=Invoke-ParserOnly $python $destination
    }
    [ordered]@{schema='cochem-config-only-staging/1';mode=$(if($Apply){'CONFIG_STAGED'}else{'READ_ONLY_PREVIEW'});proposal_sha256=$proposalHash;candidate_sha256=$candidateHash;layout_sha256=$layoutHash;destination=$destination;destination_status=$targetState;only_policy_change='integration_hold.evidence_path rebound to identical protected capture';installed_revision_verified=$true;installed_code_entries=$codeEntries;protected_python_entries=$baseEntries;parser=$parsed;holds=$holds;credentials_accessed=$false;databases_opened=$false;knowledge_index_created=$false;tasks_changed=$false;model_jobs_executed=0;activation_ready=$false}|ConvertTo-Json -Depth 5
}finally{foreach($stream in $held){$stream.Dispose()}}
