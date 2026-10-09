"""Create a future monitoring batch; never execute authentication or first start."""
import argparse
import hashlib
import json
from pathlib import Path

W=Path(__file__).resolve().parent
BASE='0f5b7f7fd4938f49b9c364694c8a9591b61d47e40d1b338239179f74087647fc'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def replace(text,old,new):
    assert old in text,old
    return text.replace(old,new)

def main():
    parser=argparse.ArgumentParser()
    for option in ('observer','staging','held'):parser.add_argument('--'+option+'-sha256',required=True)
    args=parser.parse_args()
    files={
        'install-resource-observer-r3-v3.ps1':args.observer_sha256,
        'install-independent-supervisor-staging-r3-v2.ps1':args.staging_sha256,
        'install-held-supervisor-observation-r3-v2.ps1':args.held_sha256,
        'run-pipeline-commissioning-r3-v4.ps1':'fdd8f5fcacae85f2d80cb908d87d6fdad0b06c0783d2ff20fa2d59a458c4939d',
    }
    for name,pin in files.items():assert sha(W/name)==pin,name
    source=W/'run-protected-acceptance-setup-r3-v1.ps1'
    assert sha(source)==BASE
    text=source.read_text(encoding='utf-8-sig')
    for old,new,pin in [
        ('install-resource-observer-r3-v2.ps1','install-resource-observer-r3-v3.ps1','5030df59a14fef74b1147660978becf95a839e9d6e7f9e403708ac5b319153f2'),
        ('install-independent-supervisor-staging-r3-v1.ps1','install-independent-supervisor-staging-r3-v2.ps1','4f8d3afa512ce81a73c114a384e208c39006ab3abe4771512a121ebf7ae7b098'),
        ('install-held-supervisor-observation-r3-v1.ps1','install-held-supervisor-observation-r3-v2.ps1','88c285591b579a432718dc5ab383b844e597299f5372c6a6f4dd98e9efdd38e5'),
        ('run-pipeline-commissioning-r3-v1.ps1','run-pipeline-commissioning-r3-v4.ps1','44ed0b7d79b33afee6e35b36ddf2d37056e1e51f31806f05a21c2038c98d9bf1'),
    ]:
        text=replace(text,old,new);text=replace(text,pin,files[new])
    manifest=W/'resource-observer-r3-v3/source-manifest.json'
    for row in json.loads(manifest.read_bytes())['files']:
        data=(manifest.parent/row['path']).read_bytes()
        assert len(data)==row['size'] and hashlib.sha256(data).hexdigest()==row['sha256']
    text=replace(text,'b2f1329893ce13662271070a36821cb7129410bc7d5160ce37d17b9552f55f52',sha(manifest))
    text=replace(text,'ResourceObservation4.2.7-windows-20261008-r3-v2','ResourceObservation4.2.7-windows-20261008-r3-v3')
    text=replace(text,'ProtectedAcceptanceSetup4.2.7-windows-20261008-r3-v1','PostCommissioningSetup4.2.7-windows-20261008-r3-v1')
    text=replace(text,"if($Branch -cnotin @('fresh','commissioned')){throw 'SETUP_BRANCH'}","if($Branch -cne 'commissioned'){throw 'SETUP_COMMISSIONING_REQUIRED_NO_FIRST_START'}")
    text=replace(text,"  if($phase.mode -ceq 'deferred_maintenance')", "  if($phase.phase -ceq 'first_start' -and $phase.mode -cne 'reattest_current_instance'){throw 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}\n  if($phase.mode -ceq 'deferred_maintenance')")
    start=text.index(" $branch=if((Get-SetupPathState $commissioning)")
    end=text.index(' $staged=$false',start)
    text=text[:start]+r""" $commissioningPresent=((Get-SetupPathState $commissioning) -ceq 'present')
 $branch='commissioned'
 $folder=$null;$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 $commissionedVerified=$false
 if($commissioningPresent){
  try{$witness=Open-SetupControllerWitness $folder;$commissionedVerified=$true}catch{$holds.Add([pscustomobject]@{phase='preflight';code='CURRENT_COMMISSIONED_INSTANCE_NOT_REATTESTED'})}
 }else{
  $holds.Add([pscustomobject]@{phase='preflight';code='COMMISSIONING_REQUIRED_NO_AUTHENTICATION_OR_STARTUP_IN_THIS_BATCH'})
  if((Get-SetupPathState (Split-Path -Parent $commissioning)) -ceq 'present'){$holds.Add([pscustomobject]@{phase='preflight';code='PARTIAL_FIRST_START_PRESERVE'})}
 }
"""+text[end:]
    text=replace(text,'first_start_consent_console_preserved=$true','authentication_or_first_start_invoked=$false;commissioning_required_before_apply=$true')
    text=replace(text,"  if($name -ceq 'first_start'){$args+=@('-Interactive')}","  if($name -ceq 'first_start'){throw 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}")
    text=replace(text,'Optional protected acceptance/setup series around immutable reviewed helpers.','Post-commissioning monitoring setup around immutable reviewed helpers.')
    target=W/'run-post-commissioning-setup-r3-v1.ps1'
    with target.open('x',encoding='utf-8',newline='\n') as stream:stream.write(text)
    print(json.dumps({'path':str(target),'sha256':sha(target),'observer_manifest_sha256':sha(manifest)}))

if __name__=='__main__':main()
