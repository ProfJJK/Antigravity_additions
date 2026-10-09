#Requires -Version 5.1
<# Read-only projection of three exact existing receipts. Default preview never opens
private receipts. -Inspect requires owner Administrator, emits one sanitized JSON,
and performs no Docker/API/task/DB/credential/model operation or persistent write. #>
[CmdletBinding()]
param([switch]$Inspect)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Initialize-ReceiptIdentity {
    if('CoChemTimingReceiptIdentity' -as [type]){return}
    Add-Type -TypeDefinition @"
using System; using System.IO; using System.Runtime.InteropServices; using System.Text; using Microsoft.Win32.SafeHandles;
public static class CoChemTimingReceiptIdentity {
 [StructLayout(LayoutKind.Sequential)] struct Info { public uint attributes; public System.Runtime.InteropServices.ComTypes.FILETIME created,accessed,written; public uint volume,sizeHigh,sizeLow,links,indexHigh,indexLow; }
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle h,out Info i);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder p,uint n,uint f);
 public static void Check(FileStream s,string expected) { Info i; if(!GetFileInformationByHandle(s.SafeFileHandle,out i) || i.links!=1 || (i.attributes&0x400)!=0) throw new IOException("Receipt identity refused");
 var p=new StringBuilder(32768);uint n=GetFinalPathNameByHandle(s.SafeFileHandle,p,32768,0);if(n==0||n>=32768)throw new IOException("Receipt path unavailable");
 var actual=p.ToString();if(actual.StartsWith(@"\\?\"))actual=actual.Substring(4);if(!String.Equals(actual,Path.GetFullPath(expected),StringComparison.OrdinalIgnoreCase))throw new IOException("Receipt alias refused"); }
}
"@
}
function Get-ByteHash {param([byte[]]$Bytes)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{[BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
}
function Open-TimingControl {param([string]$Path)
    if($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or [IO.Path]::GetFullPath($Path) -cne $Path){throw 'RECEIPT_PATH_REFUSED'}
    $item=Get-Item -LiteralPath $Path -Force
    if($item -isnot [IO.FileInfo]){throw 'RECEIPT_FILE_REQUIRED'}
    $current=$item
    while($null -ne $current){
        if($current.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'RECEIPT_REPARSE_REFUSED'}
        $current=if($current -is [IO.DirectoryInfo]){$current.Parent}else{$current.Directory}
    }
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        [CoChemTimingReceiptIdentity]::Check($stream,$Path)
        if($stream.Length -le 0 -or $stream.Length -gt 1048576){throw 'RECEIPT_SIZE_REFUSED'}
        $raw=[byte[]]::new([int]$stream.Length);$at=0
        while($at -lt $raw.Length){$count=$stream.Read($raw,$at,$raw.Length-$at);if($count -le 0){throw 'RECEIPT_SHORT_READ'};$at+=$count}
        [CoChemTimingReceiptIdentity]::Check($stream,$Path)
        $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
        $held.Add($stream)
        [pscustomobject]@{Text=$text;Sha256=(Get-ByteHash $raw);Length=$raw.Length}
    }catch{$stream.Dispose();throw}
}
function Read-PublicTiming {param($Control)
    if($Control.Sha256 -cne '2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74'){throw 'PUBLIC_COMMITMENT_DIFFERENT'}
    $v=$Control.Text|ConvertFrom-Json
    if($v.schema -cne 'cochem-docker-physical-acceptance/2' -or $v.status -cne 'DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_SLA_HELD' -or
       $v.helper_sha256 -cne '157549c5806e5486085d0d941ca619c5218e668653309ce6bd55dc75eb4a944f' -or
       $v.runtime_root -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3' -or
       $v.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
       $v.foundation_receipt_sha256 -cne '9aea1e8f382ea8600cbd75d679f76acadc05da32774d9d8742cad08967ce1b41' -or
       $v.nonce -cnotmatch '^[a-f0-9]{32}$' -or @($v.executions).Count -ne 2){throw 'PUBLIC_RECEIPT_DIFFERENT'}
    foreach($key in @('physical_boundary_verified','cleanup_verified','final_physical_census_empty','registry_owner_preserved','ram_ledger_preserved')){Assert-Boolean $v.$key $true}
    foreach($key in @('startup_sla_met','activation_ready','automatic_retry_allowed','chapter06_coding_workflow_tested')){Assert-Boolean $v.$key $false}
    if($v.shared_capacity -ne 4 -or $v.prepared_count -ne 2 -or $v.native_occupancy -ne 0 -or $v.native_model_jobs_executed -ne 0){throw 'PUBLIC_SCOPE_DIFFERENT'}
    $v
}
function Read-PrivateTiming {param($Control,[string]$PayloadHash)
    # The producer hashes canonical JSON before adding receipt_sha256, then
    # writes canonical JSON including that member. Do not confuse payload and
    # raw file digests or reserialize floating point values through .NET.
    $pattern=',"receipt_sha256":"'+$PayloadHash+'"'
    $matches=[regex]::Matches($Control.Text,$pattern)
    if($matches.Count -ne 1){throw 'PRIVATE_COMMITMENT_MEMBER_DIFFERENT'}
    $m=$matches[0];$unsigned=$Control.Text.Remove($m.Index,$m.Length)
    if((Get-ByteHash ([Text.Encoding]::UTF8.GetBytes($unsigned))) -cne $PayloadHash){throw 'PRIVATE_PAYLOAD_COMMITMENT_DIFFERENT'}
    $v=$Control.Text|ConvertFrom-Json
    if($v.receipt_sha256 -cne $PayloadHash){throw 'PRIVATE_TOP_LEVEL_COMMITMENT_DIFFERENT'}
    $v
}
function Assert-Boolean {param($Value,[bool]$Expected)
    if($Value -isnot [bool] -or $Value -ne $Expected){throw 'BOOLEAN_PROVENANCE_DIFFERENT'}
}
function Get-FiniteNumber {param($Value)
    if($Value -isnot [int] -and $Value -isnot [long] -and $Value -isnot [double] -and $Value -isnot [decimal]){throw 'TIMING_NUMBER_REQUIRED'}
    $n=[double]$Value;if([double]::IsNaN($n) -or [double]::IsInfinity($n)){throw 'TIMING_NONFINITE'};$n
}
function Assert-Close {param([double]$Actual,[double]$Expected)
    if([double]::IsNaN($Expected) -or [double]::IsInfinity($Expected) -or [Math]::Abs($Actual-$Expected) -gt 0.000001){throw 'TIMING_ALGEBRA_DIFFERENT'}
}
function Get-TimingProjection {param($Receipt,$Public,[string]$Phase,[string]$PayloadHash,[string]$FileHash)
    $matches=@($Public.executions|Where-Object{$_.phase -ceq $Phase});if($matches.Count -ne 1){throw 'PUBLIC_PHASE_DIFFERENT'};$p=$matches[0]
    if($Receipt.schema_version -ne 1 -or $Receipt.kind -cne 'docker-test-execution' -or
       $Receipt.policy_sha256 -cne 'e2824d479a37469146ee6beeadc944bb77e531fb478acad0cda457fd405b8114' -or
       $Receipt.image_id -cne 'sha256:d9d3e8644b6fc407c7d5ee7151fcb22470d74b8c14a4e6ef8cf8e9acb62877a2' -or
       $Receipt.receipt_sha256 -cne $PayloadHash -or $p.receipt_sha256 -cne $PayloadHash -or
       $Receipt.container_creation_path -cne 'background_pool_replenishment' -or $Receipt.container_id -cnotmatch '^[a-f0-9]{64}$' -or
       (Get-ByteHash ([Text.Encoding]::UTF8.GetBytes($Receipt.container_id))) -cne $p.container_id_sha256 -or
       $Receipt.source.sha256 -cne $p.source_sha256 -or $Receipt.job_id -cne ('windows-docker-physical-'+$Phase) -or
       $Receipt.attempt_id -cne ($Receipt.job_id+'-'+$Public.nonce)){throw 'PRIVATE_PROVENANCE_DIFFERENT'}
    foreach($key in @('warm_pool_used','cleanup_verified','source_verified','input_source_verified','test_cycle_deadline_met','startup_clock_verified')){Assert-Boolean $Receipt.$key $true}
    Assert-Boolean $Receipt.quarantine_required $false
    Assert-Boolean $Receipt.passed ($Phase -ceq 'green')
    if(($Phase -ceq 'red' -and $Receipt.failure_category -cne 'tests_failed') -or ($Phase -ceq 'green' -and $null -ne $Receipt.failure_category)){throw 'TEST_OUTCOME_DIFFERENT'}
    $numbers=[ordered]@{}
    foreach($key in @('prepared_at','preparation_seconds','request_started_at','reservation_started_at','execution_started_at','started_at','finished_at','queue_wait_seconds','handoff_wait_seconds','container_startup_seconds','execution_startup_seconds','startup_seconds','startup_sla_seconds','test_cycle_seconds','test_cycle_limit_seconds')){
        $numbers[$key]=Get-FiniteNumber $Receipt.$key;if($numbers[$key] -lt 0){throw 'NEGATIVE_TIMING'}
    }
    $n=$numbers
    if($n.prepared_at -le 0 -or $n.prepared_at -gt $n.reservation_started_at -or $n.request_started_at -gt $n.reservation_started_at -or
       $n.reservation_started_at -gt $n.execution_started_at -or $n.execution_started_at -gt $n.finished_at -or
       $n.container_startup_seconds -gt $n.execution_startup_seconds -or $n.execution_started_at+$n.execution_startup_seconds -gt $n.finished_at+0.000001){throw 'TIMESTAMP_ORDER_DIFFERENT'}
    Assert-Close $n.started_at $n.execution_started_at
    Assert-Close $n.queue_wait_seconds ($n.reservation_started_at-$n.request_started_at)
    Assert-Close $n.handoff_wait_seconds ($n.execution_started_at-$n.reservation_started_at)
    Assert-Close $n.startup_seconds ($n.execution_started_at-$n.request_started_at+$n.execution_startup_seconds)
    Assert-Close $n.startup_seconds (Get-FiniteNumber $p.startup_seconds)
    Assert-Close $n.test_cycle_seconds (Get-FiniteNumber $p.test_cycle_seconds)
    if($n.startup_sla_seconds -ne 1.5 -or $n.test_cycle_limit_seconds -ne 30 -or $n.test_cycle_seconds -gt 30){throw 'TIMING_LIMIT_DIFFERENT'}
    Assert-Boolean $Receipt.startup_sla_met ($n.startup_seconds -le 1.5)
    Assert-Boolean $p.startup_sla_met $Receipt.startup_sla_met
    $reason=if($n.startup_seconds -gt 1.5){'request_to_test_ready_exceeded_1_5_seconds_including_queue_and_handoff'}else{$null}
    if($Receipt.startup_violation_reason -cne $reason){throw 'STARTUP_REASON_DIFFERENT'}
    [ordered]@{phase=$Phase;receipt_payload_sha256=$PayloadHash;receipt_file_sha256=$FileHash;policy_sha256=$Receipt.policy_sha256;
        warm_single_use_provenance_verified=$true;startup_clock_verified=$true;cleanup_verified=$true;test_cycle_deadline_met=$true;
        startup_sla_met=$Receipt.startup_sla_met;optimization_target_missed=($n.startup_seconds -gt 1.5);startup_violation_reason=$reason;timings=$numbers}
}
function Invoke-ExistingTimingInspection {param($Public)
    $rows=@();$identifiers=@()
    foreach($item in @(
        @{phase='red';name='de4e8c2cfb6646f0938e2cfaec007ec5.receipt.json';pin='728ae5aa9b7f9a42901a957a225af5ca83b8f167ef1319f9c1a1bb3ab3a0e29c'},
        @{phase='green';name='f4d7a7348f50486db541d58ddfdd0837.receipt.json';pin='9bd9e951a7fbd56ae0bb4194e96d58a2b39ef9ae781c3de4ef3c98a1908f2d6a'})){
        $matches=@($Public.executions|Where-Object{$_.phase -ceq $item.phase})
        if($matches.Count -ne 1 -or $matches[0].private_receipt_name -cne $item.name){throw 'FIXED_RECEIPT_NAME_DIFFERENT'}
        $control=Open-TimingControl (Join-Path 'C:\ProgramData\CoChemPipeline427\private\containers' $item.name)
        $receipt=Read-PrivateTiming $control $item.pin
        if($receipt.container_id -cin $identifiers){throw 'SINGLE_USE_IDENTITY_REUSED'};$identifiers+=@($receipt.container_id)
        $rows+=@(Get-TimingProjection $receipt $Public $item.phase $item.pin $control.Sha256)
    }
    [ordered]@{schema='cochem-existing-docker-timing-projection/1';status='EXISTING_TIMING_PROVENANCE_VERIFIED';read_only=$true;
        public_receipt_sha256='2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74';historical_wrapper_status=$Public.status;
        startup_target_seconds=1.5;startup_target_changed=$false;optimization_target_missed=$true;physical_correctness_preserved=$true;
        private_receipts_read=2;docker_commands_executed=0;tasks_changed=0;databases_opened=0;credentials_read=0;model_jobs=0;
        ids_source_argv_stdout_stderr_published=$false;files_written=0;activation_ready=$false;executions=$rows}
}
$phase='entry';$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop}
    if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'WINDOWS_HOST_REQUIRED'}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if($Inspect -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'OWNER_ADMIN_REQUIRED'}
    Initialize-ReceiptIdentity;$phase='public_receipt'
    $public=Read-PublicTiming (Open-TimingControl 'C:\Program Files\CoChem\DockerExecutionAcceptance4.2.7-windows-20261007-r3-v2\docker-physical-acceptance.json')
    if(-not $Inspect){[ordered]@{schema='cochem-existing-docker-timing-plan/1';mode='READ_ONLY_PLAN';public_receipt_sha256='2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74';historical_wrapper_status=$public.status;private_receipt_reads_deferred=$true;administrator_required_for_inspect=$true;startup_target_seconds=1.5;docker_commands_executed=0;files_written=0;holds=@()}|ConvertTo-Json -Depth 4;return}
    $phase='private_timing_receipts';Invoke-ExistingTimingInspection $public|ConvertTo-Json -Depth 7
}catch{
    [ordered]@{schema='cochem-existing-docker-timing-projection/1';status='TIMING_INSPECTION_HELD';phase=$phase;raw_error_withheld=$true;error_type='InspectionError';read_only=$true;files_written=0;activation_ready=$false}|ConvertTo-Json -Compress
    exit 2
}finally{foreach($stream in $held){$stream.Dispose()}}