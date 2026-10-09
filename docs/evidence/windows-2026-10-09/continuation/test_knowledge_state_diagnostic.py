"""Inert Windows diagnostics tests; no task/SYSTEM or production index calls."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('diagnostic',ROOT/'diagnose-knowledge-state.py')
diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)


def original():
    return {'schema':'cochem-private-knowledge-system-acceptance/1','nonce':diag.ORIGINAL_NONCE,
        'system_sid':'S-1-5-18','status':'KNOWLEDGE_ACCEPTANCE_FAILED','helper_sha256':diag.ORIGINAL_HELPER,
        'payload_inventory_sha256':diag.INVENTORY,'pipeline_config_sha256':diag.CONFIG,
        'index_created_new':True,'started_at_unix_ms':1791378634141,'finished_at_unix_ms':1791378635667,
        'failure':{'phase':'new_index','error_type':'WindowsIsolationError','sensitive_extra':'never-publish'},
        'extra_secret':'never-publish'}


def test_original_receipt_is_allowlisted():
    value=diag.check_original(original())
    assert 'never-publish' not in json.dumps(value) and 'extra_secret' not in value


@pytest.mark.parametrize('field,value',[('nonce','b'*32),('helper_sha256','0'*64),('payload_inventory_sha256','0'*64),
 ('pipeline_config_sha256','0'*64),('system_sid','S-1-5-20'),('status','PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED'),
 ('index_created_new',1),('started_at_unix_ms',1791378634142),('finished_at_unix_ms',0),('failure',{})])
def test_changed_original_failure_is_refused(field,value):
    record=original();record[field]=value
    with pytest.raises(ValueError):diag.check_original(record)


def test_duplicate_control_receipt_refused():
    with pytest.raises(ValueError):diag.strict_json('{"nonce":1,"nonce":2}')


def test_actual_windows_handle_metadata_never_reads_file_data(tmp_path,monkeypatch):
    sys.path.insert(0,r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\src')
    from cochem_pipeline import windows as win
    win._api()
    path=tmp_path/'knowledge_index.db';path.write_bytes(b'NON_DATABASE_SYNTHETIC_FILE_BYTES')
    def forbidden(*args,**kwargs):raise AssertionError('File content read attempted')
    monkeypatch.setattr(Path,'read_bytes',forbidden);monkeypatch.setattr(Path,'open',forbidden)
    value=diag.metadata(path,win)
    assert value['desired_access']==0x20080 and value['bytes']==len(b'NON_DATABASE_SYNTHETIC_FILE_BYTES')
    assert value['content_read'] is False and value['content_hashed'] is False
    assert value['aces'] and value['owner_sid'].startswith('S-1-')
    assert 'NON_DATABASE' not in json.dumps(value)


def fake_metadata(path):
    return {'directory':path.is_dir(),'reparse':False,'content_read':False,'content_hashed':False}


def test_depth_names_and_generation_scope_are_bounded(tmp_path,monkeypatch):
    generation=tmp_path/('g-'+'a'*32);generation.mkdir()
    (generation/'knowledge_index.db').write_bytes(b'do not open')
    deeper=generation/'unexpected-private-name';deeper.mkdir();(deeper/'nested-private-file').write_bytes(b'do not open')
    unknown=tmp_path/'unknown-sensitive-name';unknown.mkdir();(unknown/'hidden-file').write_bytes(b'do not open')
    (tmp_path/'sources.json').write_bytes(b'do not open')
    monkeypatch.setattr(Path,'read_bytes',lambda *args:(_ for _ in ()).throw(AssertionError('content read')))
    rows=diag.inspect_state(tmp_path,fake_metadata)
    raw=json.dumps(rows)
    assert len(rows)==6
    assert 'unknown-sensitive-name' not in raw and 'unexpected-private-name' not in raw
    assert 'hidden-file' not in raw and 'nested-private-file' not in raw
    assert '/knowledge_index.db' in raw and 'name-sha256:' in raw


def test_unknown_reparse_directory_is_not_followed(tmp_path,monkeypatch):
    child=tmp_path/('g-'+'a'*32);child.mkdir();(child/'knowledge_index.db').write_bytes(b'fixture')
    def inspect(path):return {'directory':True,'reparse':path==child}
    rows=diag.inspect_state(tmp_path,inspect)
    assert len(rows)==2 and rows[-1]['reparse']


def test_missing_is_distinct_from_denied(tmp_path):
    def missing(path):raise FileNotFoundError()
    def denied(path):raise PermissionError('unpublished-sensitive-path')
    assert diag.inspect_state(tmp_path,missing)[0]['state']=='NOT_FOUND'
    value=diag.inspect_state(tmp_path,denied)[0]
    assert value['state']=='INACCESSIBLE_OR_ERROR' and 'sensitive' not in json.dumps(value)


def test_entry_bound_fails_before_broad_scan(tmp_path):
    for n in range(4):(tmp_path/f'file{n}').write_bytes(b'')
    with pytest.raises(ValueError,match='bound'):diag.inspect_state(tmp_path,fake_metadata,maximum=3)


def test_synthetic_directories_are_fresh_and_never_existing_state(tmp_path):
    seen=[]
    def validate(path):
        seen.append(path.name)
        if path.name=='fixture-mode700':raise PermissionError('synthetic fixture rejection')
    rows=diag.inspect_synthetic_directories(tmp_path,SimpleNamespace(validate_private_directory=validate),fake_metadata)
    assert seen==['fixture-inherited','fixture-mode700']
    assert [row['installed_validator'] for row in rows]==['PASSED','REJECTED']
    assert all(not list((tmp_path/name).iterdir()) for name in seen)
    with pytest.raises(FileExistsError):diag.inspect_synthetic_directories(tmp_path,SimpleNamespace(validate_private_directory=validate),fake_metadata)


def ps(body):
    path=str(ROOT/'diagnose-knowledge-state.ps1').replace("'","''")
    preamble=f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
      $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors);
      if($errors.Count){{throw ($errors|Out-String)}};
      foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
      $originalNonce='{diag.ORIGINAL_NONCE}';$originalHelperHash='{diag.ORIGINAL_HELPER}';$inventoryHash='{diag.INVENTORY}';$configHash='{diag.CONFIG}';
    """
    return subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',preamble+body],capture_output=True,text=True,timeout=30)


