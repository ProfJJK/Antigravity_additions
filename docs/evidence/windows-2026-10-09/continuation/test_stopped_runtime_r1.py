"""Inert PS5.1 flow/security fixtures. No installation, task or privileged API."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import hashlib

import pytest

ROOT = Path(__file__).parent
SCRIPT = ROOT / 'install-stopped-runtime-r1.ps1'
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')


def run(body):
    source = str(SCRIPT).replace("'", "''")
    prelude = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
      $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors);
      if($errors.Count){{throw ($errors|Out-String)}};
      foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command', prelude + body],
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('case', ['valid','unsafe','duplicate','incomplete','baseline','missing'])
def test_inventory_is_complete_confined_and_bound_to_prior_source(case):
    result = run(f"$case='{case}';" + r'''
      $script:repo='D:\repo';$script:targetRoot='C:\Program Files\CoChem\new';$script:sourceRoot=$script:targetRoot+'\source';
      $required=@('README.md','pyproject.toml','uv.lock','src/cochem_pipeline/ramdisk.py','src/cochem_pipeline/deployment.py','src/cochem_pipeline/config.py','src/cochem_pipeline/deployment_revision.py','src/cochem_supervisor/engine.py');
      $rows=@($required|ForEach-Object {[pscustomobject]@{relative=$_;sha256=('a'*64);length=1}});
      foreach($i in 1..92){$rows+=[pscustomobject]@{relative="src/package/item$i.py";sha256=('a'*64);length=1}};
      $inventory=[pscustomobject]@{schema='cochem-stopped-runtime-source/1';host='AETHERDESK';complete_source_freeze=$true;source_repository=$script:repo;target_root=$script:targetRoot;previous_source_manifest_sha256='ee994719727936497b0436448a15e80dc18f11a8b108601fd43e5a62b918260b';files=$rows};
      switch($case){'unsafe'{$rows[20].relative='src/../../secret'}'duplicate'{$rows[20].relative='SRC/COCHEM_PIPELINE/ramdisk.py'}'incomplete'{$inventory.complete_source_freeze=$false}'baseline'{$inventory.previous_source_manifest_sha256=('b'*64)}'missing'{$rows[0].relative='src/other.py'}};
      $refused=$false;$count=0;try{$files=@(Get-RuntimeFiles $inventory);$count=$files.Count}catch{$refused=$true};
      [ordered]@{refused=$refused;count=$count}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'refused': case != 'valid', 'count': 100 if case == 'valid' else 0}


@pytest.mark.parametrize('case', ['valid','capacity','token','route','subtree','existing'])
def test_only_one_configuration_delta_is_accepted(case):
    result = run(f"$case='{case}';" + r'''
      $before=[ordered]@{ramdisk=[ordered]@{enabled=$true;mount_root='R:\';lifecycle='adopt_existing'};max_execution_slots=4;token_file='unchanged';routing=[ordered]@{authority='Chapter06'}};
      $after=($before|ConvertTo-Json -Depth 10)|ConvertFrom-Json;
      $after.ramdisk|Add-Member NoteProperty workspace_subdirectory 'CoChem427-windows-20261007';
      switch($case){'capacity'{$after.max_execution_slots=6}'token'{$after.token_file='different'}'route'{$after.routing.authority='forced'}'subtree'{$after.ramdisk.workspace_subdirectory='../escape'}'existing'{$before.ramdisk['workspace_subdirectory']='old'}};
      $refused=$false;try{Assert-ScopedConfigDelta ($before|ConvertTo-Json -Depth 10) ($after|ConvertTo-Json -Depth 10)}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case != 'valid')


@pytest.mark.parametrize('case', ['absent','disabled','enabled','running','queued','unknown','instances','denied','wrong_hresult'])
def test_all_managed_daemon_states_fail_closed(case):
    result = run(f"$script:case='{case}';" + r'''
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)
        if($script:case -in @('absent','denied','wrong_hresult')){$code=if($script:case -eq 'absent'){-2147024894}elseif($script:case -eq 'denied'){-2147024891}else{-2147216625};throw [Runtime.InteropServices.COMException]::new('fixture',$code)};
        $state=switch($script:case){'running'{4}'queued'{2}'unknown'{0}default{3}};
        $task=[pscustomobject]@{Enabled=($script:case -eq 'enabled');State=$state};
        $task|Add-Member ScriptMethod GetInstances {param($unused)@{Count=$(if($script:case -eq 'instances'){1}else{0})}};$task
      };
      $refused=$false;try{Assert-StoppedRuntimeTasks $folder}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case not in ('absent','disabled'))


def test_frozen_build_restores_environment_on_failure_and_does_not_call_installer(tmp_path):
    escaped = str(tmp_path).replace("'", "''")
    result = run(f"$script:targetRoot='{escaped}';" + r'''
      $script:sourceRoot=$script:targetRoot+'\source';$script:python='C:\protected\python.exe';$script:uv='Invoke-FixtureUv';
      $env:UV_INDEX_URL='fixture-existing-index';$env:PYTHONPATH='fixture-existing-pythonpath';$beforeTemp=$env:TEMP;
      function Invoke-FixtureUv {
        if($null -ne $env:UV_INDEX_URL -or $null -ne $env:PYTHONPATH){throw 'Ambient policy leaked'};
        if($env:UV_PROJECT_ENVIRONMENT -ne ($script:targetRoot+'\.venv') -or $env:TEMP -ne ($script:targetRoot+'\build-temp')){throw 'Build escaped fresh root'};
        $script:arguments=@($args);$global:LASTEXITCODE=17
      };
      $failed=$false;try{Invoke-FrozenRuntimeBuild}catch{$failed=$true};
      [ordered]@{failed=$failed;restored=($env:UV_INDEX_URL -eq 'fixture-existing-index' -and $env:PYTHONPATH -eq 'fixture-existing-pythonpath' -and $env:TEMP -eq $beforeTemp);arguments=$script:arguments}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['failed'] and value['restored']
    arguments = value['arguments']
    assert arguments[:3] == ['--no-config','--no-cache','sync']
    assert '--frozen' in arguments and '--no-editable' in arguments and '--no-python-downloads' in arguments
    assert arguments[arguments.index('--link-mode') + 1] == 'copy'
    assert arguments[arguments.index('--extra') + 1] == 'mcp'
    assert not any('install_pipeline_windows' in value or 'provision' in value for value in arguments)


def test_runtime_tree_hardlink_is_refused_before_file_acl_mutation(tmp_path):
    outside = tmp_path / 'outside.txt'; outside.write_bytes(b'preserved')
    root = tmp_path / 'new'; root.mkdir(); os.link(outside, root / 'alias.txt')
    helper = str(REPO / 'scripts/stage_aetherdesk_427_payloads.ps1').replace("'", "''")
    result = run(f"$script:targetRoot='{str(root)}';$helper='{helper}';" + r'''
      $tokens=$null;$errors=$null;$a=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors);
      $f=$a.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Initialize-FileIdentity'},$true);
      . ([scriptblock]::Create($f.Extent.Text));Initialize-FileIdentity;
      function Assert-ProtectedPath {param($Path)};function New-CodeAcl {param($Directory)$null};
      # Retain the real tree walk/handle verifier; only ACL objects are fixtures.
    ''' + f"$treeSource='{str(ROOT / 'check-worker-denials.ps1')}';" + r'''
      $treeAst=[Management.Automation.Language.Parser]::ParseFile($treeSource,[ref]$tokens,[ref]$errors);
      $treeFunction=$treeAst.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-CodeTreeOnce'},$true);
      . ([scriptblock]::Create($treeFunction.Extent.Text));
      function Get-Acl {param($LiteralPath)
        $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'));
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-18'),'FullControl','Allow'));$acl};
      $script:aclsChanged=0;function Set-Acl {param($LiteralPath,$AclObject)$script:aclsChanged++};
      $refused=$false;try{Protect-NewRuntimeTree $script:targetRoot}catch{$refused=$true};
      [ordered]@{refused=$refused;acls_changed=$script:aclsChanged}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'refused':True,'acls_changed':0}
    assert outside.read_bytes() == b'preserved'


def test_nonadmin_apply_refuses_before_source_manifest_or_targets():
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Apply'],
                            capture_output=True,text=True,timeout=20)
    assert result.returncode != 0
    assert '-Apply requires the owner in Administrator Windows PowerShell' in result.stderr


def test_actual_ps51_python_c_transport_and_scoped_config_parser(tmp_path):
    """Execute the wrapper's exact validation body as ordinary ansac, no setup."""
    installed = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
    fixture = tmp_path / 'revision'; (fixture / 'source/src').mkdir(parents=True)
    for package in ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor'):
        shutil.copytree(installed / 'source/src' / package, fixture / 'source/src' / package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pyproject.toml','uv.lock'):
        shutil.copyfile(installed / 'source' / name, fixture / 'source' / name)
    shutil.copyfile(ROOT / 'pipeline.scoped-ram.candidate.json', fixture / 'pipeline.json')
    body = SCRIPT.read_text().split("$validation=@'\n", 1)[1].split("\n'@", 1)[0]
    # Test imports the newly repaired parser from source, but revision comparison
    # checks the real unchanged installed packages against their copied snapshot.
    code = "import sys;sys.path.insert(0," + repr(str(REPO / 'src')) + ")\n" + body
    ps = "$ErrorActionPreference='Stop';$validation=@'\n" + code + "\n'@\n"
    ps += "& '" + str(installed / '.venv/Scripts/python.exe') + "' -I -B -c $validation '" + str(fixture) + "'\nexit $LASTEXITCODE"
    command = tmp_path / 'argv-fixture.ps1'; command.write_text(ps)
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(command)],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['revision']['verified'] and value['configuration_parsed']
    assert value['ram_workspace_root'] == 'R:\\CoChem427-windows-20261007'


