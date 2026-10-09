$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$roots=@(
 'C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261007-r3-v1',
 'C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261007-r3-v1',
 'C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-codex'
)
$records=@()
foreach($root in $roots){
 foreach($name in @('series-start.json','series-failed.json','series-complete.json','native_authentication-STARTED.json','slot1-STATUS_BEFORE_COMPLETE.json','slot1-LOGIN_STARTED.json')){
  $path=Join-Path $root $name
  try{$item=Get-Item -LiteralPath $path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{continue}
  if($item.Length -gt 65536){throw 'Unexpected receipt bounds.'}
  $v=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $row=[ordered]@{path=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();bytes=$item.Length;written_utc=$item.LastWriteTimeUtc.ToString('o')}
  foreach($key in @('schema','status','provider','slot','stage','phase','state','error_type','started_utc','observed_utc','automatic_repeat_allowed','partial_outputs_preserved')){
   if($null -ne $v.PSObject.Properties[$key]){$row[$key]=$v.$key}
  }
  if($null -ne $v.PSObject.Properties['proof'] -and $null -ne $v.proof){$row.proof=$v.proof | Select-Object slot,provider,stage,decision,receipt_path,receipt_sha256,cleanup_verified}
  $records+=[pscustomobject]$row
 }
}
$log='C:\Users\ansac\CoChem427\native-login-logs\codex-slot1-695722feeac148f59c672e62873f17e1.log'
$logInfo=Get-Item -LiteralPath $log -Force
$raw=[IO.File]::ReadAllText($log)
$logSummary=[ordered]@{path=$log;sha256=(Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash.ToLowerInvariant();bytes=$logInfo.Length;created_utc=$logInfo.CreationTimeUtc.ToString('o');written_utc=$logInfo.LastWriteTimeUtc.ToString('o');device_instructions_present=$raw.Contains('device code authorization');success_text_present=($raw -match 'Successfully logged in');error_marker_present=($raw -match '(?i)error|failed|denied|expired|timed?\s*out');raw_output_returned=$false}
$taskRows=@();$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-WardenCommissioning-r3-v1')){
 try{$task=$folder.GetTask($name);$taskRows+=[pscustomobject]@{name=$name;present=$true;enabled=$task.Enabled;state=$task.State;instances=$task.GetInstances(0).Count}}
 catch{if($_.Exception.HResult -eq -2147024894){$taskRows+=[pscustomobject]@{name=$name;present=$false}}else{throw}}
}
[ordered]@{schema='cochem-native-login-preserved-metadata/1';records=$records;log=$logSummary;tasks=$taskRows;credentials_read=$false;tasks_changed=$false}|ConvertTo-Json -Depth 7
