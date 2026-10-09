"""Actual PS5 RawSD validation with inert Scheduler task-file seams.

No task, protected file or system ACL is mutated. A synthetic ordinary file
provides a real deny-write/delete FileStream during the mocked Get-Acl call.
The existing pinned custody boundary is invoked by its exact production
arguments but simulated for the protected task path.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
SOURCE = W / 'task-private-acl-v8.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
PRIVATE = 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'
PROJECTION = 'O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;SY)'
TASK = 'CoChem-4.2.7-Warden'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


spec = importlib.util.spec_from_file_location('task_acl_v8_descriptor_baseline', W / 'test_task_private_acl_v7.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
baseline.SOURCE = SOURCE
for name in dir(baseline):
    if name.startswith('test_'):
        globals()['test_baseline_' + name[5:]] = getattr(baseline, name)


def fixture(tmp_path, *, name=TASK, service=PROJECTION, backing=PRIVATE, fault=''):
    ordinary = tmp_path / 'ordinary-task-fixture.xml'
    ordinary.write_bytes(b'<Task fixture="synthetic"/>')
    code = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
'''
    code += '$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(' + q(SOURCE) + ',[ref]$t,[ref]$e);if($e.Count){throw $e[0]};'
    code += r'''foreach($n in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($n.Extent.Text))}
'''
    code += '$fixturePath=' + q(ordinary) + ';$givenName=' + q(name) + ';$givenService=' + q(service) + ';$givenBacking=' + q(backing) + ';$fault=' + q(fault) + ';'
    code += r'''
$script:events=[Collections.Generic.List[string]]::new();$script:held=$null;$script:writersDenied=$false
$expectedPath='C:\Windows\System32\Tasks\'+$givenName;$expectedHash='a'*64
function Get-Item{
    param([string]$LiteralPath,[switch]$Force,[string]$ErrorAction)
    $script:events.Add('ITEM')
    if($LiteralPath -cne $expectedPath -or -not $Force -or $ErrorAction -cne 'Stop'){throw 'Fixture metadata binding lost'}
    if($fault -ceq 'missing_file'){throw 'Fixture file missing'}
    $length=26;$attributes=[IO.FileAttributes]::Normal;$container=$false
    if($fault -ceq 'empty'){$length=0};if($fault -ceq 'oversize'){$length=131073}
    if($fault -ceq 'negative'){$length=-1};if($fault -ceq 'max_size'){$length=131072}
    if($fault -ceq 'reparse'){$attributes=[IO.FileAttributes]::ReparsePoint}
    if($fault -ceq 'directory'){$attributes=[IO.FileAttributes]::Directory;$container=$true}
    [pscustomobject]@{PSIsContainer=$container;Attributes=$attributes;Length=$length}
}
function Get-FileHash{
    param([string]$LiteralPath,[string]$Algorithm,[string]$ErrorAction)
    $script:events.Add('HASH')
    if($LiteralPath -cne $expectedPath -or $Algorithm -cne 'SHA256' -or $ErrorAction -cne 'Stop'){throw 'Fixture hash binding lost'}
    if($fault -ceq 'hash_failure'){throw 'Fixture hashing failure'}
    $hash=$expectedHash;if($fault -ceq 'invalid_hash'){$hash='not-a-digest'}
    if($fault -ceq 'uppercase_hash'){$hash=$hash.ToUpperInvariant()}
    [pscustomobject]@{Hash=$hash}
}
function Open-VerifiedFile{
    param([string]$Path,[string]$Sha256,[long]$Length)
    $script:events.Add('OPEN')
    $expectedLength=26;if($fault -ceq 'max_size'){$expectedLength=131072}
    if($Path -cne $expectedPath -or $Sha256 -cne $expectedHash -or $Length -ne $expectedLength){throw 'Fixture custody binding lost'}
    if($fault -in @('custody_failure','changed_hash','ancestor_reparse','hardlink','resolved_path_changed')){throw 'Fixture pinned custody refusal'}
    if($fault -ceq 'missing_handle'){return $null}
    $script:held=[IO.File]::Open($fixturePath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    return $script:held
}
function Get-Acl{
    param([string]$LiteralPath,[string]$ErrorAction)
    $script:events.Add('ACL')
    if($LiteralPath -cne $expectedPath -or $ErrorAction -cne 'Stop' -or $null -eq $script:held -or -not $script:held.CanRead){throw 'ACL witness was not collected under custody'}
    try{$writer=[IO.File]::Open($fixturePath,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$writer.Dispose()}catch [IO.IOException]{$script:writersDenied=$true}
    if(-not $script:writersDenied){throw 'Fixture held file permits writes'}
    if($fault -ceq 'acl_failure'){throw 'Fixture ACL read failure'}
    if($fault -ceq 'raw_acl_witness'){
        # Exercise the exact RawSD guard with an unnormalized witness. Real
        # FileSecurity canonicalizes redundant SY read and file OI entries.
        $raw=[pscustomobject]@{Descriptor=$givenBacking}
        $raw|Add-Member ScriptMethod GetSecurityDescriptorSddlForm {
            param($Sections)
            if([int]$Sections -ne 5){throw 'ACL witness section binding lost'}
            return $this.Descriptor
        }
        return $raw
    }
    $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetSecurityDescriptorSddlForm($givenBacking)
    return $acl
}
$accepted=$false;$outputs=@();$message=''
try{$outputs=@(Assert-RegisteredTaskAcl $givenService $givenName);$accepted=$true}catch{$message=$_.Exception.Message}
$closed=$null -eq $script:held -or -not $script:held.CanRead
$v=@{accepted=$accepted;outputs=$outputs.Count;events=@($script:events.ToArray());closed=$closed;writers_denied=$script:writersDenied;message=$message}
$v|ConvertTo-Json -Compress -Depth 5
'''
    encoded = base64.b64encode(code.encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('name', [TASK, 'CoChem-4.2.7-Commission-0123456789abcdef', 'CoChem-4.2.7-fixture_name.1'])
@pytest.mark.parametrize('owner', ['BA', 'SY'])
def test_exact_projection_requires_and_accepts_bound_protected_task_file(tmp_path, name, owner):
    value = fixture(tmp_path, name=name, backing=PRIVATE.replace('O:BA', 'O:'+owner))
    assert value == dict(accepted=True, outputs=0, events=['ITEM','HASH','OPEN','ACL'],
                         closed=True, writers_denied=True, message='')


@pytest.mark.parametrize('fault', ['max_size', 'uppercase_hash'])
def test_bounded_file_and_normalized_digest_pass_under_held_acl_witness(tmp_path, fault):
    value = fixture(tmp_path, fault=fault)
    assert value['accepted'] and value['closed'] and value['writers_denied']
    assert value['events'] == ['ITEM','HASH','OPEN','ACL'] and value['outputs'] == 0


@pytest.mark.parametrize('name', ['', 'Warden', '\\CoChem-4.2.7-Warden', '/CoChem-4.2.7-Warden',
    'CoChem-4.2.7-..', 'CoChem-4.2.7-foo..bar', 'CoChem-4.2.7-foo.',
    'CoChem-4.2.7-/x', 'CoChem-4.2.7-foo\\bar', 'CoChem-4.2.7-foo:bar',
    'CoChem-4.2.7-foo\n', 'CoChem-4.2.7-foo\r', 'CoChem-4.2.7-foo ',
    'cochem-4.2.7-Warden', 'CoChem-4.2.7-', 'CoChem-4.2.7-'+('x'*228),
    'C:\\Windows\\System32\\Tasks\\CoChem-4.2.7-Warden'])
def test_unbound_root_names_refuse_before_any_file_operation(tmp_path, name):
    value = fixture(tmp_path, name=name)
    assert not value['accepted'] and value['events'] == [] and value['closed']


@pytest.mark.parametrize('service', [
    PRIVATE.replace('D:P','D:'),
    PROJECTION.replace('(A;;FR;;;SY)', '(A;;FR;;;BA)'),
    PROJECTION.replace('(A;;FR;;;SY)', '(A;;FW;;;SY)'),
    PROJECTION.replace('(A;;FA;;;BA)', '(A;;FA;;;WD)'),
    PROJECTION.replace('(A;;FR;;;SY)', '(A;ID;FR;;;SY)'),
    PROJECTION+'(A;;FR;;;SY)',
])
def test_only_exact_observed_unprotected_projection_can_request_backing_proof(tmp_path, service):
    value = fixture(tmp_path, service=service)
    assert not value['accepted'] and value['events'] == [] and value['closed']


@pytest.mark.parametrize('fault,events', [
    ('missing_file',['ITEM']),('empty',['ITEM']),('oversize',['ITEM']),('negative',['ITEM']),
    ('reparse',['ITEM']),('directory',['ITEM']),('hash_failure',['ITEM','HASH']),
    ('invalid_hash',['ITEM','HASH']),('custody_failure',['ITEM','HASH','OPEN']),
    ('changed_hash',['ITEM','HASH','OPEN']),('ancestor_reparse',['ITEM','HASH','OPEN']),
    ('hardlink',['ITEM','HASH','OPEN']),('resolved_path_changed',['ITEM','HASH','OPEN']),
    ('missing_handle',['ITEM','HASH','OPEN']),('acl_failure',['ITEM','HASH','OPEN','ACL']),
])
def test_file_bounds_hash_and_native_custody_refusals_stop_before_acl_or_dispose_on_error(tmp_path, fault, events):
    value = fixture(tmp_path, fault=fault)
    assert not value['accepted'] and value['events'] == events and value['closed']
    assert value['outputs'] == 0


@pytest.mark.parametrize('backing', [
    PRIVATE.replace('D:P','D:'), PRIVATE+'(A;;FR;;;SY)',
    PRIVATE.replace('O:BA','O:WD'), PRIVATE.replace('(A;;FA;;;BA)','(A;;FA;;;WD)'),
    PRIVATE.replace('(A;;FA;;;BA)','(A;;FR;;;BA)'),
    PRIVATE.replace('(A;;FA;;;BA)','(A;;FA;;;SY)'),
    PRIVATE.replace('(A;;FA;;;SY)','(A;ID;FA;;;SY)'),
    PRIVATE.replace('(A;;FA;;;SY)','(A;OI;FA;;;SY)'),
    PRIVATE.replace('(A;;FA;;;SY)','(D;;FA;;;SY)'),
    'O:BAG:BAD:P', 'O:BAG:BAD:NO_ACCESS_CONTROL',
])
def test_backing_file_requires_protected_exact_full_control_pair_and_disposes_held_handle(tmp_path, backing):
    raw_witness = backing in {PRIVATE+'(A;;FR;;;SY)', PRIVATE.replace('(A;;FA;;;SY)','(A;OI;FA;;;SY)')}
    value = fixture(tmp_path, backing=backing, fault='raw_acl_witness' if raw_witness else '')
    assert not value['accepted'] and value['closed'] and value['writers_denied']
    assert value['events'] == ['ITEM','HASH','OPEN','ACL'] and value['outputs'] == 0


@pytest.mark.parametrize('backing', [PRIVATE+'(A;;FR;;;SY)',
                                   PRIVATE.replace('(A;;FA;;;SY)','(A;OI;FA;;;SY)')])
def test_real_filesecurity_canonicalization_keeps_protected_system_admin_only_policy(tmp_path, backing):
    # The requested witness is Get-Acl's canonical FileSecurity descriptor,
    # not native raw ACE-byte preservation. FileSecurity merges redundant
    # SYSTEM read into SYSTEM full control and strips file-inapplicable OI;
    # neither changes granted principals, protection or effective rights.
    value = fixture(tmp_path, backing=backing)
    assert value == dict(accepted=True, outputs=0, events=['ITEM','HASH','OPEN','ACL'],
                         closed=True, writers_denied=True, message='')


@pytest.mark.parametrize('service', [PRIVATE, PRIVATE+'(A;;FR;;;SY)'])
def test_protected_service_shapes_keep_v7_no_file_dependency(tmp_path, service):
    value = fixture(tmp_path, service=service, name='../not-used', fault='missing_file')
    assert value == dict(accepted=True, outputs=0, events=[], closed=True, writers_denied=False, message='')


def test_v7_is_frozen_and_v8_uses_only_existing_read_only_custody_boundary():
    assert hashlib.sha256((W/'task-private-acl-v7.ps1').read_bytes()).hexdigest() == '1b6c35f49010102033d3ec07785115e9036ee6df953fb2483ddf835ae2cee0b4'
    source = SOURCE.read_text()
    assert source.index('$stream=Open-VerifiedFile') < source.index('$acl=Get-Acl')
    assert 'finally{if($null -ne $stream){$stream.Dispose()}}' in source
    assert "[IO.Path]::Combine('C:\\Windows\\System32\\Tasks',$TaskName)" in source
    assert 'SetSecurityDescriptor' not in source and 'RegisterTaskDefinition' not in source
    assert 'Schedule.Service' not in source and 'Set-Acl' not in source and 'Write-' not in source