def test_installed_uv_documents_selected_flags_without_build_execution():
    uv = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\uv\uv.exe')
    assert hashlib.sha256(uv.read_bytes()).hexdigest() == '2019cdf564cb8f749262f5f021cedc75a99abb1c6081227ca340bbcda972611d'
    result = subprocess.run([str(uv),'sync','--help'],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0
    assert len(result.stdout.encode()) < 131072
    for flag in ('--no-config','--no-cache','--frozen','--no-editable','--link-mode','--extra','--python','--no-python-downloads'):
        assert flag in result.stdout


@pytest.mark.parametrize('exit_code', [0, 7])
def test_actual_native_stderr_is_judged_by_exit_code_in_ps51(tmp_path, exit_code):
    python = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
    result = run(f"$script:targetRoot='{tmp_path}';$script:uv='{python}';$script:fixtureExit={exit_code};" + r'''
      function Get-FrozenSyncArguments { @('-I','-B','-c',("import sys; print('benign native stderr',file=sys.stderr); sys.exit("+$script:fixtureExit+")")) };
      $failed=$false;try{Invoke-FrozenRuntimeBuild}catch{$failed=$true};
      [ordered]@{failed=$failed;preference=[string]$ErrorActionPreference;log_contains_stderr=((Get-Content -LiteralPath (Join-Path $script:targetRoot 'frozen-build.log') -Raw) -match 'benign native stderr')}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'failed': exit_code != 0, 'preference':'Stop', 'log_contains_stderr':True}


@pytest.mark.parametrize('mutate', [False, True])
def test_post_build_source_rebinding_rejects_identical_source_and_installed_drift(tmp_path, mutate):
    source = tmp_path / 'source.py'; installed = tmp_path / 'installed.py'
    original = b'approved source\n'; changed = b'mutated source!\n'
    source.write_bytes(changed if mutate else original); installed.write_bytes(source.read_bytes())
    assert source.read_bytes() == installed.read_bytes()
    helper = REPO / 'scripts/stage_aetherdesk_427_payloads.ps1'
    result = run(f"$helper='{helper}';$file='{source}';$expected='{hashlib.sha256(original).hexdigest()}';$length={len(original)};" + r'''
      $tokens=$null;$errors=$null;$a=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors);
      foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Initialize-FileIdentity','Assert-NoReparseAncestors','Open-VerifiedFile')},$true)){. ([scriptblock]::Create($f.Extent.Text))};Initialize-FileIdentity;
      $refused=$false;try{Assert-PostBuildFrozenFiles @([pscustomobject]@{destination=$file;sha256=$expected;length=$length})}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == mutate
