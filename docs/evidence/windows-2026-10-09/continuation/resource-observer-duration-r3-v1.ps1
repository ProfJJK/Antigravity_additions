# Exact 49-hour observation-task limit. Calendar months/years, signs and
# malformed/non-string values never authorize an existing task. This helper
# changes only duration representation; all original task guards remain.
function Test-ExactObserverDuration {
 param($Value,[long]$ExpectedSeconds=176400)
 if($Value -isnot [string] -or $Value.Length -gt 128 -or $ExpectedSeconds -ne 176400){return $false}
 $pattern='\AP(?:(?<days>[0-9]+)D)?(?:T(?:(?<hours>[0-9]+)H)?(?:(?<minutes>[0-9]+)M)?(?:(?<seconds>[0-9]+(?:[.][0-9]{1,7})?)S)?)?\z'
 $match=[Text.RegularExpressions.Regex]::Match($Value,$pattern)
 if(-not $match.Success){return $false}
 if(-not ($match.Groups['days'].Success -or $match.Groups['hours'].Success -or $match.Groups['minutes'].Success -or $match.Groups['seconds'].Success)){return $false}
 if($Value.Contains('T') -and -not ($match.Groups['hours'].Success -or $match.Groups['minutes'].Success -or $match.Groups['seconds'].Success)){return $false}
 try{$duration=[Xml.XmlConvert]::ToTimeSpan($Value)}catch{return $false}
 return $duration.Ticks -eq [TimeSpan]::FromSeconds($ExpectedSeconds).Ticks
}

function Assert-ObserverTask {
 param($Task,[string]$Arguments,[bool]$Running=$false,[string]$InstanceGuid='')
 $d=$Task.Definition
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
    $d.Settings.AllowDemandStart -ne $true -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.RestartCount -ne 0 -or -not (Test-ExactObserverDuration $d.Settings.ExecutionTimeLimit 176400) -or
    $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'Independent observer task definition differs.'}
 $a=$d.Actions.Item(1)
 if($a.Type -ne 0 -or $a.Path -cne $script:python -or $a.Arguments -cne $Arguments -or $a.WorkingDirectory -cne $script:packageRoot){throw 'Independent observer task action differs.'}
 Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7)) $Task.Name
 if($Running){
  $instances=$Task.GetInstances(0)
  if($Task.Enabled -ne $true -or $d.Settings.Enabled -ne $true -or $Task.State -ne 4 -or $instances.Count -ne 1 -or $instances.Item(1).InstanceGuid -cne $InstanceGuid){throw 'Observer task is not the exact single running instance.'}
 }elseif($Task.Enabled -ne $false -or $d.Settings.Enabled -ne $false -or $Task.State -ne 1 -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 267011){throw 'Observer task is not disabled and never run.'}
}
