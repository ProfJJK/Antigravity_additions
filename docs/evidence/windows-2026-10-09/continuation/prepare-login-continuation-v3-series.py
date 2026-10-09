"""Prepare an attended continuation with fresh, immutable auth attempts."""
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent

def digest(name):return hashlib.sha256((HERE/name).read_bytes()).hexdigest()

def main():
    raw=(HERE/'run-pipeline-commissioning-r3-v2.ps1').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='3b14ff276c59a6123d6c9dea141155c019a842ff4dffbc0b531b4cc9c77f9976'
    text=raw.decode('utf-8-sig')
    text=text.replace('<# One attended series: reuse/authenticate native profiles, then commission one\r\n   Warden instance. Default is metadata-only. Never repeat a partial series. #>', '<# Each deliberate invocation uses a fresh authentication attempt and reuses\r\n   current valid sign-ins. READY/PAUSE gates attended login. First start remains\r\n   one-shot: any existing activation state prevents every further invocation. #>')
    text=text.replace("$seriesRoot='C:\\Program Files\\CoChem\\CommissioningSeries4.2.7-windows-20261008-r3-v2'", "$Attempt=if($Apply){[Guid]::NewGuid().ToString('N')}else{'00000000000000000000000000000000'}\n$seriesRoot=\"C:\\Program Files\\CoChem\\CommissioningSeries4.2.7-windows-20261008-r3-v3-$Attempt\"")
    for stem in ('authenticate-native-profiles-status-first','login-six-workers-status-first','commission-first-warden'):
        text=text.replace(stem+'-r3-v2.ps1',stem+'-r3-v3.ps1')
    # v2 references here are solely the new authentication readers/admission.
    # The preserved v1 custody block has no v2 path literals.
    text=text.replace("$root='C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v2'", "$root=\"C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt\"")
    text=text.replace("'C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v2'", "\"C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt\"")
    text=text.replace('Existing series/authentication/activation artifacts must be preserved; this entrypoint has no repeat or resume mode.','An immutable attempt or activation artifact already exists. Preserve it; this exact namespace cannot be reused.')
    for pin,name in (
      ('75066bb3da5b3d2f1b65e113d5e5268e9f18a88f37bb695a863da9baeea8933b','authenticate-native-profiles-status-first-r3-v3.ps1'),
      ('86381bcdddc817637567edb1f737d142f6a9df01a1f256b6c9447d936ad6ee86','login-six-workers-status-first-r3-v3.ps1'),
      ('97a4a0976437f404e63e849d01495188b0a26c7945e1c3ebe59b564cf471c085','commission-first-warden-r3-v3.ps1'),
    ):text=text.replace(pin,digest(name))
    text=text.replace('param($CommissionStart,$CommissionFailure,$AuthStart,$AuthFailure,$WorkerStart,$WorkerFailure,$Before,$Login)', "param($CommissionStart,$CommissionFailure,$AuthStart,$AuthFailure,$WorkerStart,$WorkerFailure,$Before,$Login,[ValidateSet('slot1','slot5')][string]$FailedSlot='slot1',[ValidateSet('v1','v2')][string]$Version='v1')",1)
    text=text.replace("$event.slot -cne 'slot1'", '$event.slot -cne $FailedSlot',1)
    text=text.replace("$Before.proof.receipt_path -cne 'C:\\Program Files\\CoChem\\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-slot1-codex-before\\worker-native-status.json'", '$Before.proof.receipt_path -cne $(if($Version -ceq \'v1\'){"C:\\Program Files\\CoChem\\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-$FailedSlot-codex-before\\worker-native-status.json"}else{"C:\\Program Files\\CoChem\\NativeAuthStatusSix4.2.7-windows-20261008-r3-v2-$FailedSlot-codex-before\\worker-native-status.json"})',1)
    extra=(HERE/'login-continuation-v3-functions.ps1').read_text()
    text=text.replace('function Read-CommissioningAuthEvidence {',extra+'\nfunction Read-CommissioningAuthEvidence {',1)
    text=text.replace("if($value.schema -cne 'cochem-both-providers-status-first-result/1'", "if($value.attempt -cne $Attempt -or $value.schema -cne 'cochem-both-providers-status-first-result/1'",1)
    text=text.replace('-NonInteractive -File $source 2>&1', '-NonInteractive -File $source -Attempt $Attempt 2>&1',1)
    text=text.replace("  if($code -isnot [int] -or $code -ne 0){throw 'Commissioning phase", "  if($phaseName -ceq 'native_authentication' -and $code -is [int] -and $code -eq 20){& $Record $phaseName 'PAUSED' (Read-CommissioningPausedAuthEvidence);return 20}\n  if($code -isnot [int] -or $code -ne 0){throw 'Commissioning phase",1)
    text=text.replace("$ownsRoot=$false;$phase='preflight';$nonce=$null", "$ownsRoot=$false;$phase='preflight';$nonce=$null;$commissionGate=$null",1)
    text=text.replace("try{\r\n if($activationHash", "try{\r\n if($Apply){$commissionGate=Enter-AttendedCommissioning}\r\n if($activationHash",1)
    # If source newlines are normalized the same replacement is required.
    text=text.replace("try{\n if($activationHash", "try{\n if($Apply){$commissionGate=Enter-AttendedCommissioning}\n if($activationHash",1)
    text=text.replace('$priorCustody=Read-RecoveryPriorCustody', '$priorCustody=[pscustomobject]@{first_attempt=(Read-RecoveryPriorCustody);second_attempt=(Read-SecondFailedAuthenticationCustody)}',1)
    text=text.replace('\n Invoke-CommissioningPhases {', '\n $phaseOutcome=Invoke-CommissioningPhases {',1)
    text=text.replace("$authSource,'-Apply','-Interactive')", "$authSource,'-Attempt',$Attempt,'-Apply','-Interactive')",1)
    text=text.replace("$activationSource,'-Apply')", "$activationSource,'-Attempt',$Attempt,'-Apply')",1)
    needle=" if($script:completedPhases.Count -ne 2){throw 'Commissioning did not verify both phases.'}"
    assert text.count(needle)==1
    pause=""" if($phaseOutcome -is [int] -and $phaseOutcome -eq 20){
  $paused=[ordered]@{schema='cochem-pipeline-commissioning-series-paused/1';status='AUTHENTICATION_PAUSED_BEFORE_LOGIN';attempt=$Attempt;nonce=$nonce;controller_start_requested=$false;model_jobs_submitted=0;next='Run this same command when ready. It creates a fresh authentication attempt and reuses current valid sign-ins; no automatic retry was started.'}
  Write-SeriesRecord (Join-Path $seriesRoot 'series-paused.json') $paused;$paused|ConvertTo-Json -Depth 5;exit 20
 }
"""
    text=text.replace(needle,pause+needle,1)
    text=text.replace('status=\'IN_PROGRESS\';nonce=$nonce;runtime=$runtime;auth_helper_sha256', "status='IN_PROGRESS';attempt=$Attempt;nonce=$nonce;runtime=$runtime;auth_helper_sha256",1)
    text=text.replace("status='WARDEN_RUNNING_NATIVE_PROFILES_AUTHENTICATED';nonce=$nonce", "status='WARDEN_RUNNING_NATIVE_PROFILES_AUTHENTICATED';attempt=$Attempt;nonce=$nonce",1)
    text=text.replace("schema='cochem-pipeline-commissioning-series-failure/1';nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;automatic_repeat_allowed=$false;partial_outputs_preserved=$true;full_srs_acceptance=$false", "schema='cochem-pipeline-commissioning-series-failure/1';attempt=$Attempt;nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;automatic_repeat_allowed=$false;manual_authentication_continuation_requires_fresh_attempt_and_absent_activation=$true;partial_outputs_preserved=$true;full_srs_acceptance=$false",1)
    text=text.replace("}finally{foreach($stream in $held){$stream.Dispose()}}", "}finally{foreach($stream in $held){$stream.Dispose()};if($null -ne $commissionGate){try{$commissionGate.ReleaseMutex()}finally{$commissionGate.Dispose()}}}",1)
    with (HERE/'run-pipeline-commissioning-r3-v3.ps1').open('x',encoding='utf-8',newline='') as stream:stream.write(text)
    print(digest('run-pipeline-commissioning-r3-v3.ps1'))

if __name__=='__main__':main()
