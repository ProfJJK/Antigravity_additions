"""Workspace-only preparation from pinned v2; no deployment or authentication."""
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent
ZERO='0'*32

def digest(name):return hashlib.sha256((HERE/name).read_bytes()).hexdigest()

def main():
    raw=(HERE/'commission-first-warden-r3-v2.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='ff726500b649d5f037f3e11561afb71b2a6bf9f95dbf3e37edbc242ad4e1f8da'
    text=raw.decode('utf-8-sig')
    text=text.replace("AUTH_HELPER_HASH = '98d6e5400041fc51692703c0fd8d8ea2ade6e59bc3de49fb3efdd421dcd5f945'",f"AUTH_HELPER_HASH = '{digest('worker-native-auth-status-six-r3-v3.py')}'")
    text=text.replace("AUTH_ROOT = ROOT.parent / 'NativeAuthBoth4.2.7-windows-20261008-r3-v2'", "AUTH_PREFIX = 'NativeAuthBoth4.2.7-windows-20261008-r3-v3-'")
    text=text.replace('def validate_auth(read):',"def validate_auth(read, attempt):\n    require(type(attempt) is str and re.fullmatch('[a-f0-9]{32}',attempt) and attempt!='0'*32,'AUTH_ATTEMPT')\n    auth_root=ROOT.parent/(AUTH_PREFIX+attempt)")
    text=text.replace('AUTH_ROOT/', 'auth_root/')
    text=text.replace('NativeAuthSix4.2.7-windows-20261008-r3-v2-{provider}', 'NativeAuthSix4.2.7-windows-20261008-r3-v3-{attempt}-{provider}')
    text=text.replace('NativeAuthStatusSix4.2.7-windows-20261008-r3-v2-{slot}', 'NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-{attempt}-{slot}')
    text=text.replace("'model_jobs_executed':0,'configuration_changed':False", "'attempt':attempt,'model_jobs_executed':0,'configuration_changed':False",1)
    text=text.replace("'selected_workers_verified':6,'configuration_applied':False", "'attempt':attempt,'selected_workers_verified':6,'configuration_applied':False",1)
    text=text.replace("'provider':provider,'slot':slot,'stage':stage,'system_sid'", "'attempt':attempt,'provider':provider,'slot':slot,'stage':stage,'system_sid'",1)
    text=text.replace("auth=validate_auth(lambda path,pin:read_control(win,support,path,pin))", "auth=validate_auth(lambda path,pin:read_control(win,support,path,pin),packet.get('auth_attempt'))")
    text=text.replace("report['authentication_receipt_sha256']=auth['receipt_sha256'];", "report['authentication_attempt']=packet['auth_attempt'];report['authentication_receipt_sha256']=auth['receipt_sha256'];")
    with (HERE/'commission-first-warden-r3-v3.py').open('x',encoding='utf-8',newline='') as stream:stream.write(text)
    raw=(HERE/'commission-first-warden-r3-v2.ps1').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='97a4a0976437f404e63e849d01495188b0a26c7945e1c3ebe59b564cf471c085'
    text=raw.decode('utf-8-sig')
    text=text.replace('param([switch]$Apply)',f"param([switch]$Apply,[ValidatePattern('^[a-f0-9]{{32}}$')][string]$Attempt='{ZERO}')",1)
    text=text.replace("$ErrorActionPreference='Stop';Set-StrictMode -Version Latest",f"$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\nif($Apply -and $Attempt -ceq '{ZERO}'){{throw 'Apply requires a nonzero bound authentication attempt.'}}",1)
    text=text.replace("$path='C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v2\\series-complete.json'", "$path=\"C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt\\series-complete.json\"")
    text=text.replace("$authPath='C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v2\\series-complete.json'", "$authPath=\"C:\\Program Files\\CoChem\\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt\\series-complete.json\"")
    text=text.replace('NativeAuthSix4.2.7-windows-20261008-r3-v2-$provider','NativeAuthSix4.2.7-windows-20261008-r3-v3-$Attempt-$provider')
    text=text.replace('NativeAuthStatusSix4.2.7-windows-20261008-r3-v2-$($r.slot)','NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$($r.slot)')
    text=text.replace("if($v.schema -cne 'cochem-both", "if($v.attempt -cne $Attempt -or $v.schema -cne 'cochem-both",1)
    text=text.replace("if($p.status -cne 'SIX_CONFIGURED", "if($p.attempt -cne $Attempt -or $p.status -cne 'SIX_CONFIGURED",1)
    text=text.replace("auth_receipt_sha256=$AuthHash;model_jobs_submitted=0", "auth_receipt_sha256=$AuthHash;auth_attempt=$script:Attempt;model_jobs_submitted=0")
    text=text.replace("$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v2.py';$sourceHash='ff726500b649d5f037f3e11561afb71b2a6bf9f95dbf3e37edbc242ad4e1f8da'", f"$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v3.py';$sourceHash='{digest('commission-first-warden-r3-v3.py')}'")
    with (HERE/'commission-first-warden-r3-v3.ps1').open('x',encoding='utf-8',newline='') as stream:stream.write(text)
    print(digest('commission-first-warden-r3-v3.py'))

if __name__=='__main__':main()
