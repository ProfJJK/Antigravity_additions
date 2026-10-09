#Requires -Version 5.1
<# Bounded metadata only. No SQLite/database/control-file content is opened.
   No tasks/processes/accounts/environment/ACLs are modified. No provider calls.
   Administrator rerun improves visibility; lack of visibility is never absence.
   Outputs a sanitized JSON report to stdout only; it does not save any file. #>
[CmdletBinding()]
param()
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$diagnosticSource=$PSCommandPath
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security','CimCmdlets')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop}
function Get-ErrorClassification {
    param($Exception)
    $e=$Exception
    while($null -ne $e){
        if($e -is [UnauthorizedAccessException] -or $e.HResult -eq -2147024891){return 'INACCESSIBLE'}
        if($e -is [Management.Automation.ItemNotFoundException] -or $e -is [IO.FileNotFoundException] -or $e -is [IO.DirectoryNotFoundException] -or $e.HResult -eq -2147024894){return 'ABSENT'}
        $e=$e.InnerException
    }
    'ERROR'
}
function Get-TextDigest {
    param([string]$Text)
    $h=[Security.Cryptography.SHA256]::Create()
    try{[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
}
function Get-PathMetadata {
    param([string]$Path)
    try{
        $absolute=[IO.Path]::GetFullPath($Path);$ancestor=$absolute;$chain=[Collections.Generic.List[string]]::new()
        while($ancestor){$chain.Add($ancestor);$parent=[IO.Directory]::GetParent($ancestor);$ancestor=if($null -eq $parent){$null}else{$parent.FullName}}
        for($n=$chain.Count-1;$n -ge 0;$n--){
            $item=Get-Item -LiteralPath $chain[$n] -Force -ErrorAction Stop
            if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){return [ordered]@{path=$absolute;status='REPARSE_REFUSED';at=$chain[$n]}}
        }
        [ordered]@{path=$absolute;status='PRESENT';kind=if($item.PSIsContainer){'directory'}else{'file'};bytes=if($item.PSIsContainer){$null}else{[long]$item.Length};creation_utc=$item.CreationTimeUtc.ToString('o');write_utc=$item.LastWriteTimeUtc.ToString('o');attributes=[long]$item.Attributes;content_read=$false}
    }catch{[ordered]@{path=$Path;status=(Get-ErrorClassification $_.Exception);error_type=$_.Exception.GetType().Name}}
}
function Get-KnownRoots {
    $fixed=@('C:\ProgramData\CoChem','C:\ProgramData\CoChem\warden','C:\ProgramData\CoChemPipeline427',
      'C:\ProgramData\CoChemSupervisor423','C:\ProgramData\CoChemSupervisor424','C:\ProgramData\CoChemSupervisor425',
      'C:\ProgramData\CoChemSupervisor426','C:\ProgramData\CoChemSupervisor427','C:\ProgramData\CoChemSupervisor427-windows-20261006',
      'D:\__CoChem\__agentic\v4.1.2','D:\__CoChem\__agentic\v4.2.0','D:\__CoChem\__agentic\v4.2.0\db')
    $discovery=[ordered]@{status='COMPLETE';root='C:\ProgramData';matching_roots=@();maximum_roots=32}
    try{
        # Enumerate immediate directory names only, never their contents here.
        $rows=@(Get-ChildItem -LiteralPath 'C:\ProgramData' -Directory -Force -Filter 'CoChem*' -ErrorAction Stop)
        if($rows.Count -gt 32){$discovery.status='BOUND_EXCEEDED'}else{$discovery.matching_roots=@($rows|ForEach-Object{$_.FullName});$fixed+=@($discovery.matching_roots)}
    }catch{$discovery.status=Get-ErrorClassification $_.Exception;$discovery.error_type=$_.Exception.GetType().Name}
    [pscustomobject]@{roots=@($fixed|Sort-Object -Unique);discovery=$discovery}
}
function Get-TaskMetadata {
    param($Folder,[string]$Name)
    try{
        $t=$Folder.GetTask($Name);$d=$t.Definition
        [ordered]@{name=$Name;status='PRESENT';state=[int]$t.State;enabled=[bool]$t.Enabled;instances=[int]$t.GetInstances(0).Count;last_result=[long]$t.LastTaskResult;
          principal=[string]$d.Principal.UserId;logon_type=[int]$d.Principal.LogonType;run_level=[int]$d.Principal.RunLevel;triggers=[int]$d.Triggers.Count;actions=[int]$d.Actions.Count;
          definition_sha256=(Get-TextDigest ([string]$t.Xml));definition_text_published=$false;modified=$false}
    }catch{[ordered]@{name=$Name;status=(Get-ErrorClassification $_.Exception);error_type=$_.Exception.GetType().Name}}
}
function Get-LegacyProcessClassification {
    param([string]$CommandLine)
    $line=$CommandLine.Replace('/','\').ToLowerInvariant()
    if($line.Contains('d:\__cochem\__agentic\v4.2.0\auto_architect_mcp.py')){return 'LEGACY_V420_MCP_INGRESS'}
    $root=$line.Contains('d:\__cochem\__agentic\v4.1.2') -or $line.Contains('d:\__cochem\__agentic\v4.2.0')
    if(-not $root){return $null}
    if($line.Contains('cochem.warden.mcp_server')){return 'LEGACY_VM_WARDEN_ENTRYPOINT'}
    if($line.Contains('cochem_warden_oracle.py')){return 'LEGACY_ORACLE_ENTRYPOINT'}
    if($line.Contains('start_pipeline.py')){return 'LEGACY_PIPELINE_LAUNCHER'}
    if($line.Contains('watchdog') -or $line.Contains('worker_daemon')){return 'LEGACY_WORKER_OR_WATCHDOG_CANDIDATE'}
    return 'OTHER_PROCESS_REFERENCING_KNOWN_LEGACY_ROOT'
}
function Get-LegacyProcessMetadata {
    $output=@();$unknown=0
    try{
        $rows=@(Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='cmd.exe'" -OperationTimeoutSec 15 -ErrorAction Stop)
        if($rows.Count -gt 256){return [ordered]@{status='BOUND_EXCEEDED';maximum_candidates=256;matched=@();visibility_incomplete=$true}}
        foreach($p in $rows){
            if([string]::IsNullOrWhiteSpace([string]$p.CommandLine)){$unknown++;continue}
            $classification=Get-LegacyProcessClassification ([string]$p.CommandLine)
            if($null -ne $classification){$output+=[ordered]@{pid=[int]$p.ProcessId;parent_pid=[int]$p.ParentProcessId;created_utc=if($null -ne $p.CreationDate){$p.CreationDate.ToUniversalTime().ToString('o')}else{$null};executable=[string]$p.ExecutablePath;classification=$classification;command_line_sha256=(Get-TextDigest ([string]$p.CommandLine));command_line_published=$false}}
        }
        [ordered]@{status='CAPTURED_CANDIDATE_METADATA';candidates=$rows.Count;unreadable_command_lines=$unknown;matched=$output;visibility_incomplete=($unknown -gt 0);live_database_writer_proven=$false;scope='Fixed python/pythonw/cmd image names and known-root string classification only; no handles, process memory, credentials, database connections or open-file queries.'}
    }catch{[ordered]@{status=(Get-ErrorClassification $_.Exception);error_type=$_.Exception.GetType().Name;matched=@();visibility_incomplete=$true}}
}
function Invoke-ContinuityInspection {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $known=Get-KnownRoots;$paths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $controls=@('supervisor.db','supervisor.db-wal','supervisor.db-shm','component-recovery.db','component-recovery.db-wal','component-recovery.db-shm','active-release.json','repair-quarantine.json','release-journal.json','budget-upgrade.json','budget-upgrade-preserved.json')
    foreach($root in $known.roots){
        $null=$paths.Add($root)
        foreach($sub in @($root,(Join-Path $root 'private'))){foreach($name in $controls){$null=$paths.Add((Join-Path $sub $name))}}
    }
    foreach($path in @('D:\__CoChem\__agentic\v4.1.2\job_board.db','D:\__CoChem\__agentic\v4.1.2\knowledge_index.db','D:\__CoChem\__agentic\v4.2.0\job_board.db','D:\__CoChem\__agentic\v4.2.0\knowledge_index.db','D:\__CoChem\__agentic\v4.2.0\db\job_board.db','D:\__CoChem\__agentic\v4.2.0\db\oracle_tracking.db','D:\__CoChem\__agentic\v4.2.0\db\wikirag.db')){$null=$paths.Add($path)}
    $metadata=@();foreach($path in ($paths|Sort-Object)){$metadata+=@(Get-PathMetadata $path)}
    $names=@('CoChemHostWarden_V412','CoChem-4.2.3-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.7-Warden');$tasks=@()
    try{$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');foreach($name in $names){$tasks+=@(Get-TaskMetadata $folder $name)}}catch{$tasks=@([ordered]@{status=(Get-ErrorClassification $_.Exception);error_type=$_.Exception.GetType().Name;scope='Task Scheduler connection'})}
    [ordered]@{schema='cochem-legacy-continuity-metadata/1';captured_utc=[DateTime]::UtcNow.ToString('o');host=$env:COMPUTERNAME;administrator=$admin;system=($identity.User.Value -eq 'S-1-5-18');
      source_sha256=(Get-FileHash -LiteralPath $diagnosticSource -Algorithm SHA256).Hash.ToLowerInvariant();source_hash_scope='This diagnostic script only';
      discovery=$known.discovery;metadata=$metadata;tasks=$tasks;processes=(Get-LegacyProcessMetadata);database_contents_opened=$false;original_file_contents_read=$false;original_files_written=$false;tasks_changed=$false;processes_changed=$false;credential_stores_read=$false;
      limitations=@('Only fixed known roots and immediate discovered ProgramData CoChem roots, at root/private control locations; no recursive disk scan.','Absence is location-specific, never zero spend or proof of no other ledger.','Process command-line classification is not proof of current database handles, quiescence or cross-database consistency.','Metadata cannot reconstruct paid generation/review charges or release an unresolved authority hold.');remaining_legacy_allowance=$null;activation_ready=$false}
}
if($MyInvocation.InvocationName -ne '.'){
    if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell5.1 on AETHERDESK.'}
    Invoke-ContinuityInspection|ConvertTo-Json -Depth 8
}
