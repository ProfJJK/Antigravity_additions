#Requires -Version 5.1
<# Functions only. Read-only review of preserved, protected Claude login sessions.
   A historical receipt never authorizes login: the caller must obtain current
   explicit logged-out status and attended READY, and retain native SID exclusion.
   Import these definitions from a pinned, held source; do not dot-source a mutable
   checkout. Never read native logs or private input/cancellation channel contents. #>

function Test-ClaudeHistoryInteger {
    param($Value,[long]$Minimum=0,[long]$Maximum=4294967295)
    return (($Value -is [int] -or $Value -is [long]) -and $Value -ge $Minimum -and $Value -le $Maximum)
}

function Get-ClaudeHistoryValue {
    param($Value,[string]$Name,[switch]$Optional)
    if($Value -is [Collections.IDictionary]){
        $keys=@($Value.Keys|Where-Object{$_ -is [string] -and $_ -ceq $Name})
        if($keys.Count -eq 1){return $Value[$keys[0]]}
    }elseif($null -ne $Value){
        $properties=@($Value.PSObject.Properties|Where-Object{$_.Name -ceq $Name})
        if($properties.Count -eq 1){return $properties[0].Value}
    }
    if($Optional){return $null}
    throw 'Historical Claude evidence is missing a required exact field.'
}

function Get-ClaudeHistorySafeReceiptMetadata {
    param($Receipt)
    $result=[ordered]@{}
    $status=Get-ClaudeHistoryValue $Receipt 'status' -Optional
    if($status -is [string] -and $status -cin @('UNVERIFIED','LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED','LOGIN_COMMAND_FAILED','LOGIN_FAILED_OR_CANCELLED','CLEANUP_UNVERIFIED')){$result.status=$status}
    foreach($name in @('login_commands_executed','model_jobs_executed')){
        $value=Get-ClaudeHistoryValue $Receipt $name -Optional
        if(Test-ClaudeHistoryInteger $value 0 1){$result[$name]=$value}
    }
    $failure=Get-ClaudeHistoryValue $Receipt 'failure' -Optional
    if($null -ne $failure){
        $safe=[ordered]@{}
        $phase=Get-ClaudeHistoryValue $failure 'phase' -Optional
        if($phase -is [string] -and $phase -cin @('trusted_preflight','reservation','runtime_custody','layout','boundaries','native_custody','native_launch','native_attestation','native_wait','native_cleanup','native_result','daemon_postcheck')){$safe.phase=$phase}
        $kind=Get-ClaudeHistoryValue $failure 'error_type' -Optional
        if($kind -is [string] -and $kind -cmatch '\A[A-Za-z][A-Za-z0-9_]{0,79}\z'){$safe.error_type=$kind}
        $code=Get-ClaudeHistoryValue $failure 'winerror' -Optional
        if($null -eq $code -or (Test-ClaudeHistoryInteger $code 0 4294967295)){$safe.winerror=$code}
        if($safe.Count){$result.failure=$safe}
    }
    return [pscustomobject]$result
}

