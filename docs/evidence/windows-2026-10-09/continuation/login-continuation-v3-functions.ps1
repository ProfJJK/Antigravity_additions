# New helpers for attended continuation. No login or deployment on import.
function Enter-AttendedCommissioning {
 $security=[Security.AccessControl.MutexSecurity]::new()
 $security.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)')
 $created=$false
 $mutex=[Threading.Mutex]::new($true,'Global\CoChemPipeline427-AttendedCommissioning',[ref]$created,$security)
 if(-not $created){$mutex.Dispose();throw 'Another attended commissioning command is active. Finish or close that console before starting another; no new attempt was started.'}
 return $mutex
}

function Read-SecondFailedAuthenticationCustody {
 $oldCommission='C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v2'
 $oldAuth='C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v2'
 $oldWorker='C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v2-codex'
 foreach($root in @($oldCommission,$oldAuth,$oldWorker)){Assert-SeriesPrivateRoot $root;Assert-RecoveryAbsent (Join-Path $root 'series-complete.json')}
 foreach($path in @((Join-Path $oldCommission 'first_controller_start-STARTED.json'),(Join-Path $oldCommission 'native_authentication-VERIFIED.json'),(Join-Path $oldAuth 'codex-VERIFIED.json'),(Join-Path $oldAuth 'claude-STARTED.json'),(Join-Path $oldWorker 'slot5-LOGIN_EXITED_ZERO.json'),(Join-Path $oldWorker 'slot6-STATUS_BEFORE_STARTED.json'),'C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v2-claude')){Assert-RecoveryAbsent $path}
 $specs=@(
  [pscustomobject]@{key='CommissionStart';path=(Join-Path $oldCommission 'series-start.json')},
  [pscustomobject]@{key='CommissionFailure';path=(Join-Path $oldCommission 'series-failed.json')},
  [pscustomobject]@{key='AuthStart';path=(Join-Path $oldAuth 'series-start.json')},
  [pscustomobject]@{key='AuthFailure';path=(Join-Path $oldAuth 'series-failed.json')},
  [pscustomobject]@{key='WorkerStart';path=(Join-Path $oldWorker 'series-start.json')},
  [pscustomobject]@{key='WorkerFailure';path=(Join-Path $oldWorker 'series-failed.json')},
  [pscustomobject]@{key='Before';path=(Join-Path $oldWorker 'slot5-STATUS_BEFORE_COMPLETE.json')},
  [pscustomobject]@{key='Login';path=(Join-Path $oldWorker 'slot5-LOGIN_STARTED.json')}
 )
 $values=@{};$proofs=@()
 foreach($spec in $specs){
  $control=Read-R3Control $spec.path '' 65536;$values[$spec.key]=(Read-R3Text $control)|ConvertFrom-Json
  $proofs+=[pscustomobject]@{path=$spec.path;sha256=$control.Sha256}
 }
 Assert-RecoveryPriorRecords @values -FailedSlot slot5 -Version v2
 $leaf=$values.Before.proof;$null=Read-R3Control $leaf.receipt_path $leaf.receipt_sha256 65536
 $proofs+=[pscustomobject]@{path=$leaf.receipt_path;sha256=$leaf.receipt_sha256}
 foreach($n in 1..4){
  $events=@()
  foreach($state in @('REUSED_VERIFIED_SESSION','AUTHENTICATED_AFTER_LOGIN')){
   $path=Join-Path $oldWorker "slot$n-$state.json"
   try{$null=Get-Item -LiteralPath $path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{continue}
   $control=Read-R3Control $path '' 65536;$event=(Read-R3Text $control)|ConvertFrom-Json
   if($event.schema -cne 'cochem-six-worker-auth-event/1' -or $event.provider -cne 'codex' -or $event.slot -cne "slot$n" -or $event.state -cne $state -or $event.nonce -cne $values.WorkerStart.nonce -or $event.proof.decision -cne 'REUSE_VERIFIED_SESSION'){throw 'Earlier profile completion metadata differs.'}
   $expectedStage=if($state -ceq 'REUSED_VERIFIED_SESSION'){'before'}else{'after'}
   $expected="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v2-slot$n-codex-$expectedStage\worker-native-status.json"
   if($event.proof.stage -cne $expectedStage -or $event.proof.receipt_path -cne $expected -or $event.proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Earlier profile proof path differs.'}
   $null=Read-R3Control $expected $event.proof.receipt_sha256 65536
   $proofs+=[pscustomobject]@{path=$path;sha256=$control.Sha256};$proofs+=[pscustomobject]@{path=$expected;sha256=$event.proof.receipt_sha256};$events+=$event
  }
  if($events.Count -ne 1){throw 'Each of the first four profiles must have one preserved completion event.'}
 }
 return [pscustomobject]@{schema='cochem-failed-login-recovery-custody/1';reported_failure='codex_slot5_device_login';prior_receipts=$proofs;prior_completed_profiles=4;old_roots_modified=$false;prior_status_authorizes_new_login=$false;fresh_status_required=$true;controller_start_previously_requested=$false}
}

function Read-CommissioningPausedAuthEvidence {
 $root="C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt"
 Assert-SeriesPrivateRoot $root
 $path=Join-Path $root 'series-paused.json';$control=Read-R3Control $path '' 65536;$v=(Read-R3Text $control)|ConvertFrom-Json
 if($v.schema -cne 'cochem-both-providers-status-first-paused/1' -or $v.status -cne 'AUTHENTICATION_PAUSED_BEFORE_LOGIN' -or $v.attempt -cne $Attempt -or
    $v.exit_code -isnot [int] -or $v.exit_code -ne 20 -or $v.model_jobs_executed -ne 0 -or
    $v.activation_ready -isnot [bool] -or $v.activation_ready -or $v.pipeline_started -isnot [bool] -or $v.pipeline_started -or
    $v.series_preserved -isnot [bool] -or -not $v.series_preserved -or $v.automatic_retry_allowed -isnot [bool] -or $v.automatic_retry_allowed){throw 'Authentication pause is not bound to the current attempt.'}
 return [pscustomobject]@{status=$v.status;attempt=$Attempt;receipt_path=$path;receipt_sha256=$control.Sha256;controller_start_requested=$false}
}
