"""Generate a fresh, one-shot monitoring continuation after a false stale check.

No deployment operations. Existing outputs and original sources are preserved.
The reviewed recovery child attests an already-running observer without Run.
The unchanged resource installer is the only remaining task-starting phase.
"""
import argparse
import hashlib
from pathlib import Path
import re

W = Path(__file__).resolve().parent
OLD = 'run-post-commissioning-setup-r3-v4.ps1'
OLD_SHA = '933aac1f6ca8ab9bf959aa4578a18284fb0aef5d57e93fc2c834da557e1e13c4'
CHILD = 'resume-running-held-observer-r3-v1.ps1'
NEW = 'run-running-monitoring-continuation-r3-v8.ps1'

PRIOR_FUNCTION = r'''
function Read-SetupPriorMonitoringHold {
 $root='C:\Program Files\CoChem\PostCommissioningSetup4.2.7-windows-20261008-r3-v4'
 $failure=Read-R3Control (Join-Path $root 'series-failed.json') 'fde32aea0f0f200b161d7b0fa4ae52fabde716c5ed46a74032e7980245475b12' 65536
 $intent=Read-R3Control (Join-Path $root 'series-intent.json') '4c0638e61527cb4d82fd378e39b270362314d952ff36ae18063e12ebb083b1ac' 65536
 $f=(Read-R3Text $failure)|ConvertFrom-Json;$i=(Read-R3Text $intent)|ConvertFrom-Json
 if($f.schema -cne 'cochem-protected-acceptance-setup-failure/1' -or $f.status -cne 'SETUP_HELD_PRESERVE_PARTIAL_AND_RUNNING_STATE' -or $f.phase -cne 'held_observation' -or $f.code -cne 'SETUP_PHASE_OR_EVIDENCE_NOT_VERIFIED' -or $f.nonce -cne 'b43e819326c14735a021ff275f47f03a' -or (@($f.verified_phases) -join '|') -cne 'independent_staging|first_start'){throw 'SETUP_PRIOR_MONITORING_FAILURE_BINDING'}
 foreach($k in @('automatic_retry_or_resume','full_srs_acceptance')){if($f.$k -isnot [bool] -or $f.$k){throw 'SETUP_PRIOR_MONITORING_FAILURE_SCOPE'}}
 foreach($k in @('running_state_preserved','no_automatic_stop')){if($f.$k -isnot [bool] -or -not $f.$k){throw 'SETUP_PRIOR_MONITORING_PRESERVATION'}}
 if($i.schema -cne 'cochem-protected-acceptance-setup-intent/1' -or $i.nonce -cne $f.nonce -or $i.branch -cne 'commissioned' -or $i.full_srs_acceptance -ne $false -or $i.automatic_retry_or_resume -ne $false){throw 'SETUP_PRIOR_MONITORING_INTENT_BINDING'}
 return [ordered]@{failure_sha256=$failure.Sha256;intent_sha256=$intent.Sha256;original_series_preserved=$true;running_observer_restarted=$false;prior_series_replayed=$false}
}

'''


def checked(name, pin):
    raw = (W / name).read_bytes()
    if (W / name).is_symlink() or hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError('Reviewed source changed: ' + name)
    return raw.decode('utf-8-sig')


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Reviewed replacement is not unique')
    return text.replace(old, new)


def render(child_sha256):
    if not re.fullmatch('[a-f0-9]{64}', child_sha256):
        raise ValueError('Explicit reviewed child SHA256 required')
    checked(CHILD, child_sha256)
    text = checked(OLD, OLD_SHA)
    text = once(text, "file='install-held-supervisor-observation-r3-v3.ps1';hash='f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6'",
                f"file='{CHILD}';hash='{child_sha256}'")
    text = once(text, "$seriesRoot='C:\\Program Files\\CoChem\\PostCommissioningSetup4.2.7-windows-20261008-r3-v4'",
                "$seriesRoot='C:\\Program Files\\CoChem\\RunningMonitoringContinuation4.2.7-windows-20261008-r3-v8'")
    text = once(text, 'function Get-SetupDisposition {', PRIOR_FUNCTION + 'function Get-SetupDisposition {')
    text = once(text, " if($Branch -cne 'commissioned'){throw 'SETUP_COMMISSIONING_REQUIRED_NO_FIRST_START'}",
                " if($Branch -cne 'commissioned'){throw 'SETUP_COMMISSIONING_REQUIRED_NO_FIRST_START'}\n if(-not $StagingCompleted){throw 'SETUP_RECORDED_STAGING_REQUIRED_NO_REINSTALL'}")
    text = once(text, ' Initialize-FileIdentity;$runtime=Assert-R3InstalledBindings',
                ' Initialize-FileIdentity;$priorMonitoringHold=Read-SetupPriorMonitoringHold;$runtime=Assert-R3InstalledBindings')
    text = once(text, "system_private_preflight_deferred=$true;series_root=$seriesRoot}",
                "system_private_preflight_deferred=$true;series_root=$seriesRoot;prior_monitoring_hold=$priorMonitoringHold;held_observer_recovery_only=$true}")
    text = once(text, "started_utc=[DateTime]::UtcNow.ToString('o')})",
                "started_utc=[DateTime]::UtcNow.ToString('o');prior_monitoring_hold=$priorMonitoringHold;held_observer_recovery_only=$true})")
    text = once(text, "no_automatic_stop=$true}\n $pin=Write-SetupRecord 'series-complete.json'",
                "no_automatic_stop=$true;prior_monitoring_hold=$priorMonitoringHold;held_observer_recovery_only=$true}\n $pin=Write-SetupRecord 'series-complete.json'")
    return text.encode('utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--child-sha256', required=True)
    a = p.parse_args()
    raw = render(a.child_sha256)
    with (W / NEW).open('xb') as stream:
        stream.write(raw)
    print(NEW, hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    main()
