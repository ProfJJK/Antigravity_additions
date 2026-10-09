"""Generate the new attended continuation from pinned originals. No deployment."""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

def digest(name):
    return hashlib.sha256((HERE/name).read_bytes()).hexdigest()

def main():
    raw=(HERE/'run-pipeline-commissioning-r3-v1.ps1').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='44ed0b7d79b33afee6e35b36ddf2d37056e1e51f31806f05a21c2038c98d9bf1'
    text=raw.decode('utf-8-sig')
    for stem in ('authenticate-native-profiles-status-first','login-six-workers-status-first','commission-first-warden'):
        text=text.replace(stem+'-r3-v1.ps1',stem+'-r3-v2.ps1')
    for kind in ('Both','Six','StatusSix'):
        text=text.replace(f'NativeAuth{kind}4.2.7-windows-20261007-r3-v1',f'NativeAuth{kind}4.2.7-windows-20261008-r3-v2')
    text=text.replace('CommissioningSeries4.2.7-windows-20261007-r3-v1','CommissioningSeries4.2.7-windows-20261008-r3-v2')
    for old,new in (
        ('85801beaa1bfaaaa41ad0727162e2e6ac244051078465c0d892c66a32c34b44f','authenticate-native-profiles-status-first-r3-v2.ps1'),
        ('9e742d4df0b7faa07a6073dc4bc7f15ccf2924df9a14443a5c662bfbc7ff0361','login-six-workers-status-first-r3-v2.ps1'),
        ('75efe248449fa9be0317a85feb277d82a961d05594ee754d11b3b9d0ec4dfd1e','commission-first-warden-r3-v2.ps1'),
    ):
        text=text.replace(old,digest(new))
    functions=(HERE/'login-recovery-prior-functions-20261008.ps1').read_text(encoding='utf-8')
    text=text.replace('function Read-CommissioningAuthEvidence {',functions+'\nfunction Read-CommissioningAuthEvidence {',1)
    needle=' $holds=@(Get-CommissioningPreviewHolds $authPlan $activationPlan)'
    assert text.count(needle)==1
    text=text.replace(needle,needle+"\n $priorCustody=$null\n try{$priorCustody=Read-RecoveryPriorCustody}catch{if($Apply){throw};$holds+='Prior failed-login receipts require owner read-only verification.'}")
    needle=" Write-SeriesRecord (Join-Path $seriesRoot 'preflight-authentication.json') $authPlan"
    assert text.count(needle)==1
    text=text.replace(needle," Write-SeriesRecord (Join-Path $seriesRoot 'preserved-prior-attempt.json') $priorCustody\n"+needle)
    text=text.replace('<# One attended series: reuse/authenticate native profiles, then commission one\r\n   Warden instance. Default is metadata-only. Never repeat a partial series. #>',
                      '<# Reviewed successor after the preserved slot1 device-login failure. Fresh\r\n   status checks reuse valid sessions; browser authorization remains attended.\r\n   Starts one controller only after authentication succeeds. No automatic retry. #>')
    with (HERE/'run-pipeline-commissioning-r3-v2.ps1').open('x',encoding='utf-8',newline='') as stream:
        stream.write(text)
    print(digest('run-pipeline-commissioning-r3-v2.ps1'))

if __name__=='__main__':
    main()
