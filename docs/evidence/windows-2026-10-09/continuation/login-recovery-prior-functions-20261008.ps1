# Read-only custody of the reported failed attempt. These receipts authorize no
# login or launch: the new series obtains current status in every profile.
function Assert-RecoveryAbsent {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return}catch{throw 'Recovery prerequisite is inaccessible; absence is not established.'}
 throw 'Unexpected prior completion or later-phase artifact. Preserve it; this continuation is held.'
}

function Assert-RecoveryPriorRecords {
 param($CommissionStart,$CommissionFailure,$AuthStart,$AuthFailure,$WorkerStart,$WorkerFailure,$Before,$Login)
 foreach($pair in @(
  @($CommissionStart,$CommissionFailure,'cochem-pipeline-commissioning-series/1','cochem-pipeline-commissioning-series-failure/1','reviewed_series'),
  @($AuthStart,$AuthFailure,'cochem-both-providers-status-first/1','cochem-both-providers-status-first-failure/1','attended_provider_series'),
  @($WorkerStart,$WorkerFailure,'cochem-six-worker-status-first/1','cochem-six-worker-status-first-failure/1','status_first_authentication')
 )){
  $start=$pair[0];$failure=$pair[1]
  if($start.schema -cne $pair[2] -or $failure.schema -cne $pair[3] -or $failure.phase -cne $pair[4] -or
     $start.status -cne 'IN_PROGRESS' -or $start.nonce -cnotmatch '^[a-f0-9]{32}$' -or $failure.nonce -cne $start.nonce -or
     $failure.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$'){throw 'Prior failure receipts do not establish the reported stopped attempt.'}
 }
 foreach($v in @($CommissionFailure,$AuthFailure)){if($v.automatic_repeat_allowed -isnot [bool] -or $v.automatic_repeat_allowed){throw 'Prior repeat policy differs.'}}
 if($CommissionFailure.partial_outputs_preserved -isnot [bool] -or -not $CommissionFailure.partial_outputs_preserved -or
    $CommissionFailure.full_srs_acceptance -isnot [bool] -or $CommissionFailure.full_srs_acceptance -or
    $AuthFailure.activation_ready -isnot [bool] -or $AuthFailure.activation_ready -or
    $WorkerFailure.automatic_resume_allowed -isnot [bool] -or $WorkerFailure.automatic_resume_allowed -or
    $WorkerFailure.configuration_applied -isnot [bool] -or $WorkerFailure.configuration_applied -or
    $WorkerFailure.activation_ready -isnot [bool] -or $WorkerFailure.activation_ready -or
    $WorkerStart.provider -cne 'codex' -or $WorkerFailure.provider -cne 'codex'){throw 'Prior stopped preservation/configuration policy differs.'}
 if($WorkerStart.runtime.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
    $WorkerStart.runtime.configuration_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
    $WorkerStart.runtime.revision.source_sha256 -cne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'){throw 'Prior runtime differs.'}
 foreach($event in @($Before,$Login)){
  if($event.schema -cne 'cochem-six-worker-auth-event/1' -or $event.provider -cne 'codex' -or $event.slot -cne 'slot1' -or $event.nonce -cne $WorkerStart.nonce){throw 'Prior login event binding differs.'}
 }
 if($Before.state -cne 'STATUS_BEFORE_COMPLETE' -or $Before.proof.decision -cne 'ATTENDED_LOGIN_REQUIRED' -or $Before.proof.stage -cne 'before' -or
    $Before.proof.receipt_path -cne 'C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-slot1-codex-before\worker-native-status.json' -or
    $Before.proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or $Login.state -cne 'LOGIN_STARTED'){throw 'Prior attempt differs from the reported slot1 browser-login failure.'}
}

function Read-RecoveryPriorCustody {
 $oldCommission='C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261007-r3-v1'
 $oldAuth='C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261007-r3-v1'
 $oldWorker='C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-codex'
 foreach($root in @($oldCommission,$oldAuth,$oldWorker)){
  Assert-SeriesPrivateRoot $root
  Assert-RecoveryAbsent (Join-Path $root 'series-complete.json')
 }
 foreach($path in @(
  (Join-Path $oldCommission 'first_controller_start-STARTED.json'),
  (Join-Path $oldCommission 'native_authentication-VERIFIED.json'),
  (Join-Path $oldAuth 'codex-VERIFIED.json'),
  (Join-Path $oldAuth 'claude-STARTED.json'),
  (Join-Path $oldWorker 'slot1-LOGIN_EXITED_ZERO.json'),
  (Join-Path $oldWorker 'slot2-STATUS_BEFORE_STARTED.json'),
  'C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-claude'
 )){Assert-RecoveryAbsent $path}
 $specs=@(
  [pscustomobject]@{key='CommissionStart';path=(Join-Path $oldCommission 'series-start.json')},
  [pscustomobject]@{key='CommissionFailure';path=(Join-Path $oldCommission 'series-failed.json')},
  [pscustomobject]@{key='AuthStart';path=(Join-Path $oldAuth 'series-start.json')},
  [pscustomobject]@{key='AuthFailure';path=(Join-Path $oldAuth 'series-failed.json')},
  [pscustomobject]@{key='WorkerStart';path=(Join-Path $oldWorker 'series-start.json')},
  [pscustomobject]@{key='WorkerFailure';path=(Join-Path $oldWorker 'series-failed.json')},
  [pscustomobject]@{key='Before';path=(Join-Path $oldWorker 'slot1-STATUS_BEFORE_COMPLETE.json')},
  [pscustomobject]@{key='Login';path=(Join-Path $oldWorker 'slot1-LOGIN_STARTED.json')}
 )
 $values=@{};$proofs=@()
 foreach($spec in $specs){
  $control=Read-R3Control $spec.path '' 65536
  $values[$spec.key]=(Read-R3Text $control)|ConvertFrom-Json
  $held.Add((Open-VerifiedFile $spec.path $control.Sha256 (Get-Item -LiteralPath $spec.path).Length))
  $proofs+=[pscustomobject]@{path=$spec.path;sha256=$control.Sha256}
 }
 Assert-RecoveryPriorRecords @values
 $leaf=$values.Before.proof
 $control=Read-R3Control $leaf.receipt_path $leaf.receipt_sha256 65536
 $held.Add((Open-VerifiedFile $leaf.receipt_path $control.Sha256 (Get-Item -LiteralPath $leaf.receipt_path).Length))
 $proofs+=[pscustomobject]@{path=$leaf.receipt_path;sha256=$control.Sha256}
 return [pscustomobject]@{schema='cochem-failed-login-recovery-custody/1';reported_failure='codex_slot1_device_login';prior_receipts=$proofs;old_roots_modified=$false;prior_status_authorizes_new_login=$false;fresh_status_required=$true;controller_start_previously_requested=$false}
}