@pytest.mark.parametrize('alter', ['',"$r.nonce='wrong';","$r.index_created_new=1;","$r.failure.phase='configuration';"])
def test_ps51_original_failure_binding(alter):
    value=json.dumps(original()).replace("'","''")
    result=ps(f"$r='{value}'|ConvertFrom-Json;"+alter+"$ok=$true;try{Assert-OriginalReceipt $r}catch{$ok=$false};$ok|ConvertTo-Json -Compress")
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==(not alter)


def test_nonadmin_apply_stops_before_privileged_reads():
    result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-File',str(ROOT/'diagnose-knowledge-state.ps1'),'-Apply'],capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'Apply requires the owner' in result.stderr


def test_wrapper_source_pin_matches():
    assert hashlib.sha256((ROOT/'diagnose-knowledge-state.py').read_bytes()).hexdigest() in (ROOT/'diagnose-knowledge-state.ps1').read_text()


def test_control_buffer_hash_and_bound(tmp_path):
    path=tmp_path/'receipt.json';path.write_bytes(b'{"test":1}')
    win=SimpleNamespace(validate_code_path=lambda path:None)
    raw=diag.read_control(path,hashlib.sha256(path.read_bytes()).hexdigest(),win)
    assert diag.strict_json(raw)=={'test':1}
    with pytest.raises(ValueError,match='bounded'):diag.read_control(path,'0'*64,win,maximum=2)
    with pytest.raises(ValueError,match='pin'):diag.read_control(path,'0'*64,win)
    os.link(path,tmp_path/'alias')
    with pytest.raises(ValueError,match='single-link'):diag.read_control(path,'0'*64,win)


@pytest.mark.parametrize('extra_sid,accepted',[('',True),('S-1-1-0',False),('S-1-3-4',False)])
def test_actual_inherited_receipt_acl_accepts_only_effective_private_grants(tmp_path,extra_sid,accepted):
    # Actual inherited ACL fixture. Substitute this unprivileged fixture owner
    # for Administrators in the extracted function only; no SYSTEM ownership or
    # production access is claimed. The SYSTEM grant and full-control rule stay.
    parent=str(tmp_path/'private-fixture').replace("'","''")
    result=ps(f"$parent='{parent}';$extra='{extra_sid}';"+r'''
      Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1');
      $fixtureSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
      $f=$ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-EffectivePrivateReceipt'},$true)[0];
      . ([scriptblock]::Create($f.Extent.Text.Replace("'S-1-5-32-544'",("'"+$fixtureSid+"'"))));
      function Assert-NoReparseAncestors {param($Path)$item=Get-Item -LiteralPath $Path;while($null -ne $item){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Fixture link'};if($item -is [IO.DirectoryInfo]){$item=$item.Parent}else{$item=$item.Directory}}};
      $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetOwner([Security.Principal.SecurityIdentifier]::new($fixtureSid));$acl.SetAccessRuleProtection($true,$false);
      foreach($sid in @($fixtureSid,'S-1-5-18')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))};
      if($extra){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($extra),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))};
      $null=[IO.Directory]::CreateDirectory($parent,$acl);$file=Join-Path $parent 'receipt.json';[IO.File]::WriteAllText($file,'{}');
      $before=Get-Acl -LiteralPath $file;$sddl=$before.Sddl;$ok=$true;try{Assert-EffectivePrivateReceipt $file}catch{$ok=$false};
      [ordered]@{accepted=$ok;inherited=(-not $before.AreAccessRulesProtected);unchanged=((Get-Acl -LiteralPath $file).Sddl -ceq $sddl)}|ConvertTo-Json -Compress
    ''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'accepted':accepted,'inherited':True,'unchanged':True}