function Assert-ClaudeHistoryPrivateAcl {
    param($Acl,[switch]$Directory,[switch]$AllowInherited)
    $rules=@($Acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if(($Directory -and $AllowInherited) -or (-not $Acl.AreAccessRulesProtected -and -not $AllowInherited) -or $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cnotin @('S-1-5-18','S-1-5-32-544') -or $rules.Count -ne 2){throw 'Historical Claude evidence must retain its exact private ownership and ACL.'}
    $expectedInheritance=if($Directory){[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None}
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($rule in $rules){
        if($rule.IdentityReference.Value -cnotin @('S-1-5-18','S-1-5-32-544') -or -not $seen.Add($rule.IdentityReference.Value) -or
           $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or [long]$rule.FileSystemRights -ne 2032127 -or
           $rule.InheritanceFlags -ne $expectedInheritance -or $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None){throw 'Historical Claude evidence private grants differ.'}
    }
}

function Assert-ClaudeHistoryPrivateRoot {
    param([string]$Path)
    Assert-NoReparseAncestors $Path
    Assert-ProtectedPath $Path
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if(-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Historical Claude session root must be an ordinary private directory.'}
    Assert-ClaudeHistoryPrivateAcl (Get-Acl -LiteralPath $Path -ErrorAction Stop) -Directory
}

function Assert-ClaudeHistoryTask {
    param($Task,[string]$Root,[string]$Slot,[string]$Nonce)
    if($null -eq $Task){throw 'Historical Claude session task is absent; preserve evidence for diagnosis.'}
    if($Task.Name -cne ('CoChem-4.2.7-InteractiveClaude-r3-'+$Slot+'-'+$Nonce)){throw 'Historical Claude task name differs from its exact session.'}
    if(-not (Test-ClaudeHistoryInteger $Task.State 1 3) -or $Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0){throw 'Historical Claude session task is not conclusively terminal.'}
    $definition=$Task.Definition
    $arguments='-I -B "'+(Join-Path $Root 'worker_claude_login_bridge_r3.py')+'" --slot '+$Slot+' --nonce '+$Nonce
    if($definition.Principal.UserId -cnotin @('SYSTEM','S-1-5-18') -or $definition.Principal.LogonType -ne 5 -or $definition.Principal.RunLevel -ne 1 -or
       $definition.Triggers.Count -ne 0 -or $definition.Actions.Count -ne 1 -or $definition.Actions.Item(1).Type -ne 0 -or
       $definition.Actions.Item(1).Path -cne 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Scripts\python.exe' -or
       $definition.Actions.Item(1).Arguments -cne $arguments -or $definition.Actions.Item(1).WorkingDirectory -cne $Root -or
       $definition.Settings.MultipleInstances -ne 2 -or $definition.Settings.ExecutionTimeLimit -cne 'PT15M' -or $definition.Settings.RestartCount -ne 0){
        throw 'Historical Claude session task identity or fixed action differs.'
    }
    if(-not (Test-ClaudeHistoryInteger $Task.LastTaskResult 0 4294967295)){throw 'Historical Claude task result is not a bounded integer.'}
}

function Assert-ClaudeHistoryReceipt {
    param($Receipt,$Task,[string]$Slot,[string]$Nonce,$Runtime,[string]$ExpectedSid)
    try{
        $allowed=@('schema','slot','nonce','system_sid','helper_sha256','status','cleanup_verified','one_line_submitted','input_file_deleted','operator_cancelled','login_commands_executed','model_jobs_executed','authentication_verified','activation_ready','secret_published','native_output_after_input_published','started_at_unix_ms','finished_at_unix_ms','revision','runtime_root','install_receipt_sha256','source_manifest_sha256','resource_limits_sha256','layout_sha256','config_sha256','process','native_exit_code','failure')
        if($null -eq $Receipt -or @($Receipt.PSObject.Properties|Where-Object{$_.Name -cnotin $allowed}).Count){throw 'Historical Claude receipt fields differ from the pinned producer.'}
        $expectedRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
        $installPin='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
        $sourcePin='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
        $configPin='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
        $revisionPin='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
        if($Slot -cnotmatch '\Aslot[1-6]\z' -or $Nonce -cnotmatch '\A[a-f0-9]{32}\z' -or $Nonce -ceq ('0'*32) -or
           $ExpectedSid -cnotmatch '\AS-1-5-21-[0-9]+-[0-9]+-[0-9]+-[0-9]+\z'){
            throw 'Historical Claude selected worker binding is invalid.'
        }
        if((Get-ClaudeHistoryValue $Runtime 'install_receipt_sha256') -cne $installPin -or (Get-ClaudeHistoryValue $Runtime 'source_manifest_sha256') -cne $sourcePin -or
           (Get-ClaudeHistoryValue $Runtime 'configuration_sha256') -cne $configPin -or (Get-ClaudeHistoryValue (Get-ClaudeHistoryValue $Runtime 'revision') 'source_sha256') -cne $revisionPin){
            throw 'Historical Claude review requires the exact current r3 runtime.'
        }
        foreach($pair in @(
            @('schema','cochem-interactive-claude-login/1'),@('slot',$Slot),@('nonce',$Nonce),@('system_sid','S-1-5-18'),
            @('helper_sha256','b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'),@('runtime_root',$expectedRoot),
            @('install_receipt_sha256',$installPin),@('source_manifest_sha256',$sourcePin),@('config_sha256',$configPin),
            @('resource_limits_sha256','de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f'),
            @('layout_sha256','8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'))){
            $value=Get-ClaudeHistoryValue $Receipt $pair[0]
            if($value -isnot [string] -or $value -cne $pair[1]){throw 'Historical Claude receipt fixed identity or runtime binding differs.'}
        }
        $revision=Get-ClaudeHistoryValue $Receipt 'revision'
        if((Get-ClaudeHistoryValue $revision 'schema') -cne 'cochem-installed-revision/1' -or (Get-ClaudeHistoryValue $revision 'source_sha256') -cne $revisionPin -or
           -not (Test-ClaudeHistoryInteger (Get-ClaudeHistoryValue $revision 'files') 109 109) -or -not (Test-ClaudeHistoryInteger (Get-ClaudeHistoryValue $revision 'acceptance_files') 0 0) -or
           $null -ne (Get-ClaudeHistoryValue $revision 'acceptance_sha256')){throw 'Historical Claude receipt revision differs.'}
        foreach($name in @('verified','read_only')){$flag=Get-ClaudeHistoryValue $revision $name;if($flag -isnot [bool] -or -not $flag){throw 'Historical Claude revision verification flag differs.'}}
        foreach($name in @('cleanup_verified','one_line_submitted','input_file_deleted','operator_cancelled','authentication_verified','activation_ready','secret_published','native_output_after_input_published')){
            if((Get-ClaudeHistoryValue $Receipt $name) -isnot [bool]){throw 'Historical Claude receipt flag must be a JSON boolean.'}
        }
        foreach($name in @('authentication_verified','activation_ready','secret_published','native_output_after_input_published')){
            if(Get-ClaudeHistoryValue $Receipt $name){throw 'Historical Claude receipt makes an unexpected authentication, activation or disclosure claim.'}
        }
        $logins=Get-ClaudeHistoryValue $Receipt 'login_commands_executed';$models=Get-ClaudeHistoryValue $Receipt 'model_jobs_executed'
        $started=Get-ClaudeHistoryValue $Receipt 'started_at_unix_ms';$finished=Get-ClaudeHistoryValue $Receipt 'finished_at_unix_ms'
        if(-not (Test-ClaudeHistoryInteger $logins 0 1) -or -not (Test-ClaudeHistoryInteger $models 0 0) -or
           -not (Test-ClaudeHistoryInteger $started 1 ([long]::MaxValue)) -or -not (Test-ClaudeHistoryInteger $finished $started ([long]::MaxValue))){throw 'Historical Claude receipt counts or terminal timestamps differ.'}
        $status=Get-ClaudeHistoryValue $Receipt 'status';$cleanup=Get-ClaudeHistoryValue $Receipt 'cleanup_verified'
        $failure=Get-ClaudeHistoryValue $Receipt 'failure' -Optional
        $process=Get-ClaudeHistoryValue $Receipt 'process' -Optional
        $nativeCode=Get-ClaudeHistoryValue $Receipt 'native_exit_code' -Optional
        if($logins -eq 1 -and $cleanup -and $status -cin @('LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED','LOGIN_COMMAND_FAILED') -and $null -eq $failure){
            if(-not (Test-ClaudeHistoryInteger $nativeCode 0 4294967295) -or
               ($status -ceq 'LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED' -and ($nativeCode -ne 0 -or $Task.LastTaskResult -ne 0)) -or
               ($status -ceq 'LOGIN_COMMAND_FAILED' -and ($nativeCode -eq 0 -or $Task.LastTaskResult -ne 2))){throw 'Historical Claude native and task terminal results disagree.'}
            if($null -eq $process -or (Get-ClaudeHistoryValue $process 'token_sid') -cne $ExpectedSid -or
               (Get-ClaudeHistoryValue $process 'source') -cne 'owned_windows_process_handle_and_child_token' -or
               -not (Test-ClaudeHistoryInteger (Get-ClaudeHistoryValue $process 'pid') 1 4294967295) -or
               -not (Test-ClaudeHistoryInteger (Get-ClaudeHistoryValue $process 'creation_time_filetime') 1 ([long]::MaxValue)) -or
               (Get-ClaudeHistoryValue $process 'profile_directory_sha256') -isnot [string] -or (Get-ClaudeHistoryValue $process 'profile_directory_sha256') -cnotmatch '\A[a-f0-9]{64}\z'){
                throw 'Historical Claude native child attestation differs.'
            }
            foreach($name in @('token_matches_selected_worker','image_matches_reviewed_executable','owned_job_membership_verified')){
                $flag=Get-ClaudeHistoryValue $process $name;if($flag -isnot [bool] -or -not $flag){throw 'Historical Claude native child verification flag differs.'}
            }
            if((Get-ClaudeHistoryValue $Receipt 'operator_cancelled') -or
               ((Get-ClaudeHistoryValue $Receipt 'one_line_submitted') -and -not (Get-ClaudeHistoryValue $Receipt 'input_file_deleted'))){throw 'Historical Claude consumed input or cancellation state differs.'}
            return [pscustomobject]@{classification='TERMINAL_NATIVE_LOGIN_CLEANUP_VERIFIED';status=$status;login_commands_executed=$logins;model_jobs_executed=0;cleanup_verified=$true;authentication_authorized_by_history=$false}
        }
        # native_launch is deliberately excluded. A failed launch can create a
        # suspended child before raising, while the bridge's counter remains 0.
        # Only native_custody is after full bindings and before launch is called.
        if($logins -eq 0 -and -not $cleanup -and $status -ceq 'LOGIN_FAILED_OR_CANCELLED' -and $Task.LastTaskResult -eq 2 -and
           $null -eq $process -and $null -eq $nativeCode -and $null -ne $failure -and
           (Get-ClaudeHistoryValue $failure 'phase') -ceq 'native_custody' -and
           (Get-ClaudeHistoryValue $failure 'error_type') -is [string] -and (Get-ClaudeHistoryValue $failure 'error_type') -cmatch '\A[A-Za-z][A-Za-z0-9_]{0,79}\z'){
            $code=Get-ClaudeHistoryValue $failure 'winerror'
            if($null -ne $code -and -not (Test-ClaudeHistoryInteger $code 0 4294967295)){throw 'Historical Claude prelaunch failure metadata differs.'}
            foreach($name in @('one_line_submitted','input_file_deleted','operator_cancelled')){if(Get-ClaudeHistoryValue $Receipt $name){throw 'Historical Claude prelaunch evidence contains an inconsistent interaction flag.'}}
            return [pscustomobject]@{classification='TERMINAL_PRE_NATIVE_CUSTODY_FAILURE_NO_CHILD_LAUNCHED';status=$status;login_commands_executed=0;model_jobs_executed=0;cleanup_verified=$false;native_launch_called=$false;authentication_authorized_by_history=$false}
        }
        throw 'Historical Claude receipt does not prove terminal native cleanup or a reviewed failure before launch.'
    }catch{
        $errorValue=[InvalidOperationException]::new('Historical Claude receipt requires diagnosis; preserve all evidence and do not retry login automatically.')
        $errorValue.Data['CoChemClaudeHistorySafeMetadata']=Get-ClaudeHistorySafeReceiptMetadata $Receipt
        throw $errorValue
    }
}

function Assert-ReviewedClaudeHistory {
    param([Parameter(Mandatory=$true)][ValidateSet('slot1','slot2','slot3','slot4','slot5','slot6')][string]$Slot,[Parameter(Mandatory=$true)]$Folder,[Parameter(Mandatory=$true)]$Runtime)
    if($Slot -cnotmatch '\Aslot[1-6]\z'){throw 'Historical Claude slot must use its exact lowercase spelling.'}
    $parent='C:\Program Files\CoChem'
    Assert-NoReparseAncestors $parent;Assert-ProtectedPath $parent
    $old=@(Get-ChildItem -LiteralPath $parent -Directory -Force -Filter "InteractiveClaude427-$Slot-*" -ErrorAction Stop)
    if($old.Count){throw 'An older non-r3 Claude session remains outside this reviewed recovery; preserve it for diagnosis.'}
    $roots=@(Get-ChildItem -LiteralPath $parent -Directory -Force -Filter "InteractiveClaude427-r3-$Slot-*" -ErrorAction Stop)
    if($roots.Count -gt 128){throw 'Historical Claude session inventory exceeded its bound.'}
    $expectedTasks=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($root in $roots){
        if($root.Name -cnotmatch ('\AInteractiveClaude427-r3-'+$Slot+'-([a-f0-9]{32})\z') -or $Matches[1] -ceq ('0'*32) -or $root.FullName -cne (Join-Path $parent $root.Name)){
            throw 'Historical Claude session root name or selected slot differs.'
        }
        if(-not $expectedTasks.Add(('CoChem-4.2.7-InteractiveClaude-r3-'+$Slot+'-'+$Matches[1]))){throw 'Historical Claude session inventory contains a duplicate.'}
    }
    $tasks=@($Folder.GetTasks(1));if($tasks.Count -gt 5000){throw 'Historical Claude scheduler census exceeded its bound.'}
    foreach($candidate in $tasks){
        if($candidate.Name -like "CoChem-4.2.7-InteractiveClaude-$Slot-*" -or $candidate.Name -like "CoChem-4.2.7-InteractiveClaude-r3-$Slot-*"){
            if(-not $expectedTasks.Contains([string]$candidate.Name)){throw 'A Claude login task has no matching reviewed session root; preserve it for diagnosis.'}
        }
    }
    if(-not $roots.Count){return}
    $layout=(Read-R3Text (Read-R3Control (Join-Path 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3' 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4' 1048576))|ConvertFrom-Json
    $expectedSid=Get-ClaudeHistoryValue (Get-ClaudeHistoryValue (Get-ClaudeHistoryValue $layout 'slots') $Slot) 'sid'
    $summaries=[Collections.Generic.List[object]]::new()
    foreach($root in $roots){
        $null=$root.Name -cmatch ('\AInteractiveClaude427-r3-'+$Slot+'-([a-f0-9]{32})\z');$nonce=$Matches[1]
        $taskName='CoChem-4.2.7-InteractiveClaude-r3-'+$Slot+'-'+$nonce
        Assert-ClaudeHistoryPrivateRoot $root.FullName
        foreach($pair in @(@('worker_claude_login_bridge_r3.py','b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'),@('worker_native_status_support.py','c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'))){
            $path=Join-Path $root.FullName $pair[0]
            Assert-ClaudeHistoryPrivateAcl (Get-Acl -LiteralPath $path -ErrorAction Stop)
            $null=Read-R3Control $path $pair[1] 1048576
        }
        $task=Get-ExactTaskOrAbsent $Folder $taskName
        Assert-ClaudeHistoryTask $task $root.FullName $Slot $nonce
        $receiptPath=Join-Path $root.FullName 'receipt.json'
        # pathlib.open('x') creates this receipt with the ordinary inherited
        # BA/SY-only file ACL from the already verified private session root.
        # Sources copied by the PowerShell leaf have explicit protected ACLs.
        Assert-ClaudeHistoryPrivateAcl (Get-Acl -LiteralPath $receiptPath -ErrorAction Stop) -AllowInherited
        $control=Read-R3Control $receiptPath '' 32768
        try{
            $receipt=(Read-R3Text $control)|ConvertFrom-Json
            $proof=Assert-ClaudeHistoryReceipt $receipt $task $Slot $nonce $Runtime $expectedSid
        }catch{
            $errorValue=$_.Exception
            while($null -ne $errorValue){
                if($errorValue.Data.Contains('CoChemClaudeHistorySafeMetadata')){
                    Write-Host ('COCHEM_CLAUDE_HISTORY_HOLD '+($errorValue.Data['CoChemClaudeHistorySafeMetadata']|ConvertTo-Json -Depth 4 -Compress));break
                }
                $errorValue=$errorValue.InnerException
            }
            throw 'Historical Claude receipt is not sufficient for a fresh login; preserve every file and task for diagnosis.'
        }
        # Re-read exact scheduler state after taking immutable receipt/source
        # handles. No task, credential, process, log or channel is changed.
        Assert-ClaudeHistoryTask (Get-ExactTaskOrAbsent $Folder $taskName) $root.FullName $Slot $nonce
        $summaries.Add([pscustomobject]@{schema='cochem-reviewed-claude-session-history/1';slot=$Slot;nonce=$nonce;root=$root.FullName;task_name=$taskName;receipt_path=$receiptPath;receipt_sha256=$control.Sha256;classification=$proof.classification;status=$proof.status;login_commands_executed=$proof.login_commands_executed;cleanup_verified=$proof.cleanup_verified;prior_state_preserved=$true;authentication_authorized_by_history=$false;current_logged_out_status_required=$true;operator_ready_required=$true})
    }
    return $summaries.ToArray()
}
