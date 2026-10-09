"""Actual Windows PS5 RawSecurityDescriptor fixtures; never calls Scheduler.

Only the one exported read-only validator is imported. Descriptors are
synthetic, including binary ACE fixtures for callback/object/null DACL cases.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
SOURCE = W / 'task-private-acl-v7.ps1'
OLD = W / 'register-stopped-warden-r3.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
BASE = 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'
READ = '(A;;FR;;;SY)'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    code = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
$t=$null;$e=$null;
'''
    code += '$a=[Management.Automation.Language.Parser]::ParseFile(' + q(SOURCE) + ',[ref]$t,[ref]$e);if($e.Count){throw $e[0]};'
    code += r'''
$definitions=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true))
if($definitions.Count -ne 1 -or $definitions[0].Name -cne 'Assert-RegisteredTaskAcl'){throw 'Unexpected export surface'}
. ([scriptblock]::Create($definitions[0].Extent.Text))
function Test-Descriptor([string]$Sddl){
    $accepted=$false;$outputs=@();$errorType='';$errorText=''
    try{$outputs=@(Assert-RegisteredTaskAcl $Sddl);$accepted=$true}catch{$errorType=$_.Exception.GetType().Name;$errorText=$_.Exception.Message}
    [ordered]@{accepted=$accepted;output_count=$outputs.Count;error_type=$errorType;error_text=$errorText}
}
'''
    encoded = base64.b64encode((code + body).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('sddl', [
    BASE, BASE+READ,
    'O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:SYG:BAD:P(A;;FR;;;SY)(A;;FA;;;BA)(A;;FA;;;SY)',
    'O:BAG:BAD:P(A;;FA;;;BA)(A;;FR;;;SY)(A;;FA;;;SY)',
    'O:BAG:BAD:P(A;;0x1f01ff;;;SY)(A;;0x1f01ff;;;BA)(A;;0x120089;;;SY)',
    'O:S-1-5-32-544G:S-1-5-18D:P(A;;FA;;;S-1-5-18)(A;;FA;;;S-1-5-32-544)(A;;FR;;;S-1-5-18)',
])
def test_exact_full_control_pair_and_optional_single_system_read_are_accepted(sddl):
    value = run('Test-Descriptor ' + q(sddl) + '|ConvertTo-Json -Compress')
    assert value == dict(accepted=True, output_count=0, error_type='', error_text='')


@pytest.mark.parametrize('sddl', [
    '', 'not-sddl', 'D:P(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:WDG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:BUG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:S-1-5-21-1-2-3-1001G:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:AI(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P', 'O:BAG:BA', 'O:BAG:BAD:NO_ACCESS_CONTROL',
    'O:BAG:BAD:P(A;;FA;;;SY)',
    'O:BAG:BAD:P(A;;FA;;;SY)(A;;FR;;;BA)',
    'O:BAG:BAD:P(A;;FR;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;;FR;;;SY)(A;;FA;;;SY)',
    'O:BAG:BAD:P(A;;FA;;;BA)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;SY)',
    BASE+'(A;;FA;;;SY)', BASE+'(A;;FA;;;BA)',
    BASE+READ+READ,
    BASE+'(A;;FR;;;BA)', BASE+'(A;;FR;;;WD)', BASE+'(A;;FR;;;BU)',
    BASE+'(A;;FR;;;AU)', BASE+'(A;;FR;;;S-1-5-21-1-2-3-1001)',
    BASE+'(A;;FX;;;SY)', BASE+'(A;;FW;;;SY)', BASE+'(A;;FA;;;WD)',
    BASE+'(A;;GR;;;SY)', BASE+'(A;;0x120088;;;SY)', BASE+'(A;;0x12008b;;;SY)',
    'O:BAG:BAD:P(A;;0x1f01fe;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;;GA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(D;;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(AU;SA;FA;;;SY)(A;;FA;;;BA)',
    BASE+'(D;;FR;;;SY)',
    'O:BAG:BAD:P(A;ID;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;CI;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;OI;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;IO;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;NP;FA;;;SY)(A;;FA;;;BA)',
    BASE+'(A;ID;FR;;;SY)', BASE+'(A;CI;FR;;;SY)', BASE+'(A;OI;FR;;;SY)',
    BASE+'(A;IO;FR;;;SY)', BASE+'(A;NP;FR;;;SY)',
])
def test_all_unsafe_missing_duplicate_partial_inherited_and_other_grants_are_refused(sddl):
    value = run('Test-Descriptor ' + q(sddl) + '|ConvertTo-Json -Compress')
    assert value['accepted'] is False and value['output_count'] == 0
    assert value['error_type'] and value['error_text']


@pytest.mark.parametrize('sddl', [
    'O:BAG:BAD:P(XA;;FA;;;SY;(@User.fixture == "x"))(A;;FA;;;BA)',
    BASE+'(XA;;FR;;;SY;(@User.fixture == "x"))',
])
def test_real_conditional_callback_aces_are_parsed_then_refused(sddl):
    body = '$sddl=' + q(sddl) + ';$sd=[Security.AccessControl.RawSecurityDescriptor]::new($sddl);'
    body += r'''$callbacks=@($sd.DiscretionaryAcl|Where-Object{$_.AceType -eq [Security.AccessControl.AceType]::AccessAllowedCallback})
    $v=Test-Descriptor $sddl;@{callbacks=$callbacks.Count;accepted=$v.accepted;outputs=$v.output_count}|ConvertTo-Json -Compress'''
    assert run(body) == dict(callbacks=1, accepted=False, outputs=0)


@pytest.mark.parametrize('kind', ['object_full', 'object_read',
                                 'null_dacl', 'null_owner', 'missing_ba', 'missing_sy'])
def test_binary_descriptor_variants_are_refused_after_real_sddl_serialization(kind):
    body = '$kind=' + q(kind) + ';$sd=[Security.AccessControl.RawSecurityDescriptor]::new(' + q(BASE) + ');'
    body += r'''
    $sy=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $allowed=[Security.AccessControl.AceQualifier]::AccessAllowed
    $none=[Security.AccessControl.AceFlags]::None
    if($kind -ceq 'object_full' -or $kind -ceq 'object_read'){
        $mask=1179785;$position=2
        if($kind -ceq 'object_full'){$mask=2032127;$position=0;$sd.DiscretionaryAcl.RemoveAce(0)}
        $flags=[Security.AccessControl.ObjectAceFlags]::ObjectAceTypePresent
        $ace=[Security.AccessControl.ObjectAce]::new($none,$allowed,$mask,$sy,$flags,[Guid]::new('11111111-1111-1111-1111-111111111111'),[Guid]::Empty,$false,$null)
        $sd.DiscretionaryAcl.InsertAce($position,$ace)
    }elseif($kind -ceq 'null_dacl'){$sd.DiscretionaryAcl=$null
    }elseif($kind -ceq 'null_owner'){$sd.Owner=$null
    }elseif($kind -ceq 'missing_ba'){$sd.DiscretionaryAcl.RemoveAce(1);$sd.DiscretionaryAcl.InsertAce(1,[Security.AccessControl.CommonAce]::new($none,$allowed,1179785,$sy,$false,$null))
    }elseif($kind -ceq 'missing_sy'){$sd.DiscretionaryAcl.RemoveAce(0);$sd.DiscretionaryAcl.InsertAce(1,[Security.AccessControl.CommonAce]::new($none,$allowed,1179785,$sy,$false,$null))}
    $sddl=$sd.GetSddlForm([Security.AccessControl.AccessControlSections]::All)
    $v=Test-Descriptor $sddl;$v|ConvertTo-Json -Compress
    '''
    value = run(body)
    assert value['accepted'] is False and value['output_count'] == 0


def test_normalization_preserves_exact_masks_and_required_identities():
    body = '$sd=[Security.AccessControl.RawSecurityDescriptor]::new(' + q(BASE+READ) + ');'
    body += r'''
    $normalized=$sd.GetSddlForm([Security.AccessControl.AccessControlSections]::All)
    $again=[Security.AccessControl.RawSecurityDescriptor]::new($normalized)
    $v=Test-Descriptor $normalized
    @{accepted=$v.accepted;owner=$again.Owner.Value;protected=[bool]($again.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected);aces=@($again.DiscretionaryAcl|ForEach-Object{@{sid=$_.SecurityIdentifier.Value;mask=$_.AccessMask;flags=[int]$_.AceFlags;type=[int]$_.AceType}})}|ConvertTo-Json -Compress -Depth 6
    '''
    value = run(body)
    assert value == dict(accepted=True, owner='S-1-5-32-544', protected=True, aces=[
        dict(sid='S-1-5-18', mask=2032127, flags=0, type=0),
        dict(sid='S-1-5-32-544', mask=2032127, flags=0, type=0),
        dict(sid='S-1-5-18', mask=1179785, flags=0, type=0)])


def test_only_one_pure_export_with_no_scheduler_or_descriptor_mutation():
    value = run(r'''@{exports=@($definitions|ForEach-Object Name);outside=@($a.EndBlock.Statements|Where-Object{$_ -isnot [Management.Automation.Language.FunctionDefinitionAst]}).Count}|ConvertTo-Json -Compress''')
    assert value == dict(exports=['Assert-RegisteredTaskAcl'], outside=0)
    source = SOURCE.read_text()
    assert 'Schedule.Service' not in source and 'RegisterTaskDefinition' not in source
    assert 'SetSecurityDescriptor' not in source and 'InsertAce' not in source
    assert 'Start-Process' not in source and 'Remove-Item' not in source
    assert '.SetFlags' not in source and 'Write-' not in source
    assert 2032127 == 0x1f01ff and 1179785 == 0x120089


def test_legacy_two_ace_validator_and_source_are_unchanged():
    # The additive helper changes neither the reviewed old function nor the
    # scheduler registration flags held inside its preserved wrapper.
    assert hashlib.sha256(OLD.read_bytes()).hexdigest() == 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198'
