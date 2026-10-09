#Requires -Version 5.1
# Read-only exact task security/state metadata. No private data or task actions.
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$s=New-Object -ComObject 'Schedule.Service';$s.Connect();$f=$s.GetFolder('\')
$t=$f.GetTask('CoChem-4.2.7-Warden')
$sddl=$t.GetSecurityDescriptor(7)
$sd=[Security.AccessControl.RawSecurityDescriptor]::new($sddl)
$aces=@(foreach($ace in $sd.DiscretionaryAcl){[ordered]@{type=[int]$ace.AceType;flags=[int]$ace.AceFlags;mask=$ace.AccessMask;sid=$ace.SecurityIdentifier.Value}})
$commissionPresent=$true
try{$null=$f.GetTask('CoChem-4.2.7-WardenCommissioning-r3-v1')}catch{
 $e=$_.Exception;while($null -ne $e -and $e.HResult -ne -2147024894){$e=$e.InnerException}
 if($null -eq $e){throw};$commissionPresent=$false
}
[ordered]@{schema='cochem-pending-warden-acl-read-only/1';task_name='CoChem-4.2.7-Warden';enabled=$t.Enabled;state=$t.State;instances=$t.GetInstances(0).Count;last_result=$t.LastTaskResult;last_run_utc=$t.LastRunTime.ToUniversalTime().ToString('o');owner=$sd.Owner.Value;group=$sd.Group.Value;dacl_protected=[bool]($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected);ace_count=$sd.DiscretionaryAcl.Count;aces=$aces;commissioning_task_present=$commissionPresent;tasks_changed=0;python_executed=$false}|ConvertTo-Json -Depth 5
